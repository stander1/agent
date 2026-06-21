from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

StateTier = str


@dataclass(slots=True)
class StateRef:
    state_id: str
    state_type: str
    version: int
    payload_kind: str
    contains_embedding_refs: bool
    usage_hint: str
    tier: StateTier


@dataclass(slots=True)
class StateObject:
    state_id: str
    state_type: str
    task_id: str
    source_agent: str
    created_at: str
    payload_ref: str
    payload_kind: str
    contains_embedding_refs: bool
    summary: str
    size_bytes: int
    tier: StateTier
    lifecycle: str
    content_hash: str
    access_policy: str
    audit_payload_ref: str | None = None
    version: int = 1


@dataclass(slots=True)
class StateAccessReport:
    state_id: str
    access_level: str
    read_lease_acquire_count: int = 1
    raw_access_count: int = 0
    summary_access_count: int = 0
    evidence_snippet_access_count: int = 0
    access_escalation_count: int = 0


@dataclass(slots=True)
class StateGCReport:
    state_gc_count: int = 0
    read_lease_blocked_gc_count: int = 0
    state_tombstone_count: int = 0
    cooling_state_count: int = 0


class LeaseRegistry:
    """In-memory read lease counters for the v4 hot path."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active_readers: dict[str, int] = {}

    @contextmanager
    def read_lease(self, state_id: str):
        with self._lock:
            self._active_readers[state_id] = self._active_readers.get(state_id, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                remaining = self._active_readers.get(state_id, 0) - 1
                if remaining > 0:
                    self._active_readers[state_id] = remaining
                else:
                    self._active_readers.pop(state_id, None)

    def active_readers(self, state_id: str) -> int:
        with self._lock:
            return self._active_readers.get(state_id, 0)


class StatePoolLite:
    """File-backed state pool with SHP-State Hot/Warm/Cold tiers.

    SHP messages carry StateRef objects. Full raw artifact content is stored as
    cold audit payload and is not returned by render_prompt_view().
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.payload_dir = self.root_dir / "state_payloads"
        self.hot_dir = self.root_dir / "state_hot"
        self.warm_dir = self.root_dir / "state_warm"
        self.cold_dir = self.root_dir / "state_cold"
        self.tombstone_dir = self.root_dir / "state_tombstones"
        for path in (
            self.payload_dir,
            self.hot_dir,
            self.warm_dir,
            self.cold_dir,
            self.tombstone_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, StateObject] = {}
        self._tombstones: dict[str, dict[str, Any]] = {}
        self._lineage_state_ids: set[str] = set()
        self.leases = LeaseRegistry()

    def write_state(
        self,
        *,
        task_id: str,
        source_agent: str,
        state_type: str,
        payload: dict[str, Any],
        summary: str,
        usage_hint: str,
        payload_kind: str = "structured_non_text",
        contains_embedding_refs: bool = False,
        tier: StateTier | None = None,
        lifecycle: str = "active",
        access_policy: str = "prompt_view_only",
        audit_payload: dict[str, Any] | None = None,
    ) -> tuple[StateRef, StateObject]:
        state_id = self._next_state_id(task_id, source_agent, state_type, payload)
        tier = tier or self._default_tier(state_type, payload)
        payload_to_store = dict(payload)

        audit_payload_ref = None
        audit_size_bytes = 0
        if audit_payload is not None:
            audit_path = self.cold_dir / f"{state_id}.audit.json"
            audit_encoded = json.dumps(audit_payload, ensure_ascii=False, indent=2)
            audit_path.write_text(audit_encoded, encoding="utf-8")
            audit_payload_ref = str(audit_path)
            audit_size_bytes = len(audit_encoded.encode("utf-8"))
            payload_to_store["audit_payload_ref"] = audit_payload_ref
            payload_to_store["audit_payload_hash"] = hashlib.sha256(
                audit_encoded.encode("utf-8")
            ).hexdigest()

        encoded = json.dumps(payload_to_store, ensure_ascii=False, indent=2)
        path = self._tier_dir(tier) / f"{state_id}.json"
        path.write_text(encoded, encoding="utf-8")
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        size_bytes = len(encoded.encode("utf-8")) + audit_size_bytes

        state = StateObject(
            state_id=state_id,
            state_type=state_type,
            task_id=task_id,
            source_agent=source_agent,
            created_at=datetime.now(timezone.utc).isoformat(),
            payload_ref=str(path),
            payload_kind=payload_kind,
            contains_embedding_refs=contains_embedding_refs,
            summary=summary,
            size_bytes=size_bytes,
            tier=tier,
            lifecycle=lifecycle,
            content_hash=content_hash,
            access_policy=access_policy,
            audit_payload_ref=audit_payload_ref,
        )
        self._states[state_id] = state
        return (
            StateRef(
                state_id=state.state_id,
                state_type=state.state_type,
                version=state.version,
                payload_kind=state.payload_kind,
                contains_embedding_refs=state.contains_embedding_refs,
                usage_hint=usage_hint,
                tier=state.tier,
            ),
            state,
        )

    def render_prompt_view(
        self, state_ref: StateRef, agent_role: str, budget_chars: int = 900
    ) -> str:
        return self.render_prompt_view_with_report(
            state_ref, agent_role, budget_chars=budget_chars
        )[0]

    def render_prompt_view_with_report(
        self, state_ref: StateRef, agent_role: str, budget_chars: int = 900
    ) -> tuple[str, StateAccessReport]:
        state = self._states[state_ref.state_id]
        if state.lifecycle in {"deleted", "tombstoned"}:
            rendered = (
                f"[state_tombstone:{state.state_id}] state no longer has prompt payload; "
                f"lifecycle={state.lifecycle}"
            )
            report = StateAccessReport(
                state_id=state.state_id,
                access_level="summary",
                summary_access_count=1,
            )
            return rendered[:budget_chars], report

        with self.leases.read_lease(state.state_id):
            payload = json.loads(Path(state.payload_ref).read_text(encoding="utf-8"))

            if state.state_type == "retrieval_state":
                ranked = payload.get("evidence_rank", [])
                score_map = payload.get("score_map", {})
                snippets = []
                limit = 3 if agent_role in {"WriterAgent", "PlannerAgent"} else 5
                for chunk_id in ranked[:limit]:
                    item = payload.get("chunks", {}).get(chunk_id, {})
                    snippets.append(
                        f"- {chunk_id} score={score_map.get(chunk_id, 0):.2f}: "
                        f"{item.get('text', '')[:180]}"
                    )
                rendered = (
                    f"[retrieval_state:{state.state_id}] {state.summary}; "
                    f"query_embedding={payload.get('query_embedding_id', 'n/a')}; "
                    f"vector_dim={payload.get('vector_dim', 'n/a')}; "
                    f"embedding_state={payload.get('embedding_state_id', 'n/a')}; "
                    f"tier={state.tier}\n"
                    + "\n".join(snippets)
                )
                report = StateAccessReport(
                    state_id=state.state_id,
                    access_level="evidence_snippets",
                    evidence_snippet_access_count=1,
                )
            elif state.state_type == "embedding_state":
                rendered = (
                    f"[embedding_state:{state.state_id}] {state.summary}; "
                    f"query_embedding={payload.get('query_embedding_id', 'n/a')}; "
                    f"chunks={len(payload.get('chunk_embedding_ids', []))}; "
                    f"vector_dim={payload.get('vector_dim', 'n/a')}; "
                    f"vector_store={payload.get('vector_store_ref', 'n/a')}; "
                    f"tier={state.tier}"
                )
                report = StateAccessReport(
                    state_id=state.state_id,
                    access_level="metadata",
                    summary_access_count=1,
                )
            elif state.state_type == "artifact_state":
                rendered = (
                    f"[artifact_state:{state.state_id}] {state.summary}; "
                    f"artifact={payload.get('code_artifact_id', payload.get('artifact_id', 'n/a'))}; "
                    f"sha256={payload.get('sha256', 'n/a')[:12]}; "
                    f"tier={state.tier}; raw_content=cold_audit_only"
                )
                report = StateAccessReport(
                    state_id=state.state_id,
                    access_level="summary",
                    summary_access_count=1,
                )
            elif state.state_type == "failure_state":
                rendered = (
                    f"[failure_state:{state.state_id}] {state.summary}; "
                    f"error={payload.get('error_type', 'unknown')}; tier={state.tier}"
                )
                report = StateAccessReport(
                    state_id=state.state_id,
                    access_level="summary",
                    summary_access_count=1,
                )
            else:
                rendered = f"[{state.state_type}:{state.state_id}] {state.summary}; tier={state.tier}"
                report = StateAccessReport(
                    state_id=state.state_id,
                    access_level="summary",
                    summary_access_count=1,
                )

        return rendered[:budget_chars], report

    def render_audit_view(self, state_ref: StateRef, budget_chars: int = 4000) -> str:
        state = self._states[state_ref.state_id]
        with self.leases.read_lease(state.state_id):
            payload = json.loads(Path(state.payload_ref).read_text(encoding="utf-8"))
            rendered = {
                "state_id": state.state_id,
                "state_type": state.state_type,
                "tier": state.tier,
                "lifecycle": state.lifecycle,
                "summary": state.summary,
                "payload": payload,
                "audit_payload": self.load_audit_payload(state_ref) or {},
            }
        return json.dumps(rendered, ensure_ascii=False, indent=2)[:budget_chars]

    def load_audit_payload(self, state_ref: StateRef) -> dict[str, Any] | None:
        state = self._states[state_ref.state_id]
        if not state.audit_payload_ref:
            return None
        with self.leases.read_lease(state.state_id):
            return json.loads(Path(state.audit_payload_ref).read_text(encoding="utf-8"))

    def collect_garbage(
        self,
        *,
        protected_state_ids: set[str] | None = None,
        max_deleted: int = 4,
    ) -> StateGCReport:
        protected = protected_state_ids or set()
        report = StateGCReport()
        candidates = [
            state
            for state in self._states.values()
            if state.state_id not in protected
            and state.state_id not in self._lineage_state_ids
            and state.lifecycle in {"active", "cooling", "evict_pending"}
            and state.tier == "cold"
        ]
        candidates.sort(key=lambda item: item.created_at)
        for state in candidates[:max_deleted]:
            if self.leases.active_readers(state.state_id) > 0:
                state.lifecycle = "cooling"
                report.read_lease_blocked_gc_count += 1
                report.cooling_state_count += 1
                continue
            self._tombstone_state(state, reason="v4_gc")
            report.state_gc_count += 1
            report.state_tombstone_count += 1
        return report

    def ref_to_dict(self, state_ref: StateRef) -> dict[str, Any]:
        return asdict(state_ref)

    def mark_lineage(self, state_ids: list[str]) -> None:
        self._lineage_state_ids.update(state_ids)

    def _tombstone_state(self, state: StateObject, *, reason: str) -> None:
        tombstone = {
            "state_id": state.state_id,
            "state_type": state.state_type,
            "task_id": state.task_id,
            "source_agent": state.source_agent,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "payload_ref": state.payload_ref,
            "audit_payload_ref": state.audit_payload_ref,
            "content_hash": state.content_hash,
        }
        path = self.tombstone_dir / f"{state.state_id}.tombstone.json"
        path.write_text(json.dumps(tombstone, ensure_ascii=False, indent=2), encoding="utf-8")
        self._tombstones[state.state_id] = tombstone
        state.lifecycle = "deleted"
        for payload_ref in [state.payload_ref, state.audit_payload_ref]:
            if payload_ref and Path(payload_ref).exists():
                Path(payload_ref).unlink()

    def _tier_dir(self, tier: StateTier) -> Path:
        if tier == "hot":
            return self.hot_dir
        if tier == "warm":
            return self.warm_dir
        if tier == "cold":
            return self.cold_dir
        raise ValueError(f"Unknown state tier: {tier}")

    def _default_tier(self, state_type: str, payload: dict[str, Any]) -> StateTier:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if state_type in {"retrieval_state", "embedding_state"} and len(encoded) < 8192:
            return "hot"
        if state_type == "artifact_state":
            return "cold"
        if len(encoded) > 8192:
            return "cold"
        return "warm"

    def _next_state_id(
        self, task_id: str, source_agent: str, state_type: str, payload: dict[str, Any]
    ) -> str:
        seed = json.dumps(
            {
                "task_id": task_id,
                "source_agent": source_agent,
                "state_type": state_type,
                "payload": payload,
                "count": len(self._states),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"state_{digest}"
