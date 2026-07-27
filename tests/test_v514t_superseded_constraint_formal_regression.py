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
    / "v5.14t-superseded-constraint-formal-regression"
)
V514R_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14r-candidate-evidence-formal-regression"
)
V514S_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14s-superseded-constraint-resolution"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_verifier():
    path = STAGE_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514t_verify_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class V514TSupersededConstraintFormalRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preregistration = json.loads(
            (STAGE_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )

    def test_preregistration_binds_v514s_and_v514r(self) -> None:
        lineage = self.preregistration["source_lineage"]
        self.assertEqual(
            lineage["mechanism_baseline"],
            "v5.14s-superseded-constraint-resolution",
        )
        self.assertEqual(
            lineage["v514s_acceptance_verifier_sha256"],
            _sha256(V514S_DIR / "verify_acceptance.py"),
        )
        self.assertEqual(
            lineage["v514r_preregistration_sha256"],
            _sha256(V514R_DIR / "preregistration.json"),
        )

    def test_v514r_inputs_and_thresholds_are_not_weakened(self) -> None:
        previous = json.loads(
            (V514R_DIR / "preregistration.json").read_text(
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
        current = self.preregistration["thresholds"]
        for key, value in previous["thresholds"].items():
            self.assertIn(key, current)
            self.assertEqual(current[key], value, key)
        self.assertGreaterEqual(
            current["superseded_constraint_candidate_count_min"],
            1,
        )

    def test_runner_uses_isolated_v514t_paths(self) -> None:
        runner = (STAGE_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("AGENTLITE_V514T_EXP_ID", runner)
        self.assertIn(
            "runs/v5.14t-superseded-constraint-formal-regression",
            runner,
        )
        self.assertIn("AGENTLITE_V514B_RESUME", runner)

    def test_detector_observes_generic_historical_constraints(self) -> None:
        verifier = _load_verifier()
        self.assertIsNotNone(
            verifier._HISTORICAL_CONSTRAINT_RE.search(
                "The initial total budget was USD 3000 and was adjusted "
                "to USD 2500."
            )
        )
        self.assertIsNotNone(
            verifier._HISTORICAL_CONSTRAINT_RE.search(
                "总预算从 3000 元收紧到 2500 元。"
            )
        )
        self.assertIsNone(
            verifier._HISTORICAL_CONSTRAINT_RE.search(
                "The current total budget is USD 2500."
            )
        )


if __name__ == "__main__":
    unittest.main()
