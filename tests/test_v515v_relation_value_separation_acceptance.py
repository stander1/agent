from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

from tests.test_v515s_cross_source_predecessor_binding_acceptance import (
    _passing_inputs,
)
from tests.test_v515t_open_attribution_isolation_acceptance import (
    _open_adoption_event,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT / "experiments" / "v5.15v-relation-value-separation"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515v_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15v verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RelationValueSeparationAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def _inputs(self) -> tuple:
        commit = "abc123"
        scenario, team, workflow, session, snapshot, trace = _passing_inputs(
            commit
        )
        event = _open_adoption_event(
            mode="ccf_v3_open_candidate_evidence",
            exact=True,
        )
        event["payload"]["attribution_surface"] = (
            "decoded_model_content_v1"
        )
        trace.append(event)
        return commit, scenario, team, workflow, session, snapshot, trace

    def test_clean_active_claim_values_pass(self) -> None:
        (
            commit,
            scenario,
            team,
            workflow,
            session,
            snapshot,
            trace,
        ) = self._inputs()
        trace.append(
            {
                "event_type": "state_memory_bridge",
                "payload": {
                    "semantic_disambiguation": {
                        "locally_coalesced_relation_candidate_count": 1,
                    }
                },
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

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["relation_carrier_coalesced_count"],
            1,
        )
        self.assertEqual(
            report["summary"]["relation_value_conflation_count"],
            0,
        )

    def test_active_relation_text_value_fails(self) -> None:
        (
            commit,
            scenario,
            team,
            workflow,
            session,
            snapshot,
            trace,
        ) = self._inputs()
        snapshot["claim_cards"].append(
            {
                "claim_id": "claim-conflated",
                "status": "active",
                "value": "replaces its prior value",
                "value_type": "string",
                "source_span": {
                    "quote": "replaces its prior value",
                },
                "relations": [
                    {
                        "relation_type": "supersedes_candidate",
                        "target_value": "its prior value",
                    }
                ],
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
            "relation_text_is_not_admitted_as_active_claim_value",
            failed,
        )

    def test_runner_requires_external_inputs_and_zero_retries(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("AGENTLITE_V515V_SCENARIO_FILE", runner)
        self.assertIn("AGENTLITE_V515V_TEAM_FILE", runner)
        self.assertIn("OPENAI_MAX_RETRIES=0", runner)
        self.assertIn(
            "tests.test_v515v_relation_value_separation_acceptance",
            runner,
        )
        self.assertNotIn("api_key=", runner.casefold())
        self.assertNotIn("mimoapikey", runner.casefold())


if __name__ == "__main__":
    unittest.main()
