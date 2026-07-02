from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXAMPLES_DIR = Path(__file__).resolve().parent
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from agent_runtime.launcher import (  # noqa: E402
    LaunchRequest,
    ManagedProcessLauncher,
    read_bootstrap_status,
)
from run_autogen_broadcast_shadow_smoke import (  # noqa: E402
    summarize_agent_real_rewrite_events,
)
from run_autogen_native_smoke import _missing_modules, _read_trace_events  # noqa: E402

EXPECTED_REASONS_OFF = {
    "non_text_message_present",
    "handoff_rewrite_requires_target_preservation",
    "handoff_typed_rewrite_candidate_dry_run_only",
    "tool_rewrite_requires_call_lineage",
    "tool_rewrite_requires_result_lineage",
}
EXPECTED_BUCKETS_OFF = {
    "unsupported_message_type",
    "handoff_control_guard",
    "typed_rewrite_dry_run_guard",
    "tool_lineage_guard",
}
EXPECTED_REASONS_ON = {
    "non_text_message_present",
    "tool_rewrite_requires_call_lineage",
    "tool_rewrite_requires_result_lineage",
}
EXPECTED_BUCKETS_ON = {
    "unsupported_message_type",
    "tool_lineage_guard",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a guarded AutoGen Handoff/ToolCall real-rewrite smoke. "
            "The driver should audit typed rewrite requirements and keep native "
            "AutoGen messages unchanged."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--handoff-rewrite",
        choices=("off", "on"),
        default="off",
        help=(
            "Enable real HandoffMessage content rewrite when the typed candidate "
            "passes all gates. Defaults to off."
        ),
    )
    parser.add_argument(
        "--skip-dependency-check",
        action="store_true",
        help="Launch anyway even if AutoGen modules are not importable yet.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.skip_dependency_check:
        missing = _missing_modules()
        if missing:
            print(
                "Missing AutoGen modules: " + ", ".join(missing),
                file=sys.stderr,
            )
            print(
                'Install after confirmation with: python -m pip install -e ".[autogen]"',
                file=sys.stderr,
            )
            return 2

    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = (
            PROJECT_ROOT
            / "runs"
            / f"v5.13h-autogen-handoff-rewrite-{args.handoff_rewrite}-{stamp}"
        )
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_guard_smoke(
        output_dir=output_dir,
        python=args.python,
        handoff_rewrite=args.handoff_rewrite,
    )
    report_path = output_dir / "autogen_handoff_tool_rewrite_guard_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_handoff_tool_rewrite_guard_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_guard_smoke(
    *,
    output_dir: Path,
    python: str,
    handoff_rewrite: str = "off",
) -> dict[str, Any]:
    app_output = output_dir / "autogen_handoff_tool_rewrite_guard_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            python,
            str(
                PROJECT_ROOT
                / "examples"
                / "autogen_handoff_tool_rewrite_guard_smoke.py"
            ),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_HANDOFF_REWRITE": "1"
        if handoff_rewrite == "on"
        else "0",
        "AGENTLITE_AUTOGEN_HANDOFF_TOOL_REWRITE_GUARD_OUTPUT": str(app_output),
    }
    result = ManagedProcessLauncher().launch(request, environ=env)
    status = read_bootstrap_status(result.status_file) or {}
    details = status.get("driver_details", {})
    if not isinstance(details, dict):
        details = {}
    trace_path = Path(str(details.get("trace_path", "")))
    trace_events = _read_trace_events(trace_path)
    event_counts = Counter(str(event.get("event_type", "")) for event in trace_events)
    app_payload = (
        json.loads(app_output.read_text(encoding="utf-8"))
        if app_output.exists()
        else {}
    )
    agent_rewrite_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_agent_input_real_rewrite"
        and isinstance(event.get("payload"), dict)
    ]
    rewrite_summary = summarize_agent_real_rewrite_events(agent_rewrite_events)
    safety_summary = summarize_rewrite_safety(agent_rewrite_events)
    candidate_summary = summarize_typed_rewrite_candidates(agent_rewrite_events)
    cases = app_payload.get("cases", [])
    if not isinstance(cases, list):
        cases = []
    case_map = {str(case.get("case_id")): case for case in cases if isinstance(case, dict)}
    handoff_seen = _first_seen(case_map.get("handoff_target_guard", {}))
    tool_seen = _first_seen(case_map.get("tool_summary_lineage_guard", {}))
    handoff_rewrite_enabled = bool(details.get("handoff_rewrite_enabled"))
    expected_handoff_applied_count = 1 if handoff_rewrite_enabled else 0
    expected_fallback_count = 1 if handoff_rewrite_enabled else 2
    expected_safe_to_mutate_false_count = 1 if handoff_rewrite_enabled else 2
    expected_reasons = (
        EXPECTED_REASONS_ON if handoff_rewrite_enabled else EXPECTED_REASONS_OFF
    )
    expected_buckets = (
        EXPECTED_BUCKETS_ON if handoff_rewrite_enabled else EXPECTED_BUCKETS_OFF
    )
    checks = {
        "target_returncode_zero": result.returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "driver_phase_v5_13h": details.get("phase") == "v5.13h",
        "handoff_rewrite_mode_matches_request": handoff_rewrite_enabled
        == (handoff_rewrite == "on"),
        "two_cases_recorded": len(cases) == 2,
        "two_guard_events_recorded": int(rewrite_summary.get("event_count", 0) or 0)
        == 2,
        "expected_handoff_rewrite_applied_count": int(
            rewrite_summary.get("applied_count", 0) or 0
        )
        == expected_handoff_applied_count,
        "expected_fallback_count": int(rewrite_summary.get("fallback_count", 0) or 0)
        == expected_fallback_count,
        "all_expected_reasons_recorded": expected_reasons
        <= set(rewrite_summary.get("fallback_reasons", []) or []),
        "all_expected_buckets_recorded": expected_buckets
        <= set(rewrite_summary.get("fallback_buckets", []) or []),
        "real_message_mutation_count_matches_mode": int(
            rewrite_summary.get("real_message_mutation_count", 0) or 0
        )
        == expected_handoff_applied_count,
        "handoff_control_fields_preserved": handoff_seen.get("type") == "HandoffMessage"
        and handoff_seen.get("id") == "handoff_guard_1"
        and handoff_seen.get("target") == "guard"
        and handoff_seen.get("source") == "router"
        and handoff_seen.get("metadata", {}).get("route") == "guard_candidate",
        "handoff_content_matches_mode": (
            bool(handoff_seen.get("contains_handoff_candidate_marker"))
            and int(handoff_seen.get("handoff_marker_count", 0) or 0) < 14
            if handoff_rewrite_enabled
            else bool(handoff_seen.get("contains_handoff_marker"))
            and not bool(handoff_seen.get("contains_rewrite_marker"))
            and int(handoff_seen.get("handoff_marker_count", 0) or 0) >= 14
        ),
        "tool_summary_native_preserved": tool_seen.get("type")
        == "ToolCallSummaryMessage"
        and "call_guard_1" in set(tool_seen.get("tool_call_ids", []) or [])
        and "call_guard_1" in set(tool_seen.get("tool_result_call_ids", []) or [])
        and bool(tool_seen.get("contains_tool_marker"))
        and not bool(tool_seen.get("contains_rewrite_marker")),
        "safety_contract_recorded": "autogen_non_text_real_rewrite_guard.v1"
        in set(safety_summary.get("contracts", []) or []),
        "safety_requires_native_preservation": int(
            safety_summary.get("native_preservation_required_count", 0) or 0
        )
        == 2,
        "safety_marks_mutation_unsafe": int(
            safety_summary.get("safe_to_mutate_false_count", 0) or 0
        )
        == expected_safe_to_mutate_false_count,
        "handoff_target_audited": "guard"
        in set(safety_summary.get("handoff_targets", []) or []),
        "tool_lineage_audited": "call_guard_1"
        in set(safety_summary.get("tool_call_ids", []) or [])
        and "call_guard_1" in set(safety_summary.get("tool_result_call_ids", []) or []),
        "handoff_typed_candidate_recorded": int(
            candidate_summary.get("event_count", 0) or 0
        )
        >= 1,
        "handoff_typed_candidate_contract_recorded": (
            "autogen_handoff_typed_rewrite_candidate.v1"
            in set(candidate_summary.get("contracts", []) or [])
        ),
        "handoff_typed_candidate_semantic_safe": int(
            candidate_summary.get("candidate_safe_count", 0) or 0
        )
        >= 1,
        "handoff_typed_candidate_mutation_matches_mode": int(
            candidate_summary.get("mutation_applied_count", 0) or 0
        )
        == expected_handoff_applied_count,
        "handoff_typed_candidate_preserves_control_fields": {
            "message_type_preserved",
            "source_preserved",
            "target_preserved",
            "id_preserved",
            "metadata_preserved",
            "context_preserved",
        }
        <= set(candidate_summary.get("passing_semantic_checks", []) or []),
        "handoff_typed_candidate_reduces_tokens": int(
            candidate_summary.get("token_delta_native_minus_candidate", 0) or 0
        )
        > 0,
    }
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "returncode": result.returncode,
        "checks": checks,
        "handoff_rewrite_requested": handoff_rewrite,
        "handoff_rewrite_enabled": handoff_rewrite_enabled,
        "driver_phase": details.get("phase", ""),
        "driver_broadcast_mode": details.get("broadcast_mode", ""),
        "trace_path": str(trace_path) if trace_path else "",
        "app_output_path": str(app_output),
        "trace_event_counts": dict(sorted(event_counts.items())),
        "agent_input_real_rewrite": rewrite_summary,
        "rewrite_safety": safety_summary,
        "typed_rewrite_candidate": candidate_summary,
        "case_ids": sorted(case_map),
        "app_payload": app_payload,
    }


def summarize_rewrite_safety(events: list[dict[str, Any]]) -> dict[str, Any]:
    contracts: set[str] = set()
    message_kinds: set[str] = set()
    fallback_reasons: set[str] = set()
    fallback_buckets: set[str] = set()
    required_native_fields: set[str] = set()
    handoff_targets: set[str] = set()
    tool_call_ids: set[str] = set()
    tool_result_call_ids: set[str] = set()
    native_preservation_required_count = 0
    safe_to_mutate_false_count = 0
    lineage_complete_count = 0
    rows: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        safety = payload.get("rewrite_safety", {})
        if not isinstance(safety, dict) or not safety:
            continue
        contract = str(safety.get("contract", ""))
        if contract:
            contracts.add(contract)
        if safety.get("native_preservation_required"):
            native_preservation_required_count += 1
        if safety.get("safe_to_mutate") is False:
            safe_to_mutate_false_count += 1
        if safety.get("tool_result_lineage_complete"):
            lineage_complete_count += 1
        _extend_set(message_kinds, safety.get("message_kinds", []))
        _extend_set(fallback_reasons, safety.get("fallback_reasons", []))
        _extend_set(fallback_buckets, safety.get("fallback_buckets", []))
        _extend_set(required_native_fields, safety.get("required_native_fields", []))
        _extend_set(handoff_targets, safety.get("handoff_targets", []))
        _extend_set(tool_call_ids, safety.get("tool_call_ids", []))
        _extend_set(tool_result_call_ids, safety.get("tool_result_call_ids", []))
        rows.append(
            {
                "contract": contract,
                "message_kinds": safety.get("message_kinds", []),
                "fallback_reasons": safety.get("fallback_reasons", []),
                "fallback_buckets": safety.get("fallback_buckets", []),
                "required_native_fields": safety.get("required_native_fields", []),
                "handoff_targets": safety.get("handoff_targets", []),
                "tool_call_ids": safety.get("tool_call_ids", []),
                "tool_result_call_ids": safety.get("tool_result_call_ids", []),
                "safe_to_mutate": safety.get("safe_to_mutate"),
                "native_preservation_required": safety.get(
                    "native_preservation_required"
                ),
                "tool_result_lineage_complete": safety.get(
                    "tool_result_lineage_complete"
                ),
            }
        )
    return {
        "event_count": len(rows),
        "contracts": sorted(contracts),
        "message_kinds": sorted(message_kinds),
        "fallback_reasons": sorted(fallback_reasons),
        "fallback_buckets": sorted(fallback_buckets),
        "required_native_fields": sorted(required_native_fields),
        "handoff_targets": sorted(handoff_targets),
        "tool_call_ids": sorted(tool_call_ids),
        "tool_result_call_ids": sorted(tool_result_call_ids),
        "native_preservation_required_count": native_preservation_required_count,
        "safe_to_mutate_false_count": safe_to_mutate_false_count,
        "tool_result_lineage_complete_count": lineage_complete_count,
        "rows": rows,
    }


def summarize_typed_rewrite_candidates(events: list[dict[str, Any]]) -> dict[str, Any]:
    contracts: set[str] = set()
    message_kinds: set[str] = set()
    targets: set[str] = set()
    sources: set[str] = set()
    tool_call_ids: set[str] = set()
    tool_call_names: set[str] = set()
    tool_result_call_ids: set[str] = set()
    tool_result_names: set[str] = set()
    passing_checks: set[str] = set()
    failing_checks: set[str] = set()
    candidate_safe_count = 0
    mutation_applied_count = 0
    native_tokens = 0
    candidate_tokens = 0
    token_delta = 0
    rows: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        candidate = payload.get("typed_rewrite_candidate", {})
        if not isinstance(candidate, dict) or not candidate:
            continue
        contract = str(candidate.get("contract", ""))
        if contract:
            contracts.add(contract)
        message_kind = str(candidate.get("message_kind", ""))
        if message_kind:
            message_kinds.add(message_kind)
        target = str(candidate.get("target", ""))
        source = str(candidate.get("source", ""))
        if target:
            targets.add(target)
        if source:
            sources.add(source)
        _extend_set(tool_call_ids, candidate.get("tool_call_ids", []))
        _extend_set(tool_call_names, candidate.get("tool_call_names", []))
        _extend_set(tool_result_call_ids, candidate.get("tool_result_call_ids", []))
        _extend_set(tool_result_names, candidate.get("tool_result_names", []))
        if candidate.get("candidate_safe"):
            candidate_safe_count += 1
        if candidate.get("mutation_applied"):
            mutation_applied_count += 1
        native = int(candidate.get("native_input_tokens", 0) or 0)
        rewritten = int(candidate.get("candidate_input_tokens", 0) or 0)
        delta = int(candidate.get("token_delta_native_minus_candidate", 0) or 0)
        native_tokens += native
        candidate_tokens += rewritten
        token_delta += delta
        semantic_checks = candidate.get("semantic_checks", {})
        if isinstance(semantic_checks, dict):
            for key, value in semantic_checks.items():
                if value:
                    passing_checks.add(str(key))
                else:
                    failing_checks.add(str(key))
        rows.append(
            {
                "contract": contract,
                "message_kind": message_kind,
                "source": source,
                "target": target,
                "native_id": candidate.get("native_id", ""),
                "metadata_keys": candidate.get("metadata_keys", []),
                "context_count": candidate.get("context_count", 0),
                "tool_call_ids": candidate.get("tool_call_ids", []),
                "tool_call_names": candidate.get("tool_call_names", []),
                "tool_result_call_ids": candidate.get("tool_result_call_ids", []),
                "tool_result_names": candidate.get("tool_result_names", []),
                "tool_result_error_flags": candidate.get("tool_result_error_flags", []),
                "candidate_safe": bool(candidate.get("candidate_safe")),
                "mutation_applied": bool(candidate.get("mutation_applied")),
                "mutation_gate": candidate.get("mutation_gate", ""),
                "native_input_tokens": native,
                "candidate_input_tokens": rewritten,
                "token_delta_native_minus_candidate": delta,
                "semantic_checks": semantic_checks,
            }
        )
    return {
        "event_count": len(rows),
        "contracts": sorted(contracts),
        "message_kinds": sorted(message_kinds),
        "sources": sorted(sources),
        "targets": sorted(targets),
        "tool_call_ids": sorted(tool_call_ids),
        "tool_call_names": sorted(tool_call_names),
        "tool_result_call_ids": sorted(tool_result_call_ids),
        "tool_result_names": sorted(tool_result_names),
        "candidate_safe_count": candidate_safe_count,
        "mutation_applied_count": mutation_applied_count,
        "native_input_tokens": native_tokens,
        "candidate_input_tokens": candidate_tokens,
        "token_delta_native_minus_candidate": token_delta,
        "passing_semantic_checks": sorted(passing_checks),
        "failing_semantic_checks": sorted(failing_checks),
        "rows": rows,
    }


def _extend_set(target: set[str], values: Any) -> None:
    for value in values or []:
        text = str(value)
        if text:
            target.add(text)


def _first_seen(case: dict[str, Any]) -> dict[str, Any]:
    seen = case.get("seen_messages", []) if isinstance(case, dict) else []
    first = seen[0] if isinstance(seen, list) and seen else {}
    return first if isinstance(first, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Handoff / ToolCall Rewrite Guard Report",
        "",
        f"passed: `{str(report.get('passed')).lower()}`",
        "",
        "## Checks",
        "",
    ]
    checks = report.get("checks", {})
    if isinstance(checks, dict):
        for key, value in checks.items():
            lines.append(f"- `{key}`: `{str(value).lower()}`")
    lines.extend(["", "## Agent Input Real Rewrite", ""])
    rewrite = report.get("agent_input_real_rewrite", {})
    if isinstance(rewrite, dict):
        for key in (
            "event_count",
            "attempt_count",
            "applied_count",
            "fallback_count",
            "fallback_reasons",
            "fallback_buckets",
            "fallback_bucket_counts",
            "real_message_mutation_count",
            "native_input_tokens",
        ):
            lines.append(f"- `{key}`: `{rewrite.get(key)}`")
    lines.extend(["", "## Rewrite Safety", ""])
    safety = report.get("rewrite_safety", {})
    if isinstance(safety, dict):
        for key, value in safety.items():
            if key == "rows":
                continue
            lines.append(f"- `{key}`: `{value}`")
        rows = safety.get("rows", [])
        if isinstance(rows, list) and rows:
            lines.extend(["", "### Safety Rows", ""])
            for row in rows:
                lines.append(
                    "- "
                    f"kinds={row.get('message_kinds', [])}, "
                    f"fields={row.get('required_native_fields', [])}, "
                    f"safe_to_mutate={row.get('safe_to_mutate')}"
                )
    lines.extend(["", "## Typed Rewrite Candidate", ""])
    candidate = report.get("typed_rewrite_candidate", {})
    if isinstance(candidate, dict):
        for key, value in candidate.items():
            if key == "rows":
                continue
            lines.append(f"- `{key}`: `{value}`")
        rows = candidate.get("rows", [])
        if isinstance(rows, list) and rows:
            lines.extend(["", "### Candidate Rows", ""])
            for row in rows:
                lines.append(
                    "- "
                    f"{row.get('message_kind', '')} "
                    f"{row.get('source', '')}->{row.get('target', '')}: "
                    f"safe={row.get('candidate_safe')}, "
                    f"mutated={row.get('mutation_applied')}, "
                    f"native={row.get('native_input_tokens', 0)}, "
                    f"candidate={row.get('candidate_input_tokens', 0)}"
                )
    lines.extend(["", "## Cases", ""])
    for case_id in report.get("case_ids", []) or []:
        lines.append(f"- `{case_id}`")
    lines.extend(["", "## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    lines.append(f"- app_output_path: `{report.get('app_output_path', '')}`")
    lines.append(f"- trace_path: `{report.get('trace_path', '')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
