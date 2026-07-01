from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence


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


class MatrixEchoAgent(BaseChatAgent):
    def __init__(self, name: str = "echo") -> None:
        super().__init__(
            name=name,
            description="Echo incoming message content for fallback matrix smoke.",
        )
        self.last_seen_messages: list[dict[str, object]] = []

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
                    "content_preview": " ".join(content.split())[:240],
                    "contains_rewrite_marker": "AGENTLITE_REAL_REWRITE v1" in content,
                    "contains_fallback_marker": "FALLBACK_NATIVE_MARKER" in content,
                    "contains_short_marker": "SHORT_NATIVE_MARKER" in content,
                    "fallback_marker_count": content.count("FALLBACK_NATIVE_MARKER"),
                }
            )
        self.last_seen_messages = seen
        response_text = json.dumps(
            {
                "seen_count": len(seen),
                "first_type": seen[0]["type"] if seen else "",
                "rewrite_seen": any(item["contains_rewrite_marker"] for item in seen),
                "fallback_marker_seen": any(
                    item["contains_fallback_marker"] for item in seen
                ),
                "short_marker_seen": any(item["contains_short_marker"] for item in seen),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return Response(chat_message=TextMessage(content=response_text, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self.last_seen_messages = []


async def _run_case(
    agent: MatrixEchoAgent,
    *,
    case_id: str,
    messages: Sequence[BaseChatMessage],
) -> dict[str, Any]:
    response = None
    async for item in agent.on_messages_stream(messages, CancellationToken()):
        if isinstance(item, Response):
            response = item
    if response is None:
        raise RuntimeError(f"{case_id} did not return a Response")
    response_content = str(getattr(response.chat_message, "content", ""))
    return {
        "case_id": case_id,
        "input_message_count": len(messages),
        "seen_messages": agent.last_seen_messages,
        "response_content": response_content,
        "response_contains_rewrite_seen": '"rewrite_seen": true' in response_content,
    }


def _handoff_payload() -> str:
    paragraph = (
        "FALLBACK_NATIVE_MARKER section {idx}: HandoffMessage is intentionally "
        "unsupported by current real-rewrite. Native message must survive."
    )
    return "\n".join(paragraph.format(idx=index) for index in range(1, 10))


async def main() -> int:
    agent = MatrixEchoAgent()
    cases = []
    cases.append(await _run_case(agent, case_id="empty_messages", messages=[]))
    cases.append(
        await _run_case(
            agent,
            case_id="empty_text_payload",
            messages=[TextMessage(content="", source="user")],
        )
    )
    cases.append(
        await _run_case(
            agent,
            case_id="short_text_cost_gate",
            messages=[TextMessage(content="SHORT_NATIVE_MARKER", source="user")],
        )
    )
    cases.append(
        await _run_case(
            agent,
            case_id="unsupported_handoff_message",
            messages=[
                HandoffMessage(
                    source="router",
                    target="echo",
                    content=_handoff_payload(),
                )
            ],
        )
    )
    payload = {
        "scenario": "rewrite_fallback_matrix",
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "cases": cases,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_REWRITE_FALLBACK_MATRIX_OUTPUT")
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
