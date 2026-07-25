from __future__ import annotations

import unittest

from agent_runtime.reliability.final_delivery_guard import assess_final_delivery


class FinalDeliveryGuardTests(unittest.TestCase):
    def test_rejects_reviewer_revision_shape(self) -> None:
        content = """**ReviewerAgent 审查意见（第一轮）**

**重大问题清单：**
当前产出答非所问，遗漏了用户要求的约束。

**可执行修订清单（供Planner/Writer遵循）：**
请下一轮补齐遗漏后重新提交。

**当前产出不合格。请依据上述清单进行修订。**
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请生成完整交付物",
            content=content,
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertTrue(assessment.review_only)
        self.assertIn("review_feedback_not_final_artifact", assessment.reasons)

    def test_accepts_generic_complete_final_artifact(self) -> None:
        content = """## 最终可交付方案

### 目标
给出可以直接执行的完整方案。

### 执行步骤
1. 收集输入并确认约束。
2. 执行方案并记录结果。
3. 审查结果并形成最终报告。

### 风险与验证
记录失败原因、重试次数和最终验证结果。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请生成完整交付物",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
            minimum_body_chars=80,
        )

        self.assertTrue(assessment.valid)
        self.assertEqual(assessment.missing_requirements, ())

    def test_rejects_revision_instruction_without_a_review_heading(self) -> None:
        content = """根据团队历史，当前草案尚未达到最终交付标准。
存在内容完整性不足和决策链未整合等实质问题，需 Planner 与 Writer 协同修订。
修订指令：请补齐缺失内容。只有全部内容合理呈现，审查才会通过。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请生成完整交付物",
            content=content,
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertTrue(assessment.review_only)
        self.assertIn("review_feedback_not_final_artifact", assessment.reasons)

    def test_rejects_team_delegation_disguised_as_final_delivery(self) -> None:
        content = """### 推荐结论

最适合的候选项是方案二，原因是预算和强度最匹配。

**下一步工作建议**：
请团队以方案二为基础，继续生成用户要求的完整三日计划。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="选择方案并生成完整三日计划",
            content=content,
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertTrue(assessment.review_only)
        self.assertIn("review_feedback_not_final_artifact", assessment.reasons)

    def test_rejects_unverified_direct_quote_claimed_as_user_confirmation(self) -> None:
        content = """## 最终可交付方案

根据您的最新确认（“膝盖不是很好，不能爬陡坡或走太久”），方案改为全程平路。
以下给出三天完整安排与预算。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="选择候选项并生成三天完整安排",
            grounding_contexts=("用户偏好自然风景和轻徒步。",),
            content=content,
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertIn("ungrounded_user_confirmation_claim", assessment.reasons)

    def test_accepts_direct_quote_present_in_user_history(self) -> None:
        content = """## 最终可交付方案

根据您的最新确认（“膝盖不是很好，不能爬陡坡或走太久”），方案改为全程平路。
以下给出三天完整安排与预算。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请重新规划",
            grounding_contexts=("我的膝盖不是很好，不能爬陡坡或走太久。",),
            content=content,
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertTrue(assessment.valid)

    def test_does_not_treat_pending_confirmation_as_confirmed_user_quote(self) -> None:
        content = """## 最终可交付成果

当前需求与约束已经整理完毕。请回复以确认或调整上述“默认假设”，我们将继续推进。
一旦您对上述待确认项进行反馈，我们将启动“候选目的地筛选与比较”工作。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请先帮我整理需求和约束",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertTrue(assessment.valid)

    def test_accepts_corrected_artifact_after_explicit_final_result_boundary(self) -> None:
        content = """审查发现：初稿预算上限偏高，以下已完成修正。

### 整合后的当前阶段最终成果

第一天安排城市文化体验，第二天安排低强度自然活动，第三天返程。
交通、住宿、餐饮和活动预算均已逐项列出，总额不超过用户预算，雨天替代方案也已给出。
该内容是可由用户直接执行的完整版本，不需要 Planner 或 Writer 继续补充。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="选择目的地并生成三天两晚完整行程",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
            minimum_body_chars=80,
        )

        self.assertTrue(assessment.valid)
        self.assertFalse(assessment.review_only)

    def test_marks_compact_prior_artifact_approval_as_review_only(self) -> None:
        content = """## 最终可交付成果

基于对前序 writer 产出（artifact_state:state_1）的验收，确认其符合规范，现批准并流转。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请生成完整审计报告",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertTrue(assessment.review_only)
        self.assertTrue(assessment.approved_prior_artifact)
        self.assertIn("review_feedback_not_final_artifact", assessment.reasons)

    def test_marks_mislabeled_generic_agent_acceptance_summary_as_review_only(self) -> None:
        content = """## 最终可交付成果

作为证据验收专家，我对 EvidenceAssembler42 提交的报告进行了验收检查。
检查重点为证据血缘、字段完整性和不确定性说明，所有产出均通过验收，
符合当前协作阶段要求。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="Analyze both evidence rows and deliver a report.",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertTrue(assessment.review_only)
        self.assertTrue(assessment.approved_prior_artifact)
        self.assertIn("review_feedback_not_final_artifact", assessment.reasons)

    def test_rejects_budget_total_above_explicit_upper_bound(self) -> None:
        content = """## 最终可交付方案

方案包含交通、住宿、餐饮和活动安排，可由用户直接执行。
| 项目 | 预算 |
| --- | ---: |
| 总计 | 2500 - 3800 元 |
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请生成完整方案，总预算 3000 元以内。",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertIn("numeric_upper_bound_violation", assessment.reasons)
        self.assertIn(
            "budget_upper_bound=3000;observed_total_upper=3800",
            assessment.missing_requirements,
        )

    def test_accepts_budget_total_within_explicit_upper_bound(self) -> None:
        content = """## 最终可交付方案

方案包含交通、住宿、餐饮和活动安排，可由用户直接执行。
| 项目 | 预算 |
| --- | ---: |
| 总计 | 2500 - 2900 元 |
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请生成完整方案，总预算不得超过 3000 元。",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertTrue(assessment.valid)

    def test_rejects_budget_total_above_nondelegable_upper_bound(self) -> None:
        assessment = assess_final_delivery(
            request="请生成完整执行方案，总预算不得超过 3000 元。",
            content=(
                "## 最终可交付方案\n\n"
                "方案包含目标、步骤、风险和验收方式，"
                "总预算 3800 元。\n"
                "FINAL_ANSWER_READY"
            ),
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertIn("numeric_upper_bound_violation", assessment.reasons)


if __name__ == "__main__":
    unittest.main()
