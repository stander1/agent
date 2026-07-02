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

EXPECTED_BUCKETS = {
    "input_contract_empty",
    "empty_payload",
    "cost_gate_failed",
    "unsupported_message_type",
}
EXPECTED_REASONS = {
    "empty_messages",
    "empty_text_payload",
    "token_not_reduced",
    "non_text_message_present",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a fallback matrix smoke for guarded AutoGen real-rewrite."
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
            PROJECT_ROOT / "runs" / f"v5.13h-autogen-rewrite-fallback-matrix-{stamp}"
        )
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_matrix_smoke(output_dir=output_dir, python=args.python)
    report_path = output_dir / "autogen_rewrite_fallback_matrix_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_rewrite_fallback_matrix_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_matrix_smoke(*, output_dir: Path, python: str) -> dict[str, Any]:
    app_output = output_dir / "autogen_rewrite_fallback_matrix_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            python,
            str(PROJECT_ROOT / "examples" / "autogen_rewrite_fallback_matrix_smoke.py"),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_REWRITE_FALLBACK_MATRIX_OUTPUT": str(app_output),
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
    cases = app_payload.get("cases", [])
    if not isinstance(cases, list):
        cases = []
    case_map = {str(case.get("case_id")): case for case in cases if isinstance(case, dict)}
    seen_short = _first_seen(case_map.get("short_text_cost_gate", {}))
    seen_handoff = _first_seen(case_map.get("unsupported_handoff_message", {}))
    checks = {
        "target_returncode_zero": result.returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "driver_phase_v5_13h": details.get("phase") == "v5.13h",
        "four_cases_recorded": len(cases) == 4,
        "four_fallback_events_recorded": int(rewrite_summary.get("event_count", 0) or 0)
        == 4,
        "no_rewrite_applied": int(rewrite_summary.get("applied_count", 0) or 0) == 0,
        "fallback_count_is_four": int(rewrite_summary.get("fallback_count", 0) or 0)
        == 4,
        "all_expected_reasons_recorded": EXPECTED_REASONS
        <= set(rewrite_summary.get("fallback_reasons", []) or []),
        "all_expected_buckets_recorded": EXPECTED_BUCKETS
        <= set(rewrite_summary.get("fallback_buckets", []) or []),
        "short_text_native_preserved": bool(seen_short.get("contains_short_marker"))
        and not bool(seen_short.get("contains_rewrite_marker")),
        "handoff_native_preserved": seen_handoff.get("type") == "HandoffMessage"
        and bool(seen_handoff.get("contains_fallback_marker"))
        and not bool(seen_handoff.get("contains_rewrite_marker")),
        "native_messages_not_mutated": int(
            rewrite_summary.get("real_message_mutation_count", 0) or 0
        )
        == 0,
    }
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "returncode": result.returncode,
        "checks": checks,
        "driver_phase": details.get("phase", ""),
        "driver_broadcast_mode": details.get("broadcast_mode", ""),
        "trace_path": str(trace_path) if trace_path else "",
        "app_output_path": str(app_output),
        "trace_event_counts": dict(sorted(event_counts.items())),
        "agent_input_real_rewrite": rewrite_summary,
        "case_ids": sorted(case_map),
        "app_payload": app_payload,
    }


def _first_seen(case: dict[str, Any]) -> dict[str, Any]:
    seen = case.get("seen_messages", []) if isinstance(case, dict) else []
    first = seen[0] if isinstance(seen, list) and seen else {}
    return first if isinstance(first, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Rewrite Fallback Matrix Report",
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
        ):
            lines.append(f"- `{key}`: `{rewrite.get(key)}`")
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
