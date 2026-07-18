import unittest
import tempfile
import sqlite3
from pathlib import Path

from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.memory.schema_registry import SchemaRegistryLite


class SchemaRegistryLiteTest(unittest.TestCase):
    def test_alias_mapping_resolves_to_canonical_slot(self) -> None:
        registry = SchemaRegistryLite(
            canonical_slots={"slot.runtime.execution_language"},
            alias_mapping={"coding_lang": "slot.runtime.execution_language"},
        )

        resolved = registry.resolve("coding_lang")
        claim = registry.canonicalize_claim(
            subject="runtime",
            slot_id=resolved.slot_id,
            value="Python",
            source_agent="writer",
            confidence=0.8,
        )

        self.assertTrue(resolved.alias_hit)
        self.assertEqual(resolved.slot_id, "slot.runtime.execution_language")
        self.assertEqual(claim.schema_version, "ccf.v1-lite")
        self.assertEqual(claim.temporal_scope, "current_task")


class MemoryGovernanceTest(unittest.TestCase):
    def test_conflict_resolver_keeps_highest_confidence_claim_active(self) -> None:
        store = MemoryStoreLite()
        first = store.write_memory_with_report(
            task_id="A1",
            source_agent="writer",
            task_topic="travel preference",
            summary="预算优先，交通方案先选慢车。",
            tags=["travel_A"],
            slot_hint="reuse_strategy",
            confidence=0.62,
        )
        second = store.write_memory_with_report(
            task_id="A2",
            source_agent="reviewer",
            task_topic="travel preference",
            summary="预算优先，但交通方案改为高铁优先。",
            tags=["travel_A"],
            slot_hint="reuse_strategy",
            confidence=0.91,
        )

        first_memory = store._memories[first.memory_ref.memory_id]
        second_memory = store._memories[second.memory_ref.memory_id]
        first_claim = store._claims[first_memory.claim_id]
        second_claim = store._claims[second_memory.claim_id]
        view = store._views[second_memory.memory_view_id]

        self.assertEqual(first_claim.status, "superseded")
        self.assertEqual(first_memory.status, "superseded")
        self.assertEqual(second_claim.status, "active")
        self.assertEqual(view.active_claim_ids, [second_claim.claim_id])
        self.assertIn(first_claim.claim_id, view.historical_claim_ids)
        self.assertEqual(second.superseded_claim_count, 1)
        self.assertEqual(second.conflict_resolved_count, 1)

    def test_batch_compaction_updates_memory_view_summary_and_log(self) -> None:
        store = MemoryStoreLite()
        store.write_memory_with_report(
            task_id="B1",
            source_agent="writer",
            task_topic="security evidence",
            summary="初始结论：弱口令风险较低。",
            tags=["security_B"],
            slot_hint="failure_reason",
            confidence=0.55,
        )
        latest = store.write_memory_with_report(
            task_id="B2",
            source_agent="reviewer",
            task_topic="security evidence",
            summary="修正结论：弱口令风险高，需要立即整改。",
            tags=["security_B"],
            slot_hint="failure_reason",
            confidence=0.88,
        )

        report = store.run_compaction(slot_id=latest.memory_ref.slot_id)
        view = store._views[latest.memory_ref.memory_view_id]

        self.assertEqual(report.memory_view_count, 1)
        self.assertEqual(report.new_claim_count, 2)
        self.assertEqual(report.merged_claim_count, 1)
        self.assertEqual(report.superseded_claim_count, 1)
        self.assertIn("弱口令风险高", view.prompt_summary)
        self.assertNotIn("风险较低", view.prompt_summary)
        self.assertEqual(len(store._compaction_log), 1)

    def test_memory_reference_manager_tracks_lineage_evidence_and_replacement(self) -> None:
        store = MemoryStoreLite()
        first = store.write_memory_with_report(
            task_id="C1",
            source_agent="writer",
            task_topic="runtime evidence v1",
            summary="第一版运行时证据。",
            tags=["runtime_C"],
            slot_hint="reuse_strategy",
            source_state_ids=["state_a", "state_b"],
            evidence_refs=["evidence_a"],
            confidence=0.7,
        )
        second = store.write_memory_with_report(
            task_id="C2",
            source_agent="reviewer",
            task_topic="runtime evidence v2",
            summary="第二版运行时证据替换第一版。",
            tags=["runtime_C"],
            slot_hint="reuse_strategy",
            source_state_ids=["state_c"],
            evidence_refs=["evidence_c"],
            confidence=0.92,
        )

        refs = store.memory_references(first.memory_ref)
        ref_types = {item.ref_type for item in refs}
        self.assertIn("strong", ref_types)
        self.assertIn("weak", ref_types)
        self.assertIn("lineage", ref_types)
        self.assertIn("evidence", ref_types)
        self.assertGreaterEqual(first.memory_reference_count, 5)

        tombstoned = store.replace_memory(
            first.memory_ref,
            second.memory_ref,
            reason="higher_confidence_replacement",
        )
        self.assertGreater(tombstoned, 0)
        self.assertFalse(store.memory_references(first.memory_ref))
        chain = store.reference_manager.replacement_chain(first.memory_ref.memory_id)
        self.assertEqual(chain, [first.memory_ref.memory_id, second.memory_ref.memory_id])

    def test_memory_store_persists_warm_snapshot_when_storage_dir_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStoreLite(storage_dir=Path(tmp))
            report = store.write_memory_with_report(
                task_id="D1",
                source_agent="writer",
                task_topic="persistent memory",
                summary="持久化快照记录记忆、视图和引用。",
                tags=["persist_D"],
                slot_hint="reuse_strategy",
                source_state_ids=["state_d"],
                evidence_refs=["evidence_d"],
                confidence=0.8,
            )

            snapshot_path = Path(tmp) / "memory_store_snapshot.json"
            self.assertTrue(snapshot_path.exists())
            snapshot = snapshot_path.read_text(encoding="utf-8")
            self.assertIn(report.memory_ref.memory_id, snapshot)
            self.assertIn("memory_references", snapshot)

    def test_lifecycle_sweep_marks_dormant_and_outdated_memory(self) -> None:
        store = MemoryStoreLite()
        dormant = store.write_memory_with_report(
            task_id="L1",
            source_agent="writer",
            task_topic="lifecycle dormant",
            summary="low frequency but still valid memory",
            tags=["lifecycle"],
            slot_hint="reuse_strategy",
        )
        dormant_report = store.apply_lifecycle_transitions(
            dormant_after_seconds=0,
            outdated_after_seconds=999999,
        )
        self.assertEqual(dormant_report.dormant_transition_count, 1)
        self.assertEqual(store._memories[dormant.memory_ref.memory_id].status, "dormant")
        self.assertTrue(store.validate_read_set([store.ref(store._memories[dormant.memory_ref.memory_id])]).allowed)

        outdated_report = store.apply_lifecycle_transitions(
            dormant_after_seconds=0,
            outdated_after_seconds=0,
        )
        self.assertEqual(outdated_report.outdated_transition_count, 1)
        outdated_ref = store.ref(store._memories[dormant.memory_ref.memory_id])
        self.assertEqual(outdated_ref.status, "outdated")
        self.assertFalse(store.validate_read_set([outdated_ref]).allowed)

    def test_soft_deprecation_creates_compensating_event_and_storage_view(self) -> None:
        store = MemoryStoreLite()
        report = store.write_memory_with_report(
            task_id="S1",
            source_agent="writer",
            task_topic="soft deprecation",
            summary="claim later found stale",
            tags=["soft_deprecation"],
            slot_hint="reuse_strategy",
        )

        event = store.soft_deprecate(
            report.memory_ref,
            reason="semantic_drift_detected",
            created_by="ReviewerAgent",
        )
        storage_view = store.render_storage_view(report.memory_ref)

        self.assertEqual(event.event_type, "soft_deprecation")
        self.assertEqual(store._memories[report.memory_ref.memory_id].status, "deprecated")
        self.assertIn("storage_view", storage_view)
        self.assertIn("compensating_events", storage_view)

    def test_write_intent_fence_blocks_other_writer_and_records_patch_regeneration(self) -> None:
        store = MemoryStoreLite()
        report = store.write_memory_with_report(
            task_id="W1",
            source_agent="writer",
            task_topic="write intent",
            summary="section depends on this memory",
            tags=["write_intent"],
            slot_hint="reuse_strategy",
        )

        fence = store.open_write_intent(
            task_id="W1",
            section_id="section-2",
            locked_by="ReviewerAgent",
            read_set=[report.memory_ref],
            estimated_cost_tokens=2000,
            ttl_ms=10000,
        )
        writer_validation = store.validate_read_set(
            [report.memory_ref],
            requester="WriterAgent",
        )
        reviewer_validation = store.validate_write_intent(fence.fence_id)
        patch = store.record_patch_regeneration(
            section_id="section-2",
            read_set=[report.memory_ref],
            patch_summary="regenerate section when fence blocks stale write",
            estimated_saved_tokens=1200,
        )

        self.assertTrue(fence.allowed)
        self.assertFalse(writer_validation.allowed)
        self.assertTrue(reviewer_validation.allowed)
        self.assertTrue(patch.regenerated)
        self.assertIn(report.memory_ref.memory_id, patch.stale_memory_ids)

    def test_layered_memory_writes_warm_sqlite_and_cold_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStoreLite(storage_dir=Path(tmp))
            report = store.write_memory_with_report(
                task_id="P1",
                source_agent="writer",
                task_topic="layered memory",
                summary="memory should appear in warm sqlite and cold file layer",
                tags=["layered"],
                slot_hint="reuse_strategy",
                source_state_ids=["state_layered"],
            )

            layer = store.layered_storage_report()
            self.assertGreaterEqual(layer.warm_record_count, 3)
            self.assertGreaterEqual(layer.cold_record_count, 1)
            conn = sqlite3.connect(layer.warm_db_path)
            try:
                row = conn.execute(
                    "SELECT payload_json FROM memory_records WHERE kind='memory' AND record_id=?",
                    (report.memory_ref.memory_id,),
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(row)
            self.assertTrue((Path(layer.cold_dir) / f"{report.memory_ref.memory_id}.json").exists())

    def test_layered_memory_reloads_without_snapshot_and_keeps_claim_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage_dir = Path(tmp)
            store = MemoryStoreLite(storage_dir=storage_dir)
            report = store.write_memory_with_report(
                task_id="R1",
                source_agent="reviewer",
                task_topic="persistent travel constraint",
                summary="Avoid overnight trains in future travel plans.",
                tags=["travel_reload"],
                slot_hint="reuse_strategy",
                source_state_ids=["state_reload"],
                evidence_refs=["evidence_reload"],
                confidence=0.93,
                claim_type="preference",
                polarity="negative",
                modality="preferred",
                temporal_scope="future",
                schema_version="ccf.v2-test",
            )
            (storage_dir / "memory_store_snapshot.json").unlink()

            restored = MemoryStoreLite(storage_dir=storage_dir)
            refs = restored.search_memory(
                "future travel plans overnight trains",
                tags=["travel_reload"],
            )
            restored_memory = restored._memories[report.memory_ref.memory_id]
            restored_claim = restored._claims[restored_memory.claim_id]

            self.assertIn(report.memory_ref.memory_id, [item.memory_id for item in refs])
            self.assertEqual(restored_claim.claim_type, "preference")
            self.assertEqual(restored_claim.polarity, "negative")
            self.assertEqual(restored_claim.modality, "preferred")
            self.assertEqual(restored_claim.temporal_scope, "future")
            self.assertEqual(restored_claim.schema_version, "ccf.v2-test")
            self.assertIn(restored_memory.promotion_view_id, restored._promotion_views)
            self.assertTrue(restored.memory_references(report.memory_ref))


if __name__ == "__main__":
    unittest.main()
