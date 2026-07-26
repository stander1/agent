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
    / "v5.14j-semantic-fidelity-formal-regression"
)
FORMAL_INPUTS = (
    REPO_ROOT / "experiments" / "v5.14f-formal-scale-acceptance"
)
AGENT_SOURCES = {
    "agent_config_A.json": (
        REPO_ROOT
        / "experiments"
        / "ordinary-developer-autogen"
        / "agent_config.json"
    ),
    "agent_config_B.json": (
        REPO_ROOT
        / "experiments"
        / "v5.14b-fair-cost-quality-preflight"
        / "agent_config_B.json"
    ),
}


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514j_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14j verifier")
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
        "delivery_status": "validated_final_artifact",
        "delivery_guard_reasons": [],
        "missing_delivery_requirements": [],
        "final_resolution_kind": "reviewer_artifact",
        "final_artifact_origin_source": "reviewer",
    }


class SemanticFidelityFormalRegressionTest(unittest.TestCase):
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
        protocol_final_count: int = 0,
        incomplete_task_id: str = "",
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
        for filename, source in AGENT_SOURCES.items():
            (frozen / filename).write_bytes(source.read_bytes())

        focus_ids = {
            "A": {"A2", "A4", "A10"},
            "B": {"B10"},
        }
        for scenario in ("A", "B"):
            tasks = [_task(f"{scenario}{index}") for index in range(1, 11)]
            for group in ("native", "observed", "managed"):
                group_dir = root / scenario / group
                group_dir.mkdir(parents=True)
                (group_dir / "sequence_result.json").write_text(
                    json.dumps({"tasks": tasks}),
                    encoding="utf-8",
                )

            reports = root / scenario / "reports"
            reports.mkdir(parents=True)
            final_count = (
                protocol_final_count if scenario == "B" else 0
            )
            (reports / "managed-agentlite.json").write_text(
                json.dumps(
                    {
                        "state_summary": {
                            "routing_metadata_state_count": 0,
                            "state_dedup_reuse_count": 5,
                        },
                        "token_summary": {
                            "model_visible_protocol_marker_count": (
                                final_count
                            ),
                            "model_visible_input_protocol_marker_count": 0,
                            "model_visible_agent_output_protocol_marker_count": 0,
                            "model_visible_final_output_protocol_marker_count": (
                                final_count
                            ),
                            "current_task_fidelity_failure_count": 0,
                            "memory_query_count": 12,
                            "memory_hit_count": 8,
                            "memory_injected_count": 6,
                            "wrong_memory_hit_count": 0,
                            "memory_adoption_enforcement_failure_count": 0,
                        },
                        "review_governance_summary": {
                            "event_count": 3,
                            "authoritative_count": 3,
                            "blocking_count": 1,
                            "positive_count": 2,
                            "targeted_memory_count": 1,
                            "deprecated_memory_count": 1,
                            "blocker_admitted_count": 1,
                            "blocker_memory_count": 1,
                            "targeted_without_deprecation_count": 0,
                            "unexpected_deprecation_count": 0,
                            "blocking_without_admitted_blocker_count": 0,
                            "nonblocking_side_effect_count": 0,
                            "failure_event_count": 0,
                            "safe_event_count": 3,
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
            quality_tasks = []
            for task_id in sorted(focus_ids[scenario]):
                complete = task_id != incomplete_task_id
                quality_tasks.append(
                    {
                        "task_id": task_id,
                        "scores": {
                            "native": 9.0,
                            "managed": 9.0 if complete else 0.0,
                            "observed": 8.0,
                        },
                        "delivery_complete": {
                            "native": True,
                            "managed": complete,
                            "observed": True,
                        },
                        "technical_findings": {
                            "native": [],
                            "managed": [],
                            "observed": [],
                        },
                    }
                )
            (quality_dir / "quality_blind_summary.json").write_text(
                json.dumps({"tasks": quality_tasks}),
                encoding="utf-8",
            )

    def _preflight_report(self) -> dict[str, object]:
        return {
            "summary": {
                "passed": True,
                "task_count_per_group": 20,
            },
            "preregistration": self.preregistration,
            "aggregates": {
                "provider": {
                    "native": {"total_tokens": 1000},
                    "observed": {"total_tokens": 950},
                    "managed": {"total_tokens": 700},
                },
                "transport": {
                    "native_baseline_tokens": 900,
                    "end_to_end_collaboration_tokens": 500,
                },
                "quality": {
                    "native": {
                        "mean_score": 9.0,
                        "delivery_complete_count": 20,
                    },
                    "managed": {
                        "mean_score": 9.0,
                        "delivery_complete_count": 20,
                    },
                },
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

    def test_frozen_formal_regression_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["regression_focus_task_count"],
            4,
        )
        self.assertEqual(
            report["summary"]["protocol_surface_marker_count"],
            0,
        )

    def test_final_output_protocol_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root, protocol_final_count=1)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks["B-formal:protocol_surfaces_are_isolated"]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_focus_task_blind_incompleteness_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root, incomplete_task_id="A4")
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks[
                "A-formal:A4:blind_delivery_is_complete"
            ]["passed"]
        )
        self.assertFalse(
            checks["A-formal:A4:quality_is_noninferior"]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_agent_profile_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            (
                root
                / "system"
                / "frozen-inputs"
                / "agent_config_A.json"
            ).write_text("{}", encoding="utf-8")
            report = self.verify.evaluate(
                run_root=root,
                preflight_report=self._preflight_report(),
            )

        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks["v514h_agent_profiles_are_reused_exactly"]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_runner_reuses_frozen_inputs_and_supports_resume(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("v5.14f-formal-scale-acceptance", runner)
        self.assertIn("question_A_formal.json", runner)
        self.assertIn("question_B_formal.json", runner)
        self.assertIn("AGENTLITE_V514J_RESUME", runner)
        self.assertIn("semantic_fidelity_regression_report.json", runner)


if __name__ == "__main__":
    unittest.main()
