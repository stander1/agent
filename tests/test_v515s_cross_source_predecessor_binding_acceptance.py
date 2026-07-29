from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

from tests.test_v515q_explicit_typed_context_retention import _bound_inputs
from tests.test_v515r_structured_dependency_cost_override_acceptance import (
    _passing_trace,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.15s-cross-source-predecessor-binding"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515s_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15s verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _passing_inputs(commit: str) -> tuple[dict, dict, dict, dict, dict, list]:
    scenario, team, workflow, session, snapshot = _bound_inputs(commit)
    active = next(
        claim
        for claim in snapshot["claim_cards"]
        if claim["status"] == "active"
    )
    active["source_span"] = {
        "source_id": "state:revision",
        "start": 0,
        "end": 55,
        "quote": "The signal is now -3.1 kHz and supersedes its predecessor.",
    }
    historical = next(
        claim
        for claim in snapshot["claim_cards"]
        if claim["status"] == "superseded"
    )
    historical["source_span"] = {
        "source_id": "state:initial",
        "start": 0,
        "end": 24,
        "quote": "The signal is -4.6 kHz.",
    }
    return (
        scenario,
        team,
        workflow,
        session,
        snapshot,
        _passing_trace(team),
    )


class CrossSourcePredecessorBindingAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def test_locally_bound_cross_source_revision_passes(self) -> None:
        commit = "abc123"
        scenario, team, workflow, session, snapshot, trace = _passing_inputs(
            commit
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

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["locally_bound_predecessor_count"],
            1,
        )
        self.assertTrue(
            report["summary"]["revision_source_omits_historical_value"]
        )

    def test_unbound_predecessor_fails(self) -> None:
        commit = "abc123"
        scenario, team, workflow, session, snapshot, trace = _passing_inputs(
            commit
        )
        active = next(
            claim
            for claim in snapshot["claim_cards"]
            if claim["status"] == "active"
        )
        active["relations"][0]["target_candidate_id"] = ""

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
            "supersession_target_is_locally_bound_to_historical_claim",
            failed,
        )

    def test_revision_source_containing_old_literal_is_not_holdout_evidence(
        self,
    ) -> None:
        commit = "abc123"
        scenario, team, workflow, session, snapshot, trace = _passing_inputs(
            commit
        )
        active = next(
            claim
            for claim in snapshot["claim_cards"]
            if claim["status"] == "active"
        )
        active["source_span"]["quote"] += " The old value was -4.6 kHz."

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
        self.assertIn("revision_source_omits_predecessor_literal", failed)

    def test_runner_requires_external_inputs_and_zero_provider_retries(
        self,
    ) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("AGENTLITE_V515S_SCENARIO_FILE", runner)
        self.assertIn("AGENTLITE_V515S_TEAM_FILE", runner)
        self.assertIn("OPENAI_MAX_RETRIES=0", runner)
        self.assertIn(
            "tests.test_v515s_cross_source_predecessor_binding_acceptance",
            runner,
        )
        self.assertNotIn("api_key=", runner.casefold())
        self.assertNotIn("mimoapikey", runner.casefold())


if __name__ == "__main__":
    unittest.main()
