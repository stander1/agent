from __future__ import annotations

import json
import unittest

from agent_runtime.llm.client import OpenAICompatibleChatClient
from agent_runtime.llm.config import LlmConfig
from agent_runtime.reliability.provider_guard import (
    NormalizedProviderResponse,
    ProviderGuardReport,
)


class CapturingClient(OpenAICompatibleChatClient):
    def __init__(self, config: LlmConfig) -> None:
        super().__init__(config)
        self.payload: dict | None = None

    def _post_with_retries(self, url: str, data: bytes) -> NormalizedProviderResponse:
        del url
        self.payload = json.loads(data.decode("utf-8"))
        return NormalizedProviderResponse(
            content="ok",
            model=self.config.model,
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            finish_reason="stop",
            report=ProviderGuardReport(status="valid"),
        )


class LlmClientTest(unittest.TestCase):
    def test_default_request_has_no_completion_token_cap(self) -> None:
        client = CapturingClient(LlmConfig(api_key="test-key"))
        client.complete(system_prompt="sys", user_prompt="user")
        self.assertIsNotNone(client.payload)
        self.assertNotIn("max_completion_tokens", client.payload or {})

    def test_deepseek_can_use_bearer_auth_header(self) -> None:
        client = CapturingClient(
            LlmConfig(api_key="test-key", auth_scheme="authorization_bearer")
        )
        self.assertEqual(client.auth_headers(), {"Authorization": "Bearer test-key"})

    def test_auth_header_normalizes_surrounding_whitespace(self) -> None:
        client = CapturingClient(
            LlmConfig(api_key="  test-key\r\n", auth_scheme="authorization_bearer")
        )
        self.assertEqual(client.auth_headers(), {"Authorization": "Bearer test-key"})

    def test_auth_header_rejects_embedded_newline_without_leaking_secret(self) -> None:
        client = CapturingClient(
            LlmConfig(
                api_key="test-secret\r\ninjected-header",
                auth_scheme="authorization_bearer",
            )
        )
        with self.assertRaises(RuntimeError) as caught:
            client.auth_headers()
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertNotIn("injected-header", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
