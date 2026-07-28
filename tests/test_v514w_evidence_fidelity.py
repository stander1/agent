from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_runtime.drivers.autogen import (
    _required_evidence_assessment,
    _required_evidence_contract,
    _required_evidence_prompt_rule,
)
from agent_runtime.memory.claim_extractor import extract_claim_cards


class EvidenceFidelityAcceptanceTest(unittest.TestCase):
    def test_budget_facets_are_not_collapsed_by_sentence_level_cues(self) -> None:
        claims = extract_claim_cards(
            (
                "The working budget estimate is 2,300 USD, while the approved "
                "budget cap remains 3,000 USD."
            ),
            subject="project:generic",
        )
        by_scope = {
            str(claim["scope"]): str(claim["value"])
            for claim in claims
        }

        self.assertEqual(by_scope["estimate.budget_total"], "2300")
        self.assertEqual(by_scope["constraint.budget_upper_bound"], "3000")

    def test_budget_revision_promotes_target_not_historical_source(self) -> None:
        claims = extract_claim_cards(
            "Revise the budget cap from 2,600 USD to 3,200 USD.",
            subject="project:generic",
        )
        upper_bounds = [
            claim
            for claim in claims
            if claim["scope"] == "constraint.budget_upper_bound"
        ]

        self.assertEqual(len(upper_bounds), 1)
        self.assertEqual(upper_bounds[0]["value"], "3200")

    def test_missing_required_artifact_enforces_explicit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = (
                "Classify the release using policy_rules.md. "
                "If the evidence is unavailable, return needs_more_evidence."
            )
            assessment = _required_evidence_assessment(
                current_task=task,
                evidence_context="",
                output_text="Final decision: approved_release.",
                target_cwd=root,
            )

            self.assertTrue(assessment["blocked"])
            self.assertEqual(
                assessment["fallback_value"],
                "needs_more_evidence",
            )
            self.assertEqual(
                assessment["conflicting_decision_values"],
                ["approved_release"],
            )

    def test_explicit_fallback_output_is_not_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assessment = _required_evidence_assessment(
                current_task=(
                    "Classify using policy_rules.md. Evidence missing must "
                    "output needs_more_evidence."
                ),
                evidence_context="",
                output_text="Final decision: needs_more_evidence.",
                target_cwd=Path(tmp),
            )

            self.assertFalse(assessment["blocked"])

    def test_supplied_artifact_content_disables_missing_evidence_guard(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = (
                "Classify using policy_rules.md. Evidence missing must "
                "output needs_more_evidence."
            )
            contract = _required_evidence_contract(
                current_task=task,
                evidence_context=(
                    "BEGIN policy_rules.md\n"
                    "release.approval_threshold=0.8\n"
                    "END policy_rules.md"
                ),
                target_cwd=Path(tmp),
            )

            self.assertFalse(contract["required"])
            self.assertEqual(
                contract["available_artifacts"],
                ["policy_rules.md"],
            )

    def test_prompt_rule_names_only_missing_artifact_and_user_fallback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rule = _required_evidence_prompt_rule(
                current_task=(
                    "Use policy_rules.md. If source evidence is missing, "
                    "return needs_more_evidence."
                ),
                evidence_context="",
                target_cwd=Path(tmp),
            )

            self.assertIn("policy_rules.md", rule)
            self.assertIn("needs_more_evidence", rule)
            self.assertNotIn("approved_release", rule)
            self.assertIn("complete evidence-insufficient deliverable", rule)
            self.assertIn("resulting uncertainty", rule)


if __name__ == "__main__":
    unittest.main()
