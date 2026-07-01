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
    from autogen_agentchat.messages import TextMessage
    from autogen_agentchat.teams import RoundRobinGroupChat
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


MARKERS = {
    "switch_off_long": "TEAM_MATRIX_OFF_NATIVE_MARKER",
    "long_stream": "TEAM_MATRIX_LONG_NATIVE_MARKER",
    "short_stream": "TEAM_MATRIX_SHORT_NATIVE_MARKER",
    "message_task": "TEAM_MATRIX_MESSAGE_NATIVE_MARKER",
    "list_task": "TEAM_MATRIX_LIST_NATIVE_MARKER",
    "run_long_indirect": "TEAM_MATRIX_RUN_NATIVE_MARKER",
}


def _make_input(reply: str):
    async def input_func(*_args: Any, **_kwargs: Any) -> str:
        return reply

    return input_func


def _make_team() -> RoundRobinGroupChat:
    planner = UserProxyAgent(
        "planner",
        input_func=_make_input("planner keeps the matrix route compact."),
    )
    writer = UserProxyAgent(
        "writer",
        input_func=_make_input("writer observes the team matrix task."),
    )
    reviewer = UserProxyAgent(
        "reviewer",
        input_func=_make_input("DONE_TEAM_MATRIX reviewer closes the case."),
    )
    return RoundRobinGroupChat(
        [planner, writer, reviewer],
        termination_condition=TextMentionTermination("DONE_TEAM_MATRIX"),
    )


def _long_task(marker: str, *, sections: int = 18) -> str:
    paragraph = (
        "team matrix section {idx}: this native AutoGen team task carries "
        "planning requirements, retrieval evidence, artifact constraints, state "
        "transfer notes, review criteria, and repeated filler before the marker "
        "so compact Prompt View previews do not preserve it. {marker} The Team "
        "entry rewrite should move this body into StatePool when safe."
    )
    return "\n".join(
        paragraph.format(idx=index, marker=marker)
        for index in range(1, sections + 1)
    )


def _short_task(marker: str) -> str:
    return f"{marker} tiny task."


def _message_to_dict(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", ""))
    marker_counts = {marker: content.count(marker) for marker in MARKERS.values()}
    return {
        "type": type(message).__name__,
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "content": content,
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:320],
        "contains_team_rewrite_marker": "AGENTLITE_TEAM_REAL_REWRITE v1" in content,
        "contains_any_native_marker": any(count > 0 for count in marker_counts.values()),
        "native_marker_count": sum(marker_counts.values()),
        "marker_counts": marker_counts,
        "contains_state_pool_marker": (
            "native_team_task_moved_to_state_pool=true" in content
        ),
        "contains_broadcast_manifest": "broadcast_manifest=" in content,
        "contains_receiver_prompt_views": "receiver_prompt_views:" in content,
    }


def _task_result_to_dict(result: TaskResult | None) -> dict[str, Any]:
    if result is None:
        return {}
    messages = getattr(result, "messages", []) or []
    return {
        "type": type(result).__name__,
        "stop_reason": getattr(result, "stop_reason", ""),
        "message_count": len(messages),
        "messages": [_message_to_dict(message) for message in messages],
    }


async def _run_case(
    *,
    case_id: str,
    task: Any,
    method: str = "run_stream",
) -> dict[str, Any]:
    team = _make_team()
    stream_items: list[dict[str, Any]] = []
    task_result: TaskResult | None = None
    error: dict[str, str] = {}
    try:
        if method == "run":
            task_result = await team.run(task=task)
        else:
            async for item in team.run_stream(task=task):
                if isinstance(item, TaskResult):
                    task_result = item
                else:
                    stream_items.append(_message_to_dict(item))
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    return {
        "case_id": case_id,
        "method": method,
        "task_type": type(task).__name__ if task is not None else "None",
        "stream_items": stream_items,
        "task_result": _task_result_to_dict(task_result),
        "error": error,
    }


async def main() -> int:
    mode = os.getenv("AGENTLITE_AUTOGEN_TEAM_MATRIX_MODE", "on").strip() or "on"
    cases: list[dict[str, Any]] = []
    if mode == "off":
        cases.append(
            await _run_case(
                case_id="switch_off_long",
                task=_long_task(MARKERS["switch_off_long"]),
            )
        )
    else:
        cases.extend(
            [
                await _run_case(
                    case_id="long_stream",
                    task=_long_task(MARKERS["long_stream"]),
                ),
                await _run_case(
                    case_id="short_stream",
                    task=_short_task(MARKERS["short_stream"]),
                ),
                await _run_case(
                    case_id="message_task",
                    task=TextMessage(
                        content=_long_task(MARKERS["message_task"]),
                        source="user",
                    ),
                ),
                await _run_case(
                    case_id="list_task",
                    task=[
                        TextMessage(
                            content=_long_task(MARKERS["list_task"]),
                            source="user",
                        )
                    ],
                ),
                await _run_case(case_id="none_task", task=None),
                await _run_case(
                    case_id="run_long_indirect",
                    task=_long_task(MARKERS["run_long_indirect"]),
                    method="run",
                ),
            ]
        )
    payload = {
        "scenario": "autogen_team_rewrite_matrix",
        "mode": mode,
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "team_rewrite_env": os.getenv("AGENTLITE_AUTOGEN_TEAM_REWRITE", ""),
        "participant_names": ["planner", "writer", "reviewer"],
        "cases": cases,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_TEAM_REWRITE_MATRIX_OUTPUT")
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
