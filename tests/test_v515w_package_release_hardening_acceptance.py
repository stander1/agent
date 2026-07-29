from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT / "experiments" / "v5.15w-package-release-hardening"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515w_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15w verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PackageReleaseHardeningAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def _reports(self, path: str = "cost_guarded_fallback") -> tuple:
        metadata = {
            "name": True,
            "version_matches_runtime": True,
            "source_versions_match": True,
            "requires_tiktoken": True,
            "requires_python": True,
        }
        rewrite = {
            "name": "installed_agentlite_cli_rewrite",
            "passed": True,
            "team_takeover_path": path,
        }
        mixed = {
            "name": "installed_agentlite_cli_mixed_team_core",
            "passed": True,
            "team_takeover_path": path,
            "core_received_first": {"agentlite_prompt_view": True},
            "core_caller_reply_first": {"agentlite_prompt_view": True},
        }
        package = {
            "passed": True,
            "steps": [
                {"name": "build_meta_wheel", "passed": True},
                {
                    "name": "inspect_wheel",
                    "passed": True,
                    "metadata_checks": metadata,
                },
                {
                    "name": "verify_installed_wheel",
                    "passed": True,
                    "import_path_uses_target_site": True,
                    "doctor_ok": True,
                    "steps": [rewrite, mixed],
                },
            ],
        }
        release_names = {
            "cli_help",
            "cli_version",
            "doctor_autogen",
            "autogen_team_benchmark",
            "agentlite_cli_rewrite",
            "experiment_archive_binding",
            "autogen_studio_run_binding",
            "unittest",
            "compileall",
            "git_diff_check",
        }
        release = {
            "passed": True,
            "steps": [
                {"name": name, "passed": True}
                for name in sorted(release_names)
            ],
        }
        return package, release

    def test_cost_guarded_installed_package_passes(self) -> None:
        package, release = self._reports()
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel = Path(temp_dir) / "agentlite.whl"
            wheel.write_bytes(b"wheel")
            report = self.verifier.build_report(
                implementation_commit="abc123",
                project_version="0.5.15.dev0",
                runtime_version="0.5.15.dev0",
                package_report=package,
                release_report=release,
                wheel_path=wheel,
                wheel_members=set(
                    self.verifier.REQUIRED_SEMANTIC_MEMBERS
                ),
                wheel_sha256="a" * 64,
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertTrue(
            report["summary"]["ready_for_release_artifact_freeze"]
        )

    def test_missing_semantic_module_fails(self) -> None:
        package, release = self._reports("applied_rewrite")
        with tempfile.TemporaryDirectory() as temp_dir:
            wheel = Path(temp_dir) / "agentlite.whl"
            wheel.write_bytes(b"wheel")
            report = self.verifier.build_report(
                implementation_commit="abc123",
                project_version="0.5.15.dev0",
                runtime_version="0.5.15.dev0",
                package_report=package,
                release_report=release,
                wheel_path=wheel,
                wheel_members=set(),
                wheel_sha256="b" * 64,
            )
        failed = {
            item["name"] for item in report["checks"] if not item["passed"]
        }

        self.assertFalse(report["summary"]["passed"])
        self.assertIn("semantic_bridge_members_packaged", failed)

    def test_runner_has_no_provider_credential_dependency(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("run_package_release_gate.py", runner)
        self.assertIn("run_release_gate.py", runner)
        self.assertIn("sha256sum -c", runner)
        self.assertNotIn("OPENAI_API_KEY", runner)
        self.assertNotIn("mimoapikey", runner.casefold())


if __name__ == "__main__":
    unittest.main()
