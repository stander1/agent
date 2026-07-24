from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.core.agents import DeterministicAgent
from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.core.runtime import V0Runtime
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.state.state_pool import StatePoolLite


class SimpleAgent(DeterministicAgent):
    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.role = agent_id

    def run(self, task: TaskSpec, context: list[AgentOutput]) -> AgentOutput:
        del context
        return AgentOutput(
            agent_id=self.agent_id,
            content=(
                f"{self.agent_id} completed {task.task_id}; "
                f"{self.agent_id}_result=completed"
            ),
        )


class AsyncMemoryRuntimeTest(unittest.TestCase):
    def test_memory_writes_are_flushed_outside_main_agent_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            metrics = MetricsCollector()
            runtime = V0Runtime(
                agents=[
                    SimpleAgent("writer"),
                    SimpleAgent("reviewer"),
                    SimpleAgent("memory_manager"),
                ],
                token_counter=TokenCounter(allow_estimate=True),
                metrics=metrics,
                trace=TraceLogger(output_dir),
                state_pool=StatePoolLite(output_dir),
                memory_store=MemoryStoreLite(),
            )

            result = runtime.run_task(
                task=TaskSpec(
                    task_id="T1",
                    group_id="test",
                    title="async memory",
                    prompt="test async memory",
                ),
                round_id=1,
                mode="runtime_lite",
            )

            row = metrics.rows()[0]
            self.assertEqual(result.agent_id, "reviewer")
            self.assertEqual(row.background_memory_job_count, 3)
            self.assertEqual(row.memory_write_count, 0)

            runtime.flush_background_tasks()

            self.assertEqual(row.background_memory_completed_count, 3)
            self.assertEqual(row.background_memory_error_count, 0)
            self.assertEqual(row.memory_write_count, 3)


if __name__ == "__main__":
    unittest.main()
