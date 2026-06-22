from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from agent_runtime.core.agents import DeterministicAgent, build_default_agents
from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.core.runtime import V0Runtime
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.state.state_pool import StatePoolLite


class FailingAgent(DeterministicAgent):
    agent_id = "failing"
    role = "FailingAgent"

    def run(self, task: TaskSpec, context: list[AgentOutput]) -> AgentOutput:
        del task, context
        raise ValueError("intentional runtime failure")


class RuntimeTest(unittest.TestCase):
    def _runtime(
        self,
        output_dir: Path,
        agents: list[DeterministicAgent],
    ) -> tuple[V0Runtime, MetricsCollector]:
        metrics = MetricsCollector()
        runtime = V0Runtime(
            agents=agents,
            token_counter=TokenCounter(allow_estimate=True),
            metrics=metrics,
            trace=TraceLogger(output_dir),
            state_pool=StatePoolLite(output_dir / "state"),
            memory_store=MemoryStoreLite(output_dir / "memory"),
        )
        return runtime, metrics

    def test_failed_task_is_recorded_and_runtime_closes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            runtime, metrics = self._runtime(output_dir, [FailingAgent()])

            with self.assertRaisesRegex(ValueError, "intentional runtime failure"):
                runtime.run_task(
                    TaskSpec("failure_1", "failure", "failure", "fail now"),
                    round_id=1,
                    mode="runtime_lite",
                )

            runtime.close()
            row = metrics.rows()[0]
            events = [
                json.loads(line)
                for line in (output_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertFalse(row.success)
            self.assertTrue(any(item["event_type"] == "task_failed" for item in events))
            self.assertTrue(
                any(
                    item["event_type"] == "task_finished"
                    and item["payload"]["success"] is False
                    for item in events
                )
            )
            with self.assertRaisesRegex(RuntimeError, "closed"):
                runtime.run_task(
                    TaskSpec("failure_2", "failure", "closed", "fail now"),
                    round_id=1,
                    mode="runtime_lite",
                )

    def test_final_task_matching_does_not_treat_110_as_10(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = self._runtime(Path(tmp), build_default_agents())
            try:
                self.assertTrue(runtime._is_final_task(TaskSpec("A10", "A", "step", "x")))
                self.assertFalse(runtime._is_final_task(TaskSpec("A110", "A", "step", "x")))
            finally:
                runtime.close()

    def test_control_budget_block_stops_agent_loop_and_records_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            runtime, metrics = self._runtime(output_dir, build_default_agents())
            runtime.control_budget.max_decisions_per_task = 0
            try:
                with self.assertRaisesRegex(RuntimeError, "Communication gate blocked"):
                    runtime.run_task(
                        TaskSpec("blocked_1", "blocked", "blocked", "blocked"),
                        round_id=1,
                        mode="runtime_lite",
                    )
            finally:
                runtime.close()

            self.assertFalse(metrics.rows()[0].success)
            self.assertNotIn("blocked_1", runtime.control_budget._decision_counts)
            events = [
                json.loads(line)
                for line in (output_dir / "trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertTrue(
                any(
                    item["event_type"] == "communication_handoff_blocked"
                    for item in events
                )
            )

    def test_runtime_messages_report_cost_and_reused_memory_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            runtime, metrics = self._runtime(output_dir, build_default_agents())
            try:
                first = TaskSpec(
                    "R1",
                    "runtime_reuse",
                    "runtime reuse",
                    "structured communication runtime reuse evidence",
                )
                second = TaskSpec(
                    "R2",
                    "runtime_reuse",
                    "runtime reuse followup",
                    "structured communication runtime reuse evidence followup",
                )
                runtime.run_task(first, round_id=1, mode="runtime_lite")
                runtime.run_task(second, round_id=1, mode="runtime_lite")
                runtime.flush_background_tasks()
            finally:
                runtime.close()

            events = [
                json.loads(line)
                for line in (output_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            messages = [
                item["payload"]
                for item in events
                if item["event_type"] == "message_sent"
                and item["payload"]["task_id"] == "R2"
            ]
            second_row = next(item for item in metrics.rows() if item.task_id == "R2")

            self.assertTrue(messages)
            self.assertTrue(any(item["memory_refs"] for item in messages))
            self.assertTrue(all(item["cost_report"]["direct_text_tokens"] > 0 for item in messages))
            self.assertTrue(all(item["cost_report"]["prompt_tokens"] > 0 for item in messages))
            self.assertGreater(second_row.memory_refs_count, 0)

    def test_trace_logger_serializes_concurrent_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = TraceLogger(Path(tmp))

            def write_batch(worker_id: int) -> None:
                for index in range(50):
                    trace.write("concurrent", {"worker": worker_id, "index": index})

            threads = [
                threading.Thread(target=write_batch, args=(item,))
                for item in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            lines = trace.path.read_text(encoding="utf-8").splitlines()
            records = [json.loads(line) for line in lines]
            self.assertEqual(len(records), 200)
            self.assertTrue(all(item["event_type"] == "concurrent" for item in records))


if __name__ == "__main__":
    unittest.main()
