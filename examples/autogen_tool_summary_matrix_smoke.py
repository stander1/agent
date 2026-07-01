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


NATIVE_MARKERS = (
    "TOOL_SUMMARY_MATRIX_ALIGNED_MARKER",
    "TOOL_SUMMARY_MATRIX_MISMATCH_MARKER",
    "TOOL_SUMMARY_MATRIX_MULTI_MARKER",
    "TOOL_SUMMARY_MATRIX_SHORT_MARKER",
    "TOOL_SUMMARY_MATRIX_ERROR_MARKER",
)


class ToolSummaryMatrixAgent(BaseChatAgent):
    def __init__(self, name: str = "tool_summary_matrix") -> None:
        super().__init__(
            name=name,
            description="Record native ToolCallSummaryMessage rewrite matrix inputs.",
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
    marker_counts = {marker: content.count(marker) for marker in NATIVE_MARKERS}
    return {
        "type": type(message).__name__,
        "id": getattr(message, "id", ""),
        "source": getattr(message, "source", ""),
        "metadata": metadata,
        "content": content,
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:260],
        "contains_native_tool_marker": any(
            count > 0 for count in marker_counts.values()
        ),
        "native_tool_marker_count": sum(marker_counts.values()),
        "marker_counts": marker_counts,
        "contains_tool_summary_candidate_marker": (
            "AGENTLITE_TOOL_SUMMARY_TYPED_REWRITE_CANDIDATE v1" in content
        ),
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
    agent: ToolSummaryMatrixAgent,
    *,
    case_id: str,
    message: ToolCallSummaryMessage,
) -> dict[str, Any]:
    response = None
    async for item in agent.on_messages_stream([message], CancellationToken()):
        if isinstance(item, Response):
            response = item
    if response is None:
        raise RuntimeError(f"{case_id} did not return a Response")
    return {
        "case_id": case_id,
        "seen_messages": agent.last_seen_messages,
        "response_content": str(getattr(response.chat_message, "content", "")),
    }


def _long_text(prefix: str, *, sections: int, marker: str) -> str:
    paragraph = (
        "{marker} {prefix} section {idx}: the tool summary includes retrieval "
        "evidence, ranked observations, stdout digest, stderr digest, artifact "
        "hashes, and follow-up notes. AgentLite may only move this long body to "
        "the StatePool if tool call lineage remains native and auditable."
    )
    return "\n".join(
        paragraph.format(marker=marker, prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


def _short_text() -> str:
    return "TOOL_SUMMARY_MATRIX_SHORT_MARKER tiny summary."


def _call(call_id: str, name: str) -> FunctionCall:
    return FunctionCall(
        id=call_id,
        name=name,
        arguments=json.dumps(
            {
                "query": call_id,
                "mode": "tool_summary_matrix",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )


def _result(
    *,
    call_id: str,
    name: str,
    marker: str,
    is_error: bool | None = False,
) -> FunctionExecutionResult:
    return FunctionExecutionResult(
        call_id=call_id,
        name=name,
        content=_long_text(f"result_{call_id}", sections=6, marker=marker),
        is_error=is_error,
    )


def _message(
    *,
    message_id: str,
    case_id: str,
    marker: str,
    content: str,
    calls: list[FunctionCall],
    results: list[FunctionExecutionResult],
) -> ToolCallSummaryMessage:
    return ToolCallSummaryMessage(
        id=message_id,
        source="tool_runner",
        metadata={"matrix_case": case_id},
        content=content,
        tool_calls=calls,
        results=results,
    )


async def main() -> int:
    agent = ToolSummaryMatrixAgent()
    cases = [
        await _run_case(
            agent,
            case_id="aligned_single",
            message=_message(
                message_id="tool_summary_matrix_aligned_1",
                case_id="aligned_single",
                marker="TOOL_SUMMARY_MATRIX_ALIGNED_MARKER",
                content=_long_text(
                    "aligned_single",
                    sections=18,
                    marker="TOOL_SUMMARY_MATRIX_ALIGNED_MARKER",
                ),
                calls=[_call("call_matrix_aligned_1", "lookup_aligned")],
                results=[
                    _result(
                        call_id="call_matrix_aligned_1",
                        name="lookup_aligned",
                        marker="TOOL_SUMMARY_MATRIX_ALIGNED_MARKER",
                        is_error=False,
                    )
                ],
            ),
        ),
        await _run_case(
            agent,
            case_id="mismatched_call_id",
            message=_message(
                message_id="tool_summary_matrix_mismatch_1",
                case_id="mismatched_call_id",
                marker="TOOL_SUMMARY_MATRIX_MISMATCH_MARKER",
                content=_long_text(
                    "mismatched_call_id",
                    sections=18,
                    marker="TOOL_SUMMARY_MATRIX_MISMATCH_MARKER",
                ),
                calls=[_call("call_matrix_expected_1", "lookup_expected")],
                results=[
                    _result(
                        call_id="call_matrix_actual_1",
                        name="lookup_expected",
                        marker="TOOL_SUMMARY_MATRIX_MISMATCH_MARKER",
                        is_error=False,
                    )
                ],
            ),
        ),
        await _run_case(
            agent,
            case_id="multi_tool_complete",
            message=_message(
                message_id="tool_summary_matrix_multi_1",
                case_id="multi_tool_complete",
                marker="TOOL_SUMMARY_MATRIX_MULTI_MARKER",
                content=_long_text(
                    "multi_tool_complete",
                    sections=20,
                    marker="TOOL_SUMMARY_MATRIX_MULTI_MARKER",
                ),
                calls=[
                    _call("call_matrix_multi_1", "lookup_one"),
                    _call("call_matrix_multi_2", "lookup_two"),
                ],
                results=[
                    _result(
                        call_id="call_matrix_multi_1",
                        name="lookup_one",
                        marker="TOOL_SUMMARY_MATRIX_MULTI_MARKER",
                        is_error=False,
                    ),
                    _result(
                        call_id="call_matrix_multi_2",
                        name="lookup_two",
                        marker="TOOL_SUMMARY_MATRIX_MULTI_MARKER",
                        is_error=False,
                    ),
                ],
            ),
        ),
        await _run_case(
            agent,
            case_id="short_cost_gate",
            message=_message(
                message_id="tool_summary_matrix_short_1",
                case_id="short_cost_gate",
                marker="TOOL_SUMMARY_MATRIX_SHORT_MARKER",
                content=_short_text(),
                calls=[_call("call_matrix_short_1", "lookup_short")],
                results=[
                    _result(
                        call_id="call_matrix_short_1",
                        name="lookup_short",
                        marker="TOOL_SUMMARY_MATRIX_SHORT_MARKER",
                        is_error=False,
                    )
                ],
            ),
        ),
        await _run_case(
            agent,
            case_id="error_result_preserved",
            message=_message(
                message_id="tool_summary_matrix_error_1",
                case_id="error_result_preserved",
                marker="TOOL_SUMMARY_MATRIX_ERROR_MARKER",
                content=_long_text(
                    "error_result_preserved",
                    sections=18,
                    marker="TOOL_SUMMARY_MATRIX_ERROR_MARKER",
                ),
                calls=[_call("call_matrix_error_1", "lookup_error")],
                results=[
                    _result(
                        call_id="call_matrix_error_1",
                        name="lookup_error",
                        marker="TOOL_SUMMARY_MATRIX_ERROR_MARKER",
                        is_error=True,
                    )
                ],
            ),
        ),
    ]
    payload = {
        "scenario": "tool_summary_rewrite_matrix",
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "cases": cases,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_TOOL_SUMMARY_MATRIX_OUTPUT")
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
