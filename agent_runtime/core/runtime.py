from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from agent_runtime.core.agents import DeterministicAgent
from agent_runtime.core.communication import (
    CapabilityProfileManagerLite,
    CapabilityRouterLite,
    CommunicationGateLite,
    ControlBudgetLite,
)
from agent_runtime.core.deliverable_schema import (
    render_schema_prompt,
    schema_field_coverage,
    schema_coverage,
    schema_for_task,
)
from agent_runtime.core.models import AgentOutput, Mode, RuntimeMessage, TaskSpec
from agent_runtime.core.readiness import ReadinessBarrierLite
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.bridge.state_memory_bridge import StateToMemoryBridgeLite
from agent_runtime.memory.memory_store import (
    MemoryAdmissionReport,
    MemoryRef,
    MemoryStoreLite,
    MemoryWriteReport,
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


@dataclass(slots=True)
class RuntimeMemoryWriteResult:
    task_id: str
    round_id: int
    mode: Mode
    agent_id: str
    memory_refs: list[MemoryRef]
    source_state_ids: list[str]
    evidence_refs: list[str]
    admission_report: MemoryAdmissionReport | None = None
    write_report: MemoryWriteReport | None = None


@dataclass(slots=True)
class BackgroundMemoryJob:
    task_id: str
    round_id: int
    mode: Mode
    agent_id: str
    future: Future[RuntimeMemoryWriteResult]
    scheduled_at: float


class V0Runtime:
    """Deterministic v0 runtime for baseline measurement."""

    MAX_FORMAT_RETRY_INPUT_TOKENS = 800
    DELIVERABLE_VIEW_BUDGET_CHARS = 2200
    HANDOFF_SUMMARY_CHARS = 160
    LITE_CONTEXT_SUMMARY_CHARS = 180
    ARTIFACT_PAYLOAD_SUMMARY_CHARS = 240
    ARTIFACT_STATE_SUMMARY_CHARS = 120
    GC_PROTECTED_RECENT_STATE_COUNT = 6

    def __init__(
        self,
        agents: Iterable[DeterministicAgent],
        token_counter: TokenCounter,
        metrics: MetricsCollector,
        trace: TraceLogger,
        state_pool: StatePoolLite | None = None,
        memory_store: MemoryStoreLite | None = None,
    ) -> None:
        self.agents = list(agents)
        self.token_counter = token_counter
        self.metrics = metrics
        self.trace = trace
        self.state_pool = state_pool or StatePoolLite(Path("runs") / "state")
        self.memory_store = memory_store or MemoryStoreLite()
        self.state_memory_bridge = StateToMemoryBridgeLite(self.memory_store)
        self.readiness_barrier = ReadinessBarrierLite()
        self.capability_profiles = CapabilityProfileManagerLite(self.agents)
        self.capability_router = CapabilityRouterLite(self.capability_profiles)
        self.communication_gate = CommunicationGateLite()
        self.control_budget = ControlBudgetLite()
        self._baseline_full_history: dict[tuple[int, str], list[AgentOutput]] = {}
        self._background_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="cmjcc-memory",
        )
        self._background_memory_jobs: list[BackgroundMemoryJob] = []
        self._background_jobs_lock = threading.RLock()
        self._closed = False

    def run_task(self, task: TaskSpec, round_id: int, mode: Mode) -> AgentOutput:
        if self._closed:
            raise RuntimeError("Runtime is closed")
        started = time.perf_counter()
        try:
            return self._run_task_impl(task, round_id, mode)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.metrics.finish_task(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                latency_ms=elapsed_ms,
                success=False,
            )
            failure_payload = {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "latency_ms": elapsed_ms,
                "success": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            self.trace.write("task_failed", failure_payload)
            self.trace.write("task_finished", failure_payload)
            snapshot_writer = getattr(self, "_write_pool_snapshot", None)
            if callable(snapshot_writer):
                try:
                    snapshot_writer(task=task, round_id=round_id, mode=mode)
                except Exception as snapshot_exc:
                    self.trace.write(
                        "pool_snapshot_failed",
                        {
                            "task_id": task.task_id,
                            "round_id": round_id,
                            "mode": mode,
                            "error": repr(snapshot_exc),
                        },
                    )
            raise
        finally:
            self.state_pool.finalize_task(task.task_id)
            self.control_budget.finalize_task(task.task_id)

    def _run_task_impl(self, task: TaskSpec, round_id: int, mode: Mode) -> AgentOutput:
        started = time.perf_counter()
        history_key = (round_id, mode)
        full_history = self._baseline_full_history.get(history_key, [])
        context: list[AgentOutput] = list(full_history) if mode == "baseline_text" else []
        current_task_outputs: list[AgentOutput] = []
        self.trace.write(
            "task_started",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "title": task.title,
                "baseline_full_history_items": len(full_history)
                if mode == "baseline_text"
                else 0,
            },
        )

        previous_sender = "user"
        state_refs: list[StateRef] = []
        memory_refs_used: list[MemoryRef] = []
        lite_context: list[AgentOutput] = []
        final_deliverable_output: AgentOutput | None = None
        task_memory_refs: list[MemoryRef] = []
        task_memory_prompt_views: list[str] = []
        deliverable_view = ""
        deliverable_schema = schema_for_task(task)
        deliverable_schema_prompt = render_schema_prompt(deliverable_schema)
        if mode == "runtime_lite":
            self._drain_background_memory_jobs(reason="before_memory_search")
            search_report = self.memory_store.search_memory_with_report(
                task.prompt,
                tags=[task.group_id],
                top_k=4 if self._is_final_task(task) else 2,
            )
            task_memory_refs = search_report.refs
            self.metrics.record_memory_search_backend(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                retrieval_backend=search_report.retrieval_backend,
                vector_retrieval_count=search_report.vector_retrieval_count,
            )
            task_memory_prompt_views = [
                self.memory_store.render_prompt_view(ref) for ref in task_memory_refs
            ]
            if self._is_final_task(task):
                deliverable_view = self.memory_store.render_deliverable_view(
                    task_memory_refs,
                    task_title=task.title,
                    budget_chars=self.DELIVERABLE_VIEW_BUDGET_CHARS,
                )
            if task_memory_prompt_views:
                self.metrics.record_memory_retrieval(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    hit_count=len(task_memory_refs),
                    useful_hit_count=len(task_memory_refs),
                    wrong_hit_count=0,
                    prompt_view="\n".join(task_memory_prompt_views),
                    token_counter=self.token_counter,
                )
                memory_refs_used.extend(task_memory_refs)

        for agent in self.agents:
            agent_memory_refs = task_memory_refs
            agent_memory_prompt_views = task_memory_prompt_views
            agent_deliverable_view = deliverable_view
            if mode == "runtime_lite" and agent.agent_id == "writer":
                preflight_report = self.memory_store.validate_read_set(task_memory_refs)
                self.metrics.record_preflight_validation(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    validation_count=1,
                    block_count=int(not preflight_report.allowed),
                    stale_read_detected_count=(
                        preflight_report.stale_read_detected_count
                    ),
                )
                if not preflight_report.allowed:
                    agent_memory_refs = preflight_report.valid_refs
                    agent_memory_prompt_views = [
                        self.memory_store.render_prompt_view(ref)
                        for ref in agent_memory_refs
                    ]
                    if self._is_final_task(task):
                        agent_deliverable_view = self.memory_store.render_deliverable_view(
                            agent_memory_refs,
                            task_title=task.title,
                            budget_chars=self.DELIVERABLE_VIEW_BUDGET_CHARS,
                        )

            memory_prompt_views = (
                agent_memory_prompt_views
                if mode == "runtime_lite" and agent.agent_id in {"planner", "writer"}
                else []
            )

            prompt = self._build_prompt(
                task,
                context,
                mode,
                round_id=round_id,
                state_refs=state_refs,
                memory_prompt_views=memory_prompt_views,
                deliverable_view=agent_deliverable_view
                if mode == "runtime_lite" and self._is_final_deliverable_agent(agent)
                else "",
                deliverable_schema_prompt=deliverable_schema_prompt
                if mode == "runtime_lite" and self._is_final_deliverable_agent(agent)
                else "",
                agent_role=agent.role,
            )
            self.metrics.record_prompt(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                agent_id=agent.agent_id,
                prompt=prompt,
                token_counter=self.token_counter,
            )
            self.trace.write(
                "agent_invoked",
                {
                    "task_id": task.task_id,
                    "round_id": round_id,
                    "mode": mode,
                    "agent_id": agent.agent_id,
                    "prompt_chars": len(prompt),
                },
            )

            if getattr(agent, "expects_runtime_prompt", False):
                agent_context = [AgentOutput(agent_id="runtime_prompt", content=prompt)]
            elif mode == "runtime_lite":
                agent_context = lite_context + [
                    AgentOutput(agent_id="runtime_prompt_view", content=prompt)
                ]
            else:
                agent_context = context

            output = agent.run(task, agent_context)
            if mode == "runtime_lite" and getattr(
                agent, "expects_runtime_prompt", False
            ):
                output = self._apply_contract_guard(
                    task=task,
                    round_id=round_id,
                    mode=mode,
                    agent=agent,
                    output=output,
                )
            if mode == "runtime_lite" and deliverable_schema and agent.agent_id in {
                "writer",
                "reviewer",
            }:
                hit_count, required_count, missing_fields = schema_field_coverage(
                    deliverable_schema, output.content
                )
                self.metrics.record_deliverable_schema(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    hit_count=hit_count,
                    required_count=required_count,
                )
                if agent.agent_id == "reviewer" and missing_fields:
                    retry_prompt = self._build_schema_retry_prompt(
                        task=task,
                        reviewer_output=output.content,
                        missing_fields=missing_fields,
                        deliverable_view=deliverable_view,
                        deliverable_schema_prompt=deliverable_schema_prompt,
                    )
                    retry_output = agent.run(
                        task,
                        [AgentOutput(agent_id="runtime_prompt", content=retry_prompt)],
                    )
                    retry_llm_meta = retry_output.metadata.get("llm", {})
                    if retry_llm_meta:
                        self.metrics.record_llm_call(
                            task_id=task.task_id,
                            round_id=round_id,
                            mode=mode,
                            usage=retry_llm_meta.get("usage", {}),
                            latency_ms=float(
                                retry_llm_meta.get("latency_ms", 0.0) or 0.0
                            ),
                        )
                    self.metrics.record_final_quality_retry(
                        task_id=task.task_id,
                        round_id=round_id,
                        mode=mode,
                        retry_prompt=retry_prompt,
                        retry_output=retry_output.content,
                        token_counter=self.token_counter,
                    )
                    self.trace.write(
                        "final_quality_retry",
                        {
                            "task_id": task.task_id,
                            "round_id": round_id,
                            "mode": mode,
                            "agent_id": agent.agent_id,
                            "missing_fields": missing_fields,
                            "retry_output_chars": len(retry_output.content),
                        },
                    )
                    output = AgentOutput(
                        agent_id=output.agent_id,
                        content=(
                            f"{output.content}\n\n"
                            "【Final Schema 修复补丁】\n"
                            f"{retry_output.content}"
                        ),
                        metadata=output.metadata,
                    )
                    hit_count, required_count = schema_coverage(
                        deliverable_schema, output.content
                    )
                    self.metrics.record_deliverable_schema(
                        task_id=task.task_id,
                        round_id=round_id,
                        mode=mode,
                        hit_count=hit_count,
                        required_count=required_count,
                    )
            if self._is_final_deliverable_agent(agent):
                final_deliverable_output = output
            llm_meta = output.metadata.get("llm", {})
            if llm_meta:
                self.metrics.record_llm_call(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    usage=llm_meta.get("usage", {}),
                    latency_ms=float(llm_meta.get("latency_ms", 0.0) or 0.0),
                )
                self.metrics.record_provider_guard(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    provider_guard=llm_meta.get("provider_guard"),
                )
            context.append(output)
            current_task_outputs.append(output)

            output_state_refs: list[StateRef] = []
            output_memory_refs: list[MemoryRef] = (
                list(agent_memory_refs) if mode == "runtime_lite" else []
            )
            message_content = output.content
            if mode == "runtime_lite":
                written_state_refs = self._write_agent_state(
                    task, round_id, mode, agent, output
                )
                output_state_refs.extend(written_state_refs)
                state_refs.extend(written_state_refs)

                if agent.agent_id in {"writer", "reviewer", "memory_manager"}:
                    self._schedule_runtime_memory_write(
                        task=task,
                        round_id=round_id,
                        mode=mode,
                        agent=agent,
                        output=output,
                        state_refs=state_refs,
                        output_state_refs=output_state_refs,
                    )

                message_content = self._build_shp_message(
                    task=task,
                    round_id=round_id,
                    mode=mode,
                    from_agent=agent.agent_id,
                    next_receiver=self._next_agent_id(agent.agent_id),
                    summary=self._summary(output.content, self.HANDOFF_SUMMARY_CHARS),
                    state_refs=output_state_refs,
                    memory_refs=output_memory_refs,
                )
                lite_context.append(
                    AgentOutput(
                        agent_id=agent.agent_id,
                        content=(
                            f"{agent.agent_id} 完成阶段输出；"
                            f"state_refs={[ref.state_id for ref in output_state_refs]}; "
                            f"memory_refs={[ref.memory_id for ref in output_memory_refs]}; "
                            f"summary={self._summary(output.content, self.LITE_CONTEXT_SUMMARY_CHARS)}"
                        ),
                    )
                )

            message = RuntimeMessage(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                sender=previous_sender,
                receiver=agent.agent_id,
                content=message_content,
                state_refs=[ref.state_id for ref in output_state_refs],
                memory_refs=[ref.memory_id for ref in output_memory_refs],
                cost_report=self._build_message_cost_report(
                    message_content=message_content,
                    prompt=prompt,
                    memory_prompt_views=memory_prompt_views,
                ),
            )
            self.metrics.record_message(message, self.token_counter)
            self.trace.write("message_sent", asdict(message))
            self.trace.write(
                "agent_output_received",
                {
                    "task_id": task.task_id,
                    "round_id": round_id,
                    "mode": mode,
                    "agent_id": output.agent_id,
                    "content_chars": len(output.content),
                    "state_refs": [asdict(ref) for ref in output_state_refs],
                    "memory_refs": [asdict(ref) for ref in output_memory_refs],
                },
            )
            if mode == "runtime_lite":
                gate = self._handoff_gate(message_content)
                if not gate.get("allowed", False):
                    self.trace.write(
                        "communication_handoff_blocked",
                        {
                            "task_id": task.task_id,
                            "round_id": round_id,
                            "sender": agent.agent_id,
                            "allowed_next_step": gate.get("allowed_next_step", ""),
                            "reasons": gate.get("reasons", []),
                        },
                    )
                    raise RuntimeError(
                        "Communication gate blocked handoff: "
                        f"{gate.get('allowed_next_step', 'review_required')}"
                    )
            previous_sender = agent.agent_id

        if mode == "runtime_lite" and memory_refs_used:
            self.metrics.record_memory_supported_output(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                count=len(memory_refs_used),
            )
        if mode == "baseline_text":
            self._baseline_full_history[history_key] = full_history + current_task_outputs
        if mode == "runtime_lite":
            gc_report = self.state_pool.collect_garbage(
                protected_state_ids={
                    ref.state_id
                    for ref in state_refs[-self.GC_PROTECTED_RECENT_STATE_COUNT :]
                },
                max_deleted=2,
            )
            self.state_pool.sweep_tombstones(max_swept=2)
            self.metrics.record_state_gc(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                state_gc_count=gc_report.state_gc_count,
                read_lease_blocked_gc_count=gc_report.read_lease_blocked_gc_count,
                state_tombstone_count=gc_report.state_tombstone_count,
            )

        elapsed_ms = (time.perf_counter() - started) * 1000
        self.metrics.finish_task(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            latency_ms=elapsed_ms,
            success=True,
        )
        self.trace.write(
            "task_finished",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "latency_ms": elapsed_ms,
                "success": True,
                "final_deliverable_agent": (
                    final_deliverable_output.agent_id
                    if final_deliverable_output is not None
                    else context[-1].agent_id
                ),
            },
        )
        if final_deliverable_output is not None:
            self.trace.write(
                "final_deliverable_selected",
                {
                    "task_id": task.task_id,
                    "round_id": round_id,
                    "mode": mode,
                    "agent_id": final_deliverable_output.agent_id,
                    "content_chars": len(final_deliverable_output.content),
                },
            )
        return final_deliverable_output or context[-1]

    def flush_background_tasks(self) -> None:
        self._drain_background_memory_jobs(reason="benchmark_finished")

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.flush_background_tasks()
        finally:
            self._background_executor.shutdown(wait=True, cancel_futures=False)
            self._baseline_full_history.clear()
            self._closed = True

    def __enter__(self) -> "V0Runtime":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.close()

    def _schedule_runtime_memory_write(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: DeterministicAgent,
        output: AgentOutput,
        state_refs: list[StateRef],
        output_state_refs: list[StateRef],
    ) -> None:
        source_state_ids = [ref.state_id for ref in state_refs[-4:]]
        evidence_refs = [ref.state_id for ref in output_state_refs]
        self.state_pool.mark_lineage(source_state_ids + evidence_refs)
        future = self._background_executor.submit(
            self._write_runtime_memory,
            task=task,
            round_id=round_id,
            mode=mode,
            agent=agent,
            output=output,
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
        )
        with self._background_jobs_lock:
            self._background_memory_jobs.append(
                BackgroundMemoryJob(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    agent_id=agent.agent_id,
                    future=future,
                    scheduled_at=time.perf_counter(),
                )
            )
        self.metrics.record_background_memory_job(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            scheduled_count=1,
            completed_count=0,
            error_count=0,
            wait_ms=0.0,
        )
        self.trace.write(
            "background_memory_scheduled",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "agent_id": agent.agent_id,
                "source_state_ids": source_state_ids,
                "evidence_refs": evidence_refs,
            },
        )

    def _drain_background_memory_jobs(self, *, reason: str) -> None:
        with self._background_jobs_lock:
            if not self._background_memory_jobs:
                return
            jobs = self._background_memory_jobs
            self._background_memory_jobs = []
        self.trace.write(
            "background_memory_flush_started",
            {
                "reason": reason,
                "job_count": len(jobs),
            },
        )
        for job in jobs:
            wait_started = time.perf_counter()
            try:
                result = job.future.result()
            except Exception as exc:
                wait_ms = (time.perf_counter() - wait_started) * 1000
                self.metrics.record_background_memory_job(
                    task_id=job.task_id,
                    round_id=job.round_id,
                    mode=job.mode,
                    scheduled_count=0,
                    completed_count=0,
                    error_count=1,
                    wait_ms=wait_ms,
                )
                self.trace.write(
                    "background_memory_failed",
                    {
                        "task_id": job.task_id,
                        "round_id": job.round_id,
                        "mode": job.mode,
                        "agent_id": job.agent_id,
                        "reason": reason,
                        "error": repr(exc),
                        "wait_ms": wait_ms,
                    },
                )
                continue
            wait_ms = (time.perf_counter() - wait_started) * 1000
            self._record_runtime_memory_result(result)
            self.metrics.record_background_memory_job(
                task_id=job.task_id,
                round_id=job.round_id,
                mode=job.mode,
                scheduled_count=0,
                completed_count=1,
                error_count=0,
                wait_ms=wait_ms,
            )
            self.trace.write(
                "background_memory_committed",
                {
                    "task_id": job.task_id,
                    "round_id": job.round_id,
                    "mode": job.mode,
                    "agent_id": job.agent_id,
                    "reason": reason,
                    "memory_refs": [ref.memory_id for ref in result.memory_refs],
                    "wait_ms": wait_ms,
                },
            )

    def _record_runtime_memory_result(self, result: RuntimeMemoryWriteResult) -> None:
        if result.admission_report is not None:
            admission_report = result.admission_report
            self.metrics.record_memory_admission(
                task_id=result.task_id,
                round_id=result.round_id,
                mode=result.mode,
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
                task_id=result.task_id,
                round_id=result.round_id,
                mode=result.mode,
                memory_write_count=admission_report.memory_write_count,
                claim_card_count=admission_report.claim_card_count,
                memory_view_count=admission_report.memory_view_count,
                promotion_view_count=admission_report.promotion_view_count,
                alias_mapping_hit_count=admission_report.alias_mapping_hit_count,
                unresolved_slot_count=admission_report.unresolved_slot_count,
            )
            return

        if result.write_report is not None:
            write_report = result.write_report
            self.metrics.record_memory_write(
                task_id=result.task_id,
                round_id=result.round_id,
                mode=result.mode,
                memory_write_count=write_report.memory_write_count,
                claim_card_count=write_report.claim_card_count,
                memory_view_count=write_report.memory_view_count,
                promotion_view_count=write_report.promotion_view_count,
                alias_mapping_hit_count=write_report.alias_mapping_hit_count,
                unresolved_slot_count=write_report.unresolved_slot_count,
            )

    def _write_runtime_memory(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: DeterministicAgent,
        output: AgentOutput,
        source_state_ids: list[str],
        evidence_refs: list[str],
    ) -> RuntimeMemoryWriteResult:
        tags = [task.group_id, task.task_id, agent.agent_id]
        reuse_intent = f"供 {task.group_id} 后续连续任务复用"
        contract_guard = output.metadata.get("contract_guard")
        control: dict | None = None
        degraded = False

        if isinstance(contract_guard, dict):
            control = contract_guard.get("control", {})
            degraded = contract_guard.get("contract_status") == "degraded_fallback"
            if (
                not contract_guard.get("schema_valid")
                or degraded
                or not isinstance(control, dict)
            ):
                control = {}

        admission_report, validation = self.state_memory_bridge.promote(
            task_id=task.task_id,
            source_agent=agent.agent_id,
            task_topic=task.title,
            fallback_summary=self._summary(output.content, 180),
            tags=tags,
            slot_hint=self._slot_hint_for_task(task, agent.agent_id),
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            reuse_intent=reuse_intent,
            control=control,
            degraded=degraded,
        )
        self.trace.write(
            "state_memory_bridge",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "agent_id": agent.agent_id,
                "validation_allowed": validation.allowed,
                "validation_reasons": validation.reasons,
                "admission_status": admission_report.admission_status,
                "candidate_id": admission_report.candidate_id,
                "memory_ref": (
                    admission_report.memory_ref.memory_id
                    if admission_report.memory_ref is not None
                    else None
                ),
            },
        )
        return RuntimeMemoryWriteResult(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            agent_id=agent.agent_id,
            memory_refs=(
                [admission_report.memory_ref]
                if admission_report.memory_ref is not None
                else []
            ),
            source_state_ids=source_state_ids,
            evidence_refs=evidence_refs,
            admission_report=admission_report,
        )

    def _apply_retry_budget(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        retry_prompt: str,
    ) -> str:
        token_count = self.token_counter.count(retry_prompt)
        budget_exhausted = token_count.token_count > self.MAX_FORMAT_RETRY_INPUT_TOKENS
        if not budget_exhausted:
            self.metrics.record_retry_budget(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                retry_input_tokens=token_count.token_count,
                budget_exhausted=False,
            )
            return retry_prompt

        ratio = self.MAX_FORMAT_RETRY_INPUT_TOKENS / max(1, token_count.token_count)
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

    def _apply_contract_guard(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: DeterministicAgent,
        output: AgentOutput,
    ) -> AgentOutput:
        context = ContractContext(
            task_id=task.task_id,
            agent_id=agent.agent_id,
            role=agent.role,
            next_action=self._next_agent_id(agent.agent_id),
            artifact_ref=f"cold://{task.task_id}/{round_id}/{agent.agent_id}/artifact",
        )
        result = guard_agent_output(output.content, context)
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

        if result.retry_required:
            result.retry_attempted = True
            retry_prompt = render_contract_retry_prompt(context=context, result=result)
            retry_prompt = self._apply_retry_budget(
                task=task,
                round_id=round_id,
                mode=mode,
                retry_prompt=retry_prompt,
            )
            retry_output = agent.run(
                task,
                [AgentOutput(agent_id="runtime_prompt", content=retry_prompt)],
            )
            retry_llm_meta = retry_output.metadata.get("llm", {})
            if retry_llm_meta:
                self.metrics.record_llm_call(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    usage=retry_llm_meta.get("usage", {}),
                    latency_ms=float(retry_llm_meta.get("latency_ms", 0.0) or 0.0),
                )
                self.metrics.record_provider_guard(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    provider_guard=retry_llm_meta.get("provider_guard"),
                )
            self.metrics.record_retry_tokens(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                retry_prompt=retry_prompt,
                retry_output=retry_output.content,
                token_counter=self.token_counter,
            )
            retry_result = guard_agent_output(
                retry_output.content,
                ContractContext(
                    task_id=task.task_id,
                    agent_id=agent.agent_id,
                    role=agent.role,
                    next_action=self._next_agent_id(agent.agent_id),
                    artifact_ref=context.artifact_ref,
                ),
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
        )

        metadata = dict(output.metadata)
        metadata["contract_guard"] = result.to_dict()
        return AgentOutput(
            agent_id=output.agent_id,
            content=result.artifact,
            metadata=metadata,
        )

    def _build_prompt(
        self,
        task: TaskSpec,
        context: list[AgentOutput],
        mode: Mode,
        round_id: int,
        state_refs: list[StateRef] | None = None,
        memory_prompt_views: list[str] | None = None,
        deliverable_view: str = "",
        deliverable_schema_prompt: str = "",
        agent_role: str = "",
    ) -> str:
        if mode == "baseline_text":
            previous = "\n\n".join(item.content for item in context)
            return f"任务：{task.prompt}\n\n完整上游上下文：\n{previous}"

        if mode == "runtime_lite":
            state_views = []
            for ref in (state_refs or [])[-5:]:
                escalation_report = self.state_pool.request_progressive_access(
                    ref,
                    agent_role=agent_role,
                    reason="runtime_prompt_view",
                    need_raw=False,
                    budget_chars=700,
                )
                state_views.append(escalation_report.prompt_view)
                raw_access_count = int(
                    escalation_report.cold_access is not None
                    and escalation_report.cold_access.allowed
                )
                self.metrics.record_state_access(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    raw_access_count=raw_access_count,
                    summary_access_count=int(
                        escalation_report.selected_level
                        in {"summary", "summary_only", "metadata"}
                    ),
                    evidence_snippet_access_count=(
                        int(escalation_report.selected_level == "evidence_snippets")
                    ),
                    access_escalation_count=(
                        escalation_report.access_escalation_count
                    ),
                    read_lease_acquire_count=1 + raw_access_count,
                )
            memory_block = "\n".join(memory_prompt_views or [])
            state_block = "\n\n".join(state_views)
            deliverable_block = (
                f"\n\nDeliverable View:\n{deliverable_view}"
                if deliverable_view
                else ""
            )
            schema_block = (
                f"\n\n{deliverable_schema_prompt}" if deliverable_schema_prompt else ""
            )
            return (
                f"任务：{task.prompt}\n\n"
                "runtime_lite 控制字段：下游仅通过 Prompt View 读取状态和记忆。\n"
                f"Memory Prompt View:\n{memory_block or '无可复用记忆'}\n\n"
                f"State Prompt View:\n{state_block or '无上游状态'}"
                f"{deliverable_block}"
                f"{schema_block}"
            )

        previous = "\n\n".join(
            f"agent={item.agent_id}; state_refs=[]; memory_refs=[]; summary={item.content[:240]}"
            for item in context
        )
        return (
            f"任务：{task.prompt}\n\n"
            "runtime_stub_mode 控制字段：state_refs=[]; memory_refs=[]\n"
            f"轻量上游摘要：\n{previous}"
        )

    def _build_schema_retry_prompt(
        self,
        *,
        task: TaskSpec,
        reviewer_output: str,
        missing_fields: list[str],
        deliverable_view: str,
        deliverable_schema_prompt: str,
    ) -> str:
        missing = ", ".join(missing_fields)
        return (
            f"任务：{task.prompt}\n\n"
            "这是一次 Context-Pruned Final Schema Retry，只允许根据下方裁剪上下文补齐最终交付缺项，"
            "不要展开原始全量历史。\n\n"
            f"缺失字段：{missing}\n\n"
            f"{deliverable_schema_prompt}\n\n"
            f"Deliverable View:\n{deliverable_view or '无'}\n\n"
            f"Reviewer 初稿：\n{reviewer_output[:900]}\n\n"
            "请输出一段可直接附加到最终交付中的修复补丁，逐项覆盖缺失字段；"
            "每个缺失项都必须显式写出字段名或中文字段标签。"
        )

    def _write_agent_state(
        self,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: DeterministicAgent,
        output: AgentOutput,
    ) -> list[StateRef]:
        def commit_state(
            *,
            state_type: str,
            payload: dict,
            summary: str,
            usage_hint: str,
            contains_embedding_refs: bool,
            tier: str,
            access_policy: str,
            audit_payload: dict | None = None,
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
            payload = {
                "error_type": "contract_violation",
                "contract_status": "degraded_fallback",
                "schema_errors": contract_guard.get("schema_errors", []),
                "allowed_next_step": "review_or_retry_only",
                "artifact_digest": contract_guard.get("artifact_digest", {}),
            }
            state_type = "failure_state"
            summary = (
                f"{agent.agent_id} 输出契约降级："
                f"{'; '.join(contract_guard.get('schema_errors', [])[:2])}"
            )
            usage_hint = "review_or_retry_only"
            tier = "hot"
            access_policy = "prompt_view_only"
            audit_payload = {
                "content": output.content,
                "contract_guard": contract_guard,
                "content_chars": len(output.content),
            }
            return [
                commit_state(
                    state_type=state_type,
                    payload=payload,
                    summary=summary,
                    usage_hint=usage_hint,
                    contains_embedding_refs=False,
                    tier=tier,
                    access_policy=access_policy,
                    audit_payload=audit_payload,
                )
            ]
        elif agent.agent_id == "retriever":
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
            payload = build_retrieval_state_payload(
                task, embedding_state_id=embedding_ref.state_id
            )
            retrieval_ref = commit_state(
                state_type="retrieval_state",
                payload=payload,
                summary=f"{task.title} 的检索状态，包含 {len(task.documents)} 条证据和排序分数。",
                usage_hint="summary_context_selection",
                contains_embedding_refs=True,
                tier="hot",
                access_policy="prompt_view_only",
            )
            return [embedding_ref, retrieval_ref]
        else:
            artifact_id = f"artifact_{task.task_id}_{round_id}_{agent.agent_id}"
            sha256 = hashlib.sha256(output.content.encode("utf-8")).hexdigest()
            payload = {
                "code_artifact_id": artifact_id,
                "artifact_id": artifact_id,
                "stdout_ref": None,
                "stderr_ref": None,
                "file_path": None,
                "sha256": sha256,
                "summary": self._summary(
                    output.content, self.ARTIFACT_PAYLOAD_SUMMARY_CHARS
                ),
            }
            state_type = "artifact_state"
            summary = (
                f"{agent.agent_id} 产物状态："
                f"{self._summary(output.content, self.ARTIFACT_STATE_SUMMARY_CHARS)}"
            )
            usage_hint = "artifact_summary"
            tier = "cold"
            access_policy = "prompt_view_with_audit_cold_access"
            audit_payload = {
                "artifact_id": artifact_id,
                "sha256": sha256,
                "content": output.content,
                "content_chars": len(output.content),
            }
        return [
            commit_state(
                state_type=state_type,
                payload=payload,
                summary=summary,
                usage_hint=usage_hint,
                contains_embedding_refs=False,
                tier=tier,
                access_policy=access_policy,
                audit_payload=audit_payload,
            )
        ]

    def _build_shp_message(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        from_agent: str,
        next_receiver: str,
        summary: str,
        state_refs: list[StateRef],
        memory_refs: list[MemoryRef],
    ) -> str:
        readiness_report = self.readiness_barrier.assess(
            state_refs=state_refs,
            degraded=any(ref.state_type == "failure_state" for ref in state_refs),
        )
        route_decision = self.capability_router.route(
            sender=from_agent,
            declared_receiver=next_receiver,
            state_refs=state_refs,
            readiness=readiness_report.readiness,
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
            sender=from_agent,
            receiver=route_decision.receiver,
            summary=summary,
            action=f"{from_agent}_completed",
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

    def _next_agent_id(self, agent_id: str) -> str:
        ids = [agent.agent_id for agent in self.agents]
        try:
            index = ids.index(agent_id)
        except ValueError:
            return "runtime"
        return ids[index + 1] if index + 1 < len(ids) else "runtime"

    def _is_final_task(self, task: TaskSpec) -> bool:
        title = task.title.lower()
        explicit_final_id = re.search(r"(?:^|[^0-9])10$", task.task_id) is not None
        return explicit_final_id or "最终" in task.title or "final" in title

    def _is_final_deliverable_agent(self, agent: DeterministicAgent) -> bool:
        profile = self.capability_profiles.get(agent.agent_id)
        if profile is None:
            return False
        return bool(
            {"final_deliverable_draft", "final_deliverable_review"}
            & profile.capabilities
        )

    def _build_message_cost_report(
        self,
        *,
        message_content: str,
        prompt: str,
        memory_prompt_views: list[str],
    ) -> dict[str, int]:
        memory_text = "\n".join(memory_prompt_views)
        return {
            "direct_text_tokens": self.token_counter.count(message_content).token_count,
            "prompt_tokens": self.token_counter.count(prompt).token_count,
            "retrieved_memory_tokens": (
                self.token_counter.count(memory_text).token_count if memory_text else 0
            ),
            "control_llm_tokens": 0,
        }

    @staticmethod
    def _handoff_gate(message_content: str) -> dict[str, object]:
        payload = json.loads(message_content)
        metrics = payload.get("metrics", {})
        if not isinstance(metrics, dict):
            return {}
        gate = metrics.get("communication_gate", {})
        return gate if isinstance(gate, dict) else {}

    def _background_job_count(self) -> int:
        with self._background_jobs_lock:
            return len(self._background_memory_jobs)

    def _slot_hint_for_task(self, task: TaskSpec, agent_id: str) -> str:
        if self._is_final_task(task):
            return "final_deliverable"
        group = task.group_id.lower()
        if "travel" in group or task.task_id.startswith("A"):
            return "travel_preference" if agent_id != "reviewer" else "reuse_strategy"
        if "security" in group or task.task_id.startswith("B"):
            return "security_audit" if agent_id != "reviewer" else "failure_reason"
        return "reuse_strategy"

    @staticmethod
    def _summary(text: str, limit: int) -> str:
        compact = " ".join(text.split())
        return compact[:limit]
