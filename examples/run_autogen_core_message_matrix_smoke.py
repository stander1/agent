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
    "dataclass_content",
    "dataclass_body",
    "dataclass_text",
    "pydantic_content",
    "dataclass_content_publish",
}
EXPECTED_FIELDS = {"content", "body", "text"}
EXPECTED_TYPES = {
    "MatrixContentPayload",
    "MatrixBodyPayload",
    "MatrixTextPayload",
    "MatrixPydanticPayload",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run an AutoGen Core message-shape matrix under AgentLite and "
            "validate type-preserving rewrite plus receiver hydration."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-core-matrix-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_core_message_matrix_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            args.python,
            str(PROJECT_ROOT / "examples" / "autogen_core_message_matrix_smoke.py"),
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
            "AGENTLITE_AUTOGEN_CORE_MATRIX_OUTPUT": str(app_output),
        },
    )
    status = read_bootstrap_status(result.status_file)
    report = build_report(
        output_dir=output_dir,
        returncode=result.returncode,
        status=status or {},
        app_output=app_output,
    )
    report_path = output_dir / "autogen_core_message_matrix_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_core_message_matrix_report.md"
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
    rewrite_events = _payloads_for(trace_events, "autogen_core_content_real_rewrite")
    hydration_events = _payloads_for(trace_events, "autogen_core_receiver_hydration")
    shadow_events = _payloads_for(trace_events, "autogen_core_transport_shadow")
    received = app_payload.get("received", []) if isinstance(app_payload, dict) else []
    received_cases = {
        str(item.get("case", ""))
        for item in received
        if isinstance(item, dict)
    }
    received_fields = {
        str(item.get("field_name", ""))
        for item in received
        if isinstance(item, dict)
    }
    received_types = {
        str(item.get("message_type", ""))
        for item in received
        if isinstance(item, dict)
    }
    received_kinds = {
        str(item.get("kind", ""))
        for item in received
        if isinstance(item, dict)
    }
    marker_flags = [
        bool(item.get("agentlite_rewrite_marker"))
        for item in received
        if isinstance(item, dict)
    ]
    prompt_view_flags = [
        bool(item.get("agentlite_prompt_view"))
        for item in received
        if isinstance(item, dict)
    ]
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
    rewrite_types = {
        str(payload.get("native_message_type", "")) for payload in rewrite_events
    }
    rewrite_fields = {
        str(payload.get("rewritten_field", "")) for payload in rewrite_events
    }
    hydration_types = {
        str(payload.get("native_message_type", "")) for payload in hydration_events
    }
    hydration_fields = {
        str(payload.get("hydrated_field", "")) for payload in hydration_events
    }
    rewrite_applied = [
        payload for payload in rewrite_events if payload.get("rewrite_applied") is True
    ]
    rewrite_fallback_count = sum(
        int(payload.get("rewrite_fallback_count", 0) or 0)
        for payload in rewrite_events
    )
    hydration_applied = [
        payload
        for payload in hydration_events
        if payload.get("hydration_applied") is True
    ]
    hydration_fallback_count = sum(
        int(payload.get("hydration_fallback_count", 0) or 0)
        for payload in hydration_events
    )
    token_delta = sum(
        int(payload.get("token_delta_native_minus_rewrite", 0) or 0)
        for payload in rewrite_events
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
        "all_cases_received": EXPECTED_CASES <= received_cases,
        "direct_and_publish_received": {"direct", "publish"} <= received_kinds,
        "expected_fields_received": EXPECTED_FIELDS <= received_fields,
        "expected_types_received": EXPECTED_TYPES <= received_types,
        "rewrite_events_for_all_messages": len(rewrite_events) >= len(EXPECTED_CASES),
        "hydration_events_for_all_messages": len(hydration_events) >= len(EXPECTED_CASES),
        "rewrite_applied_without_fallback": len(rewrite_applied) == len(rewrite_events)
        and bool(rewrite_events)
        and rewrite_fallback_count == 0,
        "hydration_applied_without_fallback": len(hydration_applied)
        == len(hydration_events)
        and bool(hydration_events)
        and hydration_fallback_count == 0,
        "rewrite_types_cover_matrix": EXPECTED_TYPES <= rewrite_types,
        "hydration_types_cover_matrix": EXPECTED_TYPES <= hydration_types,
        "rewrite_fields_cover_matrix": EXPECTED_FIELDS <= rewrite_fields,
        "hydration_fields_cover_matrix": EXPECTED_FIELDS <= hydration_fields,
        "wire_marker_not_leaked": bool(marker_flags) and not any(marker_flags),
        "prompt_view_received": bool(prompt_view_flags) and all(prompt_view_flags),
        "content_shortened_at_receiver": bool(content_chars)
        and max(content_chars) < max_native_chars,
        "shadow_still_recorded": len(shadow_events) >= len(EXPECTED_CASES),
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
        "received_cases": sorted(received_cases),
        "received_fields": sorted(received_fields),
        "received_types": sorted(received_types),
        "rewrite_event_count": len(rewrite_events),
        "rewrite_applied_count": len(rewrite_applied),
        "rewrite_fallback_count": rewrite_fallback_count,
        "rewrite_types": sorted(rewrite_types),
        "rewrite_fields": sorted(rewrite_fields),
        "hydration_event_count": len(hydration_events),
        "hydration_applied_count": len(hydration_applied),
        "hydration_fallback_count": hydration_fallback_count,
        "hydration_types": sorted(hydration_types),
        "hydration_fields": sorted(hydration_fields),
        "shadow_event_count": len(shadow_events),
        "token_delta_native_minus_rewrite": token_delta,
        "receiver_content_chars": content_chars,
    }


def _payloads_for(
    trace_events: list[dict[str, Any]],
    event_type: str,
) -> list[dict[str, Any]]:
    return [
        event["payload"]
        for event in trace_events
        if event.get("event_type") == event_type
        and isinstance(event.get("payload"), dict)
    ]


def render_markdown(report: dict[str, object]) -> str:
    lines = [
        "# AutoGen Core Message Matrix Smoke Report",
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
            "## Matrix",
            "",
            f"- received_cases: `{report.get('received_cases', [])}`",
            f"- received_types: `{report.get('received_types', [])}`",
            f"- received_fields: `{report.get('received_fields', [])}`",
            f"- rewrite_event_count: `{report.get('rewrite_event_count', 0)}`",
            f"- hydration_event_count: `{report.get('hydration_event_count', 0)}`",
            f"- rewrite_fallback_count: `{report.get('rewrite_fallback_count', 0)}`",
            f"- hydration_fallback_count: `{report.get('hydration_fallback_count', 0)}`",
            f"- token_delta_native_minus_rewrite: `{report.get('token_delta_native_minus_rewrite', 0)}`",
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
