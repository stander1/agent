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
            "Run a native AutoGen Core Runtime smoke under AgentLite and validate "
            "type-preserving content rewrite for send_message / publish_message."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-core-rewrite-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_core_content_rewrite_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            args.python,
            str(PROJECT_ROOT / "examples" / "autogen_core_transport_smoke.py"),
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
            "AGENTLITE_AUTOGEN_CORE_TRANSPORT_OUTPUT": str(app_output),
        },
    )
    status = read_bootstrap_status(result.status_file)
    report = build_report(
        output_dir=output_dir,
        returncode=result.returncode,
        status=status or {},
        app_output=app_output,
    )
    report_path = output_dir / "autogen_core_content_rewrite_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_core_content_rewrite_report.md"
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
    rewrite_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_core_content_real_rewrite"
        and isinstance(event.get("payload"), dict)
    ]
    shadow_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_core_transport_shadow"
        and isinstance(event.get("payload"), dict)
    ]
    hydration_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_core_receiver_hydration"
        and isinstance(event.get("payload"), dict)
    ]
    methods = sorted(
        {
            str(event["payload"].get("method", ""))
            for event in rewrite_events
            if isinstance(event.get("payload"), dict)
        }
    )
    applied_events = [
        event
        for event in rewrite_events
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("rewrite_applied") is True
    ]
    fallback_count = sum(
        int(event["payload"].get("rewrite_fallback_count", 0) or 0)
        for event in rewrite_events
        if isinstance(event.get("payload"), dict)
    )
    schema_valid_count = sum(
        1
        for event in rewrite_events
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("schema_valid") is True
    )
    prompt_view_count = sum(
        1
        for event in rewrite_events
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("prompt_view_available") is True
    )
    state_ref_count = sum(
        int(event["payload"].get("state_ref_count", 0) or 0)
        for event in rewrite_events
        if isinstance(event.get("payload"), dict)
    )
    token_delta = sum(
        int(event["payload"].get("token_delta_native_minus_rewrite", 0) or 0)
        for event in rewrite_events
        if isinstance(event.get("payload"), dict)
    )
    hydration_applied_events = [
        event
        for event in hydration_events
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("hydration_applied") is True
    ]
    hydration_fallback_count = sum(
        int(event["payload"].get("hydration_fallback_count", 0) or 0)
        for event in hydration_events
        if isinstance(event.get("payload"), dict)
    )
    received = app_payload.get("received", []) if isinstance(app_payload, dict) else []
    received_kinds = sorted(
        {
            str(item.get("kind", ""))
            for item in received
            if isinstance(item, dict)
        }
    )
    received_markers = [
        bool(item.get("agentlite_rewrite_marker"))
        for item in received
        if isinstance(item, dict)
    ]
    received_prompt_views = [
        bool(item.get("agentlite_prompt_view"))
        for item in received
        if isinstance(item, dict)
    ]
    received_types = sorted(
        {
            str(item.get("message_type", ""))
            for item in received
            if isinstance(item, dict)
        }
    )
    payload_kinds = sorted(
        {
            str(item.get("payload_kind", ""))
            for item in received
            if isinstance(item, dict)
        }
    )
    content_chars = [
        int(item.get("content_chars", 0) or 0)
        for item in received
        if isinstance(item, dict)
    ]
    input_chars = app_payload.get("input_chars", {}) if isinstance(app_payload, dict) else {}
    max_native_chars = max(
        [int(value or 0) for value in input_chars.values()]
        if isinstance(input_chars, dict)
        else [0]
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
        "direct_and_publish_received": {"direct", "publish"} <= set(received_kinds),
        "native_type_preserved_at_receiver": received_types == ["CorePayload"],
        "payload_kind_preserved_at_receiver": payload_kinds == ["core_transport_state"],
        "rewrite_marker_not_leaked_to_receiver": bool(received_markers)
        and not any(received_markers),
        "prompt_view_received": bool(received_prompt_views)
        and all(received_prompt_views),
        "content_shortened_at_receiver": bool(content_chars)
        and max(content_chars) < max_native_chars,
        "core_rewrite_events_recorded": len(rewrite_events) >= 2,
        "send_and_publish_rewritten": {"send_message", "publish_message"}
        <= set(methods),
        "rewrite_applied_without_fallback": len(applied_events) == len(rewrite_events)
        and bool(rewrite_events)
        and fallback_count == 0,
        "receiver_hydration_applied_without_fallback": len(hydration_applied_events)
        == len(hydration_events)
        and bool(hydration_events)
        and hydration_fallback_count == 0,
        "shadow_still_recorded": len(shadow_events) >= 2,
        "state_refs_recorded": state_ref_count >= 2,
        "schema_valid": schema_valid_count == len(rewrite_events)
        and bool(rewrite_events),
        "prompt_view_available": prompt_view_count == len(rewrite_events)
        and bool(rewrite_events),
        "token_reduced": token_delta > 0,
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
        "core_content_rewrite_event_count": len(rewrite_events),
        "core_content_rewrite_applied_count": len(applied_events),
        "core_content_rewrite_fallback_count": fallback_count,
        "core_content_rewrite_methods": methods,
        "core_content_rewrite_state_ref_count": state_ref_count,
        "core_content_rewrite_schema_valid_count": schema_valid_count,
        "core_content_rewrite_prompt_view_count": prompt_view_count,
        "core_content_rewrite_token_delta": token_delta,
        "core_receiver_hydration_event_count": len(hydration_events),
        "core_receiver_hydration_applied_count": len(hydration_applied_events),
        "core_receiver_hydration_fallback_count": hydration_fallback_count,
        "core_transport_shadow_event_count": len(shadow_events),
        "receiver_content_chars": content_chars,
        "receiver_message_types": received_types,
        "receiver_payload_kinds": payload_kinds,
    }


def render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# AutoGen Core Content Rewrite Smoke Report",
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
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            f"- core_content_rewrite_event_count: `{report.get('core_content_rewrite_event_count', 0)}`",
            f"- core_content_rewrite_applied_count: `{report.get('core_content_rewrite_applied_count', 0)}`",
            f"- core_content_rewrite_fallback_count: `{report.get('core_content_rewrite_fallback_count', 0)}`",
            f"- core_content_rewrite_methods: `{report.get('core_content_rewrite_methods', [])}`",
            f"- core_content_rewrite_state_ref_count: `{report.get('core_content_rewrite_state_ref_count', 0)}`",
            f"- core_content_rewrite_token_delta: `{report.get('core_content_rewrite_token_delta', 0)}`",
            f"- core_receiver_hydration_event_count: `{report.get('core_receiver_hydration_event_count', 0)}`",
            f"- core_receiver_hydration_applied_count: `{report.get('core_receiver_hydration_applied_count', 0)}`",
            f"- core_receiver_hydration_fallback_count: `{report.get('core_receiver_hydration_fallback_count', 0)}`",
            f"- receiver_content_chars: `{report.get('receiver_content_chars', [])}`",
            "",
            "## Artifacts",
            "",
            f"- output_dir: `{report.get('output_dir', '')}`",
            f"- app_output_path: `{report.get('app_output_path', '')}`",
            f"- trace_path: `{report.get('trace_path', '')}`",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
