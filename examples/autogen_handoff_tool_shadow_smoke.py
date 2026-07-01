from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


def _missing_dependency(exc: ImportError) -> int:
    print(
        "AutoGen smoke dependencies are not installed. "
        "Install with: python -m pip install -e \".[autogen]\"",
        file=sys.stderr,
    )
    print(f"ImportError: {exc}", file=sys.stderr)
    return 2


try:
    from autogen_agentchat.agents import UserProxyAgent
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.messages import (
        HandoffMessage,
        ToolCallExecutionEvent,
        ToolCallRequestEvent,
        ToolCallSummaryMessage,
    )
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_core import DefaultTopicId, FunctionCall, SingleThreadedAgentRuntime
    from autogen_core.models import FunctionExecutionResult
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


def _make_input(reply: str):
    async def input_func(*_args: Any, **_kwargs: Any) -> str:
        return reply

    return input_func


def _long_text(prefix: str, *, sections: int, marker: str = "") -> str:
    header = f"{marker}\n" if marker else ""
    paragraph = (
        "{prefix} section {idx}: 本段用于验证 AutoGen 的真实消息类型能否被 "
        "AgentLite Driver 稳定解码。长 Handoff 和 ToolCall 结果不应该被直接"
        "复制进跨 agent 消息，而应该进入 StatePool 冷层 artifact_state；"
        "SHP wire 包只携带 state_ref、msg_type、receiver 和摘要。接收方随后"
        "通过 Prompt View 获取 artifact_id、sha256 与 raw_content=cold_audit_only "
        "等字段，从而避免长正文在多 agent 协作中反复广播。"
    )
    return header + "\n".join(
        paragraph.format(prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


def _message_summary(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", ""))
    return {
        "type": type(message).__name__,
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:240],
    }


def _result_summary(result: Any) -> dict[str, Any]:
    messages = getattr(result, "messages", [])
    return {
        "type": type(result).__name__,
        "stop_reason": getattr(result, "stop_reason", ""),
        "message_count": len(messages),
        "messages": [_message_summary(message) for message in messages],
    }


async def main() -> int:
    handoff_payload = _long_text("handoff_payload", sections=24)
    handoff_reply = _long_text(
        "handoff_reply",
        sections=20,
        marker="HANDOFF_DONE",
    )
    tool_summary_text = _long_text("tool_summary", sections=26)
    tool_result_text = _long_text("tool_result", sections=32)
    writer_reply = _long_text("writer_tool_use", sections=18, marker="TOOL_DONE")

    planner = UserProxyAgent("planner", input_func=_make_input(handoff_reply))
    handoff_team = RoundRobinGroupChat(
        [planner],
        termination_condition=TextMentionTermination("HANDOFF_DONE"),
    )
    handoff_result = await handoff_team.run(
        task=[
            HandoffMessage(
                source="router",
                target="planner",
                content=handoff_payload,
            )
        ]
    )

    call = FunctionCall(
        id="call_long_retrieval",
        name="retrieve_long_context",
        arguments=json.dumps(
            {
                "query": "AgentLite Handoff ToolCall state_ref Prompt View",
                "budget": "summary_first",
                "notes": _long_text("tool_arguments", sections=8),
            },
            ensure_ascii=False,
        ),
    )
    execution_result = FunctionExecutionResult(
        call_id="call_long_retrieval",
        name="retrieve_long_context",
        content=tool_result_text,
        is_error=False,
    )
    tool_summary = ToolCallSummaryMessage(
        source="tool_runner",
        content=tool_summary_text,
        tool_calls=[call],
        results=[execution_result],
    )
    writer = UserProxyAgent("writer", input_func=_make_input(writer_reply))
    tool_team = RoundRobinGroupChat(
        [writer],
        termination_condition=TextMentionTermination("TOOL_DONE"),
    )
    tool_result = await tool_team.run(task=[tool_summary])

    runtime = SingleThreadedAgentRuntime()
    runtime.start()
    await runtime.publish_message(
        ToolCallRequestEvent(source="tool_runner", content=[call]),
        DefaultTopicId(),
    )
    await runtime.publish_message(
        ToolCallExecutionEvent(source="tool_runner", content=[execution_result]),
        DefaultTopicId(),
    )
    await runtime.stop_when_idle()

    payload = {
        "scenario": "handoff_tool_shadow_handoff",
        "handoff_result": _result_summary(handoff_result),
        "tool_summary_result": _result_summary(tool_result),
        "input_chars": {
            "handoff_payload": len(handoff_payload),
            "handoff_reply": len(handoff_reply),
            "tool_summary_text": len(tool_summary_text),
            "tool_result_text": len(tool_result_text),
            "writer_reply": len(writer_reply),
        },
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_HANDOFF_TOOL_OUTPUT")
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
