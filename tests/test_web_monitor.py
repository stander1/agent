from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from web_monitor.parser import (
    build_run_snapshot,
    build_session_snapshot,
    list_runs,
    list_sessions,
)


class WebMonitorParserTest(unittest.TestCase):
    def test_trace_parser_builds_agent_flow_and_fallback_memory_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            events = [
                {
                    "ts": "2026-06-20T00:00:00+00:00",
                    "event_type": "task_started",
                    "payload": {
                        "task_id": "MON1",
                        "round_id": 1,
                        "mode": "runtime_lite",
                        "title": "Monitor task",
                    },
                },
                {
                    "ts": "2026-06-20T00:00:01+00:00",
                    "event_type": "agent_invoked",
                    "payload": {
                        "task_id": "MON1",
                        "round_id": 1,
                        "mode": "runtime_lite",
                        "agent_id": "planner",
                        "prompt_chars": 120,
                    },
                },
                {
                    "ts": "2026-06-20T00:00:02+00:00",
                    "event_type": "state_written",
                    "payload": {
                        "task_id": "MON1",
                        "round_id": 1,
                        "mode": "runtime_lite",
                        "state": {
                            "state_id": "state_a",
                            "state_type": "artifact_state",
                            "source_agent": "planner",
                            "tier": "cold",
                            "lifecycle": "active",
                            "summary": "planner output state",
                        },
                    },
                },
                {
                    "ts": "2026-06-20T00:00:03+00:00",
                    "event_type": "message_sent",
                    "payload": {
                        "task_id": "MON1",
                        "round_id": 1,
                        "mode": "runtime_lite",
                        "sender": "user",
                        "receiver": "planner",
                        "content": json.dumps(
                            {
                                "from": "planner",
                                "to": "retriever",
                                "summary": "split task and hand off",
                            }
                        ),
                        "state_refs": ["state_a"],
                        "memory_refs": ["mem_a"],
                        "cost_report": {},
                    },
                },
                {
                    "ts": "2026-06-20T00:00:04+00:00",
                    "event_type": "agent_output_received",
                    "payload": {
                        "task_id": "MON1",
                        "round_id": 1,
                        "mode": "runtime_lite",
                        "agent_id": "planner",
                        "content_chars": 180,
                        "state_refs": [{"state_id": "state_a"}],
                        "memory_refs": [{"memory_id": "mem_a"}],
                    },
                },
            ]
            (run_dir / "trace.jsonl").write_text(
                "\n".join(json.dumps(item) for item in events), encoding="utf-8"
            )

            snapshot = build_run_snapshot(run_dir, run_id="sample")
            runtime = snapshot["modes"]["runtime_lite"]
            self.assertEqual(runtime["agents"][0]["agent_id"], "planner")
            self.assertEqual(runtime["agents"][0]["handoff_to"], "retriever")
            self.assertEqual(runtime["state_pool"][0]["state_id"], "state_a")
            self.assertTrue(runtime["memory_graph"]["fallback"])
            self.assertTrue(
                any(node["id"] == "mem_a" for node in runtime["memory_graph"]["nodes"])
            )

    def test_pool_snapshot_builds_memory_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "trace.jsonl").write_text("", encoding="utf-8")
            (run_dir / "pool_snapshot_latest.json").write_text(
                json.dumps(
                    {
                        "state_pool": {
                            "states": [
                                {
                                    "state_id": "state_a",
                                    "state_type": "artifact_state",
                                    "source_agent": "writer",
                                    "tier": "cold",
                                    "lifecycle": "active",
                                    "summary": "artifact",
                                }
                            ]
                        },
                        "memory_store": {
                            "memory_views": [
                                {
                                    "memory_view_id": "view_a",
                                    "slot_id": "slot.project.requirement",
                                    "active_claim_ids": ["claim_a"],
                                    "prompt_summary": "view summary",
                                    "audit_claim_ids": ["claim_a"],
                                    "created_at": "2026-06-20T00:00:00+00:00",
                                    "status": "active",
                                }
                            ],
                            "claim_cards": [
                                {
                                    "claim_id": "claim_a",
                                    "summary": "claim summary",
                                    "status": "active",
                                    "promotion_view_id": "pv_a",
                                }
                            ],
                            "memories": [
                                {
                                    "memory_id": "mem_a",
                                    "summary": "memory summary",
                                    "status": "active",
                                    "memory_view_id": "view_a",
                                    "claim_id": "claim_a",
                                    "promotion_view_id": "pv_a",
                                }
                            ],
                            "promotion_views": [
                                {
                                    "promotion_view_id": "pv_a",
                                    "core_claim": "promotion summary",
                                    "source_state_ids": ["state_a"],
                                    "evidence_refs": ["state_a"],
                                }
                            ],
                            "memory_candidates": [],
                            "claim_candidates": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            snapshot = build_run_snapshot(run_dir, run_id="sample")
            graph = snapshot["modes"]["runtime_lite"]["memory_graph"]
            self.assertFalse(graph["fallback"])
            self.assertTrue(any(node["id"] == "view_a" for node in graph["nodes"]))
            self.assertTrue(
                any(
                    edge["source"] == "pv_a"
                    and edge["target"] == "state_a"
                    and edge["label"] == "source"
                    for edge in graph["edges"]
                )
            )

    def test_list_runs_returns_trace_or_summary_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            run_a = runs_dir / "run-a"
            run_a.mkdir()
            (run_a / "trace.jsonl").write_text("", encoding="utf-8")
            (run_a / "summary.json").write_text(
                json.dumps(
                    {
                        "by_mode": {
                            "baseline_text": {
                                "end_to_end_collaboration_tokens": 1000,
                                "direct_text_tokens": 900,
                            },
                            "runtime_lite": {
                                "end_to_end_collaboration_tokens": 400,
                                "direct_text_tokens": 120,
                                "prompt_view_tokens": 180,
                                "retrieved_memory_tokens": 50,
                                "control_llm_tokens": 20,
                                "retry_tokens": 30,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            ignored = runs_dir / "ignored"
            ignored.mkdir()

            runs = list_runs(runs_dir)
            self.assertEqual([item["run_id"] for item in runs], ["run-a"])
            token_summary = runs[0]["token_summary"]
            self.assertEqual(token_summary["end_to_end_collaboration_tokens"], 400)
            self.assertEqual(token_summary["native_baseline_tokens"], 1000)
            self.assertEqual(token_summary["prompt_view_tokens"], 180)
            self.assertEqual(token_summary["token_savings"], 600)

    def test_agentlite_session_snapshot_exposes_autogen_trace_and_state_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            session_dir = data_dir / "sessions" / "launch_sample"
            trace_dir = session_dir / "autogen_driver"
            trace_dir.mkdir(parents=True)
            (session_dir / "bootstrap_status.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "framework": "autogen",
                        "session_id": "launch_sample",
                        "driver": "autogen",
                        "driver_status": "active",
                        "hooks_active": True,
                    }
                ),
                encoding="utf-8",
            )
            events = [
                {
                    "ts": "2026-07-02T00:00:00+00:00",
                    "event_type": "autogen_agent_receive",
                    "payload": {
                        "agent_id": "RoundRobinGroupChat",
                        "role": "team",
                        "method": "run_stream",
                        "input_chars": 1000,
                    },
                },
                {
                    "ts": "2026-07-02T00:00:01+00:00",
                    "event_type": "state_written",
                    "payload": {
                        "state": {
                            "state_id": "state_session",
                            "state_type": "artifact_state",
                            "source_agent": "RoundRobinGroupChat",
                            "tier": "cold",
                            "lifecycle": "active",
                            "summary": "session state",
                        }
                    },
                },
                {
                    "ts": "2026-07-02T00:00:02+00:00",
                    "event_type": "autogen_model_client_usage",
                    "payload": {
                        "agent_id": "model_mimov2_5",
                        "model": "mimov2.5",
                        "usage": {
                            "prompt_tokens": 12,
                            "completion_tokens": 8,
                            "total_tokens": 20,
                        },
                        "llm_prompt_tokens": 12,
                        "llm_completion_tokens": 8,
                        "llm_total_tokens": 20,
                    },
                },
                {
                    "ts": "2026-07-02T00:00:03+00:00",
                    "event_type": "autogen_memory_retrieval",
                    "payload": {
                        "agent_id": "RoundRobinGroupChat",
                        "memory_query_count": 1,
                        "memory_hit_count": 1,
                        "useful_memory_hit_count": 1,
                        "wrong_memory_hit_count": 0,
                        "retrieved_memory_tokens": 50,
                        "memory_refs": [{"memory_id": "mem_session"}],
                    },
                },
                {
                    "ts": "2026-07-02T00:00:04+00:00",
                    "event_type": "autogen_team_input_real_rewrite",
                    "payload": {
                        "agent_id": "RoundRobinGroupChat",
                        "rewrite_applied_count": 1,
                        "rewrite_fallback_count": 0,
                        "native_full_broadcast_tokens": 1000,
                        "wire_plus_prompt_view_tokens": 260,
                        "token_delta_native_broadcast_minus_rewrite": 321,
                        "receiver_plans": [
                            {
                                "shadow_wire_tokens": 120,
                                "prompt_view_tokens": 140,
                                "retrieved_memory_tokens": 50,
                            }
                        ],
                        "state_refs": [{"state_id": "state_session"}],
                    },
                },
            ]
            (trace_dir / "trace.jsonl").write_text(
                "\n".join(json.dumps(item) for item in events),
                encoding="utf-8",
            )

            sessions = list_sessions(data_dir)
            self.assertEqual(sessions[0]["session_id"], "launch_sample")
            self.assertEqual(
                sessions[0]["token_summary"]["end_to_end_collaboration_tokens"],
                310,
            )
            self.assertEqual(sessions[0]["token_summary"]["native_baseline_tokens"], 1000)
            self.assertEqual(
                sessions[0]["token_summary"]["retrieved_memory_tokens"],
                50,
            )
            self.assertEqual(sessions[0]["token_summary"]["memory_hit_count"], 1)
            snapshot = build_session_snapshot(session_dir)
            runtime = snapshot["modes"]["runtime_lite"]
            self.assertEqual(snapshot["session_id"], "launch_sample")
            self.assertEqual(snapshot["status"], "succeeded")
            self.assertTrue(snapshot["summary"]["hooks_active"])
            self.assertEqual(runtime["state_pool"][0]["state_id"], "state_session")
            self.assertTrue(
                any(agent["agent_id"] == "RoundRobinGroupChat" for agent in runtime["agents"])
            )
            self.assertTrue(
                any(
                    "Team task rewritten" in message["summary"]
                    for message in runtime["messages"]
                )
            )
            self.assertEqual(snapshot["token_summary"]["prompt_view_tokens"], 140)
            self.assertEqual(snapshot["token_summary"]["llm_call_count"], 1)
            self.assertEqual(snapshot["token_summary"]["llm_total_tokens"], 20)
            self.assertEqual(
                snapshot["summary"]["by_mode"]["runtime_lite"][
                    "end_to_end_collaboration_tokens"
                ],
                310,
            )
            self.assertTrue(
                any(
                    "LLM usage" in message["summary"]
                    for message in runtime["messages"]
                )
            )


if __name__ == "__main__":
    unittest.main()
