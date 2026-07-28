from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.bridge.state_memory_bridge import (
    CanonicalClaimSemanticValidator,
    StateToMemoryBridgeLite,
)
from agent_runtime.eval.autogen_session_report import _metric_rows
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.memory.semantic_disambiguator import (
    ControlledSemanticDisambiguator,
    SemanticDisambiguationBudget,
    SemanticDisambiguationRequest,
)
from web_monitor.parser import _autogen_token_summary


@dataclass
class _Response:
    content: str
    usage: dict[str, int]
    model: str = "scripted-control"
    latency_ms: float = 7.5
    provider_guard: dict[str, int] | None = None


class _Client:
    def __init__(self, responses: list[_Response | Exception]) -> None:
        self.responses = list(responses)
        self.call_count = 0

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> _Response:
        del system_prompt, user_prompt
        self.call_count += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify controlled semantic disambiguation mechanisms."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--unittest-output", type=Path, required=True)
    parser.add_argument("--holdout-file", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(
    *,
    repo_root: Path,
    unittest_output: Path,
    holdout_file: Path,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    test_text = _read_text_output(unittest_output)
    checks.append(
        _check(
            "focused_unittest_suite_passed",
            "\nOK\n" in test_text.replace("\r\n", "\n"),
            _last_nonempty_line(test_text),
        )
    )

    internal_cases = [
        {
            "case_id": "optical",
            "source": (
                "After cycle seven, the spectrograph remained aligned."
            ),
            "proposal": {
                "predicate": "spectrograph_state",
                "assertion_type": "observation",
                "operator": "eq",
                "value": "aligned",
                "value_type": "string",
                "modality": "observed",
                "temporal_status": "current",
                "source_quote": "the spectrograph remained aligned",
            },
        },
        {
            "case_id": "legal",
            "source": "Clause Zeta remains enforceable through 2031.",
            "proposal": {
                "predicate": "clause_status",
                "assertion_type": "fact",
                "operator": "eq",
                "value": "enforceable",
                "value_type": "string",
                "temporal_status": "current",
                "source_quote": (
                    "Clause Zeta remains enforceable through 2031"
                ),
            },
        },
        {
            "case_id": "ecological",
            "source": (
                "During sampling, the canopy response was nonreversible."
            ),
            "proposal": {
                "predicate": "canopy_response",
                "assertion_type": "observation",
                "operator": "eq",
                "value": "nonreversible",
                "value_type": "string",
                "modality": "observed",
                "temporal_status": "historical",
                "source_quote": (
                    "the canopy response was nonreversible"
                ),
            },
        },
        {
            "case_id": "materials",
            "source": (
                "The coating thickness stayed below 4 micrometers."
            ),
            "proposal": {
                "predicate": "coating_thickness",
                "assertion_type": "constraint",
                "operator": "lt",
                "value": "4",
                "value_type": "number",
                "unit": "micrometers",
                "temporal_status": "current",
                "source_quote": (
                    "coating thickness stayed below 4 micrometers"
                ),
            },
        },
    ]
    internal_results: list[dict[str, Any]] = []
    for case in internal_cases:
        result = _run_case(case)
        internal_results.append(result)
        checks.append(
            _check(
                f"internal_case_{case['case_id']}_accepted",
                result["accepted"] and result["span_valid"],
                json.dumps(result, ensure_ascii=False),
            )
        )

    metamorphic_failures: list[str] = []
    for case in internal_cases:
        expected = case["proposal"]
        for label, transformed in (
            ("prefix", "Recorded note: " + case["source"]),
            ("suffix", case["source"] + " End of record."),
        ):
            result = _run_case({**case, "source": transformed})
            candidate = result.get("candidate") or {}
            semantic_identity = (
                candidate.get("predicate"),
                candidate.get("operator"),
                candidate.get("value"),
                candidate.get("unit", ""),
            )
            expected_identity = (
                expected["predicate"],
                expected.get("operator", "eq"),
                str(expected["value"]),
                expected.get("unit", ""),
            )
            if (
                not result["accepted"]
                or not result["span_valid"]
                or semantic_identity != expected_identity
            ):
                metamorphic_failures.append(
                    f"{case['case_id']}:{label}:{result}"
                )
    checks.append(
        _check(
            "prefix_and_suffix_transformations_preserve_semantics",
            not metamorphic_failures,
            repr(metamorphic_failures),
        )
    )

    repeated = _run_case(
        {
            "case_id": "repeated",
            "source": "signal settled; later signal settled.",
            "proposal": {
                "predicate": "signal_state",
                "value": "settled",
                "source_quote": "signal settled",
            },
        }
    )
    fabricated = _run_case(
        {
            "case_id": "fabricated",
            "source": "The specimen state was not classified.",
            "proposal": {
                "predicate": "specimen_state",
                "value": "stable",
                "source_quote": "specimen was stable",
            },
        }
    )
    malformed_client = _Client(
        [
            _Response(
                content="```json\n{\"claims\": []}\n```",
                usage={
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            )
        ]
    )
    malformed = ControlledSemanticDisambiguator(
        malformed_client
    ).disambiguate(
        _request(
            scope_id="scope-malformed",
            task_id="task-malformed",
            source_id="source-malformed",
            subject="subject-malformed",
            source_text="Unstructured source.",
        )
    )
    provider_client = _Client([TimeoutError("private provider detail")])
    provider_error = ControlledSemanticDisambiguator(
        provider_client
    ).disambiguate(
        _request(
            scope_id="scope-provider",
            task_id="task-provider",
            source_id="source-provider",
            subject="subject-provider",
            source_text="Unstructured source.",
        )
    )
    prompt_budget_client = _Client([])
    prompt_budget = ControlledSemanticDisambiguator(
        prompt_budget_client,
        budget=SemanticDisambiguationBudget(
            max_calls_per_task=1,
            max_source_chars=100,
            max_candidates_per_call=2,
            max_control_tokens_per_task=200,
        ),
    ).disambiguate(
        _request(
            scope_id="scope-bounded",
            task_id="task-bounded",
            source_id="source-bounded",
            subject="subject-bounded",
            source_text="The oscillation mode was damped.",
        )
    )
    checks.extend(
        [
            _check(
                "repeated_quote_is_rejected_without_span_guessing",
                not repeated["accepted"]
                and "claim_0:source_quote_not_unique"
                in repeated["reasons"],
                json.dumps(repeated, ensure_ascii=False),
            ),
            _check(
                "fabricated_quote_is_rejected",
                not fabricated["accepted"]
                and "claim_0:source_quote_not_found"
                in fabricated["reasons"],
                json.dumps(fabricated, ensure_ascii=False),
            ),
            _check(
                "malformed_control_response_fails_closed",
                malformed.status == "rejected"
                and "control_response_not_strict_json"
                in malformed.reasons,
                repr(malformed),
            ),
            _check(
                "provider_error_is_audit_only_and_message_is_not_exposed",
                provider_error.status == "provider_error"
                and provider_error.reasons
                == ("provider_error:TimeoutError",)
                and "private provider detail"
                not in repr(provider_error),
                repr(provider_error),
            ),
            _check(
                "prompt_budget_blocks_call_before_provider",
                prompt_budget.status == "budget_exhausted"
                and not prompt_budget_client.call_count,
                repr(prompt_budget),
            ),
        ]
    )

    bridge_case = internal_cases[0]
    bridge_client = _Client([_response(bridge_case["proposal"])])
    store = MemoryStoreLite()
    bridge = StateToMemoryBridgeLite(
        store,
        semantic_disambiguator=ControlledSemanticDisambiguator(
            bridge_client
        ),
    )
    bridge_report, bridge_validation = bridge.promote(
        task_id="task-bridge",
        scope_id="scope-bridge",
        source_agent="node-bridge",
        task_topic="cross-domain holdout",
        fallback_summary=bridge_case["source"],
        tags=["mechanism"],
        slot_hint="reuse_strategy",
        source_state_ids=["state-bridge"],
        evidence_refs=["state-bridge"],
        reuse_intent="reuse validated evidence",
    )
    checks.append(
        _check(
            "accepted_proposal_uses_normal_memory_admission",
            bridge_validation.allowed
            and bridge_report.admission_status == "admitted"
            and bridge_report.memory_write_count == 1
            and bool(store.snapshot()["claim_cards"]),
            (
                f"validation={bridge_validation};"
                f"report={bridge_report}"
            ),
        )
    )

    token_summary = _autogen_token_summary(
        [
            {
                "event_type": "state_memory_bridge",
                "payload": {
                    "semantic_disambiguation": {
                        "status": "accepted",
                        "call_count": 1,
                        "accepted_candidate_count": 1,
                        "rejected_candidate_count": 1,
                        "prompt_tokens": 80,
                        "completion_tokens": 20,
                        "total_tokens": 100,
                        "retry_count": 1,
                        "latency_ms": 7.5,
                    }
                },
            }
        ]
    )
    metric_rows = {
        row["metric"]: row["value"]
        for row in _metric_rows(token_summary)
    }
    checks.append(
        _check(
            "control_cost_is_in_end_to_end_report",
            token_summary["control_llm_call_count"] == 1
            and token_summary["control_llm_tokens"] == 100
            and token_summary["end_to_end_collaboration_tokens"] == 100
            and metric_rows["agentlite_control_llm_tokens"] == 100,
            json.dumps(token_summary, ensure_ascii=False),
        )
    )

    holdout = _evaluate_holdout(
        repo_root=repo_root,
        holdout_file=holdout_file,
    )
    checks.extend(holdout["checks"])
    narrow_terms = _added_benchmark_identifiers(repo_root)
    checks.append(
        _check(
            "production_diff_has_no_benchmark_specific_identifier",
            not narrow_terms,
            repr(narrow_terms),
        )
    )

    passed = all(item["passed"] for item in checks)
    simulated_calls = sum(
        int(item.get("call_count", 0))
        for item in internal_results
    )
    simulated_tokens = sum(
        int(item.get("total_tokens", 0))
        for item in internal_results
    )
    return {
        "schema_version": "agentlite.v515b.acceptance-report.v1",
        "summary": {
            "passed": passed,
            "check_count": len(checks),
            "passed_check_count": sum(
                item["passed"] for item in checks
            ),
            "internal_domain_count": len(internal_cases),
            "metamorphic_case_count": len(internal_cases) * 2,
            "holdout_positive_case_count": holdout[
                "positive_case_count"
            ],
            "holdout_negative_case_count": holdout[
                "negative_case_count"
            ],
            "benchmark_identifier_match_count": len(narrow_terms),
        },
        "cost": {
            "real_provider_call_count": 0,
            "scripted_control_call_count": simulated_calls,
            "scripted_control_tokens": simulated_tokens,
            "reported_control_tokens": token_summary[
                "control_llm_tokens"
            ],
            "reported_end_to_end_tokens": token_summary[
                "end_to_end_collaboration_tokens"
            ],
            "cost_scope": "scripted_mechanism_gate",
        },
        "quality": {
            "internal_case_pass_count": sum(
                result["accepted"] and result["span_valid"]
                for result in internal_results
            ),
            "metamorphic_failure_count": len(
                metamorphic_failures
            ),
            "holdout_passed": holdout["passed"],
            "unsafe_evidence_escape_count": int(
                repeated["accepted"] or fabricated["accepted"]
            ),
        },
        "holdout": {
            "holdout_id": holdout["holdout_id"],
            "authored_after_commit": holdout[
                "authored_after_commit"
            ],
        },
        "checks": checks,
    }


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    client = _Client([_response(case["proposal"])])
    result = ControlledSemanticDisambiguator(client).disambiguate(
        _request(
            scope_id=f"scope-{case['case_id']}",
            task_id=f"task-{case['case_id']}",
            source_id=f"source-{case['case_id']}",
            subject=f"subject-{case['case_id']}",
            source_text=case["source"],
        )
    )
    candidate = dict(result.candidates[0]) if result.candidates else {}
    validation = CanonicalClaimSemanticValidator().validate(
        candidate,
        source_text=case["source"],
    ) if candidate else None
    return {
        "status": result.status,
        "accepted": result.accepted,
        "reasons": list(result.reasons),
        "candidate": candidate,
        "span_valid": bool(validation and validation.allowed),
        "call_count": result.call_count,
        "total_tokens": result.total_tokens,
    }


def _response(proposal: dict[str, Any]) -> _Response:
    return _Response(
        content=json.dumps(
            {
                "schema_version": (
                    "agentlite.semantic-disambiguation.response.v1"
                ),
                "claims": [proposal],
            },
            ensure_ascii=False,
        ),
        usage={
            "prompt_tokens": 80,
            "completion_tokens": 20,
            "total_tokens": 100,
        },
        provider_guard={"retry_attempts": 1},
    )


def _request(**kwargs: str) -> SemanticDisambiguationRequest:
    return SemanticDisambiguationRequest(**kwargs)


def _evaluate_holdout(
    *,
    repo_root: Path,
    holdout_file: Path,
) -> dict[str, Any]:
    payload = json.loads(holdout_file.read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []
    current_commit = _git_output(
        repo_root,
        "rev-parse",
        "HEAD",
    ).strip()
    holdout_id = str(payload.get("holdout_id") or "")
    authored_after_commit = str(
        payload.get("authored_after_commit") or ""
    )
    positive_cases = payload.get("positive_cases")
    negative_cases = payload.get("negative_cases")
    if not isinstance(positive_cases, list):
        positive_cases = []
    if not isinstance(negative_cases, list):
        negative_cases = []
    checks.extend(
        [
            _check(
                "external_holdout_schema_is_supported",
                payload.get("schema_version")
                == "agentlite.v515b.holdout.v1",
                str(payload.get("schema_version") or ""),
            ),
            _check(
                "external_holdout_has_unique_identity",
                bool(holdout_id),
                holdout_id or "missing",
            ),
            _check(
                "external_holdout_is_bound_to_implementation_commit",
                authored_after_commit == current_commit,
                (
                    f"holdout={authored_after_commit};"
                    f"current={current_commit}"
                ),
            ),
            _check(
                "external_holdout_has_cross_domain_coverage",
                len(positive_cases) >= 3
                and len(negative_cases) >= 2,
                (
                    f"positive={len(positive_cases)};"
                    f"negative={len(negative_cases)}"
                ),
            ),
        ]
    )
    for index, case in enumerate(positive_cases):
        case_id = str(case.get("case_id") or f"positive-{index}")
        run = _run_case(
            {
                "case_id": case_id,
                "source": str(case.get("source_text") or ""),
                "proposal": dict(case.get("proposal") or {}),
            }
        )
        candidate = run.get("candidate") or {}
        expected = case.get("expected")
        if not isinstance(expected, dict):
            expected = {}
        matched = all(
            str(candidate.get(key) or "")
            == str(expected.get(key) or "")
            for key in (
                "predicate",
                "operator",
                "value",
                "unit",
                "temporal_status",
            )
        )
        checks.append(
            _check(
                f"holdout_positive_{case_id}",
                run["accepted"] and run["span_valid"] and matched,
                json.dumps(
                    {"run": run, "expected": expected},
                    ensure_ascii=False,
                ),
            )
        )
    for index, case in enumerate(negative_cases):
        case_id = str(case.get("case_id") or f"negative-{index}")
        kind = str(case.get("kind") or "")
        run = _run_case(
            {
                "case_id": case_id,
                "source": str(case.get("source_text") or ""),
                "proposal": dict(case.get("proposal") or {}),
            }
        )
        expected_reason = {
            "repeated_quote": "claim_0:source_quote_not_unique",
            "missing_quote": "claim_0:source_quote_not_found",
        }.get(kind, "")
        checks.append(
            _check(
                f"holdout_negative_{case_id}",
                bool(expected_reason)
                and not run["accepted"]
                and expected_reason in run["reasons"],
                json.dumps(
                    {"kind": kind, "run": run},
                    ensure_ascii=False,
                ),
            )
        )
    return {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "holdout_id": holdout_id,
        "authored_after_commit": authored_after_commit,
        "positive_case_count": len(positive_cases),
        "negative_case_count": len(negative_cases),
    }


def _added_benchmark_identifiers(repo_root: Path) -> list[str]:
    source_diff = _git_output(
        repo_root,
        "show",
        "--format=",
        "--unified=0",
        "HEAD",
        "--",
        "agent_runtime",
    )
    added = "\n".join(
        line[1:]
        for line in source_diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ).casefold()
    forbidden = (
        "question_a",
        "question_b",
        "a1-a10",
        "b1-b10",
        "ordinary-developer",
        "travel_delivery",
    )
    return [term for term in forbidden if term in added]


def _git_output(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def _read_text_output(path: Path) -> str:
    payload = path.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in payload:
        return payload.decode("utf-16", errors="replace")
    return payload.decode("utf-8", errors="replace")


def _last_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "empty unittest output"


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "detail": str(detail),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15b Controlled Semantic Disambiguation Acceptance",
        "",
        f"- Overall passed: `{summary['passed']}`",
        (
            "- Passed checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- External holdout: "
            f"`{report['holdout']['holdout_id']}`"
        ),
        (
            "- Real Provider calls: "
            f"`{report['cost']['real_provider_call_count']}`"
        ),
        "",
        "## Checks",
        "",
    ]
    for item in report["checks"]:
        marker = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- [{marker}] `{item['name']}`: {item['detail']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    report = evaluate(
        repo_root=args.repo_root.resolve(),
        unittest_output=args.unittest_output.resolve(),
        holdout_file=args.holdout_file.resolve(),
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.output_markdown.write_text(
        _render_markdown(report),
        encoding="utf-8",
    )
    print(
        json.dumps(
            report["summary"],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
