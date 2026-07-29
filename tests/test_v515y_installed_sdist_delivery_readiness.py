from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.15y-installed-sdist-delivery-readiness"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515y_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15y verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class InstalledSdistDeliveryReadinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def _base_report(self) -> dict:
        return {
            "summary": {
                "passed": True,
                "technical_release_candidate_ready": True,
                "open_source_publication_ready": False,
            },
            "implementation_commit": "abc123",
            "checks": [
                {"name": "base-rc", "passed": True, "detail": "passed"}
            ],
            "publication": {
                "license_present": False,
                "blockers": ["explicit_repository_license_missing"],
            },
            "release_artifacts": {"actual_sha256": {}},
        }

    def _artifact_report(self) -> dict:
        return {
            "steps": [
                {
                    "name": "verify_installed_sdist",
                    "passed": True,
                    "install_returncode": 0,
                    "probe_returncode": 0,
                    "cli_returncode": 0,
                    "loaded_from_target": True,
                    "cli_version_ok": True,
                    "runtime_version": "0.5.15rc2",
                    "distribution_version": "0.5.15rc2",
                    "missing_members": [],
                }
            ]
        }

    def _project_metadata(self) -> dict:
        return {
            "version": "0.5.15rc2",
            "urls": {
                "Source": "https://github.com/stander1/agent",
                "Issues": "https://github.com/stander1/agent/issues",
            },
            "classifiers": [
                "Development Status :: 4 - Beta",
                "Programming Language :: Python :: 3",
                "Operating System :: OS Independent",
            ],
        }

    def _delivery_guide(self) -> str:
        return (
            "v0.5.15rc2 openEuler sdist 隔离安装 "
            "SHA256 公开发布阻塞项"
        )

    def test_technical_gate_passes_with_license_blocker(self) -> None:
        report = self.verifier.build_report(
            base_report=self._base_report(),
            artifact_report=self._artifact_report(),
            project_metadata=self._project_metadata(),
            delivery_guide=self._delivery_guide(),
        )

        self.assertTrue(report["summary"]["passed"])
        self.assertTrue(report["summary"]["installed_sdist_verified"])
        self.assertFalse(
            report["summary"]["open_source_publication_ready"]
        )

    def test_installed_sdist_failure_blocks_technical_rc(self) -> None:
        artifacts = self._artifact_report()
        artifacts["steps"][0]["loaded_from_target"] = False
        artifacts["steps"][0]["passed"] = False
        report = self.verifier.build_report(
            base_report=self._base_report(),
            artifact_report=artifacts,
            project_metadata=self._project_metadata(),
            delivery_guide=self._delivery_guide(),
        )
        failed = {
            item["name"] for item in report["checks"] if not item["passed"]
        }

        self.assertFalse(report["summary"]["passed"])
        self.assertIn(
            "release_sdist_installed_outside_checkout",
            failed,
        )

    def test_runner_is_provider_free_and_keeps_all_release_gates(
        self,
    ) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("run_package_release_gate.py", runner)
        self.assertIn("run_release_gate.py", runner)
        self.assertIn("build_release_artifacts.py", runner)
        self.assertIn("sha256sum -c", runner)
        self.assertNotIn("OPENAI_API_KEY", runner)

    def test_artifact_builder_has_isolated_sdist_install_contract(
        self,
    ) -> None:
        builder = (
            REPO_ROOT / "examples" / "build_release_artifacts.py"
        ).read_text(encoding="utf-8")

        self.assertIn("verify_installed_sdist", builder)
        self.assertIn('"--no-deps"', builder)
        self.assertIn('"--no-build-isolation"', builder)
        self.assertIn("loaded_from_target", builder)


if __name__ == "__main__":
    unittest.main()
