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
    from autogen_agentchat.messages import ToolCallSummaryMessage
    from autogen_core import CancellationToken, FunctionCall
    from autogen_core.models import AssistantMessage, FunctionExecutionResult
    from autogen_core.models import UserMessage
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


NATIVE_MARKERS = (
    "INTEGRATED_TEXT_NATIVE_MARKER",
    "INTEGRATED_HANDOFF_NATIVE_MARKER",
    "INTEGRATED_TOOL_SUMMARY_NATIVE_MARKER",
)


class IntegratedRewriteAgent(BaseChatAgent):
    def __init__(self, name: str = "integrated") -> None:
        super().__init__(
            name=name,
            description="Record integrated AutoGen message rewrite inputs.",
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
                        "message_types": [item["type"] for item in seen],
                        "rewrite_markers": [
                            item["rewrite_marker_kind"] for item in seen
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
    context = list(getattr(message, "context", []) or [])
    metadata = dict(getattr(message, "metadata", {}) or {})
    marker_counts = {marker: content.count(marker) for marker in NATIVE_MARKERS}
    tool_calls = list(getattr(message, "tool_calls", []) or [])
    results = list(getattr(message, "results", []) or [])
    rewrite_marker_kind = ""
    if "AGENTLITE_REAL_REWRITE v1" in content:
        rewrite_marker_kind = "text"
    elif "AGENTLITE_HANDOFF_TYPED_REWRITE_CANDIDATE v1" in content:
        rewrite_marker_kind = "handoff"
    elif "AGENTLITE_TOOL_SUMMARY_TYPED_REWRITE_CANDIDATE v1" in content:
        rewrite_marker_kind = "tool_summary"
    return {
        "type": type(message).__name__,
        "id": getattr(message, "id", ""),
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "metadata": metadata,
        "context_count": len(context),
        "content": content,
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:320],
        "rewrite_marker_kind": rewrite_marker_kind,
        "contains_text_rewrite_marker": "AGENTLITE_REAL_REWRITE v1" in content,
        "contains_handoff_rewrite_marker": (
            "AGENTLITE_HANDOFF_TYPED_REWRITE_CANDIDATE v1" in content
        ),
        "contains_tool_summary_rewrite_marker": (
            "AGENTLITE_TOOL_SUMMARY_TYPED_REWRITE_CANDIDATE v1" in content
        ),
        "contains_state_pool_marker": (
            "native_payload_moved_to_state_pool=true" in content
            or "native_content_moved_to_state_pool=true" in content
            or "native_handoff_content_moved_to_state_pool=true" in content
            or "native_tool_summary_content_moved_to_state_pool=true" in content
        ),
        "contains_prompt_view": "prompt_view:" in content,
        "contains_any_native_marker": any(
            count > 0 for count in marker_counts.values()
        ),
        "native_marker_count": sum(marker_counts.values()),
        "marker_counts": marker_counts,
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


async def _run_case(
    agent: IntegratedRewriteAgent,
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
    return {
        "case_id": case_id,
        "input_message_count": len(messages),
        "seen_messages": agent.last_seen_messages,
        "response_content": str(getattr(response.chat_message, "content", "")),
    }


def _long_text(prefix: str, *, sections: int, marker: str) -> str:
    paragraph = (
        "{prefix} section {idx}: this native AutoGen payload repeats planning "
        "evidence, retrieval notes, schema decisions, route constraints, artifact "
        "hints, review requirements, and enough neutral filler before the probe "
        "marker so compact Prompt View previews do not accidentally include it. "
        "{marker} The AgentLite runtime may move this body into the StatePool "
        "only when the corresponding message semantics remain intact."
    )
    return "\n".join(
        paragraph.format(prefix=prefix, idx=index, marker=marker)
        for index in range(1, sections + 1)
    )


def _context_messages() -> list[UserMessage | AssistantMessage]:
    return [
        UserMessage(
            content="Context note: preserve handoff context list semantics.",
            source="planner",
        ),
        AssistantMessage(
            content="Context answer: downstream receiver still needs route metadata.",
            source="router",
        ),
    ]


async def main() -> int:
    agent = IntegratedRewriteAgent()
    call = FunctionCall(
        id="call_integrated_tool_1",
        name="retrieve_integrated_context",
        arguments=json.dumps(
            {"query": "integrated AutoGen rewrite", "mode": "smoke"},
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    result = FunctionExecutionResult(
        call_id="call_integrated_tool_1",
        name="retrieve_integrated_context",
        content=_long_text(
            "integrated tool result",
            sections=8,
            marker="INTEGRATED_TOOL_SUMMARY_NATIVE_MARKER",
        ),
        is_error=False,
    )
    cases = [
        await _run_case(
            agent,
            case_id="text_message_real_rewrite",
            messages=[
                TextMessage(
                    source="user",
                    content=_long_text(
                        "integrated text",
                        sections=20,
                        marker="INTEGRATED_TEXT_NATIVE_MARKER",
                    ),
                )
            ],
        ),
        await _run_case(
            agent,
            case_id="handoff_message_real_rewrite",
            messages=[
                HandoffMessage(
                    id="handoff_integrated_1",
                    source="router",
                    target="integrated",
                    metadata={"route": "integrated_handoff"},
                    context=_context_messages(),
                    content=_long_text(
                        "integrated handoff",
                        sections=14,
                        marker="INTEGRATED_HANDOFF_NATIVE_MARKER",
                    ),
                )
            ],
        ),
        await _run_case(
            agent,
            case_id="tool_summary_message_real_rewrite",
            messages=[
                ToolCallSummaryMessage(
                    id="tool_summary_integrated_1",
                    source="tool_runner",
                    metadata={"tool_route": "integrated_tool"},
                    content=_long_text(
                        "integrated tool summary",
                        sections=16,
                        marker="INTEGRATED_TOOL_SUMMARY_NATIVE_MARKER",
                    ),
                    tool_calls=[call],
                    results=[result],
                )
            ],
        ),
    ]
    payload = {
        "scenario": "autogen_integrated_rewrite",
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "handoff_rewrite_env": os.getenv("AGENTLITE_AUTOGEN_HANDOFF_REWRITE", ""),
        "tool_summary_rewrite_env": os.getenv(
            "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE",
            "",
        ),
        "cases": cases,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_INTEGRATED_REWRITE_OUTPUT")
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
