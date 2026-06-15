from __future__ import annotations

import math
import re
from dataclasses import dataclass
from importlib import metadata
from typing import Any


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass(slots=True)
class TokenCount:
    token_count: int
    token_count_method: str
    tokenizer_name: str
    tokenizer_version: str
    char_count: int


class TokenCounter:
    """Pluggable token counter with a CJK-aware fallback."""

    def __init__(
        self,
        tokenizer_name: str | None = None,
        model_name: str | None = None,
        prefer_transformers: bool = False,
        allow_estimate: bool = False,
    ) -> None:
        self.requested_tokenizer_name = tokenizer_name
        self.model_name = model_name
        self.allow_estimate = allow_estimate
        self._backend: Any | None = None
        self._method = "estimated"
        self._name = "cjk_mixed_estimator"
        self._version = "builtin"

        if prefer_transformers or tokenizer_name:
            self._try_load_transformers(tokenizer_name or model_name)
        if self._backend is None:
            self._try_load_tiktoken(model_name)
        if self._backend is None and not self.allow_estimate:
            raise RuntimeError(
                "No tokenizer backend is available. Install tiktoken/transformers "
                "or pass allow_estimate=True only for non-benchmark diagnostics."
            )

    def count(self, text: str) -> TokenCount:
        if self._backend is not None:
            try:
                if self._method in {"actual", "compatible"}:
                    token_count = len(self._backend.encode(text))
                    return TokenCount(
                        token_count=token_count,
                        token_count_method=self._method,
                        tokenizer_name=self._name,
                        tokenizer_version=self._version,
                        char_count=len(text),
                    )
            except Exception:
                if not self.allow_estimate:
                    raise

        if not self.allow_estimate:
            raise RuntimeError(
                "Tokenizer backend failed during token counting. Re-run with a valid "
                "tokenizer or explicitly enable allow_estimate for diagnostics."
            )
        return TokenCount(
            token_count=self._estimate_mixed(text),
            token_count_method="estimated",
            tokenizer_name="cjk_mixed_estimator",
            tokenizer_version="builtin",
            char_count=len(text),
        )

    def describe(self) -> dict[str, str]:
        return {
            "token_count_method": self._method,
            "tokenizer_name": self._name,
            "tokenizer_version": self._version,
        }

    def _try_load_transformers(self, tokenizer_name: str | None) -> None:
        if not tokenizer_name:
            return
        try:
            from transformers import AutoTokenizer  # type: ignore

            tokenizer = AutoTokenizer.from_pretrained(tokenizer_name)
            self._backend = tokenizer
            self._method = "actual"
            self._name = tokenizer_name
            self._version = metadata.version("transformers")
        except Exception:
            self._backend = None

    def _try_load_tiktoken(self, model_name: str | None) -> None:
        try:
            import tiktoken  # type: ignore

            if model_name:
                try:
                    encoding = tiktoken.encoding_for_model(model_name)
                    name = f"tiktoken:{model_name}"
                    method = "actual"
                except Exception:
                    encoding = tiktoken.get_encoding("cl100k_base")
                    name = "tiktoken:cl100k_base"
                    method = "compatible"
            else:
                encoding = tiktoken.get_encoding("cl100k_base")
                name = "tiktoken:cl100k_base"
                method = "compatible"

            self._backend = encoding
            self._method = method
            self._name = name
            self._version = metadata.version("tiktoken")
        except Exception:
            self._backend = None

    @staticmethod
    def _estimate_mixed(text: str) -> int:
        cjk_chars = len(_CJK_RE.findall(text))
        ascii_chars = sum(1 for ch in text if ord(ch) < 128)
        other_chars = max(0, len(text) - cjk_chars - ascii_chars)
        return max(1, math.ceil(ascii_chars / 4 + cjk_chars * 1.5 + other_chars / 2))
