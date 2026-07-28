from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.15c-real-provider-semantic-preflight"
)


def _load_runner() -> ModuleType:
    path = EXPERIMENT_DIR / "run_provider_preflight.py"
    spec = importlib.util.spec_from_file_location("v515c_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15c runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class _Response:
    content: str
    usage: dict[str, int]
    model: str = "scripted-provider"
    latency_ms: float = 4.0
    provider_guard: dict[str, int] | None = None


class _Client:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> _Response:
        del system_prompt, user_prompt
        return self.responses.pop(0)


def _commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _case(index: int) -> tuple[dict[str, Any], _Response]:
    value = f"state-{index}"
    quote = f"instrument {index} remained {value}"
    predicate = f"instrument_{index}_state"
    case = {
        "case_id": f"case-{index}",
        "domain": f"domain-{index}",
        "subject": f"subject-{index}",
        "source_text": f"During inspection, {quote}.",
        "expected": {
            "predicate": predicate,
            "operator": "eq",
            "value": value,
            "unit": "",
            "temporal_status": "current",
        },
    }
    response = _Response(
        content=json.dumps(
            {
                "schema_version": (
                    "agentlite.semantic-disambiguation.response.v1"
                ),
                "claims": [
                    {
                        "predicate": predicate,
                        "assertion_type": "observation",
                        "operator": "eq",
                        "value": value,
                        "value_type": "string",
                        "modality": "observed",
                        "temporal_status": "current",
                        "source_quote": quote,
                    }
                ],
            }
        ),
        usage={
            "prompt_tokens": 70,
            "completion_tokens": 30,
            "total_tokens": 100,
        },
        provider_guard={"retry_attempts": 0},
    )
    return case, response


class RealProviderSemanticPreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _load_runner()

    def test_scripted_cross_domain_preflight_passes(self) -> None:
        pairs = [_case(index) for index in range(1, 6)]
        report, outputs = self.runner.run_preflight(
            scenario_payload={
                "schema_version": "agentlite.v515c.scenarios.v1",
                "scenario_id": "scripted-preflight",
                "authored_after_commit": _commit(),
                "cases": [pair[0] for pair in pairs],
            },
            implementation_commit=_commit(),
            client=_Client([pair[1] for pair in pairs]),
            provider_mode="scripted_test",
        )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(report["summary"]["strict_success_count"], 5)
        self.assertEqual(report["cost"]["provider_call_count"], 5)
        self.assertEqual(report["cost"]["provider_total_tokens"], 500)
        self.assertEqual(report["cost"]["control_llm_tokens"], 500)
        self.assertEqual(report["cost"]["end_to_end_tokens"], 500)
        self.assertEqual(report["cost"]["double_counted_token_count"], 0)
        self.assertEqual(len(outputs), 5)
        self.assertNotIn("system_prompt", outputs[0])
        self.assertNotIn("user_prompt", outputs[0])

    def test_commit_mismatch_fails_closed(self) -> None:
        pairs = [_case(index) for index in range(1, 6)]
        report, _ = self.runner.run_preflight(
            scenario_payload={
                "schema_version": "agentlite.v515c.scenarios.v1",
                "scenario_id": "mismatched-preflight",
                "authored_after_commit": "wrong-commit",
                "cases": [pair[0] for pair in pairs],
            },
            implementation_commit=_commit(),
            client=_Client([pair[1] for pair in pairs]),
            provider_mode="scripted_test",
        )

        self.assertFalse(report["summary"]["passed"])
        failed = {
            item["name"]
            for item in report["checks"]
            if not item["passed"]
        }
        self.assertIn(
            "scenario_is_bound_to_implementation_commit",
            failed,
        )

    def test_runner_never_reads_a_key_file(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        source = (
            EXPERIMENT_DIR / "run_provider_preflight.py"
        ).read_text(encoding="utf-8")

        self.assertIn("OPENAI_API_KEY", runner)
        self.assertNotIn("mimoapikey", runner.casefold())
        self.assertNotIn("mimoapikey", source.casefold())
        self.assertNotIn("resolved_api_key", source)


if __name__ == "__main__":
    unittest.main()
