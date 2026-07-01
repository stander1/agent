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
    from autogen_agentchat.messages import TextMessage
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_core import CancellationToken
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


def _make_input(reply: str):
    async def input_func(*_args: Any, **_kwargs: Any) -> str:
        return reply

    return input_func


def _message_to_dict(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", repr(message)))
    return {
        "type": type(message).__name__,
        "source": getattr(message, "source", ""),
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


def _long_reply(prefix: str, *, sections: int, done: bool = False) -> str:
    header = "DONE_LONG\n" if done else ""
    paragraph = (
        "{prefix} section {idx}: AgentLite 的跨框架协作运行时需要把长正文转成"
        "状态池中的 artifact_state，只在交接消息里传递 state_ref。接收方通过"
        " Prompt View 读取摘要、sha256、artifact_id 和访问策略，而不是反复广播"
        "完整正文。这个段落模拟真实多 Agent 协作中会不断增长的方案分析、工程"
        "约束、验证证据和交付物描述，用于观察原生文本传输与 SHP 影子包之间的"
        " token 差距。"
    )
    return header + "\n".join(
        paragraph.format(prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


async def main() -> int:
    planner_reply = _long_reply("planner", sections=28)
    writer_reply = _long_reply("writer", sections=36, done=True)
    task_text = _long_reply("user_task", sections=12)

    planner = UserProxyAgent("planner", input_func=_make_input(planner_reply))
    writer = UserProxyAgent("writer", input_func=_make_input(writer_reply))

    direct_result = await planner.on_messages(
        [
            TextMessage(
                content=task_text,
                source="user",
            )
        ],
        CancellationToken(),
    )

    team = RoundRobinGroupChat(
        [planner, writer],
        termination_condition=TextMentionTermination("DONE_LONG"),
    )
    team_result = await team.run(task=task_text)

    payload = {
        "scenario": "long_context_shadow_handoff",
        "direct_agent_result": _message_to_dict(direct_result.chat_message),
        "team_result": _result_to_dict(team_result),
        "input_chars": {
            "task_text": len(task_text),
            "planner_reply": len(planner_reply),
            "writer_reply": len(writer_reply),
        },
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_LONG_SMOKE_OUTPUT")
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
