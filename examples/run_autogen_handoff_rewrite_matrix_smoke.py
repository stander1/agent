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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run AutoGen HandoffMessage rewrite stress matrix. The matrix proves "
            "that AgentLite mutates only safe, token-reducing single Handoff "
            "content and preserves native messages in boundary cases."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.12x-autogen-handoff-matrix-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    scenario_reports = [
        run_scenario(
            output_dir=output_dir / "off",
            python=args.python,
            scenario="off",
            handoff_rewrite="off",
        ),
        run_scenario(
            output_dir=output_dir / "on",
            python=args.python,
            scenario="on",
            handoff_rewrite="on",
        ),
    ]
    report = build_matrix_report(output_dir=output_dir, scenario_reports=scenario_reports)
    report_path = output_dir / "autogen_handoff_rewrite_matrix_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_handoff_rewrite_matrix_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_scenario(
    *,
    output_dir: Path,
    python: str,
    scenario: str,
    handoff_rewrite: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app_output = output_dir / "autogen_handoff_rewrite_matrix_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            python,
            str(PROJECT_ROOT / "examples" / "autogen_handoff_rewrite_matrix_smoke.py"),
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
        "AGENTLITE_AUTOGEN_HANDOFF_MATRIX_SCENARIO": scenario,
        "AGENTLITE_AUTOGEN_HANDOFF_MATRIX_OUTPUT": str(app_output),
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
    return {
        "scenario": scenario,
        "handoff_rewrite_requested": handoff_rewrite,
        "handoff_rewrite_enabled": bool(details.get("handoff_rewrite_enabled")),
        "returncode": result.returncode,
        "bootstrap_ok": bool(status.get("ok")),
        "driver_phase": details.get("phase", ""),
        "trace_path": str(trace_path) if trace_path else "",
        "app_output_path": str(app_output),
        "trace_event_counts": dict(sorted(event_counts.items())),
        "agent_input_real_rewrite": rewrite_summary,
        "rewrite_safety": safety_summary,
        "typed_rewrite_candidate": candidate_summary,
        "cases": cases,
        "case_map": {
            str(case.get("case_id")): case for case in cases if isinstance(case, dict)
        },
    }


def build_matrix_report(
    *,
    output_dir: Path,
    scenario_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    by_scenario = {
        str(report.get("scenario")): report
        for report in scenario_reports
        if isinstance(report, dict)
    }
    off = by_scenario.get("off", {})
    on = by_scenario.get("on", {})
    off_rewrite = _summary(off, "agent_input_real_rewrite")
    on_rewrite = _summary(on, "agent_input_real_rewrite")
    off_candidate = _summary(off, "typed_rewrite_candidate")
    on_candidate = _summary(on, "typed_rewrite_candidate")
    off_cases = _case_map(off)
    on_cases = _case_map(on)
    off_seen = _first_seen(off_cases.get("switch_off_long", {}))
    single_seen = _first_seen(on_cases.get("single_long_enabled", {}))
    short_seen = _first_seen(on_cases.get("short_text_cost_gate", {}))
    multi_seen = _seen_messages(on_cases.get("multi_handoff_enabled", {}))
    context_seen = _first_seen(on_cases.get("context_handoff_enabled", {}))
    short_candidate = _candidate_row(on_candidate, "handoff_short_gate_1")
    context_candidate = _candidate_row(on_candidate, "handoff_context_enabled_1")
    checks = {
        "all_targets_returncode_zero": all(
            int(report.get("returncode", 1)) == 0 for report in scenario_reports
        ),
        "all_bootstrap_ok": all(
            bool(report.get("bootstrap_ok")) for report in scenario_reports
        ),
        "all_driver_phase_v5_12x": all(
            report.get("driver_phase") == EXPECTED_PHASE for report in scenario_reports
        ),
        "off_switch_keeps_native_handoff": (
            not bool(off.get("handoff_rewrite_enabled"))
            and int(off_rewrite.get("applied_count", 0) or 0) == 0
            and int(off_rewrite.get("fallback_count", 0) or 0) == 1
            and bool(off_seen.get("contains_any_native_marker"))
            and not bool(off_seen.get("contains_handoff_candidate_marker"))
        ),
        "off_candidate_recorded_but_not_mutated": (
            int(off_candidate.get("event_count", 0) or 0) == 1
            and int(off_candidate.get("mutation_applied_count", 0) or 0) == 0
        ),
        "on_applies_only_two_safe_handoffs": (
            bool(on.get("handoff_rewrite_enabled"))
            and int(on_rewrite.get("event_count", 0) or 0) == 4
            and int(on_rewrite.get("applied_count", 0) or 0) == 2
            and int(on_rewrite.get("fallback_count", 0) or 0) == 2
            and int(on_rewrite.get("real_message_mutation_count", 0) or 0) == 2
        ),
        "single_long_rewritten_content_preserves_control": (
            single_seen.get("type") == "HandoffMessage"
            and single_seen.get("id") == "handoff_long_enabled_1"
            and single_seen.get("source") == "router"
            and single_seen.get("target") == "matrix"
            and single_seen.get("metadata", {}).get("route") == "single_long"
            and bool(single_seen.get("contains_handoff_candidate_marker"))
            and int(single_seen.get("native_marker_count", 999999) or 0) < 14
        ),
        "short_text_fails_cost_gate_and_stays_native": (
            short_seen.get("id") == "handoff_short_gate_1"
            and bool(short_seen.get("contains_any_native_marker"))
            and not bool(short_seen.get("contains_handoff_candidate_marker"))
            and isinstance(short_candidate, dict)
            and short_candidate.get("semantic_checks", {}).get("token_reduced") is False
        ),
        "multi_handoff_stays_native": (
            len(multi_seen) == 2
            and all(
                bool(item.get("contains_any_native_marker"))
                and not bool(item.get("contains_handoff_candidate_marker"))
                for item in multi_seen
            )
        ),
        "context_handoff_rewritten_and_context_preserved": (
            context_seen.get("id") == "handoff_context_enabled_1"
            and int(context_seen.get("context_count", 0) or 0) == 2
            and bool(context_seen.get("contains_handoff_candidate_marker"))
            and isinstance(context_candidate, dict)
            and int(context_candidate.get("context_count", 0) or 0) == 2
            and context_candidate.get("semantic_checks", {}).get("context_preserved")
            is True
            and context_candidate.get("mutation_applied") is True
        ),
        "candidate_matrix_records_token_gate_failure": "token_reduced"
        in set(on_candidate.get("failing_semantic_checks", []) or []),
        "candidate_matrix_records_semantic_contract": (
            "autogen_handoff_typed_rewrite_candidate.v1"
            in set(on_candidate.get("contracts", []) or [])
        ),
    }
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "checks": checks,
        "scenario_reports": scenario_reports,
        "comparison": {
            "off_applied_count": off_rewrite.get("applied_count", 0),
            "on_applied_count": on_rewrite.get("applied_count", 0),
            "off_fallback_count": off_rewrite.get("fallback_count", 0),
            "on_fallback_count": on_rewrite.get("fallback_count", 0),
            "on_candidate_event_count": on_candidate.get("event_count", 0),
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


def _case_map(report: dict[str, Any]) -> dict[str, Any]:
    value = report.get("case_map", {}) if isinstance(report, dict) else {}
    return value if isinstance(value, dict) else {}


def _seen_messages(case: dict[str, Any]) -> list[dict[str, Any]]:
    seen = case.get("seen_messages", []) if isinstance(case, dict) else []
    return [item for item in seen if isinstance(item, dict)] if isinstance(seen, list) else []


def _first_seen(case: dict[str, Any]) -> dict[str, Any]:
    seen = _seen_messages(case)
    return seen[0] if seen else {}


def _candidate_row(summary: dict[str, Any], native_id: str) -> dict[str, Any]:
    rows = summary.get("rows", [])
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and row.get("native_id") == native_id:
            return row
    return {}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Handoff Rewrite Matrix Report",
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
    lines.extend(["", "## Scenarios", ""])
    for scenario in report.get("scenario_reports", []) or []:
        if not isinstance(scenario, dict):
            continue
        rewrite = scenario.get("agent_input_real_rewrite", {})
        candidate = scenario.get("typed_rewrite_candidate", {})
        lines.extend(
            [
                f"### {scenario.get('scenario', '')}",
                "",
                f"- handoff_rewrite_enabled: `{scenario.get('handoff_rewrite_enabled')}`",
                f"- driver_phase: `{scenario.get('driver_phase')}`",
                f"- rewrite_event_count: `{rewrite.get('event_count', 0) if isinstance(rewrite, dict) else 0}`",
                f"- rewrite_applied_count: `{rewrite.get('applied_count', 0) if isinstance(rewrite, dict) else 0}`",
                f"- rewrite_fallback_count: `{rewrite.get('fallback_count', 0) if isinstance(rewrite, dict) else 0}`",
                f"- candidate_event_count: `{candidate.get('event_count', 0) if isinstance(candidate, dict) else 0}`",
                f"- candidate_mutation_applied_count: `{candidate.get('mutation_applied_count', 0) if isinstance(candidate, dict) else 0}`",
                f"- trace_path: `{scenario.get('trace_path', '')}`",
                "",
            ]
        )
    lines.extend(["## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
