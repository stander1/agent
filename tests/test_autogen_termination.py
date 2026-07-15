from __future__ import annotations

import unittest
from collections.abc import Sequence

from autogen_agentchat.agents import BaseChatAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import BaseChatMessage, TextMessage, ThoughtEvent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_core import CancellationToken

from agent_runtime.adapters.autogen_termination import ReviewerFinalTextTermination


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
        self.assertIn("exact final answer marker", result.stop_reason or "")


if __name__ == "__main__":
    unittest.main()
