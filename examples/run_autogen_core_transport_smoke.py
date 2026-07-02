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
            "Run a native AutoGen Core Runtime transport smoke under AgentLite "
            "and validate send_message / publish_message coverage."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-core-transport-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_core_transport_output.json"
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
    report_path = output_dir / "autogen_core_transport_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_core_transport_report.md"
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
    event_counts = Counter(
        str(event.get("event_type", "")) for event in trace_events
    )
    app_payload = (
        json.loads(app_output.read_text(encoding="utf-8"))
        if app_output.exists()
        else {}
    )
    transport_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_transport_input_state"
        and isinstance(event.get("payload"), dict)
    ]
    core_shadow_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_core_transport_shadow"
        and isinstance(event.get("payload"), dict)
    ]
    methods = sorted(
        {
            str(event["payload"].get("method", ""))
            for event in core_shadow_events
            if isinstance(event.get("payload"), dict)
        }
    )
    receivers = sorted(
        {
            str(event["payload"].get("declared_receiver", ""))
            for event in core_shadow_events
            if isinstance(event.get("payload"), dict)
            and event["payload"].get("declared_receiver")
        }
    )
    schema_valid_count = sum(
        1
        for event in core_shadow_events
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("schema_valid") is True
    )
    prompt_view_count = sum(
        1
        for event in core_shadow_events
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("prompt_view_available") is True
    )
    state_ref_count = sum(
        int(event["payload"].get("state_ref_count", 0) or 0)
        for event in core_shadow_events
        if isinstance(event.get("payload"), dict)
    )
    token_delta = sum(
        int(event["payload"].get("token_delta_native_minus_wire_plus_prompt_view", 0) or 0)
        for event in core_shadow_events
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
    checks = {
        "target_returncode_zero": returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase_v5_13h": details.get("phase") == "v5.13h",
        "app_output_written": bool(app_payload),
        "agentlite_active_in_target": bool(app_payload.get("agentlite_active"))
        if isinstance(app_payload, dict)
        else False,
        "direct_and_publish_received": {"direct", "publish"} <= set(received_kinds),
        "transport_state_events_recorded": len(transport_events) >= 2,
        "core_shadow_events_recorded": len(core_shadow_events) >= 2,
        "send_and_publish_shadowed": {"send_message", "publish_message"}
        <= set(methods),
        "route_receivers_recorded": any("agent_direct_agent" in item for item in receivers)
        and any("topic_core" in item for item in receivers),
        "state_refs_recorded": state_ref_count >= 2,
        "schema_valid": schema_valid_count == len(core_shadow_events)
        and bool(core_shadow_events),
        "prompt_view_available": prompt_view_count == len(core_shadow_events)
        and bool(core_shadow_events),
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
        "transport_state_event_count": len(transport_events),
        "core_transport_shadow_event_count": len(core_shadow_events),
        "core_transport_methods": methods,
        "core_transport_receivers": receivers,
        "core_transport_state_ref_count": state_ref_count,
        "core_transport_schema_valid_count": schema_valid_count,
        "core_transport_prompt_view_count": prompt_view_count,
        "core_transport_token_delta": token_delta,
    }


def render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# AutoGen Core Transport Smoke Report",
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
            f"- transport_state_event_count: `{report.get('transport_state_event_count', 0)}`",
            f"- core_transport_shadow_event_count: `{report.get('core_transport_shadow_event_count', 0)}`",
            f"- core_transport_methods: `{report.get('core_transport_methods', [])}`",
            f"- core_transport_receivers: `{report.get('core_transport_receivers', [])}`",
            f"- core_transport_state_ref_count: `{report.get('core_transport_state_ref_count', 0)}`",
            f"- core_transport_token_delta: `{report.get('core_transport_token_delta', 0)}`",
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
