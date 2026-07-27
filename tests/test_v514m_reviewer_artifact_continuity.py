from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT / "experiments" / "v5.14m-reviewer-artifact-continuity"
)


def _load_verify():
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514m_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _load_verify()


class ReviewerArtifactContinuityAcceptanceTest(unittest.TestCase):
    def test_deterministic_acceptance_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unittest_output = Path(tmp) / "unittest.txt"
            unittest_output.write_text(
                "Ran 1 test in 0.001s\n\nOK\n",
                encoding="utf-8",
            )
            report = VERIFY.evaluate(unittest_output=unittest_output)

        self.assertTrue(report["summary"]["passed"])
        self.assertTrue(report["summary"]["prior_artifact_promoted"])
        self.assertEqual(report["summary"]["stored_decision_count"], 1)

    def test_new_agent_profiles_use_reference_approval(self) -> None:
        for name in ("agent_config_A.json", "agent_config_B.json"):
            text = (EXPERIMENT_DIR / name).read_text(encoding="utf-8")
            self.assertIn("验收者而不是成果作者", text)
            self.assertIn("批准上一份 Writer 成果作为最终交付物", text)
            self.assertIn("不要重复成果正文", text)

    def test_windows_utf16_unittest_output_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unittest_output = Path(tmp) / "unittest.txt"
            unittest_output.write_text(
                "Ran 1 test in 0.001s\n\nOK\n",
                encoding="utf-16",
            )
            report = VERIFY.evaluate(unittest_output=unittest_output)

        self.assertTrue(report["summary"]["passed"])

    def test_runner_preserves_immutable_evidence(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("git diff --quiet", runner)
        self.assertIn("sha256sum -c", runner)
        self.assertIn("AGENTLITE_V514M_EXP_ID", runner)


if __name__ == "__main__":
    unittest.main()
