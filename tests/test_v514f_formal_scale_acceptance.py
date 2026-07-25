from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT / "experiments" / "v5.14f-formal-scale-acceptance"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514f_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14f verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _task(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "question": "请给出当前任务的完整实施方案。",
        "final_answer": (
            "## 最终可交付成果\n\n"
            "这里给出完整目标、步骤、责任、证据和验收方式。"
        ),
        "delivery_valid": True,
        "final_resolution_kind": "reviewer_artifact",
        "final_artifact_origin_source": "reviewer",
    }


class FormalScaleAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verify = _load_verifier()
        cls.preregistration = json.loads(
            (EXPERIMENT_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )

    def _write_run(
        self,
        root: Path,
        *,
        terminal_managed_score: float = 9.0,
    ) -> None:
        for scenario in ("A", "B"):
            for group in ("native", "observed", "managed"):
                group_dir = root / scenario / group
                group_dir.mkdir(parents=True)
                (group_dir / "sequence_result.json").write_text(
                    json.dumps(
                        {
                            "tasks": [
                                _task(f"{scenario}{index}")
                                for index in range(1, 11)
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
                            "state_dedup_reuse_count": 5,
                        },
                        "token_summary": {
                            "model_visible_protocol_marker_count": 0,
                            "current_task_fidelity_failure_count": 0,
                            "memory_query_count": 12,
                            "memory_hit_count": 8,
                            "memory_injected_count": 6,
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
                            "current_task_fidelity_failure_count": 0,
                            "receiver_plans": [
                                {
                                    "receiver": "domain_specialist",
                                    "current_task_units_preserved": True,
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            terminal_id = f"{scenario}10"
            quality_dir = root / scenario / "comparison"
            quality_dir.mkdir(parents=True)
            (quality_dir / "quality_blind_summary.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "task_id": terminal_id,
                                "scores": {
                                    "native": 9.0,
                                    "managed": terminal_managed_score,
                                    "observed": 8.0,
                                },
                                "delivery_complete": {
                                    "native": True,
                                    "managed": True,
                                    "observed": True,
                                },
                                "technical_findings": {
                                    "native": [],
                                    "managed": [],
                                    "observed": [],
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

    def _preflight_report(
        self,
        *,
        wrong_memory_hit_count: int = 0,
    ) -> dict[str, object]:
        return {
            "summary": {
                "passed": True,
                "task_count_per_group": 20,
            },
            "preregistration": self.preregistration,
            "aggregates": {
                "memory": {
                    "hit_count": 16,
                    "injected_count": 12,
                    "useful_hit_count": 6,
                    "wrong_hit_count": wrong_memory_hit_count,
                },
                "state": {
                    "state_type_counts": {"artifact_state": 20}
                },
            },
            "scenarios": [
                {
                    "scenario_id": "A-formal",
                    "memory": {
                        "hit_count": 8,
                        "injected_count": 6,
                        "useful_hit_count": 3,
                        "wrong_hit_count": wrong_memory_hit_count,
                    },
                },
                {
                    "scenario_id": "B-formal",
                    "memory": {
                        "hit_count": 8,
                        "injected_count": 6,
                        "useful_hit_count": 3,
                        "wrong_hit_count": 0,
                    },
                },
            ],
        }

    def test_complete_full_scale_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(report["summary"]["resume_count"], 0)
        self.assertEqual(report["summary"]["task_count_per_group"], 20)
        self.assertEqual(report["summary"]["terminal_task_count"], 2)
        self.assertEqual(
            report["state_type_diagnostics"]["missing_requested_types"],
            ["retrieval_state", "embedding_state"],
        )

    def test_terminal_quality_regression_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root, terminal_managed_score=5.0)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        failed = {
            item["name"]
            for item in report["checks"]
            if not item["passed"]
        }
        self.assertFalse(report["summary"]["passed"])
        self.assertIn(
            "A-formal:A10:managed_terminal_quality_is_acceptable",
            failed,
        )
        self.assertIn(
            "B-formal:B10:managed_terminal_quality_is_acceptable",
            failed,
        )

    def test_wrong_memory_hit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(
                    wrong_memory_hit_count=1
                ),
            )

        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(checks["formal_memory_has_no_wrong_hit"]["passed"])
        self.assertFalse(report["summary"]["passed"])

    def test_each_scenario_must_have_useful_memory_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            preflight = self._preflight_report()
            preflight["scenarios"][1]["memory"]["useful_hit_count"] = 0
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=preflight,
            )

        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks["B-formal:managed_memory_reuse_is_real"]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_resume_history_is_disclosed_without_changing_thresholds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            history = root / "system" / "resume-history.txt"
            history.parent.mkdir(parents=True)
            history.write_text(
                "resume_at=2026-07-25T18:00:00+08:00\n"
                "resume_git_commit=abc123\n---\n",
                encoding="utf-8",
            )
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(report["summary"]["resume_count"], 1)
        self.assertIn(
            "resume_git_commit=abc123",
            report["resume_evidence"]["history"],
        )

    def test_frozen_inputs_and_runner_are_full_scale(self) -> None:
        for filename in (
            "question_A_formal.json",
            "question_B_formal.json",
        ):
            payload = json.loads(
                (EXPERIMENT_DIR / filename).read_text(encoding="utf-8")
            )
            self.assertEqual(len(payload["tasks"]), 10)

        question_a = json.loads(
            (EXPERIMENT_DIR / "question_A_formal.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("冻结的本地合成资料快照", question_a["tasks"][1]["question"])

        question_b = json.loads(
            (EXPERIMENT_DIR / "question_B_formal.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("embedding_state", question_b["tasks"][6]["question"])

        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("AGENTLITE_V514B_PREREGISTRATION", runner)
        self.assertIn("question_A_formal.json", runner)
        self.assertIn("question_B_formal.json", runner)
        self.assertIn("formal_acceptance_report.json", runner)
        self.assertIn("AGENTLITE_V514F_RESUME", runner)

        base_runner = (
            REPO_ROOT
            / "experiments"
            / "v5.14b-fair-cost-quality-preflight"
            / "run_openeuler.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'PREREG="${AGENTLITE_V514B_PREREGISTRATION:-',
            base_runner,
        )
        self.assertIn(
            'A_TASKS="${AGENTLITE_V514B_A_TASKS:-',
            base_runner,
        )
        self.assertIn('RESUME="${AGENTLITE_V514B_RESUME:-0}"', base_runner)
        self.assertIn("group_is_complete()", base_runner)
        self.assertIn("archive_incomplete_group()", base_runner)
        self.assertIn("resume-history.txt", base_runner)


if __name__ == "__main__":
    unittest.main()
