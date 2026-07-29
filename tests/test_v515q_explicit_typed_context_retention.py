import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

from tests.test_v515p_closed_revision_measurement_acceptance import (
    _scenario,
    _session_report,
    _snapshot,
    _team,
    _workflow,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.15q-explicit-typed-context-retention"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515q_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15q verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bound_inputs(commit: str) -> tuple[dict, dict, dict, dict, dict]:
    scenario = _scenario(commit)
    scenario["tasks"][-1]["required_fragments"].append("false")
    scenario["model_visible_context_expectation"] = {
        "required_typed_facts": [
            {
                "kind": "active_fact",
                "value": "false",
                "value_type": "boolean",
                "unit": "",
                "status": "active",
            }
        ]
    }
    workflow = _workflow(commit)
    workflow["tasks"][-1]["provider_outputs"][0]["content"] = (
        "Current: -3.1 kHz. Archived: -4.6 kHz. Flag: false."
    )
    return (
        scenario,
        _team(commit),
        workflow,
        _session_report(),
        _snapshot(),
    )


def _trace_events(
    team_config: dict,
    *,
    include_flag_for: set[str] | None = None,
) -> list[dict]:
    include_flag_for = include_flag_for or {
        agent["name"] for agent in team_config["agents"]
    }
    core = (
        'active_fact={"slot_id":"slot.open.signal",'
        '"scope":"general","value":"-3.1",'
        '"value_type":"number","unit":"kHz",'
        '"operator":"eq","polarity":"positive","status":"active"}\n'
        'historical_fact={"slot_id":"slot.open.signal",'
        '"scope":"general","value":"-4.6",'
        '"value_type":"number","unit":"kHz",'
        '"operator":"eq","polarity":"positive",'
        '"status":"superseded"}'
    )
    flag = (
        '\nactive_fact={"slot_id":"slot.open.generic_flag",'
        '"scope":"general","value":"false",'
        '"value_type":"boolean","unit":"",'
        '"operator":"eq","polarity":"positive","status":"active"}'
    )
    return [
        {
            "event_type": "autogen_agent_receive",
            "payload": {
                "task_sequence_index": 2,
                "agent_id": agent["name"],
                "decoded_messages": [
                    {
                        "content_text": (
                            core
                            + (flag if agent["name"] in include_flag_for else "")
                        )
                    }
                ],
            },
        }
        for agent in team_config["agents"]
    ]


class ExplicitTypedContextRetentionAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def test_all_receivers_preserve_revision_pair_and_related_fact(self) -> None:
        commit = "abc123"
        scenario, team, workflow, session, snapshot = _bound_inputs(commit)
        report = self.verifier.build_report(
            implementation_commit=commit,
            scenario=scenario,
            team_config=team,
            workflow=workflow,
            session_report=session,
            memory_snapshot=snapshot,
            trace_events=_trace_events(team),
        )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["model_visible_context_receiver_count"],
            2,
        )
        self.assertEqual(report["summary"]["expected_typed_fact_count"], 3)

    def test_missing_related_fact_for_one_receiver_fails(self) -> None:
        commit = "abc123"
        scenario, team, workflow, session, snapshot = _bound_inputs(commit)
        report = self.verifier.build_report(
            implementation_commit=commit,
            scenario=scenario,
            team_config=team,
            workflow=workflow,
            session_report=session,
            memory_snapshot=snapshot,
            trace_events=_trace_events(
                team,
                include_flag_for={team["agents"][0]["name"]},
            ),
        )
        failed = {
            item["name"]
            for item in report["checks"]
            if not item["passed"]
        }

        self.assertFalse(report["summary"]["passed"])
        self.assertIn(
            "final_receiver_inputs_preserve_explicit_typed_context",
            failed,
        )

    def test_runner_binds_external_inputs_trace_and_zero_retries(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("AGENTLITE_V515Q_SCENARIO_FILE", runner)
        self.assertIn("AGENTLITE_V515Q_TEAM_FILE", runner)
        self.assertIn("OPENAI_MAX_RETRIES=0", runner)
        self.assertIn('--trace "$TRACE_FILE"', runner)
        self.assertNotIn("api_key=", runner.casefold())
        self.assertNotIn("mimoapikey", runner.casefold())


if __name__ == "__main__":
    unittest.main()
