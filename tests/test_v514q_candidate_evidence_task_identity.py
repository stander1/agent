from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14q-candidate-evidence-task-identity"
)


def _load_verifier():
    path = STAGE_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514q_verify_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _load_verifier()


class V514QCandidateEvidenceTaskIdentityTests(unittest.TestCase):
    def test_acceptance_report_passes_for_successful_suite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "unittest.txt"
            output.write_text(
                "Ran 1 test in 0.001s\n\nOK\n",
                encoding="utf-8",
            )
            report = VERIFY.evaluate(unittest_output=output)
        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["candidate_source_chars"],
            report["summary"]["candidate_selected_chars"],
        )
        self.assertEqual(
            report["summary"]["identity_guard_false_positive_count"],
            0,
        )

    def test_production_code_has_no_benchmark_or_fixed_role_special_case(self) -> None:
        source = (
            REPO_ROOT / "agent_runtime" / "drivers" / "autogen.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "Question A",
            "Question B",
            "beneficiary_demo_999",
            "CURRENT_CANDIDATE_REQUIRED_AGENT_NAMES",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
