from __future__ import annotations

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
    from autogen_agentchat.teams import RoundRobinGroupChat
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


NATIVE_MARKER = "TEAM_REWRITE_NATIVE_MARKER"


def _make_input(reply: str):
    async def input_func(*_args: Any, **_kwargs: Any) -> str:
        return reply

    return input_func


def _long_task() -> str:
    paragraph = (
        "team rewrite section {idx}: this native AutoGen team task repeats "
        "planning requirements, retrieval evidence, artifact constraints, "
        "review rules, memory hints, state transfer notes, and enough neutral "
        "filler before the marker so Prompt View previews do not accidentally "
        "contain it. {marker} The Team entry rewrite should move this whole "
        "task body into the StatePool before RoundRobinGroupChat distributes it."
    )
    return "\n".join(
        paragraph.format(idx=index, marker=NATIVE_MARKER)
        for index in range(1, 22)
    )


def _message_to_dict(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", ""))
    return {
        "type": type(message).__name__,
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "content": content,
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:320],
        "contains_team_rewrite_marker": "AGENTLITE_TEAM_REAL_REWRITE v1" in content,
        "contains_native_marker": NATIVE_MARKER in content,
        "native_marker_count": content.count(NATIVE_MARKER),
        "contains_state_pool_marker": (
            "native_team_task_moved_to_state_pool=true" in content
        ),
        "contains_broadcast_manifest": "broadcast_manifest=" in content,
        "contains_receiver_prompt_views": "receiver_prompt_views:" in content,
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
    planner = UserProxyAgent(
        "planner",
        input_func=_make_input(
            "planner saw the compact team task and produced a short route."
        ),
    )
    writer = UserProxyAgent(
        "writer",
        input_func=_make_input("writer keeps the compact team task moving."),
    )
    reviewer = UserProxyAgent(
        "reviewer",
        input_func=_make_input("DONE_TEAM_REWRITE reviewer accepts compact task."),
    )
    team = RoundRobinGroupChat(
        [planner, writer, reviewer],
        termination_condition=TextMentionTermination("DONE_TEAM_REWRITE"),
    )
    stream_items: list[dict[str, Any]] = []
    task_result: dict[str, Any] = {}
    async for item in team.run_stream(task=_long_task()):
        if isinstance(item, TaskResult):
            task_result = _task_result_to_dict(item)
        else:
            stream_items.append(_message_to_dict(item))
    payload = {
        "scenario": "autogen_team_real_rewrite",
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "team_rewrite_env": os.getenv("AGENTLITE_AUTOGEN_TEAM_REWRITE", ""),
        "participant_names": ["planner", "writer", "reviewer"],
        "stream_items": stream_items,
        "task_result": task_result,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_TEAM_REWRITE_OUTPUT")
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
