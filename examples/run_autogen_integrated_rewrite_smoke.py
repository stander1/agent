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
EXPECTED_TOOL_CALL_ID = "call_integrated_tool_1"
USER_SCRIPT = PROJECT_ROOT / "examples" / "autogen_integrated_rewrite_smoke.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one AutoGen-only user script through AgentLite and verify "
            "TextMessage, HandoffMessage, and ToolCallSummaryMessage replacement."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.12x-autogen-integrated-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_integrated(output_dir=output_dir, python=args.python)
    report_path = output_dir / "autogen_integrated_rewrite_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_integrated_rewrite_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_integrated(*, output_dir: Path, python: str) -> dict[str, Any]:
    app_output = output_dir / "autogen_integrated_rewrite_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[python, str(USER_SCRIPT)],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_HANDOFF_REWRITE": "1",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE": "1",
        "AGENTLITE_AUTOGEN_INTEGRATED_REWRITE_OUTPUT": str(app_output),
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
    rewrite_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_agent_input_real_rewrite"
        and isinstance(event.get("payload"), dict)
    ]
    rewrite_summary = summarize_agent_real_rewrite_events(rewrite_events)
    safety_summary = summarize_rewrite_safety(rewrite_events)
    candidate_summary = summarize_typed_rewrite_candidates(rewrite_events)
    source_text = USER_SCRIPT.read_text(encoding="utf-8")
    cases = _cases_by_id(app_payload)
    checks = build_checks(
        returncode=result.returncode,
        status=status,
        details=details,
        app_payload=app_payload,
        cases=cases,
        rewrite_summary=rewrite_summary,
        candidate_summary=candidate_summary,
        source_text=source_text,
    )
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "returncode": result.returncode,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase": details.get("phase", ""),
        "broadcast_mode": details.get("broadcast_mode", ""),
        "handoff_rewrite_enabled": bool(details.get("handoff_rewrite_enabled")),
        "tool_summary_rewrite_enabled": bool(
            details.get("tool_summary_rewrite_enabled")
        ),
        "trace_path": str(trace_path) if trace_path else "",
        "app_output_path": str(app_output),
        "trace_event_counts": dict(sorted(event_counts.items())),
        "checks": checks,
        "app_payload": app_payload,
        "agent_input_real_rewrite": rewrite_summary,
        "rewrite_safety": safety_summary,
        "typed_rewrite_candidate": candidate_summary,
        "case_summaries": {
            case_id: _first_seen(case) for case_id, case in cases.items()
        },
    }


def build_checks(
    *,
    returncode: int,
    status: dict[str, Any],
    details: dict[str, Any],
    app_payload: dict[str, Any],
    cases: dict[str, dict[str, Any]],
    rewrite_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
    source_text: str,
) -> dict[str, bool]:
    text_seen = _first_seen(cases.get("text_message_real_rewrite", {}))
    handoff_seen = _first_seen(cases.get("handoff_message_real_rewrite", {}))
    tool_seen = _first_seen(cases.get("tool_summary_message_real_rewrite", {}))
    contracts = set(candidate_summary.get("contracts", []) or [])
    return {
        "target_returncode_zero": returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase_v5_12x": details.get("phase") == EXPECTED_PHASE,
        "real_rewrite_mode_enabled": details.get("broadcast_mode") == "real-rewrite",
        "handoff_rewrite_enabled": bool(details.get("handoff_rewrite_enabled")),
        "tool_summary_rewrite_enabled": bool(
            details.get("tool_summary_rewrite_enabled")
        ),
        "user_script_does_not_import_agentlite": (
            "from agent_runtime" not in source_text
            and "import agent_runtime" not in source_text
            and "agent_runtime." not in source_text
        ),
        "app_output_written": bool(app_payload),
        "agentlite_active_in_user_process": bool(app_payload.get("agentlite_active")),
        "three_cases_recorded": len(cases) == 3,
        "text_message_rewritten": (
            text_seen.get("type") == "TextMessage"
            and bool(text_seen.get("contains_text_rewrite_marker"))
            and bool(text_seen.get("contains_state_pool_marker"))
            and bool(text_seen.get("contains_prompt_view"))
            and not bool(text_seen.get("contains_any_native_marker"))
        ),
        "handoff_message_rewritten": (
            handoff_seen.get("type") == "HandoffMessage"
            and handoff_seen.get("id") == "handoff_integrated_1"
            and handoff_seen.get("source") == "router"
            and handoff_seen.get("target") == "integrated"
            and int(handoff_seen.get("context_count", 0) or 0) == 2
            and bool(handoff_seen.get("contains_handoff_rewrite_marker"))
            and bool(handoff_seen.get("contains_state_pool_marker"))
            and bool(handoff_seen.get("contains_prompt_view"))
            and not bool(handoff_seen.get("contains_any_native_marker"))
        ),
        "tool_summary_message_rewritten": (
            tool_seen.get("type") == "ToolCallSummaryMessage"
            and tool_seen.get("id") == "tool_summary_integrated_1"
            and bool(tool_seen.get("contains_tool_summary_rewrite_marker"))
            and bool(tool_seen.get("contains_state_pool_marker"))
            and bool(tool_seen.get("contains_prompt_view"))
            and not bool(tool_seen.get("contains_any_native_marker"))
        ),
        "tool_summary_lineage_preserved": (
            EXPECTED_TOOL_CALL_ID in set(tool_seen.get("tool_call_ids", []) or [])
            and EXPECTED_TOOL_CALL_ID
            in set(tool_seen.get("tool_result_call_ids", []) or [])
            and tool_seen.get("tool_result_error_flags")
            == [f"{EXPECTED_TOOL_CALL_ID}:False"]
        ),
        "three_rewrite_events_recorded": int(
            rewrite_summary.get("event_count", 0) or 0
        )
        == 3,
        "all_three_mutations_applied": int(
            rewrite_summary.get("applied_count", 0) or 0
        )
        == 3
        and int(rewrite_summary.get("real_message_mutation_count", 0) or 0) == 3,
        "no_fallback_in_integrated_happy_path": int(
            rewrite_summary.get("fallback_count", 0) or 0
        )
        == 0,
        "integrated_reduces_tokens": int(
            rewrite_summary.get("token_delta_native_minus_rewrite", 0) or 0
        )
        > 0,
        "typed_candidates_for_non_text_recorded": (
            "autogen_handoff_typed_rewrite_candidate.v1" in contracts
            and "autogen_tool_summary_typed_rewrite_candidate.v1" in contracts
            and int(candidate_summary.get("mutation_applied_count", 0) or 0) == 2
        ),
    }


def _cases_by_id(app_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = app_payload.get("cases", []) if isinstance(app_payload, dict) else []
    output: dict[str, dict[str, Any]] = {}
    if isinstance(cases, list):
        for case in cases:
            if isinstance(case, dict):
                output[str(case.get("case_id", ""))] = case
    return output


def _first_seen(case: dict[str, Any]) -> dict[str, Any]:
    seen = case.get("seen_messages", []) if isinstance(case, dict) else []
    first = seen[0] if isinstance(seen, list) and seen else {}
    return first if isinstance(first, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Integrated Rewrite Report",
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
    rewrite = report.get("agent_input_real_rewrite", {})
    lines.extend(["", "## Rewrite Summary", ""])
    if isinstance(rewrite, dict):
        for key in (
            "event_count",
            "applied_count",
            "fallback_count",
            "real_message_mutation_count",
            "native_input_tokens",
            "rewritten_input_tokens",
            "token_delta_native_minus_rewrite",
        ):
            lines.append(f"- `{key}`: `{rewrite.get(key, 0)}`")
    candidate = report.get("typed_rewrite_candidate", {})
    lines.extend(["", "## Typed Candidates", ""])
    if isinstance(candidate, dict):
        for key in (
            "event_count",
            "contracts",
            "candidate_safe_count",
            "mutation_applied_count",
            "token_delta_native_minus_candidate",
        ):
            lines.append(f"- `{key}`: `{candidate.get(key, 0)}`")
    lines.extend(["", "## Case Summaries", ""])
    cases = report.get("case_summaries", {})
    if isinstance(cases, dict):
        for case_id, seen in cases.items():
            if not isinstance(seen, dict):
                continue
            lines.extend(
                [
                    f"### {case_id}",
                    "",
                    f"- type: `{seen.get('type', '')}`",
                    f"- rewrite_marker_kind: `{seen.get('rewrite_marker_kind', '')}`",
                    f"- content_chars: `{seen.get('content_chars', 0)}`",
                    f"- native_marker_count: `{seen.get('native_marker_count', 0)}`",
                    f"- tool_call_ids: `{seen.get('tool_call_ids', [])}`",
                    f"- tool_result_call_ids: `{seen.get('tool_result_call_ids', [])}`",
                    "",
                ]
            )
    lines.extend(["## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    lines.append(f"- trace_path: `{report.get('trace_path', '')}`")
    lines.append(f"- app_output_path: `{report.get('app_output_path', '')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
