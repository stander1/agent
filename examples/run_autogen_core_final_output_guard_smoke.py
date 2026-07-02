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

from agent_runtime.launcher import (  # noqa: E402
    LaunchRequest,
    ManagedProcessLauncher,
    read_bootstrap_status,
)
from run_autogen_native_smoke import _missing_modules, _read_trace_events  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run an AutoGen Core final-output guard smoke under AgentLite and "
            "validate that sender-less send_message replies stay native."
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
            print("Missing AutoGen modules: " + ", ".join(missing), file=sys.stderr)
            print(
                'Install after confirmation with: python -m pip install -e ".[autogen]"',
                file=sys.stderr,
            )
            return 2

    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-core-final-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_core_final_output_guard_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            args.python,
            str(PROJECT_ROOT / "examples" / "autogen_core_final_output_guard_smoke.py"),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    result = ManagedProcessLauncher().launch(
        request,
        environ={
            **os.environ,
            "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
            "AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE": "1",
            "AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE": "prompt-view",
            "AGENTLITE_AUTOGEN_CORE_FINAL_OUTPUT": str(app_output),
        },
    )
    status = read_bootstrap_status(result.status_file)
    report = build_report(
        output_dir=output_dir,
        returncode=result.returncode,
        status=status or {},
        app_output=app_output,
    )
    report_path = output_dir / "autogen_core_final_output_guard_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_core_final_output_guard_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def build_report(
    *,
    output_dir: Path,
    returncode: int,
    status: dict[str, object],
    app_output: Path,
) -> dict[str, object]:
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
    request_rewrite_events = _payloads_for(
        trace_events,
        "autogen_core_content_real_rewrite",
    )
    request_hydration_events = _payloads_for(
        trace_events,
        "autogen_core_receiver_hydration",
    )
    response_rewrite_events = _payloads_for(
        trace_events,
        "autogen_core_response_real_rewrite",
    )
    response_hydration_events = _payloads_for(
        trace_events,
        "autogen_core_response_hydration",
    )
    received = app_payload.get("received", []) if isinstance(app_payload, dict) else []
    reply = app_payload.get("reply", {}) if isinstance(app_payload, dict) else {}
    received_flags = {
        "request_marker": any(
            bool(row.get("agentlite_rewrite_marker"))
            for row in received
            if isinstance(row, dict)
        ),
        "request_prompt_view": all(
            bool(row.get("agentlite_prompt_view"))
            for row in received
            if isinstance(row, dict)
        )
        and bool(received),
    }
    native_final_chars = int(app_payload.get("native_final_chars", 0) or 0)
    reply_chars = int(reply.get("content_chars", 0) or 0) if isinstance(reply, dict) else 0
    request_rewrite_fallback_count = sum(
        int(payload.get("rewrite_fallback_count", 0) or 0)
        for payload in request_rewrite_events
    )
    request_hydration_fallback_count = sum(
        int(payload.get("hydration_fallback_count", 0) or 0)
        for payload in request_hydration_events
    )
    checks = {
        "target_returncode_zero": returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase_v5_13h": details.get("phase") == "v5.13h",
        "core_content_rewrite_enabled": details.get("core_content_rewrite_enabled")
        is True,
        "core_receiver_hydrate_prompt_view": details.get(
            "core_receiver_hydrate_mode"
        )
        == "prompt-view",
        "app_output_written": bool(app_payload),
        "agentlite_active_in_target": bool(app_payload.get("agentlite_active"))
        if isinstance(app_payload, dict)
        else False,
        "request_received_as_prompt_view": received_flags["request_prompt_view"],
        "request_wire_marker_not_leaked": not received_flags["request_marker"],
        "request_rewrite_recorded": len(request_rewrite_events) >= 1,
        "request_hydration_recorded": len(request_hydration_events) >= 1,
        "request_rewrite_without_fallback": request_rewrite_fallback_count == 0,
        "request_hydration_without_fallback": request_hydration_fallback_count == 0,
        "response_rewrite_not_triggered": len(response_rewrite_events) == 0,
        "response_hydration_not_triggered": len(response_hydration_events) == 0,
        "reply_type_preserved": isinstance(reply, dict)
        and reply.get("message_type") == "FinalAnswer",
        "reply_payload_kind_preserved": isinstance(reply, dict)
        and reply.get("payload_kind") == "final_output_state",
        "reply_wire_marker_not_leaked": isinstance(reply, dict)
        and not bool(reply.get("agentlite_rewrite_marker")),
        "reply_not_prompt_view": isinstance(reply, dict)
        and not bool(reply.get("agentlite_prompt_view")),
        "reply_full_native_preserved": reply_chars == native_final_chars
        and native_final_chars > 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "returncode": returncode,
        "output_dir": str(output_dir),
        "bootstrap_status": status,
        "app_output_path": str(app_output),
        "app_output": app_payload,
        "trace_path": str(trace_path) if trace_path else "",
        "trace_event_counts": dict(sorted(event_counts.items())),
        "request_rewrite_event_count": len(request_rewrite_events),
        "request_hydration_event_count": len(request_hydration_events),
        "request_rewrite_fallback_count": request_rewrite_fallback_count,
        "request_hydration_fallback_count": request_hydration_fallback_count,
        "response_rewrite_event_count": len(response_rewrite_events),
        "response_hydration_event_count": len(response_hydration_events),
        "native_final_chars": native_final_chars,
        "reply_content_chars": reply_chars,
    }


def _payloads_for(
    trace_events: list[dict[str, Any]],
    event_type: str,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in trace_events:
        if event.get("event_type") != event_type:
            continue
        payload = event.get("payload", {})
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def render_markdown(report: dict[str, object]) -> str:
    checks = report.get("checks", {})
    check_lines = []
    if isinstance(checks, dict):
        for key, value in checks.items():
            mark = "PASS" if value else "FAIL"
            check_lines.append(f"- {mark}: `{key}`")
    return "\n".join(
        [
            "# AutoGen Core Final Output Guard Smoke",
            "",
            f"- passed: `{report.get('passed')}`",
            f"- returncode: `{report.get('returncode')}`",
            f"- trace: `{report.get('trace_path')}`",
            f"- request rewrite events: `{report.get('request_rewrite_event_count')}`",
            f"- request hydration events: `{report.get('request_hydration_event_count')}`",
            f"- response rewrite events: `{report.get('response_rewrite_event_count')}`",
            f"- response hydration events: `{report.get('response_hydration_event_count')}`",
            f"- native final chars: `{report.get('native_final_chars')}`",
            f"- reply chars: `{report.get('reply_content_chars')}`",
            "",
            "## Checks",
            "",
            *check_lines,
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
