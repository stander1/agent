from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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

from agent_runtime.launcher import read_bootstrap_status  # noqa: E402
from run_autogen_native_smoke import _missing_modules, _read_trace_events  # noqa: E402
from run_autogen_team_rewrite_smoke import (  # noqa: E402
    summarize_team_rewrite_events,
)


EXPECTED_PHASE = "v5.13h"
USER_SCRIPT = PROJECT_ROOT / "examples" / "autogen_web_backend_smoke.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a stdlib HTTP backend under agentlite autogen and verify that "
            "AutoGen Team work triggered after an HTTP request is still rewritten."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-web-backend-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_web_backend_smoke(output_dir=output_dir, python=args.python)
    report_path = output_dir / "autogen_web_backend_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_web_backend_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_web_backend_smoke(*, output_dir: Path, python: str) -> dict[str, Any]:
    app_output = output_dir / "autogen_web_backend_output.json"
    data_dir = output_dir / "agentlite"
    command = [
        python,
        "-m",
        "agent_runtime.cli",
        "autogen",
        "--cwd",
        str(PROJECT_ROOT),
        "--data-dir",
        str(data_dir),
        "--",
        python,
        str(USER_SCRIPT),
    ]
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "AGENTLITE_AUTOGEN_WEB_BACKEND_OUTPUT": str(app_output),
    }
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_path = output_dir / "agentlite_autogen_stdout.txt"
    stderr_path = output_dir / "agentlite_autogen_stderr.txt"
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")

    status_file = _extract_session_file(completed.stdout or "")
    status = read_bootstrap_status(status_file) if status_file else None
    status_payload = status or {}
    details = status_payload.get("driver_details", {})
    if not isinstance(details, dict):
        details = {}
    trace_path = Path(str(details.get("trace_path", "")))
    trace_events = _read_trace_events(trace_path)
    event_counts = Counter(str(event.get("event_type", "")) for event in trace_events)
    team_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_team_input_real_rewrite"
        and isinstance(event.get("payload"), dict)
    ]
    team_summary = summarize_team_rewrite_events(team_events)
    app_payload = (
        json.loads(app_output.read_text(encoding="utf-8"))
        if app_output.exists()
        else {}
    )
    first_stream_item = _first_stream_item(app_payload)
    final_message = _last_task_message(app_payload)
    checks = build_checks(
        returncode=completed.returncode,
        status=status_payload,
        details=details,
        app_payload=app_payload,
        first_stream_item=first_stream_item,
        final_message=final_message,
        team_summary=team_summary,
    )
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "returncode": completed.returncode,
        "command": command,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "app_output_path": str(app_output),
        "status_file": str(status_file) if status_file else "",
        "bootstrap_ok": bool(status_payload.get("ok")),
        "hooks_active": bool(status_payload.get("hooks_active")),
        "driver_phase": details.get("phase", ""),
        "trace_path": str(trace_path) if trace_path else "",
        "trace_event_counts": dict(sorted(event_counts.items())),
        "checks": checks,
        "first_stream_item": first_stream_item,
        "final_message": final_message,
        "team_input_real_rewrite": team_summary,
        "app_payload": app_payload,
    }


def build_checks(
    *,
    returncode: int,
    status: dict[str, Any],
    details: dict[str, Any],
    app_payload: dict[str, Any],
    first_stream_item: dict[str, Any],
    final_message: dict[str, Any],
    team_summary: dict[str, Any],
) -> dict[str, bool]:
    return {
        "agentlite_autogen_command_returncode_zero": returncode == 0,
        "bootstrap_status_present": bool(status),
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase_v5_13h": details.get("phase") == EXPECTED_PHASE,
        "app_output_written": bool(app_payload),
        "http_status_ok": int(app_payload.get("http_status", 0) or 0) == 200,
        "agentlite_active_in_web_backend": bool(app_payload.get("agentlite_active")),
        "web_request_path_recorded": app_payload.get("request_path") == "/run",
        "team_rewrite_env_enabled": app_payload.get("team_rewrite_env") == "1",
        "first_http_task_rewritten": (
            bool(first_stream_item.get("contains_team_rewrite_marker"))
            and bool(first_stream_item.get("contains_state_pool_marker"))
            and bool(first_stream_item.get("contains_broadcast_manifest"))
            and bool(first_stream_item.get("contains_receiver_prompt_views"))
        ),
        "native_http_task_removed": (
            not bool(first_stream_item.get("contains_native_marker"))
            and int(first_stream_item.get("native_marker_count", 0) or 0) == 0
        ),
        "team_rewrite_event_recorded": int(team_summary.get("event_count", 0) or 0)
        >= 1,
        "team_rewrite_applied": int(team_summary.get("applied_count", 0) or 0) >= 1,
        "team_rewrite_no_fallback": int(team_summary.get("fallback_count", 0) or 0)
        == 0,
        "final_output_contains_done": bool(
            final_message.get("content_preview", "").find("DONE_WEB_BACKEND") >= 0
        ),
        "final_output_not_agentlite_wire": not bool(
            final_message.get("contains_team_rewrite_marker")
        )
        and not bool(final_message.get("contains_state_pool_marker"))
        and not bool(final_message.get("contains_broadcast_manifest")),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Web Backend Smoke Report",
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
    lines.extend(["", "## Key Evidence", ""])
    lines.append(f"- command: `{' '.join(report.get('command', []))}`")
    lines.append(f"- status_file: `{report.get('status_file', '')}`")
    lines.append(f"- trace_path: `{report.get('trace_path', '')}`")
    team = report.get("team_input_real_rewrite", {})
    if isinstance(team, dict):
        lines.append(
            "- token_delta_native_broadcast_minus_rewrite: "
            f"`{team.get('token_delta_native_broadcast_minus_rewrite', '')}`"
        )
        lines.append(f"- fallback_count: `{team.get('fallback_count', '')}`")
    return "\n".join(lines) + "\n"


def _extract_session_file(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        match = re.match(r"Session file:\s*(.+)\s*$", line)
        if match:
            return Path(match.group(1).strip())
    return None


def _first_stream_item(app_payload: dict[str, Any]) -> dict[str, Any]:
    items = app_payload.get("stream_items", []) if isinstance(app_payload, dict) else []
    first = items[0] if isinstance(items, list) and items else {}
    return first if isinstance(first, dict) else {}


def _last_task_message(app_payload: dict[str, Any]) -> dict[str, Any]:
    task_result = app_payload.get("task_result", {}) if isinstance(app_payload, dict) else {}
    messages = task_result.get("messages", []) if isinstance(task_result, dict) else []
    last = messages[-1] if isinstance(messages, list) and messages else {}
    return last if isinstance(last, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
