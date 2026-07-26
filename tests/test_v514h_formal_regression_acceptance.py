from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT / "experiments" / "v5.14h-formal-regression-acceptance"
)
FORMAL_INPUTS = (
    REPO_ROOT / "experiments" / "v5.14f-formal-scale-acceptance"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514h_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14h verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _task(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "question": "Provide the complete current-task implementation.",
        "final_answer": (
            "## Final deliverable\n\n"
            "Complete objectives, steps, owners, evidence and acceptance."
        ),
        "delivery_valid": True,
        "final_resolution_kind": "reviewer_artifact",
        "final_artifact_origin_source": "reviewer",
    }


class FormalRegressionAcceptanceTest(unittest.TestCase):
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
        governance_failure_count: int = 0,
        blocking_count: int = 1,
    ) -> None:
        frozen = root / "system" / "frozen-inputs"
        frozen.mkdir(parents=True)
        for filename in (
            "question_A_formal.json",
            "question_B_formal.json",
        ):
            (frozen / filename).write_bytes(
                (FORMAL_INPUTS / filename).read_bytes()
            )

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
                        }
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
                        "review_governance_summary": {
                            "event_count": 3,
                            "authoritative_count": 3,
                            "blocking_count": blocking_count,
                            "positive_count": 3 - blocking_count,
                            "targeted_memory_count": blocking_count,
                            "deprecated_memory_count": blocking_count,
                            "blocker_admitted_count": blocking_count,
                            "blocker_memory_count": blocking_count,
                            "targeted_without_deprecation_count": 0,
                            "unexpected_deprecation_count": 0,
                            "blocking_without_admitted_blocker_count": 0,
                            "nonblocking_side_effect_count": 0,
                            "failure_event_count": governance_failure_count,
                            "safe_event_count": 3 - governance_failure_count,
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
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            quality_dir = root / scenario / "comparison"
            quality_dir.mkdir(parents=True)
            (quality_dir / "quality_blind_summary.json").write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "task_id": f"{scenario}10",
                                "scores": {
                                    "native": 9.0,
                                    "managed": 9.0,
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
        preregistration: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return {
            "summary": {
                "passed": True,
                "task_count_per_group": 20,
            },
            "preregistration": (
                preregistration
                if preregistration is not None
                else self.preregistration
            ),
            "aggregates": {
                "memory": {
                    "hit_count": 16,
                    "injected_count": 12,
                    "useful_hit_count": 6,
                    "wrong_hit_count": 0,
                },
                "state": {
                    "state_type_counts": {"artifact_state": 20}
                },
            },
            "scenarios": [
                {
                    "scenario_id": scenario_id,
                    "memory": {
                        "hit_count": 8,
                        "injected_count": 6,
                        "useful_hit_count": 3,
                        "wrong_hit_count": 0,
                    },
                }
                for scenario_id in ("A-formal", "B-formal")
            ],
        }

    def test_unchanged_formal_run_with_safe_governance_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["review_governance_event_count"],
            6,
        )
        self.assertEqual(
            report["summary"]["review_governance_failure_event_count"],
            0,
        )
        self.assertFalse(
            report["review_governance"]["blocking_event_is_mandatory"]
        )

    def test_zero_blocking_events_can_pass_when_outputs_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root, blocking_count=0)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["review_governance_blocking_count"],
            0,
        )

    def test_governance_failure_is_a_formal_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root, governance_failure_count=1)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks["review_governance_has_no_failed_event"]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_legacy_threshold_drift_is_rejected(self) -> None:
        preregistration = json.loads(json.dumps(self.preregistration))
        preregistration["thresholds"][
            "managed_provider_token_reduction_min"
        ] = 0.01
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(
                    preregistration
                ),
            )

        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks[
                "v514f_cost_quality_thresholds_are_unchanged"
            ]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_frozen_task_copy_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            (
                root
                / "system"
                / "frozen-inputs"
                / "question_A_formal.json"
            ).write_text("{}", encoding="utf-8")
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks["v514f_frozen_tasks_are_reused_exactly"]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_runner_reuses_v514f_inputs_and_resume_protocol(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("v5.14f-formal-scale-acceptance", runner)
        self.assertIn("question_A_formal.json", runner)
        self.assertIn("question_B_formal.json", runner)
        self.assertIn("AGENTLITE_V514H_RESUME", runner)
        self.assertIn("formal_regression_report.json", runner)


if __name__ == "__main__":
    unittest.main()
