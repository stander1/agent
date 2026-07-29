from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

from tests.test_v515q_explicit_typed_context_retention import (
    _bound_inputs,
    _trace_events,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.15r-structured-dependency-cost-override"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515r_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15r verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _passing_trace(team_config: dict) -> list[dict]:
    events = [
        {
            "event_type": "autogen_semantic_dependency",
            "payload": {
                "task_sequence_index": 2,
                "status": "structured_required",
                "required": True,
                "reasons": ["explicit_historical_state_request"],
                "call_count": 0,
                "total_tokens": 0,
                "retry_count": 0,
            },
        }
    ]
    for event in _trace_events(team_config):
        payload = event["payload"]
        payload.update(
            {
                "continuity_context_required": True,
                "continuity_context_reasons": [
                    "explicit_historical_state_request"
                ],
                "continuity_cost_override": True,
                "native_input_tokens": 20,
                "rewritten_input_tokens": 80,
                "model_response_boundary_applied": True,
                "rewrite_outcome": "rewrite_applied",
                "fallback_buckets": [],
            }
        )
        events.append(event)
    return events


class StructuredDependencyCostOverrideAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def test_structured_zero_cost_dependency_overrides_expanded_rewrites(
        self,
    ) -> None:
        commit = "abc123"
        scenario, team, workflow, session, snapshot = _bound_inputs(commit)
        report = self.verifier.build_report(
            implementation_commit=commit,
            scenario=scenario,
            team_config=team,
            workflow=workflow,
            session_report=session,
            memory_snapshot=snapshot,
            trace_events=_passing_trace(team),
        )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["zero_cost_structured_dependency_count"],
            1,
        )
        self.assertEqual(
            report["summary"]["continuity_cost_override_count"],
            2,
        )

    def test_nonreducing_rewrite_without_override_fails(self) -> None:
        commit = "abc123"
        scenario, team, workflow, session, snapshot = _bound_inputs(commit)
        trace = _passing_trace(team)
        trace[1]["payload"]["continuity_cost_override"] = False
        report = self.verifier.build_report(
            implementation_commit=commit,
            scenario=scenario,
            team_config=team,
            workflow=workflow,
            session_report=session,
            memory_snapshot=snapshot,
            trace_events=trace,
        )
        failed = {
            item["name"] for item in report["checks"] if not item["passed"]
        }

        self.assertFalse(report["summary"]["passed"])
        self.assertIn(
            "nonreducing_final_rewrites_use_continuity_cost_override",
            failed,
        )

    def test_provider_decision_is_not_structured_zero_cost_evidence(self) -> None:
        commit = "abc123"
        scenario, team, workflow, session, snapshot = _bound_inputs(commit)
        trace = _passing_trace(team)
        trace[0]["payload"].update(
            {
                "status": "accepted",
                "reasons": [],
                "call_count": 1,
                "total_tokens": 100,
            }
        )
        report = self.verifier.build_report(
            implementation_commit=commit,
            scenario=scenario,
            team_config=team,
            workflow=workflow,
            session_report=session,
            memory_snapshot=snapshot,
            trace_events=trace,
        )
        failed = {
            item["name"] for item in report["checks"] if not item["passed"]
        }

        self.assertFalse(report["summary"]["passed"])
        self.assertIn(
            "final_dependency_uses_structured_zero_cost_path",
            failed,
        )

    def test_runner_binds_external_inputs_and_zero_retries(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("AGENTLITE_V515R_SCENARIO_FILE", runner)
        self.assertIn("AGENTLITE_V515R_TEAM_FILE", runner)
        self.assertIn("OPENAI_MAX_RETRIES=0", runner)
        self.assertIn("tests.test_v515r_structured_dependency", runner)
        self.assertNotIn("api_key=", runner.casefold())
        self.assertNotIn("mimoapikey", runner.casefold())


if __name__ == "__main__":
    unittest.main()