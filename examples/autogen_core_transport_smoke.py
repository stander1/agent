from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
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
    from autogen_core import (  # type: ignore
        AgentId,
        DefaultTopicId,
        MessageContext,
        RoutedAgent,
        SingleThreadedAgentRuntime,
        message_handler,
        type_subscription,
    )
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


RECEIVED: list[dict[str, Any]] = []


@dataclass
class CorePayload:
    source: str
    content: str
    payload_kind: str = "core_transport_state"


@dataclass
class CoreReply:
    source: str
    content: str


class DirectCoreAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("direct core transport receiver")

    @message_handler
    async def handle_payload(self, message: CorePayload, ctx: MessageContext) -> CoreReply:
        RECEIVED.append(
            {
                "agent": "direct_agent",
                "kind": "direct",
                "message_type": type(message).__name__,
                "payload_kind": message.payload_kind,
                "content_chars": len(message.content),
                "agentlite_rewrite_marker": "AGENTLITE_CORE_CONTENT_REWRITE v1"
                in message.content,
                "agentlite_prompt_view": message.content.startswith(
                    "[artifact_state:"
                ),
                "content_preview": message.content[:160],
                "sender": str(ctx.sender) if ctx.sender else "",
            }
        )
        return CoreReply(
            source="direct_agent",
            content="DIRECT_ACK " + message.content[:180],
        )


@type_subscription("core-topic")
class BroadcastCoreAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("broadcast core transport receiver")

    @message_handler
    async def handle_payload(self, message: CorePayload, ctx: MessageContext) -> None:
        RECEIVED.append(
            {
                "agent": "broadcast_agent",
                "kind": "publish",
                "message_type": type(message).__name__,
                "payload_kind": message.payload_kind,
                "content_chars": len(message.content),
                "agentlite_rewrite_marker": "AGENTLITE_CORE_CONTENT_REWRITE v1"
                in message.content,
                "agentlite_prompt_view": message.content.startswith(
                    "[artifact_state:"
                ),
                "content_preview": message.content[:160],
                "topic": str(ctx.topic_id) if ctx.topic_id else "",
                "sender": str(ctx.sender) if ctx.sender else "",
            }
        )


def _long_text(prefix: str, *, sections: int) -> str:
    paragraph = (
        "{prefix} section {idx}: 这是一段 AutoGen Core Runtime 底层通信压测文本。"
        "它模拟代码端或 Studio 后端通过 send_message / publish_message 传递的长 payload。"
        "AgentLite 不应只在 AgentChat Team 入口看到它，也应在 Core Runtime 传输层"
        "生成 StatePool state_ref、SHP shadow wire 和 receiver Prompt View。"
    )
    return "\n".join(
        paragraph.format(prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


async def main() -> int:
    runtime = SingleThreadedAgentRuntime()
    await DirectCoreAgent.register(
        runtime,
        "direct_agent",
        DirectCoreAgent,
    )
    await BroadcastCoreAgent.register(
        runtime,
        "broadcast_agent",
        BroadcastCoreAgent,
    )
    runtime.start()

    direct_payload = CorePayload(
        source="user",
        content=_long_text("direct_payload", sections=14),
    )
    publish_payload = CorePayload(
        source="system",
        content=_long_text("publish_payload", sections=16),
    )
    direct_reply = await runtime.send_message(
        direct_payload,
        AgentId("direct_agent", "default"),
    )
    await runtime.publish_message(
        publish_payload,
        DefaultTopicId("core-topic"),
    )
    await runtime.stop_when_idle()

    output = {
        "scenario": "autogen_core_transport_smoke",
        "direct_reply": {
            "type": type(direct_reply).__name__,
            "content": getattr(direct_reply, "content", ""),
        },
        "received": RECEIVED,
        "input_chars": {
            "direct_payload": len(direct_payload.content),
            "publish_payload": len(publish_payload.content),
        },
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_CORE_TRANSPORT_OUTPUT")
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
