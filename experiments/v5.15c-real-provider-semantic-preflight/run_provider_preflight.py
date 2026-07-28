from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.bridge.state_memory_bridge import StateToMemoryBridgeLite
from agent_runtime.llm.client import OpenAICompatibleChatClient
from agent_runtime.llm.config import LlmConfig
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.memory.semantic_disambiguator import (
    ControlledSemanticDisambiguator,
    SemanticDisambiguationBudget,
)


class RecordingClient:
    """Records Provider output and usage without prompts or credentials."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.case_id = ""
        self.records: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> Any:
        try:
            response = self.client.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except Exception as exc:
            self.records.append(
                {
                    "case_id": self.case_id,
                    "status": "provider_error",
                    "error_type": type(exc).__name__,
                }
            )
            raise
        usage = _mapping(_field(response, "usage", {}))
        guard = _mapping(_field(response, "provider_guard", {}))
        self.records.append(
            {
                "case_id": self.case_id,
                "status": "completed",
                "content": str(_field(response, "content", "") or ""),
                "model": str(_field(response, "model", "") or ""),
                "usage": {
                    "prompt_tokens": _positive_int(
                        usage.get("prompt_tokens")
                        or usage.get("input_tokens")
                    ),
                    "completion_tokens": _positive_int(
                        usage.get("completion_tokens")
                        or usage.get("output_tokens")
                    ),
                    "total_tokens": _positive_int(
                        usage.get("total_tokens")
                    ),
                },
                "latency_ms": float(
                    _field(response, "latency_ms", 0.0) or 0.0
                ),
                "retry_count": _nonnegative_int(
                    guard.get("retry_attempts")
                    or guard.get("retry_count")
                ),
            }
        )
        return response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a real Provider semantic-disambiguation preflight."
    )
    parser.add_argument("--scenario-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser.parse_args()


def run_preflight(
    *,
    scenario_payload: dict[str, Any],
    implementation_commit: str,
    client: Any,
    provider_mode: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    schema_supported = (
        scenario_payload.get("schema_version")
        == "agentlite.v515c.scenarios.v1"
    )
    commit_bound = (
        str(scenario_payload.get("authored_after_commit") or "")
        == implementation_commit
    )
    cases = scenario_payload.get("cases")
    if not isinstance(cases, list):
        cases = []

    recording_client = RecordingClient(client)
    disambiguator = ControlledSemanticDisambiguator(
        recording_client,
        budget=SemanticDisambiguationBudget(
            max_calls_per_task=1,
            max_source_chars=8_000,
            max_candidates_per_call=6,
            max_control_tokens_per_task=4_096,
        ),
    )
    case_rows: list[dict[str, Any]] = []
    provider_prompt_tokens = 0
    provider_completion_tokens = 0
    provider_total_tokens = 0
    provider_retry_count = 0
    provider_latency_ms = 0.0

    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            case = {}
        case_id = str(case.get("case_id") or f"case-{index + 1}")
        source_text = str(case.get("source_text") or "")
        expected = _mapping(case.get("expected"))
        recording_client.case_id = case_id
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(
            store,
            semantic_disambiguator=disambiguator,
        )
        report, validation = bridge.promote(
            task_id=f"preflight-{case_id}",
            scope_id=str(
                scenario_payload.get("scenario_id")
                or "v515c-external"
            ),
            source_agent="semantic-preflight",
            task_topic=str(
                case.get("subject")
                or f"external domain {case_id}"
            ),
            fallback_summary=source_text,
            tags=["semantic-preflight"],
            slot_hint="external_open_claim",
            source_state_ids=[f"state-{case_id}"],
            evidence_refs=[f"state-{case_id}"],
            reuse_intent="validate evidence-bound semantic reuse",
        )
        snapshot = store.snapshot()
        claims = snapshot.get("claim_cards") or []
        claim_candidates = snapshot.get("claim_candidates") or []
        semantic_match = any(
            _candidate_matches_expected(candidate, expected)
            for candidate in claim_candidates
            if isinstance(candidate, dict)
        )
        row = {
            "case_id": case_id,
            "domain": str(case.get("domain") or ""),
            "source_sha256": hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            "disambiguation_status": validation.disambiguation_status,
            "control_call_count": validation.disambiguation_call_count,
            "control_prompt_tokens": validation.control_prompt_tokens,
            "control_completion_tokens": (
                validation.control_completion_tokens
            ),
            "control_total_tokens": validation.control_total_tokens,
            "control_retry_count": validation.control_retry_count,
            "control_latency_ms": validation.control_latency_ms,
            "control_usage_estimated": validation.control_usage_estimated,
            "accepted_candidate_count": (
                validation.disambiguation_accepted_candidate_count
            ),
            "rejected_candidate_count": (
                validation.disambiguation_rejected_candidate_count
            ),
            "validation_allowed": validation.allowed,
            "validation_reasons": list(validation.reasons),
            "admission_status": report.admission_status,
            "memory_write_count": report.memory_write_count,
            "claim_count": len(claims),
            "claim_candidate_count": len(claim_candidates),
            "semantic_match": semantic_match,
            "strict_success": (
                validation.disambiguation_status == "accepted"
                and validation.disambiguation_call_count == 1
                and validation.allowed
                and report.admission_status == "admitted"
                and report.memory_write_count >= 1
                and semantic_match
            ),
            "claims": [
                _public_claim_fields(claim)
                for claim in claims
                if isinstance(claim, dict)
            ],
            "claim_candidates": [
                _public_candidate_fields(candidate)
                for candidate in claim_candidates
                if isinstance(candidate, dict)
            ],
        }
        case_rows.append(row)
        provider_prompt_tokens += validation.control_prompt_tokens
        provider_completion_tokens += validation.control_completion_tokens
        provider_total_tokens += validation.control_total_tokens
        provider_retry_count += validation.control_retry_count
        provider_latency_ms += validation.control_latency_ms

    domains = {
        row["domain"].strip().casefold()
        for row in case_rows
        if row["domain"].strip()
    }
    strict_success_count = sum(row["strict_success"] for row in case_rows)
    provider_call_count = sum(
        row["control_call_count"] for row in case_rows
    )
    unsafe_evidence_escape_count = sum(
        row["admission_status"] == "admitted"
        and not row["validation_allowed"]
        for row in case_rows
    )
    checks = [
        _check(
            "scenario_schema_supported",
            schema_supported,
            str(scenario_payload.get("schema_version") or ""),
        ),
        _check(
            "scenario_is_bound_to_implementation_commit",
            commit_bound,
            (
                f"scenario={scenario_payload.get('authored_after_commit')};"
                f"implementation={implementation_commit}"
            ),
        ),
        _check(
            "cross_domain_case_count_is_sufficient",
            len(case_rows) >= 5 and len(domains) >= 5,
            f"cases={len(case_rows)};domains={len(domains)}",
        ),
        _check(
            "all_cases_reached_real_control_path",
            provider_call_count == len(case_rows),
            f"calls={provider_call_count};cases={len(case_rows)}",
        ),
        _check(
            "strict_semantic_success_rate_meets_preflight_target",
            bool(case_rows)
            and strict_success_count / len(case_rows) >= 0.8,
            f"strict={strict_success_count}/{len(case_rows)}",
        ),
        _check(
            "provider_usage_is_complete",
            provider_call_count > 0
            and provider_prompt_tokens > 0
            and provider_completion_tokens > 0
            and provider_total_tokens
            >= provider_prompt_tokens + provider_completion_tokens,
            (
                f"calls={provider_call_count};prompt={provider_prompt_tokens};"
                f"completion={provider_completion_tokens};"
                f"total={provider_total_tokens}"
            ),
        ),
        _check(
            "no_unmetered_provider_retry",
            provider_retry_count == 0,
            (
                f"retry_count={provider_retry_count};"
                "failed-attempt Provider tokens are not observable"
            ),
        ),
        _check(
            "unsafe_evidence_does_not_enter_memory",
            unsafe_evidence_escape_count == 0,
            f"escapes={unsafe_evidence_escape_count}",
        ),
        _check(
            "provider_outputs_are_recorded_per_call",
            len(recording_client.records) == provider_call_count,
            (
                f"records={len(recording_client.records)};"
                f"calls={provider_call_count}"
            ),
        ),
    ]
    passed = all(item["passed"] for item in checks)
    return (
        {
            "schema_version": "agentlite.v515c.provider-preflight.v1",
            "summary": {
                "passed": passed,
                "ready_for_integrated_preflight": passed,
                "check_count": len(checks),
                "passed_check_count": sum(
                    item["passed"] for item in checks
                ),
                "case_count": len(case_rows),
                "domain_count": len(domains),
                "strict_success_count": strict_success_count,
            },
            "binding": {
                "scenario_id": str(
                    scenario_payload.get("scenario_id") or ""
                ),
                "authored_after_commit": str(
                    scenario_payload.get("authored_after_commit") or ""
                ),
                "implementation_commit": implementation_commit,
                "provider_mode": provider_mode,
            },
            "cost": {
                "provider_call_count": provider_call_count,
                "provider_prompt_tokens": provider_prompt_tokens,
                "provider_completion_tokens": provider_completion_tokens,
                "provider_total_tokens": provider_total_tokens,
                "control_llm_tokens": provider_total_tokens,
                "control_retry_count": provider_retry_count,
                "control_latency_ms": provider_latency_ms,
                "communication_tokens": 0,
                "retrieved_memory_tokens": 0,
                "retry_tokens": 0,
                "end_to_end_tokens": provider_total_tokens,
                "double_counted_token_count": 0,
            },
            "quality": {
                "strict_success_count": strict_success_count,
                "strict_success_rate": (
                    strict_success_count / len(case_rows)
                    if case_rows
                    else 0.0
                ),
                "unsafe_evidence_escape_count": (
                    unsafe_evidence_escape_count
                ),
            },
            "cases": case_rows,
            "checks": checks,
        },
        recording_client.records,
    )


def build_real_client() -> OpenAICompatibleChatClient:
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip()
    api_key_env = os.getenv(
        "AGENTLITE_V515C_API_KEY_ENV",
        "OPENAI_API_KEY",
    ).strip()
    if not base_url:
        raise RuntimeError("OPENAI_BASE_URL is required")
    if not model:
        raise RuntimeError("OPENAI_MODEL is required")
    return OpenAICompatibleChatClient(
        LlmConfig(
            provider="openai-compatible-control",
            base_url=base_url,
            model=model,
            api_key_env=api_key_env,
            auth_scheme="authorization_bearer",
            timeout_seconds=_positive_float_env(
                "OPENAI_TIMEOUT_SECONDS",
                300.0,
            ),
            max_retries=_nonnegative_int_env(
                "OPENAI_MAX_RETRIES",
                3,
            ),
            retry_backoff_seconds=_positive_float_env(
                "OPENAI_RETRY_BACKOFF_SECONDS",
                3.0,
            ),
            temperature=0.0,
            top_p=1.0,
        )
    )


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    scenario_file = args.scenario_file.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    scenario_payload = json.loads(
        scenario_file.read_text(encoding="utf-8")
    )
    implementation_commit = _git_output(
        repo_root,
        "rev-parse",
        "HEAD",
    ).strip()
    report, provider_outputs = run_preflight(
        scenario_payload=scenario_payload,
        implementation_commit=implementation_commit,
        client=build_real_client(),
        provider_mode="real_openai_compatible",
    )
    (output_dir / "preflight_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "provider_outputs.jsonl").write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in provider_outputs
        ),
        encoding="utf-8",
    )
    (output_dir / "preflight_report.md").write_text(
        _render_markdown(report),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


def _candidate_matches_expected(
    candidate: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    predicate_aliases = expected.get("predicate_aliases")
    if not isinstance(predicate_aliases, list):
        predicate_aliases = [expected.get("predicate")]
    normalized_aliases = {
        str(item or "").strip().casefold()
        for item in predicate_aliases
        if str(item or "").strip()
    }
    predicate = str(
        candidate.get("predicate") or ""
    ).strip().casefold()
    if normalized_aliases and predicate not in normalized_aliases:
        return False
    for field in ("operator", "value", "unit", "temporal_status"):
        if field not in expected:
            continue
        actual = (
            candidate.get("object")
            if field == "value"
            else candidate.get(field)
        )
        if str(actual or "").strip().casefold() != str(
            expected.get(field) or ""
        ).strip().casefold():
            return False
    return True


def _public_claim_fields(claim: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: claim.get(key)
        for key in (
            "claim_id",
            "subject",
            "predicate",
            "assertion_type",
            "operator",
            "value",
            "value_type",
            "unit",
            "polarity",
            "modality",
            "temporal_status",
            "source_span",
            "schema_layer",
            "status",
        )
    }


def _public_candidate_fields(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        key: candidate.get(key)
        for key in (
            "candidate_id",
            "subject",
            "predicate",
            "object",
            "assertion_type",
            "operator",
            "value_type",
            "unit",
            "polarity",
            "modality",
            "temporal_status",
            "source_span",
            "schema_layer",
            "admission_status",
        )
    }


def _field(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _nonnegative_int(value: Any) -> int:
    return _positive_int(value)


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _nonnegative_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


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


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "detail": str(detail),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    cost = report["cost"]
    quality = report["quality"]
    lines = [
        "# v5.15c Real Provider Semantic Preflight",
        "",
        f"- Overall passed: `{summary['passed']}`",
        (
            "- Passed checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- Strict semantic success: "
            f"`{quality['strict_success_count']}/{summary['case_count']}`"
        ),
        f"- Provider calls: `{cost['provider_call_count']}`",
        f"- Provider total tokens: `{cost['provider_total_tokens']}`",
        f"- End-to-end tokens: `{cost['end_to_end_tokens']}`",
        "",
        "## Checks",
        "",
    ]
    for item in report["checks"]:
        marker = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- [{marker}] `{item['name']}`: {item['detail']}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
