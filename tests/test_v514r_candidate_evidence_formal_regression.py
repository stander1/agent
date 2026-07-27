from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14r-candidate-evidence-formal-regression"
)
V514P_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14p-compact-approval-formal-regression"
)
V514Q_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14q-candidate-evidence-task-identity"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_verifier():
    path = STAGE_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514r_verify_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class V514RCandidateEvidenceFormalRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preregistration = json.loads(
            (STAGE_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )

    def test_preregistration_binds_v514q_and_v514p(self) -> None:
        lineage = self.preregistration["source_lineage"]
        self.assertEqual(
            lineage["mechanism_baseline"],
            "v5.14q-candidate-evidence-task-identity",
        )
        self.assertEqual(
            lineage["v514q_acceptance_verifier_sha256"],
            _sha256(V514Q_DIR / "verify_acceptance.py"),
        )
        self.assertEqual(
            lineage["v514p_preregistration_sha256"],
            _sha256(V514P_DIR / "preregistration.json"),
        )

    def test_v514p_formal_thresholds_are_not_weakened(self) -> None:
        previous = json.loads(
            (V514P_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )
        current = self.preregistration["thresholds"]
        for key, value in previous["thresholds"].items():
            self.assertIn(key, current)
            self.assertEqual(current[key], value, key)
        self.assertGreaterEqual(
            current["current_candidate_required_count_min_per_scenario"],
            1,
        )
        self.assertEqual(
            current["current_candidate_missing_count_max"],
            0,
        )

    def test_frozen_tasks_agents_and_runtime_parameters_are_reused(self) -> None:
        previous = json.loads(
            (V514P_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )
        for key in (
            "frozen_input_sha256",
            "frozen_agent_sha256",
            "provider",
            "temperature",
            "max_turns",
            "groups",
            "tasks_per_scenario",
            "quality_rule",
        ):
            self.assertEqual(self.preregistration[key], previous[key], key)

    def test_runner_uses_isolated_v514r_paths(self) -> None:
        runner = (STAGE_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("AGENTLITE_V514R_EXP_ID", runner)
        self.assertIn(
            "runs/v5.14r-candidate-evidence-formal-regression",
            runner,
        )
        self.assertIn("AGENTLITE_V514B_RESUME", runner)

    def test_candidate_scenario_checks_accept_complete_evidence(self) -> None:
        verifier = _load_verifier()
        checks = verifier._scenario_checks(
            row={
                "scenario_id": "R-formal",
                "current_candidate_required_count": 3,
                "current_candidate_available_count": 3,
                "current_candidate_complete_count": 2,
                "current_candidate_missing_count": 0,
                "current_candidate_source_tokens": 900,
                "current_candidate_selected_tokens": 700,
                "current_task_identity_anchored_count": 4,
            },
            thresholds=self.preregistration["thresholds"],
        )
        self.assertTrue(all(item["passed"] for item in checks), checks)

    def test_candidate_scenario_checks_reject_missing_evidence(self) -> None:
        verifier = _load_verifier()
        checks = verifier._scenario_checks(
            row={
                "scenario_id": "R-formal",
                "current_candidate_required_count": 3,
                "current_candidate_available_count": 2,
                "current_candidate_complete_count": 0,
                "current_candidate_missing_count": 1,
                "current_candidate_source_tokens": 500,
                "current_candidate_selected_tokens": 0,
                "current_task_identity_anchored_count": 0,
            },
            thresholds=self.preregistration["thresholds"],
        )
        failed = {item["name"] for item in checks if not item["passed"]}
        self.assertIn(
            "R-formal:required_candidate_is_available",
            failed,
        )
        self.assertIn(
            "R-formal:complete_candidate_rewrite_observed",
            failed,
        )
        self.assertIn(
            "R-formal:current_task_identity_anchor_observed",
            failed,
        )


if __name__ == "__main__":
    unittest.main()
