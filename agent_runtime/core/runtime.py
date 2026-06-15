from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from agent_runtime.core.agents import DeterministicAgent
from agent_runtime.core.models import AgentOutput, Mode, RuntimeMessage, TaskSpec
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.memory_store import MemoryRef, MemoryStoreLite
from agent_runtime.state.state_pool import StatePoolLite, StateRef


class V0Runtime:
    """Deterministic v0 runtime for baseline measurement."""

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

    def run_task(self, task: TaskSpec, round_id: int, mode: Mode) -> AgentOutput:
        started = time.perf_counter()
        context: list[AgentOutput] = []
        self.trace.write(
            "task_started",
            {
                "task_id": task.task_id,
                "round_id": round_id,
                "mode": mode,
                "title": task.title,
            },
        )

        previous_sender = "user"
        state_refs: list[StateRef] = []
        memory_refs_used: list[MemoryRef] = []
        lite_context: list[AgentOutput] = []
        task_memory_refs: list[MemoryRef] = []
        task_memory_prompt_views: list[str] = []
        if mode == "runtime_lite":
            task_memory_refs = self.memory_store.search_memory(
                task.prompt,
                tags=[task.group_id, task.task_id],
                top_k=2,
            )
            task_memory_prompt_views = [
                self.memory_store.render_prompt_view(ref) for ref in task_memory_refs
            ]
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
            memory_prompt_views = (
                task_memory_prompt_views
                if mode == "runtime_lite" and agent.agent_id in {"planner", "writer"}
                else []
            )

            prompt = self._build_prompt(
                task,
                context,
                mode,
                state_refs=state_refs,
                memory_prompt_views=memory_prompt_views,
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
            llm_meta = output.metadata.get("llm", {})
            if llm_meta:
                self.metrics.record_llm_call(
                    task_id=task.task_id,
                    round_id=round_id,
                    mode=mode,
                    usage=llm_meta.get("usage", {}),
                    latency_ms=float(llm_meta.get("latency_ms", 0.0) or 0.0),
                )
            context.append(output)

            output_state_refs: list[StateRef] = []
            output_memory_refs: list[MemoryRef] = []
            message_content = output.content
            if mode == "runtime_lite":
                state_ref = self._write_agent_state(task, round_id, mode, agent, output)
                output_state_refs.append(state_ref)
                state_refs.append(state_ref)

                if agent.agent_id in {"writer", "reviewer", "memory_manager"}:
                    memory_ref = self.memory_store.write_memory(
                        task_id=task.task_id,
                        source_agent=agent.agent_id,
                        task_topic=task.title,
                        summary=self._summary(output.content, 180),
                        tags=[task.group_id, task.task_id, agent.agent_id],
                    )
                    output_memory_refs.append(memory_ref)

                message_content = self._build_shp_message(
                    task=task,
                    round_id=round_id,
                    from_agent=agent.agent_id,
                    next_receiver=self._next_agent_id(agent.agent_id),
                    summary=self._summary(output.content, 160),
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
                            f"summary={self._summary(output.content, 180)}"
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
                cost_report={
                    "direct_text_tokens": 0,
                    "prompt_tokens": 0,
                    "retrieved_memory_tokens": 0,
                    "control_llm_tokens": 0,
                },
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
            previous_sender = agent.agent_id

        if mode == "runtime_lite" and memory_refs_used:
            self.metrics.record_memory_supported_output(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                count=len(memory_refs_used),
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
            },
        )
        return context[-1]

    def _build_prompt(
        self,
        task: TaskSpec,
        context: list[AgentOutput],
        mode: Mode,
        state_refs: list[StateRef] | None = None,
        memory_prompt_views: list[str] | None = None,
        agent_role: str = "",
    ) -> str:
        if mode == "baseline_text":
            previous = "\n\n".join(item.content for item in context)
            return f"任务：{task.prompt}\n\n完整上游上下文：\n{previous}"

        if mode == "runtime_lite":
            state_views = []
            for ref in (state_refs or [])[-5:]:
                state_views.append(
                    self.state_pool.render_prompt_view(
                        ref,
                        agent_role=agent_role,
                        budget_chars=700,
                    )
                )
            memory_block = "\n".join(memory_prompt_views or [])
            state_block = "\n\n".join(state_views)
            return (
                f"任务：{task.prompt}\n\n"
                "runtime_lite 控制字段：下游仅通过 Prompt View 读取状态和记忆。\n"
                f"Memory Prompt View:\n{memory_block or '无可复用记忆'}\n\n"
                f"State Prompt View:\n{state_block or '无上游状态'}"
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

    def _write_agent_state(
        self,
        task: TaskSpec,
        round_id: int,
        mode: Mode,
        agent: DeterministicAgent,
        output: AgentOutput,
    ) -> StateRef:
        if agent.agent_id == "retriever":
            payload = self._build_retrieval_payload(task)
            state_type = "retrieval_state"
            summary = f"{task.title} 的检索状态，包含 {len(task.documents)} 条证据和排序分数。"
            usage_hint = "summary_context_selection"
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
                "summary": self._summary(output.content, 240),
            }
            state_type = "artifact_state"
            summary = f"{agent.agent_id} 产物状态：{self._summary(output.content, 120)}"
            usage_hint = "artifact_summary"

        state_ref, state = self.state_pool.write_state(
            task_id=task.task_id,
            source_agent=agent.agent_id,
            state_type=state_type,
            payload=payload,
            summary=summary,
            usage_hint=usage_hint,
            contains_embedding_refs=False,
        )
        self.metrics.record_state_write(
            task_id=task.task_id,
            round_id=round_id,
            mode=mode,
            state_type=state_type,
            payload_bytes=state.size_bytes,
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

    def _build_retrieval_payload(self, task: TaskSpec) -> dict:
        chunks = {}
        score_map = {}
        evidence_rank = []
        for idx, doc in enumerate(task.documents or [task.prompt], start=1):
            chunk_id = f"{task.task_id}_chunk_{idx}"
            chunks[chunk_id] = {
                "chunk_id": chunk_id,
                "source_id": f"{task.task_id}_source_{idx}",
                "text": doc,
            }
            score = max(0.1, 1.0 - (idx - 1) * 0.12)
            score_map[chunk_id] = score
            evidence_rank.append(chunk_id)
        return {
            "chunk_ids": evidence_rank,
            "source_ids": [chunks[item]["source_id"] for item in evidence_rank],
            "score_map": score_map,
            "evidence_rank": evidence_rank,
            "chunks": chunks,
        }

    def _build_shp_message(
        self,
        *,
        task: TaskSpec,
        round_id: int,
        from_agent: str,
        next_receiver: str,
        summary: str,
        state_refs: list[StateRef],
        memory_refs: list[MemoryRef],
    ) -> str:
        packet = {
            "shp_id": f"shp_{task.task_id}_{round_id}_{from_agent}",
            "protocol_version": "v1-lite",
            "from": from_agent,
            "to": next_receiver,
            "task_id": task.task_id,
            "action": f"{from_agent}_completed",
            "summary": summary,
            "parameters": {},
            "state_refs": [self.state_pool.ref_to_dict(ref) for ref in state_refs],
            "memory_refs": [self.memory_store.ref_to_dict(ref) for ref in memory_refs],
        }
        return json.dumps(packet, ensure_ascii=False, separators=(",", ":"))

    def _next_agent_id(self, agent_id: str) -> str:
        ids = [agent.agent_id for agent in self.agents]
        try:
            index = ids.index(agent_id)
        except ValueError:
            return "runtime"
        return ids[index + 1] if index + 1 < len(ids) else "runtime"

    @staticmethod
    def _summary(text: str, limit: int) -> str:
        compact = " ".join(text.split())
        return compact[:limit]
