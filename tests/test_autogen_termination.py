from __future__ import annotations

import unittest
from collections.abc import Sequence

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import BaseChatMessage, TextMessage, ThoughtEvent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core import CancellationToken

from agent_runtime.adapters.autogen_termination import (
    ReviewerFinalTextTermination,
    resolve_final_artifact,
)


MARKER = "FINAL_ANSWER_READY"


class _ScriptedAgent(BaseChatAgent):
    def __init__(self, name: str, replies: list[str]) -> None:
        super().__init__(name=name, description=name)
        self._replies = iter(replies)

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        del messages, cancellation_token
        return Response(
            chat_message=TextMessage(content=next(self._replies), source=self.name)
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        del cancellation_token


class ReviewerFinalTextTerminationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.condition = ReviewerFinalTextTermination(
            marker=MARKER,
            source="reviewer",
        )

    async def test_ignores_reasoning_event_even_when_marker_is_exact(self) -> None:
        result = await self.condition(
            [ThoughtEvent(content=MARKER, source="reviewer")]
        )

        self.assertIsNone(result)
        self.assertFalse(self.condition.terminated)

    async def test_ignores_marker_from_non_reviewer_agent(self) -> None:
        result = await self.condition(
            [TextMessage(content=f"最终答案\n{MARKER}", source="writer")]
        )

        self.assertIsNone(result)
        self.assertFalse(self.condition.terminated)

    async def test_ignores_review_feedback_that_only_mentions_marker(self) -> None:
        result = await self.condition(
            [
                TextMessage(
                    content=f"修订后再提交，不要把 {MARKER} 当作正文。\n请继续修订。",
                    source="reviewer",
                )
            ]
        )

        self.assertIsNone(result)
        self.assertFalse(self.condition.terminated)

    async def test_stops_on_exact_last_line_of_reviewer_visible_text(self) -> None:
        result = await self.condition(
            [TextMessage(content=f"这是完整最终答案。\n\n{MARKER}\n", source="reviewer")]
        )

        self.assertIsNotNone(result)
        self.assertTrue(self.condition.terminated)

    async def test_rejects_review_only_text_even_with_exact_marker(self) -> None:
        result = await self.condition(
            [
                TextMessage(
                    content=(
                        "审查意见：当前草案没有交付预算表，请 Writer 继续补充后再提交。\n"
                        f"{MARKER}"
                    ),
                    source="reviewer",
                )
            ]
        )

        self.assertIsNone(result)
        self.assertFalse(self.condition.terminated)
        self.assertIsNotNone(self.condition.last_assessment)
        self.assertIn(
            "review_feedback_not_final_artifact",
            self.condition.last_assessment.reasons,
        )

    async def test_rejects_ungrounded_user_confirmation_and_keeps_running(self) -> None:
        await self.condition(
            [TextMessage(content="请从候选项中选一个并生成三天行程。", source="user")]
        )

        result = await self.condition(
            [
                TextMessage(
                    content=(
                        "## 最终可交付方案\n"
                        "根据您的最新确认（“膝盖不是很好，不能爬陡坡或走太久”），"
                        "现给出完整三天计划。\n"
                        f"{MARKER}"
                    ),
                    source="reviewer",
                )
            ]
        )

        self.assertIsNone(result)
        self.assertFalse(self.condition.terminated)
        self.assertIn(
            "ungrounded_user_confirmation_claim",
            self.condition.last_assessment.reasons,
        )

    async def test_rejects_revieweragent_revision_report_with_marker(self) -> None:
        result = await self.condition(
            [
                TextMessage(
                    content=(
                        "**ReviewerAgent 审查意见（第一轮）**\n"
                        "**重大问题清单：** 当前产出答非所问。\n"
                        "**可执行修订清单（供Planner/Writer遵循）：**\n"
                        "请下一轮产出完整的可执行方案。\n"
                        "**当前产出不合格。请依据上述清单进行修订。**\n"
                        f"{MARKER}"
                    ),
                    source="reviewer",
                )
            ]
        )

        self.assertIsNone(result)
        self.assertFalse(self.condition.terminated)

    async def test_accepts_review_preface_followed_by_explicit_final_artifact(self) -> None:
        result = await self.condition(
            [
                TextMessage(
                    content=(
                        "审查结论：合格。\n\n"
                        "**【最终可交付规划方案：三天两晚预算方案】**\n"
                        "第一天安排自然体验，第二天安排轻徒步，第三天返程；"
                        "交通、住宿和餐饮预算均已列明，可由用户直接执行。\n"
                        f"{MARKER}"
                    ),
                    source="reviewer",
                )
            ]
        )

        self.assertIsNotNone(result)
        self.assertTrue(self.condition.terminated)

    async def test_repairs_missing_marker_on_complete_reviewer_artifact(self) -> None:
        result = await self.condition(
            [
                TextMessage(
                    content=(
                        "## 最终可交付方案\n\n"
                        "以下是可由用户直接执行的完整方案。第一部分给出目标与约束，"
                        "第二部分列出逐步安排、责任人和时间窗口，第三部分给出预算、"
                        "风险及替代路径，并对所有关键结论提供验证方法。"
                    ),
                    source="reviewer",
                )
            ]
        )

        self.assertIsNotNone(result)
        self.assertTrue(self.condition.terminated)
        self.assertEqual(
            self.condition.last_resolved_artifact.resolution_kind,
            "reviewer_marker_repaired",
        )
        self.assertTrue(
            self.condition.last_resolved_artifact.content.rstrip().endswith(MARKER)
        )

    async def test_prior_writer_artifact_is_promoted_when_reviewer_only_approves(self) -> None:
        writer_artifact = (
            "## 完整审计报告\n\n"
            "本文给出审计范围、证据清单、逐项发现、风险等级、整改责任人与"
            "验收办法。所有结论均可追溯到输入证据，正文足以直接交付给用户。"
        )
        await self.condition(
            [TextMessage(content=writer_artifact, source="writer")]
        )

        result = await self.condition(
            [
                TextMessage(
                    content=(
                        "## 最终可交付成果\n\n"
                        "基于对前序 writer 产出（artifact_state:state_1）的验收，"
                        f"确认其符合规范，现批准并流转。\n{MARKER}"
                    ),
                    source="reviewer",
                )
            ]
        )

        self.assertIsNotNone(result)
        self.assertTrue(self.condition.terminated)
        self.assertEqual(
            self.condition.last_resolved_artifact.resolution_kind,
            "prior_artifact_approved",
        )
        self.assertEqual(
            self.condition.last_resolved_artifact.origin_source,
            "writer",
        )
        self.assertIn("完整审计报告", self.condition.last_resolved_artifact.content)

    async def test_rejects_numeric_upper_bound_violation(self) -> None:
        await self.condition(
            [TextMessage(content="请生成完整方案，总预算 3000 元以内。", source="user")]
        )

        result = await self.condition(
            [
                TextMessage(
                    content=(
                        "## 最终可交付方案\n\n"
                        "方案包含完整安排、风险与替代路径，可由用户直接执行。\n"
                        "| 项目 | 预算 |\n"
                        "| --- | ---: |\n"
                        f"| 总计 | 2500 - 3800 元 |\n{MARKER}"
                    ),
                    source="reviewer",
                )
            ]
        )

        self.assertIsNone(result)
        self.assertFalse(self.condition.terminated)
        self.assertIn(
            "numeric_upper_bound_violation",
            self.condition.last_assessment.reasons,
        )

    async def test_pure_resolver_promotes_approved_prior_artifact(self) -> None:
        resolved = resolve_final_artifact(
            [
                TextMessage(
                    content=(
                        "## 完整实施报告\n\n"
                        "报告包含范围、方法、结果、风险、责任人与验收标准，"
                        "信息完整且能够直接交付给用户执行。"
                    ),
                    source="specialist",
                ),
                TextMessage(
                    content=(
                        "确认前序 specialist 的成果符合要求，批准作为最终交付物。\n"
                        f"{MARKER}"
                    ),
                    source="reviewer",
                ),
            ],
            marker=MARKER,
            reviewer_source="reviewer",
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.origin_source, "specialist")
        self.assertEqual(resolved.resolution_kind, "prior_artifact_approved")

    async def test_rejects_review_summary_mislabeled_as_delivery_highlights(self) -> None:
        result = await self.condition(
            [
                TextMessage(
                    content=(
                        "审查结论：合格。\n"
                        "**最终交付物核心要点：**预算已控制，住宿标准已保留。\n"
                        "建议用户下一步依据 Writer 原文执行。\n"
                        f"{MARKER}"
                    ),
                    source="reviewer",
                )
            ]
        )

        self.assertIsNone(result)
        self.assertFalse(self.condition.terminated)

    async def test_component_round_trip_preserves_marker_and_source(self) -> None:
        loaded = ReviewerFinalTextTermination.load_component(
            self.condition.dump_component()
        )

        result = await loaded(
            [TextMessage(content=f"完整最终答案\n{MARKER}", source="reviewer")]
        )
        self.assertIsNotNone(result)

    async def test_round_robin_continues_after_review_feedback(self) -> None:
        team = RoundRobinGroupChat(
            [
                _ScriptedAgent("planner", ["初版规划", "修订规划"]),
                _ScriptedAgent("writer", ["不完整草案", "完整修订答案"]),
                _ScriptedAgent(
                    "reviewer",
                    [
                        f"审查不通过。正文提到了 {MARKER}，但最后一行是修订要求。\n请继续修订。",
                        f"这是完整最终答案。\n{MARKER}",
                    ],
                ),
            ],
            termination_condition=ReviewerFinalTextTermination(
                marker=MARKER,
                source="reviewer",
            ),
            max_turns=6,
        )

        result = await team.run(task="测试任务")
        agent_sources = [
            message.source
            for message in result.messages
            if message.source in {"planner", "writer", "reviewer"}
        ]

        self.assertEqual(
            agent_sources,
            ["planner", "writer", "reviewer", "planner", "writer", "reviewer"],
        )
        self.assertIn("validated final artifact", result.stop_reason or "")


if __name__ == "__main__":
    unittest.main()
