from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.adapters.base import FrameworkAdapter
from agent_runtime.core.kernel import (
    AgentDescriptor,
    CollaborationKernel,
    MemoryContext,
)
from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.state.state_pool import StatePoolLite


class FakeFrameworkAgent:
    def __init__(self, name: str, role: str) -> None:
        self.name = name
        self.role = role


class FakeAdapter:
    framework_name = "fake"

    def __init__(self) -> None:
        self.kernel: CollaborationKernel | None = None

    def activate(self, kernel: CollaborationKernel) -> None:
        self.kernel = kernel

    def deactivate(self) -> None:
        self.kernel = None

    def describe_agent(self, framework_agent: FakeFrameworkAgent) -> AgentDescriptor:
        return AgentDescriptor(
            agent_id=framework_agent.name,
            role=framework_agent.role,
        )


class CollaborationKernelTest(unittest.TestCase):
    def _kernel(
        self, output_dir: Path
    ) -> tuple[CollaborationKernel, MetricsCollector]:
        metrics = MetricsCollector()
        kernel = CollaborationKernel(
            agents=[
                FakeFrameworkAgent("planner", "PlannerAgent"),
                FakeFrameworkAgent("writer", "WriterAgent"),
            ],
            token_counter=TokenCounter(allow_estimate=True),
            metrics=metrics,
            trace=TraceLogger(output_dir),
            state_pool=StatePoolLite(output_dir / "state"),
            memory_store=MemoryStoreLite(output_dir / "memory"),
        )
        return kernel, metrics

    def test_adapter_protocol_and_session_lifecycle_are_framework_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            kernel, _ = self._kernel(output_dir)
            adapter = FakeAdapter()

            self.assertIsInstance(adapter, FrameworkAdapter)
            adapter.activate(kernel)
            session = kernel.open_session(
                framework=adapter.framework_name,
                external_session_id="external-1",
                metadata={"team": "demo"},
            )
            self.assertEqual(session.framework, "fake")
            self.assertEqual(len(kernel.active_sessions()), 1)

            kernel.close_session(session.session_id)
            adapter.deactivate()
            kernel.close()

            events = [
                json.loads(line)
                for line in (output_dir / "trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertTrue(
                any(item["event_type"] == "kernel_session_opened" for item in events)
            )
            self.assertTrue(
                any(item["event_type"] == "kernel_session_closed" for item in events)
            )

    def test_receive_output_state_and_handoff_flow_without_runtime_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            kernel, metrics = self._kernel(output_dir)
            task = TaskSpec(
                task_id="K1",
                group_id="kernel",
                title="kernel boundary",
                prompt="validate framework-neutral collaboration",
            )
            writer = AgentDescriptor("writer", "WriterAgent")
            raw_output = AgentOutput(
                agent_id="writer",
                content="plain framework output",
            )

            processed = kernel.after_agent_output(
                task=task,
                round_id=1,
                mode="runtime_lite",
                agent=writer,
                next_action="runtime",
                output=raw_output,
            )

            self.assertEqual(
                processed.output.metadata["contract_guard"]["contract_status"],
                "degraded_fallback",
            )
            self.assertEqual(processed.state_refs[0].state_type, "failure_state")

            prepared = kernel.before_agent_receive(
                task=task,
                round_id=1,
                mode="runtime_lite",
                agent=AgentDescriptor("reviewer", "ReviewerAgent"),
                state_refs=processed.state_refs,
                memory_context=MemoryContext([], []),
            )
            self.assertTrue(prepared.state_prompt_views)

            handoff = json.loads(
                kernel.build_handoff(
                    task=task,
                    round_id=1,
                    mode="runtime_lite",
                    sender="writer",
                    declared_receiver="runtime",
                    summary="writer output requires review",
                    state_refs=processed.state_refs,
                    memory_refs=[],
                )
            )
            self.assertEqual(handoff["header"]["receiver"], "reviewer")
            self.assertEqual(handoff["header"]["msg_type"], "failure_state")
            self.assertEqual(handoff["control"]["readiness"], "degraded")
            self.assertGreater(metrics.rows()[0].state_payload_bytes, 0)
            self.assertGreater(metrics.rows()[0].hot_state_count, 0)
            kernel.finalize_task(task.task_id)
            kernel.close()

    def test_v0_runtime_uses_same_kernel_owned_pools(self) -> None:
        from agent_runtime.core.agents import build_default_agents
        from agent_runtime.core.runtime import V0Runtime

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            runtime = V0Runtime(
                agents=build_default_agents(),
                token_counter=TokenCounter(allow_estimate=True),
                metrics=MetricsCollector(),
                trace=TraceLogger(output_dir),
                state_pool=StatePoolLite(output_dir / "state"),
                memory_store=MemoryStoreLite(output_dir / "memory"),
            )
            try:
                self.assertIs(runtime.state_pool, runtime.kernel.state_pool)
                self.assertIs(runtime.memory_store, runtime.kernel.memory_store)
                self.assertIs(
                    runtime.communication_gate,
                    runtime.kernel.communication_gate,
                )
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
