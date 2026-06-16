from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ProviderResponseError(ValueError):
    """Raised when deterministic normalization cannot recover a model response."""


@dataclass(slots=True)
class ProviderGuardReport:
    status: str
    repair_actions: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    raw_root_type: str = ""
    normalized_format: str = ""
    retry_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NormalizedProviderResponse:
    content: str
    model: str
    usage: dict[str, int]
    finish_reason: str | None
    report: ProviderGuardReport


def normalize_provider_response(
    payload: Any, *, default_model: str
) -> NormalizedProviderResponse:
    """Normalize known provider response envelopes before deciding to retry.

    This is the provider-side counterpart of Output Contract Guard: it first
    tries deterministic envelope repair and only raises when no usable content
    can be recovered.
    """

    report = ProviderGuardReport(status="valid", raw_root_type=type(payload).__name__)
    root = payload

    if isinstance(root, list):
        if len(root) == 1 and isinstance(root[0], dict):
            root = root[0]
            report.status = "repaired"
            report.repair_actions.append("unwrap_single_item_list")
        else:
            report.schema_errors.append("provider response list is not a single object")
            raise ProviderResponseError(_format_error(report))

    if not isinstance(root, dict):
        report.schema_errors.append(
            f"provider response root must be object, got {type(root).__name__}"
        )
        raise ProviderResponseError(_format_error(report))

    model = str(root.get("model", default_model))
    finish_reason: str | None = None
    content = ""

    if isinstance(root.get("choices"), list):
        report.normalized_format = "openai_chat"
        choices = root.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            report.schema_errors.append("choices must contain at least one object")
            raise ProviderResponseError(_format_error(report))
        choice = choices[0]
        finish_reason = choice.get("finish_reason")
        message = choice.get("message", {})
        if isinstance(message, dict):
            content = _coerce_content(message.get("content"))
            if not content:
                reasoning = _coerce_content(message.get("reasoning_content"))
                if reasoning:
                    report.schema_errors.append(
                        "message.content empty; reasoning_content is not usable output"
                    )
        if not content:
            content = _coerce_content(choice.get("text"))
            if content:
                report.status = _mark_repaired(report)
                report.repair_actions.append("extract_choice_text")
    elif "content" in root:
        content_value = root.get("content")
        content = _coerce_content(content_value)
        report.normalized_format = (
            "anthropic_content" if isinstance(content_value, list) else "top_level_content"
        )
        report.status = _mark_repaired(report)
        report.repair_actions.append("extract_top_level_content")
    elif isinstance(root.get("message"), dict):
        content = _coerce_content(root["message"].get("content"))
        report.normalized_format = "top_level_message"
        report.status = _mark_repaired(report)
        report.repair_actions.append("extract_top_level_message_content")
    else:
        report.schema_errors.append("no choices/message/content envelope found")
        raise ProviderResponseError(_format_error(report))

    if not content.strip():
        report.schema_errors.append("provider response content is empty")
        raise ProviderResponseError(_format_error(report))

    usage = _normalize_usage(root.get("usage", {}))
    return NormalizedProviderResponse(
        content=content.strip(),
        model=model,
        usage=usage,
        finish_reason=finish_reason,
        report=report,
    )


def _coerce_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _normalize_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = int(value.get("prompt_tokens", value.get("input_tokens", 0)) or 0)
    completion = int(
        value.get("completion_tokens", value.get("output_tokens", 0)) or 0
    )
    total = int(value.get("total_tokens", prompt + completion) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _mark_repaired(report: ProviderGuardReport) -> str:
    return "repaired" if report.status == "valid" else report.status


def _format_error(report: ProviderGuardReport) -> str:
    return "; ".join(report.schema_errors) or "provider response malformed"
