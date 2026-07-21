from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from contextvars import ContextVar
from pathlib import Path
from typing import Any


STUDIO_CONTEXT_MODULE = "autogenstudio.web.managers.run_context"
_studio_run_id: ContextVar[int] = ContextVar("smoke_studio_run_id")
module = types.ModuleType(STUDIO_CONTEXT_MODULE)


class RunContext:
    @classmethod
    def current_run_id(cls) -> int:
        return _studio_run_id.get()


module.RunContext = RunContext
sys.modules[STUDIO_CONTEXT_MODULE] = module

try:
    from autogen_agentchat.agents import UserProxyAgent
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.teams import RoundRobinGroupChat
except ImportError as exc:
    print(f"AutoGen smoke dependencies are missing: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc


def _make_input(reply: str):
    async def input_func(*_args: Any, **_kwargs: Any) -> str:
        return reply

    return input_func


async def _run_one(run_id: int) -> dict[str, Any]:
    token = _studio_run_id.set(run_id)
    try:
        planner = UserProxyAgent(
            f"planner_{run_id}",
            input_func=_make_input(f"plan for Studio Run {run_id}"),
        )
        writer = UserProxyAgent(
            f"writer_{run_id}",
            input_func=_make_input(f"DONE result for Studio Run {run_id}"),
        )
        team = RoundRobinGroupChat(
            [planner, writer],
            termination_condition=TextMentionTermination("DONE"),
        )
        result = await team.run(task=f"Execute isolated Studio task {run_id}")
        return {
            "run_id": run_id,
            "message_count": len(result.messages),
            "stop_reason": result.stop_reason,
        }
    finally:
        _studio_run_id.reset(token)


async def main() -> int:
    results = [await _run_one(run_id) for run_id in (101, 102)]
    payload = {
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
        "runs": results,
    }
    output = os.getenv("AGENTLITE_AUTOGEN_STUDIO_BINDING_SMOKE_OUTPUT", "")
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
