from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter


class AgentMetricsTest(unittest.TestCase):
    def test_control_llm_cost_is_separate_and_in_end_to_end_total(
        self,
    ) -> None:
        metrics = MetricsCollector()
        metrics.record_control_llm(
            task_id="semantic-task",
            round_id=1,
            mode="runtime_lite",
            call_count=1,
            prompt_tokens=30,
            completion_tokens=10,
            total_tokens=40,
            retry_count=1,
            latency_ms=12.5,
            accepted_candidate_count=2,
            rejected_candidate_count=1,
        )
        metrics.finish_task(
            "semantic-task",
            1,
            "runtime_lite",
            latency_ms=20.0,
            success=True,
        )

        row = metrics.rows()[0]
        self.assertEqual(row.control_llm_tokens, 40)
        self.assertEqual(row.control_llm_prompt_tokens, 30)
        self.assertEqual(row.control_llm_completion_tokens, 10)
        self.assertEqual(row.control_llm_retry_count, 1)
        self.assertEqual(row.semantic_disambiguation_accepted_count, 2)
        self.assertEqual(row.semantic_disambiguation_rejected_count, 1)
        self.assertEqual(row.end_to_end_collaboration_tokens, 40)

        summary = metrics.summary()["by_mode"]["runtime_lite"]
        self.assertEqual(summary["control_llm_call_count"], 1)
        self.assertEqual(summary["control_llm_tokens"], 40)
        self.assertEqual(
            summary["semantic_disambiguation_accepted_count"],
            2,
        )
        self.assertEqual(
            summary["semantic_disambiguation_rejected_count"],
            1,
        )

    def test_exports_per_agent_latency_rows_and_summary(self) -> None:
        metrics = MetricsCollector()
        counter = TokenCounter(allow_estimate=True)

        metrics.record_agent_role(
            task_id="A10",
            round_id=1,
            mode="runtime_lite",
            agent_id="writer",
            role="WriterAgent",
        )
        metrics.record_prompt(
            task_id="A10",
            round_id=1,
            mode="runtime_lite",
            agent_id="writer",
            prompt="prompt text",
            token_counter=counter,
        )
        metrics.record_llm_call(
            task_id="A10",
            round_id=1,
            mode="runtime_lite",
            agent_id="writer",
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
            latency_ms=1000.0,
            output_chars=120,
        )
        metrics.record_agent_local_timing(
            task_id="A10",
            round_id=1,
            mode="runtime_lite",
            agent_id="writer",
            local_state_read_ms=2.5,
            artifact_digest_ms=1.25,
            schema_check_ms=0.75,
            raw_access_count=0,
        )
        metrics.record_provider_guard(
            task_id="A10",
            round_id=1,
            mode="runtime_lite",
            agent_id="writer",
            provider_guard={"status": "valid", "retry_attempts": 2},
        )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            metrics.export(output_dir)

            agent_csv = output_dir / "agent_metrics.csv"
            agent_json = output_dir / "agent_metrics.json"
            self.assertTrue(agent_csv.exists())
            self.assertTrue(agent_json.exists())

            with agent_csv.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["agent"], "writer")
            self.assertEqual(int(row["llm_prompt_tokens"]), 10)
            self.assertEqual(int(row["llm_completion_tokens"]), 20)
            self.assertEqual(int(row["llm_total_tokens"]), 30)
            self.assertEqual(float(row["llm_wall_time_ms"]), 1000.0)
            self.assertEqual(float(row["tokens_per_second"]), 20.0)
            self.assertEqual(float(row["local_state_read_ms"]), 2.5)
            self.assertEqual(float(row["artifact_digest_ms"]), 1.25)
            self.assertEqual(float(row["schema_check_ms"]), 0.75)
            self.assertEqual(int(row["retry_count"]), 2)
            self.assertEqual(int(row["raw_access_count"]), 0)

            payload = json.loads(agent_json.read_text(encoding="utf-8"))
            summary = payload["summary"]["runtime_lite"]["writer"]
            self.assertEqual(summary["llm_completion_tokens"], 20)
            self.assertEqual(summary["tokens_per_second"], 20.0)


if __name__ == "__main__":
    unittest.main()
