from __future__ import annotations

import unittest

from agent_runtime.reliability.final_delivery_guard import assess_final_delivery


class FinalDeliveryGuardTests(unittest.TestCase):
    def test_rejects_internal_memory_conflict_packet_as_final_delivery(self) -> None:
        assessment = assess_final_delivery(
            request="Deliver the final implementation report.",
            content=(
                "AGENTLITE_MEMORY_CONFLICT v1\n"
                '{"protocol":"agentlite.memory_adoption_guard.v1"}\n'
                "FINAL_ANSWER_READY"
            ),
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertIn(
            "internal_protocol_message_not_deliverable",
            assessment.reasons,
        )

    def test_rejects_runtime_safety_hold_as_final_delivery(self) -> None:
        assessment = assess_final_delivery(
            request="Deliver the final implementation report.",
            content=(
                "Runtime safety hold: the generated draft used a memory fact "
                "that could not be reconciled safely. The draft was withheld "
                "and is not a final deliverable. Revision is required.\n"
                "FINAL_ANSWER_READY"
            ),
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertIn(
            "runtime_safety_hold_not_deliverable",
            assessment.reasons,
        )

    def test_rejects_output_missing_explicit_machine_deliverable_anchors(self) -> None:
        assessment = assess_final_delivery(
            request=(
                "必须输出 evidence_chain_state，并包含 hop_path、target "
                "和 evidence_refs 字段。"
            ),
            content=(
                "## 最终可交付方案\n"
                "已完成证据复核，并给出风险说明和后续建议。\n"
                "FINAL_ANSWER_READY"
            ),
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertIn("current_task_requirements_missing", assessment.reasons)
        self.assertIn("evidence_chain_state", assessment.missing_requirements)

    def test_optional_machine_identifier_is_not_required(self) -> None:
        assessment = assess_final_delivery(
            request=(
                "必须输出 final_report。"
                "debug_trace 为可选诊断信息，不需要包含。"
            ),
            content=(
                "## Final result\n"
                "The final_report contains the complete implementation result.\n"
                "FINAL_ANSWER_READY"
            ),
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertTrue(assessment.valid)
        self.assertNotIn("debug_trace", assessment.missing_requirements)

    def test_rejects_stale_output_missing_explicit_candidate_set(self) -> None:
        request = (
            "请比较下面全部三个候选并选出推荐方案：\n"
            "1. Alpha Ridge\n"
            "2. Beta Harbor\n"
            "3. Gamma Valley"
        )
        assessment = assess_final_delivery(
            request=request,
            content=(
                "## 最终可交付方案\n"
                "需求已经整理完成，后续应先确认预算和时间，再开始比较候选。\n"
                "FINAL_ANSWER_READY"
            ),
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertIn("current_task_requirements_missing", assessment.reasons)
        self.assertIn("Alpha Ridge", assessment.missing_requirements)

        corrected = assess_final_delivery(
            request=request,
            content=(
                "## 最终可交付方案\n"
                "Alpha Ridge 交通最慢，Beta Harbor 成本最高，"
                "Gamma Valley 在成本和时长之间最均衡，因此推荐 Gamma Valley。\n"
                "FINAL_ANSWER_READY"
            ),
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )
        self.assertTrue(corrected.valid)
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

    def test_marks_previous_item_approval_as_review_only(self) -> None:
        content = """## 验收通过

批准上一份 Writer 成果作为最终交付物。
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

    def test_marks_heading_only_approval_as_review_only(self) -> None:
        assessment = assess_final_delivery(
            request="请生成完整审计报告",
            content="## 验收通过\nFINAL_ANSWER_READY",
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertTrue(assessment.review_only)
        self.assertTrue(assessment.approved_prior_artifact)

    def test_complete_artifact_with_approval_heading_is_not_reference_only(
        self,
    ) -> None:
        content = """## 验收通过

以下是可直接交付的完整报告正文。报告包含范围、方法、证据、发现、
风险等级、修复责任人和验收标准，并逐项回答用户当前要求。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请生成完整审计报告",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertTrue(assessment.valid)
        self.assertFalse(assessment.approved_prior_artifact)

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

    def test_rejects_revision_request_hidden_below_final_heading(self) -> None:
        content = """## 最终可交付成果

当前产出仅为修订说明，未提供用户要求的完整成果。
请补充缺失字段并重新生成可独立阅读的交付物。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请输出包含全部字段的完整成果。",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertTrue(assessment.review_only)
        self.assertIn("review_feedback_not_final_artifact", assessment.reasons)

    def test_long_explicit_acceptance_still_references_prior_artifact(self) -> None:
        content = (
            "## 最终可交付成果\n\n"
            "作为独立验收专家，我对 EvidenceAssembler42 提交的报告"
            "进行了验收检查。所有证据、字段与约束均通过验收，批准该报告"
            "作为最终交付物。\n"
            + ("验收记录完整，未发现阻断问题。" * 180)
            + "\nFINAL_ANSWER_READY"
        )

        assessment = assess_final_delivery(
            request="请交付完整证据报告。",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertGreater(len(content), 2600)
        self.assertFalse(assessment.valid)
        self.assertTrue(assessment.approved_prior_artifact)
        self.assertTrue(assessment.review_only)

    def test_acceptance_evaluation_is_not_the_submitted_artifact(self) -> None:
        content = """## 最终可交付成果

以下是对团队消息中 AuthorAgent 产出的验收评估与最终输出。
一、字段完整性验收评估：全部字段已通过验收。
二、证据血缘验收评估：引用关系完整。
结论：该产出符合当前要求，无需修订。
FINAL_ANSWER_READY"""

        assessment = assess_final_delivery(
            request="请输出一份可直接使用的结构化状态。",
            content=content,
            source="reviewer",
            expected_source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertTrue(assessment.approved_prior_artifact)
        self.assertTrue(assessment.review_only)

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
