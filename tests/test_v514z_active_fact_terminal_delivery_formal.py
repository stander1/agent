from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_PATH = (
    REPO_ROOT
    / "experiments"
    / "v5.14z-active-fact-terminal-delivery-formal"
    / "verify_acceptance.py"
)


def _load_verify():
    spec = importlib.util.spec_from_file_location("v514z_verify_test", VERIFY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load verifier: {VERIFY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _load_verify()


class ActiveFactTerminalDeliveryFormalVerifierTest(unittest.TestCase):
    def test_release_lineage_and_evidence_hashes_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            self._write_current_commit(run_root)

            with patch.object(
                VERIFY.V514X_VERIFY,
                "evaluate",
                return_value={"checks": [], "summary": {}},
            ):
                report = VERIFY.evaluate(
                    run_root=run_root,
                    preflight_report=self._preflight(),
                )

            self.assertTrue(report["summary"]["passed"])
            self.assertEqual(report["summary"]["check_count"], 3)

    def test_tampered_mechanism_verifier_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp)
            self._write_current_commit(run_root)
            preflight = self._preflight()
            preflight["preregistration"]["source_lineage"][
                "v514y_acceptance_verifier_sha256"
            ] = "0" * 64

            with patch.object(
                VERIFY.V514X_VERIFY,
                "evaluate",
                return_value={"checks": [], "summary": {}},
            ):
                report = VERIFY.evaluate(
                    run_root=run_root,
                    preflight_report=preflight,
                )

            self.assertFalse(report["summary"]["passed"])
            failed = {
                item["name"]
                for item in report["checks"]
                if not item["passed"]
            }
            self.assertEqual(
                failed,
                {"v514y_acceptance_verifier_is_bound"},
            )

    @staticmethod
    def _write_current_commit(run_root: Path) -> None:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        path = run_root / "system/git-commit.txt"
        path.parent.mkdir(parents=True)
        path.write_text(f"{commit}\n", encoding="utf-8")

    @staticmethod
    def _preflight() -> dict:
        v514x_prereg = (
            REPO_ROOT
            / "experiments"
            / "v5.14x-evidence-fidelity-formal-regression"
            / "preregistration.json"
        )
        v514y_verify = (
            REPO_ROOT
            / "experiments"
            / "v5.14y-active-fact-terminal-delivery"
            / "verify_acceptance.py"
        )
        return {
            "preregistration": {
                "source_lineage": {
                    "mechanism_release_commit": (
                        "2cb4502c48a809c9a9a301d4b76d398b5ddf9f85"
                    ),
                    "v514x_preregistration_sha256": _sha256(
                        v514x_prereg
                    ),
                    "v514y_acceptance_verifier_sha256": _sha256(
                        v514y_verify
                    ),
                }
            }
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
