from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """Exact evidence location for a claim candidate."""

    source_id: str
    start: int
    end: int
    text_hash: str
    quote: str

    @classmethod
    def from_text(
        cls,
        *,
        source_id: str,
        text: str,
        start: int,
        end: int,
    ) -> SourceSpan:
        if start < 0 or end < start or end > len(text):
            raise ValueError("source span is outside the source text")
        quote = text[start:end]
        return cls(
            source_id=source_id,
            start=start,
            end=end,
            text_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
            quote=quote,
        )

    def matches(self, text: str) -> bool:
        if self.start < 0 or self.end < self.start or self.end > len(text):
            return False
        quote = text[self.start : self.end]
        return (
            quote == self.quote
            and hashlib.sha256(quote.encode("utf-8")).hexdigest()
            == self.text_hash
        )


@dataclass(frozen=True, slots=True)
class ClaimRelation:
    relation_type: str
    target_value: str = ""
    target_candidate_id: str = ""


@dataclass(frozen=True, slots=True)
class CanonicalClaimCandidate:
    """Open, evidence-bound assertion before schema admission."""

    candidate_id: str
    subject: str
    predicate: str
    assertion_type: str
    operator: str
    value: str
    value_type: str
    unit: str
    polarity: str
    modality: str
    temporal_status: str
    confidence: float
    source_span: SourceSpan
    relations: tuple[ClaimRelation, ...] = ()
    schema_status: str = "unresolved"
    schema_version: str = "canonical-claim-candidate.v1"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["relations"] = [
            asdict(relation) for relation in self.relations
        ]
        return payload


@dataclass(frozen=True, slots=True)
class RegistrySlotResolution:
    slot_id: str
    alias_hit: bool = False
    unresolved: bool = False
    layer: str = "unresolved"
    normalized_predicate: str = ""


@dataclass(frozen=True, slots=True)
class SlotPolicy:
    slot_id: str
    conflict_policy: str = "highest_confidence_then_latest"
    scope_required: bool = False
    temporal_required: bool = False
    prompt_rendering: str = "summary_with_evidence"
    value_type: str = "string"
    allowed_modalities: tuple[str, ...] = ()
    prompt_visible: bool = True


@dataclass(frozen=True, slots=True)
class CanonicalClaimLite:
    subject: str
    slot_id: str
    value: str
    scope: str
    claim_type: str
    polarity: str
    modality: str
    temporal_scope: str
    certainty: str
    source_agent: str
    confidence: float
    value_type: str = "string"
    unit: str = ""
    valid_from: str = ""
    valid_to: str | None = None
    schema_version: str = "ccf.v1-lite"


class SchemaRegistryLite:
    """Layered schema registry for core, developer, and dynamic predicates."""

    def __init__(
        self,
        canonical_slots: set[str],
        alias_mapping: dict[str, str],
        slot_policies: dict[str, SlotPolicy] | None = None,
        developer_slots: set[str] | None = None,
    ) -> None:
        self.canonical_slots = set(canonical_slots)
        self.developer_slots = set(developer_slots or set())
        self.dynamic_slots: set[str] = set()
        self.alias_mapping = {
            self.normalize(alias): slot_id
            for alias, slot_id in alias_mapping.items()
        }
        self.open_predicate_mapping: dict[str, str] = {}
        self.slot_policies = slot_policies or {
            slot_id: SlotPolicy(slot_id=slot_id) for slot_id in canonical_slots
        }

    def resolve(self, slot_hint: str) -> RegistrySlotResolution:
        normalized = self.normalize(slot_hint)
        if normalized in self.canonical_slots:
            return RegistrySlotResolution(
                slot_id=normalized,
                layer="core",
                normalized_predicate=normalized,
            )
        if normalized in self.developer_slots:
            return RegistrySlotResolution(
                slot_id=normalized,
                layer="developer",
                normalized_predicate=normalized,
            )
        if normalized in self.dynamic_slots:
            return RegistrySlotResolution(
                slot_id=normalized,
                layer="dynamic",
                normalized_predicate=normalized,
            )
        if normalized in self.alias_mapping:
            slot_id = self.alias_mapping[normalized]
            return RegistrySlotResolution(
                slot_id=slot_id,
                alias_hit=True,
                layer=self._layer_for_slot(slot_id),
                normalized_predicate=normalized,
            )
        open_predicate = self.normalize_open_predicate(slot_hint)
        if open_predicate in self.open_predicate_mapping:
            slot_id = self.open_predicate_mapping[open_predicate]
            return RegistrySlotResolution(
                slot_id=slot_id,
                alias_hit=True,
                layer=self._layer_for_slot(slot_id),
                normalized_predicate=open_predicate,
            )
        for slot in (
            self.canonical_slots | self.developer_slots | self.dynamic_slots
        ):
            if normalized == self.normalize(slot.split(".")[-1]):
                return RegistrySlotResolution(
                    slot_id=slot,
                    layer=self._layer_for_slot(slot),
                    normalized_predicate=normalized,
                )
        return RegistrySlotResolution(
            slot_id="unresolved_slot",
            unresolved=True,
            normalized_predicate=open_predicate,
        )

    def register_developer_slot(
        self,
        *,
        predicate: str,
        slot_id: str,
        policy: SlotPolicy | None = None,
    ) -> RegistrySlotResolution:
        normalized_slot = self.normalize(slot_id)
        if not normalized_slot or not normalized_slot.startswith("slot."):
            raise ValueError("developer slot_id must start with 'slot.'")
        open_predicate = self.normalize_open_predicate(predicate)
        if not open_predicate:
            raise ValueError("developer predicate is empty after normalization")
        self.developer_slots.add(normalized_slot)
        self.open_predicate_mapping[open_predicate] = normalized_slot
        self.slot_policies[normalized_slot] = policy or SlotPolicy(
            slot_id=normalized_slot,
            conflict_policy="latest_explicit_state_wins",
            temporal_required=True,
        )
        return RegistrySlotResolution(
            slot_id=normalized_slot,
            alias_hit=True,
            layer="developer",
            normalized_predicate=open_predicate,
        )

    def register_dynamic_predicate(
        self,
        predicate: str,
        *,
        policy: SlotPolicy | None = None,
    ) -> RegistrySlotResolution:
        existing = self.resolve(predicate)
        if not existing.unresolved:
            return existing
        open_predicate = self.normalize_open_predicate(predicate)
        if not open_predicate:
            raise ValueError("dynamic predicate is empty after normalization")
        ascii_slug = self.normalize(predicate)
        if not ascii_slug:
            ascii_slug = "predicate"
        predicate_hash = hashlib.sha256(
            open_predicate.encode("utf-8")
        ).hexdigest()[:12]
        slot_id = f"slot.open.{ascii_slug[:48]}_{predicate_hash}"
        self.dynamic_slots.add(slot_id)
        self.open_predicate_mapping[open_predicate] = slot_id
        self.slot_policies[slot_id] = policy or SlotPolicy(
            slot_id=slot_id,
            conflict_policy="latest_explicit_state_wins",
            temporal_required=True,
        )
        return RegistrySlotResolution(
            slot_id=slot_id,
            alias_hit=True,
            layer="dynamic",
            normalized_predicate=open_predicate,
        )

    def policy_for(self, slot_id: str) -> SlotPolicy:
        return self.slot_policies.get(slot_id, SlotPolicy(slot_id=slot_id))

    def canonicalize_claim(
        self,
        *,
        subject: str,
        slot_id: str,
        value: object,
        source_agent: str,
        confidence: float,
        scope: str = "general",
        claim_type: str = "fact",
        polarity: str = "positive",
        modality: str = "asserted",
        temporal_scope: str = "current_task",
        certainty: str = "asserted",
        value_type: str | None = None,
        unit: str = "",
        valid_from: str = "",
        valid_to: str | None = None,
        schema_version: str = "ccf.v1-lite",
    ) -> CanonicalClaimLite:
        policy = self.policy_for(slot_id)
        return CanonicalClaimLite(
            subject=subject,
            slot_id=slot_id,
            value=str(value),
            scope=self.normalize_scope(scope),
            claim_type=claim_type,
            polarity=polarity,
            modality=modality,
            temporal_scope=temporal_scope,
            certainty=certainty,
            source_agent=source_agent,
            confidence=confidence,
            value_type=value_type or policy.value_type,
            unit=unit,
            valid_from=valid_from,
            valid_to=valid_to,
            schema_version=schema_version,
        )

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"[^a-z0-9_.]+", "_", text.lower()).strip("_")

    @staticmethod
    def normalize_open_predicate(text: str) -> str:
        encoded: list[str] = []
        for character in str(text or "").strip().casefold():
            if character.isalnum() or character in {"_", ".", "-"}:
                encoded.append(character)
            elif character.isspace():
                encoded.append("_")
            else:
                encoded.append(f"_u{ord(character):x}_")
        return re.sub(r"_+", "_", "".join(encoded)).strip("_.-")

    def _layer_for_slot(self, slot_id: str) -> str:
        if slot_id in self.dynamic_slots:
            return "dynamic"
        if slot_id in self.developer_slots:
            return "developer"
        if slot_id in self.canonical_slots:
            return "core"
        return "unresolved"

    @classmethod
    def normalize_scope(cls, text: str) -> str:
        normalized = re.sub(
            r"[^\w\u3400-\u4dbf\u4e00-\u9fff.]+",
            "_",
            str(text or "").casefold(),
        ).strip("_")
        aliases = {
            "constraint.budget": "constraint.budget_upper_bound",
        }
        normalized = aliases.get(normalized, normalized)
        return normalized or "general"
