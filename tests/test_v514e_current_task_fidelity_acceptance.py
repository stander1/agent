from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14e-current-task-fidelity-acceptance"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514e_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14e verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _task(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "question": "请给出完整实施方案。",
        "final_answer": (
            "## 最终可交付成果\n\n"
            "这里给出目标、步骤、责任人、风险和验收方式，"
            "内容完整并可直接执行。"
        ),
        "delivery_valid": True,
        "final_resolution_kind": "reviewer_artifact",
        "final_artifact_origin_source": "reviewer",
    }


class CurrentTaskFidelityAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verify = _load_verifier()

    def _write_run(self, root: Path, *, preserved: bool = True) -> None:
        for scenario in ("A", "B"):
            for group in ("native", "observed", "managed"):
                group_dir = root / scenario / group
                group_dir.mkdir(parents=True)
                (group_dir / "sequence_result.json").write_text(
                    json.dumps(
                        {
                            "tasks": [
                                _task(f"{scenario}{index}")
                                for index in range(1, 4)
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            reports = root / scenario / "reports"
            reports.mkdir(parents=True)
            (reports / "managed-agentlite.json").write_text(
                json.dumps(
                    {
                        "state_summary": {
                            "routing_metadata_state_count": 0,
                            "state_dedup_reuse_count": 2,
                        },
                        "token_summary": {
                            "model_visible_protocol_marker_count": 0,
                            "current_task_fidelity_failure_count": (
                                0 if preserved else 1
                            ),
                            "memory_query_count": 2,
                            "memory_hit_count": 0,
                            "memory_injected_count": 0,
                        },
                        "metric_rows": [],
                    }
                ),
                encoding="utf-8",
            )

            trace = (
                root
                / scenario
                / "managed"
                / "agentlite_data"
                / "sessions"
                / "launch_test"
                / "autogen_driver"
                / "trace.jsonl"
            )
            trace.parent.mkdir(parents=True)
            trace.write_text(
                json.dumps(
                    {
                        "event_type": "autogen_team_input_real_rewrite",
                        "payload": {
                            "rewrite_applied": True,
                            "current_task_source_tokens": 80,
                            "current_task_fidelity_failure_count": (
                                0 if preserved else 1
                            ),
                            "receiver_plans": [
                                {
                                    "receiver": "arbitrary_agent",
                                    "current_task_units_preserved": preserved,
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

    def test_complete_synthetic_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report={"summary": {"passed": True}},
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["current_task_fidelity_failure_count"],
            0,
        )
        self.assertEqual(report["summary"]["receiver_plan_count"], 2)

    def test_lost_current_task_unit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root, preserved=False)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report={"summary": {"passed": True}},
            )

        self.assertFalse(report["summary"]["passed"])
        failed = {
            item["name"]
            for item in report["checks"]
            if not item["passed"]
        }
        self.assertIn(
            "A:managed:current_task_units_preserved",
            failed,
        )
        self.assertIn(
            "B:managed:fidelity_metric_reported",
            failed,
        )

    def test_runner_reuses_frozen_preflight(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "v5.14b-fair-cost-quality-preflight/run_openeuler.sh",
            runner,
        )
        self.assertIn('AGENTLITE_V514B_EXPERIMENT_LABEL="v5.14e"', runner)
        self.assertIn('AGENTLITE_V514B_MEMORY_SCOPE_PREFIX="v514e"', runner)


if __name__ == "__main__":
    unittest.main()
