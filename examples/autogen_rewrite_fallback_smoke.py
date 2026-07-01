from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Sequence


def _missing_dependency(exc: ImportError) -> int:
    print(
        "AutoGen smoke dependencies are not installed. "
        "Install with: python -m pip install -e \".[autogen]\"",
        file=sys.stderr,
    )
    print(f"ImportError: {exc}", file=sys.stderr)
    return 2


try:
    from autogen_agentchat.agents import BaseChatAgent
    from autogen_agentchat.base import Response
    from autogen_agentchat.messages import BaseChatMessage, HandoffMessage, TextMessage
    from autogen_core import CancellationToken
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


class EchoAgent(BaseChatAgent):
    def __init__(self, name: str = "echo") -> None:
        super().__init__(
            name=name,
            description="Echo incoming message content for rewrite fallback smoke.",
        )
        self.seen_messages: list[dict[str, object]] = []

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        seen = []
        for message in messages:
            content = str(getattr(message, "content", ""))
            seen.append(
                {
                    "type": type(message).__name__,
                    "source": getattr(message, "source", ""),
                    "target": getattr(message, "target", ""),
                    "content": content,
                    "content_chars": len(content),
                    "content_preview": " ".join(content.split())[:320],
                    "contains_rewrite_marker": "AGENTLITE_REAL_REWRITE v1" in content,
                    "contains_native_marker": "FALLBACK_NATIVE_MARKER" in content,
                    "native_marker_count": content.count("FALLBACK_NATIVE_MARKER"),
                }
            )
        self.seen_messages = seen
        response_text = json.dumps(
            {
                "seen_count": len(seen),
                "first_type": seen[0]["type"] if seen else "",
                "rewrite_seen": any(item["contains_rewrite_marker"] for item in seen),
                "native_marker_seen": any(item["contains_native_marker"] for item in seen),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return Response(chat_message=TextMessage(content=response_text, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self.seen_messages = []


def _handoff_payload() -> str:
    paragraph = (
        "FALLBACK_NATIVE_MARKER section {idx}: This HandoffMessage intentionally "
        "uses a currently unsupported real-rewrite message type. AgentLite should "
        "record a fallback bucket and preserve the native AutoGen message."
    )
    return "\n".join(paragraph.format(idx=index) for index in range(1, 18))


async def main() -> int:
    agent = EchoAgent()
    payload_text = _handoff_payload()
    response = None
    async for item in agent.on_messages_stream(
        [
            HandoffMessage(
                source="router",
                target="echo",
                content=payload_text,
            )
        ],
        CancellationToken(),
    ):
        if isinstance(item, Response):
            response = item
    if response is None:
        raise RuntimeError("EchoAgent stream did not return a Response")
    response_content = str(getattr(response.chat_message, "content", ""))
    payload = {
        "scenario": "rewrite_fallback_handoff_message",
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "payload_chars": len(payload_text),
        "seen_messages": agent.seen_messages,
        "response_content": response_content,
        "response_contains_rewrite_seen": '"rewrite_seen": true' in response_content,
        "response_contains_native_marker_seen": '"native_marker_seen": true'
        in response_content,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_REWRITE_FALLBACK_OUTPUT")
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
