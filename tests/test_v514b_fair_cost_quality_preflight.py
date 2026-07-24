from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT / "experiments" / "v5.14b-fair-cost-quality-preflight"
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


def _scenario_evidence(scenario_id: str) -> dict[str, object]:
    groups = {
        "native": {
            "task_count": 3,
            "valid_delivery_count": 3,
            "llm_call_count": 9,
            "llm_prompt_tokens": 7000,
            "llm_completion_tokens": 3000,
            "llm_total_tokens": 10000,
            "retry_count": 0,
            "wall_time_ms": 100000,
        },
        "observed": {
            "task_count": 3,
            "valid_delivery_count": 3,
            "llm_call_count": 9,
            "llm_prompt_tokens": 7100,
            "llm_completion_tokens": 3100,
            "llm_total_tokens": 10200,
            "retry_count": 0,
            "wall_time_ms": 101000,
        },
        "managed": {
            "task_count": 3,
            "valid_delivery_count": 3,
            "llm_call_count": 9,
            "llm_prompt_tokens": 5600,
            "llm_completion_tokens": 2400,
            "llm_total_tokens": 8000,
            "retry_count": 0,
            "wall_time_ms": 98000,
        },
    }
    quality_groups = {
        "native": {
            "task_count": 3,
            "mean_score": 9.0,
            "delivery_complete_count": 3,
            "blocking_finding_count": 0,
        },
        "observed": {
            "task_count": 3,
            "mean_score": 8.9,
            "delivery_complete_count": 3,
            "blocking_finding_count": 0,
        },
        "managed": {
            "task_count": 3,
            "mean_score": 8.8,
            "delivery_complete_count": 3,
            "blocking_finding_count": 0,
        },
    }
    return {
        "comparison": {
            "summary": {
                "scenario_id": scenario_id,
                "run_parameters": {
                    "temperature": 0,
                    "max_turns": 9,
                    "provider_model": "mimo-v2.5",
                    "provider_base_url": (
                        "https://token-plan-cn.xiaomimimo.com/v1"
                    ),
                },
                "groups": groups,
                "archive_evidence": {
                    group: {"status": "verified"} for group in groups
                },
            }
        },
        "quality": {
            "technical_review_applied": True,
            "by_group": quality_groups,
        },
        "observed_report": {
            "experiment_binding_verified": True,
            "hooks_active": True,
            "token_summary": {
                "rewrite_applied_event_count": 0,
                "rewrite_error_fallback_count": 0,
            },
        },
        "managed_report": {
            "experiment_binding_verified": True,
            "hooks_active": True,
            "state_summary": {
                "state_count": 9,
                "state_type_counts": {"artifact_state": 9},
                "payload_kind_counts": {"structured_non_text": 9},
                "structured_non_text_count": 9,
                "contains_embedding_refs_count": 0,
                "total_size_bytes": 4096,
            },
            "token_summary": {
                "native_baseline_tokens": 6000,
                "end_to_end_collaboration_tokens": 4800,
                "direct_message_tokens": 1600,
                "prompt_view_tokens": 2000,
                "retrieved_memory_tokens": 1200,
                "control_llm_tokens": 0,
                "retry_tokens": 0,
                "memory_query_count": 12,
                "memory_hit_count": 8,
                "memory_injected_count": 6,
                "useful_memory_hit_count": 4,
                "wrong_memory_hit_count": 0,
                "unassessed_memory_hit_count": 2,
                "rewrite_applied_event_count": 9,
                "rewrite_error_fallback_count": 0,
            },
        },
    }


class FairCostQualityPreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verify = _load_module("v514b_verify", "verify_preflight.py")
        cls.preregistration = json.loads(
            (EXPERIMENT_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )

    def _passing_evidence(self) -> dict[str, dict[str, object]]:
        return {
            "A-preflight": _scenario_evidence("A-preflight"),
            "B-preflight": _scenario_evidence("B-preflight"),
        }

    def test_preregistered_fixture_passes_both_pipeline_and_targets(
        self,
    ) -> None:
        report = self.verify.evaluate(
            preregistration=self.preregistration,
            evidence_by_scenario=self._passing_evidence(),
        )
        self.assertTrue(report["summary"]["passed"])
        self.assertTrue(report["summary"]["ready_for_formal_run"])
        self.assertAlmostEqual(
            report["aggregates"]["provider_reduction_ratio"],
            0.2,
        )
        self.assertAlmostEqual(
            report["aggregates"]["transport_reduction_ratio"],
            0.2,
        )

    def test_provider_cost_target_cannot_be_hidden_by_transport_savings(
        self,
    ) -> None:
        evidence = self._passing_evidence()
        for scenario in evidence.values():
            managed = scenario["comparison"]["summary"]["groups"]["managed"]
            managed["llm_total_tokens"] = 11000
            managed["llm_prompt_tokens"] = 8000
            managed["llm_completion_tokens"] = 3000
        report = self.verify.evaluate(
            preregistration=self.preregistration,
            evidence_by_scenario=evidence,
        )
        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks["managed_provider_tokens_meet_reduction_target"]["passed"]
        )
        self.assertTrue(
            checks["managed_transport_tokens_meet_reduction_target"]["passed"]
        )
        self.assertFalse(report["summary"]["ready_for_formal_run"])

    def test_quality_noninferiority_uses_frozen_margin(self) -> None:
        evidence = self._passing_evidence()
        for scenario in evidence.values():
            scenario["quality"]["by_group"]["managed"]["mean_score"] = 8.6
        report = self.verify.evaluate(
            preregistration=self.preregistration,
            evidence_by_scenario=evidence,
        )
        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(checks["managed_quality_is_noninferior"]["passed"])
        self.assertTrue(report["summary"]["evidence_pipeline_passed"])
        self.assertFalse(report["summary"]["preliminary_targets_met"])

    def test_observed_rewrite_is_an_evidence_failure(self) -> None:
        evidence = copy.deepcopy(self._passing_evidence())
        evidence["B-preflight"]["observed_report"]["token_summary"][
            "rewrite_applied_event_count"
        ] = 1
        report = self.verify.evaluate(
            preregistration=self.preregistration,
            evidence_by_scenario=evidence,
        )
        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks["B-preflight:observed_mode_is_non_modifying"]["passed"]
        )
        self.assertFalse(report["summary"]["evidence_pipeline_passed"])

    def test_missing_reliability_metric_is_not_treated_as_zero(self) -> None:
        evidence = copy.deepcopy(self._passing_evidence())
        del evidence["A-preflight"]["managed_report"]["token_summary"][
            "rewrite_error_fallback_count"
        ]
        report = self.verify.evaluate(
            preregistration=self.preregistration,
            evidence_by_scenario=evidence,
        )
        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks[
                "A-preflight:managed_reliability_metrics_present"
            ]["passed"]
        )
        self.assertFalse(report["summary"]["evidence_pipeline_passed"])

    def test_missing_real_state_evidence_is_not_inferred_from_prompt(
        self,
    ) -> None:
        evidence = copy.deepcopy(self._passing_evidence())
        evidence["B-preflight"]["managed_report"]["state_summary"] = {
            "state_count": 0,
            "state_type_counts": {},
            "payload_kind_counts": {},
            "structured_non_text_count": 0,
            "contains_embedding_refs_count": 0,
            "total_size_bytes": 0,
        }
        report = self.verify.evaluate(
            preregistration=self.preregistration,
            evidence_by_scenario=evidence,
        )
        checks = {item["name"]: item for item in report["checks"]}
        self.assertFalse(
            checks[
                "B-preflight:structured_non_text_state_observed"
            ]["passed"]
        )
        self.assertEqual(
            report["scenarios"][1]["state"]["state_type_counts"],
            {},
        )
        self.assertFalse(report["summary"]["evidence_pipeline_passed"])

    def test_task_and_agent_files_are_explicit_experiment_inputs(self) -> None:
        self.assertIn(
            "state_type_counts",
            self.preregistration["state_evidence_metrics"],
        )
        self.assertIn(
            "structured_non_text_count",
            self.preregistration["state_evidence_metrics"],
        )
        for filename in (
            "question_A_preflight.json",
            "question_B_preflight.json",
            "agent_config_B.json",
        ):
            value = json.loads(
                (EXPERIMENT_DIR / filename).read_text(encoding="utf-8")
            )
            self.assertTrue(value)
        for filename in ("question_A_preflight.json", "question_B_preflight.json"):
            payload = json.loads(
                (EXPERIMENT_DIR / filename).read_text(encoding="utf-8")
            )
            self.assertEqual(len(payload["tasks"]), 3)


if __name__ == "__main__":
    unittest.main()
