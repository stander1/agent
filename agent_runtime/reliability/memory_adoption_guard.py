"""Rules-first enforcement for outputs that adopt superseded memory facts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


_STRUCTURED_ATTRIBUTION_MODE = "ccf_v2_semantic_key_value_rules"
_UNSAFE_STATUSES = frozenset({"wrong", "mixed"})
_SCALAR_TYPES = (str, int, float)


@dataclass(frozen=True, slots=True)
class MemoryAdoptionGuardDecision:
    status: str
    output_text: str
    conflict_count: int = 0
    repaired_fact_count: int = 0
    semantic_keys: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    active_facts: tuple[dict[str, Any], ...] = ()

    @property
    def changed(self) -> bool:
        return self.status in {"rule_repaired", "blocked"}

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "changed": self.changed,
            "blocked": self.blocked,
            "conflict_count": self.conflict_count,
            "repaired_fact_count": self.repaired_fact_count,
            "semantic_keys": list(self.semantic_keys),
            "reasons": list(self.reasons),
            "active_facts": [dict(item) for item in self.active_facts],
            "allowed_next_step": (
                "review_or_retry_only" if self.blocked else "continue"
            ),
            "safe_to_continue": not self.blocked,
        }


def guard_memory_adoption_output(
    *,
    output_text: str,
    evidence_rows: Iterable[Mapping[str, Any]],
) -> MemoryAdoptionGuardDecision:
    """Repair exact structured conflicts or replace the output with a safe alert."""

    unsafe_rows = [
        dict(row)
        for row in evidence_rows
        if str(row.get("status") or "") in _UNSAFE_STATUSES
    ]
    if not unsafe_rows:
        return MemoryAdoptionGuardDecision(status="safe", output_text=output_text)

    repaired = output_text
    repaired_count = 0
    reasons: list[str] = []
    active_facts: list[dict[str, Any]] = []
    semantic_keys: list[str] = []
    for row in unsafe_rows:
        semantic_key = str(row.get("semantic_key") or "").strip()
        active_value = row.get("active_value")
        if semantic_key:
            semantic_keys.append(semantic_key)
        if semantic_key and _is_scalar(active_value):
            active_facts.append(
                {
                    "semantic_key": semantic_key,
                    "active_value": active_value,
                }
            )

        if str(row.get("attribution_mode") or "") != _STRUCTURED_ATTRIBUTION_MODE:
            reasons.append("unstructured_conflict_requires_review")
            continue
        if not semantic_key:
            reasons.append("semantic_key_missing")
            continue
        if not _is_scalar(active_value):
            reasons.append("active_value_not_scalar")
            continue

        historical_values = [
            value
            for value in row.get("historical_values", [])
            if _is_scalar(value)
            and _normalized_scalar(value) != _normalized_scalar(active_value)
        ]
        matched_spans = [
            str(span)
            for span in row.get("matched_historical_output_spans", [])
            if str(span).strip()
        ]
        if not historical_values:
            reasons.append("historical_value_missing")
            continue
        if not matched_spans:
            reasons.append("historical_output_span_missing")
            continue

        row_repaired, replacement_count = _repair_matched_spans(
            text=repaired,
            spans=matched_spans,
            historical_values=historical_values,
            active_value=active_value,
        )
        if replacement_count <= 0:
            reasons.append("exact_historical_value_not_replaceable")
            continue
        repaired = row_repaired
        repaired_count += replacement_count

    if not reasons and repaired_count > 0:
        return MemoryAdoptionGuardDecision(
            status="rule_repaired",
            output_text=repaired,
            conflict_count=len(unsafe_rows),
            repaired_fact_count=repaired_count,
            semantic_keys=tuple(dict.fromkeys(semantic_keys)),
            active_facts=tuple(_deduplicate_active_facts(active_facts)),
        )

    unique_reasons = tuple(dict.fromkeys(reasons or ["repair_not_proven_safe"]))
    facts = tuple(_deduplicate_active_facts(active_facts))
    return MemoryAdoptionGuardDecision(
        status="blocked",
        output_text=_conflict_alert(
            active_facts=facts,
            reasons=unique_reasons,
            conflict_count=len(unsafe_rows),
            output_text=output_text,
        ),
        conflict_count=len(unsafe_rows),
        repaired_fact_count=repaired_count,
        semantic_keys=tuple(dict.fromkeys(semantic_keys)),
        reasons=unique_reasons,
        active_facts=facts,
    )


def _repair_matched_spans(
    *,
    text: str,
    spans: list[str],
    historical_values: list[Any],
    active_value: Any,
) -> tuple[str, int]:
    repaired = text
    replacement_count = 0
    for span in dict.fromkeys(spans):
        if span not in repaired:
            continue
        repaired_span = span
        span_replacements = 0
        for historical_value in historical_values:
            repaired_span, count = _replace_scalar(
                repaired_span,
                old_value=historical_value,
                new_value=active_value,
            )
            span_replacements += count
        if span_replacements <= 0 or repaired_span == span:
            continue
        repaired = repaired.replace(span, repaired_span)
        replacement_count += span_replacements
    return repaired, replacement_count


def _replace_scalar(
    text: str,
    *,
    old_value: Any,
    new_value: Any,
) -> tuple[str, int]:
    old_text = str(old_value).strip()
    new_text = str(new_value).strip()
    if not old_text or not new_text:
        return text, 0
    escaped = re.escape(old_text)
    if _is_numeric_scalar(old_value):
        pattern = re.compile(rf"(?<![0-9.]){escaped}(?![0-9]|\.[0-9])")
    else:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_.-]){escaped}(?![A-Za-z0-9_.-])",
            re.IGNORECASE,
        )
    return pattern.subn(new_text, text)


def _conflict_alert(
    *,
    active_facts: tuple[dict[str, Any], ...],
    reasons: tuple[str, ...],
    conflict_count: int,
    output_text: str,
) -> str:
    payload = {
        "protocol": "agentlite.memory_adoption_guard.v1",
        "message_type": "conflict_alert",
        "contract_status": "degraded_fallback",
        "action": "REFRESH_AND_REVIEW",
        "active_facts": [dict(item) for item in active_facts],
        "conflict_count": conflict_count,
        "reasons": list(reasons),
        "rejected_output_fingerprint": hashlib.sha256(
            output_text.encode("utf-8")
        ).hexdigest()[:16],
        "allowed_next_step": "review_or_retry_only",
        "safe_to_continue": False,
    }
    return (
        "AGENTLITE_MEMORY_CONFLICT v1\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def _is_scalar(value: Any) -> bool:
    return isinstance(value, _SCALAR_TYPES) and not isinstance(value, bool)


def _is_numeric_scalar(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    return bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", str(value).strip()))


def _normalized_scalar(value: Any) -> str:
    return str(value).strip().casefold()


def _deduplicate_active_facts(
    facts: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for fact in facts:
        key = (
            str(fact.get("semantic_key") or ""),
            _normalized_scalar(fact.get("active_value")),
        )
        unique[key] = dict(fact)
    return list(unique.values())
