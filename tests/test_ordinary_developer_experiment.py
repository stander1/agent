from __future__ import annotations

import json
import os
import runpy
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_GLOBALS = runpy.run_path(
    str(PROJECT_ROOT / "experiments" / "ordinary-developer-autogen" / "code_app.py")
)
TEAM_TEMPLATE = (
    PROJECT_ROOT
    / "experiments"
    / "ordinary-developer-autogen"
    / "studio_team_config.template.json"
)


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class OrdinaryDeveloperExperimentTests(unittest.TestCase):
    def test_studio_team_template_is_memory_aware_and_secret_free(self) -> None:
        payload = json.loads(TEAM_TEMPLATE.read_text(encoding="utf-8"))
        participants = payload["config"]["participants"]

        self.assertEqual(
            [item["config"]["name"] for item in participants],
            ["planner", "writer", "reviewer"],
        )
        self.assertEqual(payload["config"]["max_turns"], 6)
        self.assertEqual(
            payload["config"]["termination_condition"]["config"]["text"],
            "FINAL_ANSWER_READY",
        )
        self.assertTrue(
            all(
                "MemoryView" in item["config"]["system_message"]
                for item in participants
            )
        )
        self.assertTrue(
            all(
                item["config"]["model_client"]["config"]["api_key"]
                == "REPLACE_WITH_YOUR_API_KEY"
                for item in participants
            )
        )
        self.assertTrue(
            all(
                item["config"]["model_client"]["config"]["max_retries"] == 3
                for item in participants
            )
        )

    def test_mimo_default_model_uses_provider_supported_name(self) -> None:
        client_type = APP_GLOBALS["OpenAICompatibleClient"]
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "https://example.invalid/v1",
            },
            clear=True,
        ):
            client = client_type.from_env(temperature=0.2)

        self.assertEqual(client.model, "mimo-v2.5")

    def test_transient_connection_failure_retries_same_request(self) -> None:
        client_type = APP_GLOBALS["OpenAICompatibleClient"]
        client = client_type(
            api_key="test-key",
            base_url="https://example.invalid/v1",
            model="test-model",
            temperature=0.2,
            max_retries=2,
            retry_backoff_seconds=0,
        )
        response = _FakeResponse(
            b'{"choices":[{"message":{"content":"ok"}}],'
            b'"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}'
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=[
                urllib.error.URLError(ConnectionRefusedError(111, "refused")),
                response,
            ],
        ) as urlopen:
            result = client.complete([{"role": "user", "content": "hello"}])

        self.assertEqual(result.content, "ok")
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(result.usage["total_tokens"], 5)
        self.assertEqual(urlopen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
