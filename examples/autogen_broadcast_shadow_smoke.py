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
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.teams import RoundRobinGroupChat
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


def _make_input(reply: str):
    async def input_func(*_args: Any, **_kwargs: Any) -> str:
        return reply

    return input_func


def _long_text(prefix: str, *, sections: int, done: bool = False) -> str:
    header = "DONE_BROADCAST\n" if done else ""
    paragraph = (
        "{prefix} section {idx}: 这一段用于模拟 AutoGen Team 中逐轮广播的长上下文。"
        "原生框架会把任务、阶段输出或工具结果放进消息流；AgentLite 的影子计划"
        "应该把正文沉入 StatePool，只在 per-receiver SHP wire 包里携带 state_ref、"
        "receiver、msg_type 和摘要。接收方再通过 Prompt View 获取角色相关的状态视图，"
        "避免 planner、writer、reviewer 反复接收同一份完整正文。"
    )
    return header + "\n".join(
        paragraph.format(prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


def _message_to_dict(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", repr(message)))
    return {
        "type": type(message).__name__,
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:240],
    }


def _result_to_dict(result: Any) -> dict[str, Any]:
    messages = getattr(result, "messages", [])
    return {
        "type": type(result).__name__,
        "stop_reason": getattr(result, "stop_reason", ""),
        "message_count": len(messages),
        "messages": [_message_to_dict(message) for message in messages],
    }


async def main() -> int:
    task_text = _long_text("team_task", sections=16)
    planner_reply = _long_text("planner_plan", sections=22)
    writer_reply = _long_text("writer_artifact", sections=28)
    reviewer_reply = _long_text("reviewer_final", sections=18, done=True)

    planner = UserProxyAgent("planner", input_func=_make_input(planner_reply))
    writer = UserProxyAgent("writer", input_func=_make_input(writer_reply))
    reviewer = UserProxyAgent("reviewer", input_func=_make_input(reviewer_reply))
    team = RoundRobinGroupChat(
        [planner, writer, reviewer],
        termination_condition=TextMentionTermination("DONE_BROADCAST"),
    )
    team_result = await team.run(task=task_text)

    payload = {
        "scenario": "broadcast_shadow_plan",
        "team_result": _result_to_dict(team_result),
        "participant_names": ["planner", "writer", "reviewer"],
        "input_chars": {
            "task_text": len(task_text),
            "planner_reply": len(planner_reply),
            "writer_reply": len(writer_reply),
            "reviewer_reply": len(reviewer_reply),
        },
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_BROADCAST_OUTPUT")
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
