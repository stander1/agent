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
    summarize_broadcast_shadow_events,
)
from run_autogen_native_smoke import _missing_modules, _read_trace_events  # noqa: E402

EXPECTED_PHASE = "v5.13h"
EXPECTED_RECEIVERS = {"planner", "writer", "reviewer"}
USER_SCRIPT = PROJECT_ROOT / "examples" / "autogen_team_rewrite_smoke.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run an AutoGen RoundRobinGroupChat task through AgentLite and "
            "verify Team-entry task rewrite before native distribution."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-team-rewrite-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_team_rewrite(output_dir=output_dir, python=args.python)
    report_path = output_dir / "autogen_team_rewrite_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_team_rewrite_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_team_rewrite(*, output_dir: Path, python: str) -> dict[str, Any]:
    app_output = output_dir / "autogen_team_rewrite_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[python, str(USER_SCRIPT)],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE_OUTPUT": str(app_output),
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
    team_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_team_input_real_rewrite"
        and isinstance(event.get("payload"), dict)
    ]
    broadcast_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_broadcast_replacement_shadow"
        and isinstance(event.get("payload"), dict)
    ]
    team_summary = summarize_team_rewrite_events(team_events)
    broadcast_summary = summarize_broadcast_shadow_events(broadcast_events)
    first_stream_item = _first_stream_item(app_payload)
    source_text = USER_SCRIPT.read_text(encoding="utf-8")
    checks = build_checks(
        returncode=result.returncode,
        status=status,
        details=details,
        app_payload=app_payload,
        first_stream_item=first_stream_item,
        team_summary=team_summary,
        broadcast_summary=broadcast_summary,
        source_text=source_text,
    )
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "returncode": result.returncode,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase": details.get("phase", ""),
        "broadcast_mode": details.get("broadcast_mode", ""),
        "team_rewrite_enabled": bool(details.get("team_rewrite_enabled")),
        "trace_path": str(trace_path) if trace_path else "",
        "app_output_path": str(app_output),
        "trace_event_counts": dict(sorted(event_counts.items())),
        "checks": checks,
        "first_stream_item": first_stream_item,
        "team_input_real_rewrite": team_summary,
        "broadcast_replacement_shadow": broadcast_summary,
        "app_payload": app_payload,
    }


def summarize_team_rewrite_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    participants: set[str] = set()
    fallback_reasons: set[str] = set()
    fallback_buckets: set[str] = set()
    native_task_tokens = 0
    rewritten_task_tokens = 0
    native_full_broadcast_tokens = 0
    wire_plus_prompt_view_tokens = 0
    token_delta_task = 0
    token_delta_broadcast = 0
    applied_count = 0
    fallback_count = 0
    mutation_count = 0
    receiver_count = 0
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        participants.update(str(item) for item in payload.get("team_participants", []))
        fallback_reasons.update(
            str(item) for item in payload.get("fallback_reasons", []) or []
        )
        fallback_buckets.update(
            str(item) for item in payload.get("fallback_buckets", []) or []
        )
        applied_count += int(payload.get("rewrite_applied_count", 0) or 0)
        fallback_count += int(payload.get("rewrite_fallback_count", 0) or 0)
        if payload.get("real_message_mutation"):
            mutation_count += 1
        receiver_count = max(receiver_count, int(payload.get("receiver_count", 0) or 0))
        native_task_tokens += int(payload.get("native_task_tokens", 0) or 0)
        rewritten_task_tokens += int(payload.get("rewritten_task_tokens", 0) or 0)
        native_full_broadcast_tokens += int(
            payload.get("native_full_broadcast_tokens", 0) or 0
        )
        wire_plus_prompt_view_tokens += int(
            payload.get("wire_plus_prompt_view_tokens", 0) or 0
        )
        token_delta_task += int(
            payload.get("token_delta_native_task_minus_rewrite", 0) or 0
        )
        token_delta_broadcast += int(
            payload.get("token_delta_native_broadcast_minus_rewrite", 0) or 0
        )
    return {
        "event_count": len(events),
        "participants": sorted(participants),
        "receiver_count": receiver_count,
        "applied_count": applied_count,
        "fallback_count": fallback_count,
        "real_message_mutation_count": mutation_count,
        "fallback_reasons": sorted(fallback_reasons),
        "fallback_buckets": sorted(fallback_buckets),
        "native_task_tokens": native_task_tokens,
        "rewritten_task_tokens": rewritten_task_tokens,
        "token_delta_native_task_minus_rewrite": token_delta_task,
        "native_full_broadcast_tokens": native_full_broadcast_tokens,
        "wire_plus_prompt_view_tokens": wire_plus_prompt_view_tokens,
        "token_delta_native_broadcast_minus_rewrite": token_delta_broadcast,
    }


def build_checks(
    *,
    returncode: int,
    status: dict[str, Any],
    details: dict[str, Any],
    app_payload: dict[str, Any],
    first_stream_item: dict[str, Any],
    team_summary: dict[str, Any],
    broadcast_summary: dict[str, Any],
    source_text: str,
) -> dict[str, bool]:
    return {
        "target_returncode_zero": returncode == 0,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase_v5_13h": details.get("phase") == EXPECTED_PHASE,
        "real_rewrite_mode_enabled": details.get("broadcast_mode") == "real-rewrite",
        "team_rewrite_enabled": bool(details.get("team_rewrite_enabled")),
        "user_script_does_not_import_agentlite": (
            "from agent_runtime" not in source_text
            and "import agent_runtime" not in source_text
            and "agent_runtime." not in source_text
        ),
        "app_output_written": bool(app_payload),
        "agentlite_active_in_user_process": bool(app_payload.get("agentlite_active")),
        "stream_task_rewritten": (
            bool(first_stream_item.get("contains_team_rewrite_marker"))
            and bool(first_stream_item.get("contains_state_pool_marker"))
            and bool(first_stream_item.get("contains_broadcast_manifest"))
            and bool(first_stream_item.get("contains_receiver_prompt_views"))
        ),
        "stream_native_task_removed": not bool(
            first_stream_item.get("contains_native_marker")
        )
        and int(first_stream_item.get("native_marker_count", 0) or 0) == 0,
        "team_rewrite_event_recorded": int(team_summary.get("event_count", 0) or 0)
        >= 1,
        "team_rewrite_applied": int(team_summary.get("applied_count", 0) or 0) >= 1,
        "team_rewrite_no_fallback": int(team_summary.get("fallback_count", 0) or 0)
        == 0,
        "team_real_message_mutation_recorded": int(
            team_summary.get("real_message_mutation_count", 0) or 0
        )
        >= 1,
        "team_receivers_preserved": set(team_summary.get("participants", []))
        >= EXPECTED_RECEIVERS,
        "team_task_tokens_reduced": int(
            team_summary.get("token_delta_native_task_minus_rewrite", 0) or 0
        )
        > 0,
        "team_broadcast_tokens_reduced": int(
            team_summary.get("token_delta_native_broadcast_minus_rewrite", 0) or 0
        )
        > 0,
        "broadcast_shadow_event_recorded": int(
            broadcast_summary.get("event_count", 0) or 0
        )
        > 0,
        "broadcast_shadow_marks_real_mutation": int(
            broadcast_summary.get("real_message_mutation_count", 0) or 0
        )
        > 0,
    }


def _first_stream_item(app_payload: dict[str, Any]) -> dict[str, Any]:
    items = app_payload.get("stream_items", []) if isinstance(app_payload, dict) else []
    first = items[0] if isinstance(items, list) and items else {}
    return first if isinstance(first, dict) else {}


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Team Rewrite Report",
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
    team = report.get("team_input_real_rewrite", {})
    lines.extend(["", "## Team Rewrite", ""])
    if isinstance(team, dict):
        for key in (
            "event_count",
            "participants",
            "applied_count",
            "fallback_count",
            "real_message_mutation_count",
            "native_task_tokens",
            "rewritten_task_tokens",
            "token_delta_native_task_minus_rewrite",
            "native_full_broadcast_tokens",
            "wire_plus_prompt_view_tokens",
            "token_delta_native_broadcast_minus_rewrite",
        ):
            lines.append(f"- `{key}`: `{team.get(key, 0)}`")
    first = report.get("first_stream_item", {})
    lines.extend(["", "## First Stream Item", ""])
    if isinstance(first, dict):
        for key in (
            "type",
            "source",
            "content_chars",
            "contains_team_rewrite_marker",
            "contains_native_marker",
            "native_marker_count",
            "contains_broadcast_manifest",
            "contains_receiver_prompt_views",
        ):
            lines.append(f"- `{key}`: `{first.get(key, '')}`")
    lines.extend(["", "## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    lines.append(f"- trace_path: `{report.get('trace_path', '')}`")
    lines.append(f"- app_output_path: `{report.get('app_output_path', '')}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
