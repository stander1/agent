from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegistrySlotResolution:
    slot_id: str
    alias_hit: bool = False
    unresolved: bool = False


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
    """Static v5.3 semantic schema registry.

    The registry keeps slot normalization, alias mapping, conflict policy, and
    Canonical Claim Lite generation in one place. It is intentionally rules-only
    so memory admission does not add LLM control cost.
    """

    def __init__(
        self,
        canonical_slots: set[str],
        alias_mapping: dict[str, str],
        slot_policies: dict[str, SlotPolicy] | None = None,
    ) -> None:
        self.canonical_slots = set(canonical_slots)
        self.alias_mapping = dict(alias_mapping)
        self.slot_policies = slot_policies or {
            slot_id: SlotPolicy(slot_id=slot_id) for slot_id in canonical_slots
        }

    def resolve(self, slot_hint: str) -> RegistrySlotResolution:
        normalized = self.normalize(slot_hint)
        if normalized in self.canonical_slots:
            return RegistrySlotResolution(slot_id=normalized)
        if normalized in self.alias_mapping:
            return RegistrySlotResolution(
                slot_id=self.alias_mapping[normalized],
                alias_hit=True,
            )
        for slot in self.canonical_slots:
            if normalized == self.normalize(slot.split(".")[-1]):
                return RegistrySlotResolution(slot_id=slot)
        return RegistrySlotResolution(slot_id="unresolved_slot", unresolved=True)

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

    @classmethod
    def normalize_scope(cls, text: str) -> str:
        normalized = cls.normalize(text)
        return normalized or "general"
