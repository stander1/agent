from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_runtime.memory.claim_extractor import (
    claim_identity,
    normalize_claim_cards,
    normalized_value,
)
from agent_runtime.memory.conflict_resolver import resolve_claim_conflicts
from agent_runtime.memory.references import (
    MemoryReferenceManagerLite,
    MemoryReferenceRecord,
)
from agent_runtime.memory.schema_registry import (
    SchemaRegistryLite,
    SlotPolicy,
    SUPERSESSION_RELATION_TYPES,
    canonical_claim_polarity,
)

_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]")

CANONICAL_SLOTS = {
    "slot.system.optimization_claim",
    "slot.user.preference.programming_language",
    "slot.project.implementation_language",
    "slot.runtime.execution_language",
    "slot.system.design_decision",
    "slot.system.failure_pattern",
    "slot.system.reuse_strategy",
    "slot.paper.evidence",
    "slot.project.requirement",
    "slot.system.deliverable_requirement",
    "slot.project.state",
    "slot.runtime.config",
    "slot.tool.result",
    "slot.user.preference",
}

ALIAS_MAPPING = {
    "coding_lang": "slot.runtime.execution_language",
    "runtime_language": "slot.runtime.execution_language",
    "programming_language": "slot.project.implementation_language",
    "implementation_language": "slot.project.implementation_language",
    "user_favorite_language": "slot.user.preference.programming_language",
    "preferred_programming_language": "slot.user.preference.programming_language",
    "failure_reason": "slot.system.failure_pattern",
    "error_pattern": "slot.system.failure_pattern",
    "reuse_hint": "slot.system.reuse_strategy",
    "reuse_strategy": "slot.system.reuse_strategy",
    "final_deliverable": "slot.system.deliverable_requirement",
    "project_state": "slot.project.state",
    "runtime_config": "slot.runtime.config",
    "tool_result": "slot.tool.result",
    "user_preference": "slot.user.preference",
}

SLOT_POLICIES = {
    "slot.project.requirement": SlotPolicy(
        slot_id="slot.project.requirement",
        conflict_policy="latest_explicit_state_wins",
        scope_required=True,
        temporal_required=True,
    ),
    "slot.project.state": SlotPolicy(
        slot_id="slot.project.state",
        conflict_policy="latest_explicit_state_wins",
        scope_required=True,
        temporal_required=True,
    ),
    "slot.system.design_decision": SlotPolicy(
        slot_id="slot.system.design_decision",
        conflict_policy="latest_explicit_state_wins",
        scope_required=True,
        temporal_required=True,
    ),
    "slot.runtime.config": SlotPolicy(
        slot_id="slot.runtime.config",
        conflict_policy="latest_explicit_state_wins",
        scope_required=True,
        temporal_required=True,
    ),
    "slot.user.preference": SlotPolicy(
        slot_id="slot.user.preference",
        conflict_policy="latest_valid_high_confidence_wins",
        scope_required=True,
        temporal_required=True,
    ),
    "slot.paper.evidence": SlotPolicy(
        slot_id="slot.paper.evidence",
        conflict_policy="verified_source_wins",
        scope_required=True,
    ),
    "slot.tool.result": SlotPolicy(
        slot_id="slot.tool.result",
        conflict_policy="tool_verified_wins",
        scope_required=True,
        temporal_required=True,
    ),
}

PROMPT_VIEW_MEMORY_STATUSES = {"active", "provisional_active"}
REUSABLE_CLAIM_CERTAINTIES = {
    "observed",
    "asserted",
    "confirmed",
    "verified",
}
DORMANT_MEMORY_STATUSES = {"dormant"}
BLOCKED_MEMORY_STATUSES = {
    "deprecated",
    "deleted",
    "legacy_audit",
    "superseded",
    "outdated",
    "unresolved_conflict",
    "pending_lifecycle_update",
}
MEMORY_LIFECYCLE_STATUSES = PROMPT_VIEW_MEMORY_STATUSES | DORMANT_MEMORY_STATUSES | BLOCKED_MEMORY_STATUSES


def classify_memory_quality_envelope(
    *,
    confidence: float,
    importance_hint: float,
    coverage_score: float,
    compression_loss_risk: str = "medium",
    raw_required_hint: bool = False,
) -> tuple[str, tuple[str, ...]]:
    """Classify quality independently of slot resolution and source structure."""

    if float(confidence) < 0.35:
        return "rejected", ("confidence_below_rejection_threshold",)
    if raw_required_hint and str(compression_loss_risk) == "high":
        return "audit_only", ("high_loss_raw_required",)
    reasons: list[str] = []
    if float(confidence) < 0.55:
        reasons.append("confidence_below_admission_threshold")
    if float(importance_hint) < 0.45:
        reasons.append("importance_below_admission_threshold")
    if float(coverage_score) < 0.35:
        reasons.append("coverage_below_admission_threshold")
    if reasons:
        return "pending", tuple(reasons)
    return "admitted", ("rules_admitted",)


@dataclass(slots=True)
class MemoryRef:
    memory_id: str
    version_id: int
    status: str
    task_topic: str
    memory_view_id: str
    slot_id: str


@dataclass(slots=True)
class PromotionView:
    promotion_view_id: str
    source_state_ids: list[str]
    core_claim: str
    evidence_refs: list[str]
    reuse_intent: str


@dataclass(slots=True)
class ClaimCard:
    claim_id: str
    subject: str
    slot_id: str
    summary: str
    source_agent: str
    source_task_id: str
    promotion_view_id: str | None
    confidence: float
    status: str
    tags: list[str]
    created_at: str
    claim_type: str = "fact"
    value: str = ""
    polarity: str = "positive"
    modality: str = "asserted"
    temporal_scope: str = "current_task"
    schema_version: str = "ccf.v1-lite"
    conflict_policy: str = "highest_confidence_then_latest"
    candidate_id: str = ""
    raw_text: str = ""
    raw_slot_text: str = ""
    scope: str = "general"
    certainty: str = "asserted"
    valid_from: str = ""
    valid_to: str | None = None
    value_type: str = "string"
    unit: str = ""
    slot_mapping_confidence: float = 1.0
    supported_by: list[str] = field(default_factory=list)
    revision_kind: str = "asserted"
    semantic_key: str = ""
    assertion_type: str = "fact"
    operator: str = "eq"
    temporal_status: str = "unspecified"
    source_span: dict[str, Any] = field(default_factory=dict)
    relations: list[dict[str, Any]] = field(default_factory=list)
    schema_layer: str = "core"


@dataclass(slots=True)
class MemoryView:
    memory_view_id: str
    slot_id: str
    active_claim_ids: list[str]
    prompt_summary: str
    audit_claim_ids: list[str]
    created_at: str
    status: str = "active"
    historical_claim_ids: list[str] = field(default_factory=list)
    conflicting_claim_ids: list[str] = field(default_factory=list)
    resolution_status: str = "resolved"
    downstream_policy: str = "use_active_claims_only"
    subject: str = ""
    scope: str = "general"
    temporal_scope: str = "current_task"
    semantic_key: str = ""
    active_value: dict[str, Any] | None = None
    historical_values: list[dict[str, Any]] = field(default_factory=list)
    conflicting_values: list[dict[str, Any]] = field(default_factory=list)
    version_id: int = 1
    schema_version: str = "ccf.v1-lite"


@dataclass(slots=True)
class MemoryObject:
    memory_id: str
    version_id: int
    task_id: str
    source_agent: str
    task_topic: str
    summary: str
    tags: list[str]
    created_at: str
    slot_id: str
    claim_id: str
    memory_view_id: str
    promotion_view_id: str | None = None
    status: str = "active"
    hit_count: int = 0
    useful_hit_count: int = 0
    wrong_hit_count: int = 0
    mixed_hit_count: int = 0
    last_accessed_at: str | None = None
    status_updated_at: str | None = None


@dataclass(slots=True)
class MemoryWriteReport:
    memory_ref: MemoryRef
    memory_write_count: int = 1
    claim_card_count: int = 1
    memory_view_count: int = 1
    promotion_view_count: int = 0
    alias_mapping_hit_count: int = 0
    unresolved_slot_count: int = 0
    superseded_claim_count: int = 0
    conflict_resolved_count: int = 0
    memory_reference_count: int = 0


@dataclass(slots=True)
class MemoryCandidate:
    candidate_id: str
    task_id: str
    source_agent: str
    task_topic: str
    summary: str
    key_points: list[str]
    tags: list[str]
    reuse_scope: list[str]
    confidence: float
    importance_hint: float
    coverage_score: float
    compression_loss_risk: str
    raw_required_hint: bool
    slot_hint: str
    source_state_ids: list[str]
    evidence_refs: list[str]
    created_at: str
    admission_status: str = "pending"
    admission_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ClaimCandidate:
    candidate_id: str
    memory_candidate_id: str
    subject: str
    predicate: str
    object: str
    condition: str
    confidence: float
    source_pointer: str
    admission_status: str = "pending"
    raw_slot_text: str = ""
    slot_id: str = ""
    scope: str = "general"
    value_type: str = "string"
    unit: str = ""
    raw_text: str = ""
    summary: str = ""
    certainty: str = "asserted"
    modality: str = "asserted"
    polarity: str = "positive"
    revision_kind: str = "asserted"
    temporal_scope: str = "cross_task"
    schema_version: str = "ccf.v2"
    assertion_type: str = "fact"
    operator: str = "eq"
    temporal_status: str = "unspecified"
    source_span: dict[str, Any] = field(default_factory=dict)
    relations: list[dict[str, Any]] = field(default_factory=list)
    schema_layer: str = "core"


@dataclass(slots=True)
class MemoryAdmissionReport:
    candidate_id: str
    admission_status: str
    admission_reasons: list[str] = field(default_factory=list)
    memory_ref: MemoryRef | None = None
    memory_candidate_count: int = 1
    claim_candidate_count: int = 0
    raw_claim_count: int = 0
    provisional_claim_count: int = 0
    slot_mapping_success_count: int = 0
    memory_admitted_count: int = 0
    memory_rejected_count: int = 0
    memory_pending_count: int = 0
    memory_audit_only_count: int = 0
    admission_unresolved_slot_count: int = 0
    claim_to_memoryview_count: int = 0
    memory_write_count: int = 0
    claim_card_count: int = 0
    memory_view_count: int = 0
    promotion_view_count: int = 0
    alias_mapping_hit_count: int = 0
    unresolved_slot_count: int = 0
    memory_refs: list[MemoryRef] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    memory_view_ids: list[str] = field(default_factory=list)
    unresolved_scope_count: int = 0
    conflict_detected_count: int = 0
    resolved_conflict_count: int = 0
    unresolved_conflict_count: int = 0
    active_value_selection_count: int = 0
    deduplicated_claim_count: int = 0
    deduplicated_memory_count: int = 0
    evidence_reference_merge_count: int = 0
    epistemic_deferred_count: int = 0


@dataclass(slots=True)
class PreflightValidationReport:
    allowed: bool
    checked_count: int = 0
    blocked_count: int = 0
    stale_read_detected_count: int = 0
    missing_memory_count: int = 0
    blocked_memory_ids: list[str] = field(default_factory=list)
    valid_refs: list[MemoryRef] = field(default_factory=list)


@dataclass(slots=True)
class MemoryStatusTransitionReport:
    memory_id: str
    old_status: str
    new_status: str
    transitioned: bool


@dataclass(slots=True)
class MemoryLifecycleSweepReport:
    checked_count: int = 0
    dormant_transition_count: int = 0
    outdated_transition_count: int = 0
    skipped_count: int = 0


@dataclass(slots=True)
class CompensatingEvent:
    event_id: str
    event_type: str
    target_memory_id: str
    target_view_id: str
    reason: str
    replacement_memory_id: str | None
    created_at: str
    created_by: str
    downstream_policy: str = "do_not_use_without_refresh"


@dataclass(slots=True)
class WriteIntentFence:
    fence_id: str
    task_id: str
    section_id: str
    locked_by: str
    memory_ids: list[str]
    expected_versions: dict[str, int]
    estimated_cost_tokens: int
    created_at: str
    expires_at: str
    allowed: bool
    blocked_memory_ids: list[str] = field(default_factory=list)
    transition_flag: str = "pending_lifecycle_update"


@dataclass(slots=True)
class PatchRegenerationReport:
    section_id: str
    regenerated: bool
    stale_memory_ids: list[str]
    patch_summary: str
    estimated_saved_tokens: int = 0


@dataclass(slots=True)
class MemoryLayerReport:
    warm_db_path: str
    cold_dir: str
    warm_record_count: int
    cold_record_count: int


@dataclass(slots=True)
class MemorySearchReport:
    refs: list[MemoryRef]
    retrieval_backend: str = "keyword_overlap_v3_lite"
    vector_retrieval_count: int = 0
    alias_mapping_hit_count: int = 0
    unresolved_slot_count: int = 0
    semantic_filter_rejected_count: int = 0
    scope_only_retrieval: bool = False


@dataclass(slots=True)
class MemoryCompactionReport:
    compaction_id: str
    memory_view_count: int = 0
    new_claim_count: int = 0
    merged_claim_count: int = 0
    superseded_claim_count: int = 0
    conflict_detected_count: int = 0
    resolved_conflict_count: int = 0
    unresolved_conflict_count: int = 0
    active_value_selection_count: int = 0
    compaction_log_count: int = 1


@dataclass(slots=True)
class SlotResolution:
    slot_id: str
    alias_hit: bool = False
    unresolved: bool = False


class MemoryStoreLite:
    """In-memory v3-lite ClaimCard and MemoryView store."""

    def __init__(
        self,
        storage_dir: Path | None = None,
        *,
        canonical_slots: set[str] | None = None,
        alias_mapping: dict[str, str] | None = None,
    ) -> None:
        resolved_slots = set(CANONICAL_SLOTS) | set(canonical_slots or set())
        resolved_aliases = dict(ALIAS_MAPPING)
        resolved_aliases.update(alias_mapping or {})
        resolved_policies = {
            slot_id: SLOT_POLICIES.get(slot_id, SlotPolicy(slot_id=slot_id))
            for slot_id in resolved_slots
        }
        self.schema_registry = SchemaRegistryLite(
            resolved_slots,
            resolved_aliases,
            slot_policies=resolved_policies,
        )
        self.storage_dir = storage_dir
        if self.storage_dir is not None:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._memories: dict[str, MemoryObject] = {}
        self._claims: dict[str, ClaimCard] = {}
        self._views: dict[str, MemoryView] = {}
        self._promotion_views: dict[str, PromotionView] = {}
        self._memory_candidates: dict[str, MemoryCandidate] = {}
        self._claim_candidates: dict[str, ClaimCandidate] = {}
        self._compaction_log: list[dict[str, Any]] = []
        self._compensating_events: list[CompensatingEvent] = []
        self._write_intents: dict[str, WriteIntentFence] = {}
        self._patch_regeneration_log: list[PatchRegenerationReport] = []
        self._section_dependency_map: dict[str, list[str]] = {}
        self.reference_manager = MemoryReferenceManagerLite()
        self.warm_db_path = self.storage_dir / "memory_warm.sqlite" if self.storage_dir else None
        self.cold_memory_dir = self.storage_dir / "memory_cold" if self.storage_dir else None
        self._init_layered_storage()
        self._load_layered_storage()
        self._migrate_loaded_legacy_claims()

    def write_memory(
        self,
        *,
        task_id: str,
        source_agent: str,
        task_topic: str,
        summary: str,
        tags: list[str],
        slot_hint: str | None = None,
        source_state_ids: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        reuse_intent: str | None = None,
        confidence: float = 0.78,
        claim_type: str = "fact",
        polarity: str = "positive",
        modality: str = "asserted",
        temporal_scope: str = "current_task",
        schema_version: str = "ccf.v1-lite",
    ) -> MemoryRef:
        return self.write_memory_with_report(
            task_id=task_id,
            source_agent=source_agent,
            task_topic=task_topic,
            summary=summary,
            tags=tags,
            slot_hint=slot_hint,
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            reuse_intent=reuse_intent,
            confidence=confidence,
            claim_type=claim_type,
            polarity=polarity,
            modality=modality,
            temporal_scope=temporal_scope,
            schema_version=schema_version,
        ).memory_ref

    def write_memory_with_report(
        self,
        *,
        task_id: str,
        source_agent: str,
        task_topic: str,
        summary: str,
        tags: list[str],
        slot_hint: str | None = None,
        source_state_ids: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        reuse_intent: str | None = None,
        confidence: float = 0.78,
        claim_type: str = "fact",
        polarity: str = "positive",
        modality: str = "asserted",
        temporal_scope: str = "current_task",
        schema_version: str = "ccf.v1-lite",
    ) -> MemoryWriteReport:
        resolved = self._resolve_slot(slot_hint or self._infer_slot_hint(tags, task_topic))
        source_state_ids = source_state_ids or []
        evidence_refs = evidence_refs or source_state_ids[:3]
        promotion_view_id = None
        promotion_count = 0
        if source_state_ids:
            promotion_view_id = self._next_id("pv", task_id, source_agent, summary)
            self._promotion_views[promotion_view_id] = PromotionView(
                promotion_view_id=promotion_view_id,
                source_state_ids=source_state_ids,
                core_claim=summary,
                evidence_refs=evidence_refs,
                reuse_intent=reuse_intent or f"复用 {task_topic} 的阶段结论",
            )
            promotion_count = 1

        claim_id = self._next_id("claim", task_id, source_agent, summary)
        now = datetime.now(timezone.utc).isoformat()
        canonical_claim = self.schema_registry.canonicalize_claim(
            subject=task_topic,
            slot_id=resolved.slot_id,
            value=summary,
            source_agent=source_agent,
            confidence=confidence,
            scope="general",
            claim_type=claim_type,
            polarity=polarity,
            modality=modality,
            temporal_scope=temporal_scope,
            certainty="asserted",
            valid_from=task_id,
            schema_version=schema_version,
        )
        slot_policy = self.schema_registry.policy_for(resolved.slot_id)
        claim = ClaimCard(
            claim_id=claim_id,
            subject=canonical_claim.subject,
            slot_id=canonical_claim.slot_id,
            summary=summary,
            source_agent=source_agent,
            source_task_id=task_id,
            promotion_view_id=promotion_view_id,
            confidence=canonical_claim.confidence,
            status="active" if not resolved.unresolved else "unresolved_slot",
            tags=tags,
            created_at=now,
            claim_type=canonical_claim.claim_type,
            value=canonical_claim.value,
            polarity=canonical_claim.polarity,
            modality=canonical_claim.modality,
            temporal_scope=canonical_claim.temporal_scope,
            schema_version=canonical_claim.schema_version,
            conflict_policy=slot_policy.conflict_policy,
            raw_text=summary,
            raw_slot_text=slot_hint or resolved.slot_id,
            scope=canonical_claim.scope,
            certainty=canonical_claim.certainty,
            valid_from=canonical_claim.valid_from,
            valid_to=canonical_claim.valid_to,
            value_type=canonical_claim.value_type,
            unit=canonical_claim.unit,
            slot_mapping_confidence=0.0 if resolved.unresolved else 1.0,
            supported_by=self._dedupe([*source_state_ids, *evidence_refs]),
        )
        claim.semantic_key = self._semantic_key(claim)
        self._claims[claim_id] = claim

        memory_view, superseded_count, conflict_count = self._upsert_memory_view(claim)
        memory_id = self._next_memory_id(task_id, source_agent, summary)
        memory = MemoryObject(
            memory_id=memory_id,
            version_id=1,
            task_id=task_id,
            source_agent=source_agent,
            task_topic=task_topic,
            summary=summary,
            tags=tags,
            created_at=now,
            slot_id=resolved.slot_id,
            claim_id=claim_id,
            memory_view_id=memory_view.memory_view_id,
            promotion_view_id=promotion_view_id,
            status=claim.status,
            status_updated_at=now,
        )
        self._memories[memory_id] = memory
        memory_reference_count = self._create_memory_references(
            memory=memory,
            claim_id=claim_id,
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            promotion_view_id=promotion_view_id,
        )
        report = MemoryWriteReport(
            memory_ref=self.ref(memory),
            promotion_view_count=promotion_count,
            alias_mapping_hit_count=int(resolved.alias_hit),
            unresolved_slot_count=int(resolved.unresolved),
            superseded_claim_count=superseded_count,
            conflict_resolved_count=conflict_count,
            memory_reference_count=memory_reference_count,
        )
        self._persist_snapshot()
        return report

    def write_memory_candidate_with_report(
        self,
        *,
        task_id: str,
        source_agent: str,
        task_topic: str,
        memory_card: dict[str, Any] | None,
        claim_cards: list[dict[str, Any]] | None,
        tags: list[str],
        slot_hint: str | None = None,
        source_state_ids: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        reuse_intent: str | None = None,
        fallback_summary: str = "",
    ) -> MemoryAdmissionReport:
        card = memory_card if isinstance(memory_card, dict) else {}
        summary = str(card.get("summary") or fallback_summary).strip()
        key_points = self._as_str_list(card.get("key_points"))
        card_tags = self._as_str_list(card.get("tags"))
        reuse_scope = self._as_str_list(card.get("reuse_scope"))
        candidate_tags = self._dedupe(tags + card_tags)
        confidence = self._float_between(card.get("confidence"), default=0.5)
        importance_hint = self._float_between(card.get("importance_hint"), default=0.5)
        coverage_score = self._float_between(card.get("coverage_score"), default=0.5)
        compression_loss_risk = str(
            card.get("compression_loss_risk") or "medium"
        ).lower()
        raw_required_hint = bool(card.get("raw_required_hint", False))
        candidate_slot_hint = str(
            card.get("slot_id") or card.get("slot_hint") or slot_hint or ""
        )
        if not candidate_slot_hint:
            candidate_slot_hint = self._infer_slot_hint(candidate_tags, task_topic)

        source_state_ids = source_state_ids or []
        evidence_refs = evidence_refs or source_state_ids[:3]
        now = datetime.now(timezone.utc).isoformat()
        candidate_id = self._next_candidate_id(
            "mc", task_id, source_agent, summary or task_topic
        )
        candidate = MemoryCandidate(
            candidate_id=candidate_id,
            task_id=task_id,
            source_agent=source_agent,
            task_topic=task_topic,
            summary=summary,
            key_points=key_points,
            tags=candidate_tags,
            reuse_scope=reuse_scope,
            confidence=confidence,
            importance_hint=importance_hint,
            coverage_score=coverage_score,
            compression_loss_risk=compression_loss_risk,
            raw_required_hint=raw_required_hint,
            slot_hint=candidate_slot_hint,
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            created_at=now,
        )

        claim_candidates = self._capture_claim_candidates(
            candidate_id=candidate_id,
            task_topic=task_topic,
            claim_cards=claim_cards or [],
            default_slot_hint=candidate_slot_hint,
            default_confidence=confidence,
            source_pointer=",".join(evidence_refs),
        )
        reusable_claim_candidates = [
            claim
            for claim in claim_candidates
            if claim.certainty.casefold() in REUSABLE_CLAIM_CERTAINTIES
        ]
        epistemic_deferred = [
            claim
            for claim in claim_candidates
            if claim.certainty.casefold() not in REUSABLE_CLAIM_CERTAINTIES
        ]
        resolved = self._resolve_slot(candidate.slot_hint)
        if resolved.unresolved:
            claim_resolutions = [
                self._resolve_slot(item.slot_id or item.raw_slot_text)
                for item in claim_candidates
            ]
            resolved = next(
                (
                    item
                    for item in claim_resolutions
                    if not item.unresolved
                ),
                resolved,
            )
        status, reasons = self._admit_candidate(candidate, resolved)
        if status == "admitted" and not claim_candidates:
            status = "audit_only"
            reasons = ["no_structured_claims"]
        elif status == "admitted" and not reusable_claim_candidates:
            status = "audit_only"
            reasons = ["epistemic_confirmation_required"]
        candidate.admission_status = status
        candidate.admission_reasons = reasons
        self._memory_candidates[candidate_id] = candidate
        for claim in claim_candidates:
            claim.admission_status = (
                "pending_confirmation"
                if claim in epistemic_deferred
                else status
            )
            self._claim_candidates[claim.candidate_id] = claim

        report = MemoryAdmissionReport(
            candidate_id=candidate_id,
            admission_status=status,
            admission_reasons=reasons,
            claim_candidate_count=len(claim_candidates),
            raw_claim_count=len(claim_candidates),
            memory_admitted_count=int(status == "admitted"),
            memory_rejected_count=int(status == "rejected"),
            memory_pending_count=int(status == "pending"),
            memory_audit_only_count=int(status == "audit_only"),
            admission_unresolved_slot_count=int(status == "unresolved_slot"),
            alias_mapping_hit_count=int(resolved.alias_hit),
            unresolved_slot_count=int(resolved.unresolved),
            epistemic_deferred_count=len(epistemic_deferred),
        )

        if status != "admitted":
            self._persist_snapshot()
            return report

        promotion_view_id = self._store_candidate_promotion_view(
            task_id=task_id,
            source_agent=source_agent,
            summary=summary,
            task_topic=task_topic,
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            reuse_intent=reuse_intent,
        )
        affected_view_ids: set[str] = set()
        newly_written_memory_ids: set[str] = set()
        successful_mapping_count = 0
        for claim_candidate in reusable_claim_candidates:
            memory, alias_hit, unresolved, unresolved_scope, deduplicated = (
                self._write_admitted_claim_candidate(
                candidate=claim_candidate,
                task_id=task_id,
                source_agent=source_agent,
                task_topic=task_topic,
                tags=candidate_tags,
                source_state_ids=source_state_ids,
                evidence_refs=evidence_refs,
                promotion_view_id=promotion_view_id,
                )
            )
            report.alias_mapping_hit_count += int(alias_hit)
            report.unresolved_slot_count += int(unresolved)
            report.unresolved_scope_count += int(unresolved_scope)
            if memory is None:
                claim_candidate.admission_status = (
                    "unresolved_slot" if unresolved else "unresolved_scope"
                )
                continue
            successful_mapping_count += 1
            if deduplicated:
                report.deduplicated_claim_count += 1
                report.deduplicated_memory_count += 1
                report.evidence_reference_merge_count += 1
            else:
                newly_written_memory_ids.add(memory.memory_id)
            affected_view_ids.add(memory.memory_view_id)
            report.memory_refs.append(self.ref(memory))
            report.claim_ids.append(memory.claim_id)
            report.memory_view_ids.append(memory.memory_view_id)

        if not report.memory_refs:
            report.admission_status = (
                "unresolved_slot"
                if report.unresolved_slot_count
                else "unresolved_scope"
            )
            report.admission_reasons = [
                "all_claim_slots_or_scopes_unresolved"
            ]
            report.memory_admitted_count = 0
            report.admission_unresolved_slot_count = int(
                report.admission_status == "unresolved_slot"
            )
            self._persist_snapshot()
            return report

        compaction_report = self.run_compaction(view_ids=affected_view_ids)
        report.conflict_detected_count = compaction_report.conflict_detected_count
        report.resolved_conflict_count = compaction_report.resolved_conflict_count
        report.unresolved_conflict_count = (
            compaction_report.unresolved_conflict_count
        )
        report.active_value_selection_count = (
            compaction_report.active_value_selection_count
        )
        unique_memory_ids = list(
            dict.fromkeys(
                ref.memory_id
                for ref in report.memory_refs
                if ref.memory_id in self._memories
            )
        )
        report.memory_refs = [
            self.ref(self._memories[memory_id])
            for memory_id in unique_memory_ids
        ]
        report.claim_ids = list(dict.fromkeys(report.claim_ids))
        report.memory_view_ids = list(dict.fromkeys(report.memory_view_ids))
        report.memory_ref = report.memory_refs[0]
        report.memory_write_count = len(newly_written_memory_ids)
        report.provisional_claim_count = len(newly_written_memory_ids)
        report.slot_mapping_success_count = successful_mapping_count
        report.claim_card_count = len(report.claim_ids)
        report.memory_view_count = len(report.memory_view_ids)
        report.promotion_view_count = int(promotion_view_id is not None)
        report.claim_to_memoryview_count = successful_mapping_count
        self._persist_snapshot()
        return report

    def _store_candidate_promotion_view(
        self,
        *,
        task_id: str,
        source_agent: str,
        summary: str,
        task_topic: str,
        source_state_ids: list[str],
        evidence_refs: list[str],
        reuse_intent: str | None,
    ) -> str | None:
        if not source_state_ids:
            return None
        normalized_states = self._dedupe(source_state_ids)
        normalized_evidence = self._dedupe(evidence_refs)
        for existing in self._promotion_views.values():
            if (
                existing.source_state_ids == normalized_states
                and existing.evidence_refs == normalized_evidence
                and existing.core_claim.strip() == summary.strip()
            ):
                return existing.promotion_view_id
        promotion_view_id = self._next_id("pv", task_id, source_agent, summary)
        self._promotion_views[promotion_view_id] = PromotionView(
            promotion_view_id=promotion_view_id,
            source_state_ids=normalized_states,
            core_claim=summary,
            evidence_refs=normalized_evidence,
            reuse_intent=reuse_intent or f"复用 {task_topic} 的结构化事实",
        )
        return promotion_view_id

    def _write_admitted_claim_candidate(
        self,
        *,
        candidate: ClaimCandidate,
        task_id: str,
        source_agent: str,
        task_topic: str,
        tags: list[str],
        source_state_ids: list[str],
        evidence_refs: list[str],
        promotion_view_id: str | None,
    ) -> tuple[MemoryObject | None, bool, bool, bool, bool]:
        resolved = self._resolve_slot(
            candidate.slot_id or candidate.raw_slot_text
        )
        if resolved.unresolved:
            return None, resolved.alias_hit, True, False, False
        policy = self.schema_registry.policy_for(resolved.slot_id)
        if policy.scope_required and candidate.scope in {"", "general"}:
            return None, resolved.alias_hit, False, True, False
        canonical = self.schema_registry.canonicalize_claim(
            subject=candidate.subject,
            slot_id=resolved.slot_id,
            value=candidate.object,
            source_agent=source_agent,
            confidence=candidate.confidence,
            scope=candidate.scope,
            claim_type="fact",
            polarity=candidate.polarity,
            modality=candidate.modality,
            temporal_scope=candidate.temporal_scope,
            certainty=candidate.certainty,
            value_type=candidate.value_type,
            unit=candidate.unit,
            valid_from=task_id,
            schema_version=candidate.schema_version,
        )
        semantic_key = claim_identity(
            {
                "subject": canonical.subject,
                "slot_id": canonical.slot_id,
                "scope": canonical.scope,
                "temporal_scope": canonical.temporal_scope,
            }
        )
        canonical_value = normalized_value(
            canonical.value,
            canonical.value_type,
            canonical.unit,
        )
        candidate.relations = self._bind_predecessor_relations(
            relations=candidate.relations,
            semantic_key=semantic_key,
            value=canonical.value,
            value_type=canonical.value_type,
            unit=canonical.unit,
            polarity=canonical.polarity,
        )
        if any(
            relation.get("relation_type") in SUPERSESSION_RELATION_TYPES
            and relation.get("target_candidate_id")
            for relation in candidate.relations
        ):
            candidate.revision_kind = "replaces"
        existing_claim = next(
            (
                claim
                for claim in self._claims.values()
                if claim.semantic_key == semantic_key
                and claim.polarity == canonical.polarity
                and claim.status in {"active", "provisional_active"}
                and (
                    canonical.temporal_scope in {"cross_task", "persistent", "global"}
                    or claim.valid_from == canonical.valid_from
                )
                and normalized_value(
                    claim.value,
                    claim.value_type,
                    claim.unit,
                )
                == canonical_value
            ),
            None,
        )
        if existing_claim is not None:
            existing_memory = next(
                (
                    memory
                    for memory in self._memories.values()
                    if memory.claim_id == existing_claim.claim_id
                    and memory.status in {"active", "provisional_active", "dormant"}
                ),
                None,
            )
            if existing_memory is not None:
                existing_claim.confidence = max(
                    existing_claim.confidence,
                    canonical.confidence,
                )
                existing_claim.supported_by = self._dedupe(
                    [
                        *existing_claim.supported_by,
                        *source_state_ids,
                        *evidence_refs,
                        candidate.source_pointer,
                    ]
                )
                existing_claim.tags = self._dedupe([*existing_claim.tags, *tags])
                existing_memory.tags = self._dedupe(
                    [*existing_memory.tags, *tags]
                )
                self._create_memory_references(
                    memory=existing_memory,
                    claim_id=existing_claim.claim_id,
                    source_state_ids=source_state_ids,
                    evidence_refs=evidence_refs,
                    promotion_view_id=promotion_view_id,
                )
                return (
                    existing_memory,
                    resolved.alias_hit,
                    False,
                    False,
                    True,
                )
        now = datetime.now(timezone.utc).isoformat()
        claim_id = self._next_id(
            "claim",
            task_id,
            source_agent,
            f"{canonical.slot_id}:{canonical.scope}:{canonical.value}",
        )
        claim = ClaimCard(
            claim_id=claim_id,
            subject=canonical.subject,
            slot_id=canonical.slot_id,
            summary=candidate.summary or candidate.raw_text or canonical.value,
            source_agent=source_agent,
            source_task_id=task_id,
            promotion_view_id=promotion_view_id,
            confidence=canonical.confidence,
            status="provisional_active",
            tags=list(tags),
            created_at=now,
            claim_type=canonical.claim_type,
            value=canonical.value,
            polarity=canonical.polarity,
            modality=canonical.modality,
            temporal_scope=canonical.temporal_scope,
            schema_version=canonical.schema_version,
            conflict_policy=policy.conflict_policy,
            candidate_id=candidate.candidate_id,
            raw_text=candidate.raw_text,
            raw_slot_text=candidate.raw_slot_text,
            scope=canonical.scope,
            certainty=canonical.certainty,
            valid_from=canonical.valid_from,
            valid_to=canonical.valid_to,
            value_type=canonical.value_type,
            unit=canonical.unit,
            slot_mapping_confidence=0.95 if resolved.alias_hit else 1.0,
            supported_by=self._dedupe(
                [*source_state_ids, *evidence_refs, candidate.source_pointer]
            ),
            revision_kind=candidate.revision_kind,
            assertion_type=candidate.assertion_type,
            operator=candidate.operator,
            temporal_status=candidate.temporal_status,
            source_span=dict(candidate.source_span),
            relations=[
                dict(relation) for relation in candidate.relations
            ],
            schema_layer=candidate.schema_layer,
        )
        claim.semantic_key = semantic_key
        self._claims[claim_id] = claim

        view_id = (
            f"view_{hashlib.sha256(claim.semantic_key.encode('utf-8')).hexdigest()[:10]}"
        )
        view = self._views.get(view_id)
        if view is None:
            view = MemoryView(
                memory_view_id=view_id,
                slot_id=claim.slot_id,
                active_claim_ids=[],
                prompt_summary="",
                audit_claim_ids=[],
                created_at=now,
                status="provisional_active",
                subject=claim.subject,
                scope=claim.scope,
                temporal_scope=claim.temporal_scope,
                semantic_key=claim.semantic_key,
                active_value=None,
                resolution_status="pending_compaction",
                downstream_policy="do_not_use_for_final_generation",
                schema_version=claim.schema_version,
            )
            self._views[view_id] = view
        if claim_id not in view.audit_claim_ids:
            view.audit_claim_ids.append(claim_id)

        memory_id = self._next_memory_id(
            task_id,
            source_agent,
            f"{claim.semantic_key}:{claim.value}",
        )
        memory = MemoryObject(
            memory_id=memory_id,
            version_id=1,
            task_id=task_id,
            source_agent=source_agent,
            task_topic=task_topic,
            summary=claim.summary,
            tags=list(tags),
            created_at=now,
            slot_id=claim.slot_id,
            claim_id=claim_id,
            memory_view_id=view_id,
            promotion_view_id=promotion_view_id,
            status="provisional_active",
            status_updated_at=now,
        )
        self._memories[memory_id] = memory
        self._create_memory_references(
            memory=memory,
            claim_id=claim_id,
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            promotion_view_id=promotion_view_id,
        )
        return memory, resolved.alias_hit, False, False, False

    def _bind_predecessor_relations(
        self,
        *,
        relations: list[dict[str, Any]],
        semantic_key: str,
        value: str,
        value_type: str,
        unit: str,
        polarity: str,
    ) -> list[dict[str, Any]]:
        active_predecessors = [
            claim
            for claim in self._claims.values()
            if claim.semantic_key == semantic_key
            and claim.status in {"active", "provisional_active"}
            and (
                normalized_value(claim.value, claim.value_type, claim.unit)
                != normalized_value(value, value_type, unit)
                or claim.polarity != polarity
            )
        ]
        if not active_predecessors:
            return [dict(relation) for relation in relations]

        bound: list[dict[str, Any]] = []
        for relation in relations:
            current = dict(relation)
            if (
                current.get("relation_type") not in SUPERSESSION_RELATION_TYPES
                or current.get("target_candidate_id")
            ):
                bound.append(current)
                continue
            target_value = str(current.get("target_value") or "").strip()
            matching = [
                claim
                for claim in active_predecessors
                if target_value
                and normalized_value(
                    claim.value,
                    claim.value_type,
                    claim.unit,
                )
                == normalized_value(target_value, claim.value_type, claim.unit)
            ]
            targets = matching or (
                active_predecessors if len(active_predecessors) == 1 else []
            )
            if len(targets) == 1:
                current["target_candidate_id"] = (
                    targets[0].candidate_id or targets[0].claim_id
                )
            bound.append(current)
        return bound

    def search_memory(
        self,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 3,
        required_tags: list[str] | None = None,
        allow_scope_only: bool = False,
    ) -> list[MemoryRef]:
        return self.search_memory_with_report(
            query,
            tags=tags,
            top_k=top_k,
            required_tags=required_tags,
            allow_scope_only=allow_scope_only,
        ).refs

    def search_memory_with_report(
        self,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 3,
        required_tags: list[str] | None = None,
        allow_scope_only: bool = False,
    ) -> MemorySearchReport:
        self.apply_lifecycle_transitions()
        query_terms = set(self._terms(query))
        requested_tags = set(tags or [])
        required_tag_set = set(required_tags or [])
        scored: list[tuple[int, MemoryObject]] = []
        semantic_filter_rejected_count = 0
        for memory in self._memories.values():
            if memory.status not in PROMPT_VIEW_MEMORY_STATUSES | DORMANT_MEMORY_STATUSES:
                continue
            memory_tags = set(memory.tags)
            if required_tag_set and not required_tag_set.issubset(memory_tags):
                continue
            claim = self._claims[memory.claim_id]
            view = self._views[memory.memory_view_id]
            searchable = " ".join(
                [memory.summary, claim.summary, view.prompt_summary, " ".join(memory.tags)]
            )
            memory_terms = set(self._terms(searchable))
            overlap = len(query_terms & memory_terms)
            tag_overlap = len(requested_tags & memory_tags)
            group_overlap = self._group_overlap(requested_tags, memory_tags)
            # Required scope is a safety boundary, not a relevance signal.
            # A same-scope memory must still share content with the query;
            # otherwise every item in a long-lived group is injected and later
            # becomes an unassessed context cost.
            if required_tag_set and overlap <= 0 and not allow_scope_only:
                semantic_filter_rejected_count += 1
                continue
            score = overlap + tag_overlap * 3 + group_overlap * 5
            if memory.status in DORMANT_MEMORY_STATUSES:
                score = max(0, score - 2)
            if score > 0:
                scored.append((score, memory))

        scored.sort(key=lambda item: (-item[0], item[1].created_at))
        refs: list[MemoryRef] = []
        selected_view_ids: set[str] = set()
        now = datetime.now(timezone.utc).isoformat()
        for _, memory in scored:
            if memory.memory_view_id in selected_view_ids:
                continue
            selected_view_ids.add(memory.memory_view_id)
            memory.hit_count += 1
            memory.last_accessed_at = now
            refs.append(self.ref(memory))
            if len(refs) >= top_k:
                break
        if refs:
            self._persist_snapshot()
        return MemorySearchReport(
            refs=refs,
            semantic_filter_rejected_count=semantic_filter_rejected_count,
            scope_only_retrieval=bool(allow_scope_only and required_tag_set),
        )

    def validate_read_set(
        self, refs: list[MemoryRef], *, requester: str = ""
    ) -> PreflightValidationReport:
        self._expire_write_intents()
        valid_refs: list[MemoryRef] = []
        blocked: list[str] = []
        stale_count = 0
        missing_count = 0
        for ref in refs:
            memory = self._memories.get(ref.memory_id)
            if memory is None:
                missing_count += 1
                blocked.append(ref.memory_id)
                continue
            if memory.version_id != ref.version_id:
                stale_count += 1
                blocked.append(ref.memory_id)
                continue
            if memory.status in BLOCKED_MEMORY_STATUSES:
                blocked.append(ref.memory_id)
                continue
            if memory.status not in PROMPT_VIEW_MEMORY_STATUSES | DORMANT_MEMORY_STATUSES:
                blocked.append(ref.memory_id)
                continue
            if self._blocked_by_write_intent(ref.memory_id, requester=requester):
                blocked.append(ref.memory_id)
                continue
            valid_refs.append(ref)
        return PreflightValidationReport(
            allowed=not blocked,
            checked_count=len(refs),
            blocked_count=len(blocked),
            stale_read_detected_count=stale_count + missing_count,
            missing_memory_count=missing_count,
            blocked_memory_ids=blocked,
            valid_refs=valid_refs,
        )

    def transition_memory_status(
        self, memory_ref: MemoryRef, new_status: str
    ) -> MemoryStatusTransitionReport:
        if new_status not in MEMORY_LIFECYCLE_STATUSES:
            raise ValueError(f"Unknown memory lifecycle status: {new_status}")
        memory = self._memories[memory_ref.memory_id]
        old_status = memory.status
        if old_status == new_status:
            return MemoryStatusTransitionReport(
                memory_id=memory.memory_id,
                old_status=old_status,
                new_status=new_status,
                transitioned=False,
            )
        memory.status = new_status
        memory.version_id += 1
        memory.status_updated_at = datetime.now(timezone.utc).isoformat()
        claim = self._claims.get(memory.claim_id)
        if claim is not None:
            claim.status = new_status
        view = self._views.get(memory.memory_view_id)
        if view is not None and claim is not None:
            if new_status in PROMPT_VIEW_MEMORY_STATUSES:
                if claim.claim_id not in view.active_claim_ids:
                    view.active_claim_ids.append(claim.claim_id)
                if claim.claim_id in view.historical_claim_ids:
                    view.historical_claim_ids.remove(claim.claim_id)
            else:
                if claim.claim_id in view.active_claim_ids:
                    view.active_claim_ids.remove(claim.claim_id)
                if (
                    claim.claim_id not in view.historical_claim_ids
                    and claim.claim_id not in view.conflicting_claim_ids
                ):
                    view.historical_claim_ids.append(claim.claim_id)
            self._refresh_memory_view(view)
        if new_status in BLOCKED_MEMORY_STATUSES:
            self.reference_manager.tombstone_memory(
                memory.memory_id, reason=f"memory_status:{new_status}"
            )
        if new_status in {"deprecated", "outdated", "superseded"}:
            self._record_compensating_event(
                event_type=f"memory_{new_status}",
                target_memory=memory,
                reason=f"status_transition:{old_status}->{new_status}",
                replacement_memory_id=None,
                created_by="MemoryStoreLite",
            )
        report = MemoryStatusTransitionReport(
            memory_id=memory.memory_id,
            old_status=old_status,
            new_status=new_status,
            transitioned=True,
        )
        self._persist_snapshot()
        return report

    def apply_lifecycle_transitions(
        self,
        *,
        dormant_after_seconds: float = 30 * 24 * 3600,
        outdated_after_seconds: float = 180 * 24 * 3600,
    ) -> MemoryLifecycleSweepReport:
        now = datetime.now(timezone.utc)
        report = MemoryLifecycleSweepReport()
        for memory in self._memories.values():
            report.checked_count += 1
            if memory.status not in PROMPT_VIEW_MEMORY_STATUSES | DORMANT_MEMORY_STATUSES:
                report.skipped_count += 1
                continue
            created_at = self._parse_dt(memory.created_at)
            age_seconds = (now - created_at).total_seconds()
            if (
                outdated_after_seconds >= 0
                and age_seconds >= outdated_after_seconds
                and memory.status != "outdated"
            ):
                self.transition_memory_status(self.ref(memory), "outdated")
                report.outdated_transition_count += 1
                continue
            if (
                dormant_after_seconds >= 0
                and age_seconds >= dormant_after_seconds
                and memory.hit_count == 0
                and memory.status in PROMPT_VIEW_MEMORY_STATUSES
            ):
                self.transition_memory_status(self.ref(memory), "dormant")
                report.dormant_transition_count += 1
        if report.dormant_transition_count or report.outdated_transition_count:
            self._persist_snapshot()
        return report

    def soft_deprecate(
        self,
        memory_ref: MemoryRef,
        *,
        reason: str,
        replacement_ref: MemoryRef | None = None,
        created_by: str = "ReviewCapability",
    ) -> CompensatingEvent:
        memory = self._memories[memory_ref.memory_id]
        self.transition_memory_status(memory_ref, "deprecated")
        event = self._record_compensating_event(
            event_type="soft_deprecation",
            target_memory=memory,
            reason=reason,
            replacement_memory_id=replacement_ref.memory_id if replacement_ref else None,
            created_by=created_by,
        )
        self._persist_snapshot()
        return event

    def compensating_events(
        self, memory_id: str | None = None
    ) -> list[CompensatingEvent]:
        if memory_id is None:
            return list(self._compensating_events)
        return [
            event
            for event in self._compensating_events
            if event.target_memory_id == memory_id
        ]

    def record_downstream_compensation(
        self,
        memory_ref: MemoryRef,
        *,
        reason: str,
        created_by: str = "MemoryAdoptionGuard",
    ) -> CompensatingEvent:
        """Audit a corrected downstream use without deprecating active memory."""

        memory = self._memories[memory_ref.memory_id]
        event = self._record_compensating_event(
            event_type="downstream_fact_correction",
            target_memory=memory,
            reason=reason,
            replacement_memory_id=None,
            created_by=created_by,
        )
        self._persist_snapshot()
        return event

    def memory_references(
        self, memory_ref: MemoryRef, ref_type: str | None = None
    ) -> list[MemoryReferenceRecord]:
        return self.reference_manager.active_refs(memory_ref.memory_id, ref_type)  # type: ignore[arg-type]

    def source_state_ids(self, memory_ref: MemoryRef) -> list[str]:
        memory = self._memories.get(memory_ref.memory_id)
        if memory is None or not memory.promotion_view_id:
            return []
        promotion = self._promotion_views.get(memory.promotion_view_id)
        if promotion is None:
            return []
        ordered = [*promotion.source_state_ids, *promotion.evidence_refs]
        return list(dict.fromkeys(state_id for state_id in ordered if state_id))

    def replace_memory(
        self, old_ref: MemoryRef, new_ref: MemoryRef, *, reason: str = ""
    ) -> int:
        active_before = len(self.reference_manager.active_refs(old_ref.memory_id))
        transition = self.transition_memory_status(old_ref, "superseded")
        if not transition.transitioned:
            return 0
        replaced_count = self.reference_manager.replace_memory(
            old_ref.memory_id,
            new_ref.memory_id,
            reason=reason or "memory_replaced_by_newer_claim",
        )
        self._record_compensating_event(
            event_type="memory_replacement",
            target_memory=self._memories[old_ref.memory_id],
            reason=reason or "memory_replaced_by_newer_claim",
            replacement_memory_id=new_ref.memory_id,
            created_by="MemoryStoreLite",
        )
        self._persist_snapshot()
        return max(active_before, replaced_count)

    def render_prompt_view(self, memory_ref: MemoryRef, budget_chars: int = 700) -> str:
        memory = self._memories[memory_ref.memory_id]
        view = self._views[memory.memory_view_id]
        claim = self._claims[memory.claim_id]
        tag_text = ", ".join(memory.tags[:5])
        prefix = (
            f"[memory_view:{view.memory_view_id}] slot={view.slot_id}; "
            f"claim={claim.claim_id}; "
        )
        suffix = f"; tags=[{tag_text}]"
        guard = self._render_revision_guard(view)
        reserved = len(prefix) + len(suffix) + len(guard)
        summary_budget = max(0, budget_chars - reserved)
        if view.active_value is not None and view.schema_version.startswith("ccf.v2"):
            summary = self._render_model_fact(view)
        else:
            summary = view.prompt_summary[:summary_budget]
        rendered = f"{prefix}{summary}{suffix}{guard}"
        if view.schema_version.startswith("ccf.v2"):
            # A typed fact and its revision policy are atomic safety records.
            # Returning a complete record is safer than slicing JSON into
            # independently selectable field fragments.
            return rendered
        return rendered[:budget_chars]

    def revision_guard(self, memory_ref: MemoryRef) -> dict[str, Any]:
        """Return prompt-safe policy plus audit-only claim data for validation."""
        memory = self._memories[memory_ref.memory_id]
        view = self._views[memory.memory_view_id]
        active_claims = [
            self._claims[claim_id]
            for claim_id in view.active_claim_ids
            if claim_id in self._claims
            and self._claims[claim_id].status in PROMPT_VIEW_MEMORY_STATUSES
        ]
        historical_claims = [
            self._claims[claim_id]
            for claim_id in view.historical_claim_ids
            if claim_id in self._claims
        ]
        return {
            "required": bool(historical_claims or view.conflicting_claim_ids),
            "schema_version": view.schema_version,
            "memory_view_id": view.memory_view_id,
            "slot_id": view.slot_id,
            "subject": view.subject,
            "scope": view.scope,
            "semantic_key": view.semantic_key,
            "resolution_status": view.resolution_status,
            "downstream_policy": view.downstream_policy,
            "active_claim_ids": [claim.claim_id for claim in active_claims],
            "active_source_task_ids": [
                claim.source_task_id for claim in active_claims
            ],
            "active_facts": [
                {
                    "semantic_key": claim.semantic_key,
                    "slot_id": claim.slot_id,
                    "scope": claim.scope,
                    "value": claim.value,
                    "value_type": claim.value_type,
                    "unit": claim.unit,
                    "polarity": claim.polarity,
                    "operator": claim.operator,
                    "claim_id": claim.claim_id,
                }
                for claim in active_claims
            ],
            "historical_claims": [
                {
                    "claim_id": claim.claim_id,
                    "source_task_id": claim.source_task_id,
                    "summary": claim.summary,
                    "value": claim.value,
                    "status": claim.status,
                    "semantic_key": claim.semantic_key,
                    "slot_id": claim.slot_id,
                    "scope": claim.scope,
                    "value_type": claim.value_type,
                    "unit": claim.unit,
                    "polarity": claim.polarity,
                    "operator": claim.operator,
                    "schema_version": claim.schema_version,
                    "claim_type": claim.claim_type,
                    "exclude_from_negative_attribution": (
                        claim.schema_version == "ccf.v1-lite"
                        and claim.slot_id
                        == "slot.system.deliverable_requirement"
                    ),
                }
                for claim in historical_claims
            ],
            "historical_facts": [
                {
                    "semantic_key": claim.semantic_key,
                    "slot_id": claim.slot_id,
                    "scope": claim.scope,
                    "value": claim.value,
                    "value_type": claim.value_type,
                    "unit": claim.unit,
                    "polarity": claim.polarity,
                    "operator": claim.operator,
                    "claim_id": claim.claim_id,
                }
                for claim in historical_claims
            ],
            "conflicting_claim_ids": list(view.conflicting_claim_ids),
            "conflicting_facts": list(view.conflicting_values),
        }

    def resolve_ref(self, memory_id: str) -> MemoryRef | None:
        """Return the current reference for a memory without changing hit counters."""
        memory = self._memories.get(memory_id)
        return self.ref(memory) if memory is not None else None

    def record_useful_hits(self, memory_ids: list[str]) -> int:
        """Persist evidence-backed downstream adoption for unique memories."""
        return self._record_feedback_hits(memory_ids, "useful_hit_count")

    def record_wrong_hits(self, memory_ids: list[str]) -> int:
        """Persist evidence that an output adopted an outdated or conflicting fact."""
        return self._record_feedback_hits(memory_ids, "wrong_hit_count")

    def record_mixed_hits(self, memory_ids: list[str]) -> int:
        """Persist outputs containing both active and outdated memory evidence."""
        return self._record_feedback_hits(memory_ids, "mixed_hit_count")

    def render_audit_view(self, memory_ref: MemoryRef, budget_chars: int = 1800) -> str:
        memory = self._memories[memory_ref.memory_id]
        view = self._views[memory.memory_view_id]
        claims = [asdict(self._claims[item]) for item in view.audit_claim_ids]
        payload: dict[str, Any] = {
            "memory_id": memory.memory_id,
            "memory_view": asdict(view),
            "claims": claims,
        }
        if memory.promotion_view_id:
            payload["promotion_view"] = asdict(
                self._promotion_views[memory.promotion_view_id]
            )
        text = str(payload)
        return text[:budget_chars]

    def get_prompt_view(
        self,
        semantic_keys: str | list[str],
        *,
        budget_chars: int = 700,
    ) -> list[str]:
        """Return active fact views without expanding historical evidence."""
        requested = (
            [semantic_keys]
            if isinstance(semantic_keys, str)
            else list(semantic_keys)
        )
        rendered: list[str] = []
        for semantic_key in dict.fromkeys(requested):
            view = next(
                (
                    item
                    for item in self._views.values()
                    if item.semantic_key == semantic_key
                    and item.active_value is not None
                    and item.resolution_status == "resolved"
                    and item.downstream_policy
                    != "do_not_use_for_final_generation"
                ),
                None,
            )
            if view is None:
                continue
            memory = next(
                (
                    item
                    for item in self._memories.values()
                    if item.memory_view_id == view.memory_view_id
                    and item.claim_id in view.active_claim_ids
                    and item.status in PROMPT_VIEW_MEMORY_STATUSES
                ),
                None,
            )
            if memory is not None:
                rendered.append(
                    self.render_prompt_view(
                        self.ref(memory),
                        budget_chars=budget_chars,
                    )
                )
        return rendered

    def get_audit_view(
        self,
        memory_view_id: str,
        *,
        budget_chars: int = 4000,
    ) -> str:
        """Expand one MemoryView for reviewers and audit tooling."""
        view = self._views.get(memory_view_id)
        if view is None:
            return ""
        memory_ids = [
            memory.memory_id
            for memory in self._memories.values()
            if memory.memory_view_id == memory_view_id
        ]
        payload = {
            "view_type": "audit_view",
            "memory_view": asdict(view),
            "claims": [
                asdict(self._claims[claim_id])
                for claim_id in view.audit_claim_ids
                if claim_id in self._claims
            ],
            "memory_ids": memory_ids,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)[:budget_chars]

    def render_historical_prompt_view(
        self,
        memory_ref: MemoryRef,
        *,
        budget_chars: int = 1200,
        max_facts: int = 4,
    ) -> str:
        """Return complete typed historical facts for an explicit field fetch."""

        memory = self._memories[memory_ref.memory_id]
        view = self._views[memory.memory_view_id]
        if (
            not view.schema_version.startswith("ccf.v2")
            or view.resolution_status != "resolved"
        ):
            return ""
        claims = [
            self._claims[claim_id]
            for claim_id in view.historical_claim_ids[-max(1, max_facts) :]
            if claim_id in self._claims
        ]
        lines: list[str] = []
        for claim in claims:
            payload = {
                "slot_id": claim.slot_id,
                "scope": claim.scope,
                "value": claim.value,
                "value_type": claim.value_type,
                "unit": claim.unit,
                "operator": claim.operator,
                "polarity": claim.polarity,
                "status": claim.status,
            }
            line = "historical_fact=" + json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            projected = len("\n".join([*lines, line]))
            if lines and projected > budget_chars:
                break
            lines.append(line)
        return "\n".join(lines)

    def expand_evidence(
        self,
        memory_view_id: str,
        claim_id: str,
        *,
        budget_chars: int = 4000,
    ) -> str:
        """Expand evidence for one claim instead of broadcasting full history."""
        view = self._views.get(memory_view_id)
        claim = self._claims.get(claim_id)
        if view is None or claim is None or claim_id not in view.audit_claim_ids:
            return ""
        memories = [
            memory
            for memory in self._memories.values()
            if memory.memory_view_id == memory_view_id
            and memory.claim_id == claim_id
        ]
        payload: dict[str, Any] = {
            "view_type": "evidence_expansion",
            "memory_view_id": memory_view_id,
            "claim": asdict(claim),
            "memories": [asdict(memory) for memory in memories],
            "references": [
                asdict(reference)
                for memory in memories
                for reference in self.reference_manager.active_refs(memory.memory_id)
            ],
        }
        promotion_ids = {
            memory.promotion_view_id
            for memory in memories
            if memory.promotion_view_id
        }
        payload["promotion_views"] = [
            asdict(self._promotion_views[promotion_id])
            for promotion_id in promotion_ids
            if promotion_id in self._promotion_views
        ]
        return json.dumps(payload, ensure_ascii=False, indent=2)[:budget_chars]

    def render_storage_view(self, memory_ref: MemoryRef, budget_chars: int = 6000) -> str:
        memory = self._memories[memory_ref.memory_id]
        claim = self._claims[memory.claim_id]
        view = self._views[memory.memory_view_id]
        payload: dict[str, Any] = {
            "view_type": "storage_view",
            "memory": asdict(memory),
            "claim": asdict(claim),
            "memory_view": asdict(view),
            "references": [
                asdict(ref) for ref in self.reference_manager.active_refs(memory.memory_id)
            ],
            "compensating_events": [
                asdict(event) for event in self.compensating_events(memory.memory_id)
            ],
            "layer": {
                "hot": "in_memory",
                "warm": str(self.warm_db_path) if self.warm_db_path else "",
                "cold": str(self.cold_memory_dir) if self.cold_memory_dir else "",
            },
        }
        if memory.promotion_view_id:
            payload["promotion_view"] = asdict(
                self._promotion_views[memory.promotion_view_id]
            )
        return json.dumps(payload, ensure_ascii=False, indent=2)[:budget_chars]

    def render_deliverable_view(
        self, refs: list[MemoryRef], *, task_title: str, budget_chars: int = 1800
    ) -> str:
        if not refs:
            return "无可复用 Deliverable View"
        lines = [f"Deliverable View for {task_title}:"]
        seen_views: set[str] = set()
        for ref in refs:
            memory = self._memories[ref.memory_id]
            view = self._views[memory.memory_view_id]
            if view.memory_view_id in seen_views:
                continue
            seen_views.add(view.memory_view_id)
            lines.append(
                f"- slot={view.slot_id}; view={view.memory_view_id}; {view.prompt_summary}"
            )
        return "\n".join(lines)[:budget_chars]

    def run_compaction(
        self,
        *,
        slot_id: str | None = None,
        view_ids: set[str] | None = None,
    ) -> MemoryCompactionReport:
        self.apply_lifecycle_transitions()
        compaction_id = f"compact_{len(self._compaction_log) + 1:04d}"
        views = [
            view
            for view in self._views.values()
            if (slot_id is None or view.slot_id == slot_id)
            and (view_ids is None or view.memory_view_id in view_ids)
        ]
        report = MemoryCompactionReport(compaction_id=compaction_id)
        for view in views:
            compactable_claims = [
                self._claims[claim_id]
                for claim_id in view.audit_claim_ids
                if claim_id in self._claims
                and self._claims[claim_id].status
                in {"active", "provisional_active"}
            ]
            provisional_count = sum(
                claim.status == "provisional_active" for claim in compactable_claims
            )
            report.new_claim_count += (
                provisional_count
                if provisional_count
                else len(view.audit_claim_ids)
                if not view.schema_version.startswith("ccf.v2")
                else 0
            )
            if not compactable_claims:
                self._refresh_memory_view(view)
                report.memory_view_count += 1
                continue

            value_groups: dict[tuple[str, str], list[ClaimCard]] = {}
            for claim in compactable_claims:
                key = (
                    normalized_value(claim.value, claim.value_type, claim.unit),
                    claim.polarity,
                )
                value_groups.setdefault(key, []).append(claim)

            if len(value_groups) == 1:
                selected_claims = list(next(iter(value_groups.values())))
                view.active_claim_ids = [
                    claim.claim_id for claim in selected_claims
                ]
                view.conflicting_claim_ids = []
                for claim in selected_claims:
                    self._set_claim_status(claim, "active")
            else:
                report.conflict_detected_count += 1
                selected_claims = self._select_compaction_winner(
                    compactable_claims
                )
                if selected_claims is None:
                    view.active_claim_ids = []
                    view.conflicting_claim_ids = [
                        claim.claim_id for claim in compactable_claims
                    ]
                    for claim in compactable_claims:
                        self._set_claim_status(claim, "unresolved_conflict")
                    report.unresolved_conflict_count += 1
                else:
                    report.resolved_conflict_count += 1
                    selected_ids = {
                        claim.claim_id for claim in selected_claims
                    }
                    view.active_claim_ids = list(selected_ids)
                    view.conflicting_claim_ids = []
                    for claim in compactable_claims:
                        if claim.claim_id in selected_ids:
                            self._set_claim_status(claim, "active")
                            continue
                        self._set_claim_status(claim, "superseded")
                        if claim.claim_id not in view.historical_claim_ids:
                            view.historical_claim_ids.append(claim.claim_id)
                            report.superseded_claim_count += 1

            active_claim_ids = [
                claim_id
                for claim_id in view.active_claim_ids
                if self._claims[claim_id].status == "active"
            ]
            view.active_claim_ids = active_claim_ids
            view.status = (
                "unresolved_conflict"
                if view.conflicting_claim_ids
                else "active"
            )
            view.version_id += 1
            self._refresh_memory_view(view)
            report.active_value_selection_count += int(
                view.active_value is not None
            )
            report.memory_view_count += 1
            if not view.schema_version.startswith("ccf.v2"):
                report.merged_claim_count += max(
                    0,
                    len(view.audit_claim_ids) - len(active_claim_ids),
                )
                report.superseded_claim_count += len(view.historical_claim_ids)
            else:
                report.merged_claim_count += max(
                    0,
                    len(compactable_claims) - len(value_groups),
                )
        self._compaction_log.append(
            {
                "compaction_id": compaction_id,
                "slot_id": slot_id,
                "view_ids": sorted(view_ids or []),
                "memory_view_count": report.memory_view_count,
                "new_claim_count": report.new_claim_count,
                "merged_claim_count": report.merged_claim_count,
                "superseded_claim_count": report.superseded_claim_count,
                "conflict_detected_count": report.conflict_detected_count,
                "resolved_conflict_count": report.resolved_conflict_count,
                "unresolved_conflict_count": report.unresolved_conflict_count,
                "active_value_selection_count": (
                    report.active_value_selection_count
                ),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._persist_snapshot()
        return report

    def _select_compaction_winner(
        self,
        claims: list[ClaimCard],
    ) -> list[ClaimCard] | None:
        if not claims:
            return []
        if any(
            claim.schema_version.startswith("ccf.v3")
            for claim in claims
        ):
            resolution = resolve_claim_conflicts(claims)
            if resolution.status != "resolved":
                return None
            active_ids = set(resolution.active_claim_ids)
            return [
                claim for claim in claims if claim.claim_id in active_ids
            ]
        policy = claims[-1].conflict_policy
        if policy in {
            "latest_explicit_state_wins",
            "latest_valid_high_confidence_wins",
            "highest_confidence_then_latest",
        }:
            if policy == "latest_explicit_state_wins":
                winner = max(
                    claims,
                    key=lambda claim: (
                        claim.revision_kind == "replaces",
                        claim.created_at,
                        claim.confidence,
                    ),
                )
            elif policy == "latest_valid_high_confidence_wins":
                winner = max(
                    claims,
                    key=lambda claim: (
                        claim.created_at,
                        claim.confidence,
                    ),
                )
            else:
                winner = max(
                    claims,
                    key=lambda claim: (
                        claim.confidence,
                        claim.created_at,
                    ),
                )
            winner_value = normalized_value(
                winner.value,
                winner.value_type,
                winner.unit,
            )
            return [
                claim
                for claim in claims
                if normalized_value(claim.value, claim.value_type, claim.unit)
                == winner_value
                and claim.polarity == winner.polarity
            ]

        ranked = sorted(
            claims,
            key=lambda claim: (claim.confidence, claim.created_at),
            reverse=True,
        )
        if len(ranked) == 1 or ranked[0].confidence > ranked[1].confidence:
            winner = ranked[0]
            winner_value = normalized_value(
                winner.value,
                winner.value_type,
                winner.unit,
            )
            return [
                claim
                for claim in claims
                if normalized_value(claim.value, claim.value_type, claim.unit)
                == winner_value
                and claim.polarity == winner.polarity
            ]
        return None

    def _set_claim_status(self, claim: ClaimCard, status: str) -> None:
        if claim.status == status:
            return
        claim.status = status
        self._sync_memory_status_for_claim(claim.claim_id, status)

    def open_write_intent(
        self,
        *,
        task_id: str,
        section_id: str,
        locked_by: str,
        read_set: list[MemoryRef],
        estimated_cost_tokens: int,
        ttl_ms: int = 3000,
    ) -> WriteIntentFence:
        validation = self.validate_read_set(read_set, requester=locked_by)
        now = datetime.now(timezone.utc)
        fence_id = self._next_id(
            "fence",
            task_id,
            locked_by,
            f"{section_id}:{len(self._write_intents)}",
        )
        fence = WriteIntentFence(
            fence_id=fence_id,
            task_id=task_id,
            section_id=section_id,
            locked_by=locked_by,
            memory_ids=[ref.memory_id for ref in read_set],
            expected_versions={ref.memory_id: ref.version_id for ref in read_set},
            estimated_cost_tokens=max(0, estimated_cost_tokens),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(milliseconds=max(1, ttl_ms))).isoformat(),
            allowed=validation.allowed,
            blocked_memory_ids=list(validation.blocked_memory_ids),
        )
        self._write_intents[fence_id] = fence
        self._section_dependency_map[section_id] = list(fence.memory_ids)
        self._persist_snapshot()
        return fence

    def validate_write_intent(self, fence_id: str) -> PreflightValidationReport:
        self._expire_write_intents()
        fence = self._write_intents.get(fence_id)
        if fence is None:
            return PreflightValidationReport(
                allowed=False,
                checked_count=0,
                blocked_count=1,
                stale_read_detected_count=1,
                blocked_memory_ids=[fence_id],
            )
        refs = []
        for memory_id, version in fence.expected_versions.items():
            memory = self._memories.get(memory_id)
            if memory is None:
                refs.append(
                    MemoryRef(
                        memory_id=memory_id,
                        version_id=version,
                        status="missing",
                        task_topic="",
                        memory_view_id="",
                        slot_id="",
                    )
                )
            else:
                refs.append(
                    MemoryRef(
                        memory_id=memory.memory_id,
                        version_id=version,
                        status=memory.status,
                        task_topic=memory.task_topic,
                        memory_view_id=memory.memory_view_id,
                        slot_id=memory.slot_id,
                    )
                )
        return self.validate_read_set(refs, requester=fence.locked_by)

    def record_patch_regeneration(
        self,
        *,
        section_id: str,
        read_set: list[MemoryRef],
        patch_summary: str,
        estimated_saved_tokens: int = 0,
    ) -> PatchRegenerationReport:
        validation = self.validate_read_set(read_set)
        report = PatchRegenerationReport(
            section_id=section_id,
            regenerated=not validation.allowed,
            stale_memory_ids=list(validation.blocked_memory_ids),
            patch_summary=patch_summary,
            estimated_saved_tokens=max(0, estimated_saved_tokens),
        )
        self._patch_regeneration_log.append(report)
        self._persist_snapshot()
        return report

    def layered_storage_report(self) -> MemoryLayerReport:
        warm_count = 0
        if self.warm_db_path and self.warm_db_path.exists():
            conn = sqlite3.connect(self.warm_db_path)
            try:
                warm_count = int(
                    conn.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0]
                )
            finally:
                conn.close()
        cold_count = (
            len(list(self.cold_memory_dir.glob("*.json")))
            if self.cold_memory_dir and self.cold_memory_dir.exists()
            else 0
        )
        return MemoryLayerReport(
            warm_db_path=str(self.warm_db_path or ""),
            cold_dir=str(self.cold_memory_dir or ""),
            warm_record_count=warm_count,
            cold_record_count=cold_count,
        )

    def ref_to_dict(self, memory_ref: MemoryRef) -> dict:
        return asdict(memory_ref)

    def snapshot(self) -> dict[str, Any]:
        return {
            "memories": [
                asdict(item)
                for item in sorted(
                    self._memories.values(), key=lambda memory: memory.created_at
                )
            ],
            "claim_cards": [
                asdict(item)
                for item in sorted(
                    self._claims.values(), key=lambda claim: claim.created_at
                )
            ],
            "memory_views": [
                asdict(item)
                for item in sorted(
                    self._views.values(), key=lambda view: view.created_at
                )
            ],
            "promotion_views": [
                asdict(item)
                for item in sorted(
                    self._promotion_views.values(),
                    key=lambda view: view.promotion_view_id,
                )
            ],
            "memory_candidates": [
                asdict(item)
                for item in sorted(
                    self._memory_candidates.values(),
                    key=lambda candidate: candidate.created_at,
                )
            ],
            "claim_candidates": [
                asdict(item)
                for item in sorted(
                    self._claim_candidates.values(),
                    key=lambda candidate: candidate.candidate_id,
                )
            ],
        }

    def ref(self, memory: MemoryObject) -> MemoryRef:
        return MemoryRef(
            memory_id=memory.memory_id,
            version_id=memory.version_id,
            status=memory.status,
            task_topic=memory.task_topic,
            memory_view_id=memory.memory_view_id,
            slot_id=memory.slot_id,
        )

    def _upsert_memory_view(self, claim: ClaimCard) -> tuple[MemoryView, int, int]:
        semantic_key = claim.semantic_key or self._semantic_key(claim)
        view_id = f"view_{hashlib.sha256(semantic_key.encode('utf-8')).hexdigest()[:10]}"
        if view_id in self._views:
            view = self._views[view_id]
            superseded_count = 0
            conflict_count = 0
            view.audit_claim_ids.append(claim.claim_id)
            if claim.status == "active":
                superseded_count, conflict_count = self._resolve_claim_conflict(
                    view, claim
                )
            self._refresh_memory_view(view)
            return view, superseded_count, conflict_count

        view = MemoryView(
            memory_view_id=view_id,
            slot_id=claim.slot_id,
            active_claim_ids=[claim.claim_id] if claim.status == "active" else [],
            prompt_summary=claim.summary,
            audit_claim_ids=[claim.claim_id],
            created_at=datetime.now(timezone.utc).isoformat(),
            subject=claim.subject,
            scope=claim.scope,
            temporal_scope=claim.temporal_scope,
            semantic_key=semantic_key,
            schema_version=claim.schema_version,
        )
        self._views[view_id] = view
        self._refresh_memory_view(view)
        return view, 0, 0

    def _resolve_claim_conflict(
        self, view: MemoryView, new_claim: ClaimCard
    ) -> tuple[int, int]:
        same_subject_claim_ids = [
            claim_id
            for claim_id in view.active_claim_ids
            if self._claims[claim_id].subject == new_claim.subject
        ]
        if not same_subject_claim_ids:
            if new_claim.claim_id not in view.active_claim_ids:
                view.active_claim_ids.append(new_claim.claim_id)
            return 0, 0

        candidates = [self._claims[item] for item in same_subject_claim_ids] + [new_claim]
        equivalent_ids = [
            claim.claim_id
            for claim in candidates
            if normalized_value(claim.value, claim.value_type, claim.unit)
            == normalized_value(new_claim.value, new_claim.value_type, new_claim.unit)
            and claim.polarity == new_claim.polarity
        ]
        if len(equivalent_ids) > 1:
            for claim_id in equivalent_ids:
                if claim_id not in view.active_claim_ids:
                    view.active_claim_ids.append(claim_id)
            return 0, 0

        best = max(candidates, key=lambda claim: (claim.confidence, claim.created_at))
        superseded_count = 0
        conflict_count = 0
        if best.claim_id == new_claim.claim_id:
            for old_id in same_subject_claim_ids:
                old_claim = self._claims[old_id]
                if old_claim.summary != new_claim.summary:
                    conflict_count += 1
                old_claim.status = "superseded"
                self._sync_memory_status_for_claim(old_id, "superseded")
                if old_id in view.active_claim_ids:
                    view.active_claim_ids.remove(old_id)
                if old_id not in view.historical_claim_ids:
                    view.historical_claim_ids.append(old_id)
                superseded_count += 1
            if new_claim.claim_id not in view.active_claim_ids:
                view.active_claim_ids.append(new_claim.claim_id)
            return superseded_count, conflict_count

        new_claim.status = "superseded"
        if new_claim.claim_id not in view.historical_claim_ids:
            view.historical_claim_ids.append(new_claim.claim_id)
        if best.summary != new_claim.summary:
            conflict_count = 1
        return 1, conflict_count

    def _refresh_memory_view(self, view: MemoryView) -> None:
        active_claims = [
            self._claims[claim_id]
            for claim_id in view.active_claim_ids
            if claim_id in self._claims
            and self._claims[claim_id].status in {"active", "provisional_active"}
        ]
        historical_claims = [
            self._claims[claim_id]
            for claim_id in view.historical_claim_ids
            if claim_id in self._claims
        ]
        conflicting_claims = [
            self._claims[claim_id]
            for claim_id in view.conflicting_claim_ids
            if claim_id in self._claims
        ]
        view.prompt_summary = self._compact_claims(
            [claim.claim_id for claim in active_claims]
        )
        if active_claims:
            best = max(active_claims, key=lambda claim: (claim.confidence, claim.created_at))
            view.active_value = self._claim_value_payload(
                best,
                supported_by=[
                    claim.claim_id
                    for claim in active_claims
                    if normalized_value(claim.value, claim.value_type, claim.unit)
                    == normalized_value(best.value, best.value_type, best.unit)
                ],
                selected_by=best.conflict_policy,
            )
            if view.schema_version.startswith("ccf.v2"):
                view.prompt_summary = self._render_model_fact(view)
        else:
            view.active_value = None
        view.historical_values = [
            self._claim_value_payload(claim, status=claim.status)
            for claim in historical_claims
        ]
        view.conflicting_values = [
            self._claim_value_payload(claim, status=claim.status)
            for claim in conflicting_claims
        ]
        if conflicting_claims:
            view.resolution_status = "unresolved_conflict"
            view.downstream_policy = "do_not_use_for_final_generation"
        else:
            view.resolution_status = "resolved"
            view.downstream_policy = (
                "use_active_claims_only"
                if historical_claims
                else "safe_for_prompt_view"
            )

    @staticmethod
    def _claim_value_payload(
        claim: ClaimCard,
        *,
        supported_by: list[str] | None = None,
        selected_by: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "value": claim.value,
            "value_type": claim.value_type,
            "unit": claim.unit,
            "confidence": claim.confidence,
            "polarity": claim.polarity,
            "operator": claim.operator,
            "valid_from": claim.valid_from,
            "valid_to": claim.valid_to,
            "supported_by": supported_by or [claim.claim_id],
        }
        if selected_by:
            payload["selected_by"] = selected_by
        if status:
            payload["status"] = status
        return payload

    @staticmethod
    def _semantic_key(claim: ClaimCard) -> str:
        return claim_identity(
            {
                "subject": claim.subject,
                "slot_id": claim.slot_id,
                "scope": claim.scope,
                "temporal_scope": claim.temporal_scope,
            }
        )

    def _sync_memory_status_for_claim(self, claim_id: str, status: str) -> None:
        for memory in self._memories.values():
            if memory.claim_id == claim_id:
                memory.status = status
                memory.version_id += 1
                memory.status_updated_at = datetime.now(timezone.utc).isoformat()
                if status in BLOCKED_MEMORY_STATUSES:
                    self.reference_manager.tombstone_memory(
                        memory.memory_id, reason=f"claim_status:{status}"
                    )
                if status in {"deprecated", "outdated", "superseded"}:
                    self._record_compensating_event(
                        event_type=f"claim_{status}",
                        target_memory=memory,
                        reason=f"claim_status:{status}",
                        replacement_memory_id=None,
                        created_by="MemoryStoreLite",
                    )
        self._persist_snapshot()

    def _create_memory_references(
        self,
        *,
        memory: MemoryObject,
        claim_id: str,
        source_state_ids: list[str],
        evidence_refs: list[str],
        promotion_view_id: str | None,
    ) -> int:
        count = 0
        self.reference_manager.create(
            memory_id=memory.memory_id,
            target_kind="claim",
            target_id=claim_id,
            ref_type="strong",
            reason="memory_claim_anchor",
        )
        count += 1
        self.reference_manager.create(
            memory_id=memory.memory_id,
            target_kind="memory_view",
            target_id=memory.memory_view_id,
            ref_type="strong",
            reason="memory_view_anchor",
        )
        count += 1
        if promotion_view_id:
            self.reference_manager.create(
                memory_id=memory.memory_id,
                target_kind="promotion_view",
                target_id=promotion_view_id,
                ref_type="weak",
                reason="promotion_view_trace",
            )
            count += 1
        for state_id in source_state_ids:
            self.reference_manager.create(
                memory_id=memory.memory_id,
                target_kind="state",
                target_id=state_id,
                ref_type="lineage",
                reason="source_state_lineage",
            )
            count += 1
        for evidence_id in evidence_refs:
            self.reference_manager.create(
                memory_id=memory.memory_id,
                target_kind="evidence",
                target_id=evidence_id,
                ref_type="evidence",
                reason="evidence_support",
            )
            count += 1
        return count

    def _persist_snapshot(self) -> None:
        if self.storage_dir is None:
            return
        payload = self._snapshot_payload()
        path = self.storage_dir / "memory_store_snapshot.json"
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
        self._persist_layered_records()

    def _snapshot_payload(self) -> dict[str, Any]:
        return {
            "memories": [asdict(item) for item in self._memories.values()],
            "claim_cards": [asdict(item) for item in self._claims.values()],
            "memory_views": [asdict(item) for item in self._views.values()],
            "promotion_views": [asdict(item) for item in self._promotion_views.values()],
            "memory_candidates": [
                asdict(item) for item in self._memory_candidates.values()
            ],
            "claim_candidates": [
                asdict(item) for item in self._claim_candidates.values()
            ],
            "memory_references": self.reference_manager.snapshot(),
            "compaction_log": list(self._compaction_log),
            "compensating_events": [
                asdict(event) for event in self._compensating_events
            ],
            "write_intents": [
                asdict(fence) for fence in self._write_intents.values()
            ],
            "section_dependency_map": dict(self._section_dependency_map),
            "patch_regeneration_log": [
                asdict(report) for report in self._patch_regeneration_log
            ],
        }

    def _init_layered_storage(self) -> None:
        if self.storage_dir is None or self.warm_db_path is None or self.cold_memory_dir is None:
            return
        self.cold_memory_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.warm_db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (kind, record_id)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _load_layered_storage(self) -> None:
        if self.storage_dir is None or self.warm_db_path is None:
            return
        conn = sqlite3.connect(self.warm_db_path, timeout=30.0)
        try:
            rows = conn.execute(
                "SELECT kind, record_id, payload_json FROM memory_records"
            ).fetchall()
        finally:
            conn.close()
        for kind, record_id, payload_json in rows:
            payload = json.loads(payload_json)
            if kind == "memory":
                self._memories[record_id] = MemoryObject(**payload)
            elif kind == "claim":
                self._claims[record_id] = ClaimCard(**payload)
            elif kind == "memory_view":
                self._views[record_id] = MemoryView(**payload)

        cold_references: list[dict[str, object]] = []
        if self.cold_memory_dir and self.cold_memory_dir.exists():
            for path in self.cold_memory_dir.glob("*.json"):
                payload = json.loads(path.read_text(encoding="utf-8"))
                promotion_payload = payload.get("promotion_view")
                if isinstance(promotion_payload, dict):
                    promotion = PromotionView(**promotion_payload)
                    self._promotion_views[promotion.promotion_view_id] = promotion
                references = payload.get("references", [])
                if isinstance(references, list):
                    cold_references.extend(
                        item for item in references if isinstance(item, dict)
                    )
        self.reference_manager.restore(cold_references)

        snapshot_path = self.storage_dir / "memory_store_snapshot.json"
        if not snapshot_path.exists():
            return
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        for payload in snapshot.get("memories", []):
            memory = MemoryObject(**payload)
            self._memories.setdefault(memory.memory_id, memory)
        for payload in snapshot.get("claim_cards", []):
            claim = ClaimCard(**payload)
            self._claims.setdefault(claim.claim_id, claim)
        for payload in snapshot.get("memory_views", []):
            view = MemoryView(**payload)
            self._views.setdefault(view.memory_view_id, view)
        for payload in snapshot.get("promotion_views", []):
            view = PromotionView(**payload)
            self._promotion_views[view.promotion_view_id] = view
        for payload in snapshot.get("memory_candidates", []):
            candidate = MemoryCandidate(**payload)
            self._memory_candidates[candidate.candidate_id] = candidate
        for payload in snapshot.get("claim_candidates", []):
            candidate = ClaimCandidate(**payload)
            self._claim_candidates[candidate.candidate_id] = candidate
        self.reference_manager.restore(snapshot.get("memory_references", []))
        self._compaction_log = list(snapshot.get("compaction_log", []))
        self._compensating_events = [
            CompensatingEvent(**payload)
            for payload in snapshot.get("compensating_events", [])
        ]
        self._write_intents = {
            fence.fence_id: fence
            for fence in (
                WriteIntentFence(**payload)
                for payload in snapshot.get("write_intents", [])
            )
        }
        self._section_dependency_map = dict(
            snapshot.get("section_dependency_map", {})
        )
        self._patch_regeneration_log = [
            PatchRegenerationReport(**payload)
            for payload in snapshot.get("patch_regeneration_log", [])
        ]

    def _migrate_loaded_legacy_claims(self) -> None:
        """Keep old whole-document deliverables for audit, never prompt injection."""
        legacy_claim_ids = {
            claim.claim_id
            for claim in self._claims.values()
            if claim.schema_version == "ccf.v1-lite"
            and claim.slot_id == "slot.system.deliverable_requirement"
        }
        if not legacy_claim_ids:
            return
        for claim_id in legacy_claim_ids:
            self._claims[claim_id].claim_type = "legacy_document_claim"
        affected_view_ids: set[str] = set()
        for memory in self._memories.values():
            if memory.claim_id not in legacy_claim_ids:
                continue
            memory.status = "legacy_audit"
            memory.status_updated_at = (
                memory.status_updated_at
                or datetime.now(timezone.utc).isoformat()
            )
            affected_view_ids.add(memory.memory_view_id)
        for view_id in affected_view_ids:
            view = self._views.get(view_id)
            if view is None:
                continue
            view.status = "legacy_audit"
            view.downstream_policy = "audit_only"

    def _persist_layered_records(self) -> None:
        if self.storage_dir is None or self.warm_db_path is None or self.cold_memory_dir is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        records: list[tuple[str, str, dict[str, Any]]] = []
        records.extend(("memory", item.memory_id, asdict(item)) for item in self._memories.values())
        records.extend(("claim", item.claim_id, asdict(item)) for item in self._claims.values())
        records.extend(("memory_view", item.memory_view_id, asdict(item)) for item in self._views.values())
        conn = sqlite3.connect(self.warm_db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.executemany(
                """
                INSERT OR REPLACE INTO memory_records(kind, record_id, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (kind, record_id, json.dumps(payload, ensure_ascii=False), now)
                    for kind, record_id, payload in records
                ],
            )
            conn.commit()
        finally:
            conn.close()
        for memory in self._memories.values():
            cold_payload = {
                "memory": asdict(memory),
                "claim": asdict(self._claims[memory.claim_id]),
                "memory_view": asdict(self._views[memory.memory_view_id]),
                "promotion_view": (
                    asdict(self._promotion_views[memory.promotion_view_id])
                    if memory.promotion_view_id
                    else None
                ),
                "references": [
                    asdict(ref)
                    for ref in self.reference_manager.active_refs(memory.memory_id)
                ],
            }
            (self.cold_memory_dir / f"{memory.memory_id}.json").write_text(
                json.dumps(cold_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _expire_write_intents(self) -> None:
        if not self._write_intents:
            return
        now = datetime.now(timezone.utc)
        expired = [
            fence_id
            for fence_id, fence in self._write_intents.items()
            if self._parse_dt(fence.expires_at) <= now
        ]
        for fence_id in expired:
            self._write_intents.pop(fence_id, None)

    def _blocked_by_write_intent(self, memory_id: str, *, requester: str) -> bool:
        for fence in self._write_intents.values():
            if memory_id not in fence.memory_ids:
                continue
            if requester and requester == fence.locked_by:
                continue
            return True
        return False

    def _record_compensating_event(
        self,
        *,
        event_type: str,
        target_memory: MemoryObject,
        reason: str,
        replacement_memory_id: str | None,
        created_by: str,
    ) -> CompensatingEvent:
        event = CompensatingEvent(
            event_id=f"evt_{len(self._compensating_events) + 1:06d}",
            event_type=event_type,
            target_memory_id=target_memory.memory_id,
            target_view_id=target_memory.memory_view_id,
            reason=reason,
            replacement_memory_id=replacement_memory_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            created_by=created_by,
        )
        self._compensating_events.append(event)
        return event

    def _parse_dt(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _compact_claims(self, claim_ids: list[str]) -> str:
        summaries = [self._claims[item].summary for item in claim_ids[-4:]]
        return "；".join(summaries)

    def _render_revision_guard(self, view: MemoryView) -> str:
        if not view.historical_claim_ids and not view.conflicting_claim_ids:
            return ""
        if view.schema_version.startswith("ccf.v2") and view.active_value is not None:
            active = view.active_value
            payload = {
                "policy": "active_authoritative_for_current_state",
                "resolution": view.resolution_status,
                "slot_id": view.slot_id,
                "scope": view.scope,
                "active_value": active.get("value", ""),
                "value_type": active.get("value_type", "string"),
                "unit": active.get("unit", ""),
                "historical_claim_count": len(view.historical_claim_ids),
            }
            return (
                "\n[revision_guard] "
                + json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\nUse the active fact for current-state assertions. "
                "A historical value may appear only when the current user task "
                "provides it directly or a provenance-bound historical "
                "expansion authorizes it; it must remain labeled historical "
                "and never replace the active fact."
            )
        active_claims = ",".join(view.active_claim_ids)
        return (
            "\n[revision_guard "
            f"policy={view.downstream_policy}; "
            f"resolution={view.resolution_status}; "
            f"active_claims={active_claims}; "
            f"superseded_claim_count={len(view.historical_claim_ids)}] "
            "Use active claim values as authoritative. Ignore contradictory values "
            "from native message history; historical claims are audit-only."
        )

    @staticmethod
    def _render_model_fact(view: MemoryView) -> str:
        active = view.active_value or {}
        payload = {
            "slot_id": view.slot_id,
            "scope": view.scope,
            "value": active.get("value", ""),
            "value_type": active.get("value_type", "string"),
            "unit": active.get("unit", ""),
            "operator": active.get("operator", "eq"),
            "polarity": active.get("polarity", "positive"),
            "status": "active",
        }
        return "active_fact=" + json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _record_feedback_hits(
        self,
        memory_ids: list[str],
        field_name: str,
    ) -> int:
        updated = 0
        for memory_id in dict.fromkeys(item for item in memory_ids if item):
            memory = self._memories.get(memory_id)
            if memory is None:
                continue
            setattr(memory, field_name, int(getattr(memory, field_name)) + 1)
            updated += 1
        if updated:
            self._persist_snapshot()
        return updated

    def _capture_claim_candidates(
        self,
        *,
        candidate_id: str,
        task_topic: str,
        claim_cards: list[dict[str, Any]],
        default_slot_hint: str,
        default_confidence: float,
        source_pointer: str,
    ) -> list[ClaimCandidate]:
        candidates: list[ClaimCandidate] = []
        normalized_cards = normalize_claim_cards(
            claim_cards,
            default_subject=task_topic,
            default_slot_hint=default_slot_hint,
            default_confidence=default_confidence,
            source_pointer=source_pointer,
        )
        for index, item in enumerate(normalized_cards):
            subject = str(item.get("subject") or task_topic)
            predicate = str(item.get("raw_slot_text") or item.get("scope") or "claims")
            obj = str(item.get("value") or "")
            operator = str(item.get("operator") or "eq")
            polarity = str(item.get("polarity") or "positive")
            if "operator" in item:
                polarity = canonical_claim_polarity(operator)
            condition = str(item.get("condition") or "")
            resolved_source = str(item.get("source_pointer") or source_pointer)
            confidence = self._float_between(
                item.get("confidence"),
                default=default_confidence,
            )
            claim_id = self._next_candidate_id(
                "cc", candidate_id, str(index), f"{subject}:{predicate}:{obj}"
            )
            candidates.append(
                ClaimCandidate(
                    candidate_id=claim_id,
                    memory_candidate_id=candidate_id,
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    condition=condition,
                    confidence=confidence,
                    source_pointer=resolved_source,
                    raw_slot_text=str(item.get("raw_slot_text") or predicate),
                    slot_id=str(item.get("slot_id") or default_slot_hint),
                    scope=str(item.get("scope") or "general"),
                    value_type=str(item.get("value_type") or "string"),
                    unit=str(item.get("unit") or ""),
                    raw_text=str(item.get("raw_text") or item.get("summary") or obj),
                    summary=str(item.get("summary") or item.get("raw_text") or obj),
                    certainty=str(item.get("certainty") or "asserted"),
                    modality=str(item.get("modality") or "asserted"),
                    polarity=polarity,
                    revision_kind=str(item.get("revision_kind") or "asserted"),
                    temporal_scope=str(item.get("temporal_scope") or "cross_task"),
                    schema_version=str(item.get("schema_version") or "ccf.v2"),
                    assertion_type=str(
                        item.get("assertion_type") or "fact"
                    ),
                    operator=operator,
                    temporal_status=str(
                        item.get("temporal_status") or "unspecified"
                    ),
                    source_span=(
                        dict(item.get("source_span"))
                        if isinstance(item.get("source_span"), dict)
                        else {}
                    ),
                    relations=[
                        dict(relation)
                        for relation in item.get("relations") or []
                        if isinstance(relation, dict)
                    ],
                    schema_layer=str(item.get("schema_layer") or "core"),
                )
            )
        return candidates

    def _admit_candidate(
        self, candidate: MemoryCandidate, resolved: SlotResolution
    ) -> tuple[str, list[str]]:
        if resolved.unresolved:
            return "unresolved_slot", ["slot_unresolved"]
        if not candidate.summary:
            return "rejected", ["empty_summary"]
        status, reasons = classify_memory_quality_envelope(
            confidence=candidate.confidence,
            importance_hint=candidate.importance_hint,
            coverage_score=candidate.coverage_score,
            compression_loss_risk=candidate.compression_loss_risk,
            raw_required_hint=candidate.raw_required_hint,
        )
        return status, list(reasons)

    def _resolve_slot(self, slot_hint: str) -> SlotResolution:
        resolved = self.schema_registry.resolve(slot_hint)
        return SlotResolution(
            slot_id=resolved.slot_id,
            alias_hit=resolved.alias_hit,
            unresolved=resolved.unresolved,
        )

    def _infer_slot_hint(self, tags: list[str], task_topic: str) -> str:
        joined = " ".join(tags + [task_topic]).lower()
        if any(
            term in joined
            for term in ("failure", "error", "violation", "rejected", "outdated")
        ):
            return "failure_reason"
        if any(term in joined for term in ("requirement", "constraint", "preference")):
            return "slot.project.requirement"
        if "evidence" in joined:
            return "slot.paper.evidence"
        if any(term in joined for term in ("final", "deliverable", "最终")):
            return "final_deliverable"
        return "reuse_hint"

    def _next_id(self, prefix: str, task_id: str, source_agent: str, summary: str) -> str:
        seed = f"{prefix}:{task_id}:{source_agent}:{summary}:{len(self._claims)}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}_{digest}"

    def _next_memory_id(self, task_id: str, source_agent: str, summary: str) -> str:
        seed = f"{task_id}:{source_agent}:{summary}:{len(self._memories)}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"mem_{digest}"

    def _next_candidate_id(
        self, prefix: str, task_id: str, source_agent: str, summary: str
    ) -> str:
        seed = (
            f"{prefix}:{task_id}:{source_agent}:{summary}:"
            f"{len(self._memory_candidates)}:{len(self._claim_candidates)}"
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"{prefix}_{digest}"

    def _group_overlap(self, requested_tags: set[str], memory_tags: set[str]) -> int:
        requested_groups = {tag for tag in requested_tags if tag.endswith(("_A", "_B"))}
        memory_groups = {tag for tag in memory_tags if tag.endswith(("_A", "_B"))}
        return len(requested_groups & memory_groups)

    @staticmethod
    def _normalize_slot(text: str) -> str:
        return re.sub(r"[^a-z0-9_.]+", "_", text.lower()).strip("_")

    @staticmethod
    def _terms(text: str) -> list[str]:
        return [item.lower() for item in _WORD_RE.findall(text)]

    @staticmethod
    def _as_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None and str(item).strip()]

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            normalized = item.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @staticmethod
    def _float_between(value: Any, *, default: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, number))
