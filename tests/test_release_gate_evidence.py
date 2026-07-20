from __future__ import annotations

import json
import runpy
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_GLOBALS = runpy.run_path(
    str(PROJECT_ROOT / "examples" / "release_gate_evidence.py")
)


class ReleaseGateEvidenceTests(unittest.TestCase):
    def test_collects_internal_rewrite_and_caller_display_restore(self) -> None:
        collect = EVIDENCE_GLOBALS["collect_team_takeover_evidence"]
        assess = EVIDENCE_GLOBALS["assess_team_takeover"]
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            session = data_dir / "sessions" / "launch_test"
            trace = session / "autogen_driver" / "trace.jsonl"
            trace.parent.mkdir(parents=True)
            trace.write_text(
                "\n".join(
                    json.dumps(event)
                    for event in (
                        {
                            "event_type": "autogen_team_input_real_rewrite",
                            "payload": {
                                "rewrite_applied_count": 1,
                                "rewrite_fallback_count": 0,
                                "real_message_mutation": True,
                                "native_task_tokens": 100,
                                "rewritten_task_tokens": 40,
                                "token_delta_native_task_minus_rewrite": 60,
                                "native_full_broadcast_tokens": 300,
                                "wire_plus_prompt_view_tokens": 90,
                                "token_delta_native_broadcast_minus_rewrite": 210,
                            },
                        },
                        {
                            "event_type": "autogen_team_display_restored",
                            "payload": {"restored_message_count": 1},
                        },
                    )
                ),
                encoding="utf-8",
            )
            (session / "bootstrap_status.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "hooks_active": True,
                        "driver_details": {
                            "phase": "v-current",
                            "trace_path": str(trace),
                        },
                    }
                ),
                encoding="utf-8",
            )
            evidence = collect(data_dir)
            checks = assess(
                evidence=evidence,
                app_payload={"agentlite_active": True},
                first_stream_item={
                    "native_marker_count": 2,
                    "contains_team_rewrite_marker": False,
                    "contains_state_pool_marker": False,
                    "contains_broadcast_manifest": False,
                    "contains_receiver_prompt_views": False,
                },
                expected_phase="v-current",
            )

        self.assertTrue(all(checks.values()))
        self.assertEqual(evidence["team_applied_count"], 1)
        self.assertEqual(evidence["task_token_savings"], 60)
        self.assertEqual(evidence["broadcast_token_savings"], 210)

    def test_rejects_visible_internal_packet_without_display_restore(self) -> None:
        assess = EVIDENCE_GLOBALS["assess_team_takeover"]
        checks = assess(
            evidence={
                "bootstrap_ok": True,
                "hooks_active": True,
                "driver_phase": "v-current",
                "team_event_count": 1,
                "team_applied_count": 1,
                "team_fallback_count": 0,
                "real_message_mutation_count": 1,
                "task_token_savings": 1,
                "broadcast_token_savings": 1,
                "display_restore_event_count": 0,
            },
            app_payload={"agentlite_active": True},
            first_stream_item={
                "native_marker_count": 0,
                "contains_team_rewrite_marker": True,
            },
            expected_phase="v-current",
        )
        self.assertFalse(checks["caller_display_restored"])


if __name__ == "__main__":
    unittest.main()
