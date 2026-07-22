from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from agent_runtime.bridge.state_memory_bridge import StateToMemoryBridgeLite
from agent_runtime.core.communication import (
    CapabilityProfileManagerLite,
    CapabilityRouterLite,
    CommunicationGateLite,
    ControlBudgetLite,
)
from agent_runtime.core.models import AgentOutput, Mode, TaskSpec
from agent_runtime.core.readiness import ReadinessBarrierLite
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.memory_store import (
    MemoryAdmissionReport,
    MemoryRef,
    MemoryStoreLite,
)
from agent_runtime.protocol.shp import build_handoff_envelope
from agent_runtime.reliability.contract_guard import (
    ContractContext,
    guard_agent_output,
    render_contract_retry_prompt,
)
from agent_runtime.state.non_text import (
    build_embedding_state_payload,
    build_retrieval_state_payload,
)
from agent_runtime.state.state_pool import StatePoolLite, StateRef


@dataclass(slots=True, frozen=True)
class AgentDescriptor:
    agent_id: str
    role: str
    capabilities: tuple[str, ...] = ()
    role_description: str = ""
    system_prompt: str = ""
    tools: tuple[dict[str, Any], ...] = ()
    preferred_actions: tuple[str, ...] = ()
    input_preference: tuple[str, ...] = ()
    output_types: tuple[str, ...] = ()
    framework_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class KernelSession:
    session_id: str
    framework: str
    external_session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    opened_at: float = field(default_factory=time.time)
    closed_at: float | None = None


@dataclass(slots=True)
class MemoryContext:
    refs: list[MemoryRef]
    prompt_views: list[str]
    deliverable_view: str = ""


@dataclass(slots=True)
class PreparedAgentInput:
    agent: AgentDescriptor
    state_prompt_views: list[str]
    memory_prompt_views: list[str]
    deliverable_view: str = ""
    raw_access_count: int = 0


@dataclass(slots=True)
class ProcessedAgentOutput:
    output: AgentOutput
    state_refs: list[StateRef]


class CollaborationKernel:
    """Framework-neutral collaboration data-plane services.

    Framework adapters keep ownership of agent construction and scheduling.
    The kernel owns contract handling, state and memory access, SHP handoff
    construction, and collaboration observability.
    """

    MAX_FORMAT_RETRY_INPUT_TOKENS = 800
    ARTIFACT_PAYLOAD_SUMMARY_CHARS = 240
    ARTIFACT_STATE_SUMMARY_CHARS = 120

    def __init__(
        self,
        *,
        agents: Iterable[object],
        token_counter: TokenCounter,
        metrics: MetricsCollector,
        trace: TraceLogger,
        state_pool: StatePoolLite | None = None,
        memory_store: MemoryStoreLite | None = None,
    ) -> None:
        agent_list = list(agents)
        self.token_counter = token_counter
        self.metrics = metrics
        self.trace = trace
        self.state_pool = state_pool or StatePoolLite(Path("runs") / "state")
        self.memory_store = memory_store or MemoryStoreLite()
        self.state_memory_bridge = StateToMemoryBridgeLite(self.memory_store)
        self.readiness_barrier = ReadinessBarrierLite()
        self.capability_profiles = CapabilityProfileManagerLite(agent_list)
        self.capability_router = CapabilityRouterLite(self.capability_profiles)
        self.communication_gate = CommunicationGateLite()
        self.control_budget = ControlBudgetLite()
        self._sessions: dict[str, KernelSession] = {}
        self._emitted_capability_profile_ids: set[str] = set()
        self._closed = False

    def register_agent(self, agent: AgentDescriptor) -> dict[str, object]:
        self._ensure_open()
        existing = self.capability_profiles.get(agent.agent_id)
        previous_version = existing.profile_version if existing is not None else 0
        profile = self.capability_profiles.register_or_update(
            agent_id=agent.agent_id,
            role=agent.role,
            role_description=agent.role_description,
            system_prompt=agent.system_prompt,
            declared_capabilities=agent.capabilities,
            tools=agent.tools,
            preferred_actions=agent.preferred_actions,
            input_preference=agent.input_preference,
            output_types=agent.output_types,
            accepted_state_types=tuple(
                agent.framework_metadata.get("accepted_state_types", ()) or ()
            ),
            message_types=tuple(
                agent.framework_metadata.get("message_types", ()) or ()
            ),
            registry_scope=str(
                agent.framework_metadata.get("registry_scope", "business")
            ),
            instance_aliases=tuple(
                agent.framework_metadata.get("instance_aliases", ()) or ()
            ),
            alias_only=bool(agent.framework_metadata.get("profile_alias_only")),
        )
        payload = profile.to_dict() if profile is not None else {}
        profile_changed = profile is not None and (
            profile.agent_id not in self._emitted_capability_profile_ids
            or existing is None or profile.profile_version != previous_version
        )
        if payload and profile_changed:
            self.trace.write(
                "capability_profile_updated",
                {
                    "agent_id": profile.agent_id,
                    "registry_scope": profile.registry_scope,
                    "profile": payload,
                },
            )
            self._emitted_capability_profile_ids.add(profile.agent_id)
        return payload

    def record_agent_execution(
        self,
        *,
        agent_id: str,
        success: bool,
        schema_valid: bool | None = None,
        action: str = "",
        cost_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> dict[str, object]:
        self._ensure_open()
        profile = self.capability_profiles.record_execution(
            agent_id,
            success=success,
            schema_valid=schema_valid,
            action=action,
            cost_tokens=cost_tokens,
            latency_ms=latency_ms,
        )
        payload = profile.to_dict() if profile is not None else {}
        if payload:
            self.trace.write(
                "capability_profile_feedback",
                {
                    "agent_id": agent_id,
                    "success": success,
                    "schema_valid": schema_valid,
                    "action": action,
                    "cost_tokens": max(0, int(cost_tokens)),
                    "latency_ms": round(max(0.0, float(latency_ms)), 3),
                    "profile_version": payload.get("profile_version", 0),
                    "schema_reliability": payload.get("schema_reliability", 0.0),
                    "history": payload.get("history", {}),
                    "profile": payload,
                },
            )
        return payload

    def begin_agent_execution(
        self,
        *,
        agent_id: str,
        memory_keys: Iterable[str] = (),
    ) -> dict[str, object]:
        self._ensure_open()
        profile = self.capability_profiles.record_execution_start(
            agent_id,
            memory_keys=memory_keys,
        )
        payload = profile.to_dict() if profile is not None else {}
        if payload:
            self.trace.write(
                "capability_profile_execution_started",
                {
                    "agent_id": agent_id,
                    "current_load": payload.get("current_load", 0),
                    "memory_locality_count": payload.get(
                        "memory_locality_count", 0
                    ),
                },
            )
        return payload

    def open_session(
        self,
        *,
        framework: str,
        external_session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> KernelSession:
        self._ensure_open()
        session = KernelSession(
            session_id=f"session_{uuid.uuid4().hex}",
            framework=framework,
            external_session_id=external_session_id,
            metadata=dict(metadata or {}),
        )
        self._sessions[session.session_id] = session
        self.trace.write(
            "kernel_session_opened",
            {
                "session_id": session.session_id,
                "framework": framework,
                "external_session_id": external_session_id,
                "metadata": session.metadata,
            },
        )
        return session

    def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.closed_at = time.time()
        self.trace.write(
            "kernel_session_closed",
            {
                "session_id": session.session_id,
                "framework": session.framework,
                "external_session_id": session.external_session_id,
            },
        )

    def active_sessions(self) -> list[KernelSession]:
        return list(self._sessions.values())

    def prepare_memory_context(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        final_task: bool,
        deliverable_budget_chars: int,
        strict_group_scope: bool = False,
    ) -> MemoryContext:
        self._ensure_open()
        started = time.perf_counter()
        search_report = self.memory_store.search_memory_with_report(
            task.prompt,
            tags=[task.group_id],
            top_k=4 if final_task else 2,
            required_tags=[task.group_id] if strict_group_scope else None,
        )
        self.metrics.record_memory_search_backend(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            retrieval_backend=search_report.retrieval_backend,
            vector_retrieval_count=search_report.vector_retrieval_count,
        )
        prompt_views = [
            self.memory_store.render_prompt_view(ref) for ref in search_report.refs
        ]
        deliverable_view = (
            self.memory_store.render_deliverable_view(
                search_report.refs,
                task_title=task.title,
                budget_chars=deliverable_budget_chars,
            )
            if final_task
            else ""
        )
        if prompt_views:
            self.metrics.record_memory_retrieval(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                hit_count=len(search_report.refs),
                useful_hit_count=0,
                wrong_hit_count=0,
                prompt_view="\n".join(prompt_views),
                token_counter=self.token_counter,
            )
        self.metrics.record_agent_local_timing(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            agent_id="local_runtime",
            local_memory_search_ms=(time.perf_counter() - started) * 1000,
        )
        return MemoryContext(
            refs=search_report.refs,
            prompt_views=prompt_views,
            deliverable_view=deliverable_view,
        )

    def record_memory_use_feedback(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        useful_refs: list[MemoryRef],
        wrong_refs: list[MemoryRef] | None = None,
        supported_output: bool = False,
    ) -> dict[str, int]:
        """Apply evidence-backed memory feedback after a downstream output exists."""
        wrong_refs = list(wrong_refs or [])
        useful_ids = list(dict.fromkeys(ref.memory_id for ref in useful_refs))
        wrong_ids = list(dict.fromkeys(ref.memory_id for ref in wrong_refs))
        persisted_useful = self.memory_store.record_useful_hits(useful_ids)
        self.metrics.record_memory_use_feedback(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            useful_hit_count=len(useful_ids),
            wrong_hit_count=len(wrong_ids),
        )
        if supported_output:
            self.metrics.record_memory_supported_output(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                count=1,
            )
        return {
            "useful_hit_count": len(useful_ids),
            "wrong_hit_count": len(wrong_ids),
            "persisted_useful_hit_count": persisted_useful,
            "memory_supported_output_count": int(supported_output),
        }

    def before_agent_receive(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: AgentDescriptor,
        state_refs: list[StateRef],
        memory_context: MemoryContext | None = None,
        state_budget_chars: int = 700,
    ) -> PreparedAgentInput:
        self._ensure_open()
        self.register_agent(agent)
        profile = self.capability_profiles.get(agent.agent_id)
        capabilities = profile.capabilities if profile is not None else ()
        preferred_actions = (
            sorted(profile.preferred_actions) if profile is not None else []
        )
        state_access_action = (
            preferred_actions[0] if len(preferred_actions) == 1 else ""
        )
        state_views: list[str] = []
        state_read_ms = 0.0
        raw_access_total = 0
        for ref in state_refs[-5:]:
            started = time.perf_counter()
            escalation_report = self.state_pool.request_progressive_access(
                ref,
                agent_role=agent.role,
                reason="runtime_prompt_view",
                need_raw=False,
                budget_chars=state_budget_chars,
                capabilities=capabilities,
                action=state_access_action,
            )
            state_read_ms += (time.perf_counter() - started) * 1000
            state_views.append(escalation_report.prompt_view)
            raw_access_count = int(
                escalation_report.cold_access is not None
                and escalation_report.cold_access.allowed
            )
            raw_access_total += raw_access_count
            self.metrics.record_state_access(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                raw_access_count=raw_access_count,
                summary_access_count=int(
                    escalation_report.selected_level
                    in {"summary", "summary_only", "metadata"}
                ),
                evidence_snippet_access_count=int(
                    escalation_report.selected_level == "evidence_snippets"
                ),
                access_escalation_count=(
                    escalation_report.access_escalation_count
                ),
                read_lease_acquire_count=1 + raw_access_count,
            )
        self.metrics.record_agent_local_timing(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            agent_id=agent.agent_id,
            local_state_read_ms=state_read_ms,
            raw_access_count=raw_access_total,
        )
        memory_context = memory_context or MemoryContext([], [])
        return PreparedAgentInput(
            agent=agent,
            state_prompt_views=state_views,
            memory_prompt_views=memory_context.prompt_views,
            deliverable_view=memory_context.deliverable_view,
            raw_access_count=raw_access_total,
        )

    def after_agent_output(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: AgentDescriptor,
        next_action: str,
        output: AgentOutput,
        retry_output: Callable[[str], AgentOutput] | None = None,
    ) -> ProcessedAgentOutput:
        self._ensure_open()
        processed = self.validate_agent_output(
            task=task,
            round_id=round_id,
            mode=mode,
            agent=agent,
            next_action=next_action,
            output=output,
            retry_output=retry_output,
        )
        state_refs = self.write_agent_state(
            task=task,
            round_id=round_id,
            mode=mode,
            agent=agent,
            output=processed,
        )
        return ProcessedAgentOutput(output=processed, state_refs=state_refs)

    def validate_agent_output(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: AgentDescriptor,
        next_action: str,
        output: AgentOutput,
        retry_output: Callable[[str], AgentOutput] | None = None,
    ) -> AgentOutput:
        self._ensure_open()
        return self._apply_contract_guard(
            task=task,
            round_id=round_id,
            mode=mode,
            agent=agent,
            next_action=next_action,
            output=output,
            retry_output=retry_output,
        )

    def write_agent_state(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: AgentDescriptor,
        output: AgentOutput,
    ) -> list[StateRef]:
        self._ensure_open()

        def commit_state(
            *,
            state_type: str,
            payload: dict[str, Any],
            summary: str,
            usage_hint: str,
            contains_embedding_refs: bool,
            tier: str,
            access_policy: str,
            audit_payload: dict[str, Any] | None = None,
        ) -> StateRef:
            state_ref, state = self.state_pool.write_state(
                task_id=task.task_id,
                source_agent=agent.agent_id,
                state_type=state_type,
                payload=payload,
                summary=summary,
                usage_hint=usage_hint,
                contains_embedding_refs=contains_embedding_refs,
                tier=tier,
                access_policy=access_policy,
                audit_payload=audit_payload,
            )
            self.metrics.record_state_write(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                state_type=state_type,
                payload_bytes=state.size_bytes,
                tier=state.tier,
            )
            self.trace.write(
                "state_written",
                {
                    "task_id": task.task_id,
                    "round_id": round_id,
                    "mode": mode,
                    "state": asdict(state),
                },
            )
            return state_ref

        contract_guard = output.metadata.get("contract_guard", {})
        if contract_guard.get("contract_status") == "degraded_fallback":
            return [
                commit_state(
                    state_type="failure_state",
                    payload={
                        "error_type": "contract_violation",
                        "contract_status": "degraded_fallback",
                        "schema_errors": contract_guard.get("schema_errors", []),
                        "allowed_next_step": "review_or_retry_only",
                        "artifact_digest": contract_guard.get(
                            "artifact_digest", {}
                        ),
                    },
                    summary=(
                        f"{agent.agent_id} 输出契约降级："
                        f"{'; '.join(contract_guard.get('schema_errors', [])[:2])}"
                    ),
                    usage_hint="review_or_retry_only",
                    contains_embedding_refs=False,
                    tier="hot",
                    access_policy="prompt_view_only",
                    audit_payload={
                        "content": output.content,
                        "contract_guard": contract_guard,
                        "content_chars": len(output.content),
                    },
                )
            ]

        if agent.agent_id == "retriever":
            embedding_payload = build_embedding_state_payload(task)
            embedding_ref = commit_state(
                state_type="embedding_state",
                payload=embedding_payload,
                summary=(
                    f"{task.title} 的向量引用状态，包含 "
                    f"{len(embedding_payload['chunk_embedding_ids'])} 个 chunk embedding。"
                ),
                usage_hint="vector_similarity_scoring",
                contains_embedding_refs=True,
                tier="hot",
                access_policy="metadata_view_only",
            )
            retrieval_payload = build_retrieval_state_payload(
                task, embedding_state_id=embedding_ref.state_id
            )
            retrieval_ref = commit_state(
                state_type="retrieval_state",
                payload=retrieval_payload,
                summary=(
                    f"{task.title} 的检索状态，包含 "
                    f"{len(task.documents)} 条证据和排序分数。"
                ),
                usage_hint="summary_context_selection",
                contains_embedding_refs=True,
                tier="hot",
                access_policy="prompt_view_only",
            )
            return [embedding_ref, retrieval_ref]

        artifact_id = f"artifact_{task.task_id}_{round_id}_{agent.agent_id}"
        sha256 = hashlib.sha256(output.content.encode("utf-8")).hexdigest()
        prompt_view_summary = str(
            output.metadata.get("prompt_view_summary", "") or ""
        ).strip()
        artifact_summary_source = prompt_view_summary or output.content
        artifact_payload_summary_limit = (
            len(artifact_summary_source)
            if prompt_view_summary
            else self.ARTIFACT_PAYLOAD_SUMMARY_CHARS
        )
        artifact_state_summary_limit = (
            len(artifact_summary_source)
            if prompt_view_summary
            else self.ARTIFACT_STATE_SUMMARY_CHARS
        )
        return [
            commit_state(
                state_type="artifact_state",
                payload={
                    "code_artifact_id": artifact_id,
                    "artifact_id": artifact_id,
                    "stdout_ref": None,
                    "stderr_ref": None,
                    "file_path": None,
                    "sha256": sha256,
                    "summary": self._summary(
                        artifact_summary_source,
                        artifact_payload_summary_limit,
                    ),
                },
                summary=(
                    f"{agent.agent_id} 产物状态："
                    f"{self._summary(artifact_summary_source, artifact_state_summary_limit)}"
                ),
                usage_hint="artifact_summary",
                contains_embedding_refs=False,
                tier="cold",
                access_policy="prompt_view_with_audit_cold_access",
                audit_payload={
                    "artifact_id": artifact_id,
                    "sha256": sha256,
                    "content": output.content,
                    "content_chars": len(output.content),
                },
            )
        ]

    def promote_memory_candidate(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: AgentDescriptor,
        summary: str,
        state_refs: list[StateRef],
        slot_hint: str,
        task_topic: str,
        candidate_kind: str,
        confidence: float,
        importance_hint: float,
        coverage_score: float,
    ) -> MemoryAdmissionReport:
        """Pass a framework output through the canonical rules-first admission path."""
        self._ensure_open()
        source_state_ids = [ref.state_id for ref in state_refs]
        evidence_refs = list(source_state_ids[:3])
        memory_card = {
            "summary": summary.strip(),
            "tags": [task.group_id, "framework:autogen", candidate_kind],
            "reuse_scope": [task.group_id],
            "slot_hint": slot_hint,
            "confidence": confidence,
            "importance_hint": importance_hint,
            "coverage_score": coverage_score,
            "compression_loss_risk": "medium",
            "raw_required_hint": False,
            "temporal_scope": "cross_task",
        }
        admission_report, validation = self.state_memory_bridge.promote(
            task_id=task.task_id,
            source_agent=agent.agent_id,
            task_topic=task_topic,
            fallback_summary=summary.strip(),
            tags=[task.group_id, task.task_id, agent.agent_id, candidate_kind],
            slot_hint=slot_hint,
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            reuse_intent=f"供 {task.group_id} 后续 AutoGen 任务复用",
            control={"memory_card": memory_card},
            degraded=False,
        )
        self.metrics.record_memory_admission(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            memory_candidate_count=admission_report.memory_candidate_count,
            claim_candidate_count=admission_report.claim_candidate_count,
            memory_admitted_count=admission_report.memory_admitted_count,
            memory_rejected_count=admission_report.memory_rejected_count,
            memory_pending_count=admission_report.memory_pending_count,
            memory_audit_only_count=admission_report.memory_audit_only_count,
            admission_unresolved_slot_count=(
                admission_report.admission_unresolved_slot_count
            ),
            claim_to_memoryview_count=admission_report.claim_to_memoryview_count,
        )
        self.metrics.record_memory_write(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            memory_write_count=admission_report.memory_write_count,
            claim_card_count=admission_report.claim_card_count,
            memory_view_count=admission_report.memory_view_count,
            promotion_view_count=admission_report.promotion_view_count,
            alias_mapping_hit_count=admission_report.alias_mapping_hit_count,
            unresolved_slot_count=admission_report.unresolved_slot_count,
        )
        self.trace.write(
            "state_memory_bridge",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "agent_id": agent.agent_id,
                "candidate_kind": candidate_kind,
                "validation_allowed": validation.allowed,
                "validation_reasons": validation.reasons,
                "admission_status": admission_report.admission_status,
                "admission_reasons": admission_report.admission_reasons,
                "candidate_id": admission_report.candidate_id,
                "memory_ref": (
                    admission_report.memory_ref.memory_id
                    if admission_report.memory_ref is not None
                    else None
                ),
            },
        )
        return admission_report

    def build_handoff(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        sender: str,
        declared_receiver: str,
        summary: str,
        state_refs: list[StateRef],
        memory_refs: list[MemoryRef],
        required_action: str | None = None,
        route_candidates: Iterable[str] | None = None,
        routing_mode: str = "active",
    ) -> str:
        self._ensure_open()
        candidates = (
            tuple(dict.fromkeys(route_candidates))
            if route_candidates is not None
            else tuple(
                self.capability_profiles.agent_ids(registry_scope="business")
            )
        )
        memory_keys = tuple(ref.memory_id for ref in memory_refs)
        readiness_report = self.readiness_barrier.assess(
            state_refs=state_refs,
            degraded=any(ref.state_type == "failure_state" for ref in state_refs),
        )
        route_decision = self.capability_router.route(
            sender=sender,
            declared_receiver=declared_receiver,
            state_refs=state_refs,
            readiness=readiness_report.readiness,
            required_action=required_action,
            candidates=candidates,
            routing_mode=routing_mode,
            task_id=task.task_id,
            candidate_memory_locality={
                candidate: self.capability_profiles.memory_locality_score(
                    candidate,
                    memory_keys,
                )
                for candidate in candidates
            },
            candidate_load={
                candidate: float(
                    getattr(self.capability_profiles.get(candidate), "current_load", 0)
                )
                for candidate in candidates
            },
        )
        budget_report = self.control_budget.record_decision(
            task_id=task.task_id,
            estimated_control_tokens=12 + len(state_refs) * 3 + len(memory_refs) * 2,
        )
        gate_report = self.communication_gate.assess(
            readiness=readiness_report.readiness,
            route_decision=route_decision,
            budget_report=budget_report,
        )
        self.metrics.record_control_decision(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            route_changed=route_decision.route_changed,
            gate_status=gate_report.status,
            budget_allowed=budget_report.allowed,
        )
        envelope = build_handoff_envelope(
            task_id=task.task_id,
            round_id=round_id,
            sender=sender,
            receiver=route_decision.receiver,
            summary=summary,
            action=f"{sender}_completed",
            state_refs=state_refs,
            memory_refs=memory_refs,
            metrics={
                "state_ref_count": len(state_refs),
                "memory_ref_count": len(memory_refs),
                "readiness_reasons": readiness_report.reasons,
                "route_decision": route_decision.to_dict(),
                "communication_gate": gate_report.to_dict(),
                "control_budget": budget_report.to_dict(),
            },
            readiness=gate_report.status,
            allowed_next_step=gate_report.allowed_next_step,
            capability_hint=route_decision.capability_hint,
            msg_type=route_decision.msg_type,
        )
        return envelope.to_json()

    def build_message_cost_report(
        self,
        *,
        message_content: str,
        prompt: str,
        memory_prompt_views: list[str],
    ) -> dict[str, int]:
        memory_text = "\n".join(memory_prompt_views)
        return {
            "direct_text_tokens": self.token_counter.count(
                message_content
            ).token_count,
            "prompt_tokens": self.token_counter.count(prompt).token_count,
            "retrieved_memory_tokens": (
                self.token_counter.count(memory_text).token_count
                if memory_text
                else 0
            ),
            "control_llm_tokens": 0,
        }

    def finalize_task(self, task_id: str) -> None:
        self.state_pool.finalize_task(task_id)
        self.control_budget.finalize_task(task_id)

    def close(self) -> None:
        if self._closed:
            return
        for session_id in list(self._sessions):
            self.close_session(session_id)
        self._closed = True

    def _apply_contract_guard(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: AgentDescriptor,
        next_action: str,
        output: AgentOutput,
        retry_output: Callable[[str], AgentOutput] | None,
    ) -> AgentOutput:
        context = ContractContext(
            task_id=task.task_id,
            agent_id=agent.agent_id,
            role=agent.role,
            next_action=next_action,
            artifact_ref=(
                f"cold://{task.task_id}/{round_id}/{agent.agent_id}/artifact"
            ),
        )
        started = time.perf_counter()
        result = guard_agent_output(output.content, context)
        self.metrics.record_agent_local_timing(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            agent_id=agent.agent_id,
            artifact_digest_ms=(time.perf_counter() - started) * 1000,
        )
        self.trace.write(
            "contract_guard_checked",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "agent_id": agent.agent_id,
                "contract_status": result.contract_status,
                "schema_valid": result.schema_valid,
                "repair_actions": result.repair_actions,
                "schema_errors": result.schema_errors,
            },
        )

        if result.retry_required and retry_output is not None:
            result.retry_attempted = True
            retry_prompt = self.apply_retry_budget(
                task=task,
                round_id=round_id,
                mode=mode,
                retry_prompt=render_contract_retry_prompt(
                    context=context, result=result
                ),
            )
            retry_result_output = retry_output(retry_prompt)
            retry_llm_meta = retry_result_output.metadata.get("llm", {})
            if retry_llm_meta:
                self.metrics.record_llm_call(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    usage=retry_llm_meta.get("usage", {}),
                    latency_ms=float(
                        retry_llm_meta.get("latency_ms", 0.0) or 0.0
                    ),
                    agent_id=agent.agent_id,
                    output_chars=len(retry_result_output.content),
                )
                self.metrics.record_provider_guard(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    provider_guard=retry_llm_meta.get("provider_guard"),
                    agent_id=agent.agent_id,
                )
            self.metrics.record_retry_tokens(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                retry_prompt=retry_prompt,
                retry_output=retry_result_output.content,
                token_counter=self.token_counter,
            )
            retry_started = time.perf_counter()
            retry_result = guard_agent_output(
                retry_result_output.content,
                ContractContext(
                    task_id=task.task_id,
                    agent_id=agent.agent_id,
                    role=agent.role,
                    next_action=next_action,
                    artifact_ref=context.artifact_ref,
                ),
            )
            self.metrics.record_agent_local_timing(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                agent_id=agent.agent_id,
                artifact_digest_ms=(time.perf_counter() - retry_started) * 1000,
            )
            if retry_result.schema_valid:
                result.control = retry_result.control
                result.schema_valid = True
                result.contract_status = "repaired"
                result.repair_actions.extend(["context_pruned_format_retry"])
                result.schema_errors = []
                result.retry_required = False
            self.trace.write(
                "contract_guard_retry",
                {
                    "task_id": task.task_id,
                    "round_id": round_id,
                    "mode": mode,
                    "agent_id": agent.agent_id,
                    "retry_schema_valid": retry_result.schema_valid,
                    "retry_status": retry_result.contract_status,
                },
            )

        self.metrics.record_contract_guard(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            contract_report=result.to_dict(),
            agent_id=agent.agent_id,
        )
        metadata = dict(output.metadata)
        metadata["contract_guard"] = result.to_dict()
        return AgentOutput(
            agent_id=output.agent_id,
            content=result.artifact,
            metadata=metadata,
        )

    def apply_retry_budget(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        retry_prompt: str,
    ) -> str:
        token_count = self.token_counter.count(retry_prompt)
        budget_exhausted = (
            token_count.token_count > self.MAX_FORMAT_RETRY_INPUT_TOKENS
        )
        if not budget_exhausted:
            self.metrics.record_retry_budget(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                retry_input_tokens=token_count.token_count,
                budget_exhausted=False,
            )
            return retry_prompt

        ratio = self.MAX_FORMAT_RETRY_INPUT_TOKENS / max(
            1, token_count.token_count
        )
        keep_chars = max(400, int(len(retry_prompt) * ratio * 0.9))
        trimmed = (
            retry_prompt[:keep_chars]
            + "\n\n[retry_budget_truncated: full artifact and overflow fields omitted]"
        )
        self.metrics.record_retry_budget(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            retry_input_tokens=token_count.token_count,
            budget_exhausted=True,
        )
        self.trace.write(
            "retry_budget_applied",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "input_tokens": token_count.token_count,
                "max_tokens": self.MAX_FORMAT_RETRY_INPUT_TOKENS,
                "trimmed_chars": len(trimmed),
            },
        )
        return trimmed

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Collaboration kernel is closed")

    @staticmethod
    def handoff_gate(message_content: str) -> dict[str, object]:
        payload = json.loads(message_content)
        metrics = payload.get("metrics", {})
        if not isinstance(metrics, dict):
            return {}
        gate = metrics.get("communication_gate", {})
        return gate if isinstance(gate, dict) else {}

    @staticmethod
    def _summary(text: str, limit: int) -> str:
        return " ".join(text.split())[:limit]
