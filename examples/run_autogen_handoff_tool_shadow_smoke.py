from __future__ import annotations

import argparse
import json
import os
import sys
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
from run_autogen_long_shadow_smoke import augment_long_context_report  # noqa: E402
from run_autogen_native_smoke import (  # noqa: E402
    _collect_decoded_messages,
    _missing_modules,
    _read_trace_events,
    build_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a native AutoGen handoff/tool-call smoke under AgentLite and "
            "validate SHP shadow handoff coverage."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.12h-autogen-handoff-tool-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_handoff_tool_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            args.python,
            str(PROJECT_ROOT / "examples" / "autogen_handoff_tool_shadow_smoke.py"),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_HANDOFF_TOOL_OUTPUT": str(app_output),
    }
    result = ManagedProcessLauncher().launch(request, environ=env)
    status = read_bootstrap_status(result.status_file)
    report = build_report(
        output_dir=output_dir,
        returncode=result.returncode,
        status=status or {},
        app_output=app_output,
    )
    augment_long_context_report(report)
    augment_message_type_coverage(report)
    report_path = output_dir / "autogen_handoff_tool_shadow_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_handoff_tool_shadow_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def augment_message_type_coverage(report: dict[str, Any]) -> None:
    trace_path = Path(str(report.get("trace_path", "")))
    trace_events = _read_trace_events(trace_path)
    decoded_messages = _collect_decoded_messages(trace_events)
    decoded_kinds = {
        str(message.get("message_kind", ""))
        for message in decoded_messages
        if isinstance(message, dict)
    }
    handoff_targets = sorted(
        {
            str(message.get("target", ""))
            for message in decoded_messages
            if isinstance(message, dict)
            and message.get("message_kind") == "handoff"
            and message.get("target")
        }
    )
    shadow_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_shp_handoff_shadow"
        and isinstance(event.get("payload"), dict)
    ]
    transport_state_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_transport_input_state"
        and isinstance(event.get("payload"), dict)
    ]
    shadow_plans = [
        event["payload"].get("plan", {})
        for event in shadow_events
        if isinstance(event.get("payload"), dict)
        and isinstance(event["payload"].get("plan", {}), dict)
    ]
    shadow_receivers = sorted(
        {
            str(plan.get("declared_receiver", ""))
            for plan in shadow_plans
            if plan.get("declared_receiver")
        }
    )
    handoff_target_plan_count = sum(
        1
        for plan in shadow_plans
        if plan.get("receiver_source") == "handoff_message_target"
    )
    tool_summary_shadow_count = sum(
        1
        for plan in shadow_plans
        if "tool_summary" in list(plan.get("message_kinds", []) or [])
    )
    coverage = {
        "decoded_message_kinds": sorted(decoded_kinds),
        "handoff_targets": handoff_targets,
        "shadow_declared_receivers": shadow_receivers,
        "handoff_target_plan_count": handoff_target_plan_count,
        "tool_summary_shadow_count": tool_summary_shadow_count,
        "tool_transport_state_count": len(transport_state_events),
    }
    report["message_type_coverage"] = coverage
    checks = report.get("checks", {})
    if isinstance(checks, dict):
        checks["handoff_message_decoded"] = "handoff" in decoded_kinds
        checks["tool_call_request_decoded"] = "tool_call" in decoded_kinds
        checks["tool_call_result_decoded"] = "tool_result" in decoded_kinds
        checks["tool_call_summary_decoded"] = "tool_summary" in decoded_kinds
        checks["handoff_target_preserved"] = bool(handoff_targets)
        checks["shadow_plan_uses_handoff_target"] = handoff_target_plan_count > 0
        checks["tool_summary_shadow_state_recorded"] = tool_summary_shadow_count > 0
        checks["tool_transport_input_state_recorded"] = len(transport_state_events) >= 2
        report["passed"] = all(bool(value) for value in checks.values())


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Handoff / ToolCall Shadow Report",
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
    lines.extend(["", "## Message Type Coverage", ""])
    coverage = report.get("message_type_coverage", {})
    if isinstance(coverage, dict):
        for key, value in coverage.items():
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Long Context Cost", ""])
    cost = report.get("long_context_cost_comparison", {})
    if isinstance(cost, dict):
        for key, value in cost.items():
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    lines.append(f"- app_output_path: `{report.get('app_output_path', '')}`")
    lines.append(f"- trace_path: `{report.get('trace_path', '')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
