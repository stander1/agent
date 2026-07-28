import json
import unittest

from agent_runtime.bridge.state_memory_bridge import StateToMemoryBridgeLite
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.memory.semantic_disambiguator import (
    ControlledSemanticDisambiguator,
)


class _SemanticClient:
    def __init__(self, claims: list[dict[str, object]]) -> None:
        self.claims = claims
        self.call_count = 0

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, object]:
        del system_prompt, user_prompt
        self.call_count += 1
        return {
            "content": json.dumps(
                {
                    "schema_version": (
                        "agentlite.semantic-disambiguation.response.v1"
                    ),
                    "claims": self.claims,
                }
            ),
            "usage": {
                "prompt_tokens": 45,
                "completion_tokens": 15,
                "total_tokens": 60,
            },
            "model": "fixture-control-model",
        }


class StateToMemoryBridgeLiteTest(unittest.TestCase):
    def test_required_control_replaces_coarse_deterministic_candidate(self) -> None:
        source = (
            'A verified source states: "The flux remains 17 qx." '
            "Use that source in the next operation."
        )
        client = _SemanticClient(
            [
                {
                    "predicate": "flux",
                    "assertion_type": "observation",
                    "operator": "eq",
                    "value": "17",
                    "value_type": "number",
                    "unit": "qx",
                    "modality": "observed",
                    "temporal_status": "current",
                    "source_quote": "The flux remains 17 qx.",
                    "confidence": 0.82,
                }
            ]
        )
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(
            store,
            semantic_disambiguator=ControlledSemanticDisambiguator(client),
        )

        report, validation = bridge.promote(
            task_id="mixed-source-one",
            scope_id="scope-isolated",
            source_agent="framework-user",
            task_topic="unseen process",
            fallback_summary=source,
            tags=["generic"],
            slot_hint="source_evidence",
            source_state_ids=["state-source"],
            evidence_refs=["state-source"],
            reuse_intent="reuse direct source evidence",
            disambiguation_policy="control_required",
        )

        self.assertTrue(validation.allowed, validation.reasons)
        self.assertEqual(validation.disambiguation_policy, "control_required")
        self.assertEqual(client.call_count, 1)
        self.assertEqual(report.admission_status, "admitted")
        claim = store.snapshot()["claim_cards"][0]
        self.assertEqual(claim["value"], "17")
        self.assertEqual(
            claim["source_span"]["quote"],
            "The flux remains 17 qx.",
        )

    def test_required_control_failure_cannot_fall_back_to_coarse_candidate(
        self,
    ) -> None:
        source = "verified_level: 17 qx. Apply it downstream."
        client = _SemanticClient(
            [
                {
                    "predicate": "verified_level",
                    "value": "19",
                    "source_quote": "fabricated quote",
                }
            ]
        )
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(
            store,
            semantic_disambiguator=ControlledSemanticDisambiguator(client),
        )

        report, validation = bridge.promote(
            task_id="mixed-source-rejected",
            scope_id="scope-isolated",
            source_agent="framework-user",
            task_topic="unseen process",
            fallback_summary=source,
            tags=["generic"],
            slot_hint="source_evidence",
            source_state_ids=["state-source"],
            evidence_refs=["state-source"],
            reuse_intent="retain rejected source for audit",
            disambiguation_policy="control_required",
        )

        self.assertFalse(validation.allowed)
        self.assertNotEqual(report.admission_status, "admitted")
        self.assertEqual(store.snapshot()["memories"], [])

    def test_controlled_disambiguation_uses_normal_admission_path(
        self,
    ) -> None:
        source = (
            "At the final checkpoint, the waveform remained quiescent."
        )
        client = _SemanticClient(
            [
                {
                    "predicate": "waveform_state",
                    "assertion_type": "observation",
                    "operator": "eq",
                    "value": "quiescent",
                    "value_type": "string",
                    "modality": "observed",
                    "temporal_status": "current",
                    "source_quote": "the waveform remained quiescent",
                    "confidence": 0.7,
                }
            ]
        )
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(
            store,
            semantic_disambiguator=ControlledSemanticDisambiguator(client),
        )

        report, validation = bridge.promote(
            task_id="semantic-one",
            scope_id="scope-isolated",
            source_agent="specialist",
            task_topic="unseen physical process",
            fallback_summary=source,
            tags=["generic"],
            slot_hint="reuse_strategy",
            source_state_ids=["state-semantic"],
            evidence_refs=["state-semantic"],
            reuse_intent="reuse validated observation",
        )

        self.assertTrue(validation.allowed, validation.reasons)
        self.assertEqual(validation.disambiguation_status, "accepted")
        self.assertEqual(validation.disambiguation_call_count, 1)
        self.assertEqual(
            validation.disambiguation_accepted_candidate_count,
            1,
        )
        self.assertEqual(validation.control_total_tokens, 60)
        self.assertEqual(report.admission_status, "admitted")
        self.assertEqual(report.memory_write_count, 1)
        claim = store.snapshot()["claim_cards"][0]
        self.assertEqual(
            claim["source_span"]["quote"],
            "the waveform remained quiescent",
        )

    def test_deterministic_candidate_skips_control_llm(self) -> None:
        client = _SemanticClient([])
        bridge = StateToMemoryBridgeLite(
            MemoryStoreLite(),
            semantic_disambiguator=ControlledSemanticDisambiguator(client),
        )

        report, validation = bridge.promote(
            task_id="deterministic-one",
            scope_id="scope-isolated",
            source_agent="specialist",
            task_topic="unseen physical process",
            fallback_summary="phase_index: 17 qx",
            tags=["generic"],
            slot_hint="reuse_strategy",
            source_state_ids=["state-structured"],
            evidence_refs=["state-structured"],
            reuse_intent="reuse validated scalar",
        )

        self.assertEqual(client.call_count, 0)
        self.assertEqual(validation.disambiguation_status, "not_requested")
        self.assertEqual(report.admission_status, "admitted")

    def test_rules_only_retains_low_authority_candidate_without_control_cost(
        self,
    ) -> None:
        client = _SemanticClient(
            [
                {
                    "predicate": "unseen_state",
                    "value": "quiescent",
                    "source_quote": "The unseen state remains quiescent.",
                }
            ]
        )
        bridge = StateToMemoryBridgeLite(
            MemoryStoreLite(),
            semantic_disambiguator=ControlledSemanticDisambiguator(client),
        )

        report, validation = bridge.promote(
            task_id="rules-only-one",
            scope_id="scope-isolated",
            source_agent="arbitrary-intermediate",
            task_topic="unseen process",
            fallback_summary="The unseen state remains quiescent.",
            tags=["generic"],
            slot_hint="reuse_strategy",
            source_state_ids=["state-rules-only"],
            evidence_refs=["state-rules-only"],
            reuse_intent="retain as a candidate pending authority",
            control={
                "memory_card": {
                    "confidence": 0.5,
                    "importance_hint": 0.4,
                    "coverage_score": 0.6,
                }
            },
            disambiguation_policy="rules_only",
        )

        self.assertEqual(client.call_count, 0)
        self.assertTrue(validation.allowed, validation.reasons)
        self.assertEqual(validation.disambiguation_policy, "rules_only")
        self.assertEqual(validation.disambiguation_status, "not_requested")
        self.assertEqual(report.admission_status, "pending")
        self.assertEqual(report.memory_write_count, 0)

    def test_failed_disambiguation_remains_audit_only(self) -> None:
        client = _SemanticClient(
            [
                {
                    "predicate": "waveform_state",
                    "value": "quiescent",
                    "source_quote": "fabricated source quote",
                }
            ]
        )
        bridge = StateToMemoryBridgeLite(
            MemoryStoreLite(),
            semantic_disambiguator=ControlledSemanticDisambiguator(client),
        )

        report, validation = bridge.promote(
            task_id="semantic-rejected",
            scope_id="scope-isolated",
            source_agent="specialist",
            task_topic="unseen physical process",
            fallback_summary="The waveform state was not resolved.",
            tags=["generic"],
            slot_hint="reuse_strategy",
            source_state_ids=["state-rejected"],
            evidence_refs=["state-rejected"],
            reuse_intent="retain source for audit",
        )

        self.assertFalse(validation.allowed)
        self.assertEqual(validation.disambiguation_status, "rejected")
        self.assertIn(
            "semantic_disambiguation_rejected",
            validation.reasons,
        )
        self.assertEqual(report.admission_status, "audit_only")
        self.assertEqual(report.memory_write_count, 0)

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
