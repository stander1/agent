from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14v-continuity-memory-formal-regression"
)
V514T_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14t-superseded-constraint-formal-regression"
)
V514U_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14u-continuity-identity-memory-resolution"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_verifier():
    path = STAGE_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514v_verify_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class V514VContinuityMemoryFormalRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.preregistration = json.loads(
            (STAGE_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )

    def test_preregistration_binds_v514u_and_v514t(self) -> None:
        lineage = self.preregistration["source_lineage"]
        self.assertEqual(
            lineage["mechanism_baseline"],
            "v5.14u-continuity-identity-memory-resolution",
        )
        self.assertEqual(
            lineage["v514u_acceptance_verifier_sha256"],
            _sha256(V514U_DIR / "verify_acceptance.py"),
        )
        self.assertEqual(
            lineage["v514t_preregistration_sha256"],
            _sha256(V514T_DIR / "preregistration.json"),
        )

    def test_v514t_inputs_and_thresholds_are_not_weakened(self) -> None:
        previous = json.loads(
            (V514T_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )
        for key in (
            "frozen_input_sha256",
            "frozen_agent_sha256",
            "provider",
            "temperature",
            "max_turns",
            "groups",
            "tasks_per_scenario",
            "quality_rule",
        ):
            self.assertEqual(self.preregistration[key], previous[key], key)
        current = self.preregistration["thresholds"]
        for key, value in previous["thresholds"].items():
            self.assertIn(key, current)
            self.assertEqual(current[key], value, key)
        self.assertGreaterEqual(
            current["approved_prior_memory_resolution_count_min_total"],
            1,
        )
        self.assertEqual(
            current["identity_guard_invalid_event_count_max"],
            0,
        )

    def test_runner_uses_isolated_v514v_paths(self) -> None:
        runner = (STAGE_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("AGENTLITE_V514V_EXP_ID", runner)
        self.assertIn(
            "runs/v5.14v-continuity-memory-formal-regression",
            runner,
        )
        self.assertIn("AGENTLITE_V514B_RESUME", runner)

    def test_trace_reader_observes_generic_resolution_and_identity_events(
        self,
    ) -> None:
        verifier = _load_verifier()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = (
                root
                / "S"
                / "managed"
                / "agentlite_data"
                / "sessions"
                / "launch_test"
                / "autogen_driver"
                / "trace.jsonl"
            )
            trace.parent.mkdir(parents=True)
            events = [
                {
                    "event_type": "autogen_memory_candidate",
                    "payload": {
                        "resolution_kind": "approved_prior_artifact",
                        "candidate_kind": "autogen_team_final",
                    },
                },
                {
                    "event_type": "autogen_current_task_identity_guard",
                    "payload": {
                        "status": "blocked_and_reanchored",
                        "task_sequence_index": 4,
                        "unsupported_claims": ["Q5"],
                    },
                },
            ]
            trace.write_text(
                "\n".join(json.dumps(item) for item in events) + "\n",
                encoding="utf-8",
            )
            observed = list(
                verifier._scenario_trace_events(root, "S")
            )
            self.assertEqual(observed, events)


if __name__ == "__main__":
    unittest.main()
