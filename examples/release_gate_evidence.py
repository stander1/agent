from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def collect_team_takeover_evidence(data_dir: Path) -> dict[str, Any]:
    sessions_dir = data_dir / "sessions"
    sessions = (
        [path for path in sessions_dir.iterdir() if path.is_dir()]
        if sessions_dir.is_dir()
        else []
    )
    if not sessions:
        return {"available": False, "reason": "session_missing"}
    session_dir = max(sessions, key=lambda path: path.stat().st_mtime_ns)
    status_path = session_dir / "bootstrap_status.json"
    status = _load_json(status_path)
    details = status.get("driver_details", {})
    if not isinstance(details, dict):
        details = {}
    trace_path_text = str(details.get("trace_path", ""))
    trace_path = Path(trace_path_text) if trace_path_text else Path()
    events = _read_jsonl(trace_path) if trace_path_text else []
    counts = Counter(str(event.get("event_type", "")) for event in events)
    team_payloads = _payloads(events, "autogen_team_input_real_rewrite")
    display_payloads = _payloads(events, "autogen_team_display_restored")
    fallback_reasons = sorted(
        {
            reason
            for payload in team_payloads
            for reason in _team_fallback_reasons(payload)
        }
    )
    return {
        "available": bool(status) and bool(events),
        "session_id": session_dir.name,
        "status_path": str(status_path),
        "trace_path": trace_path_text,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase": str(details.get("phase", "")),
        "trace_event_counts": dict(sorted(counts.items())),
        "team_event_count": len(team_payloads),
        "team_applied_count": sum(
            int(payload.get("rewrite_applied_count", 0) or 0)
            for payload in team_payloads
        ),
        "team_fallback_count": sum(
            int(payload.get("rewrite_fallback_count", 0) or 0)
            for payload in team_payloads
        ),
        "team_fallback_reasons": fallback_reasons,
        "real_message_mutation_count": sum(
            bool(payload.get("real_message_mutation")) for payload in team_payloads
        ),
        "native_task_tokens": sum(
            int(payload.get("native_task_tokens", 0) or 0)
            for payload in team_payloads
        ),
        "rewritten_task_tokens": sum(
            int(payload.get("rewritten_task_tokens", 0) or 0)
            for payload in team_payloads
        ),
        "task_token_savings": sum(
            int(payload.get("token_delta_native_task_minus_rewrite", 0) or 0)
            for payload in team_payloads
        ),
        "native_broadcast_tokens": sum(
            int(payload.get("native_full_broadcast_tokens", 0) or 0)
            for payload in team_payloads
        ),
        "wire_plus_prompt_view_tokens": sum(
            int(payload.get("wire_plus_prompt_view_tokens", 0) or 0)
            for payload in team_payloads
        ),
        "broadcast_token_savings": sum(
            int(
                payload.get(
                    "token_delta_native_broadcast_minus_rewrite", 0
                )
                or 0
            )
            for payload in team_payloads
        ),
        "display_restore_event_count": len(display_payloads),
        "display_restored_message_count": sum(
            int(payload.get("restored_message_count", 0) or 0)
            for payload in display_payloads
        ),
    }


def assess_team_takeover(
    *,
    evidence: dict[str, Any],
    app_payload: dict[str, Any],
    first_stream_item: dict[str, Any],
    expected_phase: str,
) -> dict[str, bool]:
    takeover_path = classify_team_takeover_path(evidence)
    internal_packet_hidden = (
        not bool(first_stream_item.get("contains_team_rewrite_marker"))
        and not bool(first_stream_item.get("contains_state_pool_marker"))
        and not bool(first_stream_item.get("contains_broadcast_manifest"))
        and not bool(first_stream_item.get("contains_receiver_prompt_views"))
    )
    if takeover_path == "applied_rewrite":
        caller_output_safe = (
            int(evidence.get("display_restore_event_count", 0) or 0) >= 1
            and int(first_stream_item.get("native_marker_count", 0) or 0) > 0
            and internal_packet_hidden
        )
    else:
        caller_output_safe = (
            takeover_path == "cost_guarded_fallback"
            and int(first_stream_item.get("native_marker_count", 0) or 0) > 0
            and internal_packet_hidden
        )
    return {
        "agentlite_active": bool(app_payload.get("agentlite_active")),
        "bootstrap_ok": bool(evidence.get("bootstrap_ok")),
        "hooks_active": bool(evidence.get("hooks_active")),
        "driver_phase_current": evidence.get("driver_phase") == expected_phase,
        "team_takeover_recorded": int(evidence.get("team_event_count", 0) or 0)
        >= 1,
        "team_rewrite_applied_or_cost_guarded": takeover_path
        in {"applied_rewrite", "cost_guarded_fallback"},
        "real_message_boundary_safe": (
            takeover_path == "applied_rewrite"
            and int(evidence.get("real_message_mutation_count", 0) or 0) >= 1
        )
        or (
            takeover_path == "cost_guarded_fallback"
            and int(evidence.get("real_message_mutation_count", 0) or 0) == 0
        ),
        "team_cost_boundary_safe": (
            takeover_path == "applied_rewrite"
            and int(evidence.get("task_token_savings", 0) or 0) > 0
            and int(evidence.get("broadcast_token_savings", 0) or 0) > 0
        )
        or takeover_path == "cost_guarded_fallback",
        "caller_output_safe": caller_output_safe,
    }


def classify_team_takeover_path(evidence: dict[str, Any]) -> str:
    applied_count = int(evidence.get("team_applied_count", 0) or 0)
    fallback_count = int(evidence.get("team_fallback_count", 0) or 0)
    mutation_count = int(evidence.get("real_message_mutation_count", 0) or 0)
    task_savings = int(evidence.get("task_token_savings", 0) or 0)
    broadcast_savings = int(evidence.get("broadcast_token_savings", 0) or 0)
    fallback_reasons = {
        str(reason)
        for reason in evidence.get("team_fallback_reasons", []) or []
        if str(reason)
    }
    if (
        applied_count >= 1
        and fallback_count == 0
        and mutation_count >= 1
        and task_savings > 0
        and broadcast_savings > 0
    ):
        return "applied_rewrite"
    if (
        applied_count == 0
        and fallback_count >= 1
        and mutation_count == 0
        and "token_not_reduced" in fallback_reasons
        and broadcast_savings <= 0
    ):
        return "cost_guarded_fallback"
    return "invalid"


def _team_fallback_reasons(payload: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    direct = payload.get("fallback_reasons", [])
    if isinstance(direct, list):
        reasons.extend(str(reason) for reason in direct if str(reason))
    dry_run = payload.get("rewrite_dry_run", {})
    if isinstance(dry_run, dict):
        nested = dry_run.get("fallback_reasons", [])
        if isinstance(nested, list):
            reasons.extend(str(reason) for reason in nested if str(reason))
    return reasons


def _payloads(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != event_type:
            continue
        payload = event.get("payload", {})
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
