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
    _missing_modules,
    _read_trace_events,
    build_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a native AutoGen team smoke under AgentLite and validate "
            "per-receiver SHP broadcast replacement shadow plans."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--broadcast-mode",
        choices=("shadow-only", "dry-run-rewrite", "real-rewrite"),
        default="dry-run-rewrite",
        help=(
            "Driver broadcast mode for this smoke. v5.12j validates "
            "dry-run-rewrite; v5.12k validates guarded real-rewrite."
        ),
    )
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
        run_prefix = (
            "v5.12k-autogen-real-rewrite"
            if args.broadcast_mode == "real-rewrite"
            else "v5.12j-autogen-broadcast-dry-run"
        )
        output_dir = PROJECT_ROOT / "runs" / f"{run_prefix}-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_broadcast_shadow_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            args.python,
            str(PROJECT_ROOT / "examples" / "autogen_broadcast_shadow_smoke.py"),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_OUTPUT": str(app_output),
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": args.broadcast_mode,
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
    augment_broadcast_shadow_report(report)
    report_path = output_dir / "autogen_broadcast_shadow_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_broadcast_shadow_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def augment_broadcast_shadow_report(report: dict[str, Any]) -> None:
    trace_path = Path(str(report.get("trace_path", "")))
    trace_events = _read_trace_events(trace_path)
    broadcast_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_broadcast_replacement_shadow"
        and isinstance(event.get("payload"), dict)
    ]
    team_input_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_team_input_state"
        and isinstance(event.get("payload"), dict)
    ]
    agent_rewrite_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_agent_input_real_rewrite"
        and isinstance(event.get("payload"), dict)
    ]
    summary = summarize_broadcast_shadow_events(broadcast_events)
    agent_rewrite_summary = summarize_agent_real_rewrite_events(agent_rewrite_events)
    report["broadcast_replacement_shadow"] = summary
    report["agent_input_real_rewrite"] = agent_rewrite_summary
    checks = report.get("checks", {})
    if isinstance(checks, dict):
        mode = _report_broadcast_mode(report, summary)
        checks["team_input_state_recorded"] = len(team_input_events) > 0
        checks["broadcast_shadow_event_recorded"] = len(broadcast_events) > 0
        checks["broadcast_has_three_receivers"] = summary.get("max_receiver_count", 0) >= 3
        checks["broadcast_receivers_preserved"] = set(
            summary.get("receivers", [])
        ) >= {"planner", "writer", "reviewer"}
        checks["broadcast_prompt_view_recorded"] = (
            int(summary.get("prompt_view_tokens", 0) or 0) > 0
        )
        checks["broadcast_wire_tokens_recorded"] = (
            int(summary.get("shadow_wire_tokens", 0) or 0) > 0
        )
        checks["broadcast_wire_plus_prompt_beats_native"] = (
            int(summary.get("native_full_broadcast_tokens", 0) or 0)
            > int(summary.get("wire_plus_prompt_view_tokens", 0) or 0)
        )
        if mode == "real-rewrite":
            checks["team_real_rewrite_safely_falls_back"] = (
                "team_level_real_rewrite_not_enabled_for_guarded_agent_input"
                in set(summary.get("fallback_reasons", []) or [])
            )
            checks["agent_real_rewrite_event_recorded"] = (
                int(agent_rewrite_summary.get("event_count", 0) or 0) > 0
            )
            checks["agent_real_rewrite_applied"] = (
                int(agent_rewrite_summary.get("applied_count", 0) or 0) > 0
            )
            checks["agent_real_message_mutation_recorded"] = (
                int(agent_rewrite_summary.get("real_message_mutation_count", 0) or 0)
                > 0
            )
            checks["agent_real_rewrite_reduces_tokens"] = (
                int(agent_rewrite_summary.get("token_delta_native_minus_rewrite", 0) or 0)
                > 0
            )
        else:
            checks["broadcast_dry_run_diff_recorded"] = (
                int(summary.get("rewrite_candidate_count", 0) or 0) > 0
            )
            checks["broadcast_dry_run_safe"] = (
                int(summary.get("rewrite_safe_count", 0) or 0) > 0
            )
            checks["broadcast_real_mutation_disabled"] = not bool(
                summary.get("real_message_mutation_count", 0)
            )
            checks["broadcast_native_kept_in_dry_run"] = (
                int(summary.get("native_kept_count", 0) or 0) > 0
            )
        report["passed"] = all(bool(value) for value in checks.values())


def summarize_broadcast_shadow_events(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    receivers: set[str] = set()
    native_full_broadcast_tokens = 0
    shadow_wire_tokens = 0
    prompt_view_tokens = 0
    wire_plus_prompt_view_tokens = 0
    max_receiver_count = 0
    scopes: set[str] = set()
    receiver_rows: list[dict[str, Any]] = []
    modes: set[str] = set()
    applied_actions: set[str] = set()
    fallback_reasons: set[str] = set()
    rewrite_candidate_count = 0
    rewrite_safe_count = 0
    fallback_required_count = 0
    native_kept_count = 0
    real_message_mutation_count = 0
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        dry_run = payload.get("rewrite_dry_run", {})
        if isinstance(dry_run, dict):
            mode = str(dry_run.get("mode", ""))
            if mode:
                modes.add(mode)
            action = str(dry_run.get("applied_action", ""))
            if action:
                applied_actions.add(action)
            if dry_run.get("candidate_generated"):
                rewrite_candidate_count += 1
            if dry_run.get("rewrite_safe"):
                rewrite_safe_count += 1
            if dry_run.get("fallback_required"):
                fallback_required_count += 1
            if "keep_native_autogen_broadcast" in action:
                native_kept_count += 1
            if dry_run.get("real_message_mutation"):
                real_message_mutation_count += 1
            for reason in dry_run.get("fallback_reasons", []) or []:
                fallback_reasons.add(str(reason))
        scopes.add(str(payload.get("native_scope", "")))
        max_receiver_count = max(
            max_receiver_count,
            int(payload.get("receiver_count", 0) or 0),
        )
        native_full_broadcast_tokens += int(
            payload.get("native_full_broadcast_tokens", 0) or 0
        )
        shadow_wire_tokens += int(payload.get("shadow_wire_tokens", 0) or 0)
        prompt_view_tokens += int(payload.get("prompt_view_tokens", 0) or 0)
        wire_plus_prompt_view_tokens += int(
            payload.get("wire_plus_prompt_view_tokens", 0) or 0
        )
        for row in payload.get("receiver_plans", []) or []:
            if not isinstance(row, dict):
                continue
            receiver = str(row.get("receiver", ""))
            if receiver:
                receivers.add(receiver)
            receiver_rows.append(
                {
                    "scope": payload.get("native_scope", ""),
                    "receiver": receiver,
                    "shadow_wire_tokens": row.get("shadow_wire_tokens", 0),
                    "prompt_view_tokens": row.get("prompt_view_tokens", 0),
                    "message_kinds": row.get("message_kinds", []),
                    "schema_valid": row.get("schema_valid"),
                    "prompt_view_available": row.get("prompt_view_available"),
                }
            )
    delta = native_full_broadcast_tokens - wire_plus_prompt_view_tokens
    reduction_ratio = (
        round(delta / native_full_broadcast_tokens, 6)
        if native_full_broadcast_tokens > 0
        else 0.0
    )
    return {
        "event_count": len(events),
        "broadcast_modes": sorted(modes),
        "applied_actions": sorted(applied_actions),
        "rewrite_candidate_count": rewrite_candidate_count,
        "rewrite_safe_count": rewrite_safe_count,
        "fallback_required_count": fallback_required_count,
        "fallback_reasons": sorted(fallback_reasons),
        "native_kept_count": native_kept_count,
        "real_message_mutation_count": real_message_mutation_count,
        "native_scopes": sorted(scope for scope in scopes if scope),
        "receivers": sorted(receivers),
        "max_receiver_count": max_receiver_count,
        "receiver_plan_count": len(receiver_rows),
        "native_full_broadcast_tokens": native_full_broadcast_tokens,
        "shadow_wire_tokens": shadow_wire_tokens,
        "prompt_view_tokens": prompt_view_tokens,
        "wire_plus_prompt_view_tokens": wire_plus_prompt_view_tokens,
        "token_delta_native_broadcast_minus_shadow": delta,
        "token_reduction_ratio": reduction_ratio,
        "receiver_rows": receiver_rows,
    }


def summarize_agent_real_rewrite_events(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    agents: set[str] = set()
    fallback_reasons: set[str] = set()
    fallback_buckets: set[str] = set()
    fallback_bucket_counts: dict[str, int] = {}
    native_input_tokens = 0
    rewritten_input_tokens = 0
    token_delta = 0
    candidate_count = 0
    attempt_count = 0
    applied_count = 0
    fallback_required_count = 0
    fallback_count = 0
    real_message_mutation_count = 0
    rows: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        agent_id = str(payload.get("agent_id", ""))
        if agent_id:
            agents.add(agent_id)
        if payload.get("candidate_generated"):
            candidate_count += 1
        attempt_increment = int(payload.get("rewrite_attempt_count", 0) or 0)
        applied_increment = int(payload.get("rewrite_applied_count", 0) or 0)
        fallback_increment = int(payload.get("rewrite_fallback_count", 0) or 0)
        attempt_count += attempt_increment
        applied_count += applied_increment
        fallback_count += fallback_increment
        if applied_increment == 0 and payload.get("rewrite_applied"):
            applied_count += 1
        if payload.get("fallback_required"):
            fallback_required_count += 1
            if fallback_increment == 0:
                fallback_count += 1
        if payload.get("real_message_mutation"):
            real_message_mutation_count += 1
        native_tokens = int(payload.get("native_input_tokens", 0) or 0)
        rewritten_tokens = int(payload.get("rewritten_input_tokens", 0) or 0)
        delta = int(payload.get("token_delta_native_minus_rewrite", 0) or 0)
        native_input_tokens += native_tokens
        rewritten_input_tokens += rewritten_tokens
        token_delta += delta
        for reason in payload.get("fallback_reasons", []) or []:
            fallback_reasons.add(str(reason))
        payload_buckets = payload.get("fallback_buckets", []) or []
        for bucket in payload_buckets:
            bucket_text = str(bucket)
            fallback_buckets.add(bucket_text)
            fallback_bucket_counts[bucket_text] = (
                fallback_bucket_counts.get(bucket_text, 0) + 1
            )
        rows.append(
            {
                "agent_id": agent_id,
                "rewrite_applied": bool(payload.get("rewrite_applied")),
                "fallback_required": bool(payload.get("fallback_required")),
                "native_input_tokens": native_tokens,
                "rewritten_input_tokens": rewritten_tokens,
                "token_delta_native_minus_rewrite": delta,
                "fallback_reasons": payload.get("fallback_reasons", []),
                "fallback_buckets": payload.get("fallback_buckets", []),
            }
        )
    return {
        "event_count": len(events),
        "agents": sorted(agents),
        "candidate_count": candidate_count,
        "attempt_count": attempt_count,
        "applied_count": applied_count,
        "fallback_required_count": fallback_required_count,
        "fallback_count": fallback_count,
        "fallback_reasons": sorted(fallback_reasons),
        "fallback_buckets": sorted(fallback_buckets),
        "fallback_bucket_counts": dict(sorted(fallback_bucket_counts.items())),
        "real_message_mutation_count": real_message_mutation_count,
        "native_input_tokens": native_input_tokens,
        "rewritten_input_tokens": rewritten_input_tokens,
        "token_delta_native_minus_rewrite": token_delta,
        "rows": rows,
    }


def _report_broadcast_mode(
    report: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    details = report.get("bootstrap_status", {}).get("driver_details", {})
    if isinstance(details, dict):
        mode = str(details.get("broadcast_mode", "") or "")
        if mode:
            return mode
    modes = summary.get("broadcast_modes", [])
    if isinstance(modes, list) and modes:
        return str(modes[0])
    return ""


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Broadcast Shadow Report",
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
    lines.extend(["", "## Broadcast Replacement Shadow", ""])
    summary = report.get("broadcast_replacement_shadow", {})
    if isinstance(summary, dict):
        for key, value in summary.items():
            if key == "receiver_rows":
                continue
            lines.append(f"- `{key}`: `{value}`")
        rows = summary.get("receiver_rows", [])
        if isinstance(rows, list) and rows:
            lines.extend(["", "### Receiver Rows", ""])
            for row in rows:
                lines.append(
                    "- "
                    f"`{row.get('scope', '')}` -> `{row.get('receiver', '')}`: "
                    f"wire={row.get('shadow_wire_tokens', 0)}, "
                    f"prompt_view={row.get('prompt_view_tokens', 0)}, "
                    f"kinds={row.get('message_kinds', [])}"
                )
    lines.extend(["", "## Agent Input Real Rewrite", ""])
    agent_rewrite = report.get("agent_input_real_rewrite", {})
    if isinstance(agent_rewrite, dict):
        for key, value in agent_rewrite.items():
            if key == "rows":
                continue
            lines.append(f"- `{key}`: `{value}`")
        rows = agent_rewrite.get("rows", [])
        if isinstance(rows, list) and rows:
            lines.extend(["", "### Rewrite Rows", ""])
            for row in rows:
                lines.append(
                    "- "
                    f"`{row.get('agent_id', '')}`: "
                    f"applied={row.get('rewrite_applied')}, "
                    f"fallback={row.get('fallback_required')}, "
                    f"native={row.get('native_input_tokens', 0)}, "
                    f"rewritten={row.get('rewritten_input_tokens', 0)}"
                )
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
