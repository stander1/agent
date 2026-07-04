from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.eval.autogen_session_report import (
    SessionReportRequest,
    build_autogen_session_report,
    render_autogen_session_report,
    write_autogen_session_report,
)


class AutoGenSessionReportTest(unittest.TestCase):
    def test_builds_token_report_from_latest_session_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._write_session(data_dir, "web_case")

            report = build_autogen_session_report(
                SessionReportRequest(data_dir=data_dir, session_id="latest")
            )

            self.assertEqual(report["session_id"], "web_case")
            self.assertTrue(report["hooks_active"])
            token_summary = report["token_summary"]
            self.assertEqual(token_summary["native_collaboration_tokens"], 1200)
            self.assertEqual(token_summary["agentlite_runtime_tokens"], 300)
            self.assertEqual(token_summary["agentlite_token_savings"], 900)
            self.assertAlmostEqual(
                token_summary["agentlite_token_savings_ratio"],
                0.75,
            )
            metrics = {row["metric"]: row["value"] for row in report["metric_rows"]}
            self.assertEqual(metrics["llm_call_count"], 1)
            self.assertEqual(metrics["llm_prompt_tokens"], 21)
            self.assertEqual(metrics["llm_completion_tokens"], 9)
            self.assertEqual(metrics["llm_total_tokens"], 30)
            self.assertEqual(metrics["agentlite_direct_message_tokens"], 130)
            self.assertEqual(metrics["agentlite_prompt_view_tokens"], 170)

    def test_renders_csv_and_writes_markdown_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            output = Path(tmp) / "report.md"
            self._write_session(data_dir, "web_case")
            report = build_autogen_session_report(
                SessionReportRequest(data_dir=data_dir, session_id="web_case")
            )

            csv_text = render_autogen_session_report(report, "csv")
            self.assertIn("session_id,metric,value,meaning", csv_text)
            self.assertIn("web_case,agentlite_runtime_tokens,300", csv_text)

            written = write_autogen_session_report(
                SessionReportRequest(
                    data_dir=data_dir,
                    session_id="web_case",
                    output=output,
                    report_format="markdown",
                )
            )
            self.assertEqual(written["output_path"], str(output.resolve()))
            text = output.read_text(encoding="utf-8")
            self.assertIn("# AutoGen 网页端 Session Token 报告", text)
            self.assertIn("`llm_total_tokens` | 30", text)
            self.assertIn("`native_collaboration_tokens` | 1200", text)

    @staticmethod
    def _write_session(data_dir: Path, session_id: str) -> None:
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
        events = [
            {
                "ts": "2026-07-04T00:00:00+00:00",
                "event_type": "autogen_model_client_usage",
                "payload": {
                    "agent_id": "model_mimov2_5",
                    "model": "mimov2.5",
                    "usage": {
                        "prompt_tokens": 21,
                        "completion_tokens": 9,
                        "total_tokens": 30,
                    },
                    "llm_prompt_tokens": 21,
                    "llm_completion_tokens": 9,
                    "llm_total_tokens": 30,
                },
            },
            {
                "ts": "2026-07-04T00:00:00+00:00",
                "event_type": "autogen_team_input_real_rewrite",
                "payload": {
                    "agent_id": "RoundRobinGroupChat",
                    "native_full_broadcast_tokens": 1200,
                    "wire_plus_prompt_view_tokens": 300,
                    "receiver_plans": [
                        {"shadow_wire_tokens": 60, "prompt_view_tokens": 80},
                        {"shadow_wire_tokens": 70, "prompt_view_tokens": 90},
                    ],
                },
            }
        ]
        (trace_dir / "trace.jsonl").write_text(
            "\n".join(json.dumps(item) for item in events),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
