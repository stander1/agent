from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_runtime.memory.memory_store import MemoryAdmissionReport, MemoryStoreLite


@dataclass(slots=True)
class PromotionViewDraft:
    source_state_ids: list[str]
    evidence_refs: list[str]
    core_claim: str
    reuse_intent: str
    source_agent: str
    task_topic: str


@dataclass(slots=True)
class PromotionCandidate:
    memory_card: dict[str, Any]
    claim_cards: list[dict[str, Any]]
    slot_hint: str
    tags: list[str]
    source_state_ids: list[str]
    evidence_refs: list[str]
    reuse_intent: str


@dataclass(slots=True)
class SemanticValidationResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


class PromotionViewRenderer:
    """Builds the semantic view between machine state and memory candidates."""

    def render(
        self,
        *,
        task_topic: str,
        source_agent: str,
        fallback_summary: str,
        source_state_ids: list[str],
        evidence_refs: list[str],
        reuse_intent: str,
    ) -> PromotionViewDraft:
        return PromotionViewDraft(
            source_state_ids=list(source_state_ids),
            evidence_refs=list(evidence_refs or source_state_ids[:3]),
            core_claim=fallback_summary.strip(),
            reuse_intent=reuse_intent,
            source_agent=source_agent,
            task_topic=task_topic,
        )


class MemoryPromotionCompiler:
    """Compiles a promotion view and optional control header into a candidate."""

    def compile(
        self,
        *,
        promotion_view: PromotionViewDraft,
        control: dict[str, Any] | None,
        tags: list[str],
        slot_hint: str,
        degraded: bool,
    ) -> PromotionCandidate:
        control = control if isinstance(control, dict) else {}
        memory_card = control.get("memory_card")
        if not isinstance(memory_card, dict):
            memory_card = {}
        claim_cards = control.get("claim_cards")
        if not isinstance(claim_cards, list):
            claim_cards = []

        memory_card = dict(memory_card)
        memory_card.setdefault("summary", promotion_view.core_claim)
        memory_card.setdefault("tags", tags)
        memory_card.setdefault("reuse_scope", [promotion_view.task_topic])
        memory_card.setdefault("slot_hint", slot_hint)
        if degraded:
            memory_card.setdefault("confidence", 0.42)
            memory_card.setdefault("importance_hint", 0.4)
            memory_card.setdefault("coverage_score", 0.25)
            memory_card.setdefault("compression_loss_risk", "high")
            memory_card.setdefault("raw_required_hint", True)
        else:
            memory_card.setdefault("confidence", 0.78)
            memory_card.setdefault("importance_hint", 0.68)
            memory_card.setdefault("coverage_score", 0.66)
            memory_card.setdefault("compression_loss_risk", "medium")
            memory_card.setdefault("raw_required_hint", False)

        if not claim_cards and promotion_view.core_claim:
            claim_cards = [
                {
                    "subject": promotion_view.task_topic,
                    "predicate": "summarizes",
                    "object": promotion_view.core_claim,
                    "source_pointer": ",".join(promotion_view.evidence_refs),
                    "confidence": memory_card.get("confidence", 0.5),
                }
            ]

        return PromotionCandidate(
            memory_card=memory_card,
            claim_cards=claim_cards,
            slot_hint=slot_hint,
            tags=list(tags),
            source_state_ids=list(promotion_view.source_state_ids),
            evidence_refs=list(promotion_view.evidence_refs),
            reuse_intent=promotion_view.reuse_intent,
        )


class SemanticValidatorLite:
    """Rules-first guard that keeps raw state from bypassing admission."""

    def validate(self, candidate: PromotionCandidate) -> SemanticValidationResult:
        reasons: list[str] = []
        if not str(candidate.memory_card.get("summary") or "").strip():
            reasons.append("empty_summary")
        if not candidate.source_state_ids:
            reasons.append("missing_source_state")
        if not candidate.evidence_refs:
            reasons.append("missing_evidence_ref")
        if not candidate.slot_hint:
            reasons.append("missing_slot_hint")
        return SemanticValidationResult(allowed=not reasons, reasons=reasons)


class StateToMemoryBridgeLite:
    """State-to-Memory Semantic Bridge lite implementation.

    The bridge keeps runtime state, promotion views, candidates, and long-term
    memory writes in one explicit path. It never writes raw state directly into
    MemoryStoreLite; all writes pass through Memory Admission.
    """

    def __init__(
        self,
        memory_store: MemoryStoreLite,
        *,
        renderer: PromotionViewRenderer | None = None,
        compiler: MemoryPromotionCompiler | None = None,
        validator: SemanticValidatorLite | None = None,
    ) -> None:
        self.memory_store = memory_store
        self.renderer = renderer or PromotionViewRenderer()
        self.compiler = compiler or MemoryPromotionCompiler()
        self.validator = validator or SemanticValidatorLite()

    def promote(
        self,
        *,
        task_id: str,
        source_agent: str,
        task_topic: str,
        fallback_summary: str,
        tags: list[str],
        slot_hint: str,
        source_state_ids: list[str],
        evidence_refs: list[str],
        reuse_intent: str,
        control: dict[str, Any] | None = None,
        degraded: bool = False,
    ) -> tuple[MemoryAdmissionReport, SemanticValidationResult]:
        promotion_view = self.renderer.render(
            task_topic=task_topic,
            source_agent=source_agent,
            fallback_summary=fallback_summary,
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            reuse_intent=reuse_intent,
        )
        candidate = self.compiler.compile(
            promotion_view=promotion_view,
            control=control,
            tags=tags,
            slot_hint=slot_hint,
            degraded=degraded,
        )
        validation = self.validator.validate(candidate)
        memory_card = dict(candidate.memory_card)
        if not validation.allowed:
            memory_card["confidence"] = min(float(memory_card.get("confidence", 0.5)), 0.45)
            memory_card["importance_hint"] = min(
                float(memory_card.get("importance_hint", 0.5)), 0.45
            )
            memory_card["coverage_score"] = min(
                float(memory_card.get("coverage_score", 0.5)), 0.3
            )
            memory_card["compression_loss_risk"] = "high"
            memory_card["raw_required_hint"] = True

        report = self.memory_store.write_memory_candidate_with_report(
            task_id=task_id,
            source_agent=source_agent,
            task_topic=task_topic,
            memory_card=memory_card,
            claim_cards=candidate.claim_cards,
            tags=candidate.tags,
            slot_hint=candidate.slot_hint,
            source_state_ids=candidate.source_state_ids,
            evidence_refs=candidate.evidence_refs,
            reuse_intent=candidate.reuse_intent,
            fallback_summary=fallback_summary,
        )
        if not validation.allowed:
            report.admission_reasons = list(
                dict.fromkeys(report.admission_reasons + validation.reasons)
            )
        return report, validation

