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
    from autogen_agentchat.messages import BaseChatMessage, TextMessage
    from autogen_agentchat.messages import ToolCallSummaryMessage
    from autogen_core import CancellationToken, FunctionCall
    from autogen_core.models import FunctionExecutionResult
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


class ToolSummaryRewriteSwitchAgent(BaseChatAgent):
    def __init__(self, name: str = "tool_summary_switch") -> None:
        super().__init__(
            name=name,
            description="Record ToolCallSummaryMessage content rewrite switch inputs.",
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
        return Response(
            chat_message=TextMessage(
                content=json.dumps(
                    {
                        "seen_count": len(seen),
                        "rewrite_seen": any(
                            item["contains_tool_summary_candidate_marker"]
                            for item in seen
                        ),
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
                ),
                source=self.name,
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self.last_seen_messages = []


def _message_summary(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", ""))
    tool_calls = list(getattr(message, "tool_calls", []) or [])
    results = list(getattr(message, "results", []) or [])
    metadata = dict(getattr(message, "metadata", {}) or {})
    return {
        "type": type(message).__name__,
        "id": getattr(message, "id", ""),
        "source": getattr(message, "source", ""),
        "metadata": metadata,
        "content": content,
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:260],
        "contains_native_tool_marker": "TOOL_SUMMARY_SWITCH_NATIVE_MARKER" in content,
        "native_tool_marker_count": content.count("TOOL_SUMMARY_SWITCH_NATIVE_MARKER"),
        "contains_tool_summary_candidate_marker": (
            "AGENTLITE_TOOL_SUMMARY_TYPED_REWRITE_CANDIDATE v1" in content
        ),
        "contains_state_pool_marker": (
            "native_tool_summary_content_moved_to_state_pool=true" in content
        ),
        "contains_prompt_view": "prompt_view:" in content,
        "tool_call_ids": [str(getattr(call, "id", "")) for call in tool_calls],
        "tool_call_names": [str(getattr(call, "name", "")) for call in tool_calls],
        "tool_result_call_ids": [
            str(getattr(result, "call_id", "")) for result in results
        ],
        "tool_result_names": [str(getattr(result, "name", "")) for result in results],
        "tool_result_error_flags": [
            f"{str(getattr(result, 'call_id', ''))}:{str(getattr(result, 'is_error', None))}"
            for result in results
        ],
    }


def _long_text(prefix: str, *, sections: int) -> str:
    paragraph = (
        "{prefix} section {idx}: the native tool summary repeats retrieval "
        "evidence, artifact digest fields, stdout notes, stderr notes, reviewer "
        "hints, cache hints, schema notes, route decisions, and enough filler "
        "before the probe marker to keep Prompt View previews from matching it. "
        "TOOL_SUMMARY_SWITCH_NATIVE_MARKER The rewrite switch may replace only "
        "this content body after all ToolCallSummary semantic gates pass."
    )
    return "\n".join(
        paragraph.format(prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


async def main() -> int:
    agent = ToolSummaryRewriteSwitchAgent()
    call = FunctionCall(
        id="call_tool_switch_1",
        name="retrieve_switch_context",
        arguments=json.dumps(
            {
                "query": "AgentLite ToolCallSummary real rewrite switch",
                "mode": "content_only_rewrite",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    result = FunctionExecutionResult(
        call_id="call_tool_switch_1",
        name="retrieve_switch_context",
        content=_long_text("tool_result", sections=10),
        is_error=False,
    )
    message = ToolCallSummaryMessage(
        id="tool_summary_switch_1",
        source="tool_runner",
        metadata={"tool_route": "switch"},
        content=_long_text("tool_summary", sections=18),
        tool_calls=[call],
        results=[result],
    )
    response = None
    async for item in agent.on_messages_stream([message], CancellationToken()):
        if isinstance(item, Response):
            response = item
    if response is None:
        raise RuntimeError("tool_summary_switch did not return a Response")
    payload = {
        "scenario": "tool_summary_rewrite_switch",
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "tool_summary_rewrite_env": os.getenv(
            "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE",
            "",
        ),
        "case": {
            "case_id": "tool_summary_switch",
            "seen_messages": agent.last_seen_messages,
            "response_content": str(getattr(response.chat_message, "content", "")),
        },
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE_SWITCH_OUTPUT")
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
