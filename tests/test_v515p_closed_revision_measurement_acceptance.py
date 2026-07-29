from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.15p-closed-revision-measurement-acceptance"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515p_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15p verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _session_report(*, fetch_count: int = 1) -> dict:
    metrics = {
        "registered_capability_profile_count": 2,
        "agentlite_control_llm_call_count": 2,
        "agentlite_control_llm_tokens": 120,
        "agentlite_control_llm_retry_count": 0,
        "agentlite_semantic_disambiguation_accepted_count": 2,
        "agentlite_state_memory_bridge_event_count": 4,
        "agentlite_memory_injected_count": 3,
        "agentlite_memory_supported_output_count": 1,
        "agentlite_useful_memory_hit_count": 1,
        "agentlite_wrong_memory_hit_count": 0,
        "current_task_fidelity_failure_count": 0,
        "agentlite_model_visible_protocol_marker_count": 0,
        "actual_agentlite_transport_tokens": 240,
        "memory_field_fetch_count": fetch_count,
        "memory_field_fetch_tokens": 35 if fetch_count else 0,
    }
    return {
        "driver_status": "active",
        "hooks_active": True,
        "metric_rows": [
            {"metric": key, "value": value}
            for key, value in metrics.items()
        ],
    }


def _scenario(commit: str) -> dict:
    return {
        "authored_after_commit": commit,
        "scenario_id": "external-unseen-revision",
        "tasks": [
            {
                "task_id": "initial",
                "question": "Register the certified initial reading.",
                "required_fragments": ["-4.6 kHz"],
            },
            {
                "task_id": "history",
                "question": (
                    "Report the current and former readings with explicit "
                    "current and archived labels."
                ),
                "required_fragments": [
                    "-3.1 kHz",
                    "-4.6 kHz",
                    "current",
                    "archived",
                ],
            },
        ],
        "revision_expectation": {
            "active_value": "-3.1",
            "historical_value": "-4.6",
            "value_type": "number",
            "unit": "kHz",
            "final_task_id": "history",
            "minimum_field_fetch_count": 1,
        },
    }


def _team(commit: str) -> dict:
    return {
        "authored_after_commit": commit,
        "agents": [
            {"name": "signal_curator"},
            {"name": "history_examiner"},
        ],
    }


def _workflow(commit: str) -> dict:
    return {
        "binding": {"implementation_commit": commit},
        "team": {
            "agent_names": ["signal_curator", "history_examiner"],
            "same_team_instance": True,
            "reset_between_tasks": True,
        },
        "summary": {
            "complete_agent_output_count": 4,
            "provider_usage": {
                "llm_total_tokens": 900,
                "retry_count": 0,
            },
        },
        "tasks": [
            {
                "provider_calls": 2,
                "provider_outputs": [{"content": "-4.6 kHz"}],
            },
            {
                "provider_calls": 2,
                "provider_outputs": [
                    {
                        "content": (
                            "Current: -3.1 kHz. Archived: -4.6 kHz."
                        )
                    }
                ],
            },
        ],
    }


def _snapshot(*, bind_relation: bool = True) -> dict:
    relation = {
        "relation_type": "supersedes_candidate",
        "target_candidate_id": "candidate_old" if bind_relation else "",
    }
    return {
        "claim_cards": [
            {
                "claim_id": "claim_old",
                "candidate_id": "candidate_old",
                "semantic_key": "scope|slot.open.signal|general|cross_task",
                "status": "superseded",
                "value": "-4.6",
                "value_type": "number",
                "unit": "kHz",
                "relations": [],
            },
            {
                "claim_id": "claim_new",
                "candidate_id": "candidate_new",
                "semantic_key": "scope|slot.open.signal|general|cross_task",
                "status": "active",
                "value": "-3.1",
                "value_type": "number",
                "unit": "kHz",
                "relations": [relation],
            },
        ],
        "memory_views": [
            {
                "semantic_key": "scope|slot.open.signal|general|cross_task",
                "active_claim_ids": ["claim_new"],
                "historical_claim_ids": ["claim_old"],
            }
        ],
    }


class ClosedRevisionMeasurementAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def test_typed_revision_and_unstated_history_pass(self) -> None:
        commit = "abc123"
        report = self.verifier.build_report(
            implementation_commit=commit,
            scenario=_scenario(commit),
            team_config=_team(commit),
            workflow=_workflow(commit),
            session_report=_session_report(),
            memory_snapshot=_snapshot(),
        )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(report["summary"]["memory_field_fetch_count"], 1)
        self.assertEqual(
            report["revision_evidence"]["active_claim_id"],
            "claim_new",
        )
        self.assertEqual(
            report["revision_evidence"]["historical_claim_id"],
            "claim_old",
        )

    def test_unbound_relation_and_unmetered_fetch_fail(self) -> None:
        commit = "abc123"
        report = self.verifier.build_report(
            implementation_commit=commit,
            scenario=_scenario(commit),
            team_config=_team(commit),
            workflow=_workflow(commit),
            session_report=_session_report(fetch_count=0),
            memory_snapshot=_snapshot(bind_relation=False),
        )
        failed = {
            item["name"]
            for item in report["checks"]
            if not item["passed"]
        }

        self.assertFalse(report["summary"]["passed"])
        self.assertIn(
            "active_revision_targets_historical_predecessor",
            failed,
        )
        self.assertIn(
            "unstated_history_is_charged_as_field_fetch",
            failed,
        )

    def test_runner_is_external_input_bound_and_secret_free(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("AGENTLITE_V515P_SCENARIO_FILE", runner)
        self.assertIn("AGENTLITE_V515P_TEAM_FILE", runner)
        self.assertIn("OPENAI_API_KEY", runner)
        self.assertNotIn("mimoapikey", runner.casefold())


if __name__ == "__main__":
    unittest.main()