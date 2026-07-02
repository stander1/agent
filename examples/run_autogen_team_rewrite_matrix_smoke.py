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
    summarize_broadcast_shadow_events,
)
from run_autogen_native_smoke import _missing_modules, _read_trace_events  # noqa: E402

EXPECTED_PHASE = "v5.13h"
EXPECTED_RECEIVERS = {"planner", "writer", "reviewer"}
USER_SCRIPT = PROJECT_ROOT / "examples" / "autogen_team_rewrite_matrix_smoke.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Team rewrite boundary matrix under AgentLite-managed AutoGen."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-team-matrix-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    modes = tuple(args.mode or ("off", "on"))
    mode_reports = [
        run_mode(mode=mode, output_dir=output_dir / mode, python=args.python)
        for mode in modes
    ]
    report = build_matrix_report(output_dir=output_dir, mode_reports=mode_reports)
    report_path = output_dir / "autogen_team_rewrite_matrix_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_team_rewrite_matrix_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_mode(*, mode: str, output_dir: Path, python: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app_output = output_dir / "autogen_team_rewrite_matrix_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[python, str(USER_SCRIPT)],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1" if mode == "on" else "0",
        "AGENTLITE_AUTOGEN_TEAM_MATRIX_MODE": mode,
        "AGENTLITE_AUTOGEN_TEAM_REWRITE_MATRIX_OUTPUT": str(app_output),
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
    team_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_team_input_real_rewrite"
        and isinstance(event.get("payload"), dict)
    ]
    broadcast_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_broadcast_replacement_shadow"
        and isinstance(event.get("payload"), dict)
    ]
    cases = _cases_by_id(app_payload)
    return {
        "mode": mode,
        "returncode": result.returncode,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase": details.get("phase", ""),
        "broadcast_mode": details.get("broadcast_mode", ""),
        "team_rewrite_enabled": bool(details.get("team_rewrite_enabled")),
        "trace_path": str(trace_path) if trace_path else "",
        "app_output_path": str(app_output),
        "trace_event_counts": dict(sorted(event_counts.items())),
        "app_payload": app_payload,
        "case_first_messages": {
            case_id: _case_first_message(case) for case_id, case in cases.items()
        },
        "case_errors": {
            case_id: case.get("error", {})
            for case_id, case in cases.items()
            if isinstance(case.get("error", {}), dict) and case.get("error")
        },
        "team_input_real_rewrite": summarize_team_matrix_events(team_events),
        "broadcast_replacement_shadow": summarize_broadcast_shadow_events(
            broadcast_events
        ),
    }


def summarize_team_matrix_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    participants: set[str] = set()
    fallback_reasons: set[str] = set()
    fallback_buckets: set[str] = set()
    applied_count = 0
    fallback_count = 0
    mutation_count = 0
    native_task_tokens = 0
    rewritten_task_tokens = 0
    token_delta_task = 0
    token_delta_broadcast = 0
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        reasons = [str(item) for item in payload.get("fallback_reasons", []) or []]
        buckets = [str(item) for item in payload.get("fallback_buckets", []) or []]
        participants.update(str(item) for item in payload.get("team_participants", []))
        fallback_reasons.update(reasons)
        fallback_buckets.update(buckets)
        applied_count += int(payload.get("rewrite_applied_count", 0) or 0)
        fallback_count += int(payload.get("rewrite_fallback_count", 0) or 0)
        if payload.get("real_message_mutation"):
            mutation_count += 1
        native_task_tokens += int(payload.get("native_task_tokens", 0) or 0)
        rewritten_task_tokens += int(payload.get("rewritten_task_tokens", 0) or 0)
        token_delta_task += int(
            payload.get("token_delta_native_task_minus_rewrite", 0) or 0
        )
        token_delta_broadcast += int(
            payload.get("token_delta_native_broadcast_minus_rewrite", 0) or 0
        )
        rows.append(
            {
                "call_id": payload.get("call_id", ""),
                "method": payload.get("method", ""),
                "team_rewrite_enabled": bool(payload.get("team_rewrite_enabled")),
                "rewrite_applied": bool(payload.get("rewrite_applied")),
                "real_message_mutation": bool(payload.get("real_message_mutation")),
                "fallback_required": bool(payload.get("fallback_required")),
                "fallback_reasons": reasons,
                "fallback_buckets": buckets,
                "native_task_tokens": int(payload.get("native_task_tokens", 0) or 0),
                "rewritten_task_tokens": int(
                    payload.get("rewritten_task_tokens", 0) or 0
                ),
                "token_delta_native_task_minus_rewrite": int(
                    payload.get("token_delta_native_task_minus_rewrite", 0) or 0
                ),
            }
        )
    return {
        "event_count": len(rows),
        "participants": sorted(participants),
        "applied_count": applied_count,
        "fallback_count": fallback_count,
        "real_message_mutation_count": mutation_count,
        "fallback_reasons": sorted(fallback_reasons),
        "fallback_buckets": sorted(fallback_buckets),
        "native_task_tokens": native_task_tokens,
        "rewritten_task_tokens": rewritten_task_tokens,
        "token_delta_native_task_minus_rewrite": token_delta_task,
        "token_delta_native_broadcast_minus_rewrite": token_delta_broadcast,
        "rows": rows,
    }


def build_matrix_report(
    *,
    output_dir: Path,
    mode_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    by_mode = {str(report.get("mode")): report for report in mode_reports}
    off = by_mode.get("off", {})
    on = by_mode.get("on", {})
    off_team = _summary(off, "team_input_real_rewrite")
    on_team = _summary(on, "team_input_real_rewrite")
    on_first = on.get("case_first_messages", {}) if isinstance(on, dict) else {}
    off_first = off.get("case_first_messages", {}) if isinstance(off, dict) else {}
    checks = {
        "all_targets_returncode_zero": all(
            int(report.get("returncode", 1)) == 0 for report in mode_reports
        ),
        "all_bootstrap_ok": all(bool(report.get("bootstrap_ok")) for report in mode_reports),
        "all_driver_phase_v5_13h": all(
            report.get("driver_phase") == EXPECTED_PHASE for report in mode_reports
        ),
        "off_switch_keeps_native_task": (
            off.get("team_rewrite_enabled") is False
            and int(off_team.get("applied_count", 0) or 0) == 0
            and int(off_team.get("fallback_count", 0) or 0) >= 1
            and "team_level_real_rewrite_not_enabled_for_guarded_agent_input"
            in set(off_team.get("fallback_reasons", []) or [])
            and bool(
                _case_has_native_marker(off_first.get("switch_off_long", {}))
            )
            and not bool(
                _case_has_team_rewrite_marker(off_first.get("switch_off_long", {}))
            )
        ),
        "on_long_stream_rewritten": _case_rewritten(on_first.get("long_stream", {})),
        "on_short_stream_falls_back": _case_has_native_marker(
            on_first.get("short_stream", {})
        ),
        "on_message_task_falls_back": _case_has_native_marker(
            on_first.get("message_task", {})
        ),
        "on_list_task_falls_back": _case_has_native_marker(
            on_first.get("list_task", {})
        ),
        "on_none_task_safe_fallback": (
            "missing_team_task_argument"
            in set(on_team.get("fallback_reasons", []) or [])
        ),
        "on_run_indirect_rewritten": _case_rewritten(
            on_first.get("run_long_indirect", {})
        ),
        "on_applies_only_safe_long_tasks": int(
            on_team.get("applied_count", 0) or 0
        )
        >= 2,
        "on_records_expected_fallbacks": {
            "team_task_token_not_reduced",
            "unsupported_team_task_type",
            "missing_team_task_argument",
        }
        <= set(on_team.get("fallback_reasons", []) or []),
        "on_team_receivers_preserved": set(on_team.get("participants", []))
        >= EXPECTED_RECEIVERS,
        "on_token_delta_positive": int(
            on_team.get("token_delta_native_task_minus_rewrite", 0) or 0
        )
        > 0,
    }
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "checks": checks,
        "mode_reports": mode_reports,
        "comparison": {
            "off_applied_count": off_team.get("applied_count", 0),
            "off_fallback_count": off_team.get("fallback_count", 0),
            "on_applied_count": on_team.get("applied_count", 0),
            "on_fallback_count": on_team.get("fallback_count", 0),
            "on_token_delta_task": on_team.get(
                "token_delta_native_task_minus_rewrite",
                0,
            ),
            "on_fallback_reasons": on_team.get("fallback_reasons", []),
        },
    }


def _cases_by_id(app_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cases = app_payload.get("cases", []) if isinstance(app_payload, dict) else []
    output: dict[str, dict[str, Any]] = {}
    if isinstance(cases, list):
        for case in cases:
            if isinstance(case, dict):
                output[str(case.get("case_id", ""))] = case
    return output


def _case_first_message(case: dict[str, Any]) -> dict[str, Any]:
    stream_items = case.get("stream_items", []) if isinstance(case, dict) else []
    if isinstance(stream_items, list) and stream_items:
        first = stream_items[0]
        return first if isinstance(first, dict) else {}
    task_result = case.get("task_result", {}) if isinstance(case, dict) else {}
    messages = task_result.get("messages", []) if isinstance(task_result, dict) else []
    if isinstance(messages, list) and messages:
        first = messages[0]
        return first if isinstance(first, dict) else {}
    return {}


def _summary(report: dict[str, Any], key: str) -> dict[str, Any]:
    value = report.get(key, {}) if isinstance(report, dict) else {}
    return value if isinstance(value, dict) else {}


def _case_has_native_marker(case: dict[str, Any]) -> bool:
    return bool(case.get("contains_any_native_marker")) and int(
        case.get("native_marker_count", 0) or 0
    ) > 0


def _case_has_team_rewrite_marker(case: dict[str, Any]) -> bool:
    return bool(case.get("contains_team_rewrite_marker"))


def _case_rewritten(case: dict[str, Any]) -> bool:
    return (
        bool(case.get("contains_team_rewrite_marker"))
        and bool(case.get("contains_state_pool_marker"))
        and bool(case.get("contains_broadcast_manifest"))
        and bool(case.get("contains_receiver_prompt_views"))
        and not _case_has_native_marker(case)
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Team Rewrite Matrix Report",
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
        team = mode_report.get("team_input_real_rewrite", {})
        lines.extend(
            [
                f"### {mode_report.get('mode', '')}",
                "",
                f"- team_rewrite_enabled: `{mode_report.get('team_rewrite_enabled')}`",
                f"- driver_phase: `{mode_report.get('driver_phase')}`",
                f"- event_count: `{team.get('event_count', 0) if isinstance(team, dict) else 0}`",
                f"- applied_count: `{team.get('applied_count', 0) if isinstance(team, dict) else 0}`",
                f"- fallback_count: `{team.get('fallback_count', 0) if isinstance(team, dict) else 0}`",
                f"- fallback_reasons: `{team.get('fallback_reasons', []) if isinstance(team, dict) else []}`",
                "",
            ]
        )
        cases = mode_report.get("case_first_messages", {})
        if isinstance(cases, dict):
            for case_id, first in cases.items():
                if not isinstance(first, dict):
                    continue
                lines.append(
                    f"- `{case_id}`: rewrite=`{first.get('contains_team_rewrite_marker', False)}`, "
                    f"native_marker_count=`{first.get('native_marker_count', 0)}`"
                )
            lines.append("")
    lines.extend(["## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
