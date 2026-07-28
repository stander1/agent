from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class ClaimConflictResolution:
    status: str
    active_claim_ids: tuple[str, ...] = ()
    superseded_claim_ids: tuple[str, ...] = ()
    conflicting_claim_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Claim:
    claim_id: str
    candidate_id: str
    value: str
    value_type: str
    unit: str
    operator: str
    polarity: str
    temporal_status: str
    relations: tuple[dict[str, str], ...]

    @property
    def value_identity(self) -> tuple[str, str, str, str]:
        return (
            _normalized_value(self.value, self.value_type),
            self.unit.casefold(),
            self.operator,
            self.polarity,
        )


def resolve_claim_conflicts(
    claims: Iterable[Any],
) -> ClaimConflictResolution:
    """Resolve evidence-validated claims without domain or recency heuristics."""

    rows = tuple(_coerce_claim(item) for item in claims)
    if not rows:
        return ClaimConflictResolution(status="empty")
    if any(not row.claim_id for row in rows):
        return ClaimConflictResolution(
            status="invalid",
            conflicting_claim_ids=tuple(
                row.claim_id for row in rows if row.claim_id
            ),
            reasons=("claim_id_missing",),
        )

    deferred = {
        row.claim_id
        for row in rows
        if row.temporal_status in {"historical", "future"}
    }
    eligible = [row for row in rows if row.claim_id not in deferred]
    if not eligible:
        return ClaimConflictResolution(
            status="deferred",
            superseded_claim_ids=tuple(sorted(deferred)),
            reasons=("no_current_claims",),
        )

    superseded, relation_reasons = _explicit_supersession(eligible)
    remaining = [row for row in eligible if row.claim_id not in superseded]
    if not remaining:
        return ClaimConflictResolution(
            status="unresolved",
            superseded_claim_ids=tuple(sorted(superseded | deferred)),
            conflicting_claim_ids=tuple(
                sorted(row.claim_id for row in eligible)
            ),
            reasons=tuple(
                dict.fromkeys(
                    [*relation_reasons, "supersession_removed_all_claims"]
                )
            ),
        )

    value_groups: dict[tuple[str, str, str, str], list[_Claim]] = {}
    for row in remaining:
        value_groups.setdefault(row.value_identity, []).append(row)
    if len(value_groups) == 1:
        return ClaimConflictResolution(
            status="resolved",
            active_claim_ids=tuple(row.claim_id for row in remaining),
            superseded_claim_ids=tuple(sorted(superseded | deferred)),
            reasons=tuple(
                dict.fromkeys(
                    [*relation_reasons, "equivalent_claims_merged"]
                )
            ),
        )

    compatible, compatibility_reason = _compatible_constraints(remaining)
    if compatible:
        return ClaimConflictResolution(
            status="resolved",
            active_claim_ids=tuple(row.claim_id for row in remaining),
            superseded_claim_ids=tuple(sorted(superseded | deferred)),
            reasons=tuple(
                dict.fromkeys(
                    [*relation_reasons, compatibility_reason]
                )
            ),
        )

    return ClaimConflictResolution(
        status="unresolved",
        superseded_claim_ids=tuple(sorted(superseded | deferred)),
        conflicting_claim_ids=tuple(row.claim_id for row in remaining),
        reasons=tuple(
            dict.fromkeys(
                [
                    *relation_reasons,
                    compatibility_reason or "incompatible_active_assertions",
                ]
            )
        ),
    )


def _explicit_supersession(
    claims: list[_Claim],
) -> tuple[set[str], list[str]]:
    by_reference: dict[str, _Claim] = {}
    for claim in claims:
        by_reference[claim.claim_id] = claim
        if claim.candidate_id:
            by_reference[claim.candidate_id] = claim

    superseded: set[str] = set()
    reasons: list[str] = []
    for claim in claims:
        for relation in claim.relations:
            relation_type = str(
                relation.get("relation_type") or ""
            ).strip().lower()
            if relation_type not in {
                "supersedes_value",
                "supersedes_claim",
                "supersedes_candidate",
            }:
                continue
            target_reference = str(
                relation.get("target_candidate_id")
                or relation.get("target_claim_id")
                or ""
            ).strip()
            matched: list[_Claim] = []
            if target_reference:
                target = by_reference.get(target_reference)
                if target is not None:
                    matched.append(target)
            target_value = str(
                relation.get("target_value") or ""
            ).strip()
            if target_value:
                normalized_target = _normalized_value(
                    target_value,
                    claim.value_type,
                )
                matched.extend(
                    item
                    for item in claims
                    if _normalized_value(item.value, item.value_type)
                    == normalized_target
                    and item.unit.casefold() == claim.unit.casefold()
                )
            unique_targets = {
                item.claim_id
                for item in matched
                if item.claim_id != claim.claim_id
            }
            if not unique_targets:
                reasons.append("supersession_target_not_found")
                continue
            superseded.update(unique_targets)
            reasons.append("explicit_supersession_applied")

    active_ids = {claim.claim_id for claim in claims} - superseded
    if not active_ids:
        reasons.append("supersession_cycle_or_exhaustion")
    return superseded, reasons


def _compatible_constraints(claims: list[_Claim]) -> tuple[bool, str]:
    if any(
        claim.polarity != "positive"
        and not (
            claim.polarity == "negative"
            and claim.operator == "ne"
        )
        for claim in claims
    ):
        return False, "polarity_conflict"
    if any(claim.value_type != "number" for claim in claims):
        return False, "non_numeric_assertions_require_explicit_resolution"
    units = {claim.unit.casefold() for claim in claims}
    if len(units) != 1:
        return False, "unit_mismatch"

    parsed: list[tuple[_Claim, Decimal]] = []
    for claim in claims:
        try:
            parsed.append(
                (
                    claim,
                    Decimal(
                        _normalized_value(claim.value, claim.value_type)
                    ),
                )
            )
        except InvalidOperation:
            return False, "numeric_value_invalid"

    equality_values = {
        value for claim, value in parsed if claim.operator == "eq"
    }
    if len(equality_values) > 1:
        return False, "multiple_equality_values"
    if any(
        claim.operator not in {"eq", "ne", "lt", "le", "gt", "ge"}
        for claim, _ in parsed
    ):
        return False, "operator_unsupported"

    lower: tuple[Decimal, bool] | None = None
    upper: tuple[Decimal, bool] | None = None
    excluded = {value for claim, value in parsed if claim.operator == "ne"}
    for claim, value in parsed:
        if claim.operator in {"gt", "ge"}:
            inclusive = claim.operator == "ge"
            if lower is None or value > lower[0]:
                lower = (value, inclusive)
            elif value == lower[0]:
                lower = (value, lower[1] and inclusive)
        elif claim.operator in {"lt", "le"}:
            inclusive = claim.operator == "le"
            if upper is None or value < upper[0]:
                upper = (value, inclusive)
            elif value == upper[0]:
                upper = (value, upper[1] and inclusive)

    if equality_values:
        value = next(iter(equality_values))
        if value in excluded:
            return False, "equality_excluded"
        if lower is not None and (
            value < lower[0] or (value == lower[0] and not lower[1])
        ):
            return False, "equality_below_lower_bound"
        if upper is not None and (
            value > upper[0] or (value == upper[0] and not upper[1])
        ):
            return False, "equality_above_upper_bound"
        return True, "compatible_numeric_constraints"

    if lower is not None and upper is not None:
        if lower[0] > upper[0]:
            return False, "constraint_interval_empty"
        if lower[0] == upper[0]:
            if not (lower[1] and upper[1]):
                return False, "constraint_interval_empty"
            if lower[0] in excluded:
                return False, "constraint_interval_excluded"
    return True, "compatible_numeric_constraints"


def _coerce_claim(value: Any) -> _Claim:
    def field(name: str, default: Any = "") -> Any:
        if isinstance(value, Mapping):
            return value.get(name, default)
        return getattr(value, name, default)

    relations = field("relations", ())
    return _Claim(
        claim_id=str(field("claim_id") or "").strip(),
        candidate_id=str(field("candidate_id") or "").strip(),
        value=str(field("value") or "").strip(),
        value_type=str(field("value_type") or "string").strip().lower(),
        unit=str(field("unit") or "").strip(),
        operator=str(field("operator") or "eq").strip().lower(),
        polarity=str(field("polarity") or "positive").strip().lower(),
        temporal_status=str(
            field("temporal_status") or "unspecified"
        ).strip().lower(),
        relations=tuple(
            {
                str(key): str(item_value)
                for key, item_value in item.items()
            }
            for item in (relations if isinstance(relations, (list, tuple)) else ())
            if isinstance(item, Mapping)
        ),
    )


def _normalized_value(value: str, value_type: str) -> str:
    text = str(value).strip()
    if value_type == "number":
        text = text.replace(",", "")
        try:
            decimal = Decimal(text)
        except InvalidOperation:
            return text.casefold()
        return format(decimal.normalize(), "f")
    return " ".join(text.casefold().split())
