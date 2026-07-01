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

MODES = ("shadow-only", "dry-run-rewrite", "real-rewrite")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run an AutoGen EchoAgent smoke in all broadcast modes and prove "
            "which content the agent actually receives."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--mode",
        action="append",
        choices=MODES,
        help="Run only selected mode(s). Defaults to all three modes.",
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.12x-autogen-rewrite-echo-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    modes = tuple(args.mode or MODES)
    mode_reports = [
        run_mode(mode=mode, output_dir=output_dir, python=args.python)
        for mode in modes
    ]
    report = build_comparison_report(output_dir=output_dir, mode_reports=mode_reports)
    report_path = output_dir / "autogen_rewrite_echo_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_rewrite_echo_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_mode(*, mode: str, output_dir: Path, python: str) -> dict[str, Any]:
    mode_dir = output_dir / mode
    mode_dir.mkdir(parents=True, exist_ok=True)
    app_output = mode_dir / "autogen_rewrite_echo_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            python,
            str(PROJECT_ROOT / "examples" / "autogen_rewrite_echo_smoke.py"),
        ],
        cwd=PROJECT_ROOT,
        data_dir=mode_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": mode,
        "AGENTLITE_AUTOGEN_REWRITE_ECHO_OUTPUT": str(app_output),
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
    seen = app_payload.get("seen_messages", [])
    first_seen = seen[0] if isinstance(seen, list) and seen else {}
    if not isinstance(first_seen, dict):
        first_seen = {}
    return {
        "mode": mode,
        "returncode": result.returncode,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase": details.get("phase", ""),
        "driver_broadcast_mode": details.get("broadcast_mode", ""),
        "app_output_path": str(app_output),
        "trace_path": str(trace_path) if trace_path else "",
        "trace_event_counts": dict(sorted(event_counts.items())),
        "app_payload": app_payload,
        "seen_content_chars": int(first_seen.get("content_chars", 0) or 0),
        "seen_contains_rewrite_marker": bool(
            first_seen.get("contains_rewrite_marker")
        ),
        "seen_contains_native_marker": bool(first_seen.get("contains_native_marker")),
        "seen_native_marker_count": int(first_seen.get("native_marker_count", 0) or 0),
        "seen_contains_state_ref": bool(first_seen.get("contains_state_ref")),
        "seen_contains_prompt_view": bool(first_seen.get("contains_prompt_view")),
        "agent_input_real_rewrite": rewrite_summary,
    }


def build_comparison_report(
    *,
    output_dir: Path,
    mode_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    by_mode = {str(report.get("mode")): report for report in mode_reports}
    real = by_mode.get("real-rewrite", {})
    dry = by_mode.get("dry-run-rewrite", {})
    shadow = by_mode.get("shadow-only", {})
    checks = {
        "all_targets_returncode_zero": all(
            int(report.get("returncode", 1)) == 0 for report in mode_reports
        ),
        "all_bootstrap_ok": all(bool(report.get("bootstrap_ok")) for report in mode_reports),
        "all_driver_phase_v5_12x": all(
            report.get("driver_phase") == "v5.12x" for report in mode_reports
        ),
        "shadow_agent_sees_native_content": bool(
            shadow.get("seen_contains_native_marker")
        )
        and not bool(shadow.get("seen_contains_rewrite_marker")),
        "dry_run_agent_sees_native_content": bool(
            dry.get("seen_contains_native_marker")
        )
        and not bool(dry.get("seen_contains_rewrite_marker")),
        "real_rewrite_agent_sees_rewritten_content": bool(
            real.get("seen_contains_rewrite_marker")
        ),
        "real_rewrite_reduces_native_marker_count": int(
            real.get("seen_native_marker_count", 999999) or 0
        )
        < int(shadow.get("seen_native_marker_count", 0) or 0),
        "real_rewrite_reduces_seen_chars": int(real.get("seen_content_chars", 0) or 0)
        < int(shadow.get("seen_content_chars", 0) or 0),
        "real_rewrite_contains_state_ref": bool(real.get("seen_contains_state_ref")),
        "real_rewrite_contains_prompt_view": bool(
            real.get("seen_contains_prompt_view")
        ),
        "real_rewrite_event_applied": int(
            real.get("agent_input_real_rewrite", {}).get("applied_count", 0) or 0
        )
        > 0,
        "real_rewrite_reduces_tokens": int(
            real.get("agent_input_real_rewrite", {}).get(
                "token_delta_native_minus_rewrite",
                0,
            )
            or 0
        )
        > 0,
    }
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "checks": checks,
        "mode_reports": mode_reports,
        "comparison": {
            "shadow_seen_chars": shadow.get("seen_content_chars", 0),
            "dry_run_seen_chars": dry.get("seen_content_chars", 0),
            "real_rewrite_seen_chars": real.get("seen_content_chars", 0),
            "shadow_native_marker_count": shadow.get("seen_native_marker_count", 0),
            "dry_run_native_marker_count": dry.get("seen_native_marker_count", 0),
            "real_rewrite_native_marker_count": real.get(
                "seen_native_marker_count",
                0,
            ),
            "real_rewrite_native_tokens": real.get("agent_input_real_rewrite", {}).get(
                "native_input_tokens",
                0,
            ),
            "real_rewrite_rewritten_tokens": real.get(
                "agent_input_real_rewrite",
                {},
            ).get("rewritten_input_tokens", 0),
            "real_rewrite_token_delta": real.get(
                "agent_input_real_rewrite",
                {},
            ).get("token_delta_native_minus_rewrite", 0),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Rewrite Echo Report",
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
        rewrite = mode_report.get("agent_input_real_rewrite", {})
        lines.extend(
            [
                f"### {mode_report.get('mode', '')}",
                "",
                f"- returncode: `{mode_report.get('returncode')}`",
                f"- driver_phase: `{mode_report.get('driver_phase')}`",
                f"- seen_content_chars: `{mode_report.get('seen_content_chars')}`",
                f"- seen_contains_rewrite_marker: `{mode_report.get('seen_contains_rewrite_marker')}`",
                f"- seen_contains_native_marker: `{mode_report.get('seen_contains_native_marker')}`",
                f"- seen_native_marker_count: `{mode_report.get('seen_native_marker_count')}`",
                f"- rewrite_applied_count: `{rewrite.get('applied_count', 0) if isinstance(rewrite, dict) else 0}`",
                f"- native_input_tokens: `{rewrite.get('native_input_tokens', 0) if isinstance(rewrite, dict) else 0}`",
                f"- rewritten_input_tokens: `{rewrite.get('rewritten_input_tokens', 0) if isinstance(rewrite, dict) else 0}`",
                "",
            ]
        )
    lines.extend(["## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
