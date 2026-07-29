from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_runtime.bridge.state_memory_bridge import (
    CanonicalClaimSemanticValidator,
)
from agent_runtime.memory.claim_extractor import (
    extract_canonical_claim_candidates,
    requests_historical_state,
    value_has_temporal_status,
)
from agent_runtime.memory.schema_registry import SourceSpan
from agent_runtime.memory.schema_registry import SchemaRegistryLite


class GenericSemanticBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = CanonicalClaimSemanticValidator()

    def test_open_candidates_cover_unrelated_domains(self) -> None:
        text = """latency_p95: <= 180 ms (current)
storage_capacity: 12 TB
headcount: 24 people
deadline: 2026-09-30
risk_score: medium
destination_choice: Yixing
"""
        candidates = extract_canonical_claim_candidates(
            text,
            subject="project:generic",
            source_id="state:1",
        )
        by_predicate = {item["predicate"]: item for item in candidates}

        self.assertEqual(by_predicate["latency_p95"]["operator"], "le")
        self.assertEqual(by_predicate["latency_p95"]["value"], "180")
        self.assertEqual(by_predicate["latency_p95"]["unit"], "ms")
        self.assertEqual(
            by_predicate["latency_p95"]["temporal_status"],
            "current",
        )
        self.assertEqual(by_predicate["storage_capacity"]["value"], "12")
        self.assertEqual(by_predicate["headcount"]["value"], "24")
        self.assertEqual(by_predicate["deadline"]["value_type"], "date")
        self.assertEqual(by_predicate["risk_score"]["value"], "medium")
        self.assertEqual(
            by_predicate["destination_choice"]["value"],
            "Yixing",
        )

    def test_open_value_parser_requires_a_complete_measurement(self) -> None:
        text = """transfer_rate: -1.8 qx
operating_mode: 17 qx standby
interval: 1 to 3 qx
"""
        candidates = extract_canonical_claim_candidates(
            text,
            subject="process:generic",
            source_id="state:complete-measurement",
        )
        by_predicate = {item["predicate"]: item for item in candidates}

        self.assertEqual(by_predicate["transfer_rate"]["value"], "-1.8")
        self.assertEqual(by_predicate["transfer_rate"]["value_type"], "number")
        self.assertEqual(by_predicate["transfer_rate"]["unit"], "qx")
        self.assertEqual(
            by_predicate["operating_mode"]["value"],
            "17 qx standby",
        )
        self.assertEqual(
            by_predicate["operating_mode"]["value_type"],
            "string",
        )
        self.assertEqual(by_predicate["interval"]["value"], "1 to 3 qx")
        self.assertEqual(by_predicate["interval"]["value_type"], "string")

    def test_source_span_is_exact_and_revalidated(self) -> None:
        text = "throughput_limit: >= 1200 req/s\n"
        candidate = extract_canonical_claim_candidates(
            text,
            subject="service:api",
            source_id="state:throughput",
        )[0]
        span = SourceSpan(**candidate["source_span"])

        self.assertTrue(span.matches(text))
        self.assertTrue(
            self.validator.validate(candidate, source_text=text).allowed
        )
        result = self.validator.validate(
            candidate,
            source_text=text.replace("1200", "900"),
        )
        self.assertFalse(result.allowed)
        self.assertIn("source_span_mismatch", result.reasons)

    def test_revision_preserves_prior_value_as_relation(self) -> None:
        text = "throughput_limit: from 800 req/s to 1200 req/s"
        candidate = extract_canonical_claim_candidates(
            text,
            subject="service:api",
            source_id="state:revision",
        )[0]

        self.assertEqual(candidate["assertion_type"], "revision")
        self.assertEqual(candidate["value"], "1200")
        self.assertEqual(candidate["unit"], "req/s")
        self.assertEqual(
            candidate["relations"],
            [
                {
                    "relation_type": "supersedes_value",
                    "target_value": "800",
                    "target_candidate_id": "",
                }
            ],
        )
        self.assertTrue(
            self.validator.validate(candidate, source_text=text).allowed
        )

    def test_non_supersession_relation_target_remains_source_bound(self) -> None:
        text = "signal_state: ready"
        candidate = extract_canonical_claim_candidates(
            text,
            subject="system:generic",
            source_id="state:relation-boundary",
        )[0]
        candidate["relations"] = [
            {
                "relation_type": "supports",
                "target_value": "absent target",
                "target_candidate_id": "",
            }
        ]

        result = self.validator.validate(candidate, source_text=text)

        self.assertFalse(result.allowed)
        self.assertIn("relation_target_not_in_source_span", result.reasons)

    def test_external_relation_candidate_identity_is_rejected(self) -> None:
        text = "signal_state: ready"
        candidate = extract_canonical_claim_candidates(
            text,
            subject="system:generic",
            source_id="state:relation-identity",
        )[0]
        candidate["relations"] = [
            {
                "relation_type": "supersedes_claim",
                "target_value": "",
                "target_candidate_id": "provider_supplied_id",
            }
        ]

        result = self.validator.validate(candidate, source_text=text)

        self.assertFalse(result.allowed)
        self.assertIn(
            "relation_target_candidate_id_not_locally_bound",
            result.reasons,
        )
    def test_paired_temporal_request_and_local_value_label_are_generic(
        self,
    ) -> None:
        request = (
            "Produce a two-state record. Preserve the current reading and "
            "label the former reading archived."
        )
        output = "Current reading: 8.2 units. Archived reading: 7.9 units."

        self.assertTrue(requests_historical_state(request))
        self.assertTrue(
            value_has_temporal_status(
                output,
                "7.9",
                status="historical",
            )
        )
        self.assertTrue(
            value_has_temporal_status(
                "Archived enabled flag: false.",
                False,
                status="historical",
            )
        )
        self.assertTrue(
            value_has_temporal_status(
                "Former retry count: 0.",
                0,
                status="historical",
            )
        )
        self.assertFalse(
            requests_historical_state(
                "Publish only the current reading and ignore old discussion."
            )
        )

    def test_reordering_does_not_change_semantic_values(self) -> None:
        first = """capacity: 12 TB
latency: <= 180 ms
headcount: 24 people
"""
        second = """headcount: 24 people
capacity: 12 TB
latency: <= 180 ms
"""

        def semantic_set(text: str) -> set[tuple[str, str, str, str]]:
            return {
                (
                    item["predicate"],
                    item["operator"],
                    item["value"],
                    item["unit"],
                )
                for item in extract_canonical_claim_candidates(
                    text,
                    subject="project:generic",
                    source_id="state:metamorphic",
                )
            }

        self.assertEqual(semantic_set(first), semantic_set(second))

    def test_markdown_table_uses_headers_as_open_predicates(self) -> None:
        text = """| component | threshold | owner |
| --- | --- | --- |
| api | <= 180 ms | team-a |
"""
        candidates = extract_canonical_claim_candidates(
            text,
            subject="service",
            source_id="state:table",
        )
        by_predicate = {item["predicate"]: item for item in candidates}

        self.assertEqual(by_predicate["threshold"]["subject"], "service/api")
        self.assertEqual(by_predicate["threshold"]["operator"], "le")
        self.assertEqual(by_predicate["threshold"]["value"], "180")
        self.assertEqual(by_predicate["owner"]["value"], "team-a")

    def test_compact_json_scalars_keep_exact_evidence_spans(self) -> None:
        text = (
            '{"phase":"verified","retry_count":3,'
            '"enabled":true,"ratio":1.25e-2,"nested":{"mode":"safe"}}'
        )

        candidates = extract_canonical_claim_candidates(
            text,
            subject="artifact:generic",
            source_id="state:json",
        )
        by_predicate = {item["predicate"]: item for item in candidates}

        self.assertEqual(by_predicate["phase"]["value"], "verified")
        self.assertEqual(by_predicate["retry_count"]["value"], "3")
        self.assertEqual(by_predicate["enabled"]["value_type"], "boolean")
        self.assertEqual(by_predicate["ratio"]["value"], "1.25e-2")
        self.assertEqual(by_predicate["mode"]["value"], "safe")
        for candidate in candidates:
            span = SourceSpan(**candidate["source_span"])
            self.assertTrue(span.matches(text))
            self.assertTrue(
                self.validator.validate(
                    candidate,
                    source_text=text,
                ).allowed
            )

    def test_inline_assignments_preserve_typed_values_and_exact_spans(
        self,
    ) -> None:
        text = (
            'phase_bias=-1.8 qx, interlock=false; '
            'material_state="alpha, beta"\n'
            "sample_count=1,234 records, verified=true"
        )

        candidates = extract_canonical_claim_candidates(
            text,
            subject="artifact:unseen",
            source_id="state:inline",
        )
        by_predicate = {item["predicate"]: item for item in candidates}

        self.assertEqual(by_predicate["phase_bias"]["value"], "-1.8")
        self.assertEqual(by_predicate["phase_bias"]["unit"], "qx")
        self.assertEqual(
            by_predicate["interlock"]["value_type"],
            "boolean",
        )
        self.assertEqual(
            by_predicate["material_state"]["value"],
            "alpha, beta",
        )
        self.assertEqual(by_predicate["sample_count"]["value"], "1234")
        self.assertEqual(by_predicate["sample_count"]["unit"], "records")
        self.assertEqual(by_predicate["verified"]["value"], "true")
        for candidate in candidates:
            span = SourceSpan(**candidate["source_span"])
            self.assertTrue(span.matches(text))
            self.assertTrue(
                self.validator.validate(
                    candidate,
                    source_text=text,
                ).allowed
            )

    def test_generic_extractor_does_not_call_legacy_domain_rules(self) -> None:
        with patch(
            "agent_runtime.memory.claim_extractor._extract_known_claims",
            side_effect=AssertionError("legacy domain extractor was called"),
        ):
            candidates = extract_canonical_claim_candidates(
                "unknown_metric: 42 qux",
                subject="holdout",
                source_id="state:holdout",
            )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["predicate"], "unknown_metric")
        self.assertEqual(candidates[0]["unit"], "qux")

    def test_layered_registry_registers_open_predicates_without_domain_map(
        self,
    ) -> None:
        registry = SchemaRegistryLite(
            canonical_slots={"slot.system.design_decision"},
            alias_mapping={},
        )

        unresolved = registry.resolve("任意新指标")
        self.assertTrue(unresolved.unresolved)

        dynamic = registry.register_dynamic_predicate("任意新指标")
        self.assertFalse(dynamic.unresolved)
        self.assertEqual(dynamic.layer, "dynamic")
        self.assertTrue(dynamic.slot_id.startswith("slot.open."))
        self.assertEqual(
            registry.resolve("任意新指标").slot_id,
            dynamic.slot_id,
        )

        developer = registry.register_developer_slot(
            predicate="service_level",
            slot_id="slot.developer.service_level",
        )
        self.assertEqual(developer.layer, "developer")
        self.assertEqual(
            registry.resolve("service_level").slot_id,
            "slot.developer.service_level",
        )

    def test_dynamic_schema_identity_survives_slug_collision(self) -> None:
        registry = SchemaRegistryLite(
            canonical_slots=set(),
            alias_mapping={},
        )

        hyphenated = registry.register_dynamic_predicate("signal-rate")
        spaced = registry.register_dynamic_predicate("signal rate")
        slashed = registry.register_dynamic_predicate("signal/rate")

        self.assertNotEqual(hyphenated.slot_id, spaced.slot_id)
        self.assertNotEqual(spaced.slot_id, slashed.slot_id)
        self.assertNotEqual(hyphenated.slot_id, slashed.slot_id)
        self.assertEqual(
            registry.resolve("signal-rate").slot_id,
            hyphenated.slot_id,
        )
        self.assertEqual(
            registry.resolve("signal rate").slot_id,
            spaced.slot_id,
        )
        self.assertEqual(
            registry.resolve("signal/rate").slot_id,
            slashed.slot_id,
        )


if __name__ == "__main__":
    unittest.main()
