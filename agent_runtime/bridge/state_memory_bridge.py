from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from agent_runtime.memory.claim_extractor import (
    extract_canonical_claim_candidates,
    extract_claim_cards,
)
from agent_runtime.memory.memory_store import MemoryAdmissionReport, MemoryStoreLite
from agent_runtime.memory.schema_registry import (
    SourceSpan,
    canonical_claim_polarity,
)
from agent_runtime.memory.semantic_disambiguator import (
    SemanticDisambiguationRequest,
    SemanticDisambiguationResult,
    SemanticDisambiguator,
)

DISAMBIGUATION_POLICIES = {
    "rules_only",
    "fallback",
    "control_required",
}


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
    canonical_claim_candidates: list[dict[str, Any]]
    source_text: str
    slot_hint: str
    tags: list[str]
    source_state_ids: list[str]
    evidence_refs: list[str]
    reuse_intent: str


@dataclass(slots=True)
class SemanticValidationResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    explicit_claim_count: int = 0
    explicit_claim_valid_count: int = 0
    canonical_candidate_count: int = 0
    canonical_candidate_valid_count: int = 0
    dynamic_schema_registration_count: int = 0
    unresolved_schema_count: int = 0
    disambiguation_status: str = "not_requested"
    disambiguation_call_count: int = 0
    disambiguation_accepted_candidate_count: int = 0
    disambiguation_rejected_candidate_count: int = 0
    disambiguation_locally_rebound_candidate_count: int = 0
    disambiguation_diagnostics: list[str] = field(default_factory=list)
    control_prompt_tokens: int = 0
    control_completion_tokens: int = 0
    control_total_tokens: int = 0
    control_usage_estimated: bool = False
    control_retry_count: int = 0
    control_model: str = ""
    control_latency_ms: float = 0.0
    disambiguation_policy: str = "fallback"


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

    def __init__(self, *, legacy_domain_extractor_enabled: bool = False) -> None:
        self.legacy_domain_extractor_enabled = legacy_domain_extractor_enabled

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

        canonical_claim_candidates: list[dict[str, Any]] = []
        if not claim_cards and promotion_view.core_claim:
            canonical_claim_candidates = extract_canonical_claim_candidates(
                promotion_view.core_claim,
                subject=promotion_view.task_topic,
                source_id=(
                    promotion_view.evidence_refs[0]
                    if promotion_view.evidence_refs
                    else "promotion_view"
                ),
                default_confidence=float(memory_card.get("confidence", 0.5)),
            )
            if (
                not canonical_claim_candidates
                and self.legacy_domain_extractor_enabled
            ):
                claim_cards = extract_claim_cards(
                    promotion_view.core_claim,
                    subject=promotion_view.task_topic,
                    source_pointer=",".join(promotion_view.evidence_refs),
                    default_confidence=float(
                        memory_card.get("confidence", 0.5)
                    ),
                )

        return PromotionCandidate(
            memory_card=memory_card,
            claim_cards=claim_cards,
            canonical_claim_candidates=canonical_claim_candidates,
            source_text=promotion_view.core_claim,
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


class CanonicalClaimSemanticValidator:
    """Validates evidence-bound open claims without domain assumptions."""

    def validate(
        self,
        candidate: dict[str, Any],
        *,
        source_text: str,
    ) -> SemanticValidationResult:
        reasons: list[str] = []
        predicate = str(candidate.get("predicate") or "").strip()
        value = str(candidate.get("value") or "").strip()
        if not predicate:
            reasons.append("missing_open_predicate")
        if not value:
            reasons.append("missing_claim_value")

        span_payload = candidate.get("source_span")
        span = self._source_span(span_payload)
        if span is None:
            reasons.append("missing_or_invalid_source_span")
        elif not span.matches(source_text):
            reasons.append("source_span_mismatch")
        else:
            quote = span.quote
            if value and not self._contains_value(
                quote,
                value,
                str(candidate.get("value_type") or "string"),
            ):
                reasons.append("claim_value_not_in_source_span")
            unit = str(candidate.get("unit") or "").strip()
            if unit and unit.casefold() not in quote.casefold():
                reasons.append("claim_unit_not_in_source_span")
            for relation in candidate.get("relations") or []:
                if not isinstance(relation, dict):
                    reasons.append("invalid_claim_relation")
                    continue
                target_value = str(relation.get("target_value") or "").strip()
                if target_value and not self._contains_value(
                    quote,
                    target_value,
                    "string",
                ):
                    reasons.append("relation_target_not_in_source_span")

        temporal_status = str(
            candidate.get("temporal_status") or "unspecified"
        )
        if temporal_status not in {
            "current",
            "historical",
            "future",
            "unspecified",
        }:
            reasons.append("invalid_temporal_status")
        operator = str(candidate.get("operator") or "eq")
        if operator not in {"eq", "ne", "lt", "le", "gt", "ge"}:
            reasons.append("invalid_claim_operator")
        return SemanticValidationResult(
            allowed=not reasons,
            reasons=list(dict.fromkeys(reasons)),
        )

    @staticmethod
    def _source_span(payload: Any) -> SourceSpan | None:
        if not isinstance(payload, dict):
            return None
        try:
            return SourceSpan(
                source_id=str(payload.get("source_id") or ""),
                start=int(payload["start"]),
                end=int(payload["end"]),
                text_hash=str(payload.get("text_hash") or ""),
                quote=str(payload.get("quote") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _contains_value(quote: str, value: str, value_type: str) -> bool:
        if value_type == "number":
            return re.sub(r"(?<=\d),(?=\d)", "", value) in re.sub(
                r"(?<=\d),(?=\d)",
                "",
                quote,
            )
        normalized_value = re.sub(r"\s+", " ", value).strip().casefold()
        normalized_quote = re.sub(r"\s+", " ", quote).casefold()
        return normalized_value in normalized_quote


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
        canonical_validator: CanonicalClaimSemanticValidator | None = None,
        semantic_disambiguator: SemanticDisambiguator | None = None,
        allow_dynamic_schema: bool = True,
    ) -> None:
        self.memory_store = memory_store
        self.renderer = renderer or PromotionViewRenderer()
        self.compiler = compiler or MemoryPromotionCompiler()
        self.validator = validator or SemanticValidatorLite()
        self.canonical_validator = (
            canonical_validator or CanonicalClaimSemanticValidator()
        )
        self.semantic_disambiguator = semantic_disambiguator
        self.allow_dynamic_schema = allow_dynamic_schema

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
        scope_id: str = "",
        control: dict[str, Any] | None = None,
        degraded: bool = False,
        disambiguation_policy: str = "fallback",
    ) -> tuple[MemoryAdmissionReport, SemanticValidationResult]:
        if disambiguation_policy not in DISAMBIGUATION_POLICIES:
            raise ValueError(
                "disambiguation_policy must be one of "
                f"{sorted(DISAMBIGUATION_POLICIES)}"
            )
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
        disambiguation = SemanticDisambiguationResult(
            status="not_requested",
        )
        disambiguation_reasons: list[str] = []
        if disambiguation_policy == "control_required":
            candidate.claim_cards = []
            candidate.canonical_claim_candidates = []
        disambiguation_required = (
            disambiguation_policy != "rules_only"
            and (
                disambiguation_policy == "control_required"
                or (
                    not candidate.claim_cards
                    and not candidate.canonical_claim_candidates
                )
            )
        )
        if disambiguation_required and self.semantic_disambiguator is None:
            if disambiguation_policy == "control_required":
                disambiguation = SemanticDisambiguationResult(
                    status="unavailable",
                    reasons=("semantic_disambiguator_unavailable",),
                )
                disambiguation_reasons.extend(
                    (
                        "semantic_disambiguation_required",
                        "semantic_disambiguator_unavailable",
                    )
                )
        elif disambiguation_required:
            disambiguation = self.semantic_disambiguator.disambiguate(
                SemanticDisambiguationRequest(
                    scope_id=scope_id.strip() or task_topic.strip(),
                    task_id=task_id,
                    subject=task_topic,
                    source_id=(
                        candidate.evidence_refs[0]
                        if candidate.evidence_refs
                        else "promotion_view"
                    ),
                    source_text=candidate.source_text,
                )
            )
            if disambiguation.accepted:
                candidate.canonical_claim_candidates = [
                    dict(item) for item in disambiguation.candidates
                ]
            else:
                disambiguation_reasons.append(
                    f"semantic_disambiguation_{disambiguation.status}"
                )
                disambiguation_reasons.extend(disambiguation.reasons)
        validation = self.validator.validate(candidate)
        canonical_reasons: list[str] = []
        canonical_valid_count = 0
        dynamic_registration_count = 0
        unresolved_schema_count = 0
        explicit_reasons: list[str] = []
        explicit_claim_count = len(candidate.claim_cards)
        resolved_claim_cards: list[dict[str, Any]] = []
        for explicit_claim in candidate.claim_cards:
            validated_claim, claim_reasons = (
                self._validate_explicit_claim_card(
                    explicit_claim,
                    source_text=candidate.source_text,
                    default_source_id=(
                        candidate.evidence_refs[0]
                        if candidate.evidence_refs
                        else "promotion_view"
                    ),
                )
            )
            if validated_claim is None:
                explicit_reasons.extend(claim_reasons)
                continue
            resolved_claim_cards.append(validated_claim)
        explicit_valid_count = len(resolved_claim_cards)
        for canonical_candidate in candidate.canonical_claim_candidates:
            claim_validation = self.canonical_validator.validate(
                canonical_candidate,
                source_text=candidate.source_text,
            )
            if not claim_validation.allowed:
                canonical_reasons.extend(claim_validation.reasons)
                continue
            canonical_valid_count += 1
            predicate = str(canonical_candidate.get("predicate") or "")
            resolved = self.memory_store.schema_registry.resolve(predicate)
            if resolved.unresolved and self.allow_dynamic_schema:
                resolved = (
                    self.memory_store.schema_registry.register_dynamic_predicate(
                        predicate
                    )
                )
                dynamic_registration_count += int(
                    resolved.layer == "dynamic"
                )
            if resolved.unresolved:
                unresolved_schema_count += 1
                canonical_reasons.append("open_predicate_unresolved")
                continue
            resolved_claim_cards.append(
                self._claim_card_from_canonical_candidate(
                    canonical_candidate,
                    slot_id=resolved.slot_id,
                    schema_layer=resolved.layer,
                )
            )
        candidate.claim_cards = resolved_claim_cards
        validation = SemanticValidationResult(
            allowed=(
                validation.allowed
                and not explicit_reasons
                and not canonical_reasons
                and not disambiguation_reasons
            ),
            reasons=list(
                dict.fromkeys(
                    [
                        *validation.reasons,
                        *explicit_reasons,
                        *canonical_reasons,
                        *disambiguation_reasons,
                    ]
                )
            ),
            explicit_claim_count=explicit_claim_count,
            explicit_claim_valid_count=explicit_valid_count,
            canonical_candidate_count=len(
                candidate.canonical_claim_candidates
            ),
            canonical_candidate_valid_count=canonical_valid_count,
            dynamic_schema_registration_count=dynamic_registration_count,
            unresolved_schema_count=unresolved_schema_count,
            disambiguation_status=disambiguation.status,
            disambiguation_call_count=disambiguation.call_count,
            disambiguation_accepted_candidate_count=len(
                disambiguation.candidates
            ),
            disambiguation_rejected_candidate_count=(
                disambiguation.rejected_candidate_count
            ),
            disambiguation_locally_rebound_candidate_count=(
                disambiguation.locally_rebound_candidate_count
            ),
            disambiguation_diagnostics=list(disambiguation.reasons),
            control_prompt_tokens=disambiguation.prompt_tokens,
            control_completion_tokens=disambiguation.completion_tokens,
            control_total_tokens=disambiguation.total_tokens,
            control_usage_estimated=disambiguation.usage_estimated,
            control_retry_count=disambiguation.retry_count,
            control_model=disambiguation.model,
            control_latency_ms=disambiguation.latency_ms,
            disambiguation_policy=disambiguation_policy,
        )
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

    def _validate_explicit_claim_card(
        self,
        claim: Any,
        *,
        source_text: str,
        default_source_id: str,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        if not isinstance(claim, dict):
            return None, ["explicit_claim_not_mapping"]
        value = str(claim.get("value") or "").strip()
        predicate = str(
            claim.get("raw_slot_text")
            or claim.get("scope")
            or claim.get("slot_id")
            or ""
        ).strip()
        if not value:
            return None, ["explicit_claim_value_missing"]
        if not predicate:
            return None, ["explicit_claim_predicate_missing"]

        enriched = dict(claim)
        span_payload = enriched.get("source_span")
        if not isinstance(span_payload, dict):
            spans = self._exact_value_spans(source_text, value)
            if not spans:
                return None, ["explicit_claim_value_not_in_source"]
            if len(spans) > 1:
                return None, ["explicit_claim_value_span_ambiguous"]
            value_start, value_end = spans[0]
            line_start = source_text.rfind("\n", 0, value_start) + 1
            line_end = source_text.find("\n", value_end)
            if line_end < 0:
                line_end = len(source_text)
            span = SourceSpan.from_text(
                source_id=default_source_id,
                text=source_text,
                start=line_start,
                end=line_end,
            )
            span_payload = {
                "source_id": span.source_id,
                "start": span.start,
                "end": span.end,
                "text_hash": span.text_hash,
                "quote": span.quote,
            }
            enriched["source_span"] = span_payload

        surrogate = {
            "predicate": predicate,
            "value": value,
            "value_type": str(
                enriched.get("value_type") or "string"
            ),
            "unit": str(enriched.get("unit") or ""),
            "operator": str(enriched.get("operator") or "eq"),
            "temporal_status": str(
                enriched.get("temporal_status") or "unspecified"
            ),
            "relations": list(enriched.get("relations") or []),
            "source_span": span_payload,
        }
        result = self.canonical_validator.validate(
            surrogate,
            source_text=source_text,
        )
        if not result.allowed:
            return None, [
                f"explicit_claim_{reason}" for reason in result.reasons
            ]
        enriched["source_pointer"] = str(
            span_payload.get("source_id") or default_source_id
        )
        enriched["schema_version"] = str(
            enriched.get("schema_version") or "ccf.v3"
        )
        enriched.setdefault("assertion_type", "fact")
        enriched.setdefault("operator", "eq")
        enriched["polarity"] = canonical_claim_polarity(
            enriched["operator"]
        )
        enriched.setdefault("temporal_status", "unspecified")
        enriched.setdefault("relations", [])
        enriched.setdefault("schema_layer", "developer")
        return enriched, []

    @staticmethod
    def _exact_value_spans(
        source_text: str,
        value: str,
    ) -> list[tuple[int, int]]:
        if not source_text or not value:
            return []
        escaped = re.escape(value)
        prefix = r"(?<![\w])" if re.match(r"[\w]", value[0]) else ""
        suffix = r"(?![\w])" if re.match(r"[\w]", value[-1]) else ""
        pattern = f"{prefix}{escaped}{suffix}"
        return [
            (match.start(), match.end())
            for match in re.finditer(
                pattern,
                source_text,
                re.IGNORECASE,
            )
        ]

    @staticmethod
    def _claim_card_from_canonical_candidate(
        candidate: dict[str, Any],
        *,
        slot_id: str,
        schema_layer: str,
    ) -> dict[str, Any]:
        temporal_status = str(
            candidate.get("temporal_status") or "unspecified"
        )
        certainty = (
            "historical"
            if temporal_status == "historical"
            else "proposed"
            if temporal_status == "future"
            else "asserted"
        )
        relations = [
            relation
            for relation in candidate.get("relations") or []
            if isinstance(relation, dict)
        ]
        revision_kind = (
            "replaces"
            if any(
                relation.get("relation_type") == "supersedes_value"
                for relation in relations
            )
            else "asserted"
        )
        source_span = candidate.get("source_span")
        source_pointer = ""
        raw_text = ""
        if isinstance(source_span, dict):
            source_pointer = str(source_span.get("source_id") or "")
            raw_text = str(source_span.get("quote") or "")
        return {
            "subject": str(candidate.get("subject") or ""),
            "raw_slot_text": str(candidate.get("predicate") or ""),
            "slot_id": slot_id,
            "scope": "general",
            "value": str(candidate.get("value") or ""),
            "value_type": str(candidate.get("value_type") or "string"),
            "unit": str(candidate.get("unit") or ""),
            "raw_text": raw_text,
            "summary": raw_text,
            "certainty": certainty,
            "modality": str(candidate.get("modality") or "asserted"),
            "polarity": canonical_claim_polarity(
                candidate.get("operator") or "eq"
            ),
            "confidence": float(candidate.get("confidence") or 0.0),
            "revision_kind": revision_kind,
            "temporal_scope": "cross_task",
            "source_pointer": source_pointer,
            "assertion_type": str(
                candidate.get("assertion_type") or "fact"
            ),
            "operator": str(candidate.get("operator") or "eq"),
            "temporal_status": temporal_status,
            "source_span": source_span if isinstance(source_span, dict) else {},
            "relations": relations,
            "schema_layer": schema_layer,
            "schema_version": "ccf.v3-open",
        }
