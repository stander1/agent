from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class LlmConfig:
    provider: str = "mimo"
    base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    model: str = "mimo-v2.5"
    api_key_env: str = "MIMO_API_KEY"
    api_key: str | None = None
    timeout_seconds: float = 120.0
    max_retries: int = 2
    retry_backoff_seconds: float = 2.0
    max_completion_tokens: int = 300
    temperature: float = 0.2
    top_p: float = 0.9

    @property
    def resolved_api_key(self) -> str:
        key = self.api_key or os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(
                f"Missing LLM API key. Set {self.api_key_env} or provide a local config."
            )
        return key

    def without_secret(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "max_completion_tokens": self.max_completion_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
        }


def load_llm_config(
    path: Path | None = None,
    *,
    base_url: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    retry_backoff_seconds: float | None = None,
    max_completion_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
) -> LlmConfig:
    config = LlmConfig()
    if path is not None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        config = LlmConfig(**{**asdict(config), **payload})

    updates: dict[str, Any] = {}
    for key, value in {
        "base_url": base_url,
        "model": model,
        "api_key_env": api_key_env,
        "timeout_seconds": timeout_seconds,
        "max_retries": max_retries,
        "retry_backoff_seconds": retry_backoff_seconds,
        "max_completion_tokens": max_completion_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }.items():
        if value is not None:
            updates[key] = value
    if updates:
        config = replace(config, **updates)
    return config
