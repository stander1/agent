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

EXPECTED_PHASE = "v5.13h"
EXPECTED_CONTRACT = "autogen_tool_summary_typed_rewrite_candidate.v1"
EXPECTED_CALL_ID = "call_tool_switch_1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run ToolCallSummaryMessage rewrite switch in off/on modes. "
            "On mode should mutate only message content while preserving "
            "tool_calls/results."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--mode",
        action="append",
        choices=("off", "on"),
        help="Run only selected mode(s). Defaults to off and on.",
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
            / f"v5.13h-autogen-tool-summary-rewrite-switch-{stamp}"
        )
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    modes = tuple(args.mode or ("off", "on"))
    mode_reports = [
        run_mode(mode=mode, output_dir=output_dir / mode, python=args.python)
        for mode in modes
    ]
    report = build_switch_report(output_dir=output_dir, mode_reports=mode_reports)
    report_path = output_dir / "autogen_tool_summary_rewrite_switch_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_tool_summary_rewrite_switch_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_mode(*, mode: str, output_dir: Path, python: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app_output = output_dir / "autogen_tool_summary_rewrite_switch_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            python,
            str(
                PROJECT_ROOT
                / "examples"
                / "autogen_tool_summary_rewrite_switch_smoke.py"
            ),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE": "1" if mode == "on" else "0",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE_SWITCH_OUTPUT": str(app_output),
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
    return {
        "mode": mode,
        "returncode": result.returncode,
        "bootstrap_ok": bool(status.get("ok")),
        "driver_phase": details.get("phase", ""),
        "tool_summary_rewrite_enabled": bool(
            details.get("tool_summary_rewrite_enabled")
        ),
        "trace_path": str(trace_path) if trace_path else "",
        "app_output_path": str(app_output),
        "trace_event_counts": dict(sorted(event_counts.items())),
        "app_payload": app_payload,
        "seen": seen,
        "agent_input_real_rewrite": rewrite_summary,
        "rewrite_safety": safety_summary,
        "typed_rewrite_candidate": candidate_summary,
    }


def build_switch_report(
    *,
    output_dir: Path,
    mode_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    by_mode = {str(report.get("mode")): report for report in mode_reports}
    off = by_mode.get("off", {})
    on = by_mode.get("on", {})
    off_rewrite = _summary(off, "agent_input_real_rewrite")
    on_rewrite = _summary(on, "agent_input_real_rewrite")
    off_candidate = _summary(off, "typed_rewrite_candidate")
    on_candidate = _summary(on, "typed_rewrite_candidate")
    off_seen = off.get("seen", {}) if isinstance(off, dict) else {}
    on_seen = on.get("seen", {}) if isinstance(on, dict) else {}
    checks = {
        "all_targets_returncode_zero": all(
            int(report.get("returncode", 1)) == 0 for report in mode_reports
        ),
        "all_bootstrap_ok": all(bool(report.get("bootstrap_ok")) for report in mode_reports),
        "all_driver_phase_v5_13h": all(
            report.get("driver_phase") == EXPECTED_PHASE for report in mode_reports
        ),
        "off_mode_keeps_native_tool_summary": (
            off.get("tool_summary_rewrite_enabled") is False
            and int(off_rewrite.get("applied_count", 0) or 0) == 0
            and int(off_rewrite.get("fallback_count", 0) or 0) == 1
            and bool(off_seen.get("contains_native_tool_marker"))
            and not bool(off_seen.get("contains_tool_summary_candidate_marker"))
            and EXPECTED_CALL_ID in set(off_seen.get("tool_call_ids", []) or [])
            and EXPECTED_CALL_ID in set(off_seen.get("tool_result_call_ids", []) or [])
        ),
        "off_mode_candidate_safe_not_mutated": (
            int(off_candidate.get("candidate_safe_count", 0) or 0) == 1
            and int(off_candidate.get("mutation_applied_count", 0) or 0) == 0
        ),
        "on_mode_rewrites_tool_summary_content": (
            on.get("tool_summary_rewrite_enabled") is True
            and int(on_rewrite.get("applied_count", 0) or 0) == 1
            and int(on_rewrite.get("fallback_count", 0) or 0) == 0
            and int(on_rewrite.get("real_message_mutation_count", 0) or 0) == 1
            and bool(on_seen.get("contains_tool_summary_candidate_marker"))
            and bool(on_seen.get("contains_state_pool_marker"))
            and bool(on_seen.get("contains_prompt_view"))
            and not bool(on_seen.get("contains_native_tool_marker"))
        ),
        "on_mode_preserves_tool_lineage_for_agent": (
            EXPECTED_CALL_ID in set(on_seen.get("tool_call_ids", []) or [])
            and EXPECTED_CALL_ID in set(on_seen.get("tool_result_call_ids", []) or [])
            and on_seen.get("tool_result_error_flags") == [f"{EXPECTED_CALL_ID}:False"]
        ),
        "on_mode_candidate_mutated_and_safe": (
            int(on_candidate.get("candidate_safe_count", 0) or 0) == 1
            and int(on_candidate.get("mutation_applied_count", 0) or 0) == 1
            and EXPECTED_CONTRACT in set(on_candidate.get("contracts", []) or [])
        ),
        "on_mode_reduces_tokens": int(
            on_candidate.get("token_delta_native_minus_candidate", 0) or 0
        )
        > 0,
        "switch_preserves_candidate_contract_in_both_modes": (
            EXPECTED_CONTRACT in set(off_candidate.get("contracts", []) or [])
            and EXPECTED_CONTRACT in set(on_candidate.get("contracts", []) or [])
        ),
    }
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "checks": checks,
        "mode_reports": mode_reports,
        "comparison": {
            "off_applied_count": off_rewrite.get("applied_count", 0),
            "on_applied_count": on_rewrite.get("applied_count", 0),
            "off_fallback_count": off_rewrite.get("fallback_count", 0),
            "on_fallback_count": on_rewrite.get("fallback_count", 0),
            "off_candidate_mutation_applied_count": off_candidate.get(
                "mutation_applied_count",
                0,
            ),
            "on_candidate_mutation_applied_count": on_candidate.get(
                "mutation_applied_count",
                0,
            ),
            "on_candidate_token_delta": on_candidate.get(
                "token_delta_native_minus_candidate",
                0,
            ),
        },
    }


def _summary(report: dict[str, Any], key: str) -> dict[str, Any]:
    value = report.get(key, {}) if isinstance(report, dict) else {}
    return value if isinstance(value, dict) else {}


def _first_seen(app_payload: dict[str, Any]) -> dict[str, Any]:
    case = app_payload.get("case", {}) if isinstance(app_payload, dict) else {}
    seen = case.get("seen_messages", []) if isinstance(case, dict) else []
    first = seen[0] if isinstance(seen, list) and seen else {}
    return first if isinstance(first, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen ToolCallSummary Rewrite Switch Report",
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
    lines.extend(["", "## Comparison", ""])
    comparison = report.get("comparison", {})
    if isinstance(comparison, dict):
        for key, value in comparison.items():
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Modes", ""])
    for mode_report in report.get("mode_reports", []) or []:
        if not isinstance(mode_report, dict):
            continue
        rewrite = mode_report.get("agent_input_real_rewrite", {})
        candidate = mode_report.get("typed_rewrite_candidate", {})
        seen = mode_report.get("seen", {})
        lines.extend(
            [
                f"### {mode_report.get('mode', '')}",
                "",
                f"- tool_summary_rewrite_enabled: `{mode_report.get('tool_summary_rewrite_enabled')}`",
                f"- driver_phase: `{mode_report.get('driver_phase')}`",
                f"- rewrite_applied_count: `{rewrite.get('applied_count', 0) if isinstance(rewrite, dict) else 0}`",
                f"- rewrite_fallback_count: `{rewrite.get('fallback_count', 0) if isinstance(rewrite, dict) else 0}`",
                f"- candidate_mutation_applied_count: `{candidate.get('mutation_applied_count', 0) if isinstance(candidate, dict) else 0}`",
                f"- seen_contains_candidate_marker: `{seen.get('contains_tool_summary_candidate_marker') if isinstance(seen, dict) else False}`",
                f"- seen_tool_call_ids: `{seen.get('tool_call_ids', []) if isinstance(seen, dict) else []}`",
                "",
            ]
        )
    lines.extend(["## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
