import unittest
from unittest.mock import patch

from agent_runtime.cli import _tokenizer_backend_check


class DoctorTokenizerCheckTest(unittest.TestCase):
    def test_reports_loaded_tokenizer_backend(self) -> None:
        class ReadyCounter:
            def __init__(self) -> None:
                pass

            def describe(self) -> dict[str, str]:
                return {
                    "tokenizer_name": "tiktoken:cl100k_base",
                    "tokenizer_version": "test",
                }

        with patch(
            "agent_runtime.eval.token_counter.TokenCounter",
            ReadyCounter,
        ):
            result = _tokenizer_backend_check()

        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "tokenizer:cl100k_base")

    def test_reports_missing_encoding_data_instead_of_import_success(self) -> None:
        class BrokenCounter:
            def __init__(self) -> None:
                raise RuntimeError("encoding data unavailable")

        with patch(
            "agent_runtime.eval.token_counter.TokenCounter",
            BrokenCounter,
        ):
            result = _tokenizer_backend_check()

        self.assertFalse(result["ok"])
        self.assertIn("encoding data unavailable", result["detail"])


if __name__ == "__main__":
    unittest.main()
