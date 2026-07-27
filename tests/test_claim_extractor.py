from __future__ import annotations

import unittest

from agent_runtime.memory.claim_extractor import (
    claim_identity,
    extract_claim_cards,
)
from agent_runtime.memory.schema_registry import SchemaRegistryLite


class ClaimExtractorTests(unittest.TestCase):
    def test_legacy_budget_scope_maps_to_upper_bound(self) -> None:
        self.assertEqual(
            SchemaRegistryLite.normalize_scope("constraint.budget"),
            "constraint.budget_upper_bound",
        )

    def test_budget_facets_have_distinct_claim_identities(self) -> None:
        claims = extract_claim_cards(
            (
                "总预算上限不超过3000元。"
                "当前方案预计总费用为2320元。"
                "伴手礼预算为200元。"
            ),
            subject="project:generic",
        )
        budget_claims = {
            str(claim["scope"]): claim
            for claim in claims
            if "budget" in str(claim["scope"])
        }

        self.assertEqual(
            budget_claims["constraint.budget_upper_bound"]["value"],
            "3000",
        )
        self.assertEqual(
            budget_claims["estimate.budget_total"]["value"],
            "2320",
        )
        allocation_scope = next(
            scope
            for scope in budget_claims
            if scope.startswith("allocation.budget.")
        )
        self.assertEqual(budget_claims[allocation_scope]["value"], "200")
        self.assertEqual(len({claim_identity(row) for row in budget_claims.values()}), 3)

    def test_markdown_budget_table_preserves_component_rows(self) -> None:
        claims = extract_claim_cards(
            """
| 费用类别 | 预算（元） | 说明 |
| --- | ---: | --- |
| 住宿 | 760 | 两晚 |
| 核心活动 | 180 | 门票 |
| 总计 | 2300 | 当前估算 |
""",
            subject="project:generic",
        )
        budget_claims = {
            str(claim["scope"]): claim
            for claim in claims
            if "budget" in str(claim["scope"])
        }

        self.assertEqual(budget_claims["estimate.budget_total"]["value"], "2300")
        component_values = {
            str(claim["value"])
            for scope, claim in budget_claims.items()
            if scope.startswith("allocation.budget.")
        }
        self.assertEqual(component_values, {"760", "180"})

    def test_total_budget_phrase_is_not_misclassified_as_component(self) -> None:
        claims = extract_claim_cards(
            "本方案总预算为2300元。",
            subject="project:generic",
        )
        budget_claims = [
            claim for claim in claims if "budget" in str(claim["scope"])
        ]

        self.assertEqual(len(budget_claims), 1)
        self.assertEqual(
            budget_claims[0]["scope"],
            "estimate.budget_total",
        )

    def test_explicit_overall_decision_is_preserved_as_a_typed_claim(self) -> None:
        claims = extract_claim_cards(
            (
                "| 候选标签 | 局部判断 |\n"
                "| --- | --- |\n"
                "| option_alpha | needs_review |\n"
                "综合风险判断：needs_more_evidence（需要补充来源证据）。"
            ),
            subject="project:generic",
        )
        decisions = [
            claim
            for claim in claims
            if claim["scope"] == "decision.risk"
        ]

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["value"], "needs_more_evidence")
        self.assertEqual(
            decisions[0]["slot_id"],
            "slot.system.design_decision",
        )
        self.assertEqual(decisions[0]["modality"], "decision")

    def test_english_final_status_is_preserved_without_domain_rules(self) -> None:
        claims = extract_claim_cards(
            "Final status: ready_for_release.",
            subject="project:generic",
        )
        decisions = [
            claim
            for claim in claims
            if claim["scope"] == "decision.status"
        ]

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["value"], "ready_for_release")

    def test_assignment_suffix_is_not_promoted_to_global_decision(self) -> None:
        claims = extract_claim_cards(
            "reviewer completed T1; reviewer_result=completed",
            subject="project:generic",
        )

        self.assertFalse(
            any(
                claim["slot_id"] == "slot.system.design_decision"
                for claim in claims
            )
        )


if __name__ == "__main__":
    unittest.main()
