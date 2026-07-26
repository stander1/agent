from __future__ import annotations

import asyncio
import json
import os
import runpy
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
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
JUDGE_GLOBALS = runpy.run_path(
    str(
        PROJECT_ROOT
        / "experiments"
        / "ordinary-developer-autogen"
        / "judge_stateful_blind_batch.py"
    )
)
TECHNICAL_JUDGE_GLOBALS = runpy.run_path(
    str(
        PROJECT_ROOT
        / "experiments"
        / "ordinary-developer-autogen"
        / "judge_stateful_technical_blind_batch.py"
    )
)
SUMMARIZE_GLOBALS = runpy.run_path(
    str(
        PROJECT_ROOT
        / "experiments"
        / "ordinary-developer-autogen"
        / "summarize_stateful_blind_scores.py"
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
    def test_blind_judges_use_deterministic_json_repair(self) -> None:
        raw = (
            "```json\n"
            '{"task_id":"T1","evaluations":[],"best_candidate_ids":[],}'
            "\n```"
        )
        for globals_ in (
            JUDGE_GLOBALS,
            TECHNICAL_JUDGE_GLOBALS,
        ):
            parsed = globals_["json_from_response"](raw)
            self.assertEqual(parsed["task_id"], "T1")

    def test_technical_judge_uses_layered_retry_and_counts_all_usage(
        self,
    ) -> None:
        candidate_ids = ["c1", "c2", "c3"]
        valid = {
            "task_id": "T1",
            "evaluations": [
                {
                    "candidate_id": candidate_id,
                    "constraint_fidelity": 2,
                    "technical_correctness": 4,
                    "internal_consistency": 2,
                    "executability": 2,
                    "delivery_usable": True,
                    "findings": [],
                    "strengths": ["valid"],
                }
                for candidate_id in candidate_ids
            ],
            "best_candidate_ids": candidate_ids,
            "summary": "equivalent",
        }
        calls: list[dict[str, str]] = []

        class FakeClient:
            def __init__(self, config: object) -> None:
                del config

            def complete(
                self,
                *,
                system_prompt: str,
                user_prompt: str,
            ) -> SimpleNamespace:
                calls.append(
                    {
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                    }
                )
                if len(calls) == 1:
                    content = '{"task_id":"T1","evaluations":['
                    usage = {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    }
                elif len(calls) == 2:
                    content = json.dumps(
                        {
                            "task_id": "T1",
                            "evaluations": [],
                            "best_candidate_ids": [],
                            "summary": "",
                        }
                    )
                    usage = {
                        "prompt_tokens": 30,
                        "completion_tokens": 15,
                        "total_tokens": 45,
                    }
                else:
                    content = json.dumps(valid)
                    usage = {
                        "prompt_tokens": 30,
                        "completion_tokens": 15,
                        "total_tokens": 45,
                    }
                return SimpleNamespace(
                    content=content,
                    usage=usage,
                    latency_ms=10.0,
                    model="fake-model",
                    raw_finish_reason="stop",
                    provider_guard={"status": "valid"},
                )

        batch = {
            "tasks": [
                {
                    "task_id": "T1",
                    "question": "UNIQUE_FULL_HISTORY_MARKER",
                    "candidates": [
                        {
                            "candidate_id": candidate_id,
                            "track_id": candidate_id,
                            "previous_answer": "",
                            "answer": f"answer-{candidate_id}",
                        }
                        for candidate_id in candidate_ids
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_path = root / "batch.json"
            output_path = root / "technical.json"
            batch_path.write_text(
                json.dumps(batch),
                encoding="utf-8",
            )
            argv = [
                "judge_stateful_technical_blind_batch.py",
                "--batch",
                str(batch_path),
                "--output",
                str(output_path),
                "--config",
                str(
                    PROJECT_ROOT / "configs" / "llm.mimo.example.json"
                ),
                "--format-retries",
                "2",
            ]
            with patch.dict(
                TECHNICAL_JUDGE_GLOBALS["main"].__globals__,
                {"OpenAICompatibleChatClient": FakeClient},
            ), patch.object(sys, "argv", argv):
                result = TECHNICAL_JUDGE_GLOBALS["main"]()

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 3)
        self.assertIn('"context_mode": "pruned"', calls[1]["user_prompt"])
        self.assertNotIn(
            "UNIQUE_FULL_HISTORY_MARKER",
            calls[1]["user_prompt"],
        )
        self.assertIn(
            "UNIQUE_FULL_HISTORY_MARKER",
            calls[2]["user_prompt"],
        )
        self.assertIn("压缩说明", calls[2]["user_prompt"])
        row = payload["results"][0]
        self.assertEqual(row["format_retry_count"], 2)
        self.assertEqual(
            [item["mode"] for item in row["judge_attempts"]],
            [
                "initial_audit",
                "pruned_format_repair",
                "concise_full_reaudit",
            ],
        )
        self.assertEqual(row["judge_usage"]["total_tokens"], 210)
        self.assertEqual(row["judge_latency_ms"], 30.0)

    def test_blind_judges_validate_and_reuse_task_checkpoints(self) -> None:
        tasks = [
            {
                "task_id": "T1",
                "question": "first",
                "candidates": [
                    {"candidate_id": "c1"},
                    {"candidate_id": "c2"},
                ],
            },
            {
                "task_id": "T2",
                "question": "second",
                "candidates": [
                    {"candidate_id": "d1"},
                    {"candidate_id": "d2"},
                ],
            },
        ]
        checkpoint = {
            "results": [
                {
                    "task_id": "T1",
                    "evaluations": [
                        {"candidate_id": "c1"},
                        {"candidate_id": "c2"},
                    ],
                }
            ]
        }

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "scores.json"
            output.write_text(
                json.dumps(checkpoint),
                encoding="utf-8",
            )
            for globals_ in (
                JUDGE_GLOBALS,
                TECHNICAL_JUDGE_GLOBALS,
            ):
                loaded = globals_["load_checkpoint_results"](
                    output,
                    tasks=tasks,
                )
                self.assertEqual(
                    [item["task_id"] for item in loaded],
                    ["T1"],
                )

            checkpoint["results"][0]["evaluations"] = [
                {"candidate_id": "wrong"}
            ]
            output.write_text(
                json.dumps(checkpoint),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Candidate mismatch"):
                JUDGE_GLOBALS["load_checkpoint_results"](
                    output,
                    tasks=tasks,
                )

    def test_technical_judge_caps_scores_and_blocks_high_severity(self) -> None:
        normalize_result = TECHNICAL_JUDGE_GLOBALS["normalize_result"]
        parsed = {
            "task_id": "T1",
            "evaluations": [
                {
                    "candidate_id": "c1",
                    "constraint_fidelity": 2,
                    "technical_correctness": 4,
                    "internal_consistency": 2,
                    "executability": 2,
                    "delivery_usable": True,
                    "findings": [
                        {
                            "severity": "medium",
                            "evidence": "a concrete command",
                            "issue": "the command uses the wrong option",
                            "repair": "replace the option",
                        }
                    ],
                    "strengths": [],
                },
                {
                    "candidate_id": "c2",
                    "constraint_fidelity": 2,
                    "technical_correctness": 4,
                    "internal_consistency": 2,
                    "executability": 2,
                    "delivery_usable": True,
                    "findings": [
                        {
                            "severity": "high",
                            "evidence": "destructive operation",
                            "issue": "the operation can lose data",
                            "repair": "use a verified non-destructive sequence",
                        }
                    ],
                    "strengths": [],
                },
                {
                    "candidate_id": "c3",
                    "constraint_fidelity": 2,
                    "technical_correctness": 4,
                    "internal_consistency": 2,
                    "executability": 2,
                    "delivery_usable": True,
                    "findings": [],
                    "strengths": [],
                },
            ],
        }

        result = normalize_result(
            parsed,
            task_id="T1",
            candidate_ids=["c1", "c2", "c3"],
        )

        self.assertEqual([row["total"] for row in result["evaluations"]], [8, 6, 10])
        self.assertTrue(result["evaluations"][0]["delivery_usable"])
        self.assertFalse(result["evaluations"][1]["delivery_usable"])
        self.assertEqual(result["best_candidate_ids"], ["c3"])

    def test_quality_summary_combines_primary_and_technical_blind_scores(
        self,
    ) -> None:
        summarize_scores = SUMMARIZE_GLOBALS["summarize_scores"]
        groups = ("native", "observed", "managed")
        candidates = ("c1", "c2", "c3")
        primary_rows = []
        technical_rows = []
        for candidate_id in candidates:
            primary_rows.append(
                {
                    "candidate_id": candidate_id,
                    "task_completion": 4,
                    "context_retention": 3,
                    "correctness_consistency": 2,
                    "clarity_actionability": 1,
                    "total": 10,
                    "delivery_complete": True,
                }
            )
            technical_total = {"c1": 8, "c2": 6, "c3": 10}[candidate_id]
            blocking = candidate_id == "c2"
            technical_rows.append(
                {
                    "candidate_id": candidate_id,
                    "total": technical_total,
                    "delivery_usable": not blocking,
                    "finding_count": int(candidate_id != "c3"),
                    "blocking_finding_count": int(blocking),
                    "findings": [] if candidate_id == "c3" else [{"severity": "high" if blocking else "medium"}],
                }
            )
        scores = {
            "frozen_before_unblinding": True,
            "results": [
                {
                    "task_id": "T1",
                    "evaluations": primary_rows,
                    "summary": "",
                    "judge_usage": {
                        "prompt_tokens": 80,
                        "completion_tokens": 20,
                        "total_tokens": 100,
                    },
                    "judge_latency_ms": 12,
                }
            ],
        }
        technical_scores = {
            "frozen_before_unblinding": True,
            "results": [
                {
                    "task_id": "T1",
                    "evaluations": technical_rows,
                    "summary": "",
                    "judge_usage": {
                        "prompt_tokens": 90,
                        "completion_tokens": 30,
                        "total_tokens": 120,
                    },
                    "judge_latency_ms": 15,
                }
            ],
        }
        mapping = {
            "scenario_id": "technical-test",
            "mapping": [
                {
                    "task_id": "T1",
                    "candidate_id": candidate_id,
                    "group": group,
                }
                for candidate_id, group in zip(candidates, groups)
            ],
        }

        result = summarize_scores(
            scores=scores,
            mapping=mapping,
            technical_scores=technical_scores,
        )

        self.assertTrue(result["technical_review_applied"])
        self.assertEqual(
            result["tasks"][0]["scores"],
            {"native": 8, "observed": 6, "managed": 10},
        )
        self.assertEqual(result["tasks"][0]["winners"], ["managed"])
        self.assertFalse(result["tasks"][0]["delivery_complete"]["observed"])
        self.assertEqual(result["by_group"]["native"]["primary_mean_score"], 10)
        self.assertEqual(result["by_group"]["native"]["technical_mean_score"], 8)
        self.assertEqual(
            result["evaluation_judge_usage"]["primary"]["total_tokens"], 100
        )
        self.assertEqual(
            result["evaluation_judge_usage"]["technical"]["total_tokens"],
            120,
        )
        self.assertFalse(
            result["evaluation_judge_usage"][
                "included_in_runtime_collaboration_cost"
            ]
        )

    def test_blind_judge_normalizes_scores_and_requires_every_candidate(
        self,
    ) -> None:
        normalize_result = JUDGE_GLOBALS["normalize_result"]
        parsed = {
            "task_id": "A1",
            "evaluations": [
                {
                    "candidate_id": candidate_id,
                    "task_completion": 4,
                    "context_retention": 3,
                    "correctness_consistency": 2,
                    "clarity_actionability": 1,
                    "total": 999,
                    "delivery_complete": True,
                    "strengths": [],
                    "risks": [],
                }
                for candidate_id in ("c1", "c2", "c3")
            ],
        }
        result = normalize_result(
            parsed,
            task_id="A1",
            candidate_ids=["c1", "c2", "c3"],
        )
        self.assertEqual([row["total"] for row in result["evaluations"]], [10, 10, 10])
        self.assertEqual(result["best_candidate_ids"], ["c1", "c2", "c3"])

        parsed["evaluations"].pop()
        with self.assertRaisesRegex(ValueError, "every candidate"):
            normalize_result(
                parsed,
                task_id="A1",
                candidate_ids=["c1", "c2", "c3"],
            )

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
            usage_rows = []
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
                prompt_base = {"native": 20, "observed": 22, "managed": 12}[group]
                for agent in ("analyst", "composer", "auditor"):
                    prompt_tokens = prompt_base + index
                    usage_rows.append(
                        {
                            "task_id": task_id,
                            "agent": agent,
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": 3,
                            "total_tokens": prompt_tokens + 3,
                        }
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
            (root / "llm_usage.jsonl").write_text(
                "\n".join(json.dumps(row) for row in usage_rows) + "\n",
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
                require_immutable=False,
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
        normalized = result["summary"]["normalized_common_calls"]
        self.assertTrue(normalized["available"])
        self.assertEqual(normalized["common_call_count"], 6)
        self.assertEqual(
            normalized["groups"]["managed"]["unmatched_call_count"],
            0,
        )
        self.assertLess(
            normalized["groups"]["managed"]["llm_prompt_tokens"],
            normalized["groups"]["native"]["llm_prompt_tokens"],
        )
        self.assertEqual(
            normalized["groups"]["managed"]["llm_prompt_tokens"],
            81,
        )
        self.assertEqual(
            normalized["groups"]["managed"]["llm_total_tokens"],
            99,
        )
        self.assertEqual(len(blind_batch["tasks"][0]["candidates"]), 3)
        self.assertNotIn("group", blind_batch["tasks"][0]["candidates"][0])
        first_tracks = {
            item["track_id"] for item in blind_batch["tasks"][0]["candidates"]
        }
        second_tracks = {
            item["track_id"] for item in blind_batch["tasks"][1]["candidates"]
        }
        self.assertEqual(first_tracks, second_tracks)
        self.assertTrue(
            all(
                item["previous_answer"] == ""
                for item in blind_batch["tasks"][0]["candidates"]
            )
        )
        self.assertEqual(
            {item["previous_answer"] for item in blind_batch["tasks"][1]["candidates"]},
            {"answer 1"},
        )
        self.assertEqual(
            {item["group"] for item in blind_mapping["mapping"]},
            {"native", "observed", "managed"},
        )
        self.assertTrue(
            all(item["track_id"].startswith("track_") for item in blind_mapping["mapping"])
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
                    content = (
                        "完整最终答案：已直接回应当前问题，并给出清晰、完整、可执行的交付内容。"
                        "这里保留足够正文用于验证最终产物不是一句审查结论或流程说明。"
                        "用户可以直接使用这份结果继续下一项任务。\nFINAL_ANSWER_READY"
                    )
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

    def test_invalid_reviewer_delivery_is_recorded_without_semantic_retry(self) -> None:
        client_type = APP_GLOBALS["OpenAICompatibleClient"]
        result_type = APP_GLOBALS["LLMResult"]
        run_team = APP_GLOBALS["run_team"]

        class FakeClient:
            model = "fake-model"

            def __init__(self) -> None:
                self.calls = 0

            def complete(self, messages: list[dict[str, str]]) -> object:
                self.calls += 1
                replies = {
                    1: "规划：先核对预算，再生成完整预算表。",
                    2: "草案包含交通、住宿、餐饮和活动四类费用。",
                    3: (
                        "审查意见：当前草案缺少可核算明细，请 Writer 继续补充。\n"
                        "FINAL_ANSWER_READY"
                    ),
                }
                content = replies[self.calls]
                return result_type(
                    content=content,
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 10,
                        "total_tokens": 20,
                    },
                    wall_time_ms=1,
                )

        fake_client = FakeClient()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            client_type,
            "from_env",
            return_value=fake_client,
        ):
            payload = asyncio.run(
                run_team(
                    question="请优化旅行预算并交付完整预算表",
                    agent_configs=APP_GLOBALS["DEFAULT_AGENTS"],
                    output_dir=Path(temp_dir),
                    temperature=0.0,
                    max_turns=3,
                )
            )

        self.assertFalse(payload["summary"]["delivery_valid"])
        self.assertEqual(payload["summary"]["delivery_status"], "task_failed")
        self.assertEqual(payload["summary"]["semantic_retry_count"], 0)
        self.assertEqual(payload["raw"]["llm_usage"]["calls"], 3)
        self.assertEqual(fake_client.calls, 3)
        self.assertEqual(payload["raw"]["messages"][-1]["source"], "reviewer")
        self.assertIn(
            "请 Writer 继续补充",
            payload["raw"]["messages"][-1]["content"],
        )

    def test_question_sequence_contains_ordered_a1_to_a10_tasks(self) -> None:
        loader = APP_GLOBALS["_load_question_sequence"]

        scenario_id, tasks = loader(QUESTION_SEQUENCE)

        self.assertEqual(scenario_id, "A")
        self.assertEqual([task.task_id for task in tasks], [f"A{i}" for i in range(1, 11)])
        self.assertIn("3000 元", tasks[0].question)
        self.assertIn("决策日志", tasks[-1].question)

    def test_cost_logger_refuses_to_overwrite_old_rows(self) -> None:
        logger_type = APP_GLOBALS["CostLogger"]
        with tempfile.TemporaryDirectory() as temp_dir:
            usage_path = Path(temp_dir) / "llm_usage.jsonl"
            usage_path.write_text('{"stale": true}\n', encoding="utf-8")
            with self.assertRaises(FileExistsError):
                logger_type(Path(temp_dir))

            self.assertEqual(
                usage_path.read_text(encoding="utf-8"),
                '{"stale": true}\n',
            )

    def test_cost_logger_summarizes_each_task(self) -> None:
        logger_type = APP_GLOBALS["CostLogger"]
        with tempfile.TemporaryDirectory() as temp_dir:
            logger = logger_type(Path(temp_dir))
            usage_path = Path(temp_dir) / "llm_usage.jsonl"
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
                content=(
                    "完整最终答案：已直接回应当前问题，并给出清晰、完整、可执行的交付内容。"
                    "这里保留足够正文用于验证最终产物不是一句审查结论或流程说明。"
                    "用户可以直接使用这份结果继续下一项任务。\nFINAL_ANSWER_READY"
                ),
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
        self.assertFalse(
            validator(
                source="reviewer",
                content=(
                    "审查意见：当前草案缺少完整预算表，请 Writer 继续补充后再提交。\n"
                    "FINAL_ANSWER_READY"
                ),
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
        self.assertEqual(payload["config"]["max_turns"], 9)
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
