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

EXPECTED_CASES = {
    "response_content",
    "response_body",
    "response_text",
    "response_pydantic",
}
EXPECTED_FIELDS = {"content", "body", "text"}
EXPECTED_RESPONSE_TYPES = {
    "ResponseContentReply",
    "ResponseBodyReply",
    "ResponseTextReply",
    "ResponsePydanticReply",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run an AutoGen Core send_message response rewrite smoke under "
            "AgentLite and validate request plus response takeover."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-core-response-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_core_response_rewrite_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            args.python,
            str(PROJECT_ROOT / "examples" / "autogen_core_response_rewrite_smoke.py"),
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
            "AGENTLITE_AUTOGEN_CORE_RESPONSE_OUTPUT": str(app_output),
        },
    )
    status = read_bootstrap_status(result.status_file)
    report = build_report(
        output_dir=output_dir,
        returncode=result.returncode,
        status=status or {},
        app_output=app_output,
    )
    report_path = output_dir / "autogen_core_response_rewrite_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_core_response_rewrite_report.md"
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
    received_requests = (
        app_payload.get("received_requests", [])
        if isinstance(app_payload, dict)
        else []
    )
    caller_replies = (
        app_payload.get("caller_replies", [])
        if isinstance(app_payload, dict)
        else []
    )
    request_cases = _values(received_requests, "case")
    reply_cases = _values(caller_replies, "case")
    reply_fields = _values(caller_replies, "field_name")
    reply_types = _values(caller_replies, "message_type")
    request_marker_flags = _bools(received_requests, "agentlite_rewrite_marker")
    request_prompt_flags = _bools(received_requests, "agentlite_prompt_view")
    reply_marker_flags = _bools(caller_replies, "agentlite_rewrite_marker")
    reply_prompt_flags = _bools(caller_replies, "agentlite_prompt_view")
    reply_chars = _ints(caller_replies, "content_chars")
    native_reply_chars = (
        app_payload.get("native_reply_chars", {})
        if isinstance(app_payload, dict)
        else {}
    )
    native_reply_char_values = (
        [int(value or 0) for value in native_reply_chars.values()]
        if isinstance(native_reply_chars, dict)
        else []
    )
    max_native_reply_chars = (
        max(native_reply_char_values) if native_reply_char_values else 0
    )
    response_rewrite_applied = [
        payload
        for payload in response_rewrite_events
        if payload.get("rewrite_applied") is True
    ]
    response_hydration_applied = [
        payload
        for payload in response_hydration_events
        if payload.get("hydration_applied") is True
    ]
    response_rewrite_fallback_count = sum(
        int(payload.get("rewrite_fallback_count", 0) or 0)
        for payload in response_rewrite_events
    )
    response_hydration_fallback_count = sum(
        int(payload.get("hydration_fallback_count", 0) or 0)
        for payload in response_hydration_events
    )
    request_rewrite_fallback_count = sum(
        int(payload.get("rewrite_fallback_count", 0) or 0)
        for payload in request_rewrite_events
    )
    request_hydration_fallback_count = sum(
        int(payload.get("hydration_fallback_count", 0) or 0)
        for payload in request_hydration_events
    )
    request_token_delta = sum(
        int(payload.get("token_delta_native_minus_rewrite", 0) or 0)
        for payload in request_rewrite_events
    )
    response_token_delta = sum(
        int(payload.get("token_delta_native_minus_rewrite", 0) or 0)
        for payload in response_rewrite_events
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
        "all_requests_received": EXPECTED_CASES <= request_cases,
        "all_replies_returned": EXPECTED_CASES <= reply_cases,
        "reply_fields_cover_matrix": EXPECTED_FIELDS <= reply_fields,
        "reply_types_cover_matrix": EXPECTED_RESPONSE_TYPES <= reply_types,
        "request_rewrite_events_for_all_messages": len(request_rewrite_events)
        >= len(EXPECTED_CASES),
        "request_hydration_events_for_all_messages": len(request_hydration_events)
        >= len(EXPECTED_CASES),
        "response_rewrite_events_for_all_replies": len(response_rewrite_events)
        >= len(EXPECTED_CASES),
        "response_hydration_events_for_all_replies": len(response_hydration_events)
        >= len(EXPECTED_CASES),
        "request_rewrite_without_fallback": request_rewrite_fallback_count == 0,
        "request_hydration_without_fallback": request_hydration_fallback_count == 0,
        "response_rewrite_without_fallback": len(response_rewrite_applied)
        == len(response_rewrite_events)
        and bool(response_rewrite_events)
        and response_rewrite_fallback_count == 0,
        "response_hydration_without_fallback": len(response_hydration_applied)
        == len(response_hydration_events)
        and bool(response_hydration_events)
        and response_hydration_fallback_count == 0,
        "request_wire_marker_not_leaked": bool(request_marker_flags)
        and not any(request_marker_flags),
        "request_prompt_view_received": bool(request_prompt_flags)
        and all(request_prompt_flags),
        "reply_wire_marker_not_leaked": bool(reply_marker_flags)
        and not any(reply_marker_flags),
        "reply_prompt_view_returned": bool(reply_prompt_flags)
        and all(reply_prompt_flags),
        "reply_content_shortened": bool(reply_chars)
        and max(reply_chars) < max_native_reply_chars,
        "request_token_reduced": request_token_delta > 0,
        "response_token_reduced": response_token_delta > 0,
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
        "request_cases": sorted(request_cases),
        "reply_cases": sorted(reply_cases),
        "reply_fields": sorted(reply_fields),
        "reply_types": sorted(reply_types),
        "request_rewrite_event_count": len(request_rewrite_events),
        "request_hydration_event_count": len(request_hydration_events),
        "request_rewrite_fallback_count": request_rewrite_fallback_count,
        "request_hydration_fallback_count": request_hydration_fallback_count,
        "response_rewrite_event_count": len(response_rewrite_events),
        "response_hydration_event_count": len(response_hydration_events),
        "response_rewrite_fallback_count": response_rewrite_fallback_count,
        "response_hydration_fallback_count": response_hydration_fallback_count,
        "request_token_delta_native_minus_rewrite": request_token_delta,
        "response_token_delta_native_minus_rewrite": response_token_delta,
        "reply_content_chars": reply_chars,
        "max_native_reply_chars": max_native_reply_chars,
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


def _values(rows: Any, key: str) -> set[str]:
    return {
        str(row.get(key, ""))
        for row in rows
        if isinstance(row, dict) and row.get(key, "") != ""
    }


def _bools(rows: Any, key: str) -> list[bool]:
    return [
        bool(row.get(key))
        for row in rows
        if isinstance(row, dict)
    ]


def _ints(rows: Any, key: str) -> list[int]:
    values = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            values.append(int(row.get(key, 0) or 0))
        except (TypeError, ValueError):
            values.append(0)
    return values


def render_markdown(report: dict[str, object]) -> str:
    checks = report.get("checks", {})
    check_lines = []
    if isinstance(checks, dict):
        for key, value in checks.items():
            mark = "PASS" if value else "FAIL"
            check_lines.append(f"- {mark}: `{key}`")
    return "\n".join(
        [
            "# AutoGen Core Response Rewrite Smoke",
            "",
            f"- passed: `{report.get('passed')}`",
            f"- returncode: `{report.get('returncode')}`",
            f"- trace: `{report.get('trace_path')}`",
            f"- request rewrite events: `{report.get('request_rewrite_event_count')}`",
            f"- request hydration events: `{report.get('request_hydration_event_count')}`",
            f"- response rewrite events: `{report.get('response_rewrite_event_count')}`",
            f"- response hydration events: `{report.get('response_hydration_event_count')}`",
            f"- request token delta: `{report.get('request_token_delta_native_minus_rewrite')}`",
            f"- response token delta: `{report.get('response_token_delta_native_minus_rewrite')}`",
            "",
            "## Checks",
            "",
            *check_lines,
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
