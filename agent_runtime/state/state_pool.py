from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _io_path(path: Path | str) -> Path:
    """Return a Windows long-path-safe Path for filesystem I/O."""

    candidate = Path(path)
    if os.name != "nt":
        return candidate
    raw = str(candidate)
    if raw.startswith("\\\\?\\"):
        return candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        resolved = candidate.absolute()
    resolved_raw = str(resolved)
    if resolved_raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + resolved_raw.lstrip("\\"))
    return Path("\\\\?\\" + resolved_raw)


def _write_text(path: Path | str, text: str) -> None:
    target = Path(path)
    _io_path(target.parent).mkdir(parents=True, exist_ok=True)
    _io_path(target).write_text(text, encoding="utf-8")


def _read_text(path: Path | str) -> str:
    return _io_path(path).read_text(encoding="utf-8")


def _read_bytes(path: Path | str) -> bytes:
    return _io_path(path).read_bytes()


def _exists(path: Path | str) -> bool:
    return _io_path(path).exists()


def _stat_size(path: Path | str) -> int:
    return _io_path(path).stat().st_size


def _unlink(path: Path | str) -> None:
    _io_path(path).unlink()

StateTier = str

STATE_LIFECYCLE_STATES = {
    "active",
    "dormant",
    "outdated",
    "retry_carried",
    "superseded",
    "compressed",
    "archived",
    "evicted",
    "deleted",
    "cooling",
    "evict_pending",
}


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
    dependency_ref_count: int = 0
    retry_ref_count: int = 0
    superseded_by: str | None = None
    gc_policy: str = "quota_or_lifecycle"
    fallback_summary: str = ""
    replacement_state_id: str | None = None
    evicted_at: str | None = None
    admission_score: float = 1.0
    admission_status: str = "admitted"
    access_count: int = 0
    last_accessed_at: str | None = None
    status_updated_at: str | None = None
    outdated_reason: str = ""


@dataclass(slots=True)
class StateAccessReport:
    state_id: str
    access_level: str
    fencing_token: str = ""
    lease_ttl_seconds: float = 0.0
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
    quota_evicted_count: int = 0
    physical_delete_count: int = 0
    retry_summary_state_count: int = 0
    lifecycle_transition_count: int = 0
    retry_carried_protected_count: int = 0
    dag_liveness_protected_count: int = 0
    io_budget_gc_count: int = 0
    raw_cache_evicted_count: int = 0


@dataclass(slots=True)
class StateQuotaConfig:
    max_task_states: int = 24
    max_task_bytes: int = 256_000
    max_hot_states: int = 12
    tombstone_grace_seconds: float = 300.0


@dataclass(slots=True)
class StateAdmissionPolicy:
    downstream_need_weight: float = 0.45
    confidence_weight: float = 0.35
    novelty_weight: float = 0.20
    size_penalty_weight: float = 0.15
    admit_threshold: float = 0.45


@dataclass(slots=True)
class StateAdmissionReport:
    admitted: bool
    score: float
    status: str
    reasons: list[str]
    downstream_need: float
    confidence: float
    novelty: float
    size_penalty: float


@dataclass(slots=True)
class ColdAccessBudget:
    max_cold_reads_per_task: int = 4
    max_cold_bytes_per_phase: int = 64_000
    max_concurrent_cold_reads: int = 2
    max_raw_view_tokens: int = 4096
    cold_read_timeout_ms: int = 3000
    full_raw_read_policy: str = "forbidden_by_default"


@dataclass(slots=True)
class ColdAccessReport:
    state_id: str
    allowed: bool
    reason: str
    bytes_read: int = 0
    cold_read_count: int = 0
    budget_remaining: int = 0
    cache_hit: bool = False
    payload: dict[str, Any] | None = None
    raw_view_id: str = ""
    chunk_ids: list[str] | None = None


@dataclass(slots=True)
class AccessEscalationReport:
    state_id: str
    selected_level: str
    allowed: bool
    reason: str
    expected_summary_tokens: int
    expected_raw_tokens: int
    access_escalation_count: int = 0
    prompt_view: str = ""
    cold_access: ColdAccessReport | None = None
    summary_first_expected_cost: float = 0.0
    raw_first_expected_cost: float = 0.0
    raw_need_probability: float = 0.0
    decision_basis: str = "summary_first_default"


@dataclass(slots=True)
class LoopBudgetConfig:
    max_attempts: int = 10
    max_loop_state_bytes: int = 52_428_800
    max_carried_states: int = 20
    keep_last_k_attempts: int = 3


@dataclass(slots=True)
class LoopBudgetReport:
    allowed: bool
    attempt_count: int
    carried_state_count: int
    carried_state_bytes: int
    max_attempts: int
    max_loop_state_bytes: int
    max_carried_states: int
    reasons: list[str]


@dataclass(slots=True)
class RawChunk:
    chunk_id: str
    state_id: str
    span: str
    offset_start: int
    offset_end: int
    summary: str
    tags: list[str]


@dataclass(slots=True)
class RawSpanResolutionReport:
    state_id: str
    query: str
    matched_chunk_ids: list[str]
    raw_view_id: str
    content: str
    size_tokens: int
    cache_hit: bool


@dataclass(slots=True)
class PrefetchReport:
    requested_count: int = 0
    prefetched_count: int = 0
    skipped_count: int = 0
    raw_view_ids: list[str] | None = None


@dataclass(slots=True)
class StateLifecycleTransitionReport:
    state_id: str
    old_lifecycle: str
    new_lifecycle: str
    transitioned: bool


@dataclass(slots=True)
class ReadLeaseRecord:
    state_id: str
    fencing_token: str
    owner: str
    acquired_at: datetime
    last_heartbeat_at: datetime
    ttl_seconds: float


class LeaseRegistry:
    """In-memory read leases with TTL, heartbeat, and fencing tokens."""

    def __init__(self, default_ttl_seconds: float = 30.0) -> None:
        self._lock = threading.RLock()
        self.default_ttl_seconds = default_ttl_seconds
        self._records: dict[str, ReadLeaseRecord] = {}
        self._counter = 0

    @contextmanager
    def read_lease(
        self, state_id: str, *, owner: str = "runtime", ttl_seconds: float | None = None
    ):
        record = self.acquire_read_lease(
            state_id, owner=owner, ttl_seconds=ttl_seconds
        )
        try:
            yield record
        finally:
            self.release_read_lease(record.fencing_token)

    def acquire_read_lease(
        self, state_id: str, *, owner: str = "runtime", ttl_seconds: float | None = None
    ) -> ReadLeaseRecord:
        with self._lock:
            self.recover_expired_locked()
            self._counter += 1
            now = datetime.now(timezone.utc)
            token = f"lease_{state_id}_{self._counter}"
            record = ReadLeaseRecord(
                state_id=state_id,
                fencing_token=token,
                owner=owner,
                acquired_at=now,
                last_heartbeat_at=now,
                ttl_seconds=(
                    ttl_seconds
                    if ttl_seconds is not None
                    else self.default_ttl_seconds
                ),
            )
            self._records[token] = record
            return record

    def heartbeat(self, fencing_token: str) -> bool:
        with self._lock:
            self.recover_expired_locked()
            record = self._records.get(fencing_token)
            if record is None:
                return False
            record.last_heartbeat_at = datetime.now(timezone.utc)
            return True

    def release_read_lease(self, fencing_token: str) -> bool:
        with self._lock:
            return self._records.pop(fencing_token, None) is not None

    def recover_expired(self) -> int:
        with self._lock:
            return self.recover_expired_locked()

    def recover_expired_locked(self) -> int:
        now = datetime.now(timezone.utc)
        expired = [
            token
            for token, record in self._records.items()
            if record.last_heartbeat_at
            + timedelta(seconds=record.ttl_seconds)
            <= now
        ]
        for token in expired:
            self._records.pop(token, None)
        return len(expired)

    def active_readers(self, state_id: str) -> int:
        with self._lock:
            self.recover_expired_locked()
            return sum(1 for record in self._records.values() if record.state_id == state_id)

    def active_fencing_tokens(self, state_id: str) -> list[str]:
        with self._lock:
            self.recover_expired_locked()
            return [
                record.fencing_token
                for record in self._records.values()
                if record.state_id == state_id
            ]


class StatePoolLite:
    """File-backed state pool with SHP-State Hot/Warm/Cold tiers.

    SHP messages carry StateRef objects. Full raw artifact content is stored as
    cold audit payload and is not returned by render_prompt_view().
    """

    def __init__(
        self,
        root_dir: Path,
        quota_config: StateQuotaConfig | None = None,
        cold_access_budget: ColdAccessBudget | None = None,
        admission_policy: StateAdmissionPolicy | None = None,
        loop_budget_config: LoopBudgetConfig | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.payload_dir = self.root_dir / "state_payloads"
        self.hot_dir = self.root_dir / "state_hot"
        self.warm_dir = self.root_dir / "state_warm"
        self.cold_dir = self.root_dir / "state_cold"
        self.tombstone_dir = self.root_dir / "state_tombstones"
        self._storage_dirs = (
            self.payload_dir,
            self.hot_dir,
            self.warm_dir,
            self.cold_dir,
            self.tombstone_dir,
        )
        self._ensure_storage_dirs()
        self._states: dict[str, StateObject] = {}
        self._hot_payloads: dict[str, dict[str, Any]] = {}
        self._tombstones: dict[str, dict[str, Any]] = {}
        self._lineage_state_ids: set[str] = set()
        self.quota_config = quota_config or StateQuotaConfig()
        self.cold_access_budget = cold_access_budget or ColdAccessBudget()
        self.admission_policy = admission_policy or StateAdmissionPolicy()
        self.loop_budget_config = loop_budget_config or LoopBudgetConfig()
        self._cold_read_counts: dict[str, int] = {}
        self._cold_read_bytes: dict[str, int] = {}
        self._raw_view_cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self._raw_chunk_index: dict[str, list[RawChunk]] = {}
        self._raw_view_materialized: dict[str, RawSpanResolutionReport] = {}
        self._prefetch_queue: list[str] = []
        self._active_cold_reads = 0
        self.leases = LeaseRegistry()

    def _ensure_storage_dirs(self) -> None:
        for path in self._storage_dirs:
            _io_path(path).mkdir(parents=True, exist_ok=True)

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
        dependency_ref_count: int = 0,
        retry_ref_count: int = 0,
        gc_policy: str = "quota_or_lifecycle",
        downstream_need: float | None = None,
        confidence: float | None = None,
        novelty: float | None = None,
    ) -> tuple[StateRef, StateObject]:
        if lifecycle not in STATE_LIFECYCLE_STATES:
            raise ValueError(f"Unknown state lifecycle: {lifecycle}")
        state_id = self._next_state_id(task_id, source_agent, state_type, payload)
        requested_tier = tier
        payload_to_store = dict(payload)

        audit_payload_ref = None
        audit_size_bytes = 0
        if audit_payload is not None:
            audit_path = self.cold_dir / f"{state_id}.audit.json"
            audit_encoded = json.dumps(audit_payload, ensure_ascii=False, indent=2)
            _write_text(audit_path, audit_encoded)
            audit_payload_ref = str(audit_path)
            audit_size_bytes = len(audit_encoded.encode("utf-8"))
            payload_to_store["audit_payload_ref"] = audit_payload_ref
            payload_to_store["audit_payload_hash"] = hashlib.sha256(
                audit_encoded.encode("utf-8")
            ).hexdigest()

        original_encoded = json.dumps(payload_to_store, ensure_ascii=False, indent=2)
        admission = self.assess_state_admission(
            state_type=state_type,
            payload_bytes=len(original_encoded.encode("utf-8")) + audit_size_bytes,
            downstream_need=downstream_need,
            confidence=confidence,
            novelty=novelty,
        )
        if requested_tier is None:
            tier = self._tier_from_admission(
                state_type=state_type,
                payload=payload,
                payload_bytes=len(original_encoded.encode("utf-8")) + audit_size_bytes,
                admission=admission,
                has_audit_payload=audit_payload_ref is not None,
            )
        else:
            tier = requested_tier
        if not admission.admitted:
            payload_to_store = {
                "admission_status": admission.status,
                "summary": summary,
                "original_payload_hash": hashlib.sha256(
                    original_encoded.encode("utf-8")
                ).hexdigest(),
                "original_payload_bytes": len(original_encoded.encode("utf-8")),
            }
            if audit_payload_ref:
                payload_to_store["audit_payload_ref"] = audit_payload_ref
                payload_to_store["audit_payload_hash"] = hashlib.sha256(
                    _read_bytes(audit_payload_ref)
                ).hexdigest()
        encoded = json.dumps(payload_to_store, ensure_ascii=False, indent=2)
        path = self._tier_dir(tier) / f"{state_id}.json"
        _write_text(path, encoded)
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
            dependency_ref_count=dependency_ref_count,
            retry_ref_count=retry_ref_count,
            gc_policy=gc_policy,
            fallback_summary=summary[:280],
            admission_score=admission.score,
            admission_status=admission.status,
            status_updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._states[state_id] = state
        if tier == "hot":
            self._hot_payloads[state_id] = dict(payload_to_store)
        state_ref = StateRef(
            state_id=state.state_id,
            state_type=state.state_type,
            version=state.version,
            payload_kind=state.payload_kind,
            contains_embedding_refs=state.contains_embedding_refs,
            usage_hint=usage_hint,
            tier=state.tier,
        )
        if audit_payload is not None:
            self.build_raw_chunk_index(state_ref)
        return (
            state_ref,
            state,
        )

    def assess_state_admission(
        self,
        *,
        state_type: str,
        payload_bytes: int,
        downstream_need: float | None = None,
        confidence: float | None = None,
        novelty: float | None = None,
    ) -> StateAdmissionReport:
        defaults = {
            "retrieval_state": (0.95, 0.82, 0.78),
            "embedding_state": (0.75, 0.8, 0.62),
            "artifact_state": (0.72, 0.76, 0.58),
            "failure_state": (1.0, 0.95, 0.7),
            "retry_loop_summary": (0.88, 0.8, 0.55),
        }
        fallback = defaults.get(state_type, (0.6, 0.65, 0.5))
        need = self._clamp01(downstream_need if downstream_need is not None else fallback[0])
        conf = self._clamp01(confidence if confidence is not None else fallback[1])
        nov = self._clamp01(novelty if novelty is not None else fallback[2])
        size_penalty = min(1.0, payload_bytes / max(1, self.quota_config.max_task_bytes))
        policy = self.admission_policy
        score = (
            policy.downstream_need_weight * need
            + policy.confidence_weight * conf
            + policy.novelty_weight * nov
            - policy.size_penalty_weight * size_penalty
        )
        reasons: list[str] = []
        if need < 0.35:
            reasons.append("low_downstream_need")
        if conf < 0.35:
            reasons.append("low_confidence")
        if nov < 0.2:
            reasons.append("low_novelty")
        if score < policy.admit_threshold:
            reasons.append("score_below_admit_threshold")
        admitted = score >= policy.admit_threshold
        return StateAdmissionReport(
            admitted=admitted,
            score=round(score, 4),
            status="admitted" if admitted else "audit_only",
            reasons=reasons or ["rules_admitted"],
            downstream_need=need,
            confidence=conf,
            novelty=nov,
            size_penalty=round(size_penalty, 4),
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
        state = self._states.get(state_ref.state_id)
        if state is None:
            tombstone = self._tombstones.get(state_ref.state_id)
            if tombstone is None:
                raise KeyError(state_ref.state_id)
            rendered = (
                f"[state_tombstone:{state_ref.state_id}] state no longer has prompt payload; "
                f"lifecycle=deleted; "
                f"fallback_summary={tombstone.get('fallback_summary', '')}"
            )
            return rendered[:budget_chars], StateAccessReport(
                state_id=state_ref.state_id,
                access_level="summary",
                read_lease_acquire_count=0,
                summary_access_count=1,
            )
        self._mark_accessed(state)
        if state.lifecycle in {"deleted", "tombstoned", "evicted"}:
            tombstone = self._tombstones.get(state.state_id, {})
            fallback_summary = tombstone.get("fallback_summary") or state.fallback_summary
            rendered = (
                f"[state_tombstone:{state.state_id}] state no longer has prompt payload; "
                f"lifecycle={state.lifecycle}; fallback_summary={fallback_summary}"
            )
            report = StateAccessReport(
                state_id=state.state_id,
                access_level="summary",
                read_lease_acquire_count=0,
                summary_access_count=1,
            )
            return rendered[:budget_chars], report

        with self.leases.read_lease(state.state_id, owner=agent_role) as lease:
            payload = self._load_prompt_payload(state)

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
                    fencing_token=lease.fencing_token,
                    lease_ttl_seconds=lease.ttl_seconds,
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
                    fencing_token=lease.fencing_token,
                    lease_ttl_seconds=lease.ttl_seconds,
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
                    fencing_token=lease.fencing_token,
                    lease_ttl_seconds=lease.ttl_seconds,
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
                    fencing_token=lease.fencing_token,
                    lease_ttl_seconds=lease.ttl_seconds,
                    summary_access_count=1,
                )
            else:
                rendered = f"[{state.state_type}:{state.state_id}] {state.summary}; tier={state.tier}"
                report = StateAccessReport(
                    state_id=state.state_id,
                    access_level="summary",
                    fencing_token=lease.fencing_token,
                    lease_ttl_seconds=lease.ttl_seconds,
                    summary_access_count=1,
                )

        return rendered[:budget_chars], report

    def render_audit_view(self, state_ref: StateRef, budget_chars: int = 4000) -> str:
        state = self._states[state_ref.state_id]
        self._mark_accessed(state)
        with self.leases.read_lease(state.state_id, owner="audit_view"):
            payload = json.loads(_read_text(state.payload_ref))
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
        self._mark_accessed(state)
        with self.leases.read_lease(state.state_id, owner="audit_payload"):
            return json.loads(_read_text(state.audit_payload_ref))

    def request_cold_access(
        self,
        state_ref: StateRef,
        *,
        reason: str,
        max_bytes: int | None = None,
    ) -> ColdAccessReport:
        state = self._states[state_ref.state_id]
        self._mark_accessed(state)
        if state.lifecycle in {"deleted"}:
            return ColdAccessReport(
                state_id=state.state_id,
                allowed=False,
                reason="state_deleted",
                budget_remaining=0,
            )
        if not state.audit_payload_ref or not _exists(state.audit_payload_ref):
            return ColdAccessReport(
                state_id=state.state_id,
                allowed=False,
                reason="audit_payload_missing",
                budget_remaining=self._cold_budget_remaining(state.task_id),
            )
        if self._active_cold_reads >= self.cold_access_budget.max_concurrent_cold_reads:
            return ColdAccessReport(
                state_id=state.state_id,
                allowed=False,
                reason="max_concurrent_cold_reads_exceeded",
                budget_remaining=self._cold_budget_remaining(state.task_id),
            )

        read_count = self._cold_read_counts.get(state.task_id, 0)
        if read_count >= self.cold_access_budget.max_cold_reads_per_task:
            return ColdAccessReport(
                state_id=state.state_id,
                allowed=False,
                reason="cold_read_count_budget_exceeded",
                cold_read_count=read_count,
                budget_remaining=0,
            )

        cached = self._raw_view_cache.get(state.state_id)
        if cached is None:
            payload_bytes = _stat_size(state.audit_payload_ref)
        else:
            payload_bytes = cached[0]
        raw_view_tokens = max(1, (payload_bytes + 3) // 4)
        if raw_view_tokens > self.cold_access_budget.max_raw_view_tokens:
            return ColdAccessReport(
                state_id=state.state_id,
                allowed=False,
                reason="raw_view_token_budget_exceeded",
                bytes_read=0,
                cold_read_count=read_count,
                budget_remaining=self._cold_budget_remaining(state.task_id),
            )
        byte_limit = min(
            max_bytes or self.cold_access_budget.max_cold_bytes_per_phase,
            self.cold_access_budget.max_cold_bytes_per_phase,
        )
        already_read = self._cold_read_bytes.get(state.task_id, 0)
        if already_read + payload_bytes > byte_limit:
            return ColdAccessReport(
                state_id=state.state_id,
                allowed=False,
                reason="cold_read_byte_budget_exceeded",
                bytes_read=already_read,
                cold_read_count=read_count,
                budget_remaining=max(0, byte_limit - already_read),
            )

        cache_hit = cached is not None
        if cached is None:
            self._active_cold_reads += 1
            try:
                with self.leases.read_lease(state.state_id, owner="cold_access"):
                    payload = json.loads(_read_text(state.audit_payload_ref))
                self._raw_view_cache[state.state_id] = (payload_bytes, payload)
            finally:
                self._active_cold_reads = max(0, self._active_cold_reads - 1)
        else:
            payload = cached[1]
        raw_view_id = f"rv_{state.state_id}_{len(self._raw_view_materialized) + 1:04d}"
        self._cold_read_counts[state.task_id] = read_count + 1
        self._cold_read_bytes[state.task_id] = already_read + payload_bytes
        return ColdAccessReport(
            state_id=state.state_id,
            allowed=True,
            reason=reason,
            bytes_read=payload_bytes,
            cold_read_count=read_count + 1,
            budget_remaining=max(0, byte_limit - already_read - payload_bytes),
            cache_hit=cache_hit,
            payload=payload,
            raw_view_id=raw_view_id,
            chunk_ids=[chunk.chunk_id for chunk in self._raw_chunk_index.get(state.state_id, [])[:3]],
        )

    def request_progressive_access(
        self,
        state_ref: StateRef,
        *,
        agent_role: str,
        reason: str,
        need_raw: bool = False,
        expected_summary_tokens: int = 96,
        expected_raw_tokens: int = 900,
        expected_request_tokens: int = 32,
        expected_recovery_tokens: int = 128,
        raw_need_probability: float = 0.25,
        budget_chars: int = 900,
    ) -> AccessEscalationReport:
        state = self._states[state_ref.state_id]
        prompt_view, prompt_report = self.render_prompt_view_with_report(
            state_ref, agent_role=agent_role, budget_chars=budget_chars
        )
        probability = self._clamp01(raw_need_probability)
        summary_first_cost = expected_summary_tokens + probability * (
            expected_request_tokens + expected_raw_tokens + expected_recovery_tokens
        )
        raw_first_cost = expected_request_tokens + expected_raw_tokens
        if state.lifecycle in {"deleted", "evicted"}:
            return AccessEscalationReport(
                state_id=state.state_id,
                selected_level="summary_only",
                allowed=True,
                reason="state_unavailable_uses_fallback_summary",
                expected_summary_tokens=expected_summary_tokens,
                expected_raw_tokens=expected_raw_tokens,
                prompt_view=prompt_view,
                summary_first_expected_cost=round(summary_first_cost, 2),
                raw_first_expected_cost=round(raw_first_cost, 2),
                raw_need_probability=probability,
                decision_basis="fallback_summary_for_unavailable_state",
            )
        if not need_raw:
            return AccessEscalationReport(
                state_id=state.state_id,
                selected_level=prompt_report.access_level,
                allowed=True,
                reason="prompt_view_sufficient",
                expected_summary_tokens=expected_summary_tokens,
                expected_raw_tokens=expected_raw_tokens,
                prompt_view=prompt_view,
                summary_first_expected_cost=round(summary_first_cost, 2),
                raw_first_expected_cost=round(raw_first_cost, 2),
                raw_need_probability=probability,
                decision_basis="summary_first_expected_cost_model",
            )
        if state.tier != "cold" or not state.audit_payload_ref:
            return AccessEscalationReport(
                state_id=state.state_id,
                selected_level=prompt_report.access_level,
                allowed=True,
                reason="raw_access_not_available_or_not_needed_for_tier",
                expected_summary_tokens=expected_summary_tokens,
                expected_raw_tokens=expected_raw_tokens,
                prompt_view=prompt_view,
                summary_first_expected_cost=round(summary_first_cost, 2),
                raw_first_expected_cost=round(raw_first_cost, 2),
                raw_need_probability=probability,
                decision_basis="raw_unavailable",
            )

        cold_access = self.request_cold_access(state_ref, reason=reason)
        raw_first_preferred = summary_first_cost > raw_first_cost
        return AccessEscalationReport(
            state_id=state.state_id,
            selected_level="full_cold_read" if cold_access.allowed else "summary_only",
            allowed=cold_access.allowed,
            reason=(
                "raw_first_expected_cost_lower"
                if cold_access.allowed and raw_first_preferred
                else cold_access.reason
            ),
            expected_summary_tokens=expected_summary_tokens,
            expected_raw_tokens=expected_raw_tokens,
            access_escalation_count=1,
            prompt_view=prompt_view,
            cold_access=cold_access,
            summary_first_expected_cost=round(summary_first_cost, 2),
            raw_first_expected_cost=round(raw_first_cost, 2),
            raw_need_probability=probability,
            decision_basis=(
                "raw_first_expected_cost_model"
                if raw_first_preferred
                else "summary_first_then_escalate"
            ),
        )

    def collect_garbage(
        self,
        *,
        protected_state_ids: set[str] | None = None,
        max_deleted: int = 4,
    ) -> StateGCReport:
        protected = protected_state_ids or set()
        report = StateGCReport()
        report.raw_cache_evicted_count = self._prune_raw_view_cache()
        report.io_budget_gc_count += report.raw_cache_evicted_count
        retry_protected = {
            state.state_id
            for state in self._states.values()
            if state.lifecycle == "retry_carried" or state.retry_ref_count > 0
        }
        dag_protected = {
            state.state_id
            for state in self._states.values()
            if state.dependency_ref_count > 0 or state.state_id in self._lineage_state_ids
        }
        report.retry_carried_protected_count = len(retry_protected)
        report.dag_liveness_protected_count = len(dag_protected)
        candidates = [
            state
            for state in self._states.values()
            if state.state_id not in protected
            and state.state_id not in retry_protected
            and state.state_id not in dag_protected
            and state.lifecycle in {"active", "dormant", "outdated", "cooling", "evict_pending"}
            and state.tier == "cold"
        ]
        quota_candidates = self._quota_eviction_candidates(protected)
        candidates.extend(
            state
            for state in quota_candidates
            if state.state_id not in {item.state_id for item in candidates}
        )
        candidates.sort(key=lambda item: item.created_at)
        for state in candidates[:max_deleted]:
            if self.leases.active_readers(state.state_id) > 0:
                state.lifecycle = "cooling"
                report.read_lease_blocked_gc_count += 1
                report.cooling_state_count += 1
                continue
            self._tombstone_state(state, reason="v5_4_gc")
            report.state_gc_count += 1
            report.state_tombstone_count += 1
            if state in quota_candidates:
                report.quota_evicted_count += 1
        return report

    def sweep_tombstones(
        self,
        *,
        max_swept: int = 8,
        min_age_seconds: float | None = None,
    ) -> StateGCReport:
        report = StateGCReport()
        grace_seconds = (
            self.quota_config.tombstone_grace_seconds
            if min_age_seconds is None
            else max(0.0, min_age_seconds)
        )
        now = datetime.now(timezone.utc)
        evicted = [
            state
            for state in self._states.values()
            if state.lifecycle == "evicted" and self.leases.active_readers(state.state_id) == 0
            and state.evicted_at is not None
            and (now - self._parse_dt(state.evicted_at)).total_seconds() >= grace_seconds
        ]
        evicted.sort(key=lambda item: item.evicted_at or item.created_at)
        for state in evicted[:max_swept]:
            for payload_ref in [state.payload_ref, state.audit_payload_ref]:
                if payload_ref and _exists(payload_ref):
                    _unlink(payload_ref)
            old = state.lifecycle
            state.lifecycle = "deleted"
            state.version += 1
            self._hot_payloads.pop(state.state_id, None)
            self._raw_view_cache.pop(state.state_id, None)
            self._raw_chunk_index.pop(state.state_id, None)
            self._lineage_state_ids.discard(state.state_id)
            self._states.pop(state.state_id, None)
            report.physical_delete_count += 1
            report.lifecycle_transition_count += int(old != state.lifecycle)
        return report

    def transition_state(
        self, state_ref: StateRef, new_lifecycle: str, *, superseded_by: str | None = None
    ) -> StateLifecycleTransitionReport:
        if new_lifecycle not in STATE_LIFECYCLE_STATES:
            raise ValueError(f"Unknown state lifecycle: {new_lifecycle}")
        state = self._states[state_ref.state_id]
        old = state.lifecycle
        if old == new_lifecycle:
            return StateLifecycleTransitionReport(
                state_id=state.state_id,
                old_lifecycle=old,
                new_lifecycle=new_lifecycle,
                transitioned=False,
            )
        state.lifecycle = new_lifecycle
        state.version += 1
        state.status_updated_at = datetime.now(timezone.utc).isoformat()
        if superseded_by:
            state.superseded_by = superseded_by
        if new_lifecycle == "outdated":
            state.outdated_reason = "manual_transition"
        return StateLifecycleTransitionReport(
            state_id=state.state_id,
            old_lifecycle=old,
            new_lifecycle=new_lifecycle,
            transitioned=True,
        )

    def supersede_state(
        self, old_ref: StateRef, new_ref: StateRef, *, reason: str = "state_replaced"
    ) -> StateLifecycleTransitionReport:
        old_state = self._states[old_ref.state_id]
        old_state.replacement_state_id = new_ref.state_id
        old_state.outdated_reason = reason
        return self.transition_state(old_ref, "outdated", superseded_by=new_ref.state_id)

    def apply_lifecycle_transitions(
        self,
        *,
        dormant_after_seconds: float = 30 * 24 * 3600,
        outdated_after_seconds: float = 180 * 24 * 3600,
    ) -> StateGCReport:
        now = datetime.now(timezone.utc)
        report = StateGCReport()
        for state in self._states.values():
            if state.lifecycle not in {"active", "dormant"}:
                continue
            created_at = self._parse_dt(state.created_at)
            age_seconds = (now - created_at).total_seconds()
            if outdated_after_seconds >= 0 and age_seconds >= outdated_after_seconds:
                old = state.lifecycle
                state.lifecycle = "outdated"
                state.version += 1
                state.status_updated_at = now.isoformat()
                state.outdated_reason = "age_threshold"
                report.lifecycle_transition_count += int(old != state.lifecycle)
                continue
            if (
                dormant_after_seconds >= 0
                and age_seconds >= dormant_after_seconds
                and state.access_count == 0
                and state.lifecycle == "active"
            ):
                state.lifecycle = "dormant"
                state.version += 1
                state.status_updated_at = now.isoformat()
                report.lifecycle_transition_count += 1
        return report

    def write_retry_loop_summary(
        self,
        *,
        task_id: str,
        source_agent: str,
        attempts: list[dict[str, Any]],
        carried_state_refs: list[StateRef],
        max_attempts: int = 8,
    ) -> StateRef:
        budget_report = self.assess_loop_budget(
            attempts=attempts,
            carried_state_refs=carried_state_refs,
        )
        keep_attempts = min(
            max_attempts,
            self.loop_budget_config.keep_last_k_attempts,
            self.loop_budget_config.max_attempts,
        )
        compact_attempts = attempts[-keep_attempts:]
        compact_carried_refs = carried_state_refs[: self.loop_budget_config.max_carried_states]
        payload = {
            "attempt_count": len(attempts),
            "compressed_attempt_count": len(compact_attempts),
            "carried_state_ids": [ref.state_id for ref in compact_carried_refs],
            "loop_budget": asdict(budget_report),
            "attempt_summaries": [
                {
                    "attempt_id": item.get("attempt_id", index + 1),
                    "status": item.get("status", "unknown"),
                    "summary": str(item.get("summary", ""))[:240],
                }
                for index, item in enumerate(compact_attempts)
            ],
        }
        ref, _ = self.write_state(
            task_id=task_id,
            source_agent=source_agent,
            state_type="retry_loop_summary",
            payload=payload,
            summary=f"{task_id} retry loop summary: {len(attempts)} attempts",
            usage_hint="retry_history_compaction",
            lifecycle="retry_carried",
            tier="warm",
            retry_ref_count=len(compact_carried_refs),
        )
        return ref

    def assess_loop_budget(
        self, *, attempts: list[dict[str, Any]], carried_state_refs: list[StateRef]
    ) -> LoopBudgetReport:
        carried_bytes = 0
        for ref in carried_state_refs:
            state = self._states.get(ref.state_id)
            if state is not None:
                carried_bytes += state.size_bytes
        reasons: list[str] = []
        config = self.loop_budget_config
        if len(attempts) > config.max_attempts:
            reasons.append("max_attempts_exceeded")
        if len(carried_state_refs) > config.max_carried_states:
            reasons.append("max_carried_states_exceeded")
        if carried_bytes > config.max_loop_state_bytes:
            reasons.append("max_loop_state_bytes_exceeded")
        return LoopBudgetReport(
            allowed=not reasons,
            attempt_count=len(attempts),
            carried_state_count=len(carried_state_refs),
            carried_state_bytes=carried_bytes,
            max_attempts=config.max_attempts,
            max_loop_state_bytes=config.max_loop_state_bytes,
            max_carried_states=config.max_carried_states,
            reasons=reasons or ["within_loop_budget"],
        )

    def ref_to_dict(self, state_ref: StateRef) -> dict[str, Any]:
        return asdict(state_ref)

    def resolve_ref(self, state_id: str) -> StateRef | None:
        state = self._states.get(state_id)
        if state is None or state.lifecycle in {"deleted", "tombstoned", "evicted"}:
            return None
        return StateRef(
            state_id=state.state_id,
            state_type=state.state_type,
            version=state.version,
            payload_kind=state.payload_kind,
            contains_embedding_refs=state.contains_embedding_refs,
            usage_hint=state.state_type,
            tier=state.tier,
        )

    def validate_fencing_token(
        self,
        state_ref: StateRef,
        fencing_token: str,
        *,
        expected_version: int | None = None,
    ) -> bool:
        state = self._states.get(state_ref.state_id)
        if state is None or state.lifecycle in {"deleted", "evicted"}:
            return False
        if expected_version is not None and state.version != expected_version:
            return False
        return fencing_token in self.leases.active_fencing_tokens(state.state_id)

    def finalize_task(self, task_id: str) -> None:
        self._cold_read_counts.pop(task_id, None)
        self._cold_read_bytes.pop(task_id, None)
        self._prefetch_queue = [
            state_id
            for state_id in self._prefetch_queue
            if self._states.get(state_id) is not None
            and self._states[state_id].task_id != task_id
        ]

    def snapshot(self) -> dict[str, Any]:
        states = []
        for state in sorted(self._states.values(), key=lambda item: item.created_at):
            states.append(
                {
                    "state_id": state.state_id,
                    "state_type": state.state_type,
                    "task_id": state.task_id,
                    "source_agent": state.source_agent,
                    "created_at": state.created_at,
                    "payload_ref": state.payload_ref,
                    "payload_kind": state.payload_kind,
                    "contains_embedding_refs": state.contains_embedding_refs,
                    "summary": state.summary,
                    "size_bytes": state.size_bytes,
                    "tier": state.tier,
                    "lifecycle": state.lifecycle,
                    "content_hash": state.content_hash,
                    "access_policy": state.access_policy,
                    "audit_payload_ref": state.audit_payload_ref,
                    "version": state.version,
                    "dependency_ref_count": state.dependency_ref_count,
                    "retry_ref_count": state.retry_ref_count,
                    "superseded_by": state.superseded_by,
                    "gc_policy": state.gc_policy,
                    "fallback_summary": state.fallback_summary,
                    "replacement_state_id": state.replacement_state_id,
                    "evicted_at": state.evicted_at,
                    "active_readers": self.leases.active_readers(state.state_id),
                    "lineage_protected": state.state_id in self._lineage_state_ids,
                }
            )
        return {
            "states": states,
            "tombstones": list(self._tombstones.values()),
            "lineage_state_ids": sorted(self._lineage_state_ids),
            "hot_payload_count": len(self._hot_payloads),
        }

    def mark_lineage(self, state_ids: list[str]) -> None:
        self._lineage_state_ids.update(state_ids)

    def build_raw_chunk_index(
        self, state_ref: StateRef, *, chunk_chars: int = 1200
    ) -> list[RawChunk]:
        state = self._states[state_ref.state_id]
        if not state.audit_payload_ref or not _exists(state.audit_payload_ref):
            self._raw_chunk_index[state.state_id] = []
            return []
        with self.leases.read_lease(state.state_id, owner="raw_chunk_index"):
            payload = json.loads(_read_text(state.audit_payload_ref))
        text = self._payload_to_text(payload)
        chunks: list[RawChunk] = []
        for index, start in enumerate(range(0, len(text), max(1, chunk_chars)), start=1):
            end = min(len(text), start + chunk_chars)
            chunk_text = text[start:end]
            chunk = RawChunk(
                chunk_id=f"{state.state_id}:c_{index:03d}",
                state_id=state.state_id,
                span=f"{start}-{end}",
                offset_start=start,
                offset_end=end,
                summary=chunk_text[:160],
                tags=self._chunk_terms(chunk_text)[:8],
            )
            chunks.append(chunk)
        self._raw_chunk_index[state.state_id] = chunks
        return chunks

    def resolve_raw_span(
        self,
        state_ref: StateRef,
        *,
        query: str,
        max_chunks: int = 2,
    ) -> RawSpanResolutionReport:
        state = self._states[state_ref.state_id]
        chunks = self._raw_chunk_index.get(state.state_id) or self.build_raw_chunk_index(state_ref)
        query_terms = set(self._chunk_terms(query))
        scored: list[tuple[int, RawChunk]] = []
        for chunk in chunks:
            overlap = len(query_terms & set(chunk.tags))
            scored.append((overlap, chunk))
        scored.sort(key=lambda item: (-item[0], item[1].offset_start))
        selected = [chunk for _, chunk in scored[:max(1, max_chunks)]]
        cache_key = "|".join([state.state_id, query, ",".join(chunk.chunk_id for chunk in selected)])
        raw_view_id = f"rv_{hashlib.sha256(cache_key.encode('utf-8')).hexdigest()[:12]}"
        cached = self._raw_view_materialized.get(raw_view_id)
        if cached is not None:
            return RawSpanResolutionReport(
                state_id=state.state_id,
                query=query,
                matched_chunk_ids=list(cached.matched_chunk_ids),
                raw_view_id=cached.raw_view_id,
                content=cached.content,
                size_tokens=cached.size_tokens,
                cache_hit=True,
            )
        source_text = ""
        if state.audit_payload_ref and _exists(state.audit_payload_ref):
            with self.leases.read_lease(state.state_id, owner="raw_span"):
                source_text = self._payload_to_text(
                    json.loads(_read_text(state.audit_payload_ref))
                )
        content = "\n".join(
            source_text[chunk.offset_start : chunk.offset_end] for chunk in selected
        )
        report = RawSpanResolutionReport(
            state_id=state.state_id,
            query=query,
            matched_chunk_ids=[chunk.chunk_id for chunk in selected],
            raw_view_id=raw_view_id,
            content=content,
            size_tokens=max(1, (len(content) + 3) // 4),
            cache_hit=False,
        )
        self._raw_view_materialized[raw_view_id] = report
        return report

    def prefetch_raw_view(
        self, state_refs: list[StateRef], *, query: str, max_chunks: int = 2
    ) -> PrefetchReport:
        raw_view_ids: list[str] = []
        skipped = 0
        for ref in state_refs:
            state = self._states.get(ref.state_id)
            if state is None or not state.audit_payload_ref:
                skipped += 1
                continue
            self._prefetch_queue.append(ref.state_id)
            report = self.resolve_raw_span(ref, query=query, max_chunks=max_chunks)
            raw_view_ids.append(report.raw_view_id)
        return PrefetchReport(
            requested_count=len(state_refs),
            prefetched_count=len(raw_view_ids),
            skipped_count=skipped,
            raw_view_ids=raw_view_ids,
        )

    def _tombstone_state(self, state: StateObject, *, reason: str) -> None:
        if state.lifecycle == "evicted":
            return
        tombstone = {
            "state_id": state.state_id,
            "state_type": state.state_type,
            "task_id": state.task_id,
            "source_agent": state.source_agent,
            "evicted_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "payload_ref": state.payload_ref,
            "audit_payload_ref": state.audit_payload_ref,
            "content_hash": state.content_hash,
            "fallback_summary": state.fallback_summary or state.summary[:280],
            "replacement_state_id": state.replacement_state_id,
            "stage": "logical_eviction",
        }
        path = self.tombstone_dir / f"{state.state_id}.tombstone.json"
        _write_text(path, json.dumps(tombstone, ensure_ascii=False, indent=2))
        self._tombstones[state.state_id] = tombstone
        state.lifecycle = "evicted"
        state.evicted_at = tombstone["evicted_at"]
        state.version += 1

    def _quota_eviction_candidates(self, protected: set[str]) -> list[StateObject]:
        candidates: list[StateObject] = []
        by_task: dict[str, list[StateObject]] = {}
        for state in self._states.values():
            if (
                state.state_id in protected
                or state.state_id in self._lineage_state_ids
                or state.lifecycle == "retry_carried"
                or state.retry_ref_count > 0
                or state.dependency_ref_count > 0
                or state.lifecycle not in {"active", "dormant", "outdated", "cooling", "evict_pending", "archived"}
            ):
                continue
            by_task.setdefault(state.task_id, []).append(state)
        for states in by_task.values():
            states.sort(key=lambda item: item.created_at)
            total_bytes = sum(item.size_bytes for item in states)
            if len(states) > self.quota_config.max_task_states:
                candidates.extend(states[: len(states) - self.quota_config.max_task_states])
            while total_bytes > self.quota_config.max_task_bytes and states:
                candidate = states.pop(0)
                candidates.append(candidate)
                total_bytes -= candidate.size_bytes
            hot_states = [item for item in states if item.tier == "hot"]
            if len(hot_states) > self.quota_config.max_hot_states:
                candidates.extend(hot_states[: len(hot_states) - self.quota_config.max_hot_states])
        return candidates

    def _cold_budget_remaining(self, task_id: str) -> int:
        return max(
            0,
            self.cold_access_budget.max_cold_reads_per_task
            - self._cold_read_counts.get(task_id, 0),
        )

    def _mark_accessed(self, state: StateObject) -> None:
        state.access_count += 1
        state.last_accessed_at = datetime.now(timezone.utc).isoformat()

    def _prune_raw_view_cache(self) -> int:
        if not self._raw_view_cache:
            return 0
        max_bytes = max(1, self.cold_access_budget.max_raw_view_tokens * 4)
        total_bytes = sum(size for size, _ in self._raw_view_cache.values())
        evicted = 0
        for state_id in list(self._raw_view_cache.keys()):
            if total_bytes <= max_bytes:
                break
            size, _ = self._raw_view_cache.pop(state_id)
            total_bytes -= size
            evicted += 1
        return evicted

    def _payload_to_text(self, payload: Any) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, dict):
            parts: list[str] = []
            for key, value in payload.items():
                parts.append(f"{key}: {self._payload_to_text(value)}")
            return "\n".join(parts)
        if isinstance(payload, list):
            return "\n".join(self._payload_to_text(item) for item in payload)
        return str(payload)

    def _chunk_terms(self, text: str) -> list[str]:
        terms: list[str] = []
        current = []
        for char in text.lower():
            if char.isalnum() or "\u4e00" <= char <= "\u9fff":
                current.append(char)
            elif current:
                term = "".join(current)
                if term not in terms:
                    terms.append(term)
                current = []
        if current:
            term = "".join(current)
            if term not in terms:
                terms.append(term)
        return terms

    def _parse_dt(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _clamp01(self, value: float) -> float:
        return max(0.0, min(1.0, float(value)))

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

    def _tier_from_admission(
        self,
        *,
        state_type: str,
        payload: dict[str, Any],
        payload_bytes: int,
        admission: StateAdmissionReport,
        has_audit_payload: bool,
    ) -> StateTier:
        if not admission.admitted:
            return "cold" if has_audit_payload else "warm"
        if state_type == "artifact_state" or payload_bytes > 8192:
            return "cold"
        if admission.score >= 0.75:
            return "hot"
        return self._default_tier(state_type, payload)

    def _load_prompt_payload(self, state: StateObject) -> dict[str, Any]:
        if state.tier == "hot":
            payload = self._hot_payloads.get(state.state_id)
            if payload is not None:
                return dict(payload)
        return json.loads(_read_text(state.payload_ref))

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
