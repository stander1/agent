from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from agent_runtime.eval.experiment_archive import (
    AGENTLITE_SESSION_RESULT_FILE,
    EXPERIMENT_RESULT_FILE,
    EXPERIMENT_RUN_FILE,
    complete_experiment_archive,
    create_agentlite_session_binding,
    initialize_experiment_archive,
    verify_bound_experiment,
)
from agent_runtime.launcher import LaunchRequest, ManagedProcessLauncher


class ExperimentArchiveTest(unittest.TestCase):
    def test_archive_refuses_reuse_and_keeps_existing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "run"
            identity = initialize_experiment_archive(
                output_dir=output_dir,
                scenario_id="generic-sequence",
                experiment_mode="native",
            )
            original = (output_dir / EXPERIMENT_RUN_FILE).read_text(encoding="utf-8")

            with self.assertRaises(FileExistsError):
                initialize_experiment_archive(
                    output_dir=output_dir,
                    scenario_id="second-run",
                    experiment_mode="native",
                )

            self.assertEqual(
                (output_dir / EXPERIMENT_RUN_FILE).read_text(encoding="utf-8"),
                original,
            )
            self.assertTrue(identity.run_id.startswith("run_"))

    def test_binding_verification_rejects_provider_usage_from_other_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "agentlite"
            session_id = "launch_expected"
            session_dir = data_dir / "sessions" / session_id
            session_dir.mkdir(parents=True)
            experiment_dir = root / "experiment"
            create_agentlite_session_binding(
                experiment_dir=experiment_dir,
                session_id=session_id,
                data_dir=data_dir,
                session_dir=session_dir,
                framework="autogen",
                cwd=root,
                command=[sys.executable, "app.py"],
            )
            identity = initialize_experiment_archive(
                output_dir=experiment_dir,
                scenario_id="generic-sequence",
                experiment_mode="managed",
                environ={
                    "AGENTLITE_SESSION_ID": session_id,
                    "AGENTLITE_DATA_DIR": str(data_dir),
                },
            )
            usage = {
                "calls": 1,
                "llm_prompt_tokens": 7,
                "llm_completion_tokens": 3,
                "llm_total_tokens": 10,
                "binding": {
                    **identity.binding(),
                    "agentlite_session_id": "launch_wrong",
                },
            }
            (experiment_dir / "llm_usage_summary.json").write_text(
                json.dumps(usage), encoding="utf-8"
            )
            complete_experiment_archive(
                identity,
                summary={"llm_total_tokens": 10},
                artifact_paths=[experiment_dir / "llm_usage_summary.json"],
            )

            with self.assertRaisesRegex(ValueError, "usage_session_matches"):
                verify_bound_experiment(experiment_dir)

    def test_launcher_binds_child_archive_to_exact_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "agentlite"
            experiment_dir = root / "experiment"
            script = root / "app.py"
            script.write_text(
                "\n".join(
                    [
                        "import json, os",
                        "from pathlib import Path",
                        "from agent_runtime.eval.experiment_archive import initialize_experiment_archive, complete_experiment_archive",
                        "out = Path(os.environ['AGENTLITE_EXPERIMENT_DIR'])",
                        "identity = initialize_experiment_archive(output_dir=out, scenario_id='generic', experiment_mode='managed')",
                        "usage = {'calls': 1, 'llm_prompt_tokens': 2, 'llm_completion_tokens': 1, 'llm_total_tokens': 3, 'binding': identity.binding()}",
                        "usage_path = out / 'llm_usage_summary.json'",
                        "usage_path.write_text(json.dumps(usage), encoding='utf-8')",
                        "complete_experiment_archive(identity, summary={'llm_total_tokens': 3}, artifact_paths=[usage_path])",
                    ]
                ),
                encoding="utf-8",
            )
            request = LaunchRequest(
                framework="probe",
                command=[sys.executable, str(script)],
                cwd=root,
                data_dir=data_dir,
                experiment_dir=experiment_dir,
            )

            result = ManagedProcessLauncher().launch(request)

            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.binding_verified)
            self.assertEqual(result.binding_error, "")
            self.assertTrue((experiment_dir / EXPERIMENT_RESULT_FILE).is_file())
            self.assertTrue((experiment_dir / AGENTLITE_SESSION_RESULT_FILE).is_file())
            verified = verify_bound_experiment(experiment_dir)
            self.assertEqual(verified["session_id"], result.session_id)
            with self.assertRaises(ValueError):
                ManagedProcessLauncher().launch(request)
            archived_status = (
                experiment_dir
                / "agentlite_data"
                / "sessions"
                / result.session_id
                / "bootstrap_status.json"
            )
            archived_status.write_text(
                archived_status.read_text(encoding="utf-8") + " ",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "session_archive_hash_matches"):
                verify_bound_experiment(experiment_dir)

    def test_autogen_launcher_exports_reports_from_bound_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experiment_dir = root / "experiment"
            script = root / "app.py"
            script.write_text(
                "\n".join(
                    [
                        "import json, os",
                        "from pathlib import Path",
                        "from agent_runtime.eval.experiment_archive import initialize_experiment_archive, complete_experiment_archive",
                        "out = Path(os.environ['AGENTLITE_EXPERIMENT_DIR'])",
                        "identity = initialize_experiment_archive(output_dir=out, scenario_id='generic', experiment_mode='managed')",
                        "usage = {'calls': 1, 'llm_prompt_tokens': 5, 'llm_completion_tokens': 2, 'llm_total_tokens': 7, 'binding': identity.binding()}",
                        "usage_path = out / 'llm_usage_summary.json'",
                        "usage_path.write_text(json.dumps(usage), encoding='utf-8')",
                        "complete_experiment_archive(identity, summary={'llm_total_tokens': 7}, artifact_paths=[usage_path])",
                    ]
                ),
                encoding="utf-8",
            )
            result = ManagedProcessLauncher().launch(
                LaunchRequest(
                    framework="autogen",
                    command=[sys.executable, str(script)],
                    cwd=root,
                    data_dir=root / "agentlite",
                    experiment_dir=experiment_dir,
                )
            )

            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.binding_verified)
            self.assertEqual(len(result.report_files), 2)
            report = json.loads(
                (experiment_dir / "agentlite_session_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(report["session_id"], result.session_id)
            self.assertTrue(report["experiment_binding_verified"])
            self.assertEqual(report["token_summary"]["llm_total_tokens"], 7)


if __name__ == "__main__":
    unittest.main()
