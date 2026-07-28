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
        system_prompt = client.calls[0]["system_prompt"]
        self.assertIn("lower_snake_case", system_prompt)
        self.assertIn(
            'assertion_type=["constraint", "decision", "fact", '
            '"observation", "preference"]',
            system_prompt,
        )
        self.assertIn(
            'operator=["eq", "ge", "gt", "le", "lt", "ne"]',
            system_prompt,
        )
        self.assertIn(
            'temporal_status=["current", "future", "historical", '
            '"unspecified"]',
            system_prompt,
        )
        self.assertIn(
            "Each claim may contain only predicate, assertion_type",
            system_prompt,
        )
        self.assertIn("Do not output subject", system_prompt)
        self.assertIn(
            "do not rewrite them as booleans",
            system_prompt,
        )
        self.assertIn(
            "value must contain only the exact signed numeric literal",
            system_prompt,
        )
        self.assertIn(
            "Each relation object may contain only relation_type",
            system_prompt,
        )
        self.assertIn(
            "target_candidate_id must be an empty string",
            system_prompt,
        )

    def test_normalizes_complete_measurement_from_exact_evidence(self) -> None:
        text = "The transfer coefficient is -1.8 qx."
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "transfer_coefficient",
                            "value": "-1.8 qx",
                            "value_type": "string",
                            "unit": "",
                            "source_quote": text,
                        }
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text, task_id="normalize-measurement")
        )

        self.assertTrue(result.accepted, result.reasons)
        self.assertEqual(result.locally_normalized_candidate_count, 1)
        candidate = result.candidates[0]
        self.assertEqual(candidate["value"], "-1.8")
        self.assertEqual(candidate["value_type"], "number")
        self.assertEqual(candidate["unit"], "qx")
        self.assertEqual(
            candidate["schema_status"],
            "llm_proposed_locally_normalized",
        )
        validation = CanonicalClaimSemanticValidator().validate(
            candidate,
            source_text=text,
        )
        self.assertTrue(validation.allowed, validation.reasons)

    def test_rejects_repeated_measurement_inside_one_quote(self) -> None:
        text = "The twin readings are -1.8 qx and -1.8 qx."
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "twin_reading",
                            "value": "-1.8 qx",
                            "value_type": "string",
                            "source_quote": text,
                        }
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text, task_id="repeated-measurement")
        )

        self.assertFalse(result.accepted)
        self.assertIn(
            "claim_0:measurement_value_not_unique_in_source_quote",
            result.reasons,
        )

    def test_does_not_normalize_descriptions_or_ranges(self) -> None:
        text = (
            "The operating mode is 17 qx standby. "
            "The interval is 1 to 3 qx."
        )
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "operating_mode",
                            "value": "17 qx standby",
                            "value_type": "string",
                            "source_quote": (
                                "The operating mode is 17 qx standby."
                            ),
                        },
                        {
                            "predicate": "interval",
                            "value": "1 to 3 qx",
                            "value_type": "string",
                            "source_quote": "The interval is 1 to 3 qx.",
                        },
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text, task_id="preserve-descriptions")
        )

        self.assertTrue(result.accepted, result.reasons)
        self.assertEqual(result.locally_normalized_candidate_count, 0)
        self.assertEqual(
            [candidate["value_type"] for candidate in result.candidates],
            ["string", "string"],
        )

    def test_rejects_measurement_unit_mismatch(self) -> None:
        text = "The transfer coefficient is -1.8 qx."
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "transfer_coefficient",
                            "value": "-1.8 qx",
                            "value_type": "string",
                            "unit": "ms",
                            "source_quote": text,
                        }
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text, task_id="reject-unit-mismatch")
        )

        self.assertFalse(result.accepted)
        self.assertIn("claim_0:measurement_unit_mismatch", result.reasons)

    def test_relation_contract_names_unknown_nested_fields(self) -> None:
        text = "The reading is 9 qx, replacing the former reading."
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "reading",
                            "value": "9",
                            "value_type": "number",
                            "unit": "qx",
                            "source_quote": text,
                            "relations": [
                                {
                                    "relation_type": "supersedes_value",
                                    "target_value": "former reading",
                                    "target_predicate": "reading",
                                }
                            ],
                        }
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text, task_id="unknown-relation-field")
        )

        self.assertFalse(result.accepted)
        self.assertIn(
            "claim_0:relation_has_unknown_fields:target_predicate",
            result.reasons,
        )

    def test_provider_cannot_bind_internal_relation_identifier(self) -> None:
        text = "The reading is 9 qx, replacing the former reading."
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "reading",
                            "value": "9",
                            "value_type": "number",
                            "unit": "qx",
                            "source_quote": text,
                            "relations": [
                                {
                                    "relation_type": "supersedes_value",
                                    "target_value": "former reading",
                                    "target_candidate_id": "provider_guess",
                                }
                            ],
                        }
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text, task_id="provider-relation-id")
        )

        self.assertFalse(result.accepted)
        self.assertIn(
            "claim_0:relation_target_candidate_id_not_empty",
            result.reasons,
        )

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

    def test_scalar_sign_and_boolean_value_do_not_change_logical_polarity(
        self,
    ) -> None:
        text = (
            "The offset is -18.7 units. "
            "The feature flag is false. "
            "The state is not equal to dormant."
        )
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "offset",
                            "assertion_type": "observation",
                            "operator": "eq",
                            "value": "-18.7",
                            "value_type": "number",
                            "unit": "units",
                            "polarity": "negative",
                            "modality": "observed",
                            "temporal_status": "current",
                            "source_quote": "The offset is -18.7 units.",
                            "confidence": 0.9,
                            "relations": [],
                        },
                        {
                            "predicate": "feature_flag",
                            "assertion_type": "fact",
                            "operator": "eq",
                            "value": "false",
                            "value_type": "boolean",
                            "unit": "",
                            "polarity": "negative",
                            "modality": "asserted",
                            "temporal_status": "current",
                            "source_quote": "The feature flag is false.",
                            "confidence": 0.9,
                            "relations": [],
                        },
                        {
                            "predicate": "state",
                            "assertion_type": "constraint",
                            "operator": "ne",
                            "value": "dormant",
                            "value_type": "string",
                            "unit": "",
                            "polarity": "positive",
                            "modality": "asserted",
                            "temporal_status": "current",
                            "source_quote": (
                                "The state is not equal to dormant."
                            ),
                            "confidence": 0.9,
                            "relations": [],
                        },
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text)
        )

        self.assertTrue(result.accepted, result.reasons)
        self.assertEqual(
            [item["polarity"] for item in result.candidates],
            ["positive", "positive", "negative"],
        )
        self.assertIn(
            "Polarity follows operator",
            client.calls[0]["system_prompt"],
        )

    def test_locally_rebinds_unique_scalar_and_preserves_false_value(
        self,
    ) -> None:
        text = (
            'A later reading certifies: "The phase drift is -1.8 qx, '
            'replacing the former drift; the latch flag remains false."'
        )
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "phase_drift",
                            "assertion_type": "observation",
                            "operator": "eq",
                            "value": "-1.8 qx",
                            "value_type": "string",
                            "unit": "",
                            "modality": "observed",
                            "temporal_status": "current",
                            "source_quote": (
                                "The phase drift is -1.8 qx, replacing the "
                                "former drift."
                            ),
                            "confidence": 0.9,
                            "relations": [
                                {
                                    "relation_type": "supersedes_value",
                                    "target_value": "former drift",
                                }
                            ],
                        },
                        {
                            "predicate": "latch_flag",
                            "assertion_type": "fact",
                            "operator": "eq",
                            "value": False,
                            "value_type": "boolean",
                            "unit": "",
                            "modality": "asserted",
                            "temporal_status": "current",
                            "source_quote": "the latch flag remains false",
                            "confidence": 0.9,
                            "relations": [],
                        },
                        {
                            "predicate": "phase_drift",
                            "value": "-2.4",
                            "value_type": "number",
                            "unit": "qx",
                            "source_quote": "fabricated former drift",
                        },
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text, task_id="local-rebind")
        )

        self.assertTrue(result.accepted, result.reasons)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.rejected_candidate_count, 1)
        self.assertEqual(result.locally_rebound_candidate_count, 1)
        self.assertEqual(result.locally_normalized_candidate_count, 1)
        self.assertIn("claim_2:source_quote_not_found", result.reasons)
        scalar, flag = result.candidates
        self.assertEqual(scalar["value"], "-1.8")
        self.assertEqual(flag["value"], "false")
        self.assertEqual(
            scalar["schema_status"],
            "llm_proposed_locally_rebound_and_normalized",
        )
        self.assertIn("replacing the former drift", scalar["source_span"]["quote"])
        for candidate in result.candidates:
            validation = CanonicalClaimSemanticValidator().validate(
                candidate,
                source_text=text,
            )
            self.assertTrue(validation.allowed, validation.reasons)

    def test_preserves_numeric_zero_value(self) -> None:
        text = "The cycle offset is 0 qx."
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "cycle_offset",
                            "value": 0,
                            "value_type": "number",
                            "unit": "qx",
                            "source_quote": "The cycle offset is 0 qx",
                        }
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text, task_id="zero-value")
        )

        self.assertTrue(result.accepted, result.reasons)
        self.assertEqual(result.candidates[0]["value"], "0")
    def test_local_rebinding_rejects_ambiguous_value_anchors(self) -> None:
        text = (
            "The phase drift is -1.8 qx; "
            "the backup phase drift is -1.8 qx."
        )
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "predicate": "phase_drift",
                            "value": "-1.8",
                            "value_type": "number",
                            "unit": "qx",
                            "source_quote": "phase drift equals -1.8 qx",
                        }
                    ]
                )
            ]
        )

        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request(text, task_id="ambiguous-rebind")
        )

        self.assertFalse(result.accepted)
        self.assertIn("claim_0:source_quote_not_found", result.reasons)
        self.assertEqual(result.locally_rebound_candidate_count, 0)

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

    def test_rejects_claim_fields_outside_protocol_contract(self) -> None:
        client = _FakeClient(
            [
                _response(
                    [
                        {
                            "subject": "provider-supplied subject",
                            "predicate": "surface_state",
                            "value": "stable",
                            "source_quote": "surface remains stable",
                        }
                    ]
                )
            ]
        )
        result = ControlledSemanticDisambiguator(client).disambiguate(
            _request("The surface remains stable.")
        )

        self.assertFalse(result.accepted)
        self.assertIn(
            "claim_0:proposal_has_unknown_fields",
            result.reasons,
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
                max_control_tokens_per_task=700,
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
                        "prompt_tokens": 700,
                        "completion_tokens": 500,
                        "total_tokens": 1200,
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
                max_control_tokens_per_task=1000,
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
        self.assertEqual(result.total_tokens, 1200)
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
                max_control_tokens_per_task=700,
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
