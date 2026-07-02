from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from run_autogen_long_shadow_smoke import build_prompt_view_samples  # noqa: E402
from run_autogen_handoff_tool_shadow_smoke import (  # noqa: E402
    augment_message_type_coverage,
)
from run_autogen_broadcast_shadow_smoke import (  # noqa: E402
    augment_broadcast_shadow_report,
    summarize_agent_real_rewrite_events,
    summarize_broadcast_shadow_events,
)
from run_autogen_rewrite_echo_smoke import build_comparison_report  # noqa: E402
from run_autogen_handoff_tool_rewrite_guard_smoke import (  # noqa: E402
    summarize_rewrite_safety,
    summarize_typed_rewrite_candidates,
)


class AutoGenLongShadowReportTest(unittest.TestCase):
    def test_builds_artifact_prompt_view_from_state_written_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload_path = root / "state_payload.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "code_artifact_id": "artifact_1",
                        "sha256": "abcdef1234567890",
                    }
                ),
                encoding="utf-8",
            )
            trace_events = [
                {
                    "event_type": "state_written",
                    "payload": {
                        "state": {
                            "state_id": "state_1",
                            "state_type": "artifact_state",
                            "summary": "writer produced a long artifact",
                            "tier": "cold",
                            "payload_ref": str(payload_path),
                        }
                    },
                }
            ]

            views = build_prompt_view_samples(trace_events)

            self.assertEqual(len(views), 1)
            self.assertIn("[artifact_state:state_1]", views[0])
            self.assertIn("artifact=artifact_1", views[0])
            self.assertIn("sha256=abcdef123456", views[0])
            self.assertIn("raw_content=cold_audit_only", views[0])

    def test_handoff_tool_runner_checks_message_type_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace_path = root / "trace.jsonl"
            events = [
                {
                    "event_type": "autogen_agent_receive",
                    "payload": {
                        "decoded_messages": [
                            {
                                "message_kind": "handoff",
                                "target": "planner",
                            },
                            {"message_kind": "tool_call"},
                            {"message_kind": "tool_result"},
                            {"message_kind": "tool_summary"},
                        ]
                    },
                },
                {
                    "event_type": "autogen_shp_handoff_shadow",
                    "payload": {
                        "plan": {
                            "declared_receiver": "planner",
                            "receiver_source": "handoff_message_target",
                            "message_kinds": ["handoff", "tool_summary"],
                        }
                    },
                },
                {"event_type": "autogen_transport_input_state", "payload": {}},
                {"event_type": "autogen_transport_input_state", "payload": {}},
            ]
            trace_path.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            report = {
                "trace_path": str(trace_path),
                "checks": {"existing_check": True},
            }

            augment_message_type_coverage(report)

            checks = report["checks"]
            self.assertTrue(checks["handoff_message_decoded"])
            self.assertTrue(checks["tool_call_request_decoded"])
            self.assertTrue(checks["tool_call_result_decoded"])
            self.assertTrue(checks["tool_call_summary_decoded"])
            self.assertTrue(checks["shadow_plan_uses_handoff_target"])
            self.assertTrue(checks["tool_summary_shadow_state_recorded"])
            self.assertTrue(checks["tool_transport_input_state_recorded"])
            self.assertTrue(report["passed"])

    def test_broadcast_runner_checks_per_receiver_shadow_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace_path = root / "trace.jsonl"
            events = [
                {"event_type": "autogen_team_input_state", "payload": {}},
                {
                    "event_type": "autogen_broadcast_replacement_shadow",
                    "payload": {
                        "native_scope": "team_input",
                        "receiver_count": 3,
                        "native_full_broadcast_tokens": 900,
                        "shadow_wire_tokens": 120,
                        "prompt_view_tokens": 180,
                        "wire_plus_prompt_view_tokens": 300,
                        "rewrite_dry_run": {
                            "mode": "dry-run-rewrite",
                            "candidate_generated": True,
                            "rewrite_safe": True,
                            "real_message_mutation": False,
                            "applied_action": "dry_run_only_keep_native_autogen_broadcast",
                            "fallback_required": False,
                            "fallback_reasons": [],
                        },
                        "receiver_plans": [
                            {
                                "receiver": "planner",
                                "shadow_wire_tokens": 40,
                                "prompt_view_tokens": 60,
                                "message_kinds": ["text"],
                                "schema_valid": True,
                                "prompt_view_available": True,
                            },
                            {
                                "receiver": "writer",
                                "shadow_wire_tokens": 40,
                                "prompt_view_tokens": 60,
                                "message_kinds": ["text"],
                                "schema_valid": True,
                                "prompt_view_available": True,
                            },
                            {
                                "receiver": "reviewer",
                                "shadow_wire_tokens": 40,
                                "prompt_view_tokens": 60,
                                "message_kinds": ["text"],
                                "schema_valid": True,
                                "prompt_view_available": True,
                            },
                        ],
                    },
                },
            ]
            trace_path.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            report = {
                "trace_path": str(trace_path),
                "checks": {"existing_check": True},
            }

            augment_broadcast_shadow_report(report)

            checks = report["checks"]
            self.assertTrue(checks["team_input_state_recorded"])
            self.assertTrue(checks["broadcast_shadow_event_recorded"])
            self.assertTrue(checks["broadcast_has_three_receivers"])
            self.assertTrue(checks["broadcast_receivers_preserved"])
            self.assertTrue(checks["broadcast_prompt_view_recorded"])
            self.assertTrue(checks["broadcast_wire_tokens_recorded"])
            self.assertTrue(checks["broadcast_wire_plus_prompt_beats_native"])
            self.assertTrue(checks["broadcast_dry_run_diff_recorded"])
            self.assertTrue(checks["broadcast_dry_run_safe"])
            self.assertTrue(checks["broadcast_real_mutation_disabled"])
            self.assertTrue(checks["broadcast_native_kept_in_dry_run"])
            self.assertEqual(
                report["broadcast_replacement_shadow"]["receivers"],
                ["planner", "reviewer", "writer"],
            )
            self.assertEqual(
                report["broadcast_replacement_shadow"]["broadcast_modes"],
                ["dry-run-rewrite"],
            )
            self.assertTrue(report["passed"])

    def test_broadcast_summary_preserves_real_rewrite_fallback_audit(self) -> None:
        events = [
            {
                "event_type": "autogen_broadcast_replacement_shadow",
                "payload": {
                    "native_scope": "team_output",
                    "receiver_count": 1,
                    "native_full_broadcast_tokens": 100,
                    "shadow_wire_tokens": 20,
                    "prompt_view_tokens": 20,
                    "wire_plus_prompt_view_tokens": 40,
                    "rewrite_dry_run": {
                        "mode": "real-rewrite",
                        "candidate_generated": True,
                        "rewrite_safe": False,
                        "real_message_mutation": False,
                        "applied_action": "fallback_keep_native_autogen_broadcast",
                        "fallback_required": True,
                        "fallback_reasons": [
                            "team_level_real_rewrite_not_enabled_for_guarded_agent_input"
                        ],
                    },
                    "receiver_plans": [
                        {
                            "receiver": "writer",
                            "shadow_wire_tokens": 20,
                            "prompt_view_tokens": 20,
                            "message_kinds": ["text"],
                            "schema_valid": True,
                            "prompt_view_available": True,
                        }
                    ],
                },
            }
        ]

        summary = summarize_broadcast_shadow_events(events)

        self.assertEqual(summary["broadcast_modes"], ["real-rewrite"])
        self.assertEqual(summary["fallback_required_count"], 1)
        self.assertEqual(summary["real_message_mutation_count"], 0)
        self.assertIn(
            "team_level_real_rewrite_not_enabled_for_guarded_agent_input",
            summary["fallback_reasons"],
        )
        self.assertEqual(
            summary["applied_actions"],
            ["fallback_keep_native_autogen_broadcast"],
        )

    def test_summarizes_agent_input_real_rewrite_events(self) -> None:
        events = [
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "agent_id": "writer",
                    "candidate_generated": True,
                    "rewrite_applied": True,
                    "fallback_required": False,
                    "real_message_mutation": True,
                    "rewrite_attempt_count": 1,
                    "rewrite_applied_count": 1,
                    "rewrite_fallback_count": 0,
                    "native_input_tokens": 1200,
                    "rewritten_input_tokens": 180,
                    "token_delta_native_minus_rewrite": 1020,
                    "fallback_reasons": [],
                },
            }
        ]

        summary = summarize_agent_real_rewrite_events(events)

        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["agents"], ["writer"])
        self.assertEqual(summary["candidate_count"], 1)
        self.assertEqual(summary["attempt_count"], 1)
        self.assertEqual(summary["applied_count"], 1)
        self.assertEqual(summary["fallback_count"], 0)
        self.assertEqual(summary["fallback_required_count"], 0)
        self.assertEqual(summary["fallback_buckets"], [])
        self.assertEqual(summary["fallback_bucket_counts"], {})
        self.assertEqual(summary["real_message_mutation_count"], 1)
        self.assertEqual(summary["token_delta_native_minus_rewrite"], 1020)

    def test_summarizes_agent_input_real_rewrite_fallback_buckets(self) -> None:
        events = [
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "agent_id": "writer",
                    "candidate_generated": False,
                    "rewrite_applied": False,
                    "fallback_required": True,
                    "real_message_mutation": False,
                    "rewrite_attempt_count": 1,
                    "rewrite_applied_count": 0,
                    "rewrite_fallback_count": 1,
                    "native_input_tokens": 0,
                    "rewritten_input_tokens": 0,
                    "token_delta_native_minus_rewrite": 0,
                    "fallback_reasons": ["non_text_message_present"],
                    "fallback_buckets": ["unsupported_message_type"],
                    "fallback_bucket_counts": {"unsupported_message_type": 1},
                },
            }
        ]

        summary = summarize_agent_real_rewrite_events(events)

        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["attempt_count"], 1)
        self.assertEqual(summary["applied_count"], 0)
        self.assertEqual(summary["fallback_count"], 1)
        self.assertEqual(summary["fallback_required_count"], 1)
        self.assertEqual(summary["fallback_reasons"], ["non_text_message_present"])
        self.assertEqual(summary["fallback_buckets"], ["unsupported_message_type"])
        self.assertEqual(
            summary["fallback_bucket_counts"],
            {"unsupported_message_type": 1},
        )
        self.assertEqual(summary["real_message_mutation_count"], 0)

    def test_summarizes_non_text_rewrite_safety(self) -> None:
        events = [
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "rewrite_safety": {
                        "contract": "autogen_non_text_real_rewrite_guard.v1",
                        "safe_to_mutate": False,
                        "native_preservation_required": True,
                        "message_kinds": ["handoff"],
                        "fallback_reasons": [
                            "non_text_message_present",
                            "handoff_rewrite_requires_target_preservation",
                        ],
                        "fallback_buckets": [
                            "unsupported_message_type",
                            "handoff_control_guard",
                        ],
                        "required_native_fields": ["target", "source", "content"],
                        "handoff_targets": ["writer"],
                    }
                },
            },
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "rewrite_safety": {
                        "contract": "autogen_non_text_real_rewrite_guard.v1",
                        "safe_to_mutate": False,
                        "native_preservation_required": True,
                        "message_kinds": ["tool_summary"],
                        "fallback_reasons": [
                            "non_text_message_present",
                            "tool_rewrite_requires_call_lineage",
                            "tool_rewrite_requires_result_lineage",
                        ],
                        "fallback_buckets": [
                            "unsupported_message_type",
                            "tool_lineage_guard",
                        ],
                        "required_native_fields": [
                            "tool_calls.id",
                            "results.call_id",
                        ],
                        "tool_call_ids": ["call_1"],
                        "tool_result_call_ids": ["call_1"],
                        "tool_result_lineage_complete": True,
                    }
                },
            },
        ]

        summary = summarize_rewrite_safety(events)

        self.assertEqual(
            summary["contracts"],
            ["autogen_non_text_real_rewrite_guard.v1"],
        )
        self.assertEqual(summary["native_preservation_required_count"], 2)
        self.assertEqual(summary["safe_to_mutate_false_count"], 2)
        self.assertEqual(summary["handoff_targets"], ["writer"])
        self.assertEqual(summary["tool_call_ids"], ["call_1"])
        self.assertEqual(summary["tool_result_call_ids"], ["call_1"])
        self.assertIn("handoff_control_guard", summary["fallback_buckets"])
        self.assertIn("tool_lineage_guard", summary["fallback_buckets"])

    def test_summarizes_handoff_typed_rewrite_candidate(self) -> None:
        events = [
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "typed_rewrite_candidate": {
                        "contract": "autogen_handoff_typed_rewrite_candidate.v1",
                        "candidate_generated": True,
                        "candidate_safe": True,
                        "mutation_applied": False,
                        "mutation_gate": "dry_run_only",
                        "message_kind": "handoff",
                        "source": "router",
                        "target": "writer",
                        "native_id": "handoff_1",
                        "metadata_keys": ["route"],
                        "context_count": 0,
                        "native_input_tokens": 1000,
                        "candidate_input_tokens": 180,
                        "token_delta_native_minus_candidate": 820,
                        "semantic_checks": {
                            "message_type_preserved": True,
                            "source_preserved": True,
                            "target_preserved": True,
                            "id_preserved": True,
                            "metadata_preserved": True,
                            "context_preserved": True,
                            "token_reduced": True,
                        },
                    }
                },
            }
        ]

        summary = summarize_typed_rewrite_candidates(events)

        self.assertEqual(
            summary["contracts"],
            ["autogen_handoff_typed_rewrite_candidate.v1"],
        )
        self.assertEqual(summary["message_kinds"], ["handoff"])
        self.assertEqual(summary["sources"], ["router"])
        self.assertEqual(summary["targets"], ["writer"])
        self.assertEqual(summary["candidate_safe_count"], 1)
        self.assertEqual(summary["mutation_applied_count"], 0)
        self.assertEqual(summary["token_delta_native_minus_candidate"], 820)
        self.assertIn("target_preserved", summary["passing_semantic_checks"])

    def test_summarizes_tool_summary_typed_rewrite_candidate(self) -> None:
        events = [
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "typed_rewrite_candidate": {
                        "contract": "autogen_tool_summary_typed_rewrite_candidate.v1",
                        "candidate_generated": True,
                        "candidate_safe": True,
                        "mutation_applied": False,
                        "mutation_gate": "dry_run_only",
                        "message_kind": "tool_summary",
                        "source": "tool_runner",
                        "target": "writer",
                        "native_id": "tool_summary_1",
                        "tool_call_ids": ["call_1"],
                        "tool_call_names": ["lookup"],
                        "tool_result_call_ids": ["call_1"],
                        "tool_result_names": ["lookup"],
                        "tool_result_error_flags": ["call_1:False"],
                        "native_input_tokens": 1000,
                        "candidate_input_tokens": 260,
                        "token_delta_native_minus_candidate": 740,
                        "semantic_checks": {
                            "message_type_preserved": True,
                            "tool_calls_preserved": True,
                            "tool_results_preserved": True,
                            "tool_result_lineage_complete": True,
                            "token_reduced": True,
                        },
                    }
                },
            }
        ]

        summary = summarize_typed_rewrite_candidates(events)

        self.assertEqual(
            summary["contracts"],
            ["autogen_tool_summary_typed_rewrite_candidate.v1"],
        )
        self.assertEqual(summary["message_kinds"], ["tool_summary"])
        self.assertEqual(summary["tool_call_ids"], ["call_1"])
        self.assertEqual(summary["tool_call_names"], ["lookup"])
        self.assertEqual(summary["tool_result_call_ids"], ["call_1"])
        self.assertEqual(summary["tool_result_names"], ["lookup"])
        self.assertEqual(summary["candidate_safe_count"], 1)
        self.assertEqual(summary["mutation_applied_count"], 0)
        self.assertIn("tool_result_lineage_complete", summary["passing_semantic_checks"])
        self.assertEqual(
            summary["rows"][0]["tool_result_error_flags"],
            ["call_1:False"],
        )

    def test_rewrite_echo_comparison_checks_modes(self) -> None:
        mode_reports = [
            {
                "mode": "shadow-only",
                "returncode": 0,
                "bootstrap_ok": True,
                "driver_phase": "v5.13h",
                "seen_content_chars": 1000,
                "seen_contains_native_marker": True,
                "seen_native_marker_count": 10,
                "seen_contains_rewrite_marker": False,
                "agent_input_real_rewrite": {},
            },
            {
                "mode": "dry-run-rewrite",
                "returncode": 0,
                "bootstrap_ok": True,
                "driver_phase": "v5.13h",
                "seen_content_chars": 1000,
                "seen_contains_native_marker": True,
                "seen_native_marker_count": 10,
                "seen_contains_rewrite_marker": False,
                "agent_input_real_rewrite": {},
            },
            {
                "mode": "real-rewrite",
                "returncode": 0,
                "bootstrap_ok": True,
                "driver_phase": "v5.13h",
                "seen_content_chars": 220,
                "seen_contains_native_marker": True,
                "seen_native_marker_count": 1,
                "seen_contains_rewrite_marker": True,
                "seen_contains_state_ref": True,
                "seen_contains_prompt_view": True,
                "agent_input_real_rewrite": {
                    "applied_count": 1,
                    "native_input_tokens": 1000,
                    "rewritten_input_tokens": 160,
                    "token_delta_native_minus_rewrite": 840,
                },
            },
        ]

        report = build_comparison_report(
            output_dir=Path("dummy"),
            mode_reports=mode_reports,
        )

        self.assertTrue(report["passed"])
        self.assertTrue(report["checks"]["real_rewrite_agent_sees_rewritten_content"])
        self.assertTrue(report["checks"]["real_rewrite_reduces_native_marker_count"])
        self.assertTrue(report["checks"]["real_rewrite_reduces_seen_chars"])
        self.assertEqual(report["comparison"]["real_rewrite_rewritten_tokens"], 160)


if __name__ == "__main__":
    unittest.main()
