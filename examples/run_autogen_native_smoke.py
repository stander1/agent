from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.launcher import (  # noqa: E402
    LaunchRequest,
    ManagedProcessLauncher,
    read_bootstrap_status,
)


REQUIRED_MODULES = ("autogen_agentchat", "autogen_core")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a native AutoGen RoundRobinGroupChat smoke under AgentLite. "
            "The target app imports only AutoGen modules; AgentLite is injected "
            "by the launcher."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.12d-autogen-native-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_native_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            args.python,
            str(PROJECT_ROOT / "examples" / "autogen_native_roundrobin_smoke.py"),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_SMOKE_OUTPUT": str(app_output),
    }
    result = ManagedProcessLauncher().launch(request, environ=env)
    status = read_bootstrap_status(result.status_file)
    report = build_report(
        output_dir=output_dir,
        returncode=result.returncode,
        status=status or {},
        app_output=app_output,
    )
    report_path = output_dir / "autogen_native_smoke_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_native_smoke_report.md"
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
    state_ref_count = sum(
        len(event.get("payload", {}).get("state_refs", []))
        for event in trace_events
        if event.get("event_type") == "autogen_agent_output"
        and isinstance(event.get("payload"), dict)
    )
    decoded_messages = _collect_decoded_messages(trace_events)
    decoded_kinds = sorted(
        {
            str(message.get("message_kind", ""))
            for message in decoded_messages
            if isinstance(message, dict)
        }
    )
    shadow_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_shp_handoff_shadow"
        and isinstance(event.get("payload"), dict)
    ]
    shadow_cost = _summarize_shadow_cost(shadow_events)
    required_checks = {
        "target_returncode_zero": returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase_v5_12x": details.get("phase") == "v5.12x",
        "app_output_written": bool(app_payload),
        "agentlite_active_in_target": bool(app_payload.get("agentlite_active")),
        "receive_event_recorded": event_counts["autogen_agent_receive"] > 0,
        "output_event_recorded": event_counts["autogen_agent_output"] > 0,
        "state_ref_recorded": state_ref_count > 0,
        "codec_decoded_messages_recorded": len(decoded_messages) > 0,
        "shp_shadow_event_recorded": len(shadow_events) > 0,
        "shp_shadow_envelope_recorded": _has_shp_shadow_envelope(shadow_events),
        "shp_shadow_gate_recorded": _has_shp_shadow_gate(shadow_events),
    }
    return {
        "passed": all(required_checks.values()),
        "checks": required_checks,
        "returncode": returncode,
        "output_dir": str(output_dir),
        "bootstrap_status": status,
        "app_output_path": str(app_output),
        "app_output": app_payload,
        "trace_path": str(trace_path) if trace_path else "",
        "trace_event_counts": dict(sorted(event_counts.items())),
        "state_ref_count": state_ref_count,
        "decoded_message_count": len(decoded_messages),
        "decoded_message_kinds": decoded_kinds,
        "shp_shadow_event_count": len(shadow_events),
        "shp_shadow_cost": shadow_cost,
    }


def render_markdown(report: dict[str, object]) -> str:
    checks = report.get("checks", {})
    if not isinstance(checks, dict):
        checks = {}
    lines = [
        "# AutoGen Native Smoke Report",
        "",
        f"passed: `{str(report.get('passed')).lower()}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        lines.append(f"- `{key}`: `{str(value).lower()}`")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- output_dir: `{report.get('output_dir', '')}`",
            f"- app_output_path: `{report.get('app_output_path', '')}`",
            f"- trace_path: `{report.get('trace_path', '')}`",
            "",
            "## Trace Event Counts",
            "",
        ]
    )
    event_counts = report.get("trace_event_counts", {})
    if isinstance(event_counts, dict):
        for key, value in event_counts.items():
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Decoded Message Kinds", ""])
    decoded_kinds = report.get("decoded_message_kinds", [])
    if isinstance(decoded_kinds, list):
        for item in decoded_kinds:
            lines.append(f"- `{item}`")
    lines.extend(["", "## SHP Shadow Handoff", ""])
    shadow_cost = report.get("shp_shadow_cost", {})
    lines.append(f"- shadow_event_count: `{report.get('shp_shadow_event_count', 0)}`")
    if isinstance(shadow_cost, dict):
        for key, value in shadow_cost.items():
            lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines) + "\n"


def _missing_modules() -> list[str]:
    import importlib.util

    return [
        module_name
        for module_name in REQUIRED_MODULES
        if importlib.util.find_spec(module_name) is None
    ]


def _read_trace_events(path: Path) -> list[dict[str, object]]:
    if not path or not path.exists():
        return []
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict):
            events.append(event)
    return events


def _collect_decoded_messages(
    trace_events: list[dict[str, object]],
) -> list[dict[str, object]]:
    decoded: list[dict[str, object]] = []
    for event in trace_events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        messages = payload.get("decoded_messages", [])
        if not isinstance(messages, list):
            continue
        decoded.extend(message for message in messages if isinstance(message, dict))
    return decoded


def _has_shp_shadow_envelope(trace_events: list[dict[str, object]]) -> bool:
    for event in trace_events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        envelope = payload.get("shadow_envelope", {})
        if not isinstance(envelope, dict):
            continue
        header = envelope.get("header", {})
        if isinstance(header, dict) and header.get("protocol_version") == "shp.v1-lite":
            return True
    return False


def _has_shp_shadow_gate(trace_events: list[dict[str, object]]) -> bool:
    for event in trace_events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        gate = payload.get("communication_gate", {})
        if isinstance(gate, dict) and gate.get("status"):
            return True
    return False


def _summarize_shadow_cost(
    trace_events: list[dict[str, object]],
) -> dict[str, int]:
    native_tokens = 0
    shp_tokens = 0
    audit_tokens = 0
    token_delta = 0
    for event in trace_events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        native_tokens += int(payload.get("native_output_text_tokens", 0) or 0)
        shp_tokens += int(payload.get("shp_shadow_envelope_tokens", 0) or 0)
        audit_tokens += int(
            payload.get("shp_shadow_audit_envelope_tokens", 0) or 0
        )
        token_delta += int(payload.get("token_delta_native_minus_shp", 0) or 0)
    return {
        "native_output_text_tokens": native_tokens,
        "shp_shadow_envelope_tokens": shp_tokens,
        "shp_shadow_audit_envelope_tokens": audit_tokens,
        "token_delta_native_minus_shp": token_delta,
    }


if __name__ == "__main__":
    raise SystemExit(main())
