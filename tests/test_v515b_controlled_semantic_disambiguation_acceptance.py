from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.15b-controlled-semantic-disambiguation"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515b_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15b verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _proposal(
    *,
    predicate: str,
    value: str,
    quote: str,
    operator: str = "eq",
    unit: str = "",
    temporal_status: str = "current",
) -> dict[str, str]:
    return {
        "predicate": predicate,
        "assertion_type": "observation",
        "operator": operator,
        "value": value,
        "value_type": "number" if value.isdigit() else "string",
        "unit": unit,
        "temporal_status": temporal_status,
        "source_quote": quote,
    }


class ControlledSemanticDisambiguationAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verify = _load_verifier()

    def test_synthetic_external_holdout_passes(self) -> None:
        positive_cases = [
            {
                "case_id": "acoustic",
                "source_text": "The resonator mode remained coherent.",
                "proposal": _proposal(
                    predicate="resonator_mode",
                    value="coherent",
                    quote="resonator mode remained coherent",
                ),
                "expected": {
                    "predicate": "resonator_mode",
                    "operator": "eq",
                    "value": "coherent",
                    "unit": "",
                    "temporal_status": "current",
                },
            },
            {
                "case_id": "archival",
                "source_text": "The charter status was ratified.",
                "proposal": _proposal(
                    predicate="charter_status",
                    value="ratified",
                    quote="charter status was ratified",
                ),
                "expected": {
                    "predicate": "charter_status",
                    "operator": "eq",
                    "value": "ratified",
                    "unit": "",
                    "temporal_status": "current",
                },
            },
            {
                "case_id": "cryogenic",
                "source_text": "The chamber pressure stayed below 7 pascals.",
                "proposal": _proposal(
                    predicate="chamber_pressure",
                    operator="lt",
                    value="7",
                    unit="pascals",
                    quote="chamber pressure stayed below 7 pascals",
                ),
                "expected": {
                    "predicate": "chamber_pressure",
                    "operator": "lt",
                    "value": "7",
                    "unit": "pascals",
                    "temporal_status": "current",
                },
            },
        ]
        negative_cases = [
            {
                "case_id": "duplicate",
                "kind": "repeated_quote",
                "source_text": "phase locked; later phase locked.",
                "proposal": _proposal(
                    predicate="phase_state",
                    value="locked",
                    quote="phase locked",
                ),
            },
            {
                "case_id": "absent",
                "kind": "missing_quote",
                "source_text": "The classification remains pending.",
                "proposal": _proposal(
                    predicate="classification_state",
                    value="approved",
                    quote="classification was approved",
                ),
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unittest_output = root / "unittest.txt"
            unittest_output.write_text(
                "Ran 1 test in 0.001s\n\nOK\n",
                encoding="utf-8",
            )
            holdout = root / "holdout.json"
            holdout.write_text(
                json.dumps(
                    {
                        "schema_version": "agentlite.v515b.holdout.v1",
                        "holdout_id": "synthetic-verifier-test",
                        "authored_after_commit": _commit(),
                        "positive_cases": positive_cases,
                        "negative_cases": negative_cases,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report = self.verify.evaluate(
                repo_root=REPO_ROOT,
                unittest_output=unittest_output,
                holdout_file=holdout,
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["passed_check_count"],
            report["summary"]["check_count"],
        )
        self.assertEqual(report["cost"]["real_provider_call_count"], 0)
        self.assertEqual(report["quality"]["unsafe_evidence_escape_count"], 0)

    def test_runner_requires_external_holdout_and_archives_it(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("AGENTLITE_V515B_HOLDOUT_FILE", runner)
        self.assertIn('cp "$HOLDOUT_FILE"', runner)
        self.assertIn("holdout_input.json.sha256", runner)
        self.assertIn("sha256sum -c", runner)


if __name__ == "__main__":
    unittest.main()
