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
        "AutoGen Core response smoke dependencies are not installed. "
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
    from pydantic import BaseModel
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


RECEIVED: list[dict[str, Any]] = []
CALLER_REPLIES: list[dict[str, Any]] = []


@dataclass
class ResponseRequest:
    source: str
    content: str
    case: str


@dataclass
class ResponseContentReply:
    source: str
    content: str
    payload_kind: str = "response_content_state"


@dataclass
class ResponseBodyReply:
    source: str
    body: str
    payload_kind: str = "response_body_state"


@dataclass
class ResponseTextReply:
    source: str
    text: str
    payload_kind: str = "response_text_state"


class ResponsePydanticReply(BaseModel):
    source: str
    content: str
    payload_kind: str = "response_pydantic_state"


def _long_text(prefix: str, *, sections: int = 12) -> str:
    paragraph = (
        "{prefix} section {idx}: AutoGen Core response rewrite smoke payload. "
        "This text simulates a verbose agent reply that should not be copied "
        "through the runtime as full native collaboration text. AgentLite should "
        "store the native response in StatePool, send a compact state reference, "
        "and hydrate the caller-visible result into a role-scoped Prompt View."
    )
    return "\n".join(
        paragraph.format(prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


def _record_request(case: str, message: ResponseRequest, ctx: MessageContext) -> None:
    content = message.content
    RECEIVED.append(
        {
            "case": case,
            "message_type": type(message).__name__,
            "content_chars": len(content),
            "agentlite_rewrite_marker": "AGENTLITE_CORE_CONTENT_REWRITE v1"
            in content,
            "agentlite_prompt_view": content.startswith("[artifact_state:"),
            "sender": str(ctx.sender) if ctx.sender else "",
        }
    )


def _field_value(message: Any, field_name: str) -> str:
    return str(getattr(message, field_name, "") or "")


def _record_reply(case: str, reply: Any, field_name: str) -> None:
    value = _field_value(reply, field_name)
    CALLER_REPLIES.append(
        {
            "case": case,
            "message_type": type(reply).__name__,
            "field_name": field_name,
            "payload_kind": str(getattr(reply, "payload_kind", "") or ""),
            "content_chars": len(value),
            "agentlite_rewrite_marker": "AGENTLITE_CORE_CONTENT_REWRITE v1"
            in value,
            "agentlite_prompt_view": value.startswith("[artifact_state:"),
            "content_preview": value[:160],
        }
    )


class ResponseContentAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("response content receiver")

    @message_handler
    async def handle_payload(
        self,
        message: ResponseRequest,
        ctx: MessageContext,
    ) -> ResponseContentReply:
        _record_request("response_content", message, ctx)
        return ResponseContentReply(
            source="content_agent",
            content=_long_text("response_content_reply"),
        )


class ResponseBodyAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("response body receiver")

    @message_handler
    async def handle_payload(
        self,
        message: ResponseRequest,
        ctx: MessageContext,
    ) -> ResponseBodyReply:
        _record_request("response_body", message, ctx)
        return ResponseBodyReply(
            source="body_agent",
            body=_long_text("response_body_reply"),
        )


class ResponseTextAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("response text receiver")

    @message_handler
    async def handle_payload(
        self,
        message: ResponseRequest,
        ctx: MessageContext,
    ) -> ResponseTextReply:
        _record_request("response_text", message, ctx)
        return ResponseTextReply(
            source="text_agent",
            text=_long_text("response_text_reply"),
        )


class ResponsePydanticAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("response pydantic receiver")

    @message_handler
    async def handle_payload(
        self,
        message: ResponseRequest,
        ctx: MessageContext,
    ) -> ResponsePydanticReply:
        _record_request("response_pydantic", message, ctx)
        return ResponsePydanticReply(
            source="pydantic_agent",
            content=_long_text("response_pydantic_reply"),
        )


class CallerAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("registered caller identity")


async def main() -> int:
    runtime = SingleThreadedAgentRuntime()
    await CallerAgent.register(runtime, "caller", CallerAgent)
    await ResponseContentAgent.register(
        runtime,
        "response_content",
        ResponseContentAgent,
    )
    await ResponseBodyAgent.register(runtime, "response_body", ResponseBodyAgent)
    await ResponseTextAgent.register(runtime, "response_text", ResponseTextAgent)
    await ResponsePydanticAgent.register(
        runtime,
        "response_pydantic",
        ResponsePydanticAgent,
    )
    runtime.start()

    cases = {
        "response_content": (
            AgentId("response_content", "default"),
            ResponseRequest(
                source="user",
                case="response_content",
                content=_long_text("request_content"),
            ),
            "content",
        ),
        "response_body": (
            AgentId("response_body", "default"),
            ResponseRequest(
                source="user",
                case="response_body",
                content=_long_text("request_body"),
            ),
            "body",
        ),
        "response_text": (
            AgentId("response_text", "default"),
            ResponseRequest(
                source="user",
                case="response_text",
                content=_long_text("request_text"),
            ),
            "text",
        ),
        "response_pydantic": (
            AgentId("response_pydantic", "default"),
            ResponseRequest(
                source="user",
                case="response_pydantic",
                content=_long_text("request_pydantic"),
            ),
            "content",
        ),
    }
    for case, (recipient, request, reply_field) in cases.items():
        reply = await runtime.send_message(
            request,
            recipient,
            sender=AgentId("caller", "default"),
        )
        _record_reply(case, reply, reply_field)
    await runtime.stop_when_idle()

    input_chars = {
        case: len(request.content)
        for case, (_, request, _) in cases.items()
    }
    native_reply_chars = {
        "response_content": len(_long_text("response_content_reply")),
        "response_body": len(_long_text("response_body_reply")),
        "response_text": len(_long_text("response_text_reply")),
        "response_pydantic": len(_long_text("response_pydantic_reply")),
    }
    output = {
        "scenario": "autogen_core_response_rewrite_smoke",
        "received_requests": RECEIVED,
        "caller_replies": CALLER_REPLIES,
        "input_chars": input_chars,
        "native_reply_chars": native_reply_chars,
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_CORE_RESPONSE_OUTPUT")
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
