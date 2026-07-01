from __future__ import annotations

import importlib.util
import unittest

from agent_runtime.drivers.autogen_codec import AutoGenMessageCodec


class AutoGenMessageCodecTest(unittest.TestCase):
    def test_decodes_text_handoff_and_tool_messages_from_dicts(self) -> None:
        codec = AutoGenMessageCodec()

        text = codec.decode(
            {"type": "TextMessage", "source": "writer", "content": "hello"}
        )
        handoff = codec.decode(
            {
                "type": "HandoffMessage",
                "source": "planner",
                "target": "writer",
                "content": "please continue",
            }
        )
        tool_request = codec.decode(
            {
                "type": "ToolCallRequestEvent",
                "source": "assistant",
                "content": [
                    {
                        "id": "call_1",
                        "name": "lookup",
                        "arguments": '{"query": "agentlite"}',
                    }
                ],
            }
        )
        tool_result = codec.decode(
            {
                "type": "ToolCallExecutionEvent",
                "source": "assistant",
                "content": [
                    {
                        "call_id": "call_1",
                        "name": "lookup",
                        "content": "ok",
                        "is_error": False,
                    }
                ],
            }
        )

        self.assertEqual(text.message_kind, "text")
        self.assertEqual(text.content_text, "hello")
        self.assertEqual(handoff.message_kind, "handoff")
        self.assertEqual(handoff.target, "writer")
        self.assertEqual(tool_request.message_kind, "tool_call")
        self.assertEqual(tool_request.tool_calls[0]["name"], "lookup")
        self.assertEqual(tool_result.message_kind, "tool_result")
        self.assertEqual(tool_result.tool_results[0]["content"], "ok")

    def test_render_text_keeps_tool_payload_when_no_text_content(self) -> None:
        codec = AutoGenMessageCodec()
        decoded = codec.decode_many(
            [
                {
                    "type": "ToolCallRequestEvent",
                    "source": "assistant",
                    "content": [
                        {
                            "id": "call_1",
                            "name": "lookup",
                            "arguments": "{}",
                        }
                    ],
                }
            ]
        )

        rendered = codec.render_text(decoded)

        self.assertIn("tool_calls", rendered)
        self.assertIn("lookup", rendered)

    @unittest.skipIf(
        importlib.util.find_spec("autogen_agentchat") is None,
        "real AutoGen package is not installed",
    )
    def test_decodes_real_autogen_messages_when_installed(self) -> None:
        from autogen_agentchat.messages import (  # type: ignore
            HandoffMessage,
            TextMessage,
            ToolCallExecutionEvent,
            ToolCallRequestEvent,
            ToolCallSummaryMessage,
        )
        from autogen_core import FunctionCall  # type: ignore
        from autogen_core.models import FunctionExecutionResult  # type: ignore

        codec = AutoGenMessageCodec()
        call = FunctionCall(id="call_1", name="lookup", arguments="{}")
        result = FunctionExecutionResult(
            call_id="call_1", name="lookup", content="ok", is_error=False
        )
        messages = [
            TextMessage(source="writer", content="hello"),
            HandoffMessage(source="planner", target="writer", content="handoff"),
            ToolCallRequestEvent(source="assistant", content=[call]),
            ToolCallExecutionEvent(source="assistant", content=[result]),
            ToolCallSummaryMessage(
                source="assistant",
                content="lookup ok",
                tool_calls=[call],
                results=[result],
            ),
        ]

        decoded = [codec.decode(message) for message in messages]

        self.assertEqual(
            [item.message_kind for item in decoded],
            ["text", "handoff", "tool_call", "tool_result", "tool_summary"],
        )
        self.assertEqual(decoded[1].target, "writer")
        self.assertEqual(decoded[2].tool_calls[0]["name"], "lookup")
        self.assertEqual(decoded[3].tool_results[0]["content"], "ok")
        self.assertEqual(decoded[4].tool_results[0]["call_id"], "call_1")


if __name__ == "__main__":
    unittest.main()
