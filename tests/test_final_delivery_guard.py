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


if __name__ == "__main__":
    unittest.main()
