from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


class V514ISemanticFidelityConflictIdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        path = (
            repo_root
            / "experiments"
            / "v5.14i-semantic-fidelity-conflict-identity"
            / "verify_acceptance.py"
        )
        spec = importlib.util.spec_from_file_location("v514i_verify", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.module = module
        cls.repo_root = repo_root

    def test_complete_synthetic_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            unittest_output = Path(tmp) / "unittest.txt"
            unittest_output.write_text(
                "Ran 1 test in 0.001s\n\nOK\n",
                encoding="utf-8",
            )
            report = self.module.evaluate(
                repo_root=self.repo_root,
                unittest_output=unittest_output,
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["passed_check_count"],
            report["summary"]["check_count"],
        )
        self.assertEqual(report["summary"]["duplicate_conflict_count"], 0)
        self.assertEqual(report["summary"]["partial_repair_blocked_count"], 1)


if __name__ == "__main__":
    unittest.main()
