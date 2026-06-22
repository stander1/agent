from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

from agent_runtime.state.state_pool import StateRef


@dataclass(slots=True)
class CapabilityProfile:
    agent_id: str
    role: str
    capabilities: set[str] = field(default_factory=set)
    accepted_state_types: set[str] = field(default_factory=set)
    message_types: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["capabilities"] = sorted(self.capabilities)
        payload["accepted_state_types"] = sorted(self.accepted_state_types)
        payload["message_types"] = sorted(self.message_types)
        return payload


@dataclass(slots=True)
class RouteDecision:
    receiver: str
    msg_type: str
    capability_hint: list[str]
    route_changed: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class CommunicationGateReport:
    allowed: bool
    status: str
    allowed_next_step: str
    reasons: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class ControlBudgetReport:
    allowed: bool
    task_id: str
    decision_count: int
    max_decisions_per_task: int
    estimated_control_tokens: int
    max_control_tokens_per_task: int
    reason: str = ""
    control_path: str = "rules_first"
    scoring_assist_allowed: bool = True
    llm_fallback_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CapabilityProfileManagerLite:
    """Rule-based capability profiles for the competition runtime."""

    DEFAULTS: dict[str, CapabilityProfile] = {
        "planner": CapabilityProfile(
            agent_id="planner",
            role="PlannerAgent",
            capabilities={"task_decomposition", "routing_seed"},
            accepted_state_types=set(),
            message_types={"agent_output", "route_decision"},
        ),
        "retriever": CapabilityProfile(
            agent_id="retriever",
            role="RetrieverAgent",
            capabilities={"retrieval", "embedding_generation", "evidence_ranking"},
            accepted_state_types={"artifact_state", "memory_ref_handoff"},
            message_types={"state_ref_handoff", "retrieval_state", "embedding_state"},
        ),
        "writer": CapabilityProfile(
            agent_id="writer",
            role="WriterAgent",
            capabilities={"synthesis", "final_deliverable_draft", "memory_use"},
            accepted_state_types={
                "retrieval_state",
                "embedding_state",
                "artifact_state",
                "retry_loop_summary",
            },
            message_types={"state_ref_handoff", "memory_ref_handoff", "artifact_state"},
        ),
        "reviewer": CapabilityProfile(
            agent_id="reviewer",
            role="ReviewerAgent",
            capabilities={
                "validation",
                "schema_review",
                "failure_review",
                "final_deliverable_review",
            },
            accepted_state_types={
                "artifact_state",
                "failure_state",
                "retry_loop_summary",
                "final_deliverable",
            },
            message_types={
                "agent_output",
                "artifact_state",
                "failure_state",
                "readiness_report",
            },
        ),
        "memory_manager": CapabilityProfile(
            agent_id="memory_manager",
            role="MemoryManagerAgent",
            capabilities={"memory_governance", "claim_compaction"},
            accepted_state_types={"artifact_state", "retrieval_state"},
            message_types={"memory_promotion", "memory_ref_handoff"},
        ),
    }

    def __init__(self, agents: Iterable[object]) -> None:
        self._profiles = dict(self.DEFAULTS)
        for agent in agents:
            agent_id = str(getattr(agent, "agent_id", ""))
            if not agent_id or agent_id in self._profiles:
                continue
            role = str(getattr(agent, "role", agent_id))
            self._profiles[agent_id] = CapabilityProfile(
                agent_id=agent_id,
                role=role,
                capabilities={role.lower()},
                accepted_state_types={"artifact_state"},
                message_types={"agent_output"},
            )

    def get(self, agent_id: str) -> CapabilityProfile | None:
        return self._profiles.get(agent_id)

    def capability_hint_for(self, agent_id: str) -> list[str]:
        profile = self.get(agent_id)
        if profile is None:
            return []
        return sorted(profile.capabilities)

    def can_accept(self, agent_id: str, state_type: str) -> bool:
        profile = self.get(agent_id)
        if profile is None:
            return False
        return state_type in profile.accepted_state_types

    def snapshot(self) -> dict[str, object]:
        return {agent_id: profile.to_dict() for agent_id, profile in self._profiles.items()}


class CapabilityRouterLite:
    def __init__(self, profiles: CapabilityProfileManagerLite) -> None:
        self.profiles = profiles

    def route(
        self,
        *,
        sender: str,
        declared_receiver: str,
        state_refs: list[StateRef],
        readiness: str,
    ) -> RouteDecision:
        state_types = {ref.state_type for ref in state_refs}
        reasons: list[str] = []
        receiver = declared_receiver
        msg_type = self._message_type_for(state_types)

        if readiness == "blocked":
            receiver = "runtime"
            msg_type = "readiness_report"
            reasons.append("readiness_blocked")
        elif "failure_state" in state_types:
            receiver = "reviewer" if self.profiles.get("reviewer") else declared_receiver
            msg_type = "failure_state"
            reasons.append("failure_state_requires_review")
        elif sender == "retriever" and (
            "retrieval_state" in state_types or "embedding_state" in state_types
        ):
            receiver = "writer" if self.profiles.can_accept("writer", "retrieval_state") else declared_receiver
            msg_type = "state_ref_handoff"
            reasons.append("retrieval_state_targets_writer")
        elif sender == "writer" and "artifact_state" in state_types:
            receiver = "reviewer" if self.profiles.can_accept("reviewer", "artifact_state") else declared_receiver
            msg_type = "artifact_state"
            reasons.append("artifact_state_targets_reviewer")
        elif sender == "reviewer":
            receiver = "runtime"
            msg_type = "final_deliverable"
            reasons.append("reviewer_handoff_finishes_runtime")
        elif receiver == "runtime":
            reasons.append("declared_receiver_runtime")
        else:
            reasons.append("declared_linear_route")

        return RouteDecision(
            receiver=receiver,
            msg_type=msg_type,
            capability_hint=self.profiles.capability_hint_for(receiver),
            route_changed=receiver != declared_receiver,
            reasons=reasons,
        )

    def _message_type_for(self, state_types: set[str]) -> str:
        if "failure_state" in state_types:
            return "failure_state"
        if "retrieval_state" in state_types or "embedding_state" in state_types:
            return "state_ref_handoff"
        if "artifact_state" in state_types:
            return "artifact_state"
        return "agent_output"

    def resolve_tie(
        self,
        candidates: list[str],
        *,
        required_state_type: str | None = None,
        preferred_capability: str | None = None,
    ) -> RouteDecision:
        scored: list[tuple[int, str, list[str]]] = []
        for agent_id in candidates:
            profile = self.profiles.get(agent_id)
            if profile is None:
                scored.append((0, agent_id, ["missing_profile"]))
                continue
            score = 0
            reasons: list[str] = []
            if required_state_type and required_state_type in profile.accepted_state_types:
                score += 3
                reasons.append("state_type_match")
            if preferred_capability and preferred_capability in profile.capabilities:
                score += 2
                reasons.append("capability_match")
            score += min(1, len(profile.message_types))
            if not reasons:
                reasons.append("stable_agent_id_tiebreak")
            scored.append((score, agent_id, reasons))
        scored.sort(key=lambda item: (-item[0], item[1]))
        _, receiver, reasons = scored[0]
        return RouteDecision(
            receiver=receiver,
            msg_type=(
                "state_ref_handoff"
                if required_state_type in {"retrieval_state", "embedding_state"}
                else "agent_output"
            ),
            capability_hint=self.profiles.capability_hint_for(receiver),
            route_changed=True,
            reasons=["cold_start_tie_resolved", *reasons],
        )


class CommunicationGateLite:
    def assess(
        self,
        *,
        readiness: str,
        route_decision: RouteDecision,
        budget_report: ControlBudgetReport,
    ) -> CommunicationGateReport:
        reasons = list(route_decision.reasons)
        if not budget_report.allowed:
            reasons.append(budget_report.reason or "control_budget_exhausted")
            return CommunicationGateReport(
                allowed=False,
                status="blocked",
                allowed_next_step="control_budget_review",
                reasons=reasons,
            )
        if readiness == "blocked":
            reasons.append("missing_required_state")
            return CommunicationGateReport(
                allowed=False,
                status="blocked",
                allowed_next_step="produce_state_before_handoff",
                reasons=reasons,
            )
        if readiness == "degraded":
            reasons.append("degraded_handoff")
            return CommunicationGateReport(
                allowed=True,
                status="degraded",
                allowed_next_step="review_or_retry_only",
                reasons=reasons,
            )
        return CommunicationGateReport(
            allowed=True,
            status="ready",
            allowed_next_step="continue",
            reasons=reasons,
        )


class ControlBudgetLite:
    def __init__(
        self,
        *,
        max_decisions_per_task: int = 64,
        max_control_tokens_per_task: int = 4096,
    ) -> None:
        self.max_decisions_per_task = max_decisions_per_task
        self.max_control_tokens_per_task = max_control_tokens_per_task
        self._decision_counts: dict[str, int] = {}
        self._token_counts: dict[str, int] = {}

    def record_decision(
        self,
        *,
        task_id: str,
        estimated_control_tokens: int = 0,
        scoring_confidence: float = 1.0,
        requires_llm_fallback: bool = False,
    ) -> ControlBudgetReport:
        decision_count = self._decision_counts.get(task_id, 0) + 1
        token_count = self._token_counts.get(task_id, 0) + max(0, estimated_control_tokens)
        self._decision_counts[task_id] = decision_count
        self._token_counts[task_id] = token_count
        allowed = (
            decision_count <= self.max_decisions_per_task
            and token_count <= self.max_control_tokens_per_task
        )
        reason = "" if allowed else "control_budget_exhausted"
        if not allowed:
            control_path = "blocked"
        elif requires_llm_fallback:
            control_path = "llm_fallback"
        elif scoring_confidence < 0.65:
            control_path = "scoring_assist"
        else:
            control_path = "rules_first"
        return ControlBudgetReport(
            allowed=allowed,
            task_id=task_id,
            decision_count=decision_count,
            max_decisions_per_task=self.max_decisions_per_task,
            estimated_control_tokens=token_count,
            max_control_tokens_per_task=self.max_control_tokens_per_task,
            reason=reason,
            control_path=control_path,
            scoring_assist_allowed=allowed and scoring_confidence < 0.65,
            llm_fallback_allowed=allowed and requires_llm_fallback,
        )

    def finalize_task(self, task_id: str) -> None:
        self._decision_counts.pop(task_id, None)
        self._token_counts.pop(task_id, None)
