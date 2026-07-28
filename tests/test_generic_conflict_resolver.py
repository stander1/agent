from __future__ import annotations

import unittest

from agent_runtime.bridge.state_memory_bridge import StateToMemoryBridgeLite
from agent_runtime.memory.conflict_resolver import resolve_claim_conflicts
from agent_runtime.memory.memory_store import MemoryStoreLite


class GenericConflictResolverTest(unittest.TestCase):
    def test_explicit_revision_supersedes_matching_value(self) -> None:
        resolution = resolve_claim_conflicts(
            [
                self._claim("old", "800", unit="req/s"),
                self._claim(
                    "new",
                    "1200",
                    unit="req/s",
                    relations=[
                        {
                            "relation_type": "supersedes_value",
                            "target_value": "800",
                        }
                    ],
                ),
            ]
        )

        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.active_claim_ids, ("new",))
        self.assertEqual(resolution.superseded_claim_ids, ("old",))
        self.assertIn("explicit_supersession_applied", resolution.reasons)

    def test_unrelated_string_values_remain_unresolved_without_revision(
        self,
    ) -> None:
        resolution = resolve_claim_conflicts(
            [
                self._claim("first", "alpha", value_type="string"),
                self._claim("second", "beta", value_type="string"),
            ]
        )

        self.assertEqual(resolution.status, "unresolved")
        self.assertEqual(
            set(resolution.conflicting_claim_ids),
            {"first", "second"},
        )

    def test_compatible_numeric_constraints_coexist(self) -> None:
        resolution = resolve_claim_conflicts(
            [
                self._claim("minimum", "10", operator="ge"),
                self._claim("maximum", "20", operator="le"),
                self._claim(
                    "excluded",
                    "13",
                    operator="ne",
                    polarity="negative",
                ),
            ]
        )

        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(
            set(resolution.active_claim_ids),
            {"minimum", "maximum", "excluded"},
        )
        self.assertIn("compatible_numeric_constraints", resolution.reasons)

    def test_incompatible_numeric_constraints_remain_unresolved(self) -> None:
        resolution = resolve_claim_conflicts(
            [
                self._claim("minimum", "30", operator="ge"),
                self._claim("maximum", "20", operator="le"),
            ]
        )

        self.assertEqual(resolution.status, "unresolved")
        self.assertIn("constraint_interval_empty", resolution.reasons)

    def test_historical_and_future_claims_are_never_selected(self) -> None:
        resolution = resolve_claim_conflicts(
            [
                self._claim(
                    "historical",
                    "old",
                    value_type="string",
                    temporal_status="historical",
                ),
                self._claim(
                    "future",
                    "proposal",
                    value_type="string",
                    temporal_status="future",
                ),
                self._claim(
                    "current",
                    "active",
                    value_type="string",
                    temporal_status="current",
                ),
            ]
        )

        self.assertEqual(resolution.status, "resolved")
        self.assertEqual(resolution.active_claim_ids, ("current",))
        self.assertEqual(
            set(resolution.superseded_claim_ids),
            {"historical", "future"},
        )

    def test_domain_and_role_terms_do_not_affect_resolution(self) -> None:
        for field_name, first, second in (
            ("dosage_window", "4", "6"),
            ("compiler_passes", "4", "6"),
            ("shipment_batches", "4", "6"),
        ):
            with self.subTest(field_name=field_name):
                resolution = resolve_claim_conflicts(
                    [
                        {
                            **self._claim("old", first),
                            "raw_slot_text": field_name,
                            "source_agent": "arbitrary-agent-a",
                        },
                        {
                            **self._claim(
                                "new",
                                second,
                                relations=[
                                    {
                                        "relation_type": "supersedes_value",
                                        "target_value": first,
                                    }
                                ],
                            ),
                            "raw_slot_text": field_name,
                            "source_agent": "arbitrary-agent-b",
                        },
                    ]
                )
                self.assertEqual(resolution.active_claim_ids, ("new",))

    def test_memory_compaction_uses_explicit_open_claim_revision(self) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(store)
        common = {
            "source_agent": "arbitrary-specialist",
            "task_topic": "service:holdout",
            "tags": ["holdout"],
            "slot_hint": "reuse_strategy",
            "reuse_intent": "reuse validated facts",
            "control": None,
            "degraded": False,
        }
        first, first_validation = bridge.promote(
            task_id="T1",
            fallback_summary="throughput_limit: 800 req/s",
            source_state_ids=["state_1"],
            evidence_refs=["state_1"],
            **common,
        )
        second, second_validation = bridge.promote(
            task_id="T2",
            fallback_summary=(
                "throughput_limit: from 800 req/s to 1200 req/s"
            ),
            source_state_ids=["state_2"],
            evidence_refs=["state_2"],
            **common,
        )

        self.assertTrue(first_validation.allowed)
        self.assertTrue(second_validation.allowed)
        self.assertEqual(first.admission_status, "admitted")
        self.assertEqual(second.admission_status, "admitted")
        view = next(
            item
            for item in store.snapshot()["memory_views"]
            if item["slot_id"] == first.memory_ref.slot_id
        )
        self.assertEqual(view["active_value"]["value"], "1200")
        self.assertEqual(len(view["historical_values"]), 1)
        self.assertEqual(view["historical_values"][0]["value"], "800")

    @staticmethod
    def _claim(
        claim_id: str,
        value: str,
        *,
        value_type: str = "number",
        unit: str = "",
        operator: str = "eq",
        polarity: str = "positive",
        temporal_status: str = "current",
        relations: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        return {
            "claim_id": claim_id,
            "candidate_id": f"candidate_{claim_id}",
            "value": value,
            "value_type": value_type,
            "unit": unit,
            "operator": operator,
            "polarity": polarity,
            "temporal_status": temporal_status,
            "relations": relations or [],
        }


if __name__ == "__main__":
    unittest.main()
