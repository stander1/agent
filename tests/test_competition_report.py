from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.eval.competition_report import (
    SuiteRun,
    build_competition_report,
    percent_change,
    render_markdown_report,
    write_report_files,
)


def summary_pair(baseline_tokens: int, runtime_tokens: int) -> dict:
    return {
        "by_mode": {
            "baseline_text": {
                "task_runs": 1,
                "success_count": 1,
                "llm_total_tokens": baseline_tokens,
                "latency_ms": 100.0,
                "fallback_count": 0,
                "contract_retry_count": 0,
                "wrong_memory_hit_count": 0,
                "raw_access_count": 0,
            },
            "runtime_lite": {
                "task_runs": 1,
                "success_count": 1,
                "llm_total_tokens": runtime_tokens,
                "latency_ms": 70.0,
                "fallback_count": 0,
                "contract_retry_count": 0,
                "wrong_memory_hit_count": 0,
                "raw_access_count": 0,
                "deliverable_schema_required_count": 2,
                "deliverable_schema_hit_count": 2,
                "deliverable_schema_complete_count": 1,
            },
        }
    }


class CompetitionReportTest(unittest.TestCase):
    def test_percent_change(self) -> None:
        self.assertEqual(percent_change(50, 100), -50.0)
        self.assertIsNone(percent_change(50, 0))

    def test_builds_aggregate_and_quality_gate(self) -> None:
        runs = [
            SuiteRun("A", "suite A", "a.json", Path("runs/a"), summary_pair(100, 10)),
            SuiteRun("B", "suite B", "b.json", Path("runs/b"), summary_pair(200, 20)),
        ]
        report = build_competition_report(runs=runs, provider="test")
        total_tokens = report["aggregate"]["metrics"]["llm_total_tokens"]
        self.assertEqual(total_tokens["baseline"], 300)
        self.assertEqual(total_tokens["runtime_lite"], 30)
        self.assertEqual(total_tokens["percent_change"], -90.0)
        self.assertTrue(report["suites"][0]["quality"]["runtime_lite"]["passed"])

    def test_writes_json_and_markdown(self) -> None:
        runs = [SuiteRun("A", "suite A", "a.json", Path("runs/a"), summary_pair(100, 10))]
        report = build_competition_report(runs=runs, provider="test")
        with tempfile.TemporaryDirectory() as tmp:
            json_path, markdown_path = write_report_files(
                report=report, output_dir=Path(tmp)
            )
            self.assertTrue(json_path.exists())
            self.assertTrue(markdown_path.exists())
            self.assertIn("v5.0", render_markdown_report(report))


if __name__ == "__main__":
    unittest.main()
