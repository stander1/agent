from __future__ import annotations

import asyncio
import json
import os
import runpy
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_GLOBALS = runpy.run_path(
    str(PROJECT_ROOT / "experiments" / "ordinary-developer-autogen" / "code_app.py")
)
COMPARE_GLOBALS = runpy.run_path(
    str(
        PROJECT_ROOT
        / "experiments"
        / "ordinary-developer-autogen"
        / "compare_stateful_runs.py"
    )
)
TEAM_TEMPLATE = (
    PROJECT_ROOT
    / "experiments"
    / "ordinary-developer-autogen"
    / "studio_team_config.template.json"
)
QUESTION_SEQUENCE = (
    PROJECT_ROOT
    / "experiments"
    / "ordinary-developer-autogen"
    / "question_A_sequence.json"
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
    def test_comparison_uses_actual_usage_and_keeps_blind_group_mapping_separate(
        self,
    ) -> None:
        compare_runs = COMPARE_GLOBALS["compare_runs"]

        def write_group(root: Path, group: str, total_tokens: int) -> None:
            root.mkdir(parents=True)
            per_task = total_tokens // 2
            tasks = []
            candidates = []
            mapping = []
            for index, task_id in enumerate(("A1", "A2"), start=1):
                usage = {
                    "calls": 3,
                    "llm_prompt_tokens": per_task - 10,
                    "llm_completion_tokens": 10,
                    "llm_total_tokens": per_task,
                    "llm_wall_time_ms": 3,
                    "retry_count": 0,
                    "by_agent": {},
                }
                tasks.append(
                    {
                        "task_id": task_id,
                        "question": f"question {task_id}",
                        "llm_usage": usage,
                        "wall_time_ms": 5,
                        "delivery_valid": True,
                        "final_answer_chars": 8,
                    }
                )
                candidate_id = f"{group}_{index}"
                candidates.append(
                    {"candidate_id": candidate_id, "answer": f"answer {index}"}
                )
                mapping.append(
                    {"candidate_id": candidate_id, "task_id": task_id}
                )
            sequence = {
                "summary": {
                    "scenario_id": "A",
                    "experiment_mode": group,
                    "same_team_instance": True,
                    "task_count": 2,
                    "valid_delivery_count": 2,
                    "wall_time_ms": 10,
                    "llm_usage": {
                        "calls": 6,
                        "llm_prompt_tokens": total_tokens - 20,
                        "llm_completion_tokens": 20,
                        "llm_total_tokens": total_tokens,
                        "llm_wall_time_ms": 6,
                        "retry_count": 0,
                        "by_agent": {},
                    },
                },
                "agent_configs": [{"name": "shared-agent"}],
                "tasks": tasks,
            }
            (root / "sequence_result.json").write_text(
                json.dumps(sequence), encoding="utf-8"
            )
            (root / "quality_blind_candidates.json").write_text(
                json.dumps({"scenario_id": "A", "candidates": candidates}),
                encoding="utf-8",
            )
            (root / "quality_blind_mapping.json").write_text(
                json.dumps({"scenario_id": "A", "mapping": mapping}),
                encoding="utf-8",
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dirs = {group: root / group for group in ("native", "observed", "managed")}
            write_group(run_dirs["native"], "native", 100)
            write_group(run_dirs["observed"], "observed", 110)
            write_group(run_dirs["managed"], "managed", 80)

            result = compare_runs(
                run_dirs=run_dirs,
                output_dir=root / "comparison",
                blind_seed=7,
            )
            blind_batch = json.loads(
                (root / "comparison" / "quality_blind_batch.json").read_text(
                    encoding="utf-8"
                )
            )
            blind_mapping = json.loads(
                (root / "comparison" / "quality_blind_mapping.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(result["summary"]["managed_vs_native"]["token_delta"], -20)
        self.assertTrue(
            result["summary"]["managed_vs_native"]["actual_llm_tokens_lower"]
        )
        self.assertEqual(len(blind_batch["tasks"][0]["candidates"]), 3)
        self.assertNotIn("group", blind_batch["tasks"][0]["candidates"][0])
        self.assertEqual(
            {item["group"] for item in blind_mapping["mapping"]},
            {"native", "observed", "managed"},
        )

    def test_stateful_sequence_reuses_one_team_and_exports_provider_usage(self) -> None:
        client_type = APP_GLOBALS["OpenAICompatibleClient"]
        result_type = APP_GLOBALS["LLMResult"]
        task_type = APP_GLOBALS["TaskSpec"]
        run_sequence = APP_GLOBALS["run_task_sequence"]

        class FakeClient:
            model = "fake-model"

            def __init__(self) -> None:
                self.calls = 0

            def complete(self, messages: list[dict[str, str]]) -> object:
                self.calls += 1
                role_index = (self.calls - 1) % 3
                if role_index == 0:
                    content = "规划"
                elif role_index == 1:
                    content = "完整草案"
                else:
                    content = "完整最终答案\nFINAL_ANSWER_READY"
                return result_type(
                    content=content,
                    usage={
                        "prompt_tokens": len(messages[-1]["content"]),
                        "completion_tokens": len(content),
                        "total_tokens": len(messages[-1]["content"]) + len(content),
                    },
                    wall_time_ms=1,
                )

        fake_client = FakeClient()
        agent_configs = [
            {"name": "planner", "description": "planner", "system_prompt": "plan"},
            {"name": "writer", "description": "writer", "system_prompt": "write"},
            {"name": "reviewer", "description": "reviewer", "system_prompt": "review"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            client_type,
            "from_env",
            return_value=fake_client,
        ):
            payload = asyncio.run(
                run_sequence(
                    scenario_id="test",
                    tasks=[
                        task_type(task_id="A1", question="第一问"),
                        task_type(task_id="A2", question="基于上一问继续"),
                    ],
                    agent_configs=agent_configs,
                    output_dir=Path(temp_dir),
                    temperature=0.0,
                    max_turns=6,
                    experiment_mode="native",
                )
            )
            usage_rows = [
                json.loads(line)
                for line in (Path(temp_dir) / "llm_usage.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            blind_payload = json.loads(
                (Path(temp_dir) / "quality_blind_candidates.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertTrue(payload["summary"]["same_team_instance"])
        self.assertEqual(payload["summary"]["task_count"], 2)
        self.assertEqual(payload["summary"]["valid_delivery_count"], 2)
        self.assertEqual(payload["summary"]["llm_usage"]["calls"], 6)
        self.assertEqual([row["task_id"] for row in usage_rows[:3]], ["A1"] * 3)
        self.assertEqual([row["task_id"] for row in usage_rows[3:]], ["A2"] * 3)
        self.assertGreater(
            usage_rows[3]["history_message_count"],
            usage_rows[0]["history_message_count"],
        )
        self.assertNotIn("experiment_mode", blind_payload["candidates"][0])

    def test_question_sequence_contains_ordered_a1_to_a10_tasks(self) -> None:
        loader = APP_GLOBALS["_load_question_sequence"]

        scenario_id, tasks = loader(QUESTION_SEQUENCE)

        self.assertEqual(scenario_id, "A")
        self.assertEqual([task.task_id for task in tasks], [f"A{i}" for i in range(1, 11)])
        self.assertIn("3000 元", tasks[0].question)
        self.assertIn("决策日志", tasks[-1].question)

    def test_cost_logger_truncates_old_rows_and_summarizes_each_task(self) -> None:
        logger_type = APP_GLOBALS["CostLogger"]
        with tempfile.TemporaryDirectory() as temp_dir:
            usage_path = Path(temp_dir) / "llm_usage.jsonl"
            usage_path.write_text('{"stale": true}\n', encoding="utf-8")
            logger = logger_type(Path(temp_dir))
            logger.add(
                {
                    "task_id": "A1",
                    "agent": "planner",
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                    "wall_time_ms": 7,
                    "retry_count": 0,
                }
            )
            logger.add(
                {
                    "task_id": "A2",
                    "agent": "writer",
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
                    "wall_time_ms": 9,
                    "retry_count": 1,
                }
            )

            summary = logger.summary()

            self.assertNotIn("stale", usage_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["llm_total_tokens"], 20)
            self.assertEqual(summary["by_task"]["A1"]["llm_total_tokens"], 5)
            self.assertEqual(summary["by_task"]["A2"]["retry_count"], 1)

    def test_final_delivery_requires_reviewer_exact_last_line_marker(self) -> None:
        validator = APP_GLOBALS["_is_valid_final_delivery"]
        strip_marker = APP_GLOBALS["_strip_done_token"]

        self.assertTrue(
            validator(
                source="reviewer",
                content="完整最终答案\n\nFINAL_ANSWER_READY",
            )
        )
        self.assertFalse(
            validator(
                source="writer",
                content="完整最终答案\nFINAL_ANSWER_READY",
            )
        )
        self.assertFalse(
            validator(
                source="reviewer",
                content="FINAL_ANSWER_READY 只是审查说明",
            )
        )
        self.assertEqual(
            strip_marker("完整最终答案\n\nFINAL_ANSWER_READY\n"),
            "完整最终答案",
        )

    def test_studio_team_template_is_memory_aware_and_secret_free(self) -> None:
        payload = json.loads(TEAM_TEMPLATE.read_text(encoding="utf-8"))
        participants = payload["config"]["participants"]

        self.assertEqual(
            [item["config"]["name"] for item in participants],
            ["planner", "writer", "reviewer"],
        )
        self.assertEqual(payload["config"]["max_turns"], 6)
        self.assertEqual(
            payload["config"]["termination_condition"]["provider"],
            "agent_runtime.adapters.autogen_termination.ReviewerFinalTextTermination",
        )
        self.assertEqual(
            payload["config"]["termination_condition"]["config"]["marker"],
            "FINAL_ANSWER_READY",
        )
        self.assertEqual(
            payload["config"]["termination_condition"]["config"]["source"],
            "reviewer",
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

        from agent_runtime.adapters.autogen_termination import (
            ReviewerFinalTextTermination,
        )

        termination = ReviewerFinalTextTermination.load_component(
            payload["config"]["termination_condition"]
        )
        self.assertIsInstance(termination, ReviewerFinalTextTermination)

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
