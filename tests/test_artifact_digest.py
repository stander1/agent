import unittest

from agent_runtime.reliability.contract_guard import build_artifact_digest


class TypedArtifactDigestTest(unittest.TestCase):
    def test_python_digest_extracts_code_structure(self) -> None:
        digest = build_artifact_digest(
            "import os\n\n"
            "class Runner:\n"
            "    pass\n\n"
            "def main(path):\n"
            "    return os.path.exists(path)\n"
        )

        self.assertEqual(digest["artifact_type"], "python_code")
        self.assertEqual(digest["parse_status"], "ok")
        self.assertIn("os", digest["imports"])
        self.assertIn("Runner", digest["classes"])
        self.assertIn("main(path)", digest["function_signatures"])
        self.assertIn("main", digest["entrypoints"])

    def test_json_digest_extracts_schema_like_fields(self) -> None:
        digest = build_artifact_digest('{"name":"demo","count":3,"ok":true}')

        self.assertEqual(digest["artifact_type"], "json")
        self.assertEqual(digest["top_level_type"], "object")
        self.assertEqual(digest["field_types"]["count"], "int")
        self.assertIn("name", digest["top_keys"])

    def test_log_and_table_digests_are_typed(self) -> None:
        log_digest = build_artifact_digest(
            "running step\nTraceback (most recent call last):\nRuntimeError: failed\nexit code 1"
        )
        table_digest = build_artifact_digest("name,count\nA,1\nB,2\n")

        self.assertEqual(log_digest["artifact_type"], "terminal_log")
        self.assertEqual(log_digest["exit_code"], 1)
        self.assertTrue(log_digest["error_patterns"])
        self.assertEqual(table_digest["artifact_type"], "csv_table")
        self.assertEqual(table_digest["column_names"], ["name", "count"])
        self.assertEqual(table_digest["sample_row_count"], 2)


if __name__ == "__main__":
    unittest.main()
