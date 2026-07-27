from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14p-compact-approval-formal-regression"
)
V514M_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14m-reviewer-artifact-continuity"
)
V514N_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14n-reviewer-artifact-formal-regression"
)
V514O_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14o-compact-review-approval"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class V514PCompactApprovalFormalRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preregistration = json.loads(
            (STAGE_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )

    def test_preregistration_binds_v514o_mechanism(self) -> None:
        lineage = self.preregistration["source_lineage"]
        self.assertEqual(
            lineage["v514o_acceptance_verifier_sha256"],
            _sha256(V514O_DIR / "verify_acceptance.py"),
        )
        self.assertEqual(
            lineage["v514n_preregistration_sha256"],
            _sha256(V514N_DIR / "preregistration.json"),
        )
        self.assertEqual(
            lineage["mechanism_baseline"],
            "v5.14o-compact-review-approval",
        )

    def test_v514n_thresholds_remain_unchanged(self) -> None:
        previous = json.loads(
            (V514N_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            self.preregistration["thresholds"],
            previous["thresholds"],
        )

    def test_v514m_agent_profiles_are_reused_exactly(self) -> None:
        frozen = self.preregistration["frozen_agent_sha256"]
        self.assertEqual(
            frozen["agent_config_A.json"],
            _sha256(V514M_DIR / "agent_config_A.json"),
        )
        self.assertEqual(
            frozen["agent_config_B.json"],
            _sha256(V514M_DIR / "agent_config_B.json"),
        )

    def test_runner_uses_isolated_v514p_paths(self) -> None:
        runner = (STAGE_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("AGENTLITE_V514P_EXP_ID", runner)
        self.assertIn(
            "runs/v5.14p-compact-approval-formal-regression",
            runner,
        )
        self.assertIn(
            "v5.14m-reviewer-artifact-continuity/agent_config_A.json",
            runner,
        )
        self.assertIn(
            "v5.14m-reviewer-artifact-continuity/agent_config_B.json",
            runner,
        )

    def test_verifier_binds_v514o_without_weakening_artifact_gates(
        self,
    ) -> None:
        verifier = (STAGE_DIR / "verify_acceptance.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("formal_run_descends_from_v514o_release", verifier)
        self.assertIn("v514o_acceptance_verifier_is_bound", verifier)
        self.assertIn(
            "managed_reviewer_does_not_replace_business_artifacts",
            verifier,
        )
        self.assertIn(
            "reviewer_approval_note_is_never_final_body",
            verifier,
        )
        self.assertIn(
            "explicit_decisions_enter_typed_shared_memory",
            verifier,
        )


if __name__ == "__main__":
    unittest.main()
