from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from web_monitor.parser import build_run_snapshot, list_runs


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
            ignored = runs_dir / "ignored"
            ignored.mkdir()

            runs = list_runs(runs_dir)
            self.assertEqual([item["run_id"] for item in runs], ["run-a"])


if __name__ == "__main__":
    unittest.main()
