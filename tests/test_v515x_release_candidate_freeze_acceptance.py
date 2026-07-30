from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT / "experiments" / "v5.15x-release-candidate-freeze"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515x_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15x verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_release_contract() -> ModuleType:
    path = REPO_ROOT / "examples" / "release_package_contract.py"
    spec = importlib.util.spec_from_file_location(
        "v515x_release_package_contract",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load release package contract")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReleaseCandidateFreezeAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def _base_report(self) -> dict:
        checks = [
            {"name": "base-package", "passed": True, "detail": "passed"}
        ]
        return {
            "summary": {"passed": True},
            "implementation_commit": "abc123",
            "checks": checks,
            "wheel": {"sha256": "a" * 64},
        }

    def _artifact_report(self) -> dict:
        return {
            "passed": True,
            "version": "0.5.15rc1",
            "runtime_version": "0.5.15rc1",
            "source_versions_match": True,
            "artifacts": {
                "wheel": {"sha256": "b" * 64},
                "sdist": {"sha256": "c" * 64},
            },
            "steps": [
                {
                    "name": "inspect_wheel",
                    "passed": True,
                    "missing_members": [],
                    "forbidden_members": [],
                },
                {
                    "name": "inspect_sdist",
                    "passed": True,
                    "missing_members": [],
                    "forbidden_members": [],
                },
            ],
        }

    def test_technical_rc_passes_with_explicit_license_blocker(self) -> None:
        report = self.verifier.build_report(
            base_report=self._base_report(),
            artifact_report=self._artifact_report(),
            actual_artifact_sha256={
                "wheel": "b" * 64,
                "sdist": "c" * 64,
            },
            license_present=False,
        )

        self.assertTrue(report["summary"]["passed"])
        self.assertTrue(
            report["summary"]["technical_release_candidate_ready"]
        )
        self.assertFalse(
            report["summary"]["open_source_publication_ready"]
        )
        self.assertEqual(
            report["publication"]["blockers"],
            ["explicit_repository_license_missing"],
        )

    def test_artifact_hash_mismatch_fails(self) -> None:
        report = self.verifier.build_report(
            base_report=self._base_report(),
            artifact_report=self._artifact_report(),
            actual_artifact_sha256={
                "wheel": "d" * 64,
                "sdist": "c" * 64,
            },
            license_present=True,
        )
        failed = {
            item["name"] for item in report["checks"] if not item["passed"]
        }

        self.assertFalse(report["summary"]["passed"])
        self.assertIn("release_artifact_hashes_verified", failed)

    def test_explicit_license_clears_publication_blocker(self) -> None:
        report = self.verifier.build_report(
            base_report=self._base_report(),
            artifact_report=self._artifact_report(),
            actual_artifact_sha256={
                "wheel": "b" * 64,
                "sdist": "c" * 64,
            },
            license_present=True,
        )

        self.assertTrue(report["summary"]["passed"])
        self.assertTrue(
            report["summary"]["open_source_publication_ready"]
        )
        self.assertEqual(report["publication"]["blockers"], [])

    def test_final_release_version_is_accepted_by_inherited_gate(self) -> None:
        artifacts = self._artifact_report()
        artifacts["version"] = "0.5.15"
        artifacts["runtime_version"] = "0.5.15"
        report = self.verifier.build_report(
            base_report=self._base_report(),
            artifact_report=artifacts,
            actual_artifact_sha256={
                "wheel": "b" * 64,
                "sdist": "c" * 64,
            },
            license_present=True,
        )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(report["summary"]["release_version"], "0.5.15")

    def test_runner_uses_all_three_release_gates(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("run_package_release_gate.py", runner)
        self.assertIn("run_release_gate.py", runner)
        self.assertIn("build_release_artifacts.py", runner)
        self.assertIn("sha256sum -c", runner)
        self.assertNotIn("OPENAI_API_KEY", runner)

    def test_sdist_contract_contains_release_candidate_reproduction_files(
        self,
    ) -> None:
        contract = _load_release_contract()
        required = contract.REQUIRED_SDIST_MEMBERS

        self.assertIn(
            "docs/planning/v5.15x-release-candidate-freeze.md",
            required,
        )
        self.assertIn(
            "docs/competition/RESULTS_SNAPSHOT.md",
            required,
        )
        self.assertIn(
            "docs/competition/DEVELOPMENT_RECORD.md",
            required,
        )
        self.assertIn("LICENSE", required)
        self.assertEqual(contract.REQUIRED_LICENSE_EXPRESSION, "Apache-2.0")
        self.assertTrue(
            contract.apache_license_text_valid(
                (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
            )
        )
        self.assertIn(
            "experiments/v5.15x-release-candidate-freeze/verify_acceptance.py",
            required,
        )
        self.assertIn(
            "experiments/v5.15x-release-candidate-freeze/run_openeuler.sh",
            required,
        )
        self.assertIn(
            "docs/experiments/",
            contract.FORBIDDEN_DISTRIBUTION_PREFIXES,
        )
        self.assertIn(
            "docs/history/",
            contract.FORBIDDEN_DISTRIBUTION_PREFIXES,
        )
        self.assertIn(
            "experiments/v5.15z-release-token-quality-formal/",
            contract.FORBIDDEN_DISTRIBUTION_PREFIXES,
        )


if __name__ == "__main__":
    unittest.main()
