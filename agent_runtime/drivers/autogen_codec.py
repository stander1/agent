from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class DecodedAutoGenMessage:
    """Stable, framework-neutral view of an AutoGen message or event."""

    native_type: str
    message_kind: str
    source: str = ""
    target: str = ""
    content_text: str = ""
    content_preview: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AutoGenMessageCodec:
    """Map AutoGen AgentChat/Core messages to internal audit structures.

    The codec intentionally uses duck typing so the core runtime does not gain a
    hard import dependency on AutoGen. It accepts real AutoGen pydantic models,
    dictionaries from tests, or light wrapper objects with matching attributes.
    """

    def decode(self, value: Any) -> DecodedAutoGenMessage:
        native_type = type(value).__name__
        raw = _plain_jsonable(_to_plain_dict(value))
        if not isinstance(raw, dict):
            raw = {"type": native_type, "content": str(raw)}
        message_type = str(raw.get("type") or native_type)
        content = raw.get("content")
        text = _content_to_text(content)
        tool_calls = _decode_tool_calls(raw.get("tool_calls") or content)
        tool_results = _decode_tool_results(raw.get("results") or content)
        kind = self._classify(
            message_type=message_type,
            raw=raw,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        return DecodedAutoGenMessage(
            native_type=native_type,
            message_kind=kind,
            source=str(raw.get("source") or ""),
            target=str(raw.get("target") or ""),
            content_text=text,
            content_preview=_preview(text),
            tool_calls=tool_calls,
            tool_results=tool_results,
            metadata=_metadata(raw),
            raw=raw,
        )

    def decode_many(self, value: Any) -> list[DecodedAutoGenMessage]:
        if value is None:
            return []
        if isinstance(value, dict):
            if "args" in value or "kwargs" in value:
                decoded: list[DecodedAutoGenMessage] = []
                for item in value.get("args", ()) or ():
                    decoded.extend(self.decode_many(item))
                kwargs = value.get("kwargs", {}) or {}
                if isinstance(kwargs, dict):
                    for item in kwargs.values():
                        decoded.extend(self.decode_many(item))
                return decoded
            return [self.decode(value)]
        if isinstance(value, (str, bytes)):
            return [self.decode(value)]
        if isinstance(value, (list, tuple, set)):
            decoded = []
            for item in value:
                decoded.extend(self.decode_many(item))
            return decoded
        for attr in ("messages", "chat_message", "inner_messages"):
            if hasattr(value, attr):
                try:
                    nested = getattr(value, attr)
                except Exception:
                    continue
                decoded = self.decode_many(nested)
                if decoded:
                    return decoded
        return [self.decode(value)]

    @staticmethod
    def render_text(messages: list[DecodedAutoGenMessage]) -> str:
        parts = []
        for message in messages:
            if message.content_text:
                prefix = message.source or message.native_type
                parts.append(f"{prefix}: {message.content_text}")
            elif message.tool_calls:
                parts.append(
                    f"{message.source or message.native_type}: "
                    f"tool_calls={json.dumps(message.tool_calls, ensure_ascii=False)}"
                )
            elif message.tool_results:
                parts.append(
                    f"{message.source or message.native_type}: "
                    f"tool_results={json.dumps(message.tool_results, ensure_ascii=False)}"
                )
        return "\n".join(parts)

    @staticmethod
    def _classify(
        *,
        message_type: str,
        raw: dict[str, Any],
        tool_calls: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
    ) -> str:
        if message_type == "HandoffMessage" or raw.get("target"):
            return "handoff"
        if message_type == "ToolCallSummaryMessage" or (
            tool_calls and tool_results
        ):
            return "tool_summary"
        if message_type == "ToolCallRequestEvent":
            return "tool_call"
        if message_type == "ToolCallExecutionEvent":
            return "tool_result"
        if tool_calls:
            return "tool_call"
        if tool_results:
            return "tool_result"
        if message_type.endswith("Event"):
            return "event"
        return "text"


def _metadata(raw: dict[str, Any]) -> dict[str, Any]:
    metadata = raw.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "None", "content": ""}
    if isinstance(value, bytes):
        return {"type": "bytes", "content": value.decode("utf-8", errors="replace")}
    if isinstance(value, str):
        return {"type": "str", "content": value}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    raw: dict[str, Any] = {"type": type(value).__name__}
    for attr in (
        "id",
        "source",
        "target",
        "content",
        "metadata",
        "tool_calls",
        "results",
        "type",
    ):
        if hasattr(value, attr):
            try:
                raw[attr] = getattr(value, attr)
            except Exception:
                continue
    if "content" not in raw:
        raw["content"] = repr(value)
    return raw


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, dict):
        return json.dumps(_plain_jsonable(content), ensure_ascii=False, sort_keys=True)
    if isinstance(content, (list, tuple, set)):
        parts = []
        for item in content:
            if _looks_like_tool_call(item) or _looks_like_tool_result(item):
                continue
            parts.append(_content_to_text(item))
        return "\n".join(part for part in parts if part)
    if hasattr(content, "model_dump") or hasattr(content, "__dict__"):
        return json.dumps(_plain_jsonable(content), ensure_ascii=False, sort_keys=True)
    return str(content)


def _decode_tool_calls(value: Any) -> list[dict[str, Any]]:
    items = _as_sequence(value)
    calls = []
    for item in items:
        raw = _to_plain_dict(item)
        if not _looks_like_tool_call(raw):
            continue
        calls.append(
            {
                "id": str(raw.get("id") or raw.get("call_id") or ""),
                "name": str(raw.get("name") or ""),
                "arguments": raw.get("arguments", ""),
            }
        )
    return calls


def _decode_tool_results(value: Any) -> list[dict[str, Any]]:
    items = _as_sequence(value)
    results = []
    for item in items:
        raw = _to_plain_dict(item)
        if not _looks_like_tool_result(raw):
            continue
        results.append(
            {
                "call_id": str(raw.get("call_id") or raw.get("id") or ""),
                "name": str(raw.get("name") or ""),
                "content": _content_to_text(raw.get("content", "")),
                "is_error": raw.get("is_error"),
            }
        )
    return results


def _as_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _looks_like_tool_call(value: Any) -> bool:
    raw = value if isinstance(value, dict) else _to_plain_dict(value)
    return "arguments" in raw and "name" in raw and (
        "id" in raw or "call_id" in raw
    )


def _looks_like_tool_result(value: Any) -> bool:
    raw = value if isinstance(value, dict) else _to_plain_dict(value)
    return "call_id" in raw and "name" in raw and "content" in raw


def _plain_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _plain_jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _preview(text: str, *, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]
