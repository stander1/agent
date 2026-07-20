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


if __name__ == "__main__":
    unittest.main()
