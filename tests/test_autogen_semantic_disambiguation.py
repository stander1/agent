from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.bootstrap.startup import BootstrapContext
from agent_runtime.drivers.autogen import (
    SEMANTIC_DISAMBIGUATION_API_KEY_ENV,
    SEMANTIC_DISAMBIGUATION_ENV,
    SEMANTIC_DISAMBIGUATION_MAX_CALLS_ENV,
    SEMANTIC_DISAMBIGUATION_MAX_TOKENS_ENV,
    AutoGenHookManager,
)


class AutoGenSemanticDisambiguationTest(unittest.TestCase):
    def test_control_path_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {SEMANTIC_DISAMBIGUATION_ENV: "0"},
            clear=False,
        ):
            manager = AutoGenHookManager(self._context(Path(tmp)))

        self.assertFalse(manager.semantic_disambiguation_enabled)
        self.assertIsNone(
            manager.kernel.state_memory_bridge.semantic_disambiguator
        )

    def test_explicit_opt_in_builds_bounded_client_without_copying_secret(
        self,
    ) -> None:
        env = {
            SEMANTIC_DISAMBIGUATION_ENV: "1",
            SEMANTIC_DISAMBIGUATION_API_KEY_ENV: "CONTROL_TEST_KEY",
            SEMANTIC_DISAMBIGUATION_MAX_CALLS_ENV: "2",
            SEMANTIC_DISAMBIGUATION_MAX_TOKENS_ENV: "333",
            "CONTROL_TEST_KEY": "not-read-during-construction",
            "OPENAI_BASE_URL": "https://provider.invalid/v1",
            "OPENAI_MODEL": "control-model",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            env,
            clear=False,
        ):
            manager = AutoGenHookManager(self._context(Path(tmp)))

        self.assertTrue(manager.semantic_disambiguation_enabled)
        disambiguator = (
            manager.kernel.state_memory_bridge.semantic_disambiguator
        )
        self.assertIsNotNone(disambiguator)
        assert disambiguator is not None
        self.assertEqual(disambiguator.budget.max_calls_per_task, 2)
        self.assertEqual(
            disambiguator.budget.max_control_tokens_per_task,
            333,
        )
        config = disambiguator.client.config
        self.assertEqual(config.api_key_env, "CONTROL_TEST_KEY")
        self.assertIsNone(config.api_key)
        self.assertEqual(config.auth_scheme, "authorization_bearer")
        self.assertEqual(config.base_url, "https://provider.invalid/v1")
        self.assertEqual(config.model, "control-model")

    @staticmethod
    def _context(root: Path) -> BootstrapContext:
        status_file = (
            root / "sessions" / "launch-semantic" / "bootstrap_status.json"
        )
        status_file.parent.mkdir(parents=True, exist_ok=True)
        workspace = root / "workspace"
        workspace.mkdir()
        return BootstrapContext(
            framework="autogen",
            session_id="launch-semantic",
            data_dir=root,
            status_file=status_file,
            target_cwd=workspace,
        )


if __name__ == "__main__":
    unittest.main()
