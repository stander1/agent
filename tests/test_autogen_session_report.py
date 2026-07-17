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
from web_monitor.parser import _autogen_token_summary


class AutoGenSessionReportTest(unittest.TestCase):
    def test_modern_trace_counts_injection_only_after_applied_rewrite(self) -> None:
        events = [
            {
                "event_type": "autogen_memory_retrieval",
                "payload": {
                    "memory_query_count": 1,
                    "memory_hit_count": 1,
                    "memory_injected_count": 0,
                    "useful_memory_hit_count": 0,
                    "wrong_memory_hit_count": 0,
                    "unassessed_memory_hit_count": 0,
                    "retrieved_memory_tokens": 12,
                },
            },
            {
                "event_type": "autogen_team_input_real_rewrite",
                "payload": {
                    "rewrite_applied": False,
                    "native_full_broadcast_tokens": 100,
                    "wire_plus_prompt_view_tokens": 20,
                    "memory_injected_count": 0,
                },
            },
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "rewrite_applied": True,
                    "native_input_tokens": 100,
                    "rewritten_input_tokens": 40,
                    "memory_injected_count": 1,
                },
            },
        ]

        summary = _autogen_token_summary(events)

        self.assertEqual(summary["memory_hit_count"], 1)
        self.assertEqual(summary["memory_injected_count"], 1)
        self.assertEqual(summary["useful_memory_hit_count"], 0)
        self.assertEqual(summary["unassessed_memory_hit_count"], 1)
        self.assertEqual(summary["native_baseline_tokens"], 200)
        self.assertEqual(summary["runtime_tokens"], 140)

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
            self.assertEqual(token_summary["shadow_native_tokens"], 900)
            self.assertEqual(token_summary["shadow_candidate_tokens"], 300)
            self.assertEqual(token_summary["memory_injected_count"], 1)
            self.assertEqual(token_summary["useful_memory_hit_count"], 0)
            self.assertEqual(token_summary["unassessed_memory_hit_count"], 1)
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
            self.assertIn("web_case,actual_agentlite_transport_tokens,300", csv_text)

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
            self.assertIn("`actual_native_transport_tokens` | 1200", text)

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
                    "rewrite_applied": True,
                    "rewrite_applied_count": 1,
                    "native_full_broadcast_tokens": 1200,
                    "wire_plus_prompt_view_tokens": 300,
                    "receiver_plans": [
                        {"shadow_wire_tokens": 60, "prompt_view_tokens": 80},
                        {"shadow_wire_tokens": 70, "prompt_view_tokens": 90},
                    ],
                },
            },
            {
                "ts": "2026-07-04T00:00:01+00:00",
                "event_type": "autogen_broadcast_replacement_shadow",
                "payload": {
                    "native_full_broadcast_tokens": 900,
                    "wire_plus_prompt_view_tokens": 300,
                    "receiver_plans": [
                        {"shadow_wire_tokens": 50, "prompt_view_tokens": 100},
                        {"shadow_wire_tokens": 50, "prompt_view_tokens": 100},
                    ],
                },
            },
            {
                "ts": "2026-07-04T00:00:02+00:00",
                "event_type": "autogen_memory_retrieval",
                "payload": {
                    "memory_query_count": 1,
                    "memory_hit_count": 1,
                    "useful_memory_hit_count": 1,
                    "wrong_memory_hit_count": 0,
                    "retrieved_memory_tokens": 20,
                },
            },
        ]
        (trace_dir / "trace.jsonl").write_text(
            "\n".join(json.dumps(item) for item in events),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
