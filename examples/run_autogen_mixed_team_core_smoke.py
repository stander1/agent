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
from run_autogen_native_smoke import _missing_modules, _read_trace_events  # noqa: E402
from run_autogen_team_rewrite_smoke import (  # noqa: E402
    summarize_team_rewrite_events,
)

EXPECTED_PHASE = "v5.13i"
USER_SCRIPT = PROJECT_ROOT / "examples" / "autogen_mixed_team_core_smoke.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one mixed AutoGen AgentChat Team + Core user script through "
            "AgentLite and validate end-to-end takeover."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-mixed-team-core-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_mixed_team_core_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[args.python, str(USER_SCRIPT)],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    result = ManagedProcessLauncher().launch(
        request,
        environ={
            **os.environ,
            "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
            "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
            "AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE": "1",
            "AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE": "prompt-view",
            "AGENTLITE_AUTOGEN_MIXED_TEAM_CORE_OUTPUT": str(app_output),
        },
    )
    status = read_bootstrap_status(result.status_file)
    report = build_report(
        output_dir=output_dir,
        returncode=result.returncode,
        status=status or {},
        app_output=app_output,
    )
    report_path = output_dir / "autogen_mixed_team_core_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_mixed_team_core_report.md"
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
    team_events = _events_for(trace_events, "autogen_team_input_real_rewrite")
    team_summary = summarize_team_rewrite_events(team_events)
    core_request_rewrite_events = _payloads_for(
        trace_events,
        "autogen_core_content_real_rewrite",
    )
    core_request_rewrite_events = [
        payload
        for payload in core_request_rewrite_events
        if payload.get("native_message_type") == "MixedCoreRequest"
    ]
    core_request_hydration_events = _payloads_for(
        trace_events,
        "autogen_core_receiver_hydration",
    )
    core_request_hydration_events = [
        payload
        for payload in core_request_hydration_events
        if payload.get("native_message_type") == "MixedCoreRequest"
    ]
    core_response_rewrite_events = _payloads_for(
        trace_events,
        "autogen_core_response_real_rewrite",
    )
    core_response_rewrite_events = [
        payload
        for payload in core_response_rewrite_events
        if payload.get("native_response_type") == "MixedCoreReply"
    ]
    core_response_hydration_events = _payloads_for(
        trace_events,
        "autogen_core_response_hydration",
    )
    core_response_hydration_events = [
        payload
        for payload in core_response_hydration_events
        if payload.get("native_response_type") == "MixedCoreReply"
    ]
    bridge_seen = _first_dict(app_payload.get("bridge_seen_messages", []))
    core_received = _first_dict(app_payload.get("core_received", []))
    core_reply = _first_dict(app_payload.get("core_caller_replies", []))
    stream_item = _first_dict(app_payload.get("stream_items", []))
    final_message = _last_task_message(app_payload.get("task_result", {}))
    source_text = USER_SCRIPT.read_text(encoding="utf-8")
    core_request_fallback_count = _sum_count(
        core_request_rewrite_events,
        "rewrite_fallback_count",
    )
    core_request_hydration_fallback_count = _sum_count(
        core_request_hydration_events,
        "hydration_fallback_count",
    )
    core_response_fallback_count = _sum_count(
        core_response_rewrite_events,
        "rewrite_fallback_count",
    )
    core_response_hydration_fallback_count = _sum_count(
        core_response_hydration_events,
        "hydration_fallback_count",
    )
    core_request_token_delta = _sum_count(
        core_request_rewrite_events,
        "token_delta_native_minus_rewrite",
    )
    core_response_token_delta = _sum_count(
        core_response_rewrite_events,
        "token_delta_native_minus_rewrite",
    )
    checks = {
        "target_returncode_zero": returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase_v5_13i": details.get("phase") == EXPECTED_PHASE,
        "real_rewrite_mode_enabled": details.get("broadcast_mode") == "real-rewrite",
        "team_rewrite_enabled": bool(details.get("team_rewrite_enabled")),
        "core_content_rewrite_enabled": details.get("core_content_rewrite_enabled")
        is True,
        "core_receiver_hydrate_prompt_view": details.get(
            "core_receiver_hydrate_mode"
        )
        == "prompt-view",
        "user_script_does_not_import_agentlite": (
            "from agent_runtime" not in source_text
            and "import agent_runtime" not in source_text
            and "agent_runtime." not in source_text
        ),
        "app_output_written": bool(app_payload),
        "agentlite_active_in_user_process": bool(app_payload.get("agentlite_active")),
        "team_rewrite_event_recorded": int(team_summary.get("event_count", 0) or 0)
        >= 1,
        "team_rewrite_applied": int(team_summary.get("applied_count", 0) or 0)
        >= 1,
        "team_rewrite_no_fallback": int(team_summary.get("fallback_count", 0) or 0)
        == 0,
        "team_rewrite_reduces_tokens": int(
            team_summary.get("token_delta_native_broadcast_minus_rewrite", 0) or 0
        )
        > 0,
        "bridge_agent_saw_team_packet": (
            bridge_seen.get("type") == "TextMessage"
            and bool(bridge_seen.get("contains_team_rewrite_marker"))
            and bool(bridge_seen.get("contains_state_pool_marker"))
            and bool(bridge_seen.get("contains_broadcast_manifest"))
            and bool(bridge_seen.get("contains_receiver_prompt_views"))
            and not bool(bridge_seen.get("contains_team_native_marker"))
        ),
        "core_worker_saw_prompt_view_request": (
            core_received.get("message_type") == "MixedCoreRequest"
            and core_received.get("payload_kind") == "mixed_core_request_state"
            and bool(core_received.get("agentlite_prompt_view"))
            and not bool(core_received.get("contains_core_request_native_marker"))
            and not bool(core_received.get("contains_core_rewrite_marker"))
        ),
        "core_caller_saw_prompt_view_reply": (
            core_reply.get("message_type") == "MixedCoreReply"
            and core_reply.get("payload_kind") == "mixed_core_reply_state"
            and bool(core_reply.get("agentlite_prompt_view"))
            and not bool(core_reply.get("contains_core_reply_native_marker"))
            and not bool(core_reply.get("contains_core_rewrite_marker"))
        ),
        "core_request_rewrite_and_hydration_recorded": (
            len(core_request_rewrite_events) >= 1
            and len(core_request_hydration_events) >= 1
        ),
        "core_response_rewrite_and_hydration_recorded": (
            len(core_response_rewrite_events) >= 1
            and len(core_response_hydration_events) >= 1
        ),
        "core_request_no_fallback": core_request_fallback_count == 0
        and core_request_hydration_fallback_count == 0,
        "core_response_no_fallback": core_response_fallback_count == 0
        and core_response_hydration_fallback_count == 0,
        "core_request_reduces_tokens": core_request_token_delta > 0,
        "core_response_reduces_tokens": core_response_token_delta > 0,
        "team_final_output_contains_done": (
            bool(stream_item.get("contains_done_token"))
            or bool(final_message.get("contains_done_token"))
        ),
        "team_final_output_not_agentlite_wire": (
            not bool(final_message.get("contains_team_rewrite_marker"))
            and not bool(final_message.get("contains_state_pool_marker"))
            and not bool(final_message.get("contains_broadcast_manifest"))
        ),
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
        "team_input_real_rewrite": team_summary,
        "bridge_seen_first_message": bridge_seen,
        "core_received_first": core_received,
        "core_caller_reply_first": core_reply,
        "stream_item_first": stream_item,
        "task_result_last_message": final_message,
        "core_request_rewrite_event_count": len(core_request_rewrite_events),
        "core_request_hydration_event_count": len(core_request_hydration_events),
        "core_response_rewrite_event_count": len(core_response_rewrite_events),
        "core_response_hydration_event_count": len(core_response_hydration_events),
        "core_request_fallback_count": core_request_fallback_count,
        "core_request_hydration_fallback_count": core_request_hydration_fallback_count,
        "core_response_fallback_count": core_response_fallback_count,
        "core_response_hydration_fallback_count": (
            core_response_hydration_fallback_count
        ),
        "core_request_token_delta_native_minus_rewrite": core_request_token_delta,
        "core_response_token_delta_native_minus_rewrite": core_response_token_delta,
    }


def _events_for(
    trace_events: list[dict[str, Any]],
    event_type: str,
) -> list[dict[str, Any]]:
    return [
        event
        for event in trace_events
        if event.get("event_type") == event_type
        and isinstance(event.get("payload"), dict)
    ]


def _payloads_for(
    trace_events: list[dict[str, Any]],
    event_type: str,
) -> list[dict[str, Any]]:
    return [event["payload"] for event in _events_for(trace_events, event_type)]


def _sum_count(payloads: list[dict[str, Any]], field_name: str) -> int:
    total = 0
    for payload in payloads:
        try:
            total += int(payload.get(field_name, 0) or 0)
        except (TypeError, ValueError):
            pass
    return total


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _last_task_message(task_result: Any) -> dict[str, Any]:
    if not isinstance(task_result, dict):
        return {}
    messages = task_result.get("messages", [])
    if isinstance(messages, list) and messages and isinstance(messages[-1], dict):
        return messages[-1]
    return {}


def render_markdown(report: dict[str, object]) -> str:
    checks = report.get("checks", {})
    check_lines = []
    if isinstance(checks, dict):
        for key, value in checks.items():
            mark = "PASS" if value else "FAIL"
            check_lines.append(f"- {mark}: `{key}`")
    return "\n".join(
        [
            "# AutoGen Mixed Team Core Smoke",
            "",
            f"- passed: `{report.get('passed')}`",
            f"- returncode: `{report.get('returncode')}`",
            f"- trace: `{report.get('trace_path')}`",
            f"- team rewrite events: `{report.get('team_input_real_rewrite', {})}`",
            f"- core request rewrite events: `{report.get('core_request_rewrite_event_count')}`",
            f"- core request hydration events: `{report.get('core_request_hydration_event_count')}`",
            f"- core response rewrite events: `{report.get('core_response_rewrite_event_count')}`",
            f"- core response hydration events: `{report.get('core_response_hydration_event_count')}`",
            f"- core request token delta: `{report.get('core_request_token_delta_native_minus_rewrite')}`",
            f"- core response token delta: `{report.get('core_response_token_delta_native_minus_rewrite')}`",
            "",
            "## Checks",
            "",
            *check_lines,
            "",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
