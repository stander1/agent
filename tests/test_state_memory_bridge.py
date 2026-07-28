import unittest

from agent_runtime.bridge.state_memory_bridge import StateToMemoryBridgeLite
from agent_runtime.memory.memory_store import MemoryStoreLite


class StateToMemoryBridgeLiteTest(unittest.TestCase):
    def test_bridge_promotes_valid_state_into_admitted_memory_candidate(self) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(store)

        report, validation = bridge.promote(
            task_id="T1",
            source_agent="arbitrary-author",
            task_topic="generic project",
            fallback_summary="total_limit: 3000 CNY",
            tags=["generic", "T1", "arbitrary-author"],
            slot_hint="reuse_strategy",
            source_state_ids=["state_generic"],
            evidence_refs=["state_generic"],
            reuse_intent="reuse the validated exact fact",
            control={
                "claim_cards": [
                    {
                        "subject": "generic project",
                        "raw_slot_text": "total_limit",
                        "slot_id": "slot.project.requirement",
                        "scope": "constraint.total_limit",
                        "value": "3000",
                        "value_type": "integer",
                        "unit": "CNY",
                        "certainty": "confirmed",
                        "modality": "asserted",
                        "polarity": "positive",
                        "confidence": 0.9,
                    }
                ]
            },
            degraded=False,
        )

        self.assertTrue(validation.allowed)
        self.assertEqual(report.admission_status, "admitted")
        self.assertEqual(report.memory_write_count, 1)
        self.assertEqual(report.promotion_view_count, 1)
        self.assertIsNotNone(report.memory_ref)
        self.assertEqual(validation.explicit_claim_count, 1)
        self.assertEqual(validation.explicit_claim_valid_count, 1)
        claim = store.snapshot()["claim_cards"][0]
        self.assertEqual(claim["source_span"]["source_id"], "state_generic")
        self.assertEqual(claim["source_span"]["quote"], "total_limit: 3000 CNY")

    def test_explicit_claim_without_matching_source_is_audit_only(self) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(store)

        report, validation = bridge.promote(
            task_id="T-unbound",
            source_agent="arbitrary-author",
            task_topic="generic project",
            fallback_summary="status_label: green",
            tags=["generic"],
            slot_hint="reuse_strategy",
            source_state_ids=["state_unbound"],
            evidence_refs=["state_unbound"],
            reuse_intent="retain only evidence-bound facts",
            control={
                "claim_cards": [
                    {
                        "subject": "generic project",
                        "raw_slot_text": "status_label",
                        "slot_id": "slot.project.requirement",
                        "scope": "status.label",
                        "value": "red",
                        "certainty": "confirmed",
                    }
                ]
            },
            degraded=False,
        )

        self.assertFalse(validation.allowed)
        self.assertEqual(validation.explicit_claim_count, 1)
        self.assertEqual(validation.explicit_claim_valid_count, 0)
        self.assertIn(
            "explicit_claim_value_not_in_source",
            validation.reasons,
        )
        self.assertEqual(report.admission_status, "audit_only")
        self.assertEqual(report.memory_write_count, 0)
        self.assertIsNone(report.memory_ref)

    def test_bridge_keeps_unstructured_summary_out_of_formal_memory(self) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(store)

        report, validation = bridge.promote(
            task_id="N1",
            source_agent="writer",
            task_topic="generic project",
            fallback_summary="This is a reusable narrative summary without a concrete fact.",
            tags=["generic", "N1", "writer"],
            slot_hint="reuse_strategy",
            source_state_ids=["state_summary"],
            evidence_refs=["state_summary"],
            reuse_intent="reuse in later tasks",
            control=None,
            degraded=False,
        )

        self.assertTrue(validation.allowed)
        self.assertEqual(report.admission_status, "audit_only")
        self.assertEqual(report.memory_write_count, 0)
        self.assertEqual(report.admission_reasons, ["no_structured_claims"])
        self.assertIsNone(report.memory_ref)

    def test_degraded_contract_becomes_audit_only_candidate(self) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(store)

        report, validation = bridge.promote(
            task_id="B1",
            source_agent="reviewer",
            task_topic="security audit",
            fallback_summary="控制头降级，只允许审查或重试。",
            tags=["security_B", "B1", "reviewer"],
            slot_hint="failure_reason",
            source_state_ids=["state_failure"],
            evidence_refs=["state_failure"],
            reuse_intent="供 security_B 后续连续任务复用",
            control=None,
            degraded=True,
        )

        self.assertTrue(validation.allowed)
        self.assertEqual(report.admission_status, "audit_only")
        self.assertEqual(report.memory_write_count, 0)
        self.assertIsNone(report.memory_ref)

    def test_bridge_admits_validated_open_predicate_without_domain_schema(
        self,
    ) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(store)

        report, validation = bridge.promote(
            task_id="G1",
            source_agent="specialist",
            task_topic="holdout project",
            fallback_summary="unknown_metric: <= 42 qux",
            tags=["holdout", "G1"],
            slot_hint="reuse_strategy",
            source_state_ids=["state_holdout"],
            evidence_refs=["state_holdout"],
            reuse_intent="reuse validated open fact",
            control=None,
            degraded=False,
        )

        self.assertTrue(validation.allowed)
        self.assertEqual(validation.canonical_candidate_count, 1)
        self.assertEqual(validation.canonical_candidate_valid_count, 1)
        self.assertEqual(validation.dynamic_schema_registration_count, 1)
        self.assertEqual(report.admission_status, "admitted")
        self.assertEqual(report.memory_write_count, 1)
        self.assertIsNotNone(report.memory_ref)
        assert report.memory_ref is not None
        self.assertTrue(report.memory_ref.slot_id.startswith("slot.open."))

        claim = next(
            item
            for item in store.snapshot()["claim_cards"]
            if item["claim_id"] == report.claim_ids[0]
        )
        self.assertEqual(claim["operator"], "le")
        self.assertEqual(claim["value"], "42")
        self.assertEqual(claim["unit"], "qux")
        self.assertEqual(claim["schema_layer"], "dynamic")
        self.assertEqual(claim["source_span"]["source_id"], "state_holdout")

    def test_historical_open_candidate_is_audit_only(self) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(store)

        report, validation = bridge.promote(
            task_id="G2",
            source_agent="specialist",
            task_topic="holdout project",
            fallback_summary="previous_limit: 9 qux",
            tags=["holdout", "G2"],
            slot_hint="reuse_strategy",
            source_state_ids=["state_history"],
            evidence_refs=["state_history"],
            reuse_intent="retain history for audit",
            control=None,
            degraded=False,
        )

        self.assertTrue(validation.allowed)
        self.assertEqual(report.admission_status, "audit_only")
        self.assertEqual(report.memory_write_count, 0)
        self.assertEqual(
            report.admission_reasons,
            ["epistemic_confirmation_required"],
        )

    def test_unresolved_open_predicate_fails_closed_when_dynamic_schema_is_off(
        self,
    ) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(
            store,
            allow_dynamic_schema=False,
        )

        report, validation = bridge.promote(
            task_id="G3",
            source_agent="specialist",
            task_topic="holdout project",
            fallback_summary="unregistered_signal: 7 qux",
            tags=["holdout", "G3"],
            slot_hint="reuse_strategy",
            source_state_ids=["state_unresolved"],
            evidence_refs=["state_unresolved"],
            reuse_intent="do not admit unresolved facts",
            control=None,
            degraded=False,
        )

        self.assertFalse(validation.allowed)
        self.assertEqual(validation.unresolved_schema_count, 1)
        self.assertIn("open_predicate_unresolved", validation.reasons)
        self.assertEqual(report.admission_status, "audit_only")
        self.assertEqual(report.memory_write_count, 0)
        self.assertIsNone(report.memory_ref)


if __name__ == "__main__":
    unittest.main()
