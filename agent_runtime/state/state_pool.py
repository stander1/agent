from __future__ import annotations

import hashlib
import json
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
        for path in (self.payload_dir, self.hot_dir, self.warm_dir, self.cold_dir):
            path.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, StateObject] = {}

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
        state = self._states[state_ref.state_id]
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
                f"[retrieval_state:{state.state_id}] {state.summary}; tier={state.tier}\n"
                + "\n".join(snippets)
            )
        elif state.state_type == "artifact_state":
            rendered = (
                f"[artifact_state:{state.state_id}] {state.summary}; "
                f"artifact={payload.get('code_artifact_id', payload.get('artifact_id', 'n/a'))}; "
                f"sha256={payload.get('sha256', 'n/a')[:12]}; "
                f"tier={state.tier}; raw_content=cold_audit_only"
            )
        elif state.state_type == "failure_state":
            rendered = (
                f"[failure_state:{state.state_id}] {state.summary}; "
                f"error={payload.get('error_type', 'unknown')}; tier={state.tier}"
            )
        else:
            rendered = f"[{state.state_type}:{state.state_id}] {state.summary}; tier={state.tier}"

        return rendered[:budget_chars]

    def render_audit_view(self, state_ref: StateRef, budget_chars: int = 4000) -> str:
        state = self._states[state_ref.state_id]
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
        return json.loads(Path(state.audit_payload_ref).read_text(encoding="utf-8"))

    def ref_to_dict(self, state_ref: StateRef) -> dict[str, Any]:
        return asdict(state_ref)

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
