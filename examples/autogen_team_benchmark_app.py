from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


def _missing_dependency(exc: ImportError) -> int:
    print(
        "AutoGen benchmark dependencies are not installed. "
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


NATIVE_MARKER = "TEAM_BENCH_NATIVE_MARKER"
TEAM_REWRITE_MARKER = "AGENTLITE_TEAM_REAL_REWRITE v1"
DONE_MARKER = "DONE_TEAM_BENCH"
PARTICIPANTS = ("planner", "writer", "reviewer")
QUALITY_TERMS = {
    "autogen": ("autogen",),
    "team": ("team",),
    "architecture_route": ("architecture route",),
    "runtime_hooks": ("runtime hooks", "hook"),
    "state_pool": ("statepool", "state pool"),
    "prompt_view": ("prompt view",),
    "shp": ("shp",),
    "fallback": ("fallback",),
    "schema": ("schema",),
    "token": ("token",),
    "monitoring": ("metrics", "monitoring", "trace"),
    "final_acceptance": (DONE_MARKER.lower(),),
}


def _make_input(reply: str):
    async def input_func(*_args: Any, **_kwargs: Any) -> str:
        return reply

    return input_func


def _long_task() -> str:
    paragraph = (
        "team benchmark section {idx}: build a cross-framework runtime layer "
        "for AutoGen-style multi-agent collaboration. The native task carries "
        "architecture requirements, runtime hooks, message passing constraints, "
        "StatePool and Prompt View notes, SHP envelope requirements, schema "
        "guard rules, fallback behavior, metrics, token accounting, and a "
        "complete delivery expectation. Keep enough neutral text before the "
        "marker so a compact Prompt View preview should not retain it. "
        "{marker} The Team entry rewrite should move this full task body into "
        "a state payload and pass only compact references to participants."
    )
    return "\n".join(
        paragraph.format(idx=index, marker=NATIVE_MARKER)
        for index in range(1, 20)
    )


def _agent_reply(role: str) -> str:
    if role == "planner":
        return (
            "planner deliverable: architecture route uses an AgentLite launcher "
            "around AutoGen Team execution, then installs runtime hooks before "
            "the user code imports AutoGen. The route keeps user agents unchanged "
            "while message transport becomes observable through trace metrics."
        )
    if role == "writer":
        return (
            "writer deliverable: runtime hooks intercept Team task entry and "
            "per-agent message delivery. Long native task content is persisted "
            "in StatePool, downstream participants receive SHP state_ref packets "
            "plus role-specific Prompt View text, schema checks guard the compact "
            "contract, fallback keeps native AutoGen messages when rewriting is "
            "unsafe, and token metrics compare native broadcast cost with "
            "wire plus Prompt View cost."
        )
    return (
        "reviewer deliverable: complete. The final solution preserves AutoGen "
        "Team semantics, explains StatePool and Prompt View usage, names SHP, "
        "schema, fallback, metrics, monitoring, and token accounting, and keeps "
        "the user program free of runtime-specific imports. "
        f"{DONE_MARKER}"
    )


def _message_to_dict(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", ""))
    return {
        "type": type(message).__name__,
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "content": content,
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:360],
        "contains_team_rewrite_marker": TEAM_REWRITE_MARKER in content,
        "contains_native_marker": NATIVE_MARKER in content,
        "native_marker_count": content.count(NATIVE_MARKER),
        "contains_state_pool_marker": (
            "native_team_task_moved_to_state_pool=true" in content
        ),
        "contains_broadcast_manifest": "broadcast_manifest=" in content,
        "contains_receiver_prompt_views": "receiver_prompt_views:" in content,
        "contains_done_marker": DONE_MARKER in content,
    }


def _task_result_to_dict(result: TaskResult) -> dict[str, Any]:
    messages = getattr(result, "messages", []) or []
    message_dicts = [_message_to_dict(message) for message in messages]
    agent_output_text = "\n".join(
        item["content"]
        for item in message_dicts
        if str(item.get("source", "")) in set(PARTICIPANTS)
    )
    return {
        "type": type(result).__name__,
        "stop_reason": getattr(result, "stop_reason", ""),
        "message_count": len(messages),
        "messages": message_dicts,
        "agent_output_text": agent_output_text,
        "quality": _quality_score(agent_output_text),
    }


def _quality_score(text: str) -> dict[str, Any]:
    lowered = text.lower()
    flags = {
        key: any(term in lowered for term in terms)
        for key, terms in QUALITY_TERMS.items()
    }
    return {
        "score": sum(1 for value in flags.values() if value),
        "max_score": len(flags),
        "flags": flags,
        "text_chars": len(text),
    }


async def main() -> int:
    task_text = _long_task()
    planner = UserProxyAgent(
        "planner",
        input_func=_make_input(_agent_reply("planner")),
    )
    writer = UserProxyAgent(
        "writer",
        input_func=_make_input(_agent_reply("writer")),
    )
    reviewer = UserProxyAgent(
        "reviewer",
        input_func=_make_input(_agent_reply("reviewer")),
    )
    team = RoundRobinGroupChat(
        [planner, writer, reviewer],
        termination_condition=TextMentionTermination(DONE_MARKER),
    )
    stream_items: list[dict[str, Any]] = []
    task_result: dict[str, Any] = {}
    async for item in team.run_stream(task=task_text):
        if isinstance(item, TaskResult):
            task_result = _task_result_to_dict(item)
        else:
            stream_items.append(_message_to_dict(item))

    payload = {
        "scenario": "autogen_team_native_vs_managed_benchmark",
        "mode": os.getenv("AGENTLITE_AUTOGEN_TEAM_BENCHMARK_MODE", ""),
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
        "team_rewrite_env": os.getenv("AGENTLITE_AUTOGEN_TEAM_REWRITE", ""),
        "participant_names": list(PARTICIPANTS),
        "native_task": {
            "chars": len(task_text),
            "sha256": hashlib.sha256(task_text.encode("utf-8")).hexdigest(),
            "native_marker_count": task_text.count(NATIVE_MARKER),
        },
        "stream_items": stream_items,
        "task_result": task_result,
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_TEAM_BENCHMARK_OUTPUT")
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
