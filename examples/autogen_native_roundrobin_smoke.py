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
    return {
        "type": type(message).__name__,
        "source": getattr(message, "source", ""),
        "content": getattr(message, "content", repr(message)),
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
    planner = UserProxyAgent(
        "planner",
        input_func=_make_input(
            "planner_result: split the task into architecture, runtime, and "
            "verification steps."
        ),
    )
    writer = UserProxyAgent(
        "writer",
        input_func=_make_input(
            "DONE writer_result: deliver a compact implementation plan with "
            "trace and state verification."
        ),
    )

    direct_result = await planner.on_messages(
        [
            TextMessage(
                content="Please produce one planning message for AgentLite.",
                source="user",
            )
        ],
        CancellationToken(),
    )

    team = RoundRobinGroupChat(
        [planner, writer],
        termination_condition=TextMentionTermination("DONE"),
    )
    team_result = await team.run(
        task=(
            "Use a two-agent AutoGen team to draft a minimal AgentLite "
            "integration validation plan."
        )
    )

    payload = {
        "direct_agent_result": _message_to_dict(direct_result.chat_message),
        "team_result": _result_to_dict(team_result),
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_SMOKE_OUTPUT")
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
