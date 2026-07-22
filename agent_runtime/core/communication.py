from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from agent_runtime.state.state_pool import StateRef


@dataclass(slots=True)
class CapabilityProfile:
    agent_id: str
    role: str
    profile_version: int = 1
    role_capabilities: dict[str, float] = field(default_factory=dict)
    tool_capabilities: dict[str, float] = field(default_factory=dict)
    tool_capability_sources: dict[str, set[str]] = field(default_factory=dict)
    tool_cost_levels: dict[str, float] = field(default_factory=dict)
    runtime_capabilities: dict[str, float] = field(default_factory=dict)
    declared_preferred_actions: set[str] = field(default_factory=set)
    declared_input_preference: set[str] = field(default_factory=set)
    declared_output_types: set[str] = field(default_factory=set)
    declared_accepted_state_types: set[str] = field(default_factory=set)
    declared_message_types: set[str] = field(default_factory=set)
    tool_supported_actions: set[str] = field(default_factory=set)
    runtime_preferred_actions: set[str] = field(default_factory=set)
    preferred_actions: set[str] = field(default_factory=set)
    input_preference: set[str] = field(default_factory=set)
    output_types: set[str] = field(default_factory=set)
    available_tools: set[str] = field(default_factory=set)
    accepted_state_types: set[str] = field(default_factory=set)
    message_types: set[str] = field(default_factory=set)
    schema_success_count: int = 0
    schema_failure_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_cost_tokens: int = 0
    total_latency_ms: float = 0.0
    current_load: int = 0
    memory_locality_keys: set[str] = field(default_factory=set)
    profile_fingerprint: str = ""
    updated_at: str = ""

    @property
    def capabilities(self) -> set[str]:
        return {
            *self.role_capabilities,
            *self.tool_capabilities,
            *self.runtime_capabilities,
        }

    @property
    def history_success(self) -> float:
        return (self.success_count + 1) / (
            self.success_count + self.failure_count + 2
        )

    @property
    def schema_reliability(self) -> float:
        return (self.schema_success_count + 1) / (
            self.schema_success_count + self.schema_failure_count + 2
        )

    @property
    def average_cost_tokens(self) -> float:
        calls = self.success_count + self.failure_count
        return self.total_cost_tokens / calls if calls else 0.0

    @property
    def average_latency_ms(self) -> float:
        calls = self.success_count + self.failure_count
        return self.total_latency_ms / calls if calls else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "profile_version": self.profile_version,
            "role_capabilities": _capability_rows(
                self.role_capabilities,
                source="role",
            ),
            "tool_capabilities": _tool_capability_rows(self),
            "runtime_capabilities": _capability_rows(
                self.runtime_capabilities,
                source="runtime",
            ),
            "capabilities": sorted(self.capabilities),
            "preferred_actions": sorted(self.preferred_actions),
            "input_preference": sorted(self.input_preference),
            "output_types": sorted(self.output_types),
            "output_type": sorted(self.output_types),
            "available_tools": sorted(self.available_tools),
            "tool_cost_levels": {
                key: round(value, 4)
                for key, value in sorted(self.tool_cost_levels.items())
            },
            "accepted_state_types": sorted(self.accepted_state_types),
            "message_types": sorted(self.message_types),
            "schema_reliability": round(self.schema_reliability, 6),
            "current_load": self.current_load,
            "memory_locality_count": len(self.memory_locality_keys),
            "history": {
                "success_count": self.success_count,
                "failure_count": self.failure_count,
                "history_success_prior": round(self.history_success, 6),
                "schema_success_count": self.schema_success_count,
                "schema_failure_count": self.schema_failure_count,
                "average_cost_tokens": round(self.average_cost_tokens, 3),
                "average_latency_ms": round(self.average_latency_ms, 3),
            },
            "profile_fingerprint": self.profile_fingerprint,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class ToolCapabilityDescriptor:
    tool_id: str
    description: str = ""
    capability_tags: tuple[str, ...] = ()
    supported_actions: tuple[str, ...] = ()
    cost_level: float = 0.0


@dataclass(slots=True)
class RouteDecision:
    receiver: str
    msg_type: str
    capability_hint: list[str]
    route_changed: bool
    reasons: list[str]
    required_action: str = ""
    candidate_scores: dict[str, float] = field(default_factory=dict)
    routing_mode: str = "active"
    recommended_receiver: str = ""
    planner_fallback_required: bool = False
    planner_fallback_reasons: list[str] = field(default_factory=list)

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


CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "task_decomposition": ("planner", "planning", "decompose", "规划", "计划", "拆解"),
    "routing": ("router", "orchestrator", "coordinator", "routing", "调度", "路由", "协调"),
    "orchestration": ("orchestrator", "workflow", "team manager", "编排", "工作流"),
    "retrieval": ("retriever", "research", "search", "检索", "搜索", "调研"),
    "evidence_ranking": ("evidence", "citation", "source", "证据", "引用", "来源"),
    "embedding_generation": ("embedding", "vector", "向量", "嵌入"),
    "synthesis": (
        "synthesis",
        "integrate",
        "merge",
        "writer",
        "author",
        "综合",
        "整合",
        "汇总",
    ),
    "writing": ("writer", "author", "draft", "撰写", "写作", "成文"),
    "summarization": ("summarizer", "summary", "summarize", "摘要", "总结"),
    "validation": ("reviewer", "critic", "validator", "auditor", "审查", "评审", "核验"),
    "schema_review": ("schema", "format", "contract", "格式", "结构", "契约"),
    "failure_review": ("failure", "debug", "diagnose", "失败", "错误", "诊断"),
    "execution": ("executor", "execute", "runner", "执行", "运行"),
    "coding": ("coder", "developer", "programming", "代码", "编程", "开发"),
    "tool_use": ("tool", "function", "workbench", "工具", "函数"),
    "memory_governance": ("memory manager", "memory", "记忆管理", "记忆治理"),
    "claim_compaction": ("compaction", "consolidate", "claim", "压缩", "合并", "主张"),
    "data_analysis": ("analyst", "analysis", "statistics", "分析", "统计"),
    "final_deliverable_draft": (
        "final answer",
        "deliverable",
        "writer",
        "author",
        "最终交付",
        "最终答案",
    ),
    "final_deliverable_review": (
        "final review",
        "acceptance",
        "reviewer",
        "critic",
        "auditor",
        "validator",
        "最终审查",
        "验收",
    ),
    "memory_use": ("reuse memory", "memory view", "记忆复用", "记忆视图"),
}

CAPABILITY_ACTIONS: dict[str, tuple[str, ...]] = {
    "task_decomposition": ("DECOMPOSE_TASK",),
    "routing": ("ROUTE_TASK",),
    "orchestration": ("ROUTE_TASK", "CHECK_READINESS"),
    "retrieval": ("RETRIEVE_EVIDENCE",),
    "evidence_ranking": ("RETRIEVE_EVIDENCE", "VERIFY_CLAIM"),
    "embedding_generation": ("BUILD_EMBEDDING",),
    "synthesis": ("MERGE_SUMMARY", "WRITE_OUTPUT"),
    "writing": ("WRITE_OUTPUT",),
    "summarization": ("SUMMARIZE_CONTENT", "MERGE_SUMMARY"),
    "validation": ("REVIEW_OUTPUT", "VERIFY_CLAIM"),
    "schema_review": ("REVIEW_SCHEMA",),
    "failure_review": ("DIAGNOSE_FAILURE",),
    "execution": ("EXECUTE_TOOL",),
    "coding": ("RUN_CODE", "WRITE_CODE"),
    "tool_use": ("EXECUTE_TOOL",),
    "memory_governance": ("UPDATE_MEMORY", "MERGE_MEMORY"),
    "claim_compaction": ("MERGE_MEMORY", "RESOLVE_CONFLICT"),
    "data_analysis": ("ANALYZE_DATA",),
    "final_deliverable_draft": ("WRITE_OUTPUT",),
    "final_deliverable_review": ("REVIEW_OUTPUT",),
    "memory_use": ("READ_MEMORY",),
}

ACTION_CAPABILITIES: dict[str, tuple[str, ...]] = {}
for _capability_name, _actions in CAPABILITY_ACTIONS.items():
    for _action_name in _actions:
        ACTION_CAPABILITIES.setdefault(_action_name, tuple())
        ACTION_CAPABILITIES[_action_name] = (
            *ACTION_CAPABILITIES[_action_name],
            _capability_name,
        )

CAPABILITY_INPUT_PREFERENCES: dict[str, tuple[str, ...]] = {
    "task_decomposition": ("task_goal", "constraints", "candidate_options"),
    "routing": ("metadata", "readiness", "capability_hints"),
    "retrieval": ("query", "constraints", "negative_findings"),
    "evidence_ranking": ("evidence_snippets", "scores", "sources"),
    "synthesis": ("summary", "evidence_snippets", "confirmed_decisions"),
    "writing": ("task_goal", "constraints", "evidence_snippets", "deliverable_schema"),
    "summarization": ("topic_card", "evidence_snippets", "key_points"),
    "validation": ("deliverable", "evidence_snippets", "risks", "constraints"),
    "schema_review": ("schema", "structured_output", "validation_errors"),
    "failure_review": ("failure_state", "execution_trace", "retry_hint"),
    "execution": ("tool_inputs", "artifact_refs", "retry_hint"),
    "coding": ("requirements", "code_artifacts", "stderr", "failure_state"),
    "memory_governance": ("promotion_view", "memory_candidates", "confidence"),
    "claim_compaction": ("claim_cards", "conflicts", "lineage"),
    "data_analysis": ("structured_data", "metrics", "evidence_snippets"),
}

CAPABILITY_OUTPUT_TYPES: dict[str, tuple[str, ...]] = {
    "task_decomposition": ("task_plan",),
    "routing": ("route_decision",),
    "retrieval": ("retrieval_state",),
    "evidence_ranking": ("evidence_view",),
    "embedding_generation": ("embedding_state",),
    "synthesis": ("summary_section",),
    "writing": ("draft_section",),
    "summarization": ("summary",),
    "validation": ("review_report",),
    "schema_review": ("schema_report",),
    "failure_review": ("failure_diagnosis",),
    "execution": ("artifact_state",),
    "coding": ("code_artifact",),
    "memory_governance": ("memory_admission_report",),
    "claim_compaction": ("memory_view",),
    "data_analysis": ("analysis_report",),
    "final_deliverable_draft": ("final_deliverable",),
    "final_deliverable_review": ("acceptance_report",),
}

CAPABILITY_STATE_TYPES: dict[str, tuple[str, ...]] = {
    "task_decomposition": ("task_graph_state", "coverage_state"),
    "routing": ("task_graph_state", "failure_state", "readiness_report"),
    "orchestration": ("task_graph_state", "coverage_state", "failure_state"),
    "retrieval": ("retrieval_state", "embedding_state", "coverage_state"),
    "evidence_ranking": ("retrieval_state", "embedding_state"),
    "embedding_generation": ("embedding_state",),
    "synthesis": ("retrieval_state", "artifact_state", "memory_ref_handoff"),
    "writing": ("retrieval_state", "artifact_state", "retry_loop_summary"),
    "summarization": ("retrieval_state", "artifact_state"),
    "validation": ("artifact_state", "failure_state", "final_deliverable"),
    "schema_review": ("artifact_state", "failure_state"),
    "failure_review": ("failure_state", "retry_loop_summary", "artifact_state"),
    "execution": ("artifact_state", "failure_state", "retry_loop_summary"),
    "coding": ("artifact_state", "failure_state"),
    "memory_governance": ("artifact_state", "retrieval_state", "memory_ref_handoff"),
    "claim_compaction": ("artifact_state", "retrieval_state", "memory_ref_handoff"),
    "data_analysis": ("artifact_state", "retrieval_state"),
    "memory_use": ("memory_ref_handoff",),
}


class CapabilityProfileManagerLite:
    """Rules-first, multi-source capability profiles from the final design."""

    def __init__(self, agents: Iterable[object]) -> None:
        self._profiles: dict[str, CapabilityProfile] = {}
        for agent in agents:
            self.register_or_update(
                agent_id=str(getattr(agent, "agent_id", "")),
                role=str(getattr(agent, "role", getattr(agent, "agent_id", ""))),
                declared_capabilities=tuple(
                    getattr(agent, "capabilities", ()) or ()
                ),
            )

    def register_or_update(
        self,
        *,
        agent_id: str,
        role: str,
        role_description: str = "",
        system_prompt: str = "",
        declared_capabilities: Iterable[str] = (),
        tools: Iterable[ToolCapabilityDescriptor | Mapping[str, Any]] = (),
        preferred_actions: Iterable[str] = (),
        input_preference: Iterable[str] = (),
        output_types: Iterable[str] = (),
        accepted_state_types: Iterable[str] = (),
        message_types: Iterable[str] = (),
    ) -> CapabilityProfile | None:
        normalized_id = str(agent_id or "").strip()
        if not normalized_id:
            return None
        normalized_role = str(role or normalized_id).strip()
        existing = self._profiles.get(normalized_id)
        profile = existing or CapabilityProfile(
            agent_id=normalized_id,
            role=normalized_role,
        )
        before = _profile_identity(profile)
        profile.role = normalized_role

        role_text = " ".join(
            part
            for part in (normalized_id, normalized_role, role_description, system_prompt)
            if part
        )
        inferred = _infer_capabilities(role_text)
        declared = {
            _normalize_tag(item)
            for item in declared_capabilities
            if str(item).strip()
        }
        profile.role_capabilities = {
            **{name: 0.88 for name in inferred},
            **{name: 1.0 for name in declared},
        }
        profile.declared_preferred_actions = {
            _normalize_action(item)
            for item in preferred_actions
            if str(item).strip()
        }
        profile.declared_input_preference = {
            _normalize_tag(item)
            for item in input_preference
            if str(item).strip()
        }
        profile.declared_output_types = {
            _normalize_tag(item) for item in output_types if str(item).strip()
        }
        profile.declared_accepted_state_types = {
            str(item).strip()
            for item in accepted_state_types
            if str(item).strip()
        }
        profile.declared_message_types = {
            str(item).strip() for item in message_types if str(item).strip()
        }
        self._sync_tools(profile, tools)
        self._refresh_derived_fields(profile)
        profile.profile_fingerprint = hashlib.sha256(
            role_text.encode("utf-8")
        ).hexdigest()[:16]
        profile.updated_at = _utc_now()
        if existing is not None and _profile_identity(profile) != before:
            profile.profile_version += 1
        self._profiles[normalized_id] = profile
        return profile

    def sync_tools(
        self,
        agent_id: str,
        tools: Iterable[ToolCapabilityDescriptor | Mapping[str, Any]],
    ) -> CapabilityProfile | None:
        profile = self.get(agent_id)
        if profile is None:
            return None
        before = _profile_identity(profile)
        self._sync_tools(profile, tools)
        self._refresh_derived_fields(profile)
        if _profile_identity(profile) != before:
            profile.profile_version += 1
            profile.updated_at = _utc_now()
        return profile

    def record_execution(
        self,
        agent_id: str,
        *,
        success: bool,
        schema_valid: bool | None = None,
        action: str = "",
        cost_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> CapabilityProfile | None:
        profile = self.get(agent_id)
        if profile is None:
            return None
        if success:
            profile.success_count += 1
        else:
            profile.failure_count += 1
        if schema_valid is True:
            profile.schema_success_count += 1
        elif schema_valid is False:
            profile.schema_failure_count += 1
        profile.total_cost_tokens += max(0, int(cost_tokens))
        profile.total_latency_ms += max(0.0, float(latency_ms))
        profile.current_load = max(0, profile.current_load - 1)
        normalized_action = _normalize_action(action) if action else ""
        for capability in ACTION_CAPABILITIES.get(normalized_action, ()):
            prior = profile.runtime_capabilities.get(capability, 0.5)
            profile.runtime_capabilities[capability] = round(
                min(0.99, prior + 0.05) if success else max(0.1, prior - 0.08),
                4,
            )
        if normalized_action and success:
            profile.runtime_preferred_actions.add(normalized_action)
        profile.profile_version += 1
        profile.updated_at = _utc_now()
        self._refresh_derived_fields(profile)
        return profile

    def record_execution_start(
        self,
        agent_id: str,
        *,
        memory_keys: Iterable[str] = (),
    ) -> CapabilityProfile | None:
        profile = self.get(agent_id)
        if profile is None:
            return None
        profile.current_load += 1
        profile.memory_locality_keys.update(
            str(item).strip() for item in memory_keys if str(item).strip()
        )
        if len(profile.memory_locality_keys) > 256:
            profile.memory_locality_keys = set(
                sorted(profile.memory_locality_keys)[-256:]
            )
        return profile

    def memory_locality_score(
        self,
        agent_id: str,
        memory_keys: Iterable[str],
    ) -> float:
        profile = self.get(agent_id)
        requested = {str(item).strip() for item in memory_keys if str(item).strip()}
        if profile is None or not requested:
            return 0.0
        return len(requested & profile.memory_locality_keys) / len(requested)

    def _sync_tools(
        self,
        profile: CapabilityProfile,
        tools: Iterable[ToolCapabilityDescriptor | Mapping[str, Any]],
    ) -> None:
        profile.tool_capabilities = {}
        profile.tool_capability_sources = {}
        profile.tool_cost_levels = {}
        profile.available_tools = set()
        profile.tool_supported_actions = set()
        for raw_tool in tools:
            tool = _coerce_tool(raw_tool)
            if tool is None or not tool.tool_id:
                continue
            profile.available_tools.add(tool.tool_id)
            profile.tool_cost_levels[tool.tool_id] = max(
                0.0,
                min(1.0, float(tool.cost_level)),
            )
            inferred = {
                *_infer_capabilities(
                    " ".join((tool.tool_id, tool.description, *tool.capability_tags))
                ),
                *(_normalize_tag(item) for item in tool.capability_tags),
                "tool_use",
            }
            for capability in inferred:
                profile.tool_capabilities[capability] = max(
                    profile.tool_capabilities.get(capability, 0.0),
                    0.85,
                )
                profile.tool_capability_sources.setdefault(capability, set()).add(
                    tool.tool_id
                )
            profile.tool_supported_actions.update(
                _normalize_action(item) for item in tool.supported_actions
            )

    def _refresh_derived_fields(self, profile: CapabilityProfile) -> None:
        profile.preferred_actions = profile.declared_preferred_actions | {
            action
            for capability in profile.capabilities
            for action in CAPABILITY_ACTIONS.get(capability, ())
        } | profile.tool_supported_actions | profile.runtime_preferred_actions
        profile.input_preference = profile.declared_input_preference | {
            preference
            for capability in profile.capabilities
            for preference in CAPABILITY_INPUT_PREFERENCES.get(capability, ())
        }
        profile.output_types = profile.declared_output_types | {
            output
            for capability in profile.capabilities
            for output in CAPABILITY_OUTPUT_TYPES.get(capability, ())
        }
        profile.accepted_state_types = profile.declared_accepted_state_types | {
            state_type
            for capability in profile.capabilities
            for state_type in CAPABILITY_STATE_TYPES.get(capability, ())
        }
        profile.message_types = profile.declared_message_types | (
            _message_types_for_capabilities(profile.capabilities)
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

    def agent_ids(self) -> list[str]:
        return sorted(self._profiles)


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
        required_action: str | None = None,
        candidates: Iterable[str] | None = None,
        routing_mode: str = "active",
        task_id: str = "",
        candidate_memory_locality: Mapping[str, float] | None = None,
        candidate_load: Mapping[str, float] | None = None,
    ) -> RouteDecision:
        state_types = {ref.state_type for ref in state_refs}
        reasons: list[str] = []
        receiver = declared_receiver
        msg_type = self._message_type_for(state_types)
        action = _normalize_action(
            required_action or _action_for_state_types(state_types)
        )
        candidate_ids = list(
            dict.fromkeys(
                item
                for item in (
                    list(candidates)
                    if candidates is not None
                    else self.profiles.agent_ids()
                )
                if self.profiles.get(item) is not None
            )
        )
        scores = {
            item
            : self._route_score(
                self.profiles.get(item),
                action=action,
                state_types=state_types,
            )
            for item in candidate_ids
        }
        planner_fallback_reasons = self._planner_fallback_reasons(
            action=action,
            candidates=candidate_ids,
            scores=scores,
            readiness=readiness,
            state_types=state_types,
        )
        planner_fallback_required = bool(planner_fallback_reasons)
        planner_candidate = self._planner_candidate(
            candidate_ids,
            task_id=task_id,
            memory_locality=candidate_memory_locality or {},
            current_load=candidate_load or {},
        )
        recommended_receiver = (
            planner_candidate
            if planner_fallback_required and planner_candidate
            else declared_receiver
        )

        if readiness == "blocked":
            receiver = "runtime"
            msg_type = "readiness_report"
            reasons.append("readiness_blocked")
        elif self._sender_finishes_workflow(sender, state_types):
            receiver = "runtime"
            msg_type = "final_deliverable"
            reasons.append("final_deliverable_review_completed")
        elif receiver == "runtime" and not state_types:
            reasons.append("declared_receiver_runtime")
        else:
            best_score_value = max(scores.values(), default=-1.0)
            tied = [
                agent_id
                for agent_id, score in scores.items()
                if abs(score - best_score_value) <= 1e-9
            ]
            best = (
                self._select_equivalent_candidate(
                    tied,
                    action=action,
                    task_id=task_id,
                    memory_locality=candidate_memory_locality or {},
                    current_load=candidate_load or {},
                )
                if len(tied) > 1
                else tied[0]
                if tied
                else declared_receiver
            )
            recommended_receiver = best
            declared_score = scores.get(declared_receiver, -1.0)
            best_score = scores.get(best, -1.0)
            if planner_fallback_required and planner_candidate:
                recommended_receiver = planner_candidate
                if routing_mode == "active":
                    receiver = planner_candidate
                    reasons.append("planner_fallback_selected")
                else:
                    reasons.append("planner_fallback_advisory")
            elif routing_mode == "active" and best_score > declared_score + 0.05:
                receiver = best
                reasons.append(
                    "cold_start_tie_resolved"
                    if len(tied) > 1
                    else "capability_route_selected"
                )
            else:
                reasons.append(
                    "framework_declared_route_preserved"
                    if routing_mode != "active"
                    else "declared_route_capable"
                )

        return RouteDecision(
            receiver=receiver,
            msg_type=msg_type,
            capability_hint=self.profiles.capability_hint_for(receiver),
            route_changed=receiver != declared_receiver,
            reasons=reasons,
            required_action=action,
            candidate_scores={key: round(value, 6) for key, value in scores.items()},
            routing_mode=routing_mode,
            recommended_receiver=recommended_receiver,
            planner_fallback_required=planner_fallback_required,
            planner_fallback_reasons=planner_fallback_reasons,
        )

    def _planner_fallback_reasons(
        self,
        *,
        action: str,
        candidates: list[str],
        scores: Mapping[str, float],
        readiness: str,
        state_types: set[str],
    ) -> list[str]:
        reasons: list[str] = []
        if readiness == "blocked":
            reasons.append("complex_or_blocked_state")
        candidate_profiles = [
            profile
            for candidate in candidates
            if (profile := self.profiles.get(candidate)) is not None
        ]
        action_known = action in ACTION_CAPABILITIES or any(
            action in profile.preferred_actions for profile in candidate_profiles
        )
        if action and not action_known and action != "HANDLE_TASK":
            reasons.append("action_not_in_routing_table")

        required = set(ACTION_CAPABILITIES.get(action, ()))
        capability_or_state_covered = any(
            required & profile.capabilities
            or state_types & profile.accepted_state_types
            for profile in candidate_profiles
        )
        if required and not capability_or_state_covered:
            reasons.append("required_capabilities_uncovered")

        if len(scores) > 1:
            ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
            top_score = ranked[0][1]
            close = [
                agent_id
                for agent_id, score in ranked
                if top_score - score <= 0.02
            ]
            signatures = {
                tuple(
                    sorted(
                        required
                        & (self.profiles.get(agent_id).capabilities)
                    )
                )
                for agent_id in close
                if self.profiles.get(agent_id) is not None
            }
            if len(close) > 1 and len(signatures) > 1:
                reasons.append("close_scores_with_different_capability_types")
        return reasons

    def _planner_candidate(
        self,
        candidates: Iterable[str],
        *,
        task_id: str,
        memory_locality: Mapping[str, float],
        current_load: Mapping[str, float],
    ) -> str:
        planner_candidates = [
            agent_id
            for agent_id in candidates
            if (
                (profile := self.profiles.get(agent_id)) is not None
                and profile.capabilities
                & {"task_decomposition", "routing", "orchestration"}
            )
        ]
        if not planner_candidates:
            return ""
        planner_scores = {
            agent_id: self._route_score(
                self.profiles.get(agent_id),
                action="DECOMPOSE_TASK",
                state_types=set(),
            )
            for agent_id in planner_candidates
        }
        top_score = max(planner_scores.values())
        tied = [
            agent_id
            for agent_id, score in planner_scores.items()
            if abs(score - top_score) <= 1e-9
        ]
        return self._select_equivalent_candidate(
            tied,
            action="DECOMPOSE_TASK",
            task_id=task_id,
            memory_locality=memory_locality,
            current_load=current_load,
        )

    def _route_score(
        self,
        profile: CapabilityProfile | None,
        *,
        action: str,
        state_types: set[str],
    ) -> float:
        if profile is None:
            return -1.0
        action_match = 1.0 if action and action in profile.preferred_actions else 0.0
        required_capabilities = set(ACTION_CAPABILITIES.get(action, ()))
        capability_match = (
            len(required_capabilities & profile.capabilities) / len(required_capabilities)
            if required_capabilities
            else 0.5
        )
        tool_match = (
            1.0
            if profile.available_tools
            and action
            and action in profile.tool_supported_actions
            else 0.0
        )
        tool_cost = (
            sum(profile.tool_cost_levels.values()) / len(profile.tool_cost_levels)
            if profile.tool_cost_levels
            else 0.0
        )
        cost_penalty = max(
            min(1.0, profile.average_cost_tokens / 8000.0),
            tool_cost,
        )
        return (
            0.45 * action_match
            + 0.35 * capability_match
            + 0.10 * tool_match
            + 0.10 * profile.history_success
            - 0.15 * cost_penalty
            - 0.10 * (1.0 - profile.schema_reliability)
        )

    def _select_equivalent_candidate(
        self,
        candidates: list[str],
        *,
        action: str,
        task_id: str,
        memory_locality: Mapping[str, float],
        current_load: Mapping[str, float],
    ) -> str:
        required = set(ACTION_CAPABILITIES.get(action, ()))

        def rank(agent_id: str) -> tuple[float, ...]:
            profile = self.profiles.get(agent_id)
            if profile is None:
                return (-1.0,)
            matching = required & profile.capabilities
            specificity = (
                len(matching) / max(1, len(profile.capabilities))
                if required
                else 0.0
            )
            tool_available = float(
                bool(action and action in profile.tool_supported_actions)
            )
            locality = max(0.0, min(1.0, float(memory_locality.get(agent_id, 0.0))))
            load = max(0.0, float(current_load.get(agent_id, 0.0)))
            cost = min(1.0, profile.average_cost_tokens / 8000.0)
            stable_hash = int(
                hashlib.sha256(
                    f"{task_id}:{action}:{agent_id}".encode("utf-8")
                ).hexdigest()[:12],
                16,
            )
            return (
                specificity,
                tool_available,
                locality,
                -load,
                profile.schema_reliability,
                -cost,
                float(stable_hash),
            )

        return max(sorted(candidates), key=rank)

    def _sender_finishes_workflow(
        self,
        sender: str,
        state_types: set[str],
    ) -> bool:
        profile = self.profiles.get(sender)
        return bool(
            profile is not None
            and "final_deliverable_review" in profile.capabilities
            and not state_types
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
        task_id: str = "",
        action: str = "",
        memory_locality: Mapping[str, float] | None = None,
        current_load: Mapping[str, float] | None = None,
    ) -> RouteDecision:
        scored: list[tuple[float, str, list[str]]] = []
        resolved_action = _normalize_action(action) or _normalize_action(
            next(iter(CAPABILITY_ACTIONS.get(preferred_capability or "", ())), "")
        )
        for agent_id in candidates:
            profile = self.profiles.get(agent_id)
            if profile is None:
                scored.append((-1.0, agent_id, ["missing_profile"]))
                continue
            score = self._route_score(
                profile,
                action=resolved_action,
                state_types={required_state_type} if required_state_type else set(),
            )
            reasons: list[str] = []
            if required_state_type and required_state_type in profile.accepted_state_types:
                score += 0.3
                reasons.append("state_type_match")
            if preferred_capability and preferred_capability in profile.capabilities:
                score += 0.2
                reasons.append("capability_match")
            if not reasons:
                reasons.append("stable_agent_id_tiebreak")
            scored.append((score, agent_id, reasons))
        scored.sort(key=lambda item: (-item[0], item[1]))
        top_score = scored[0][0]
        tied = [item[1] for item in scored if abs(item[0] - top_score) <= 1e-9]
        receiver = self._select_equivalent_candidate(
            tied,
            action=resolved_action,
            task_id=task_id,
            memory_locality=memory_locality or {},
            current_load=current_load or {},
        )
        reasons = next(item[2] for item in scored if item[1] == receiver)
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
            candidate_scores={item[1]: round(item[0], 6) for item in scored},
            recommended_receiver=receiver,
        )


def _capability_rows(
    values: Mapping[str, float],
    *,
    source: str,
) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "source": source,
            "confidence": round(float(confidence), 4),
        }
        for name, confidence in sorted(values.items())
    ]


def _tool_capability_rows(profile: CapabilityProfile) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, confidence in sorted(profile.tool_capabilities.items()):
        sources = sorted(profile.tool_capability_sources.get(name, ())) or ["unknown"]
        rows.extend(
            {
                "name": name,
                "source": f"tool:{source}",
                "confidence": round(float(confidence), 4),
            }
            for source in sources
        )
    return rows


def _normalize_tag(value: object) -> str:
    text = re.sub(r"[^\w]+", "_", str(value or "").strip().casefold())
    return text.strip("_")


def _normalize_action(value: object) -> str:
    return _normalize_tag(value).upper()


def _infer_capabilities(text: str) -> set[str]:
    normalized = str(text or "").casefold()
    return {
        capability
        for capability, cues in CAPABILITY_KEYWORDS.items()
        if any(cue.casefold() in normalized for cue in cues)
    }


def _message_types_for_capabilities(capabilities: set[str]) -> set[str]:
    message_types = {"agent_output"}
    if capabilities & {"retrieval", "evidence_ranking", "embedding_generation"}:
        message_types.update({"state_ref_handoff", "retrieval_state", "embedding_state"})
    if capabilities & {"synthesis", "writing", "execution", "coding"}:
        message_types.update({"state_ref_handoff", "memory_ref_handoff", "artifact_state"})
    if capabilities & {"validation", "schema_review", "failure_review"}:
        message_types.update({"artifact_state", "failure_state", "readiness_report"})
    if capabilities & {"memory_governance", "claim_compaction"}:
        message_types.update({"memory_promotion", "memory_ref_handoff"})
    return message_types


def _profile_identity(profile: CapabilityProfile) -> tuple[object, ...]:
    return (
        profile.role,
        tuple(sorted(profile.role_capabilities.items())),
        tuple(sorted(profile.tool_capabilities.items())),
        tuple(
            (key, tuple(sorted(value)))
            for key, value in sorted(profile.tool_capability_sources.items())
        ),
        tuple(sorted(profile.tool_cost_levels.items())),
        tuple(sorted(profile.runtime_capabilities.items())),
        tuple(sorted(profile.declared_preferred_actions)),
        tuple(sorted(profile.declared_input_preference)),
        tuple(sorted(profile.declared_output_types)),
        tuple(sorted(profile.declared_accepted_state_types)),
        tuple(sorted(profile.declared_message_types)),
        tuple(sorted(profile.tool_supported_actions)),
        tuple(sorted(profile.runtime_preferred_actions)),
        tuple(sorted(profile.preferred_actions)),
        tuple(sorted(profile.input_preference)),
        tuple(sorted(profile.output_types)),
        tuple(sorted(profile.available_tools)),
        tuple(sorted(profile.accepted_state_types)),
        tuple(sorted(profile.message_types)),
        profile.profile_fingerprint,
    )


def _coerce_tool(
    value: ToolCapabilityDescriptor | Mapping[str, Any],
) -> ToolCapabilityDescriptor | None:
    if isinstance(value, ToolCapabilityDescriptor):
        return value
    if not isinstance(value, Mapping):
        return None
    tool_id = str(value.get("tool_id") or value.get("name") or "").strip()
    if not tool_id:
        return None
    return ToolCapabilityDescriptor(
        tool_id=tool_id,
        description=str(value.get("description") or ""),
        capability_tags=tuple(str(item) for item in value.get("capability_tags", ()) or ()),
        supported_actions=tuple(str(item) for item in value.get("supported_actions", ()) or ()),
        cost_level=float(value.get("cost_level") or 0.0),
    )


def _action_for_state_types(state_types: set[str]) -> str:
    if "failure_state" in state_types:
        return "DIAGNOSE_FAILURE"
    if state_types & {"retrieval_state", "embedding_state"}:
        return "WRITE_OUTPUT"
    if "artifact_state" in state_types:
        return "REVIEW_OUTPUT"
    if "memory_ref_handoff" in state_types:
        return "READ_MEMORY"
    return ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
