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

    def test_estimate_and_upper_bound_in_same_sentence_remain_distinct(
        self,
    ) -> None:
        claims = extract_claim_cards(
            "原方案总预算为2300元，已低于新上限2600元。",
            subject="project:generic",
        )
        budget_claims = {
            (str(claim["scope"]), str(claim["value"]))
            for claim in claims
            if "budget" in str(claim["scope"])
        }

        self.assertIn(("estimate.budget_total", "2300"), budget_claims)
        self.assertIn(
            ("constraint.budget_upper_bound", "2600"),
            budget_claims,
        )
        self.assertNotIn(
            ("constraint.budget_upper_bound", "2300"),
            budget_claims,
        )

    def test_budget_revision_promotes_target_not_historical_source(
        self,
    ) -> None:
        claims = extract_claim_cards(
            "将总预算上限从3000元正式更新为2600元。",
            subject="project:generic",
        )
        upper_bounds = [
            claim
            for claim in claims
            if claim["scope"] == "constraint.budget_upper_bound"
        ]

        self.assertEqual([claim["value"] for claim in upper_bounds], ["2600"])
        self.assertEqual(upper_bounds[0]["revision_kind"], "replaces")

    def test_grouped_currency_amount_is_not_truncated(self) -> None:
        claims = extract_claim_cards(
            "总预算上限更新为2,600元。",
            subject="project:generic",
        )
        upper_bounds = [
            claim
            for claim in claims
            if claim["scope"] == "constraint.budget_upper_bound"
        ]

        self.assertEqual([claim["value"] for claim in upper_bounds], ["2600"])

    def test_component_and_reserve_amounts_do_not_become_global_caps(
        self,
    ) -> None:
        claims = extract_claim_cards(
            (
                "方案为用户预留了700元的预算弹性空间。"
                "留给餐饮和应急的预算余量约400元。"
                "保留项：交通（380元）、住宿（700元）、"
                "本地接驳（180元）及核心活动（180元）预算均予以保证。"
            ),
            subject="project:generic",
        )

        self.assertFalse(
            any(
                claim["scope"] == "constraint.budget_upper_bound"
                for claim in claims
            )
        )
        self.assertTrue(
            any(
                str(claim["scope"]).startswith("allocation.budget.")
                for claim in claims
            )
        )

    def test_base_budget_is_estimate_and_budget_note_number_is_ignored(
        self,
    ) -> None:
        claims = extract_claim_cards(
            "在2200元的基础预算上优化方案。预算说明：1.",
            subject="project:generic",
        )
        budget_rows = [
            claim
            for claim in claims
            if "budget" in str(claim["scope"])
        ]

        self.assertIn(
            ("estimate.budget_total", "2200"),
            {
                (str(claim["scope"]), str(claim["value"]))
                for claim in budget_rows
            },
        )
        self.assertFalse(
            any(
                claim["scope"] == "constraint.budget_upper_bound"
                for claim in budget_rows
            )
        )
        self.assertFalse(any(str(claim["value"]) == "1" for claim in budget_rows))

    def test_selected_destination_drops_trailing_reason_heading(self) -> None:
        claims = extract_claim_cards(
            "最终目的地：宜兴 选择理由：交通与节奏更匹配。",
            subject="project:generic",
        )
        destinations = [
            claim
            for claim in claims
            if claim["scope"] == "plan.selected_destination"
        ]

        self.assertEqual([claim["value"] for claim in destinations], ["宜兴"])
        self.assertFalse(
            any(
                claim["scope"] == "config.destination"
                for claim in claims
            )
        )

    def test_historical_decision_log_does_not_emit_active_budget_cap(
        self,
    ) -> None:
        claims = extract_claim_cards(
            """## Current plan
The budget cap is 2600 USD and the estimated total is 2400 USD.

## Decision log
R3 -> R4: the old total budget was capped at 3000 USD.
""",
            subject="project:generic",
        )
        upper_bounds = [
            str(claim["value"])
            for claim in claims
            if claim["scope"] == "constraint.budget_upper_bound"
        ]

        self.assertEqual(upper_bounds, ["2600"])

    def test_current_result_after_plain_history_heading_is_extracted(
        self,
    ) -> None:
        claims = extract_claim_cards(
            """Decision log:
- Interaction #1: the initial cap was 3000 USD.
Current budget cap is 2600 USD.
""",
            subject="project:generic",
        )
        upper_bounds = [
            str(claim["value"])
            for claim in claims
            if claim["scope"] == "constraint.budget_upper_bound"
        ]

        self.assertEqual(upper_bounds, ["2600"])

    def test_bold_history_heading_is_excluded_from_active_claims(self) -> None:
        claims = extract_claim_cards(
            """Current budget cap: 2600 USD.
**Decision log**
R1 -> R2: historical budget cap: 3000 USD.
""",
            subject="project:generic",
        )
        upper_bounds = [
            str(claim["value"])
            for claim in claims
            if claim["scope"] == "constraint.budget_upper_bound"
        ]

        self.assertEqual(upper_bounds, ["2600"])

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
