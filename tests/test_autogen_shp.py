from __future__ import annotations

import unittest

from agent_runtime.drivers.autogen_codec import DecodedAutoGenMessage
from agent_runtime.drivers.autogen_shp import (
    plan_shadow_broadcast,
    plan_shadow_handoff,
)


class AutoGenShadowHandoffTest(unittest.TestCase):
    def test_uses_handoff_target_when_autogen_message_declares_one(self) -> None:
        plan = plan_shadow_handoff(
            sender="planner",
            target_kind="agentchat_agent",
            method_name="on_messages",
            decoded_messages=[
                DecodedAutoGenMessage(
                    native_type="HandoffMessage",
                    message_kind="handoff",
                    source="planner",
                    target="writer",
                    content_text="continue",
                )
            ],
            native_text="continue",
        )

        self.assertEqual(plan.declared_receiver, "writer")
        self.assertEqual(plan.receiver_source, "handoff_message_target")
        self.assertEqual(plan.handoff_targets, ["writer"])
        self.assertIn("handoff", plan.message_kinds)

    def test_keeps_unknown_autogen_routing_as_shadow_default(self) -> None:
        plan = plan_shadow_handoff(
            sender="RoundRobinGroupChat",
            target_kind="agentchat_team",
            method_name="run",
            decoded_messages=[
                DecodedAutoGenMessage(
                    native_type="TextMessage",
                    message_kind="text",
                    source="writer",
                    content_text="done",
                )
            ],
            native_text="done",
        )

        self.assertEqual(plan.declared_receiver, "autogen_next")
        self.assertEqual(plan.receiver_source, "agentchat_team_default")
        self.assertEqual(plan.message_sources, ["writer"])
        self.assertIn("RoundRobinGroupChat.run", plan.summary)

    def test_builds_per_receiver_broadcast_shadow_plan(self) -> None:
        plan = plan_shadow_broadcast(
            sender="RoundRobinGroupChat",
            target_kind="agentchat_team",
            method_name="run",
            decoded_messages=[
                DecodedAutoGenMessage(
                    native_type="TextMessage",
                    message_kind="text",
                    source="user",
                    content_text="long task",
                )
            ],
            native_text="long task",
            participant_names=["planner", "writer", "planner"],
        )

        self.assertEqual(plan.participant_names, ["planner", "writer"])
        self.assertEqual(len(plan.receiver_plans), 2)
        self.assertEqual(
            [item.declared_receiver for item in plan.receiver_plans],
            ["planner", "writer"],
        )
        self.assertTrue(
            all(
                item.receiver_source == "team_participant_names"
                for item in plan.receiver_plans
            )
        )
        self.assertIn("broadcast candidate to planner", plan.receiver_plans[0].summary)


if __name__ == "__main__":
    unittest.main()
