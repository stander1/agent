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
from run_autogen_handoff_tool_rewrite_guard_smoke import (  # noqa: E402
    summarize_rewrite_safety,
    summarize_typed_rewrite_candidates,
)
from run_autogen_native_smoke import _missing_modules, _read_trace_events  # noqa: E402

EXPECTED_PHASE = "v5.12x"
EXPECTED_CONTRACT = "autogen_tool_summary_typed_rewrite_candidate.v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run AutoGen ToolCallSummaryMessage typed rewrite matrix. The matrix "
            "keeps native AutoGen messages unchanged while validating safe and "
            "unsafe typed candidates."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
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
            PROJECT_ROOT / "runs" / f"v5.12x-autogen-tool-summary-matrix-{stamp}"
        )
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_matrix_smoke(output_dir=output_dir, python=args.python)
    report_path = output_dir / "autogen_tool_summary_matrix_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_tool_summary_matrix_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_matrix_smoke(*, output_dir: Path, python: str) -> dict[str, Any]:
    app_output = output_dir / "autogen_tool_summary_matrix_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            python,
            str(PROJECT_ROOT / "examples" / "autogen_tool_summary_matrix_smoke.py"),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_MATRIX_OUTPUT": str(app_output),
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
    case_map = _case_map(app_payload)
    candidate_by_id = {
        str(row.get("native_id", "")): row
        for row in candidate_summary.get("rows", []) or []
        if isinstance(row, dict)
    }
    aligned = candidate_by_id.get("tool_summary_matrix_aligned_1", {})
    mismatch = candidate_by_id.get("tool_summary_matrix_mismatch_1", {})
    multi = candidate_by_id.get("tool_summary_matrix_multi_1", {})
    short = candidate_by_id.get("tool_summary_matrix_short_1", {})
    error = candidate_by_id.get("tool_summary_matrix_error_1", {})
    checks = {
        "target_returncode_zero": result.returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "driver_phase_v5_12x": details.get("phase") == EXPECTED_PHASE,
        "five_cases_recorded": len(case_map) == 5,
        "five_rewrite_audit_events_recorded": int(
            rewrite_summary.get("event_count", 0) or 0
        )
        == 5,
        "five_candidates_recorded": int(candidate_summary.get("event_count", 0) or 0)
        == 5,
        "tool_summary_contract_recorded": candidate_summary.get("contracts") == [
            EXPECTED_CONTRACT
        ],
        "no_real_mutation_in_matrix": (
            int(rewrite_summary.get("applied_count", 0) or 0) == 0
            and int(rewrite_summary.get("fallback_count", 0) or 0) == 5
            and int(rewrite_summary.get("real_message_mutation_count", 0) or 0) == 0
            and int(candidate_summary.get("mutation_applied_count", 0) or 0) == 0
        ),
        "all_agents_receive_native_tool_summary": all(
            _seen_native_tool_summary(case) for case in case_map.values()
        ),
        "aligned_single_candidate_safe": (
            bool(aligned.get("candidate_safe"))
            and aligned.get("semantic_checks", {}).get("tool_result_lineage_complete")
            is True
            and aligned.get("semantic_checks", {}).get("token_reduced") is True
            and aligned.get("tool_call_ids") == ["call_matrix_aligned_1"]
            and aligned.get("tool_result_call_ids") == ["call_matrix_aligned_1"]
        ),
        "mismatched_call_id_candidate_unsafe": (
            mismatch.get("candidate_safe") is False
            and mismatch.get("semantic_checks", {}).get("tool_result_lineage_complete")
            is False
            and mismatch.get("tool_call_ids") == ["call_matrix_expected_1"]
            and mismatch.get("tool_result_call_ids") == ["call_matrix_actual_1"]
        ),
        "multi_tool_candidate_preserves_all_lineage": (
            bool(multi.get("candidate_safe"))
            and multi.get("tool_call_ids")
            == ["call_matrix_multi_1", "call_matrix_multi_2"]
            and multi.get("tool_result_call_ids")
            == ["call_matrix_multi_1", "call_matrix_multi_2"]
            and multi.get("semantic_checks", {}).get("tool_calls_preserved") is True
            and multi.get("semantic_checks", {}).get("tool_results_preserved") is True
        ),
        "short_cost_gate_fails_token_reduced": (
            bool(short.get("candidate_safe"))
            and short.get("semantic_checks", {}).get("token_reduced") is False
            and int(short.get("token_delta_native_minus_candidate", 0) or 0) < 0
        ),
        "error_result_preserves_error_flag": (
            bool(error.get("candidate_safe"))
            and error.get("tool_result_error_flags") == ["call_matrix_error_1:True"]
            and _first_seen(case_map.get("error_result_preserved", {})).get(
                "tool_result_error_flags"
            )
            == ["call_matrix_error_1:True"]
        ),
        "candidate_safe_count_expected": int(
            candidate_summary.get("candidate_safe_count", 0) or 0
        )
        == 4,
        "matrix_records_lineage_and_cost_failures": {
            "tool_result_lineage_complete",
            "token_reduced",
        }
        <= set(candidate_summary.get("failing_semantic_checks", []) or []),
        "safety_records_all_tool_ids": {
            "call_matrix_aligned_1",
            "call_matrix_expected_1",
            "call_matrix_multi_1",
            "call_matrix_multi_2",
            "call_matrix_short_1",
            "call_matrix_error_1",
        }
        <= set(safety_summary.get("tool_call_ids", []) or []),
    }
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "returncode": result.returncode,
        "checks": checks,
        "driver_phase": details.get("phase", ""),
        "trace_path": str(trace_path) if trace_path else "",
        "app_output_path": str(app_output),
        "trace_event_counts": dict(sorted(event_counts.items())),
        "agent_input_real_rewrite": rewrite_summary,
        "rewrite_safety": safety_summary,
        "typed_rewrite_candidate": candidate_summary,
        "case_ids": sorted(case_map),
        "app_payload": app_payload,
    }


def _case_map(app_payload: dict[str, Any]) -> dict[str, Any]:
    cases = app_payload.get("cases", []) if isinstance(app_payload, dict) else []
    if not isinstance(cases, list):
        return {}
    return {
        str(case.get("case_id")): case
        for case in cases
        if isinstance(case, dict) and case.get("case_id")
    }


def _first_seen(case: dict[str, Any]) -> dict[str, Any]:
    seen = case.get("seen_messages", []) if isinstance(case, dict) else []
    first = seen[0] if isinstance(seen, list) and seen else {}
    return first if isinstance(first, dict) else {}


def _seen_native_tool_summary(case: dict[str, Any]) -> bool:
    seen = _first_seen(case)
    return (
        seen.get("type") == "ToolCallSummaryMessage"
        and bool(seen.get("contains_native_tool_marker"))
        and not bool(seen.get("contains_tool_summary_candidate_marker"))
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen ToolCallSummary Matrix Report",
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
            "applied_count",
            "fallback_count",
            "real_message_mutation_count",
            "native_input_tokens",
            "rewritten_input_tokens",
            "token_delta_native_minus_rewrite",
            "fallback_reasons",
        ):
            lines.append(f"- `{key}`: `{rewrite.get(key)}`")
    lines.extend(["", "## Typed Rewrite Candidate", ""])
    candidate = report.get("typed_rewrite_candidate", {})
    if isinstance(candidate, dict):
        for key in (
            "contracts",
            "message_kinds",
            "candidate_safe_count",
            "mutation_applied_count",
            "tool_call_ids",
            "tool_result_call_ids",
            "native_input_tokens",
            "candidate_input_tokens",
            "token_delta_native_minus_candidate",
            "failing_semantic_checks",
        ):
            lines.append(f"- `{key}`: `{candidate.get(key)}`")
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
