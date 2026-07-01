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
    from autogen_agentchat.messages import (
        BaseChatMessage,
        HandoffMessage,
        TextMessage,
        ToolCallSummaryMessage,
    )
    from autogen_core import CancellationToken, FunctionCall
    from autogen_core.models import FunctionExecutionResult
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


class GuardEchoAgent(BaseChatAgent):
    def __init__(self, name: str = "guard") -> None:
        super().__init__(
            name=name,
            description="Record incoming Handoff/ToolCall messages for rewrite guard smoke.",
        )
        self.last_seen_messages: list[dict[str, Any]] = []

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self,
        messages: Sequence[BaseChatMessage],
        cancellation_token: CancellationToken,
    ) -> Response:
        seen = [_message_summary(message) for message in messages]
        self.last_seen_messages = seen
        response_text = json.dumps(
            {
                "seen_count": len(seen),
                "message_types": [item["type"] for item in seen],
                "rewrite_seen": any(item["contains_rewrite_marker"] for item in seen),
                "handoff_target": seen[0].get("target", "") if seen else "",
                "tool_call_ids": [
                    call_id
                    for item in seen
                    for call_id in item.get("tool_call_ids", [])
                ],
                "tool_result_call_ids": [
                    call_id
                    for item in seen
                    for call_id in item.get("tool_result_call_ids", [])
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return Response(chat_message=TextMessage(content=response_text, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self.last_seen_messages = []


def _message_summary(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", ""))
    tool_calls = list(getattr(message, "tool_calls", []) or [])
    results = list(getattr(message, "results", []) or [])
    context = list(getattr(message, "context", []) or [])
    metadata = dict(getattr(message, "metadata", {}) or {})
    return {
        "type": type(message).__name__,
        "id": getattr(message, "id", ""),
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "metadata": metadata,
        "context_count": len(context),
        "content": content,
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:240],
        "contains_rewrite_marker": (
            "AGENTLITE_REAL_REWRITE v1" in content
            or "AGENTLITE_HANDOFF_TYPED_REWRITE_CANDIDATE v1" in content
        ),
        "contains_handoff_candidate_marker": (
            "AGENTLITE_HANDOFF_TYPED_REWRITE_CANDIDATE v1" in content
        ),
        "contains_handoff_marker": "HANDOFF_NATIVE_GUARD_MARKER" in content,
        "handoff_marker_count": content.count("HANDOFF_NATIVE_GUARD_MARKER"),
        "contains_tool_marker": "TOOL_NATIVE_GUARD_MARKER" in content,
        "tool_call_ids": [str(getattr(call, "id", "")) for call in tool_calls],
        "tool_call_names": [str(getattr(call, "name", "")) for call in tool_calls],
        "tool_result_call_ids": [
            str(getattr(result, "call_id", "")) for result in results
        ],
        "tool_result_names": [str(getattr(result, "name", "")) for result in results],
    }


async def _run_case(
    agent: GuardEchoAgent,
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


def _long_text(prefix: str, *, sections: int, marker: str) -> str:
    paragraph = (
        "{marker} {prefix} section {idx}: this payload is intentionally long "
        "enough to tempt rewrite, but Handoff/ToolCall control fields must be "
        "preserved before AgentLite can mutate the native AutoGen message."
    )
    return "\n".join(
        paragraph.format(marker=marker, prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


async def main() -> int:
    agent = GuardEchoAgent()
    call = FunctionCall(
        id="call_guard_1",
        name="retrieve_guard_context",
        arguments=json.dumps(
            {
                "query": "AgentLite typed rewrite guard",
                "mode": "lineage_required",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    result = FunctionExecutionResult(
        call_id="call_guard_1",
        name="retrieve_guard_context",
        content=_long_text(
            "tool_result",
            sections=12,
            marker="TOOL_NATIVE_GUARD_MARKER",
        ),
        is_error=False,
    )
    cases = [
        await _run_case(
            agent,
            case_id="handoff_target_guard",
            messages=[
                HandoffMessage(
                    id="handoff_guard_1",
                    source="router",
                    target="guard",
                    metadata={"route": "guard_candidate"},
                    content=_long_text(
                        "handoff",
                        sections=14,
                        marker="HANDOFF_NATIVE_GUARD_MARKER",
                    ),
                )
            ],
        ),
        await _run_case(
            agent,
            case_id="tool_summary_lineage_guard",
            messages=[
                ToolCallSummaryMessage(
                    source="tool_runner",
                    content=_long_text(
                        "tool_summary",
                        sections=16,
                        marker="TOOL_NATIVE_GUARD_MARKER",
                    ),
                    tool_calls=[call],
                    results=[result],
                )
            ],
        ),
    ]
    payload = {
        "scenario": "handoff_tool_rewrite_guard",
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "cases": cases,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_HANDOFF_TOOL_REWRITE_GUARD_OUTPUT")
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
