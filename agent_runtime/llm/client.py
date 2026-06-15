from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from agent_runtime.llm.config import LlmConfig


@dataclass(slots=True)
class ChatCompletionResult:
    content: str
    model: str
    usage: dict[str, int]
    latency_ms: float
    raw_finish_reason: str | None = None


class OpenAICompatibleChatClient:
    """Small stdlib client for OpenAI-compatible chat completion endpoints."""

    def __init__(self, config: LlmConfig) -> None:
        self.config = config

    def complete(self, *, system_prompt: str, user_prompt: str) -> ChatCompletionResult:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "max_completion_tokens": self.config.max_completion_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "api-key": self.config.resolved_api_key,
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"LLM request failed with HTTP {exc.code}: {detail[:800]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc.reason}") from exc

        latency_ms = (time.perf_counter() - started) * 1000
        choices: list[dict[str, Any]] = response_payload.get("choices", [])
        if not choices:
            raise RuntimeError("LLM response did not contain choices.")
        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            finish_reason = choice.get("finish_reason")
            reasoning = message.get("reasoning_content", "")
            raise RuntimeError(
                "LLM response content was empty. "
                f"finish_reason={finish_reason!r}; "
                f"reasoning_chars={len(reasoning) if isinstance(reasoning, str) else 0}; "
                f"response_keys={list(response_payload.keys())}"
            )

        usage = response_payload.get("usage", {}) or {}
        normalized_usage = {
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }
        return ChatCompletionResult(
            content=content.strip(),
            model=str(response_payload.get("model", self.config.model)),
            usage=normalized_usage,
            latency_ms=latency_ms,
            raw_finish_reason=choice.get("finish_reason"),
        )
