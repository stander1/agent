import unittest

from agent_runtime.reliability.contract_guard import (
    ARTIFACT_CLOSE,
    ARTIFACT_OPEN,
    CONTROL_CLOSE,
    CONTROL_OPEN,
    ContractContext,
    guard_agent_output,
    parse_and_repair_json,
)
from agent_runtime.reliability.provider_guard import (
    ProviderResponseError,
    normalize_provider_response,
)
from agent_runtime.reliability.structured_output_guard import (
    aggregate_attempt_usage,
    guard_structured_json_object,
    render_pruned_json_retry_prompt,
)


class ProviderGuardTest(unittest.TestCase):
    def test_unwraps_single_item_list_response(self) -> None:
        response = normalize_provider_response(
            [
                {
                    "model": "mimo-v2.5",
                    "choices": [
                        {"message": {"content": "正常内容"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                }
            ],
            default_model="mimo-v2.5",
        )

        self.assertEqual(response.content, "正常内容")
        self.assertEqual(response.report.status, "repaired")
        self.assertIn("unwrap_single_item_list", response.report.repair_actions)
        self.assertEqual(response.usage["total_tokens"], 5)

    def test_extracts_top_level_content_before_retry(self) -> None:
        response = normalize_provider_response(
            {"content": "简化响应内容", "usage": {"input_tokens": 4, "output_tokens": 5}},
            default_model="mimo-v2.5",
        )

        self.assertEqual(response.content, "简化响应内容")
        self.assertEqual(response.report.normalized_format, "top_level_content")
        self.assertEqual(response.usage["total_tokens"], 9)

    def test_empty_content_is_unrecoverable(self) -> None:
        with self.assertRaises(ProviderResponseError):
            normalize_provider_response(
                {"choices": [{"message": {"content": ""}}]},
                default_model="mimo-v2.5",
            )


class ContractGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ContractContext(
            task_id="A10",
            agent_id="writer",
            role="WriterAgent",
            next_action="reviewer",
            artifact_ref="cold://A10/1/writer/artifact",
        )

    def test_repairs_markdown_json_and_fills_defaults(self) -> None:
        raw = (
            f"{CONTROL_OPEN}\n"
            "```json\n"
            "{'msg_type':'agent_output','task_id':'A10','from':'writer',"
            "'action_completed':'writer_completed','memory_card':{'summary':'摘要','tags':['A'],},"
            "'claim_cards':[],}\n"
            "```\n"
            f"{CONTROL_CLOSE}\n"
            f"{ARTIFACT_OPEN}\n正文结果\n{ARTIFACT_CLOSE}"
        )
        result = guard_agent_output(raw, self.context)

        self.assertTrue(result.schema_valid)
        self.assertEqual(result.contract_status, "repaired")
        self.assertEqual(result.artifact, "正文结果")
        self.assertIn("default_handoff_suggestion", result.repair_actions)
        self.assertIn("default_cost_report", result.repair_actions)

    def test_missing_control_degrades_without_schema_valid(self) -> None:
        result = guard_agent_output("只有普通正文，没有控制头。", self.context)

        self.assertFalse(result.schema_valid)
        self.assertEqual(result.contract_status, "degraded_fallback")
        self.assertTrue(result.retry_required)
        self.assertEqual(result.control["allowed_next_step"], "review_or_retry_only")

    def test_naked_retry_json_can_be_extracted(self) -> None:
        result = guard_agent_output(
            '{"msg_type":"agent_output","task_id":"A10","from":"writer",'
            '"action_completed":"writer_completed","memory_card":{"summary":"摘要"},'
            '"claim_cards":[],"handoff_suggestion":{"next_action":"reviewer"},'
            '"cost_report":{}}',
            self.context,
        )

        self.assertTrue(result.schema_valid)
        self.assertIn("extract_json_without_control_tag", result.repair_actions)

    def test_parse_and_repair_json_handles_trailing_comma(self) -> None:
        parsed, actions, errors = parse_and_repair_json('{"a": 1,}')

        self.assertEqual(parsed, {"a": 1})
        self.assertIn("deterministic_json_repair", actions)
        self.assertEqual(errors, [])


class StructuredOutputGuardTest(unittest.TestCase):
    def test_repairs_fenced_json_before_retry(self) -> None:
        result = guard_structured_json_object(
            'prefix\n```json\n{"task_id":"T1","evaluations":[],}\n```'
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.parsed["task_id"], "T1")
        self.assertIn("extract_json_block", result.repair_actions)
        self.assertIn("deterministic_json_repair", result.repair_actions)

    def test_pruned_retry_contains_contract_not_full_task_history(self) -> None:
        prompt = render_pruned_json_retry_prompt(
            task_id="T8",
            candidate_ids=["c1", "c2", "c3"],
            invalid_response='{"task_id":"T8","evaluations":[',
            validation_error="unterminated array",
            evaluation_fields=["total", "delivery_usable"],
            response_kind="technical_blind_audit",
        )

        self.assertIn('"context_mode": "pruned"', prompt)
        self.assertIn('"candidate_ids"', prompt)
        self.assertIn("unterminated array", prompt)
        self.assertNotIn("连续任务历史", prompt)

    def test_attempt_usage_includes_failed_format_calls(self) -> None:
        usage = aggregate_attempt_usage(
            [
                {
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                    }
                },
                {
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 10,
                        "total_tokens": 30,
                    }
                },
            ]
        )

        self.assertEqual(
            usage,
            {
                "prompt_tokens": 120,
                "completion_tokens": 60,
                "total_tokens": 180,
            },
        )


if __name__ == "__main__":
    unittest.main()
