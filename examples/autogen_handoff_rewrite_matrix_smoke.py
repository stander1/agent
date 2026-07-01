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
    from autogen_core.models import AssistantMessage, UserMessage
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


NATIVE_MARKERS = (
    "HANDOFF_MATRIX_OFF_MARKER",
    "HANDOFF_MATRIX_LONG_MARKER",
    "HANDOFF_MATRIX_SHORT_MARKER",
    "HANDOFF_MATRIX_MULTI_A_MARKER",
    "HANDOFF_MATRIX_MULTI_B_MARKER",
    "HANDOFF_MATRIX_CONTEXT_MARKER",
)


class MatrixEchoAgent(BaseChatAgent):
    def __init__(self, name: str = "matrix") -> None:
        super().__init__(
            name=name,
            description="Record HandoffMessage rewrite matrix inputs.",
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
                        "rewrite_seen": any(
                            item["contains_handoff_candidate_marker"] for item in seen
                        ),
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
    return {
        "type": type(message).__name__,
        "id": getattr(message, "id", ""),
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "metadata": metadata,
        "context_count": len(context),
        "content": content,
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:260],
        "contains_handoff_candidate_marker": (
            "AGENTLITE_HANDOFF_TYPED_REWRITE_CANDIDATE v1" in content
        ),
        "contains_any_native_marker": any(count > 0 for count in marker_counts.values()),
        "native_marker_count": sum(marker_counts.values()),
        "marker_counts": marker_counts,
    }


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
    return {
        "case_id": case_id,
        "input_message_count": len(messages),
        "seen_messages": agent.last_seen_messages,
        "response_content": str(getattr(response.chat_message, "content", "")),
    }


def _long_text(prefix: str, *, sections: int, marker: str) -> str:
    paragraph = (
        "{marker} {prefix} section {idx}: this handoff payload contains repeated "
        "planning evidence, route rationale, state notes, and downstream writing "
        "requirements. AgentLite may move this body into the StatePool only when "
        "the native AutoGen HandoffMessage control fields remain unchanged."
    )
    return "\n".join(
        paragraph.format(marker=marker, prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


def _short_text() -> str:
    return "HANDOFF_MATRIX_SHORT_MARKER tiny handoff."


def _context_messages() -> list[UserMessage | AssistantMessage]:
    return [
        UserMessage(
            content="Context note: reviewer needs the concise design route.",
            source="planner",
        ),
        AssistantMessage(
            content="Context answer: preserve this context list during rewrite.",
            source="router",
        ),
    ]


async def _run_off_scenario(agent: MatrixEchoAgent) -> list[dict[str, Any]]:
    return [
        await _run_case(
            agent,
            case_id="switch_off_long",
            messages=[
                HandoffMessage(
                    id="handoff_switch_off_1",
                    source="router",
                    target="matrix",
                    metadata={"route": "switch_off"},
                    content=_long_text(
                        "switch_off",
                        sections=14,
                        marker="HANDOFF_MATRIX_OFF_MARKER",
                    ),
                )
            ],
        )
    ]


async def _run_on_scenario(agent: MatrixEchoAgent) -> list[dict[str, Any]]:
    return [
        await _run_case(
            agent,
            case_id="single_long_enabled",
            messages=[
                HandoffMessage(
                    id="handoff_long_enabled_1",
                    source="router",
                    target="matrix",
                    metadata={"route": "single_long"},
                    content=_long_text(
                        "single_long",
                        sections=14,
                        marker="HANDOFF_MATRIX_LONG_MARKER",
                    ),
                )
            ],
        ),
        await _run_case(
            agent,
            case_id="short_text_cost_gate",
            messages=[
                HandoffMessage(
                    id="handoff_short_gate_1",
                    source="router",
                    target="matrix",
                    metadata={"route": "short_gate"},
                    content=_short_text(),
                )
            ],
        ),
        await _run_case(
            agent,
            case_id="multi_handoff_enabled",
            messages=[
                HandoffMessage(
                    id="handoff_multi_a_1",
                    source="router",
                    target="matrix",
                    metadata={"route": "multi_a"},
                    content=_long_text(
                        "multi_a",
                        sections=8,
                        marker="HANDOFF_MATRIX_MULTI_A_MARKER",
                    ),
                ),
                HandoffMessage(
                    id="handoff_multi_b_1",
                    source="router",
                    target="matrix",
                    metadata={"route": "multi_b"},
                    content=_long_text(
                        "multi_b",
                        sections=8,
                        marker="HANDOFF_MATRIX_MULTI_B_MARKER",
                    ),
                ),
            ],
        ),
        await _run_case(
            agent,
            case_id="context_handoff_enabled",
            messages=[
                HandoffMessage(
                    id="handoff_context_enabled_1",
                    source="router",
                    target="matrix",
                    metadata={"route": "context_long"},
                    content=_long_text(
                        "context_long",
                        sections=14,
                        marker="HANDOFF_MATRIX_CONTEXT_MARKER",
                    ),
                    context=_context_messages(),
                )
            ],
        ),
    ]


async def main() -> int:
    scenario = os.getenv("AGENTLITE_AUTOGEN_HANDOFF_MATRIX_SCENARIO", "on")
    agent = MatrixEchoAgent()
    if scenario == "off":
        cases = await _run_off_scenario(agent)
    elif scenario == "on":
        cases = await _run_on_scenario(agent)
    else:
        raise ValueError(f"unknown scenario: {scenario}")
    payload = {
        "scenario": scenario,
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "handoff_rewrite_env": os.getenv("AGENTLITE_AUTOGEN_HANDOFF_REWRITE", ""),
        "cases": cases,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_HANDOFF_MATRIX_OUTPUT")
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
