from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import json
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Iterable

from agent_runtime.core.agents import DeterministicAgent
from agent_runtime.core.deliverable_schema import (
    render_schema_prompt,
    schema_field_coverage,
    schema_coverage,
    schema_for_task,
)
from agent_runtime.core.kernel import (
    AgentDescriptor,
    CollaborationKernel,
    MemoryContext,
)
from agent_runtime.core.models import AgentOutput, Mode, RuntimeMessage, TaskSpec
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.memory_store import (
    MemoryAdmissionReport,
    MemoryRef,
    MemoryStoreLite,
    MemoryWriteReport,
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


@dataclass(slots=True)
class BoundedBaselineEntry:
    task_id: str
    group_id: str
    title: str
    final_answer: str
    bounded_summary: str
    key_messages: list[str]


class V0Runtime:
    """Deterministic v0 runtime for baseline measurement."""

    MAX_FORMAT_RETRY_INPUT_TOKENS = 800
    DELIVERABLE_VIEW_BUDGET_CHARS = 2200
    HANDOFF_SUMMARY_CHARS = 160
    LITE_CONTEXT_SUMMARY_CHARS = 180
    ARTIFACT_PAYLOAD_SUMMARY_CHARS = 240
    ARTIFACT_STATE_SUMMARY_CHARS = 120
    GC_PROTECTED_RECENT_STATE_COUNT = 6
    BOUNDED_LOCAL_CONTEXT_BUDGET_TOKENS = 8000
    BOUNDED_CROSS_CONTEXT_BUDGET_TOKENS = 1200
    BOUNDED_RECENT_KEY_MESSAGES_K = 2
    BOUNDED_SUMMARY_MAX_TOKENS = 500

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
        self.kernel = CollaborationKernel(
            agents=self.agents,
            token_counter=token_counter,
            metrics=metrics,
            trace=trace,
            state_pool=state_pool,
            memory_store=memory_store,
        )
        # Compatibility aliases keep existing experiments and extensions stable
        # while framework-neutral behavior moves behind CollaborationKernel.
        self.state_pool = self.kernel.state_pool
        self.memory_store = self.kernel.memory_store
        self.state_memory_bridge = self.kernel.state_memory_bridge
        self.readiness_barrier = self.kernel.readiness_barrier
        self.capability_profiles = self.kernel.capability_profiles
        self.capability_router = self.kernel.capability_router
        self.communication_gate = self.kernel.communication_gate
        self.control_budget = self.kernel.control_budget
        self._baseline_full_history: dict[tuple[int, str], list[AgentOutput]] = {}
        self._bounded_histories: dict[tuple[int, str], list[BoundedBaselineEntry]] = {}
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
            self.kernel.finalize_task(task.task_id)

    def _run_task_impl(self, task: TaskSpec, round_id: int, mode: Mode) -> AgentOutput:
        started = time.perf_counter()
        stress_history_key = (round_id, mode)
        full_history = (
            self._baseline_full_history.get(stress_history_key, [])
            if self._is_stress_baseline_mode(mode)
            else []
        )
        bounded_history_key = (round_id, self._task_thread_key(task))
        bounded_history = (
            self._bounded_histories.get(bounded_history_key, [])
            if self._is_bounded_baseline_mode(mode)
            else []
        )
        if self._is_stress_baseline_mode(mode):
            context: list[AgentOutput] = list(full_history)
        elif self._is_bounded_baseline_mode(mode):
            context = self._build_bounded_cross_task_context(bounded_history)
        else:
            context = []
        current_task_outputs: list[AgentOutput] = []
        self.trace.write(
            "task_started",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "title": task.title,
                "baseline_full_history_items": len(full_history)
                if self._is_stress_baseline_mode(mode)
                else 0,
                "bounded_history_items": len(bounded_history),
                "thread_key": self._task_thread_key(task),
            },
        )

        previous_sender = "user"
        state_refs: list[StateRef] = []
        memory_refs_used: list[MemoryRef] = []
        lite_context: list[AgentOutput] = []
        final_deliverable_output: AgentOutput | None = None
        draft_answer_output: AgentOutput | None = None
        review_report_output: AgentOutput | None = None
        final_answer_output: AgentOutput | None = None
        task_memory_refs: list[MemoryRef] = []
        task_memory_prompt_views: list[str] = []
        deliverable_view = ""
        deliverable_schema = schema_for_task(task)
        deliverable_schema_prompt = render_schema_prompt(deliverable_schema)
        if mode == "runtime_lite":
            self._drain_background_memory_jobs(reason="before_memory_search")
            memory_context = self.kernel.prepare_memory_context(
                task=task,
                round_id=round_id,
                mode=mode,
                final_task=self._is_final_task(task),
                deliverable_budget_chars=self.DELIVERABLE_VIEW_BUDGET_CHARS,
            )
            task_memory_refs = memory_context.refs
            task_memory_prompt_views = memory_context.prompt_views
            deliverable_view = memory_context.deliverable_view
            memory_refs_used.extend(task_memory_refs)

        for agent in self.agents:
            self.metrics.record_agent_role(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                agent_id=agent.agent_id,
                role=agent.role,
            )
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
                agent_id=agent.agent_id,
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
                schema_check_started = time.perf_counter()
                hit_count, required_count, missing_fields = schema_field_coverage(
                    deliverable_schema, output.content
                )
                self.metrics.record_agent_local_timing(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    agent_id=agent.agent_id,
                    schema_check_ms=(time.perf_counter() - schema_check_started)
                    * 1000,
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
                            agent_id=agent.agent_id,
                            output_chars=len(retry_output.content),
                        )
                        self.metrics.record_provider_guard(
                            task_id=task.task_id,
                            round_id=round_id,
                            mode=mode,
                            provider_guard=retry_llm_meta.get("provider_guard"),
                            agent_id=agent.agent_id,
                        )
                    self.metrics.record_agent_retry(
                        task_id=task.task_id,
                        round_id=round_id,
                        mode=mode,
                        agent_id=agent.agent_id,
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
                    schema_check_started = time.perf_counter()
                    hit_count, required_count = schema_coverage(
                        deliverable_schema, output.content
                    )
                    self.metrics.record_agent_local_timing(
                        task_id=task.task_id,
                        round_id=round_id,
                        mode=mode,
                        agent_id=agent.agent_id,
                        schema_check_ms=(time.perf_counter() - schema_check_started)
                        * 1000,
                    )
                    self.metrics.record_deliverable_schema(
                        task_id=task.task_id,
                        round_id=round_id,
                        mode=mode,
                        hit_count=hit_count,
                        required_count=required_count,
                    )
            deliverable_role = self._deliverable_role_for_agent(agent)
            if deliverable_role == "draft_answer":
                draft_answer_output = output
                if self._is_final_task(task):
                    final_answer_output = output
            elif deliverable_role == "review_report":
                review_report_output = output
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
                    agent_id=agent.agent_id,
                    output_chars=len(output.content),
                )
                self.metrics.record_provider_guard(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    provider_guard=llm_meta.get("provider_guard"),
                    agent_id=agent.agent_id,
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
            self._write_pool_snapshot(task=task, round_id=round_id, mode=mode)
            previous_sender = agent.agent_id

        if mode == "runtime_lite" and memory_refs_used:
            self.metrics.record_memory_supported_output(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                count=len(memory_refs_used),
            )
        selected_output = self._select_deliverable_output(
            task=task,
            fallback_output=final_deliverable_output or context[-1],
            draft_answer_output=draft_answer_output,
            review_report_output=review_report_output,
            final_answer_output=final_answer_output,
        )
        if self._is_stress_baseline_mode(mode):
            self._baseline_full_history[stress_history_key] = (
                full_history + current_task_outputs
            )
        if self._is_bounded_baseline_mode(mode):
            bounded_entry = self._build_bounded_history_entry(
                task=task,
                round_id=round_id,
                mode=mode,
                selected_output=selected_output,
                current_task_outputs=current_task_outputs,
            )
            self._bounded_histories[bounded_history_key] = (
                bounded_history + [bounded_entry]
            )
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
                    selected_output.agent_id
                ),
                "deliverable_role": selected_output.metadata.get(
                    "deliverable_role", ""
                ),
            },
        )
        if selected_output is not None:
            self.trace.write(
                "final_deliverable_selected",
                {
                    "task_id": task.task_id,
                    "round_id": round_id,
                    "mode": mode,
                    "agent_id": selected_output.agent_id,
                    "deliverable_role": selected_output.metadata.get(
                        "deliverable_role", ""
                    ),
                    "content_chars": len(selected_output.content),
                },
            )
        self._write_pool_snapshot(task=task, round_id=round_id, mode=mode)
        return selected_output

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
            self._bounded_histories.clear()
            self.kernel.close()
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
                self.metrics.record_agent_local_timing(
                    task_id=job.task_id,
                    round_id=job.round_id,
                    mode=job.mode,
                    agent_id="local_runtime",
                    local_memory_search_ms=wait_ms,
                )
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
            self.metrics.record_agent_local_timing(
                task_id=job.task_id,
                round_id=job.round_id,
                mode=job.mode,
                agent_id="local_runtime",
                local_memory_search_ms=wait_ms,
            )
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

    def _write_pool_snapshot(self, *, task: TaskSpec, round_id: int, mode: Mode) -> None:
        memory_snapshot = (
            {
                "status": "background_memory_pending",
                "pending_job_count": self._background_job_count(),
            }
            if self._background_job_count()
            else self.memory_store.snapshot()
        )
        snapshot = {
            "task_id": task.task_id,
            "round_id": round_id,
            "mode": mode,
            "state_pool": self.state_pool.snapshot(),
            "memory_store": memory_snapshot,
        }
        path = self.trace.output_dir / "pool_snapshot_latest.json"
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

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
        return self.kernel.apply_retry_budget(
            task=task,
            round_id=round_id,
            mode=mode,
            retry_prompt=retry_prompt,
        )

    def _apply_contract_guard(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: DeterministicAgent,
        output: AgentOutput,
    ) -> AgentOutput:
        return self.kernel.validate_agent_output(
            task=task,
            round_id=round_id,
            mode=mode,
            agent=self._agent_descriptor(agent),
            next_action=self._next_agent_id(agent.agent_id),
            output=output,
            retry_output=lambda retry_prompt: agent.run(
                task,
                [AgentOutput(agent_id="runtime_prompt", content=retry_prompt)],
            ),
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
        agent_id: str = "",
    ) -> str:
        if self._is_stress_baseline_mode(mode):
            previous = "\n\n".join(item.content for item in context)
            return f"任务：{task.prompt}\n\n完整上游上下文：\n{previous}"

        if self._is_bounded_baseline_mode(mode):
            previous = "\n\n".join(
                f"[{item.agent_id}]\n{item.content}" for item in context
            )
            previous = self._truncate_to_token_budget(
                previous,
                self.BOUNDED_LOCAL_CONTEXT_BUDGET_TOKENS,
            )
            return (
                f"任务：{task.prompt}\n\n"
                "baseline_bounded_nl_framework：当前 task 内可以共享自然语言上下文；"
                "跨 task 只能看到上一轮最终交付、bounded summary 和少量 key messages。\n\n"
                f"有界上下文：\n{previous or '无可用跨任务上下文'}"
            )

        if mode == "runtime_lite":
            prepared = self.kernel.before_agent_receive(
                task=task,
                round_id=round_id,
                mode=mode,
                agent=AgentDescriptor(
                    agent_id=agent_id or agent_role or "unknown",
                    role=agent_role or agent_id or "UnknownAgent",
                ),
                state_refs=list(state_refs or []),
                memory_context=MemoryContext(
                    refs=[],
                    prompt_views=list(memory_prompt_views or []),
                    deliverable_view=deliverable_view,
                ),
            )
            memory_block = "\n".join(prepared.memory_prompt_views)
            state_block = "\n\n".join(prepared.state_prompt_views)
            deliverable_block = (
                f"\n\nDeliverable View:\n{prepared.deliverable_view}"
                if prepared.deliverable_view
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
        return self.kernel.write_agent_state(
            task=task,
            round_id=round_id,
            mode=mode,
            agent=self._agent_descriptor(agent),
            output=output,
        )

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
        return self.kernel.build_handoff(
            task=task,
            round_id=round_id,
            mode=mode,
            sender=from_agent,
            declared_receiver=next_receiver,
            summary=summary,
            state_refs=state_refs,
            memory_refs=memory_refs,
        )

    def _build_bounded_cross_task_context(
        self, entries: list[BoundedBaselineEntry]
    ) -> list[AgentOutput]:
        if not entries:
            return []
        remaining = self.BOUNDED_CROSS_CONTEXT_BUDGET_TOKENS
        blocks: list[str] = []
        for entry in reversed(entries):
            key_messages = "\n".join(f"- {item}" for item in entry.key_messages)
            block = (
                f"Previous task: {entry.task_id} | {entry.title}\n"
                f"accepted_final_output:\n{entry.final_answer}\n\n"
                f"bounded_summary:\n{entry.bounded_summary}\n\n"
                f"key_messages:\n{key_messages or '- none'}"
            )
            token_count = self.token_counter.count(block).token_count
            if token_count > remaining:
                block = self._truncate_to_token_budget(block, remaining)
                token_count = self.token_counter.count(block).token_count
            if block:
                blocks.insert(0, block)
                remaining -= token_count
            if remaining <= 0:
                break
        if not blocks:
            return []
        content = "\n\n---\n\n".join(blocks)
        return [
            AgentOutput(
                agent_id="bounded_cross_task_context",
                content=content,
                metadata={
                    "baseline_context": "bounded_natural_language",
                    "entry_count": len(blocks),
                    "token_budget": self.BOUNDED_CROSS_CONTEXT_BUDGET_TOKENS,
                },
            )
        ]

    def _build_bounded_history_entry(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        selected_output: AgentOutput,
        current_task_outputs: list[AgentOutput],
    ) -> BoundedBaselineEntry:
        source_text = "\n\n".join(output.content for output in current_task_outputs)
        summary_seed = (
            f"{task.task_id} {task.title}\n"
            f"{self._summary(selected_output.content, 1600)}"
        )
        bounded_summary = self._truncate_to_token_budget(
            summary_seed,
            self.BOUNDED_SUMMARY_MAX_TOKENS,
        )
        final_answer = self._truncate_to_token_budget(
            selected_output.content,
            max(
                1,
                self.BOUNDED_CROSS_CONTEXT_BUDGET_TOKENS
                - self.BOUNDED_SUMMARY_MAX_TOKENS,
            ),
        )
        key_messages = [
            f"{output.agent_id}: {self._summary(output.content, 260)}"
            for output in current_task_outputs[-self.BOUNDED_RECENT_KEY_MESSAGES_K :]
        ]
        estimated_tokens = self.token_counter.count(
            source_text + "\n\n" + bounded_summary
        ).token_count
        self.metrics.record_summary_update(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            estimated_tokens=estimated_tokens,
            method="deterministic",
        )
        self.trace.write(
            "bounded_summary_updated",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "method": "deterministic",
                "summary_tokens_estimated": estimated_tokens,
                "key_message_count": len(key_messages),
            },
        )
        return BoundedBaselineEntry(
            task_id=task.task_id,
            group_id=task.group_id,
            title=task.title,
            final_answer=final_answer,
            bounded_summary=bounded_summary,
            key_messages=key_messages,
        )

    def _select_deliverable_output(
        self,
        *,
        task: TaskSpec,
        fallback_output: AgentOutput,
        draft_answer_output: AgentOutput | None,
        review_report_output: AgentOutput | None,
        final_answer_output: AgentOutput | None,
    ) -> AgentOutput:
        final_answer = (
            final_answer_output
            if final_answer_output is not None
            else draft_answer_output
            if draft_answer_output is not None
            else fallback_output
        )
        selected = final_answer if self._is_final_task(task) else fallback_output
        roles: dict[str, dict[str, object]] = {}
        if draft_answer_output is not None:
            roles["draft_answer"] = self._deliverable_payload(draft_answer_output)
        if review_report_output is not None:
            roles["review_report"] = self._deliverable_payload(review_report_output)
        if final_answer is not None:
            roles["final_answer"] = self._deliverable_payload(final_answer)
        selected_role = (
            "final_answer"
            if self._is_final_task(task) and selected is final_answer
            else "review_report"
            if review_report_output is selected
            else "draft_answer"
            if draft_answer_output is selected
            else "agent_output"
        )
        metadata = dict(selected.metadata)
        metadata["deliverable_role"] = selected_role
        metadata["deliverable_roles"] = roles
        return AgentOutput(
            agent_id=selected.agent_id,
            content=selected.content,
            metadata=metadata,
        )

    @staticmethod
    def _deliverable_payload(output: AgentOutput) -> dict[str, object]:
        return {
            "agent_id": output.agent_id,
            "content": output.content,
            "content_chars": len(output.content),
            "metadata": output.metadata,
        }

    def _next_agent_id(self, agent_id: str) -> str:
        ids = [agent.agent_id for agent in self.agents]
        try:
            index = ids.index(agent_id)
        except ValueError:
            return "runtime"
        return ids[index + 1] if index + 1 < len(ids) else "runtime"

    def _is_final_task(self, task: TaskSpec) -> bool:
        configured = task.metadata.get("is_final_task")
        if configured is not None:
            return bool(configured)
        title = task.title.strip().lower()
        return title.startswith("\u6700\u7ec8") or title.startswith("final")

    @staticmethod
    def _is_stress_baseline_mode(mode: Mode) -> bool:
        return mode in {"baseline_text", "baseline_stress_full_broadcast"}

    @staticmethod
    def _is_bounded_baseline_mode(mode: Mode) -> bool:
        return mode == "baseline_bounded_nl_framework"

    @staticmethod
    def _task_thread_key(task: TaskSpec) -> str:
        if task.group_id:
            return task.group_id
        match = re.match(r"([A-Za-z]+)", task.task_id)
        return match.group(1) if match else "default"

    def _deliverable_role_for_agent(self, agent: DeterministicAgent) -> str:
        profile = self.capability_profiles.get(agent.agent_id)
        capabilities = profile.capabilities if profile is not None else set()
        if "final_deliverable_draft" in capabilities:
            return "draft_answer"
        if "final_deliverable_review" in capabilities:
            return "review_report"
        return ""

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
        return self.kernel.build_message_cost_report(
            message_content=message_content,
            prompt=prompt,
            memory_prompt_views=memory_prompt_views,
        )

    @staticmethod
    def _handoff_gate(message_content: str) -> dict[str, object]:
        return CollaborationKernel.handoff_gate(message_content)

    def _background_job_count(self) -> int:
        with self._background_jobs_lock:
            return len(self._background_memory_jobs)

    def _slot_hint_for_task(self, task: TaskSpec, agent_id: str) -> str:
        configured = task.metadata.get("memory_slot_hint")
        if isinstance(configured, dict):
            configured = configured.get(agent_id, configured.get("default"))
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
        if self._is_final_task(task):
            return "final_deliverable"
        if agent_id == "reviewer":
            return "failure_reason"
        return "reuse_strategy"

    @staticmethod
    def _agent_descriptor(agent: object) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=str(getattr(agent, "agent_id", "")),
            role=str(getattr(agent, "role", getattr(agent, "agent_id", ""))),
        )

    @staticmethod
    def _summary(text: str, limit: int) -> str:
        compact = " ".join(text.split())
        return compact[:limit]

    def _truncate_to_token_budget(self, text: str, budget_tokens: int) -> str:
        if budget_tokens <= 0 or not text:
            return ""
        token_count = self.token_counter.count(text).token_count
        if token_count <= budget_tokens:
            return text
        low = 0
        high = len(text)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = text[:mid]
            if self.token_counter.count(candidate).token_count <= budget_tokens:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        return best.rstrip()
