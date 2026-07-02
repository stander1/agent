from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


def _missing_dependency(exc: ImportError) -> int:
    print(
        "AutoGen mixed smoke dependencies are not installed. "
        "Install with: python -m pip install -e \".[autogen]\"",
        file=sys.stderr,
    )
    print(f"ImportError: {exc}", file=sys.stderr)
    return 2


try:
    from autogen_agentchat.agents import BaseChatAgent
    from autogen_agentchat.base import Response, TaskResult
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.messages import BaseChatMessage, TextMessage
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_core import (  # type: ignore
        AgentId,
        CancellationToken,
        MessageContext,
        RoutedAgent,
        SingleThreadedAgentRuntime,
        message_handler,
    )
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


TEAM_NATIVE_MARKER = "MIXED_TEAM_NATIVE_MARKER"
CORE_REQUEST_NATIVE_MARKER = "MIXED_CORE_REQUEST_NATIVE_MARKER"
CORE_REPLY_NATIVE_MARKER = "MIXED_CORE_REPLY_NATIVE_MARKER"
DONE_TOKEN = "DONE_MIXED"

CORE_RECEIVED: list[dict[str, Any]] = []
CORE_CALLER_REPLIES: list[dict[str, Any]] = []


@dataclass
class MixedCoreRequest:
    source: str
    content: str
    payload_kind: str = "mixed_core_request_state"


@dataclass
class MixedCoreReply:
    source: str
    content: str
    payload_kind: str = "mixed_core_reply_state"


def _long_text(prefix: str, *, sections: int, marker: str) -> str:
    paragraph = (
        "{prefix} section {idx}: this mixed AutoGen smoke payload carries "
        "planning notes, retrieval hints, artifact metadata, schema constraints, "
        "review criteria, and enough neutral filler before the marker so compact "
        "Prompt View previews do not accidentally expose it. {marker} AgentLite "
        "should move this long collaboration body through StatePool whenever it "
        "is an internal agent communication."
    )
    return "\n".join(
        paragraph.format(prefix=prefix, idx=index, marker=marker)
        for index in range(1, sections + 1)
    )


def _team_task() -> str:
    return _long_text(
        "mixed team task",
        sections=24,
        marker=TEAM_NATIVE_MARKER,
    )


def _core_request_text() -> str:
    return _long_text(
        "mixed core request",
        sections=16,
        marker=CORE_REQUEST_NATIVE_MARKER,
    )


def _core_reply_text() -> str:
    return _long_text(
        "mixed core reply",
        sections=18,
        marker=CORE_REPLY_NATIVE_MARKER,
    )


class MixedCoreCaller(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("mixed registered core caller")


class MixedCoreWorker(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("mixed core worker")

    @message_handler
    async def handle_payload(
        self,
        message: MixedCoreRequest,
        ctx: MessageContext,
    ) -> MixedCoreReply:
        content = message.content
        CORE_RECEIVED.append(
            {
                "message_type": type(message).__name__,
                "payload_kind": message.payload_kind,
                "content_chars": len(content),
                "contains_core_request_native_marker": CORE_REQUEST_NATIVE_MARKER
                in content,
                "contains_core_rewrite_marker": "AGENTLITE_CORE_CONTENT_REWRITE v1"
                in content,
                "agentlite_prompt_view": content.startswith("[artifact_state:"),
                "sender": str(ctx.sender) if ctx.sender else "",
            }
        )
        return MixedCoreReply(
            source="mixed_core_worker",
            content=_core_reply_text(),
        )


class MixedBridgeAgent(BaseChatAgent):
    def __init__(self, name: str = "mixed_bridge") -> None:
        super().__init__(
            name=name,
            description="Bridge AutoGen AgentChat team messages into AutoGen Core.",
        )
        self.last_seen_messages: list[dict[str, Any]] = []
        self.last_core_reply: dict[str, Any] = {}

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        self.last_seen_messages = [_message_to_dict(message) for message in messages]

        runtime = SingleThreadedAgentRuntime()
        await MixedCoreCaller.register(runtime, "mixed_core_caller", MixedCoreCaller)
        await MixedCoreWorker.register(runtime, "mixed_core_worker", MixedCoreWorker)
        runtime.start()
        reply = await runtime.send_message(
            MixedCoreRequest(
                source=self.name,
                content=_core_request_text(),
            ),
            AgentId("mixed_core_worker", "default"),
            sender=AgentId("mixed_core_caller", "default"),
        )
        await runtime.stop_when_idle()

        self.last_core_reply = _core_reply_to_dict(reply)
        CORE_CALLER_REPLIES.append(self.last_core_reply)
        final_payload = {
            "status": DONE_TOKEN,
            "team_input_chars_seen": self.last_seen_messages[0]["content_chars"]
            if self.last_seen_messages
            else 0,
            "team_prompt_view_seen": bool(
                self.last_seen_messages
                and self.last_seen_messages[0]["contains_receiver_prompt_views"]
            ),
            "core_reply_prompt_view_seen": bool(
                self.last_core_reply.get("agentlite_prompt_view")
            ),
            "core_reply_type": self.last_core_reply.get("message_type", ""),
        }
        return Response(
            chat_message=TextMessage(
                content=json.dumps(
                    final_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                source=self.name,
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self.last_seen_messages = []
        self.last_core_reply = {}


def _message_to_dict(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", "") or "")
    return {
        "type": type(message).__name__,
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:320],
        "contains_team_rewrite_marker": "AGENTLITE_TEAM_REAL_REWRITE v1" in content,
        "contains_team_native_marker": TEAM_NATIVE_MARKER in content,
        "contains_state_pool_marker": (
            "native_team_task_moved_to_state_pool=true" in content
        ),
        "contains_broadcast_manifest": "broadcast_manifest=" in content,
        "contains_receiver_prompt_views": "receiver_prompt_views:" in content,
        "contains_done_token": DONE_TOKEN in content,
    }


def _core_reply_to_dict(reply: Any) -> dict[str, Any]:
    content = str(getattr(reply, "content", "") or "")
    return {
        "message_type": type(reply).__name__,
        "payload_kind": str(getattr(reply, "payload_kind", "") or ""),
        "content_chars": len(content),
        "contains_core_reply_native_marker": CORE_REPLY_NATIVE_MARKER in content,
        "contains_core_rewrite_marker": "AGENTLITE_CORE_CONTENT_REWRITE v1"
        in content,
        "agentlite_prompt_view": content.startswith("[artifact_state:"),
        "content_preview": content[:160],
    }


def _task_result_to_dict(result: TaskResult) -> dict[str, Any]:
    messages = getattr(result, "messages", []) or []
    return {
        "type": type(result).__name__,
        "stop_reason": getattr(result, "stop_reason", ""),
        "message_count": len(messages),
        "messages": [_message_to_dict(message) for message in messages],
    }


async def main() -> int:
    bridge = MixedBridgeAgent()
    team = RoundRobinGroupChat(
        [bridge],
        termination_condition=TextMentionTermination(DONE_TOKEN),
    )
    stream_items: list[dict[str, Any]] = []
    task_result: dict[str, Any] = {}
    async for item in team.run_stream(task=_team_task()):
        if isinstance(item, TaskResult):
            task_result = _task_result_to_dict(item)
        else:
            stream_items.append(_message_to_dict(item))

    payload = {
        "scenario": "autogen_mixed_team_core_smoke",
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "team_rewrite_env": os.getenv("AGENTLITE_AUTOGEN_TEAM_REWRITE", ""),
        "core_content_rewrite_env": os.getenv(
            "AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE",
            "",
        ),
        "core_receiver_hydrate_env": os.getenv(
            "AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE",
            "",
        ),
        "native_lengths": {
            "team_task_chars": len(_team_task()),
            "core_request_chars": len(_core_request_text()),
            "core_reply_chars": len(_core_reply_text()),
        },
        "bridge_seen_messages": bridge.last_seen_messages,
        "core_received": CORE_RECEIVED,
        "core_caller_replies": CORE_CALLER_REPLIES,
        "stream_items": stream_items,
        "task_result": task_result,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_MIXED_TEAM_CORE_OUTPUT")
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
