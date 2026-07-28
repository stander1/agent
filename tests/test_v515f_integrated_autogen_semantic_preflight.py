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
    / "v5.15f-integrated-autogen-semantic-preflight"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515f_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15f verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _session_report() -> dict:
    metrics = {
        "registered_capability_profile_count": 3,
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
    }
    return {
        "driver_status": "active",
        "hooks_active": True,
        "metric_rows": [
            {"metric": key, "value": value}
            for key, value in metrics.items()
        ],
    }


class IntegratedAutoGenSemanticPreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def test_generic_integrated_evidence_passes(self) -> None:
        commit = "abc123"
        scenario = {
            "authored_after_commit": commit,
            "scenario_id": "external-chain",
            "tasks": [
                {
                    "task_id": "t1",
                    "required_fragments": ["42 mg/L"],
                },
                {
                    "task_id": "t2",
                    "required_fragments": [
                        "45 mg/L",
                        "42 mg/L",
                        "superseded measurement",
                    ],
                },
            ],
        }
        team_config = {
            "authored_after_commit": commit,
            "agents": [
                {"name": "evidence_mapper"},
                {"name": "synthesis_operator"},
                {"name": "consistency_auditor"},
            ],
        }
        workflow = {
            "binding": {"implementation_commit": commit},
            "team": {
                "agent_names": [
                    "evidence_mapper",
                    "synthesis_operator",
                    "consistency_auditor",
                ],
                "same_team_instance": True,
                "reset_between_tasks": True,
            },
            "summary": {
                "complete_agent_output_count": 6,
                "provider_usage": {
                    "llm_total_tokens": 900,
                    "retry_count": 0,
                },
            },
            "tasks": [
                {
                    "provider_calls": 3,
                    "provider_outputs": [{"content": "42 mg/L"}],
                },
                {
                    "provider_calls": 3,
                    "provider_outputs": [
                        {
                            "content": (
                                "45 mg/L replaces 42 mg/L as the "
                                "Superseded Measurement."
                            )
                        }
                    ],
                },
            ],
        }
        report = self.verifier.build_report(
            implementation_commit=commit,
            scenario=scenario,
            team_config=team_config,
            workflow=workflow,
            session_report=_session_report(),
        )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(report["cost"]["provider_total_tokens"], 1020)
        self.assertEqual(
            report["cost"]["transport_tokens_excluding_control"],
            120,
        )
        self.assertEqual(
            report["cost"]["accounted_end_to_end_tokens"],
            1140,
        )
        self.assertEqual(
            report["cost"]["control_double_counted_token_count"],
            0,
        )

    def test_benchmark_role_names_do_not_satisfy_generic_gate(self) -> None:
        report = self.verifier.build_report(
            implementation_commit="abc123",
            scenario={
                "authored_after_commit": "abc123",
                "scenario_id": "external-chain",
                "tasks": [
                    {"task_id": "t1", "required_fragments": []},
                    {"task_id": "t2", "required_fragments": []},
                ],
            },
            team_config={
                "authored_after_commit": "abc123",
                "agents": [
                    {"name": "planner"},
                    {"name": "writer"},
                    {"name": "reviewer"},
                ],
            },
            workflow={
                "binding": {"implementation_commit": "abc123"},
                "team": {
                    "agent_names": ["planner", "writer", "reviewer"],
                    "same_team_instance": True,
                    "reset_between_tasks": True,
                },
                "summary": {
                    "complete_agent_output_count": 6,
                    "provider_usage": {
                        "llm_total_tokens": 900,
                        "retry_count": 0,
                    },
                },
                "tasks": [
                    {"provider_calls": 3, "provider_outputs": []},
                    {"provider_calls": 3, "provider_outputs": []},
                ],
            },
            session_report=_session_report(),
        )

        failed = {
            item["name"]
            for item in report["checks"]
            if not item["passed"]
        }
        self.assertIn(
            "workflow_uses_arbitrary_capability_names",
            failed,
        )

    def test_runner_does_not_read_local_key_file(self) -> None:
        runner = (
            EXPERIMENT_DIR / "run_openeuler.sh"
        ).read_text(encoding="utf-8")
        app = (
            EXPERIMENT_DIR / "run_autogen_preflight_app.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OPENAI_API_KEY", runner)
        self.assertNotIn("mimoapikey", runner.casefold())
        self.assertNotIn("mimoapikey", app.casefold())


if __name__ == "__main__":
    unittest.main()
