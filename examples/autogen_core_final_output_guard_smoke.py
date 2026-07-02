from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _missing_dependency(exc: ImportError) -> int:
    print(
        "AutoGen Core final-output smoke dependencies are not installed. "
        "Install with: python -m pip install -e \".[autogen]\"",
        file=sys.stderr,
    )
    print(f"ImportError: {exc}", file=sys.stderr)
    return 2


try:
    from autogen_core import (  # type: ignore
        AgentId,
        MessageContext,
        RoutedAgent,
        SingleThreadedAgentRuntime,
        message_handler,
    )
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


RECEIVED: list[dict[str, Any]] = []


@dataclass
class FinalRequest:
    source: str
    content: str


@dataclass
class FinalAnswer:
    source: str
    content: str
    payload_kind: str = "final_output_state"


def _long_text(prefix: str, *, sections: int = 14) -> str:
    paragraph = (
        "{prefix} section {idx}: This is a user-facing final answer payload. "
        "AgentLite may optimize internal collaboration messages, but this final "
        "boundary should preserve the native answer text that application code "
        "expects to show or persist."
    )
    return "\n".join(
        paragraph.format(prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


class FinalOutputAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("final output receiver")

    @message_handler
    async def handle_payload(
        self,
        message: FinalRequest,
        ctx: MessageContext,
    ) -> FinalAnswer:
        content = message.content
        RECEIVED.append(
            {
                "message_type": type(message).__name__,
                "content_chars": len(content),
                "agentlite_rewrite_marker": "AGENTLITE_CORE_CONTENT_REWRITE v1"
                in content,
                "agentlite_prompt_view": content.startswith("[artifact_state:"),
                "sender": str(ctx.sender) if ctx.sender else "",
            }
        )
        return FinalAnswer(
            source="final_agent",
            content=_long_text("final_output"),
        )


async def main() -> int:
    runtime = SingleThreadedAgentRuntime()
    await FinalOutputAgent.register(runtime, "final_agent", FinalOutputAgent)
    runtime.start()

    request = FinalRequest(
        source="user",
        content=_long_text("final_request"),
    )
    reply = await runtime.send_message(
        request,
        AgentId("final_agent", "default"),
    )
    await runtime.stop_when_idle()

    output = {
        "scenario": "autogen_core_final_output_guard_smoke",
        "received": RECEIVED,
        "input_chars": len(request.content),
        "native_final_chars": len(_long_text("final_output")),
        "reply": {
            "message_type": type(reply).__name__,
            "payload_kind": str(getattr(reply, "payload_kind", "") or ""),
            "content_chars": len(str(getattr(reply, "content", "") or "")),
            "agentlite_rewrite_marker": "AGENTLITE_CORE_CONTENT_REWRITE v1"
            in str(getattr(reply, "content", "") or ""),
            "agentlite_prompt_view": str(
                getattr(reply, "content", "") or ""
            ).startswith("[artifact_state:"),
            "content_preview": str(getattr(reply, "content", "") or "")[:160],
        },
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_CORE_FINAL_OUTPUT")
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
