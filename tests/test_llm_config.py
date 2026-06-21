from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from agent_runtime.llm.config import load_llm_config


class LlmConfigTest(unittest.TestCase):
    def test_loads_config_without_exposing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.local.json"
            path.write_text(
                '{"model":"mimo-v2.5-pro","api_key_env":"TEST_MIMO_KEY"}',
                encoding="utf-8",
            )
            os.environ["TEST_MIMO_KEY"] = "secret-value"
            try:
                config = load_llm_config(path)
                self.assertEqual(config.resolved_api_key, "secret-value")
                self.assertNotIn("secret-value", str(config.without_secret()))
            finally:
                os.environ.pop("TEST_MIMO_KEY", None)

    def test_cli_overrides_file_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.local.json"
            path.write_text('{"model":"old-model"}', encoding="utf-8")
            config = load_llm_config(path, model="new-model")
            self.assertEqual(config.model, "new-model")

    def test_ignores_legacy_completion_token_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.local.json"
            path.write_text('{"max_completion_tokens":300}', encoding="utf-8")
            config = load_llm_config(path)
            self.assertNotIn("max_completion_tokens", config.without_secret())

    def test_loads_auth_scheme(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.local.json"
            path.write_text('{"auth_scheme":"authorization_bearer"}', encoding="utf-8")
            config = load_llm_config(path)
            self.assertEqual(config.auth_scheme, "authorization_bearer")


if __name__ == "__main__":
    unittest.main()
