from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_runtime.memory.references import (
    MemoryReferenceManagerLite,
    MemoryReferenceRecord,
)
from agent_runtime.memory.schema_registry import SchemaRegistryLite

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
    "slot.project.requirement.travel",
    "slot.project.requirement.security_audit",
    "slot.system.deliverable_requirement",
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
    "travel_preference": "slot.project.requirement.travel",
    "travel_budget": "slot.project.requirement.travel",
    "security_audit": "slot.project.requirement.security_audit",
    "evidence_chain": "slot.project.requirement.security_audit",
    "final_deliverable": "slot.system.deliverable_requirement",
}

PROMPT_VIEW_MEMORY_STATUSES = {"active", "provisional_active"}
DORMANT_MEMORY_STATUSES = {"dormant"}
BLOCKED_MEMORY_STATUSES = {
    "deprecated",
    "deleted",
    "superseded",
    "outdated",
    "unresolved_conflict",
    "pending_lifecycle_update",
}
MEMORY_LIFECYCLE_STATUSES = PROMPT_VIEW_MEMORY_STATUSES | DORMANT_MEMORY_STATUSES | BLOCKED_MEMORY_STATUSES


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


@dataclass(slots=True)
class MemoryAdmissionReport:
    candidate_id: str
    admission_status: str
    admission_reasons: list[str] = field(default_factory=list)
    memory_ref: MemoryRef | None = None
    memory_candidate_count: int = 1
    claim_candidate_count: int = 0
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


@dataclass(slots=True)
class MemoryCompactionReport:
    compaction_id: str
    memory_view_count: int = 0
    new_claim_count: int = 0
    merged_claim_count: int = 0
    superseded_claim_count: int = 0
    unresolved_conflict_count: int = 0
    compaction_log_count: int = 1


@dataclass(slots=True)
class SlotResolution:
    slot_id: str
    alias_hit: bool = False
    unresolved: bool = False


class MemoryStoreLite:
    """In-memory v3-lite ClaimCard and MemoryView store."""

    def __init__(self, storage_dir: Path | None = None) -> None:
        self.schema_registry = SchemaRegistryLite(CANONICAL_SLOTS, ALIAS_MAPPING)
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
            claim_type=claim_type,
            polarity=polarity,
            modality=modality,
            temporal_scope=temporal_scope,
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
        )
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
        )
        resolved = self._resolve_slot(candidate.slot_hint)
        status, reasons = self._admit_candidate(candidate, resolved)
        candidate.admission_status = status
        candidate.admission_reasons = reasons
        self._memory_candidates[candidate_id] = candidate
        for claim in claim_candidates:
            claim.admission_status = status
            self._claim_candidates[claim.candidate_id] = claim

        report = MemoryAdmissionReport(
            candidate_id=candidate_id,
            admission_status=status,
            admission_reasons=reasons,
            claim_candidate_count=len(claim_candidates),
            memory_admitted_count=int(status == "admitted"),
            memory_rejected_count=int(status == "rejected"),
            memory_pending_count=int(status == "pending"),
            memory_audit_only_count=int(status == "audit_only"),
            admission_unresolved_slot_count=int(status == "unresolved_slot"),
            alias_mapping_hit_count=int(resolved.alias_hit),
            unresolved_slot_count=int(resolved.unresolved),
        )

        if status != "admitted":
            self._persist_snapshot()
            return report

        write_report = self.write_memory_with_report(
            task_id=task_id,
            source_agent=source_agent,
            task_topic=task_topic,
            summary=summary,
            tags=candidate_tags,
            slot_hint=resolved.slot_id,
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            reuse_intent=reuse_intent,
            confidence=confidence,
            claim_type=str(card.get("claim_type") or "fact"),
            polarity=str(card.get("polarity") or "positive"),
            modality=str(card.get("modality") or "asserted"),
            temporal_scope=str(card.get("temporal_scope") or "current_task"),
            schema_version=str(card.get("schema_version") or "ccf.v1-lite"),
        )
        report.memory_ref = write_report.memory_ref
        report.memory_write_count = write_report.memory_write_count
        report.claim_card_count = write_report.claim_card_count
        report.memory_view_count = write_report.memory_view_count
        report.promotion_view_count = write_report.promotion_view_count
        report.alias_mapping_hit_count += write_report.alias_mapping_hit_count
        report.unresolved_slot_count += write_report.unresolved_slot_count
        report.claim_to_memoryview_count = write_report.claim_card_count
        return report

    def search_memory(
        self,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 3,
        required_tags: list[str] | None = None,
    ) -> list[MemoryRef]:
        return self.search_memory_with_report(
            query,
            tags=tags,
            top_k=top_k,
            required_tags=required_tags,
        ).refs

    def search_memory_with_report(
        self,
        query: str,
        tags: list[str] | None = None,
        top_k: int = 3,
        required_tags: list[str] | None = None,
    ) -> MemorySearchReport:
        self.apply_lifecycle_transitions()
        query_terms = set(self._terms(query))
        requested_tags = set(tags or [])
        required_tag_set = set(required_tags or [])
        scored: list[tuple[int, MemoryObject]] = []
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
            score = overlap + tag_overlap * 3 + group_overlap * 5
            if memory.status in DORMANT_MEMORY_STATUSES:
                score = max(0, score - 2)
            if score > 0:
                scored.append((score, memory))

        scored.sort(key=lambda item: (-item[0], item[1].created_at))
        refs: list[MemoryRef] = []
        now = datetime.now(timezone.utc).isoformat()
        for _, memory in scored[:top_k]:
            memory.hit_count += 1
            memory.last_accessed_at = now
            refs.append(self.ref(memory))
        if refs:
            self._persist_snapshot()
        return MemorySearchReport(refs=refs)

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
        created_by: str = "ReviewerAgent",
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

    def memory_references(
        self, memory_ref: MemoryRef, ref_type: str | None = None
    ) -> list[MemoryReferenceRecord]:
        return self.reference_manager.active_refs(memory_ref.memory_id, ref_type)  # type: ignore[arg-type]

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
        rendered = (
            f"[memory_view:{view.memory_view_id}] slot={view.slot_id}; "
            f"claim={claim.claim_id}; {view.prompt_summary}; tags=[{tag_text}]"
        )
        return rendered[:budget_chars]

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

    def run_compaction(self, *, slot_id: str | None = None) -> MemoryCompactionReport:
        self.apply_lifecycle_transitions()
        compaction_id = f"compact_{len(self._compaction_log) + 1:04d}"
        views = [
            view
            for view in self._views.values()
            if slot_id is None or view.slot_id == slot_id
        ]
        report = MemoryCompactionReport(compaction_id=compaction_id)
        for view in views:
            active_claim_ids = [
                claim_id
                for claim_id in view.active_claim_ids
                if self._claims[claim_id].status == "active"
            ]
            view.active_claim_ids = active_claim_ids
            view.prompt_summary = self._compact_claims(active_claim_ids)
            view.resolution_status = (
                "unresolved_conflict" if view.conflicting_claim_ids else "resolved"
            )
            report.memory_view_count += 1
            report.new_claim_count += len(view.audit_claim_ids)
            report.merged_claim_count += max(0, len(view.audit_claim_ids) - len(active_claim_ids))
            report.superseded_claim_count += len(view.historical_claim_ids)
            report.unresolved_conflict_count += len(view.conflicting_claim_ids)
        self._compaction_log.append(
            {
                "compaction_id": compaction_id,
                "slot_id": slot_id,
                "memory_view_count": report.memory_view_count,
                "new_claim_count": report.new_claim_count,
                "merged_claim_count": report.merged_claim_count,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._persist_snapshot()
        return report

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
        view_id = f"view_{hashlib.sha256(claim.slot_id.encode('utf-8')).hexdigest()[:10]}"
        if view_id in self._views:
            view = self._views[view_id]
            superseded_count = 0
            conflict_count = 0
            view.audit_claim_ids.append(claim.claim_id)
            if claim.status == "active":
                superseded_count, conflict_count = self._resolve_claim_conflict(
                    view, claim
                )
            view.prompt_summary = self._compact_claims(view.active_claim_ids)
            view.resolution_status = (
                "unresolved_conflict" if view.conflicting_claim_ids else "resolved"
            )
            return view, superseded_count, conflict_count

        view = MemoryView(
            memory_view_id=view_id,
            slot_id=claim.slot_id,
            active_claim_ids=[claim.claim_id] if claim.status == "active" else [],
            prompt_summary=claim.summary,
            audit_claim_ids=[claim.claim_id],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._views[view_id] = view
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

    def _capture_claim_candidates(
        self,
        *,
        candidate_id: str,
        task_topic: str,
        claim_cards: list[dict[str, Any]],
    ) -> list[ClaimCandidate]:
        candidates: list[ClaimCandidate] = []
        for index, item in enumerate(claim_cards):
            if not isinstance(item, dict):
                continue
            subject = str(item.get("subject") or task_topic)
            predicate = str(item.get("predicate") or item.get("claim_type") or "claims")
            obj = str(item.get("object") or item.get("summary") or item.get("claim") or "")
            condition = str(item.get("condition") or "")
            source_pointer = str(item.get("source_pointer") or item.get("source") or "")
            confidence = self._float_between(item.get("confidence"), default=0.5)
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
                    source_pointer=source_pointer,
                )
            )
        return candidates

    def _admit_candidate(
        self, candidate: MemoryCandidate, resolved: SlotResolution
    ) -> tuple[str, list[str]]:
        reasons: list[str] = []
        if resolved.unresolved:
            return "unresolved_slot", ["slot_unresolved"]
        if not candidate.summary:
            return "rejected", ["empty_summary"]
        if candidate.confidence < 0.35:
            return "rejected", ["confidence_below_rejection_threshold"]
        if candidate.raw_required_hint and candidate.compression_loss_risk == "high":
            return "audit_only", ["high_loss_raw_required"]
        if candidate.confidence < 0.55:
            reasons.append("confidence_below_admission_threshold")
        if candidate.importance_hint < 0.45:
            reasons.append("importance_below_admission_threshold")
        if candidate.coverage_score < 0.35:
            reasons.append("coverage_below_admission_threshold")
        if reasons:
            return "pending", reasons
        return "admitted", ["rules_admitted"]

    def _resolve_slot(self, slot_hint: str) -> SlotResolution:
        resolved = self.schema_registry.resolve(slot_hint)
        return SlotResolution(
            slot_id=resolved.slot_id,
            alias_hit=resolved.alias_hit,
            unresolved=resolved.unresolved,
        )

    def _infer_slot_hint(self, tags: list[str], task_topic: str) -> str:
        joined = " ".join(tags + [task_topic]).lower()
        if "travel" in joined or "旅行" in joined or "budget" in joined:
            return "travel_preference"
        if "security" in joined or "audit" in joined or "审计" in joined:
            return "security_audit"
        if "reviewer" in joined or "failure" in joined:
            return "failure_reason"
        if "memory_manager" in joined or "writer" in joined:
            return "reuse_hint"
        return "final_deliverable" if "最终" in task_topic else "reuse_hint"

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
