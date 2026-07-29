from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType

from tests.test_v515s_cross_source_predecessor_binding_acceptance import (
    _passing_inputs,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT / "experiments" / "v5.15t-open-attribution-isolation"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515t_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15t verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _open_adoption_event(*, mode: str, exact: bool = True) -> dict:
    return {
        "event_type": "autogen_memory_adoption",
        "payload": {
            "attribution_mode": mode,
            "evidence": [
                {
                    "attribution_mode": mode,
                    "legacy_domain_candidate_count": 0,
                    "open_output_candidate_count": 1,
                    "open_current_task_candidate_count": 0,
                    "matched_active_match_modes": (
                        ["open_candidate_exact"] if exact else []
                    ),
                    "matched_historical_match_modes": [],
                }
            ],
        },
    }


class OpenAttributionIsolationAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verifier = _load_verifier()

    def _report(self, *, mode: str, exact: bool = True) -> dict:
        commit = "abc123"
        scenario, team, workflow, session, snapshot, trace = _passing_inputs(
            commit
        )
        trace.append(_open_adoption_event(mode=mode, exact=exact))
        return self.verifier.build_report(
            implementation_commit=commit,
            scenario=scenario,
            team_config=team,
            workflow=workflow,
            session_report=session,
            memory_snapshot=snapshot,
            trace_events=trace,
        )

    def test_open_attribution_evidence_passes(self) -> None:
        report = self._report(mode="ccf_v3_open_candidate_evidence")

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["open_candidate_exact_match_count"],
            1,
        )
        self.assertEqual(report["summary"]["legacy_domain_candidate_count"], 0)

    def test_legacy_attribution_mode_fails(self) -> None:
        report = self._report(mode="ccf_v2_semantic_key_value_rules")
        failed = {
            item["name"] for item in report["checks"] if not item["passed"]
        }

        self.assertFalse(report["summary"]["passed"])
        self.assertIn("memory_attribution_uses_open_candidates_only", failed)

    def test_missing_exact_open_match_fails(self) -> None:
        report = self._report(
            mode="ccf_v3_open_candidate_evidence",
            exact=False,
        )
        failed = {
            item["name"] for item in report["checks"] if not item["passed"]
        }

        self.assertFalse(report["summary"]["passed"])
        self.assertIn("open_predicate_exact_match_observed", failed)

    def test_runner_requires_external_inputs_and_zero_retries(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("AGENTLITE_V515T_SCENARIO_FILE", runner)
        self.assertIn("AGENTLITE_V515T_TEAM_FILE", runner)
        self.assertIn("OPENAI_MAX_RETRIES=0", runner)
        self.assertIn(
            "tests.test_v515t_open_attribution_isolation_acceptance",
            runner,
        )
        self.assertNotIn("api_key=", runner.casefold())
        self.assertNotIn("mimoapikey", runner.casefold())


if __name__ == "__main__":
    unittest.main()