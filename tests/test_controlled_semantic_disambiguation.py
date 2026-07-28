from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Any

from agent_runtime.bridge.state_memory_bridge import (
    CanonicalClaimSemanticValidator,
)
from agent_runtime.memory.semantic_disambiguator import (
    ControlledSemanticDisambiguator,
    SemanticDisambiguationBudget,
    SemanticDisambiguationRequest,
)


@dataclass
class _Response:
    content: str
    usage: dict[str, int]
    model: str = "fixture-model"
    latency_ms: float = 3.5
    provider_guard: dict[str, Any] | None = None


class _FakeClient:
    def __init__(self, responses: list[_Response | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, str]] = []

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> _Response:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(
    claims: list[dict[str, Any]],
    *,
    usage: dict[str, int] | None = None,
) -> _Response:
    return _Response(
        content=json.dumps(
            {
                "schema_version": (
                    "agentlite.semantic-disambiguation.response.v1"
                ),
                "claims": claims,
            },
            ensure_ascii=False,
        ),
        usage=usage
        or {
            "prompt_tokens": 80,
            "completion_tokens": 40,
            "total_tokens": 120,
        },
        provider_guard={"retry_attempts": 1},
    )


def _request(
    source_text: str,
    *,
    task_id: str = "task-one",
) -> SemanticDisambiguationRequest:
    return SemanticDisambiguationRequest(
        scope_id="scope-unseen",
        task_id=task_id,
        subject="subject-unseen",
        source_id=f"source:{task_id}",
        source_text=source_text,
    )


class ControlledSemanticDisambiguationTest(unittest.TestCase):
    def test_accepts_unique_evidence_quote_and_derives_span_locally(
        self,
    ) -> None:
        text = (
            "After the third pulse, the membrane remained quiescent. "
            "No other state was recorded."
        )
        quote = "the membrane remained quiescent"
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "membrane_state",
                            "assertion_type": "observation",
                            "operator": "eq",
                            "value": "quiescent",
                            "value_type": "string",
                            "unit": "",
                            "polarity": "positive",
                            "modality": "observed",
                            "temporal_status": "current",
                            "source_quote": quote,
                            "confidence": 0.95,
                            "relations": [],
                        }
                    ]
                )
            ]
        )
        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text)
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.total_tokens, 120)
        self.assertEqual(result.retry_count, 1)
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate["confidence"], 0.72)
        self.assertEqual(candidate["source_span"]["quote"], quote)
        self.assertEqual(
            text[
                candidate["source_span"]["start"] :
                candidate["source_span"]["end"]
            ],
            quote,
        )
        validation = CanonicalClaimSemanticValidator().validate(
            candidate,
            source_text=text,
        )
        self.assertTrue(validation.allowed, validation.reasons)
        request_payload = json.loads(client.calls[0]["user_prompt"])
        self.assertEqual(request_payload["task_id"], "task-one")
        self.assertEqual(request_payload["source_text"], text)

    def test_rejects_quote_not_present_in_source(self) -> None:
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "surface_state",
                            "value": "stable",
                            "source_quote": "fabricated stable statement",
                        }
                    ]
                )
            ]
        )
        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request("The surface response was not classified.")
        )

        self.assertFalse(result.accepted)
        self.assertIn("claim_0:source_quote_not_found", result.reasons)

    def test_rejects_repeated_quote_without_guessing_span(self) -> None:
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "signal_state",
                            "value": "nominal",
                            "source_quote": "signal nominal",
                        }
                    ]
                )
            ]
        )
        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request("signal nominal; later signal nominal.")
        )

        self.assertFalse(result.accepted)
        self.assertIn("claim_0:source_quote_not_unique", result.reasons)

    def test_rejects_non_json_and_unknown_fields(self) -> None:
        client = _FakeClient(
            [
                _Response(
                    content="```json\n{\"claims\": []}\n```",
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 10,
                        "total_tokens": 20,
                    },
                ),
                _Response(
                    content=json.dumps(
                        {
                            "claims": [],
                            "write_memory": True,
                        }
                    ),
                    usage={
                        "prompt_tokens": 10,
                        "completion_tokens": 10,
                        "total_tokens": 20,
                    },
                ),
            ]
        )
        first = ControlledSemanticDisambiguator(client).disambiguate(
            _request("Unstructured source.", task_id="strict-one")
        )
        second = ControlledSemanticDisambiguator(client).disambiguate(
            _request("Another source.", task_id="strict-two")
        )

        self.assertIn("control_response_not_strict_json", first.reasons)
        self.assertIn(
            "control_response_has_unknown_top_level_fields",
            second.reasons,
        )

    def test_call_budget_is_isolated_by_scope_and_task(self) -> None:
        client = _FakeClient(
            [
                _response([]),
                _response([]),
            ]
        )
        disambiguator = ControlledSemanticDisambiguator(
            client,
            budget=SemanticDisambiguationBudget(
                max_calls_per_task=1,
                max_source_chars=100,
                max_candidates_per_call=2,
                max_control_tokens_per_task=500,
            ),
        )
        first = disambiguator.disambiguate(
            _request("First unresolved source.")
        )
        blocked = disambiguator.disambiguate(
            _request("Second source in the same task.")
        )
        other_task = disambiguator.disambiguate(
            _request("Independent source.", task_id="task-two")
        )

        self.assertEqual(first.status, "rejected")
        self.assertEqual(blocked.status, "budget_exhausted")
        self.assertIn("control_call_budget_exhausted", blocked.reasons)
        self.assertEqual(other_task.status, "rejected")
        self.assertEqual(len(client.calls), 2)

    def test_token_budget_excess_fails_closed_after_observed_usage(
        self,
    ) -> None:
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "oscillation_mode",
                            "value": "damped",
                            "source_quote": "mode was damped",
                        }
                    ],
                    usage={
                        "prompt_tokens": 210,
                        "completion_tokens": 140,
                        "total_tokens": 350,
                    },
                )
            ]
        )
        disambiguator = ControlledSemanticDisambiguator(
            client,
            budget=SemanticDisambiguationBudget(
                max_calls_per_task=2,
                max_source_chars=100,
                max_candidates_per_call=2,
                max_control_tokens_per_task=300,
            ),
        )
        result = disambiguator.disambiguate(
            _request("The oscillation mode was damped.")
        )
        next_result = disambiguator.disambiguate(
            _request("The oscillation mode was damped.")
        )

        self.assertEqual(result.status, "budget_exhausted")
        self.assertFalse(result.candidates)
        self.assertEqual(result.total_tokens, 350)
        self.assertIn(
            "control_token_budget_exhausted",
            next_result.reasons,
        )
        self.assertEqual(len(client.calls), 1)

    def test_prompt_budget_is_checked_before_provider_call(self) -> None:
        client = _FakeClient([])
        result = ControlledSemanticDisambiguator(
            client,
            budget=SemanticDisambiguationBudget(
                max_calls_per_task=1,
                max_source_chars=100,
                max_candidates_per_call=2,
                max_control_tokens_per_task=200,
            ),
        ).disambiguate(
            _request("The oscillation mode was damped.")
        )

        self.assertEqual(result.status, "budget_exhausted")
        self.assertIn(
            "control_prompt_exceeds_remaining_budget",
            result.reasons,
        )
        self.assertTrue(result.usage_estimated)
        self.assertFalse(client.calls)

    def test_source_and_candidate_limits_fail_before_admission(self) -> None:
        source_client = _FakeClient([])
        source_result = ControlledSemanticDisambiguator(
            source_client,
            budget=SemanticDisambiguationBudget(
                max_calls_per_task=1,
                max_source_chars=10,
                max_candidates_per_call=1,
                max_control_tokens_per_task=100,
            ),
        ).disambiguate(_request("This source is too long."))
        self.assertIn("source_window_exceeds_budget", source_result.reasons)
        self.assertFalse(source_client.calls)

        candidate_client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "one",
                            "value": "alpha",
                            "source_quote": "alpha",
                        },
                        {
                            "predicate": "two",
                            "value": "beta",
                            "source_quote": "beta",
                        },
                    ]
                )
            ]
        )
        candidate_result = ControlledSemanticDisambiguator(
            candidate_client,
            budget=SemanticDisambiguationBudget(
                max_calls_per_task=1,
                max_source_chars=100,
                max_candidates_per_call=1,
                max_control_tokens_per_task=500,
            ),
        ).disambiguate(_request("alpha beta", task_id="candidate-limit"))
        self.assertIn(
            "candidate_count_exceeds_budget",
            candidate_result.reasons,
        )

    def test_provider_errors_are_audit_only(self) -> None:
        result = ControlledSemanticDisambiguator(
            _FakeClient([TimeoutError("provider unavailable")])
        ).disambiguate(_request("The source remains unresolved."))

        self.assertEqual(result.status, "provider_error")
        self.assertFalse(result.candidates)
        self.assertEqual(result.reasons, ("provider_error:TimeoutError",))


if __name__ == "__main__":
    unittest.main()
