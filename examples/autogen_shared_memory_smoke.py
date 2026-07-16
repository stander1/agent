from __future__ import annotations

import argparse
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
    from autogen_agentchat.base import TaskResult
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.messages import TextMessage
    from autogen_agentchat.teams import RoundRobinGroupChat
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


MEMORY_FACT = (
    "confirmed preference: three-day trip, budget 3000 CNY, "
    "nature walks and local food, avoid crowded commercial attractions"
)
DONE_MARKER = "DONE_SHARED_MEMORY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("seed", "recall"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _make_input(reply: str):
    async def input_func(*_args: Any, **_kwargs: Any) -> str:
        return reply

    return input_func


def _make_team(mode: str) -> RoundRobinGroupChat:
    planner = UserProxyAgent(
        "planner",
        input_func=_make_input("planner completed the compact route"),
    )
    writer = UserProxyAgent(
        "writer",
        input_func=_make_input("writer completed the compact draft"),
    )
    final_reply = (
        f"{MEMORY_FACT}. {DONE_MARKER}"
        if mode == "seed"
        else f"reviewer completed the follow-up plan. {DONE_MARKER}"
    )
    reviewer = UserProxyAgent(
        "reviewer",
        input_func=_make_input(final_reply),
    )
    return RoundRobinGroupChat(
        [planner, writer, reviewer],
        termination_condition=TextMentionTermination(DONE_MARKER),
    )


def _task(mode: str) -> str:
    if mode == "recall":
        return "Continue the previous travel plan without restating old preferences."
    paragraph = (
        "Build a reusable travel preference record. Keep constraints, evidence, "
        "handoff state, and final reviewer decisions consistent across later tasks. "
    )
    return (paragraph * 18).strip()


def _message_payload(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", ""))
    return {
        "type": type(message).__name__,
        "source": str(getattr(message, "source", "")),
        "content": content,
        "content_chars": len(content),
        "contains_shared_memory_marker": "AGENTLITE_SHARED_MEMORY v1" in content,
        "contains_memory_fact": MEMORY_FACT in content,
        "contains_team_rewrite_marker": "AGENTLITE_TEAM_REAL_REWRITE v1" in content,
    }


async def run(mode: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    result_payload: dict[str, Any] = {}
    # AutoGen Studio passes a Sequence[ChatMessage], not a plain string.
    studio_task = [TextMessage(source="user", content=_task(mode))]
    async for item in _make_team(mode).run_stream(task=studio_task):
        if isinstance(item, TaskResult):
            result_payload = {
                "stop_reason": str(getattr(item, "stop_reason", "")),
                "messages": [
                    _message_payload(message)
                    for message in (getattr(item, "messages", []) or [])
                ],
            }
        else:
            items.append(_message_payload(item))
    return {
        "mode": mode,
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "stream_items": items,
        "task_result": result_payload,
    }


def main() -> int:
    args = parse_args()
    payload = asyncio.run(run(args.mode))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
