from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_runtime.eval.experiment_archive import (
    complete_experiment_archive,
    create_agentlite_session_binding,
    initialize_experiment_archive,
)
from agent_runtime.eval.autogen_session_report import (
    SessionReportRequest,
    build_autogen_session_report,
    render_autogen_session_report,
    write_autogen_session_report,
)
from web_monitor.parser import _autogen_token_summary


class AutoGenSessionReportTest(unittest.TestCase):
    def test_agent_rewrite_cost_components_are_mutually_exclusive(self) -> None:
        events = [
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "rewrite_applied": True,
                    "native_input_tokens": 400,
                    "rewritten_input_tokens": 100,
                    "prompt_view_tokens": 30,
                    "retrieved_memory_tokens": 20,
                    "memory_injected_count": 1,
                },
            }
        ]

        summary = _autogen_token_summary(events)

        self.assertEqual(summary["runtime_tokens"], 100)
        self.assertEqual(summary["direct_message_tokens"], 50)
        self.assertEqual(summary["prompt_view_tokens"], 30)
        self.assertEqual(summary["retrieved_memory_tokens"], 20)
        self.assertEqual(
            summary["direct_message_tokens"]
            + summary["prompt_view_tokens"]
            + summary["retrieved_memory_tokens"],
            summary["runtime_tokens"],
        )

    def test_modern_trace_counts_injection_only_after_applied_rewrite(self) -> None:
        events = [
            {
                "event_type": "capability_profile_updated",
                "payload": {
                    "agent_id": "DeliveryComposer",
                    "profile": {
                        "agent_id": "DeliveryComposer",
                        "registry_scope": "business",
                    },
                },
            },
            {
                "event_type": "capability_profile_updated",
                "payload": {
                    "agent_id": "SingleThreadedAgentRuntime",
                    "profile": {
                        "agent_id": "SingleThreadedAgentRuntime",
                        "registry_scope": "system",
                    },
                },
            },
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
                    "fallback_reasons": ["token_not_reduced"],
                    "fallback_buckets": ["cost_gate_failed"],
                    "memory_candidate_deduplicated_count": 1,
                    "memory_candidate_deduplicated_fanout_count": 3,
                    "memory_candidate_deduplicated_tokens": 60,
                },
            },
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "rewrite_applied": True,
                    "continuity_context_required": True,
                    "continuity_cost_override": True,
                    "native_input_tokens": 100,
                    "rewritten_input_tokens": 40,
                    "memory_injected_count": 1,
                    "memory_candidate_deduplicated_count": 2,
                    "memory_candidate_deduplicated_tokens": 40,
                    "memory_source_view_tokens": 80,
                    "minimal_role_view_tokens": 30,
                    "memory_role_view_candidate_tokens": 90,
                    "memory_no_expansion_fallback_count": 1,
                    "memory_field_fetch_count": 1,
                    "memory_field_fetch_tokens": 12,
                    "current_task_source_tokens": 100,
                    "current_task_role_view_tokens": 40,
                    "current_task_role_view_candidate_tokens": 120,
                    "current_task_no_expansion_fallback_count": 1,
                    "rewrite_safety": {
                        "team_receiver_role_view_hydration": True
                    },
                },
            },
            {
                "event_type": "autogen_memory_candidate",
                "payload": {
                    "candidate_kind": "autogen_team_final",
                    "delivery_assessment": {"valid": True},
                },
            },
            {
                "event_type": "autogen_core_content_real_rewrite",
                "payload": {
                    "rewrite_applied": False,
                    "rewrite_fallback_count": 1,
                    "fallback_reasons": ["unsupported_core_message_content_field"],
                    "fallback_buckets": ["core_message_contract_invalid"],
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
        self.assertEqual(summary["rewrite_audit_event_count"], 3)
        self.assertEqual(summary["rewrite_costed_event_count"], 2)
        self.assertEqual(summary["rewrite_applied_event_count"], 1)
        self.assertEqual(summary["rewrite_fallback_event_count"], 2)
        self.assertEqual(summary["rewrite_cost_gate_fallback_count"], 1)
        self.assertEqual(summary["rewrite_contract_fallback_count"], 1)
        self.assertEqual(summary["actual_rewrite_event_count"], 1)
        self.assertEqual(summary["continuity_required_event_count"], 1)
        self.assertEqual(summary["continuity_cost_override_count"], 1)
        self.assertEqual(summary["continuity_memory_injection_count"], 1)
        self.assertEqual(summary["memory_candidate_deduplicated_count"], 5)
        self.assertEqual(summary["memory_candidate_deduplicated_tokens"], 100)
        self.assertEqual(summary["memory_source_view_tokens"], 80)
        self.assertEqual(summary["minimal_role_view_tokens"], 30)
        self.assertEqual(summary["memory_role_view_candidate_tokens"], 90)
        self.assertEqual(summary["memory_no_expansion_fallback_count"], 1)
        self.assertEqual(summary["role_view_saved_tokens"], 50)
        self.assertEqual(summary["role_view_reduction_ratio"], 0.625)
        self.assertEqual(summary["memory_field_fetch_count"], 1)
        self.assertEqual(summary["memory_field_fetch_tokens"], 12)
        self.assertEqual(summary["receiver_role_view_hydration_count"], 1)
        self.assertEqual(summary["current_task_source_tokens"], 100)
        self.assertEqual(summary["current_task_role_view_tokens"], 40)
        self.assertEqual(summary["current_task_role_view_candidate_tokens"], 120)
        self.assertEqual(summary["current_task_no_expansion_fallback_count"], 1)
        self.assertEqual(summary["current_task_role_view_saved_tokens"], 60)
        self.assertEqual(summary["current_task_role_view_reduction_ratio"], 0.6)
        self.assertEqual(summary["final_delivery_assessed_count"], 1)
        self.assertEqual(summary["final_delivery_valid_count"], 1)
        self.assertEqual(summary["final_delivery_invalid_count"], 0)
        self.assertEqual(summary["final_delivery_valid_rate"], 1.0)
        self.assertEqual(summary["registered_capability_profile_count"], 1)
        self.assertEqual(summary["registered_system_profile_count"], 1)
        self.assertEqual(summary["registered_total_profile_count"], 2)

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
            self.assertEqual(metrics["rewrite_audit_event_count"], 1)
            self.assertEqual(metrics["rewrite_applied_event_count"], 1)
            self.assertEqual(metrics["rewrite_fallback_event_count"], 0)

    def test_external_provider_usage_fills_custom_client_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            self._write_session(data_dir, "custom_client")
            trace_path = (
                data_dir
                / "sessions"
                / "custom_client"
                / "autogen_driver"
                / "trace.jsonl"
            )
            rows = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            trace_path.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in rows
                    if row.get("event_type") != "autogen_model_client_usage"
                ),
                encoding="utf-8",
            )
            usage_path = Path(tmp) / "llm_usage_summary.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "calls": 7,
                        "llm_prompt_tokens": 120,
                        "llm_completion_tokens": 30,
                        "llm_total_tokens": 150,
                    }
                ),
                encoding="utf-8",
            )

            report = build_autogen_session_report(
                SessionReportRequest(
                    data_dir=data_dir,
                    session_id="custom_client",
                    provider_usage=usage_path,
                )
            )

            self.assertEqual(report["llm_usage_source"], "external_provider_usage_file")
            self.assertEqual(report["token_summary"]["llm_call_count"], 7)
            self.assertEqual(report["token_summary"]["llm_total_tokens"], 150)

    def test_bound_experiment_uses_exact_session_instead_of_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            expected_id = "launch_expected"
            self._write_session(data_dir, expected_id)
            self._write_session(data_dir, "launch_unrelated_newer")
            experiment_dir = root / "experiment"
            create_agentlite_session_binding(
                experiment_dir=experiment_dir,
                session_id=expected_id,
                data_dir=data_dir,
                session_dir=data_dir / "sessions" / expected_id,
                framework="autogen",
                cwd=root,
                command=["python", "app.py"],
            )
            identity = initialize_experiment_archive(
                output_dir=experiment_dir,
                scenario_id="generic",
                experiment_mode="managed",
                environ={
                    "AGENTLITE_SESSION_ID": expected_id,
                    "AGENTLITE_DATA_DIR": str(data_dir),
                },
            )
            usage_path = experiment_dir / "llm_usage_summary.json"
            usage_path.write_text(
                json.dumps(
                    {
                        "calls": 2,
                        "llm_prompt_tokens": 40,
                        "llm_completion_tokens": 10,
                        "llm_total_tokens": 50,
                        "binding": identity.binding(),
                    }
                ),
                encoding="utf-8",
            )
            complete_experiment_archive(
                identity,
                summary={"llm_total_tokens": 50},
                artifact_paths=[usage_path],
            )

            report = build_autogen_session_report(
                SessionReportRequest(experiment_dir=experiment_dir)
            )

            self.assertEqual(report["session_id"], expected_id)
            self.assertEqual(report["experiment_run_id"], identity.run_id)
            self.assertTrue(report["experiment_binding_verified"])
            self.assertTrue(
                all(report["experiment_binding_checks"].values())
            )
            self.assertEqual(report["provider_usage_path"], str(usage_path.resolve()))

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
            self.assertIn("# AutoGen Session Token 报告", text)
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
