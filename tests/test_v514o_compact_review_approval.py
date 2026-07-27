from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14o-compact-review-approval"
)


def _load_verifier():
    path = STAGE_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514o_verify_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _load_verifier()


class V514OCompactReviewApprovalTests(unittest.TestCase):
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
            report["summary"]["approval_variant_count"],
            report["summary"]["promoted_variant_count"],
        )

    def test_fixture_does_not_name_benchmark_tasks(self) -> None:
        source = (STAGE_DIR / "verify_acceptance.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Question A", source)
        self.assertNotIn("Question B", source)
        self.assertNotIn("A10", source)
        self.assertNotIn("B10", source)


if __name__ == "__main__":
    unittest.main()
