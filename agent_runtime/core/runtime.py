from __future__ import annotations

import time
from dataclasses import asdict
from typing import Iterable

from agent_runtime.core.agents import DeterministicAgent
from agent_runtime.core.models import AgentOutput, Mode, RuntimeMessage, TaskSpec
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger


class V0Runtime:
    """Deterministic v0 runtime for baseline measurement."""

    def __init__(
        self,
        agents: Iterable[DeterministicAgent],
        token_counter: TokenCounter,
        metrics: MetricsCollector,
        trace: TraceLogger,
    ) -> None:
        self.agents = list(agents)
        self.token_counter = token_counter
        self.metrics = metrics
        self.trace = trace

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
        for agent in self.agents:
            prompt = self._build_prompt(task, context, mode)
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

            output = agent.run(task, context)
            context.append(output)

            message = RuntimeMessage(
                task_id=task.task_id,
                round_id=round_id,
                mode=mode,
                sender=previous_sender,
                receiver=agent.agent_id,
                content=output.content,
                state_refs=[],
                memory_refs=[],
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
                    "state_refs": [],
                    "memory_refs": [],
                },
            )
            previous_sender = agent.agent_id

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
        self, task: TaskSpec, context: list[AgentOutput], mode: Mode
    ) -> str:
        if mode == "baseline_text":
            previous = "\n\n".join(item.content for item in context)
            return f"任务：{task.prompt}\n\n完整上游上下文：\n{previous}"

        previous = "\n\n".join(
            f"agent={item.agent_id}; state_refs=[]; memory_refs=[]; summary={item.content[:240]}"
            for item in context
        )
        return (
            f"任务：{task.prompt}\n\n"
            "runtime_stub_mode 控制字段：state_refs=[]; memory_refs=[]\n"
            f"轻量上游摘要：\n{previous}"
        )

