from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from agent_runtime.cli import build_managed_environment_overlay, build_parser
from agent_runtime.bootstrap.startup import BootstrapContext
from agent_runtime.drivers.loader import load_and_activate_driver
from agent_runtime.launcher import (
    LaunchRequest,
    ManagedProcessLauncher,
    read_bootstrap_status,
)


class ManagedProcessLauncherTest(unittest.TestCase):
    def test_probe_driver_runs_before_unmodified_target_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_output = root / "target.json"
            script = root / "app.py"
            script.write_text(
                "\n".join(
                    [
                        "import json",
                        "import os",
                        "from pathlib import Path",
                        (
                            f"Path({str(target_output)!r}).write_text("
                            "json.dumps({"
                            "'probe': os.getenv('AGENTLITE_PROBE_DRIVER_ACTIVE'),"
                            "'session': os.getenv('AGENTLITE_ACTIVE_SESSION_ID')"
                            "}), encoding='utf-8')"
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            request = LaunchRequest(
                framework="probe",
                command=[sys.executable, str(script)],
                cwd=root,
                data_dir=root / "data",
            )

            result = ManagedProcessLauncher().launch(request)

            self.assertEqual(result.returncode, 0)
            target = json.loads(target_output.read_text(encoding="utf-8"))
            status = read_bootstrap_status(result.status_file)
            self.assertEqual(target["probe"], "1")
            self.assertEqual(target["session"], result.session_id)
            self.assertIsNotNone(status)
            self.assertTrue(status["ok"])
            self.assertTrue(status["hooks_active"])
            self.assertEqual(status["driver"], "probe")

    def test_unknown_framework_stops_target_before_user_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_output = root / "should_not_exist.txt"
            script = root / "app.py"
            script.write_text(
                (
                    "from pathlib import Path\n"
                    f"Path({str(target_output)!r}).write_text('ran', encoding='utf-8')\n"
                ),
                encoding="utf-8",
            )
            request = LaunchRequest(
                framework="unknown",
                command=[sys.executable, str(script)],
                cwd=root,
                data_dir=root / "data",
            )

            result = ManagedProcessLauncher().launch(request)

            self.assertEqual(result.returncode, 78)
            self.assertFalse(target_output.exists())
            status = read_bootstrap_status(result.status_file)
            self.assertIsNotNone(status)
            self.assertFalse(status["ok"])
            self.assertIn("unknown framework", status["error"])

    def test_python_flags_that_disable_injection_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = LaunchRequest(
                framework="probe",
                command=[sys.executable, "-S", "-c", "print('no bootstrap')"],
                cwd=root,
                data_dir=root / "data",
            )

            with self.assertRaisesRegex(ValueError, "disable AgentLite"):
                ManagedProcessLauncher().validate_request(request)

    def test_autogen_driver_installs_import_hook_without_framework_package(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = BootstrapContext(
                framework="autogen",
                session_id="test-session",
                data_dir=root,
                status_file=root / "status.json",
                target_cwd=root,
            )

            activation = load_and_activate_driver(context)

            self.assertEqual(activation.status, "active")
            self.assertTrue(activation.hooks_active)
            self.assertEqual(activation.details["phase"], "v5.13h")
            self.assertEqual(activation.details["broadcast_mode"], "shadow-only")
            self.assertEqual(
                activation.details["hook_mode"], "managed_import_patch"
            )

    def test_autogen_driver_patches_agentchat_on_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_dir = root / "fakepkg" / "autogen_agentchat" / "agents"
            package_dir.mkdir(parents=True)
            (package_dir.parent / "__init__.py").write_text("", encoding="utf-8")
            (package_dir / "__init__.py").write_text(
                "\n".join(
                    [
                        "class AssistantAgent:",
                        "    def __init__(self, name):",
                        "        self.name = name",
                        "    async def on_messages(self, messages, cancellation_token=None):",
                        "        return {'content': 'reply:' + messages[0]}",
                    ]
                ),
                encoding="utf-8",
            )
            target_output = root / "target.json"
            script = root / "app.py"
            script.write_text(
                "\n".join(
                    [
                        "import asyncio",
                        "import json",
                        "import os",
                        "from pathlib import Path",
                        "from autogen_agentchat.agents import AssistantAgent",
                        "async def main():",
                        "    agent = AssistantAgent('writer')",
                        "    result = await agent.on_messages(['hello'], cancellation_token=None)",
                        (
                            f"    Path({str(target_output)!r}).write_text("
                            "json.dumps({"
                            "'active': os.getenv('AGENTLITE_AUTOGEN_DRIVER_ACTIVE'),"
                            "'result': result['content']"
                            "}), encoding='utf-8')"
                        ),
                        "asyncio.run(main())",
                    ]
                ),
                encoding="utf-8",
            )
            request = LaunchRequest(
                framework="autogen",
                command=[sys.executable, str(script)],
                cwd=root,
                data_dir=root / "data",
            )

            result = ManagedProcessLauncher().launch(
                request,
                environ={
                    **os.environ,
                    "PYTHONPATH": str(root / "fakepkg"),
                },
            )

            self.assertEqual(result.returncode, 0)
            target = json.loads(target_output.read_text(encoding="utf-8"))
            self.assertEqual(target["active"], "1")
            self.assertEqual(target["result"], "reply:hello")
            status = read_bootstrap_status(result.status_file)
            self.assertIsNotNone(status)
            self.assertTrue(status["hooks_active"])
            self.assertTrue(status["framework_available"])
            self.assertEqual(status["driver_status"], "active")
            details = status["driver_details"]
            self.assertEqual(details["phase"], "v5.13h")
            self.assertEqual(details["broadcast_mode"], "shadow-only")
            self.assertIn("autogen_agentchat", details["available_modules"])

            trace_path = result.status_file.parent / "autogen_driver" / "trace.jsonl"
            trace = trace_path.read_text(encoding="utf-8")
            self.assertIn("autogen_agent_receive", trace)
            self.assertIn("autogen_agent_output", trace)
            self.assertIn("autogen_shp_handoff_shadow", trace)
            self.assertIn("artifact_state", trace)

    def test_existing_pythonpath_is_preserved_after_bootstrap_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request = LaunchRequest(
                framework="probe",
                command=[sys.executable, "-c", "print('ok')"],
                cwd=root,
                data_dir=root / "data",
            )
            launcher = ManagedProcessLauncher()
            status_file = root / "status.json"

            env = launcher.build_environment(
                request,
                session_id="session-test",
                status_file=status_file,
                environ={"PYTHONPATH": "existing-path"},
            )

            paths = env["PYTHONPATH"].split(os.pathsep)
            self.assertEqual(paths[-1], "existing-path")
            self.assertTrue(paths[0].endswith("agent_runtime\\bootstrap") or paths[0].endswith("agent_runtime/bootstrap"))

    def test_existing_sitecustomize_is_chained_after_agentlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing_dir = root / "existing"
            existing_dir.mkdir()
            (existing_dir / "sitecustomize.py").write_text(
                (
                    "import os\n"
                    "os.environ['EXISTING_SITECUSTOMIZE_ACTIVE'] = '1'\n"
                ),
                encoding="utf-8",
            )
            target_output = root / "target.json"
            script = root / "app.py"
            script.write_text(
                "\n".join(
                    [
                        "import json",
                        "import os",
                        "from pathlib import Path",
                        (
                            f"Path({str(target_output)!r}).write_text("
                            "json.dumps({"
                            "'agentlite': os.getenv('AGENTLITE_PROBE_DRIVER_ACTIVE'),"
                            "'existing': os.getenv('EXISTING_SITECUSTOMIZE_ACTIVE')"
                            "}), encoding='utf-8')"
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            request = LaunchRequest(
                framework="probe",
                command=[sys.executable, str(script)],
                cwd=root,
                data_dir=root / "data",
            )

            result = ManagedProcessLauncher().launch(
                request,
                environ={
                    **os.environ,
                    "PYTHONPATH": str(existing_dir),
                },
            )

            self.assertEqual(result.returncode, 0)
            target = json.loads(target_output.read_text(encoding="utf-8"))
            self.assertEqual(target["agentlite"], "1")
            self.assertEqual(target["existing"], "1")


class AgentLiteCliTest(unittest.TestCase):
    def test_autogen_subcommand_defaults_to_full_takeover(self) -> None:
        args = build_parser().parse_args(
            ["autogen", "--cwd", ".", "--", sys.executable, "app.py"]
        )

        self.assertEqual(args.subcommand, "autogen")
        self.assertEqual(args.rewrite, "all")
        self.assertEqual(args.command, ["--", sys.executable, "app.py"])

    def test_run_subcommand_keeps_explicit_framework_and_rewrite(self) -> None:
        args = build_parser().parse_args(
            [
                "run",
                "--framework",
                "autogen",
                "--rewrite",
                "team",
                "--",
                sys.executable,
                "app.py",
            ]
        )

        self.assertEqual(args.subcommand, "run")
        self.assertEqual(args.framework, "autogen")
        self.assertEqual(args.rewrite, "team")
        self.assertEqual(args.command, ["--", sys.executable, "app.py"])

    def test_monitor_subcommand_accepts_runs_and_data_dirs(self) -> None:
        args = build_parser().parse_args(
            [
                "monitor",
                "--host",
                "127.0.0.1",
                "--port",
                "9000",
                "--runs-dir",
                "runs",
                "--data-dir",
                "agentlite-data",
            ]
        )

        self.assertEqual(args.subcommand, "monitor")
        self.assertEqual(args.port, 9000)
        self.assertEqual(str(args.runs_dir), "runs")
        self.assertEqual(str(args.data_dir), "agentlite-data")

    def test_autogen_rewrite_all_sets_release_env_switches(self) -> None:
        env = build_managed_environment_overlay(
            framework="autogen",
            rewrite="all",
            broadcast_mode=None,
            base={"EXISTING": "1"},
        )

        self.assertEqual(env["EXISTING"], "1")
        self.assertEqual(env["AGENTLITE_AUTOGEN_BROADCAST_MODE"], "real-rewrite")
        self.assertEqual(env["AGENTLITE_AUTOGEN_TEAM_REWRITE"], "1")
        self.assertEqual(env["AGENTLITE_AUTOGEN_HANDOFF_REWRITE"], "1")
        self.assertEqual(env["AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE"], "1")
        self.assertEqual(env["AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE"], "1")
        self.assertEqual(
            env["AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE"],
            "prompt-view",
        )

    def test_broadcast_mode_overrides_rewrite_preset_for_diagnostics(self) -> None:
        env = build_managed_environment_overlay(
            framework="autogen",
            rewrite="all",
            broadcast_mode="dry-run-rewrite",
            base={},
        )

        self.assertEqual(
            env["AGENTLITE_AUTOGEN_BROADCAST_MODE"],
            "dry-run-rewrite",
        )
        self.assertEqual(env["AGENTLITE_AUTOGEN_TEAM_REWRITE"], "1")
        self.assertEqual(env["AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE"], "1")
        self.assertEqual(
            env["AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE"],
            "prompt-view",
        )

    def test_rewrite_preset_is_ignored_for_non_autogen_frameworks(self) -> None:
        env = build_managed_environment_overlay(
            framework="probe",
            rewrite="all",
            broadcast_mode="real-rewrite",
            base={},
        )

        self.assertNotIn("AGENTLITE_AUTOGEN_BROADCAST_MODE", env)
        self.assertNotIn("AGENTLITE_AUTOGEN_TEAM_REWRITE", env)
        self.assertNotIn("AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE", env)
        self.assertNotIn("AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE", env)


if __name__ == "__main__":
    unittest.main()
