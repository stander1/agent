import json
import tempfile
import unittest
from pathlib import Path

from examples.run_cross_task_eval import build_cross_task_report, render_markdown_report
from examples.run_cross_task_local_eval import build_bounded_suite


class CrossTaskEvalReportTest(unittest.TestCase):
    def test_build_cross_task_report_splits_a_b_and_checks_memory_reuse(self) -> None:
        summary = {
            "by_mode": {
                "baseline_bounded_nl_framework": {
                    "task_runs": 2,
                    "success_count": 2,
                    "llm_total_tokens": 1000,
                    "end_to_end_collaboration_tokens": 1500,
                    "summary_update_count": 2,
                    "summary_update_estimated_tokens": 100,
                    "memory_hit_count": 0,
                    "useful_memory_hit_count": 0,
                    "wrong_memory_hit_count": 0,
                    "raw_access_count": 0,
                    "fallback_count": 0,
                },
                "runtime_lite": {
                    "task_runs": 2,
                    "success_count": 2,
                    "llm_total_tokens": 300,
                    "end_to_end_collaboration_tokens": 400,
                    "summary_update_count": 0,
                    "summary_update_estimated_tokens": 0,
                    "memory_hit_count": 4,
                    "useful_memory_hit_count": 4,
                    "wrong_memory_hit_count": 0,
                    "raw_access_count": 0,
                    "fallback_count": 0,
                },
            }
        }
        rows = [
            {"task_id": "A1", "mode": "baseline_bounded_nl_framework", "task_runs": 1, "llm_total_tokens": 500},
            {"task_id": "A1", "mode": "runtime_lite", "task_runs": 1, "llm_total_tokens": 150, "memory_hit_count": 1},
            {"task_id": "B1", "mode": "baseline_bounded_nl_framework", "task_runs": 1, "llm_total_tokens": 500},
            {"task_id": "B1", "mode": "runtime_lite", "task_runs": 1, "llm_total_tokens": 150, "memory_hit_count": 3},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            metrics_path = Path(tmp) / "metrics.json"
            metrics_path.write_text(json.dumps({"rows": rows}), encoding="utf-8")

            report = build_cross_task_report(
                summary=summary,
                metrics_path=metrics_path,
                provider="test",
                suites=["A", "B"],
            )
            markdown = render_markdown_report(report)

        self.assertTrue(report["shared_runtime"])
        self.assertEqual(report["by_suite"]["A"]["task_count"], 1)
        self.assertEqual(report["by_suite"]["B"]["task_count"], 1)
        self.assertEqual(
            report["by_suite"]["A"]["metrics"]["task_runs"]["runtime_lite"], 1
        )
        self.assertEqual(
            report["by_suite"]["B"]["metrics"]["success_count"]["baseline"], 1
        )
        self.assertTrue(report["audit_focus"]["checks"]["runtime_has_memory_hits"])
        self.assertTrue(report["audit_focus"]["checks"]["raw_access_low"])
        self.assertEqual(report["baseline_mode"], "baseline_bounded_nl_framework")
        self.assertIn("v5.10", markdown)
        self.assertIn("baseline_bounded_nl_framework", markdown)
        self.assertIn("llm_total_tokens", markdown)

    def test_build_bounded_suite_limits_local_smoke_size(self) -> None:
        payload = {
            "tasks": [
                {"task_id": "A1", "group_id": "A", "title": "one", "prompt": "p1"},
                {"task_id": "A2", "group_id": "A", "title": "two", "prompt": "p2"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "suite.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            target = build_bounded_suite(
                source_path=source,
                output_dir=Path(tmp) / "bounded",
                max_tasks=1,
            )
            bounded = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(len(bounded["tasks"]), 1)
        self.assertEqual(bounded["tasks"][0]["task_id"], "A1")


if __name__ == "__main__":
    unittest.main()
