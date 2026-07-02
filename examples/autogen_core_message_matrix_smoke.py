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
        "AutoGen matrix smoke dependencies are not installed. "
        "Install with: python -m pip install -e \".[autogen]\"",
        file=sys.stderr,
    )
    print(f"ImportError: {exc}", file=sys.stderr)
    return 2


try:
    from autogen_core import (  # type: ignore
        AgentId,
        DefaultTopicId,
        MessageContext,
        RoutedAgent,
        SingleThreadedAgentRuntime,
        message_handler,
        type_subscription,
    )
    from pydantic import BaseModel
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


RECEIVED: list[dict[str, Any]] = []


@dataclass
class MatrixContentPayload:
    source: str
    content: str
    payload_kind: str = "matrix_content_state"


@dataclass
class MatrixBodyPayload:
    source: str
    body: str
    payload_kind: str = "matrix_body_state"


@dataclass
class MatrixTextPayload:
    source: str
    text: str
    payload_kind: str = "matrix_text_state"


class MatrixPydanticPayload(BaseModel):
    source: str
    content: str
    payload_kind: str = "matrix_pydantic_state"


@dataclass
class MatrixReply:
    source: str
    content: str


def _long_text(prefix: str, *, sections: int = 10) -> str:
    paragraph = (
        "{prefix} section {idx}: AutoGen Core 消息结构矩阵压测文本。"
        "它模拟不同框架或 Studio 后端可能传入的 content/body/text 字段。"
        "AgentLite 应保持原 Python 类型，同时将长文本字段替换为低开销状态引用，"
        "并在接收端自动转换为 Prompt View。"
    )
    return "\n".join(
        paragraph.format(prefix=prefix, idx=index)
        for index in range(1, sections + 1)
    )


def _record(
    *,
    case: str,
    kind: str,
    message: Any,
    field_name: str,
    ctx: MessageContext,
) -> None:
    value = str(getattr(message, field_name, "") or "")
    RECEIVED.append(
        {
            "case": case,
            "kind": kind,
            "message_type": type(message).__name__,
            "field_name": field_name,
            "payload_kind": str(getattr(message, "payload_kind", "") or ""),
            "content_chars": len(value),
            "agentlite_rewrite_marker": "AGENTLITE_CORE_CONTENT_REWRITE v1" in value,
            "agentlite_prompt_view": value.startswith("[artifact_state:"),
            "content_preview": value[:160],
            "topic": str(ctx.topic_id) if ctx.topic_id else "",
            "sender": str(ctx.sender) if ctx.sender else "",
        }
    )


class MatrixContentAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("matrix content receiver")

    @message_handler
    async def handle_payload(
        self,
        message: MatrixContentPayload,
        ctx: MessageContext,
    ) -> MatrixReply:
        _record(
            case="dataclass_content",
            kind="direct",
            message=message,
            field_name="content",
            ctx=ctx,
        )
        return MatrixReply(source="content_agent", content=message.content[:120])


class MatrixBodyAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("matrix body receiver")

    @message_handler
    async def handle_payload(
        self,
        message: MatrixBodyPayload,
        ctx: MessageContext,
    ) -> MatrixReply:
        _record(
            case="dataclass_body",
            kind="direct",
            message=message,
            field_name="body",
            ctx=ctx,
        )
        return MatrixReply(source="body_agent", content=message.body[:120])


class MatrixTextAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("matrix text receiver")

    @message_handler
    async def handle_payload(
        self,
        message: MatrixTextPayload,
        ctx: MessageContext,
    ) -> MatrixReply:
        _record(
            case="dataclass_text",
            kind="direct",
            message=message,
            field_name="text",
            ctx=ctx,
        )
        return MatrixReply(source="text_agent", content=message.text[:120])


class MatrixPydanticAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("matrix pydantic receiver")

    @message_handler
    async def handle_payload(
        self,
        message: MatrixPydanticPayload,
        ctx: MessageContext,
    ) -> MatrixReply:
        _record(
            case="pydantic_content",
            kind="direct",
            message=message,
            field_name="content",
            ctx=ctx,
        )
        return MatrixReply(source="pydantic_agent", content=message.content[:120])


@type_subscription("matrix-topic")
class MatrixBroadcastAgent(RoutedAgent):
    def __init__(self) -> None:
        super().__init__("matrix broadcast receiver")

    @message_handler
    async def handle_payload(
        self,
        message: MatrixContentPayload,
        ctx: MessageContext,
    ) -> None:
        _record(
            case="dataclass_content_publish",
            kind="publish",
            message=message,
            field_name="content",
            ctx=ctx,
        )


async def main() -> int:
    runtime = SingleThreadedAgentRuntime()
    await MatrixContentAgent.register(runtime, "matrix_content", MatrixContentAgent)
    await MatrixBodyAgent.register(runtime, "matrix_body", MatrixBodyAgent)
    await MatrixTextAgent.register(runtime, "matrix_text", MatrixTextAgent)
    await MatrixPydanticAgent.register(runtime, "matrix_pydantic", MatrixPydanticAgent)
    await MatrixBroadcastAgent.register(runtime, "matrix_broadcast", MatrixBroadcastAgent)
    runtime.start()

    cases = {
        "dataclass_content": MatrixContentPayload(
            source="user",
            content=_long_text("dataclass_content"),
        ),
        "dataclass_body": MatrixBodyPayload(
            source="user",
            body=_long_text("dataclass_body"),
        ),
        "dataclass_text": MatrixTextPayload(
            source="user",
            text=_long_text("dataclass_text"),
        ),
        "pydantic_content": MatrixPydanticPayload(
            source="user",
            content=_long_text("pydantic_content"),
        ),
        "dataclass_content_publish": MatrixContentPayload(
            source="system",
            content=_long_text("dataclass_content_publish"),
        ),
    }

    replies = {
        "dataclass_content": await runtime.send_message(
            cases["dataclass_content"],
            AgentId("matrix_content", "default"),
        ),
        "dataclass_body": await runtime.send_message(
            cases["dataclass_body"],
            AgentId("matrix_body", "default"),
        ),
        "dataclass_text": await runtime.send_message(
            cases["dataclass_text"],
            AgentId("matrix_text", "default"),
        ),
        "pydantic_content": await runtime.send_message(
            cases["pydantic_content"],
            AgentId("matrix_pydantic", "default"),
        ),
    }
    await runtime.publish_message(
        cases["dataclass_content_publish"],
        DefaultTopicId("matrix-topic"),
    )
    await runtime.stop_when_idle()

    input_chars = {
        "dataclass_content": len(cases["dataclass_content"].content),
        "dataclass_body": len(cases["dataclass_body"].body),
        "dataclass_text": len(cases["dataclass_text"].text),
        "pydantic_content": len(cases["pydantic_content"].content),
        "dataclass_content_publish": len(cases["dataclass_content_publish"].content),
    }
    output = {
        "scenario": "autogen_core_message_matrix_smoke",
        "received": RECEIVED,
        "input_chars": input_chars,
        "reply_types": {
            key: type(value).__name__ for key, value in replies.items()
        },
        "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
        "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
    }
    output_path = os.getenv("AGENTLITE_AUTOGEN_CORE_MATRIX_OUTPUT")
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
