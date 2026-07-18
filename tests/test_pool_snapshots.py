from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.state.state_pool import StatePoolLite


class PoolSnapshotTest(unittest.TestCase):
    def test_state_pool_snapshot_exports_public_state_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(Path(tmp))
            state_ref, state = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "artifact_T1_writer", "sha256": "abc"},
                summary="writer artifact summary",
                usage_hint="artifact_summary",
                tier="cold",
                access_policy="prompt_view_with_audit_cold_access",
                audit_payload={"content": "raw artifact"},
            )
            pool.mark_lineage([state_ref.state_id])

            snapshot = pool.snapshot()
            self.assertEqual(len(snapshot["states"]), 1)
            state_item = snapshot["states"][0]
            self.assertEqual(state_item["state_id"], state.state_id)
            self.assertEqual(state_item["tier"], "cold")
            self.assertEqual(state_item["source_agent"], "writer")
            self.assertEqual(
                state_item["access_policy"], "prompt_view_with_audit_cold_access"
            )
            self.assertTrue(state_item["lineage_protected"])

    def test_memory_store_snapshot_exports_graph_sources(self) -> None:
        store = MemoryStoreLite()
        report = store.write_memory_with_report(
            task_id="A2",
            source_agent="writer",
            task_topic="travel memory",
            summary="budget and food preference can be reused",
            tags=["travel_A", "writer"],
            slot_hint="reuse_strategy",
            source_state_ids=["state_a"],
            evidence_refs=["state_a"],
        )

        snapshot = store.snapshot()
        self.assertEqual(len(snapshot["memories"]), 1)
        self.assertEqual(len(snapshot["claim_cards"]), 1)
        self.assertEqual(len(snapshot["memory_views"]), 1)
        self.assertEqual(len(snapshot["promotion_views"]), 1)
        self.assertEqual(snapshot["memories"][0]["memory_id"], report.memory_ref.memory_id)
        self.assertEqual(
            snapshot["promotion_views"][0]["source_state_ids"], ["state_a"]
        )


if __name__ == "__main__":
    unittest.main()
