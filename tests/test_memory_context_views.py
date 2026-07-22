from __future__ import annotations

import unittest

from agent_runtime.memory.context_views import (
    build_minimal_role_view,
    field_fetch_query,
    infer_collaboration_role,
)


class MemoryContextViewsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.views = [
            "[memory_view:view_1] slot=slot.project.requirement; claim=claim_1; "
            "用户确认预算为3000元，并偏好公共交通；候选方案包括甲、乙、丙；"
            "已决定优先比较交通便利性和执行风险；详细历史讨论无需重复传递； "
            "tags=[scope:test]"
        ]

    def test_role_inference_uses_generic_names_and_descriptions(self) -> None:
        self.assertEqual(infer_collaboration_role("route_planner"), "planner")
        self.assertEqual(infer_collaboration_role("quality_auditor"), "reviewer")
        self.assertEqual(infer_collaboration_role("unknown_agent"), "general")

    def test_planner_view_keeps_requested_options_preferences_and_constraints(self) -> None:
        result = build_minimal_role_view(
            query="请基于上一轮候选项和已确认预算选择方案",
            prompt_views=self.views,
            role="planner",
            budget_chars=260,
        )
        self.assertIn("候选方案包括甲、乙、丙", result.text)
        self.assertIn("预算为3000元", result.text)
        self.assertEqual(result.missing_fields, ())
        self.assertNotIn("详细历史讨论无需重复传递", result.text)
        self.assertLess(result.selected_chars, result.source_chars + 100)

    def test_reviewer_view_keeps_decision_and_risk_related_evidence(self) -> None:
        result = build_minimal_role_view(
            query="审查已确认的选择结论和执行风险",
            prompt_views=self.views,
            role="reviewer",
            budget_chars=260,
        )
        self.assertIn("已决定优先比较交通便利性和执行风险", result.text)
        self.assertIn("decisions", result.covered_fields)
        self.assertIn("risks", result.covered_fields)

    def test_missing_requested_field_produces_bounded_fetch_query(self) -> None:
        result = build_minimal_role_view(
            query="请依据证据来源复核结论",
            prompt_views=self.views,
            role="reviewer",
        )
        self.assertIn("evidence", result.missing_fields)
        query = field_fetch_query("复核结论", result.missing_fields)
        self.assertIn("证据", query)
        self.assertIn("来源", query)

    def test_current_selection_action_does_not_request_prior_decision(self) -> None:
        result = build_minimal_role_view(
            query="请从本文给出的三个方案中选择一个",
            prompt_views=self.views,
            role="planner",
        )
        self.assertNotIn("decisions", result.requested_fields)

    def test_confirmed_constraint_does_not_imply_prior_decision(self) -> None:
        result = build_minimal_role_view(
            query="请沿用已确认预算和前序候选项继续完成方案",
            prompt_views=self.views,
            role="planner",
        )
        self.assertIn("constraints", result.requested_fields)
        self.assertIn("candidates", result.requested_fields)
        self.assertNotIn("decisions", result.requested_fields)


if __name__ == "__main__":
    unittest.main()
