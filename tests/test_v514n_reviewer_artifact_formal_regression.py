from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14n-reviewer-artifact-formal-regression"
)
V514L_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14l-typed-reliability-formal-regression"
)
V514M_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14m-reviewer-artifact-continuity"
)


def _load_verifier():
    path = STAGE_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514n_verify_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _load_verifier()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class V514NReviewerArtifactFormalRegressionTests(unittest.TestCase):
    def test_preregistration_binds_v514m_profiles(self) -> None:
        preregistration = json.loads(
            (STAGE_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )
        frozen = preregistration["frozen_agent_sha256"]
        self.assertEqual(
            frozen["agent_config_A.json"],
            _sha256(V514M_DIR / "agent_config_A.json"),
        )
        self.assertEqual(
            frozen["agent_config_B.json"],
            _sha256(V514M_DIR / "agent_config_B.json"),
        )

    def test_legacy_thresholds_remain_unchanged(self) -> None:
        previous = json.loads(
            (V514L_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )
        current = json.loads(
            (STAGE_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )
        for key, value in previous["thresholds"].items():
            self.assertEqual(current["thresholds"].get(key), value, key)

    def test_artifact_audit_accepts_reference_promotion(self) -> None:
        report = VERIFY._audit_managed_artifacts(
            [
                {
                    "final_resolution_kind": "prior_artifact_approved",
                    "final_artifact_origin_source": "builder",
                    "final_answer": "完整业务成果",
                    "messages": [
                        {
                            "source": "reviewer",
                            "content": (
                                "## 验收通过\n"
                                "批准上一份成果作为最终交付物\n"
                                "FINAL_ANSWER_READY"
                            ),
                        }
                    ],
                }
            ]
        )
        self.assertEqual(report["prior_artifact_approved_count"], 1)
        self.assertEqual(report["reviewer_origin_mismatch_count"], 0)
        self.assertEqual(report["approval_body_leak_count"], 0)

    def test_artifact_audit_detects_reviewer_body_leak(self) -> None:
        approval = "## 验收通过\n批准上一份成果作为最终交付物"
        report = VERIFY._audit_managed_artifacts(
            [
                {
                    "final_resolution_kind": "prior_artifact_approved",
                    "final_artifact_origin_source": "reviewer",
                    "final_answer": approval,
                    "messages": [
                        {
                            "source": "reviewer",
                            "content": approval + "\nFINAL_ANSWER_READY",
                        }
                    ],
                }
            ]
        )
        self.assertEqual(report["reviewer_origin_mismatch_count"], 1)
        self.assertEqual(report["approval_body_leak_count"], 1)

    def test_explicit_decision_memory_is_counted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = (
                Path(tmp)
                / "sessions"
                / "launch_test"
                / "autogen_driver"
                / "pool_snapshot_latest.json"
            )
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "memory_store": {
                            "claim_cards": [
                                {
                                    "claim_id": "claim_decision",
                                    "slot_id": (
                                        "slot.system.design_decision"
                                    ),
                                    "scope": "decision.risk",
                                },
                                {
                                    "claim_id": "claim_selected",
                                    "slot_id": (
                                        "slot.system.design_decision"
                                    ),
                                    "scope": "plan.selected_destination",
                                },
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                VERIFY._count_explicit_decision_memories(Path(tmp)),
                1,
            )

    def test_runner_uses_v514m_profiles_for_both_scenarios(self) -> None:
        runner = (STAGE_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "v5.14m-reviewer-artifact-continuity/agent_config_A.json",
            runner,
        )
        self.assertIn(
            "v5.14m-reviewer-artifact-continuity/agent_config_B.json",
            runner,
        )

    def test_only_obsolete_agent_identity_check_is_superseded(self) -> None:
        verifier = (STAGE_DIR / "verify_acceptance.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '"v514h_agent_profiles_are_reused_exactly"',
            verifier,
        )
        self.assertIn("replacement", verifier)
        self.assertIn("formal_copy_matches_preregistered_profile", verifier)


if __name__ == "__main__":
    unittest.main()
