import unittest

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
            slot_hint="travel_preference",
            confidence=0.62,
        )
        second = store.write_memory_with_report(
            task_id="A2",
            source_agent="reviewer",
            task_topic="travel preference",
            summary="预算优先，但交通方案改为高铁优先。",
            tags=["travel_A"],
            slot_hint="travel_preference",
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
            slot_hint="security_audit",
            confidence=0.55,
        )
        latest = store.write_memory_with_report(
            task_id="B2",
            source_agent="reviewer",
            task_topic="security evidence",
            summary="修正结论：弱口令风险高，需要立即整改。",
            tags=["security_B"],
            slot_hint="security_audit",
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


if __name__ == "__main__":
    unittest.main()
