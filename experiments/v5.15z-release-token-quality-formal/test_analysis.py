from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.15z-release-token-quality-formal"
)


def _load_module(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        name,
        EXPERIMENT_DIR / filename,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _scenario_row(
    scenario_id: str,
    *,
    native_tokens: int,
    observed_tokens: int,
    managed_tokens: int,
    native_quality: float,
    managed_quality: float,
) -> dict[str, object]:
    return {
        "scenario_id": scenario_id,
        "task_counts": {
            "native": 10,
            "observed": 10,
            "managed": 10,
        },
        "provider": {
            "native": {
                "calls": 30,
                "total_tokens": native_tokens,
                "strict_delivery_count": 10,
            },
            "observed": {
                "calls": 31,
                "total_tokens": observed_tokens,
                "strict_delivery_count": 10,
            },
            "managed": {
                "calls": 32,
                "total_tokens": managed_tokens,
                "strict_delivery_count": 10,
            },
        },
        "quality": {
            "native": {
                "task_count": 10,
                "mean_score": native_quality,
                "delivery_complete_count": 10,
                "blocking_finding_count": 0,
            },
            "observed": {
                "task_count": 10,
                "mean_score": native_quality - 0.1,
                "delivery_complete_count": 10,
                "blocking_finding_count": 1,
            },
            "managed": {
                "task_count": 10,
                "mean_score": managed_quality,
                "delivery_complete_count": 10,
                "blocking_finding_count": 1,
            },
        },
        "transport": {
            "native_baseline_tokens": 1000,
            "end_to_end_collaboration_tokens": 700,
        },
        "memory": {
            "query_count": 10,
            "hit_count": 8,
            "injected_count": 7,
            "useful_hit_count": 1,
            "wrong_hit_count": 0,
            "unassessed_hit_count": 6,
        },
    }


def _write_repeat(
    runs_root: Path,
    *,
    batch_id: str,
    number: int,
    managed_adjustment: int,
) -> None:
    repeat_id = f"{batch_id}-r{number}"
    root = runs_root / repeat_id
    rows = [
        _scenario_row(
            "A",
            native_tokens=1000,
            observed_tokens=1050,
            managed_tokens=800 + managed_adjustment,
            native_quality=9.0,
            managed_quality=8.8 - (managed_adjustment / 1000),
        ),
        _scenario_row(
            "B",
            native_tokens=1000,
            observed_tokens=1100,
            managed_tokens=900 + managed_adjustment,
            native_quality=8.0,
            managed_quality=8.1 - (managed_adjustment / 1000),
        ),
    ]
    native_total = 2000
    observed_total = 2150
    managed_total = 1700 + (2 * managed_adjustment)
    _write_json(
        root / "preflight_report.json",
        {
            "summary": {"passed": True},
            "preregistration": {
                "scenarios": [
                    {"scenario_id": "A", "directory": "A"},
                    {"scenario_id": "B", "directory": "B"},
                ]
            },
            "aggregates": {
                "provider": {
                    "native": {"total_tokens": native_total},
                    "observed": {"total_tokens": observed_total},
                    "managed": {"total_tokens": managed_total},
                },
                "quality": {
                    "native": {
                        "mean_score": 8.5,
                        "delivery_complete_count": 20,
                    },
                    "observed": {
                        "mean_score": 8.4,
                        "delivery_complete_count": 20,
                    },
                    "managed": {
                        "mean_score": 8.45 - (managed_adjustment / 1000),
                        "delivery_complete_count": 20,
                    },
                },
                "transport": {
                    "native_baseline_tokens": 2000,
                    "end_to_end_collaboration_tokens": 1400,
                },
                "memory": {
                    "query_count": 20,
                    "hit_count": 16,
                    "injected_count": 14,
                    "useful_hit_count": 2,
                    "wrong_hit_count": 0,
                    "unassessed_hit_count": 12,
                },
            },
            "scenarios": rows,
        },
    )
    _write_json(
        root / "release_token_quality_formal_report.json",
        {"summary": {"passed": True}},
    )
    for scenario in ("A", "B"):
        for group, retries in (
            ("native", 0),
            ("observed", 1),
            ("managed", 2),
        ):
            _write_json(
                root / scenario / group / "sequence_result.json",
                {
                    "summary": {
                        "llm_usage": {
                            "calls": 30,
                            "retry_count": retries,
                        }
                    }
                },
            )
        _write_json(
            root / scenario / "comparison" / "quality_blind_summary.json",
            {
                "by_group": rows[0 if scenario == "A" else 1]["quality"],
                "evaluation_judge_usage": {
                    "primary": {
                        "call_count": 10,
                        "total_tokens": 100,
                        "format_retry_count": 0,
                    },
                    "technical": {
                        "call_count": 10,
                        "total_tokens": 120,
                        "format_retry_count": 1,
                    },
                },
            },
        )
        _write_json(
            root / scenario / "reports" / "managed-agentlite.json",
            {
                "token_summary": {
                    "memory_conflict_detected_count": 2,
                    "memory_conflict_resolved_count": 2,
                    "memory_unresolved_conflict_count": 0,
                    "rewrite_error_fallback_count": 0,
                },
                "review_governance_summary": {
                    "event_count": 3,
                    "failure_event_count": 1,
                },
            },
        )


def _write_ablation_run(
    runs_root: Path,
    *,
    run_id: str,
    native_quality: float,
    managed_quality: float,
    native_tokens: int,
    managed_tokens: int,
    injected: int,
) -> None:
    _write_json(
        runs_root / run_id / "preflight_report.json",
        {
            "aggregates": {
                "provider": {
                    "native": {"total_tokens": native_tokens},
                    "observed": {"total_tokens": native_tokens},
                    "managed": {"total_tokens": managed_tokens},
                },
                "quality": {
                    "native": {
                        "mean_score": native_quality,
                        "delivery_complete_count": 20,
                        "blocking_finding_count": 0,
                    },
                    "observed": {
                        "mean_score": native_quality,
                        "delivery_complete_count": 20,
                        "blocking_finding_count": 0,
                    },
                    "managed": {
                        "mean_score": managed_quality,
                        "delivery_complete_count": 20,
                        "blocking_finding_count": 1,
                    },
                },
                "memory": {
                    "query_count": injected,
                    "hit_count": injected,
                    "injected_count": injected,
                    "useful_hit_count": 0,
                    "wrong_hit_count": 0,
                    "unassessed_hit_count": injected,
                },
            }
        },
    )


class ReleaseBenchmarkAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.aggregate_module = _load_module(
            "v515z_aggregate",
            "aggregate_repeats.py",
        )
        cls.fidelity_module = _load_module(
            "v515z_fidelity",
            "analyze_fact_fidelity.py",
        )
        cls.ablation_module = _load_module(
            "v515z_ablation",
            "compare_memory_ablation.py",
        )

    def test_repeat_aggregate_preserves_scenario_and_variance_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir)
            _write_repeat(
                runs_root,
                batch_id="batch",
                number=1,
                managed_adjustment=0,
            )
            _write_repeat(
                runs_root,
                batch_id="batch",
                number=2,
                managed_adjustment=100,
            )
            report = self.aggregate_module.aggregate(
                batch_id="batch",
                repeat_count=2,
                runs_root=runs_root,
            )

        self.assertEqual(
            report["schema_version"],
            "agentlite.v515z.repeat-aggregate.v2",
        )
        self.assertEqual(set(report["by_scenario"]), {"A", "B"})
        self.assertEqual(report["pooled"]["provider_calls"]["managed"], 128)
        self.assertEqual(report["pooled"]["provider_retries"]["managed"], 8)
        self.assertEqual(report["pooled"]["memory"]["unassessed_hit_count"], 24)
        self.assertEqual(
            report["pooled"]["judge_usage"]["technical"][
                "format_retry_count"
            ],
            4,
        )
        distribution = report["repeat_statistics"][
            "provider_reduction_ratio"
        ]
        self.assertEqual(distribution["count"], 2)
        self.assertGreater(distribution["sample_stdev"], 0)
        self.assertLess(distribution["ci95_low"], distribution["ci95_high"])

    def test_fact_fidelity_localizes_generic_claim_visibility_loss(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_json(
                root / "A" / "managed" / "sequence_result.json",
                {
                    "tasks": [
                        {
                            "task_id": "A1",
                            "task_index": 1,
                            "question": (
                                "- latency_limit: 120 ms\n"
                                "- capacity: 64 GiB"
                            ),
                            "delivery_valid": True,
                            "final_answer": (
                                "latency_limit: 120 ms\ncapacity: 64 GiB"
                            ),
                        },
                        {
                            "task_id": "A2",
                            "task_index": 2,
                            "question": "- latency_limit: 95 ms",
                            "delivery_valid": True,
                            "final_answer": "latency_limit: 95 ms",
                        },
                    ]
                },
            )
            _write_json(
                root
                / "A"
                / "comparison"
                / "quality_blind_summary.json",
                {
                    "tasks": [
                        {
                            "task_id": "A2",
                            "technical_findings": {
                                "managed": [
                                    {
                                        "evidence": (
                                            "The required capacity 64 GiB "
                                            "is missing."
                                        )
                                    }
                                ]
                            },
                        }
                    ]
                },
            )
            trace_path = root / "trace.jsonl"
            trace_rows = [
                {
                    "event_type": "autogen_agent_receive",
                    "payload": {
                        "task_sequence_index": 1,
                        "decoded_messages": [
                            {
                                "content_text": (
                                    "latency_limit: 120 ms\ncapacity: 64 GiB"
                                )
                            }
                        ],
                    },
                },
                {
                    "event_type": "autogen_agent_output",
                    "payload": {
                        "task_sequence_index": 1,
                        "decoded_messages": [
                            {
                                "content_text": (
                                    "latency_limit: 120 ms\ncapacity: 64 GiB"
                                )
                            }
                        ],
                    },
                },
                {
                    "event_type": "autogen_agent_receive",
                    "payload": {
                        "task_sequence_index": 2,
                        "decoded_messages": [
                            {"content_text": "latency_limit: 95 ms"}
                        ],
                    },
                },
                {
                    "event_type": "autogen_memory_retrieval",
                    "payload": {
                        "task_sequence_index": 2,
                        "prompt_view_preview": "capacity: 64 GiB",
                    },
                },
                {
                    "event_type": "autogen_agent_output",
                    "payload": {
                        "task_sequence_index": 2,
                        "decoded_messages": [
                            {"content_text": "latency_limit: 95 ms"}
                        ],
                    },
                },
            ]
            trace_path.write_text(
                "\n".join(json.dumps(row) for row in trace_rows) + "\n",
                encoding="utf-8",
            )
            report = self.fidelity_module.analyze(
                run_root=root,
                scenario="A",
                mode="managed",
                trace_path=trace_path,
            )

        task = report["tasks"][1]
        capacity = next(
            item
            for item in task["claim_journeys"]
            if item["predicate"] == "capacity"
        )
        self.assertTrue(capacity["visibility"]["retrieved_memory"])
        self.assertFalse(capacity["visibility"]["final_answer"])
        self.assertEqual(capacity["first_visibility_loss"], "agent_outputs")
        self.assertTrue(capacity["correlated_with_audited_finding"])
        self.assertTrue(capacity["source_span"]["exact"])

    def test_memory_ablation_uses_native_control_for_difference_in_differences(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_root = Path(temp_dir)
            _write_ablation_run(
                runs_root,
                run_id="trial-memory-on-r1",
                native_quality=9.0,
                managed_quality=9.0,
                native_tokens=1000,
                managed_tokens=800,
                injected=12,
            )
            _write_ablation_run(
                runs_root,
                run_id="trial-memory-off-r1",
                native_quality=9.0,
                managed_quality=8.0,
                native_tokens=1000,
                managed_tokens=1000,
                injected=0,
            )
            report = self.ablation_module.compare(
                ablation_id="trial",
                repeat_count=1,
                runs_root=runs_root,
            )

        self.assertTrue(report["summary"]["complete"])
        self.assertAlmostEqual(
            report["difference_in_differences"]["quality_mean"],
            1.0,
        )
        self.assertEqual(
            report["difference_in_differences"]["provider_tokens"],
            -200.0,
        )

    def test_memory_ablation_switches_the_actual_feature_flag(self) -> None:
        from agent_runtime.drivers.autogen import (
            _resolve_shared_memory_enabled,
        )

        script = (
            EXPERIMENT_DIR / "run_memory_ablation.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("AGENTLITE_AUTOGEN_SHARED_MEMORY", script)
        self.assertNotIn("AGENTLITE_MEMORY_SCOPE=disabled", script)
        self.assertTrue(
            _resolve_shared_memory_enabled(
                broadcast_mode="real-rewrite",
                raw_value="1",
            )
        )
        self.assertFalse(
            _resolve_shared_memory_enabled(
                broadcast_mode="real-rewrite",
                raw_value="0",
            )
        )


if __name__ == "__main__":
    unittest.main()
