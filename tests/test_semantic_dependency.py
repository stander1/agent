from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from agent_runtime.memory.semantic_disambiguator import (
    ControlledSemanticDependencyAnalyzer,
    SemanticDependencyBudget,
    SemanticDependencyRequest,
)


@dataclass
class _Response:
    content: str
    usage: dict[str, int]
    model: str = "fixture-model"
    latency_ms: float = 2.5


class _FakeClient:
    def __init__(self, content: dict[str, object]) -> None:
        self.content = content
        self.calls: list[dict[str, str]] = []

    def complete(self, *, system_prompt: str, user_prompt: str) -> _Response:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        return _Response(
            content=json.dumps(self.content),
            usage={
                "prompt_tokens": 70,
                "completion_tokens": 20,
                "total_tokens": 90,
            },
        )


def _request(text: str) -> SemanticDependencyRequest:
    return SemanticDependencyRequest(
        scope_id="scope-holdout",
        task_id="task-two",
        current_text=text,
        memory_items=(
            {
                "memory_id": "mem-one",
                "summary": "A prior observation established an exact value.",
            },
        ),
    )


class ControlledSemanticDependencyAnalyzerTest(unittest.TestCase):
    def test_accepts_grounded_open_vocabulary_dependency(self) -> None:
        text = (
            "Native state has been reset. Use the confirmed baseline from "
            "the preceding interaction and preserve its exact wording."
        )
        quote = "the confirmed baseline from the preceding interaction"
        client = _FakeClient(
            {
                "schema_version": "agentlite.semantic-dependency.response.v1",
                "required": True,
                "memory_ids": ["mem-one"],
                "source_quote": quote,
                "confidence": 0.94,
            }
        )

        result = ControlledSemanticDependencyAnalyzer(client).analyze(
            _request(text)
        )

        self.assertTrue(result.accepted)
        self.assertTrue(result.required)
        self.assertEqual(result.memory_ids, ("mem-one",))
        self.assertEqual(result.source_quote, quote)
        self.assertEqual(result.total_tokens, 90)
        prompt = json.loads(client.calls[0]["user_prompt"])
        self.assertEqual(prompt["current_text"], text)
        self.assertEqual(prompt["memory_items"][0]["memory_id"], "mem-one")

    def test_rejects_ungrounded_or_unknown_dependency_evidence(self) -> None:
        client = _FakeClient(
            {
                "schema_version": "agentlite.semantic-dependency.response.v1",
                "required": True,
                "memory_ids": ["mem-invented"],
                "source_quote": "a phrase not in the task",
                "confidence": 0.99,
            }
        )

        result = ControlledSemanticDependencyAnalyzer(client).analyze(
            _request("Write a self-contained current summary.")
        )

        self.assertFalse(result.accepted)
        self.assertIn("unknown_memory_id", result.reasons)
        self.assertIn("source_quote_not_found", result.reasons)

    def test_nonrequired_decision_must_not_select_memory(self) -> None:
        client = _FakeClient(
            {
                "schema_version": "agentlite.semantic-dependency.response.v1",
                "required": False,
                "memory_ids": ["mem-one"],
                "source_quote": "",
                "confidence": 0.91,
            }
        )

        result = ControlledSemanticDependencyAnalyzer(client).analyze(
            _request("Write a self-contained current summary.")
        )

        self.assertFalse(result.accepted)
        self.assertIn(
            "nonrequired_response_has_dependency_evidence",
            result.reasons,
        )

    def test_budget_is_fail_closed(self) -> None:
        client = _FakeClient(
            {
                "schema_version": "agentlite.semantic-dependency.response.v1",
                "required": False,
                "memory_ids": [],
                "source_quote": "",
                "confidence": 0.90,
            }
        )
        analyzer = ControlledSemanticDependencyAnalyzer(
            client,
            budget=SemanticDependencyBudget(max_calls_per_task=1),
        )

        first = analyzer.analyze(_request("Summarize the current request."))
        second = analyzer.analyze(_request("Summarize the current request."))

        self.assertTrue(first.accepted)
        self.assertEqual(second.status, "budget_exhausted")
        self.assertEqual(len(client.calls), 1)
