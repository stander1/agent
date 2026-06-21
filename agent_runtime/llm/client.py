from __future__ import annotations

import json
import http.client
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from agent_runtime.llm.config import LlmConfig
from agent_runtime.reliability.provider_guard import (
    NormalizedProviderResponse,
    ProviderResponseError,
    normalize_provider_response,
)


@dataclass(slots=True)
class ChatCompletionResult:
    content: str
    model: str
    usage: dict[str, int]
    latency_ms: float
    raw_finish_reason: str | None = None
    provider_guard: dict[str, Any] | None = None


class OpenAICompatibleChatClient:
    """Small stdlib client for OpenAI-compatible chat completion endpoints."""

    def __init__(self, config: LlmConfig) -> None:
        self.config = config

    def auth_headers(self) -> dict[str, str]:
        key = self.config.resolved_api_key
        if self.config.auth_scheme == "authorization_bearer":
            return {"Authorization": f"Bearer {key}"}
        if self.config.auth_scheme == "api_key":
            return {"api-key": key}
        raise ValueError(f"Unsupported auth_scheme: {self.config.auth_scheme}")

    def complete(self, *, system_prompt: str, user_prompt: str) -> ChatCompletionResult:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature,
            "top_p": self.config.top_p,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        started = time.perf_counter()
        response_payload = self._post_with_retries(url, data)

        latency_ms = (time.perf_counter() - started) * 1000
        return ChatCompletionResult(
            content=response_payload.content,
            model=response_payload.model,
            usage=response_payload.usage,
            latency_ms=latency_ms,
            raw_finish_reason=response_payload.finish_reason,
            provider_guard=response_payload.report.to_dict(),
        )

    def _post_with_retries(self, url: str, data: bytes) -> NormalizedProviderResponse:
        last_error: Exception | None = None
        attempts = max(1, self.config.max_retries + 1)
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "application/json",
                    **self.auth_headers(),
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.config.timeout_seconds
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    normalized = normalize_provider_response(
                        payload, default_model=self.config.model
                    )
                    normalized.report.retry_attempts = attempt - 1
                    return normalized
            except ProviderResponseError as exc:
                if attempt >= attempts:
                    raise RuntimeError(
                        f"LLM provider response unrecoverable: {exc}"
                    ) from exc
                last_error = exc
            except json.JSONDecodeError as exc:
                if attempt >= attempts:
                    raise RuntimeError(
                        f"LLM response JSON parse failed: {exc}"
                    ) from exc
                last_error = exc
            except ValueError as exc:
                if attempt >= attempts:
                    raise RuntimeError(
                        f"LLM response normalization failed: {exc}"
                    ) from exc
                last_error = exc
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code < 500 or attempt >= attempts:
                    raise RuntimeError(
                        f"LLM request failed with HTTP {exc.code}: {detail[:800]}"
                    ) from exc
                last_error = exc
            except urllib.error.URLError as exc:
                if attempt >= attempts:
                    raise RuntimeError(f"LLM request failed: {exc.reason}") from exc
                last_error = exc
            except (http.client.RemoteDisconnected, TimeoutError, ConnectionError) as exc:
                if attempt >= attempts:
                    raise RuntimeError(f"LLM connection failed: {exc}") from exc
                last_error = exc
            time.sleep(self.config.retry_backoff_seconds * attempt)
        raise RuntimeError(f"LLM request failed after retries: {last_error}") from last_error
