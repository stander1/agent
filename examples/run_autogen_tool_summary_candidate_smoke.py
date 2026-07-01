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
EXPECTED_CALL_ID = "call_tool_summary_1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a native AutoGen ToolCallSummaryMessage typed rewrite candidate "
            "smoke. The message must stay native while AgentLite records a safe "
            "candidate with tool lineage preserved."
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
            PROJECT_ROOT / "runs" / f"v5.12x-autogen-tool-summary-candidate-{stamp}"
        )
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_candidate_smoke(output_dir=output_dir, python=args.python)
    report_path = output_dir / "autogen_tool_summary_candidate_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_tool_summary_candidate_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_candidate_smoke(*, output_dir: Path, python: str) -> dict[str, Any]:
    app_output = output_dir / "autogen_tool_summary_candidate_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            python,
            str(PROJECT_ROOT / "examples" / "autogen_tool_summary_candidate_smoke.py"),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_CANDIDATE_OUTPUT": str(app_output),
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
    seen = _first_seen(app_payload)
    candidate_row = _candidate_row(candidate_summary)
    passing_checks = set(candidate_summary.get("passing_semantic_checks", []) or [])
    checks = {
        "target_returncode_zero": result.returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "driver_phase_v5_12x": details.get("phase") == EXPECTED_PHASE,
        "one_rewrite_audit_event_recorded": int(
            rewrite_summary.get("event_count", 0) or 0
        )
        == 1,
        "candidate_recorded": int(candidate_summary.get("event_count", 0) or 0) == 1,
        "candidate_contract_recorded": EXPECTED_CONTRACT
        in set(candidate_summary.get("contracts", []) or []),
        "candidate_safe_but_not_mutated": (
            int(candidate_summary.get("candidate_safe_count", 0) or 0) == 1
            and int(candidate_summary.get("mutation_applied_count", 0) or 0) == 0
            and int(rewrite_summary.get("applied_count", 0) or 0) == 0
            and int(rewrite_summary.get("fallback_count", 0) or 0) == 1
            and int(rewrite_summary.get("real_message_mutation_count", 0) or 0) == 0
        ),
        "native_tool_summary_preserved_for_agent": (
            seen.get("type") == "ToolCallSummaryMessage"
            and seen.get("id") == "tool_summary_candidate_1"
            and seen.get("source") == "tool_runner"
            and seen.get("metadata", {}).get("tool_route") == "candidate"
            and bool(seen.get("contains_native_tool_marker"))
            and not bool(seen.get("contains_tool_summary_candidate_marker"))
        ),
        "tool_lineage_preserved_for_agent": (
            EXPECTED_CALL_ID in set(seen.get("tool_call_ids", []) or [])
            and EXPECTED_CALL_ID in set(seen.get("tool_result_call_ids", []) or [])
        ),
        "tool_lineage_recorded_in_safety": (
            EXPECTED_CALL_ID in set(safety_summary.get("tool_call_ids", []) or [])
            and EXPECTED_CALL_ID
            in set(safety_summary.get("tool_result_call_ids", []) or [])
            and int(safety_summary.get("tool_result_lineage_complete_count", 0) or 0)
            == 1
        ),
        "tool_lineage_recorded_in_candidate": (
            EXPECTED_CALL_ID in set(candidate_summary.get("tool_call_ids", []) or [])
            and EXPECTED_CALL_ID
            in set(candidate_summary.get("tool_result_call_ids", []) or [])
            and candidate_row.get("tool_result_error_flags") == [
                f"{EXPECTED_CALL_ID}:False"
            ]
        ),
        "candidate_preserves_required_fields": {
            "message_type_preserved",
            "source_preserved",
            "id_preserved",
            "metadata_preserved",
            "tool_calls_preserved",
            "tool_results_preserved",
            "tool_call_ids_preserved",
            "tool_result_call_ids_preserved",
            "tool_result_lineage_complete",
            "content_replaced_only",
            "state_ref_available",
            "schema_valid",
            "prompt_view_available",
            "token_reduced",
        }
        <= passing_checks,
        "candidate_reduces_tokens": int(
            candidate_summary.get("token_delta_native_minus_candidate", 0) or 0
        )
        > 0,
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
        "app_payload": app_payload,
    }


def _first_seen(app_payload: dict[str, Any]) -> dict[str, Any]:
    case = app_payload.get("case", {}) if isinstance(app_payload, dict) else {}
    seen = case.get("seen_messages", []) if isinstance(case, dict) else []
    first = seen[0] if isinstance(seen, list) and seen else {}
    return first if isinstance(first, dict) else {}


def _candidate_row(summary: dict[str, Any]) -> dict[str, Any]:
    rows = summary.get("rows", []) if isinstance(summary, dict) else []
    first = rows[0] if isinstance(rows, list) and rows else {}
    return first if isinstance(first, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen ToolCallSummary Candidate Report",
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
            "passing_semantic_checks",
        ):
            lines.append(f"- `{key}`: `{candidate.get(key)}`")
    lines.extend(["", "## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    lines.append(f"- app_output_path: `{report.get('app_output_path', '')}`")
    lines.append(f"- trace_path: `{report.get('trace_path', '')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
