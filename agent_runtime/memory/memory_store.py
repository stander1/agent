from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

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


@dataclass(slots=True)
class MemoryView:
    memory_view_id: str
    slot_id: str
    active_claim_ids: list[str]
    prompt_summary: str
    audit_claim_ids: list[str]
    created_at: str
    status: str = "active"


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


@dataclass(slots=True)
class MemoryWriteReport:
    memory_ref: MemoryRef
    memory_write_count: int = 1
    claim_card_count: int = 1
    memory_view_count: int = 1
    promotion_view_count: int = 0
    alias_mapping_hit_count: int = 0
    unresolved_slot_count: int = 0


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
class MemorySearchReport:
    refs: list[MemoryRef]
    retrieval_backend: str = "keyword_overlap_v3_lite"
    vector_retrieval_count: int = 0
    alias_mapping_hit_count: int = 0
    unresolved_slot_count: int = 0


@dataclass(slots=True)
class SlotResolution:
    slot_id: str
    alias_hit: bool = False
    unresolved: bool = False


class MemoryStoreLite:
    """In-memory v3-lite ClaimCard and MemoryView store."""

    def __init__(self) -> None:
        self._memories: dict[str, MemoryObject] = {}
        self._claims: dict[str, ClaimCard] = {}
        self._views: dict[str, MemoryView] = {}
        self._promotion_views: dict[str, PromotionView] = {}
        self._memory_candidates: dict[str, MemoryCandidate] = {}
        self._claim_candidates: dict[str, ClaimCandidate] = {}

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
        claim = ClaimCard(
            claim_id=claim_id,
            subject=task_topic,
            slot_id=resolved.slot_id,
            summary=summary,
            source_agent=source_agent,
            source_task_id=task_id,
            promotion_view_id=promotion_view_id,
            confidence=confidence,
            status="active" if not resolved.unresolved else "unresolved_slot",
            tags=tags,
            created_at=now,
        )
        self._claims[claim_id] = claim

        memory_view = self._upsert_memory_view(claim)
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
        )
        self._memories[memory_id] = memory
        return MemoryWriteReport(
            memory_ref=self.ref(memory),
            promotion_view_count=promotion_count,
            alias_mapping_hit_count=int(resolved.alias_hit),
            unresolved_slot_count=int(resolved.unresolved),
        )

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
        self, query: str, tags: list[str] | None = None, top_k: int = 3
    ) -> list[MemoryRef]:
        return self.search_memory_with_report(query, tags=tags, top_k=top_k).refs

    def search_memory_with_report(
        self, query: str, tags: list[str] | None = None, top_k: int = 3
    ) -> MemorySearchReport:
        query_terms = set(self._terms(query))
        requested_tags = set(tags or [])
        scored: list[tuple[int, MemoryObject]] = []
        for memory in self._memories.values():
            if memory.status != "active":
                continue
            claim = self._claims[memory.claim_id]
            view = self._views[memory.memory_view_id]
            searchable = " ".join(
                [memory.summary, claim.summary, view.prompt_summary, " ".join(memory.tags)]
            )
            memory_terms = set(self._terms(searchable))
            overlap = len(query_terms & memory_terms)
            tag_overlap = len(requested_tags & set(memory.tags))
            group_overlap = self._group_overlap(requested_tags, set(memory.tags))
            score = overlap + tag_overlap * 3 + group_overlap * 5
            if score > 0:
                scored.append((score, memory))

        scored.sort(key=lambda item: (-item[0], item[1].created_at))
        refs: list[MemoryRef] = []
        for _, memory in scored[:top_k]:
            memory.hit_count += 1
            refs.append(self.ref(memory))
        return MemorySearchReport(refs=refs)

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

    def ref_to_dict(self, memory_ref: MemoryRef) -> dict:
        return asdict(memory_ref)

    def ref(self, memory: MemoryObject) -> MemoryRef:
        return MemoryRef(
            memory_id=memory.memory_id,
            version_id=memory.version_id,
            status=memory.status,
            task_topic=memory.task_topic,
            memory_view_id=memory.memory_view_id,
            slot_id=memory.slot_id,
        )

    def _upsert_memory_view(self, claim: ClaimCard) -> MemoryView:
        view_id = f"view_{hashlib.sha256(claim.slot_id.encode('utf-8')).hexdigest()[:10]}"
        if view_id in self._views:
            view = self._views[view_id]
            if claim.claim_id not in view.active_claim_ids and claim.status == "active":
                view.active_claim_ids.append(claim.claim_id)
            view.audit_claim_ids.append(claim.claim_id)
            view.prompt_summary = self._compact_claims(view.active_claim_ids)
            return view

        view = MemoryView(
            memory_view_id=view_id,
            slot_id=claim.slot_id,
            active_claim_ids=[claim.claim_id] if claim.status == "active" else [],
            prompt_summary=claim.summary,
            audit_claim_ids=[claim.claim_id],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._views[view_id] = view
        return view

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
        normalized = self._normalize_slot(slot_hint)
        if normalized in CANONICAL_SLOTS:
            return SlotResolution(slot_id=normalized)
        if normalized in ALIAS_MAPPING:
            return SlotResolution(slot_id=ALIAS_MAPPING[normalized], alias_hit=True)
        for slot in CANONICAL_SLOTS:
            if normalized == self._normalize_slot(slot.split(".")[-1]):
                return SlotResolution(slot_id=slot)
        return SlotResolution(slot_id="unresolved_slot", unresolved=True)

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
