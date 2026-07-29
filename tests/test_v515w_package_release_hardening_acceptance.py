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


def _load_team_benchmark() -> ModuleType:
    examples_dir = REPO_ROOT / "examples"
    if str(examples_dir) not in sys.path:
        sys.path.insert(0, str(examples_dir))
    path = examples_dir / "run_autogen_team_benchmark.py"
    spec = importlib.util.spec_from_file_location("v515w_team_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load Team benchmark")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PackageReleaseHardeningAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()
        cls.team_benchmark = _load_team_benchmark()

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

    def test_team_benchmark_accepts_cost_guarded_native_fallback(self) -> None:
        native_first = {
            "content": "task TEAM_BENCH_NATIVE_MARKER",
        }
        managed_first = {
            "content": "task TEAM_BENCH_NATIVE_MARKER",
        }
        quality = {"score": 1, "max_score": 1}
        team = {
            "event_count": 1,
            "applied_count": 0,
            "fallback_count": 1,
            "fallback_reasons": ["token_not_reduced"],
            "real_message_mutation_count": 0,
            "token_delta_native_task_minus_rewrite": -2,
            "token_delta_native_broadcast_minus_rewrite": -3,
        }
        native = {
            "returncode": 0,
            "app_payload": {"agentlite_active": False},
            "first_stream_item": native_first,
            "quality": quality,
        }
        managed = {
            "returncode": 0,
            "bootstrap_ok": True,
            "hooks_active": True,
            "driver_phase": self.team_benchmark.EXPECTED_PHASE,
            "broadcast_mode": "real-rewrite",
            "team_rewrite_enabled": True,
            "app_payload": {"agentlite_active": True},
            "first_stream_item": managed_first,
            "trace_event_counts": {},
            "team_input_real_rewrite": team,
            "quality": quality,
        }
        comparison = {
            "visible_input_token_delta_native_minus_managed": 0,
            "quality_delta_managed_minus_native": 0,
        }

        checks = self.team_benchmark.build_checks(
            native=native,
            managed=managed,
            comparison=comparison,
            source_text="AutoGen-only app",
        )

        self.assertTrue(all(checks.values()))


if __name__ == "__main__":
    unittest.main()
