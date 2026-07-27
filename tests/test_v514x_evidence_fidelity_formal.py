from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_PATH = (
    REPO_ROOT
    / "experiments"
    / "v5.14x-evidence-fidelity-formal-regression"
    / "verify_acceptance.py"
)


def _load_verify():
    spec = importlib.util.spec_from_file_location("v514x_verify_test", VERIFY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load verifier: {VERIFY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _load_verify()


class EvidenceFidelityFormalVerifierTest(unittest.TestCase):
    def test_zero_guard_events_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            self._write_run_commit(run_root)

            with patch.object(
                VERIFY.V514V_VERIFY,
                "evaluate",
                return_value={"checks": [], "summary": {}},
            ):
                report = VERIFY.evaluate(
                    run_root=run_root,
                    preflight_report=self._preflight(),
                )

            self.assertTrue(report["summary"]["passed"])
            self.assertEqual(
                report["summary"][
                    "required_evidence_guard_event_count"
                ],
                0,
            )

    def test_grounded_guard_event_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            self._write_run_commit(run_root)
            trace = (
                run_root
                / "B/managed/agentlite_data/sessions/session_1"
                / "autogen_driver/trace.jsonl"
            )
            trace.parent.mkdir(parents=True)
            trace.write_text(
                json.dumps(
                    {
                        "event_type": "autogen_required_evidence_guard",
                        "payload": {
                            "task_id": "generic-task",
                            "status": "blocked_and_deferred",
                            "unavailable_artifacts": ["policy_rules.md"],
                            "fallback_value": "needs_more_evidence",
                            "claimed_unavailable_artifacts": [],
                            "conflicting_decision_values": [
                                "approved_release"
                            ],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(
                VERIFY.V514V_VERIFY,
                "evaluate",
                return_value={"checks": [], "summary": {}},
            ):
                report = VERIFY.evaluate(
                    run_root=run_root,
                    preflight_report=self._preflight(),
                )

            self.assertTrue(report["summary"]["passed"])
            self.assertEqual(
                report["summary"][
                    "required_evidence_guard_invalid_event_count"
                ],
                0,
            )

    @staticmethod
    def _write_run_commit(run_root: Path) -> None:
        path = run_root / "system/git-commit.txt"
        path.parent.mkdir(parents=True)
        path.write_text(
            "22fb57618cf35b0d4a09491651424eafa38268e5\n",
            encoding="utf-8",
        )

    @staticmethod
    def _preflight() -> dict:
        v514v_prereg = (
            REPO_ROOT
            / "experiments"
            / "v5.14v-continuity-memory-formal-regression"
            / "preregistration.json"
        )
        v514w_verify = (
            REPO_ROOT
            / "experiments"
            / "v5.14w-evidence-fidelity-governance"
            / "verify_acceptance.py"
        )
        return {
            "preregistration": {
                "source_lineage": {
                    "mechanism_release_commit": (
                        "22fb57618cf35b0d4a09491651424eafa38268e5"
                    ),
                    "v514v_preregistration_sha256": _sha256(v514v_prereg),
                    "v514w_acceptance_verifier_sha256": _sha256(
                        v514w_verify
                    ),
                },
                "thresholds": {
                    "required_evidence_guard_invalid_event_count_max": 0
                },
                "scenarios": [
                    {
                        "scenario_id": "B-formal",
                        "directory": "B",
                    }
                ],
            }
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
