from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.adapters.autogen_studio import (
    STUDIO_APPDIR_ENV,
    STUDIO_RUN_CONTEXT_MODULE,
    activate_run_binding,
    current_studio_run_binding,
    detect_studio_appdir,
    read_studio_run_metadata,
    reset_run_binding,
)
from agent_runtime.bootstrap.startup import BootstrapContext
from agent_runtime.drivers.autogen import AutoGenHookManager
from agent_runtime.eval.autogen_session_report import (
    RunReportRequest,
    build_autogen_run_report,
    render_autogen_run_report,
)
from web_monitor.parser import build_session_snapshot, list_framework_runs


class FakeTeam:
    _participant_names = ["planner", "writer", "reviewer"]


class AutoGenStudioRunBindingTest(unittest.TestCase):
    def test_detects_studio_appdir_without_affecting_normal_autogen_commands(self) -> None:
        cwd = Path("C:/workspace")
        self.assertEqual(
            detect_studio_appdir(
                ["autogenstudio", "ui", "--appdir", "studio-app"],
                cwd=cwd,
            ),
            (cwd / "studio-app").resolve(),
        )
        self.assertEqual(
            detect_studio_appdir(
                ["python", "-m", "autogenstudio", "ui", "--appdir=web-app"],
                cwd=cwd,
            ),
            (cwd / "web-app").resolve(),
        )
        self.assertIsNone(
            detect_studio_appdir(["python", "app.py"], cwd=cwd)
        )

    def test_resolves_native_studio_run_context_and_database_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            appdir = Path(tmp)
            self._write_studio_database(appdir)
            module = types.ModuleType(STUDIO_RUN_CONTEXT_MODULE)

            class RunContext:
                @classmethod
                def current_run_id(cls) -> int:
                    return 22

            module.RunContext = RunContext
            with patch.dict(sys.modules, {STUDIO_RUN_CONTEXT_MODULE: module}):
                binding = current_studio_run_binding()

            self.assertIsNotNone(binding)
            assert binding is not None
            self.assertEqual(binding.framework_run_id, "autogenstudio:22")
            self.assertEqual(binding.studio_run_id, "22")
            metadata = read_studio_run_metadata(appdir=appdir, run_id="22")
            self.assertEqual(metadata["session_id"], "13")
            self.assertEqual(metadata["team_id"], "16")
            self.assertEqual(metadata["status"], "COMPLETE")
            self.assertIn("budget", metadata["task_preview"])

    def test_driver_propagates_studio_run_to_nested_trace_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appdir = root / "studio"
            appdir.mkdir()
            self._write_studio_database(appdir)
            module = types.ModuleType(STUDIO_RUN_CONTEXT_MODULE)

            class RunContext:
                @classmethod
                def current_run_id(cls) -> int:
                    return 22

            module.RunContext = RunContext
            with patch.dict(sys.modules, {STUDIO_RUN_CONTEXT_MODULE: module}), patch.dict(
                "os.environ",
                {STUDIO_APPDIR_ENV: str(appdir)},
                clear=False,
            ):
                manager = AutoGenHookManager(self._context(root))
                context = manager.record_call_start(
                    instance=FakeTeam(),
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": "Create a budget-aware plan"},
                )
                token = activate_run_binding(context.run_binding)
                try:
                    manager.trace.write("nested_probe", {"agent_id": "writer"})
                    manager.record_call_end(context, "complete answer")
                finally:
                    reset_run_binding(token)

            events = self._read_events(manager.trace.path)
            probe = next(event for event in events if event["event_type"] == "nested_probe")
            self.assertEqual(probe["payload"]["framework_run_id"], "autogenstudio:22")
            self.assertEqual(probe["payload"]["studio_session_id"], "13")
            start = next(
                event
                for event in events
                if event["event_type"] == "autogen_framework_run_started"
            )
            self.assertEqual(start["payload"]["studio_team_id"], "16")
            manifest = (
                manager.output_dir
                / "runs"
                / "autogenstudio_22"
                / "run.json"
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["framework_run_id"], "autogenstudio:22")

    def test_long_fallback_run_id_uses_bounded_manifest_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = AutoGenHookManager(self._context(Path(tmp)))
            framework_run_id = "autogen:" + "launch_" + ("a" * 96) + "_000001"
            manager._write_framework_run_manifest(
                framework_run_id,
                {"framework_run_id": framework_run_id, "status": "completed"},
            )
            manifests = list((manager.output_dir / "runs").glob("*/run.json"))
            self.assertEqual(len(manifests), 1)
            self.assertLessEqual(len(manifests[0].parent.name), 16)
            payload = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["framework_run_id"], framework_run_id)

    def test_run_report_and_monitor_snapshot_exclude_other_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            session_id = "launch_studio"
            session_dir = data_dir / "sessions" / session_id
            trace_dir = session_dir / "autogen_driver"
            trace_dir.mkdir(parents=True)
            (session_dir / "bootstrap_status.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "framework": "autogen",
                        "session_id": session_id,
                        "driver": "autogen",
                        "driver_status": "active",
                        "hooks_active": True,
                    }
                ),
                encoding="utf-8",
            )
            events = self._run_events("22", 10, 5, 15) + self._run_events(
                "23", 100, 40, 140
            )
            (trace_dir / "trace.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events),
                encoding="utf-8",
            )

            listed = list_framework_runs(data_dir)
            self.assertEqual(len(listed), 2)
            self.assertEqual(listed[0]["framework_run_id"], "autogenstudio:23")
            run_22 = next(item for item in listed if item["studio_run_id"] == "22")
            self.assertEqual(run_22["token_summary"]["llm_total_tokens"], 15)
            snapshot = build_session_snapshot(
                session_dir,
                session_id=session_id,
                framework_run_id="autogenstudio:22",
            )
            self.assertEqual(snapshot["studio_run_id"], "22")
            self.assertEqual(snapshot["token_summary"]["llm_total_tokens"], 15)
            report = build_autogen_run_report(
                RunReportRequest(
                    data_dir=data_dir,
                    session_id=session_id,
                    run_id="autogenstudio:22",
                )
            )
            self.assertEqual(report["token_summary"]["llm_total_tokens"], 15)
            self.assertEqual(report["token_summary"]["llm_call_count"], 1)
            markdown = render_autogen_run_report(report, "markdown")
            self.assertIn("AutoGen Run Token 报告", markdown)
            self.assertIn("autogenstudio:22", markdown)
            latest = build_autogen_run_report(
                RunReportRequest(
                    data_dir=data_dir,
                    session_id=session_id,
                    run_id="latest",
                )
            )
            self.assertEqual(latest["framework_run_id"], "autogenstudio:23")
            self.assertEqual(latest["token_summary"]["llm_total_tokens"], 140)

    @staticmethod
    def _context(root: Path) -> BootstrapContext:
        status_file = root / "sessions" / "launch_studio" / "bootstrap_status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        return BootstrapContext(
            framework="autogen",
            session_id="launch_studio",
            data_dir=root,
            status_file=status_file,
            target_cwd=workspace,
        )

    @staticmethod
    def _write_studio_database(appdir: Path) -> None:
        connection = sqlite3.connect(appdir / "autogen04202.db")
        connection.executescript(
            """
            CREATE TABLE session (
                id INTEGER PRIMARY KEY,
                team_id INTEGER,
                name TEXT
            );
            CREATE TABLE run (
                id INTEGER PRIMARY KEY,
                session_id INTEGER,
                status TEXT,
                created_at TEXT,
                updated_at TEXT,
                task JSON
            );
            INSERT INTO session(id, team_id, name)
            VALUES (13, 16, 'Studio test session');
            INSERT INTO run(id, session_id, status, created_at, updated_at, task)
            VALUES (
                22,
                13,
                'COMPLETE',
                '2026-07-21T00:00:00',
                '2026-07-21T00:01:00',
                '{"content": "Create a budget-aware plan"}'
            );
            """
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _run_events(
        run_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> list[dict[str, object]]:
        fields = {
            "framework_run_id": f"autogenstudio:{run_id}",
            "framework_run_source": "autogenstudio_run_context",
            "studio_run_id": run_id,
            "studio_session_id": "13",
            "studio_team_id": "16",
        }
        return [
            {
                "ts": f"2026-07-21T00:00:{run_id}+00:00",
                "event_type": "autogen_framework_run_started",
                "payload": {
                    **fields,
                    "status": "running",
                    "task_preview": f"Task {run_id}",
                },
            },
            {
                "ts": f"2026-07-21T00:01:{run_id}+00:00",
                "event_type": "autogen_model_client_usage",
                "payload": {
                    **fields,
                    "llm_prompt_tokens": prompt_tokens,
                    "llm_completion_tokens": completion_tokens,
                    "llm_total_tokens": total_tokens,
                },
            },
            {
                "ts": f"2026-07-21T00:02:{run_id}+00:00",
                "event_type": "autogen_framework_run_finished",
                "payload": {**fields, "status": "completed"},
            },
        ]

    @staticmethod
    def _read_events(path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


if __name__ == "__main__":
    unittest.main()
