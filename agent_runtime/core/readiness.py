from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ReadinessReport:
    readiness: str
    allowed_next_step: str
    reasons: list[str] = field(default_factory=list)


class ReadinessBarrierLite:
    """Rules-first information readiness barrier for SHP handoff."""

    def assess(
        self,
        *,
        state_refs: list[Any],
        required_state_types: set[str] | None = None,
        degraded: bool = False,
    ) -> ReadinessReport:
        if degraded:
            return ReadinessReport(
                readiness="degraded",
                allowed_next_step="review_or_retry_only",
                reasons=["degraded_contract_or_state"],
            )
        if not state_refs:
            return ReadinessReport(
                readiness="blocked",
                allowed_next_step="produce_state_before_handoff",
                reasons=["missing_state_refs"],
            )
        required = required_state_types or set()
        present = {getattr(ref, "state_type", "") for ref in state_refs}
        missing = sorted(required - present)
        if missing:
            return ReadinessReport(
                readiness="blocked",
                allowed_next_step="produce_required_state",
                reasons=[f"missing_state_type:{item}" for item in missing],
            )
        return ReadinessReport(readiness="ready", allowed_next_step="continue")
