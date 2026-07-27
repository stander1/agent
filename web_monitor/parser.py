from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_runtime.core.communication import CapabilityProfileManagerLite


AGENT_ORDER = ["planner", "retriever", "writer", "reviewer", "memory_manager"]
DEFAULT_MODES = ["baseline_text", "runtime_lite"]


def build_run_snapshot(
    run_dir: Path,
    *,
    run_id: str | None = None,
    status: str | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    run_id = run_id or run_dir.name
    trace_events = _read_jsonl(run_dir / "trace.jsonl")
    summary = _read_json(run_dir / "summary.json", default={})
    pool_snapshot = _read_json(run_dir / "pool_snapshot_latest.json", default={})
    agent_profiles = _agent_profiles(trace_events, pool_snapshot=pool_snapshot)
    if isinstance(summary, dict):
        summary = {**summary, "agent_profiles": agent_profiles}

    snapshot: dict[str, Any] = {
        "run_id": run_id,
        "status": status or _infer_status(run_dir),
        "output_dir": str(run_dir),
        "modes": {mode: _empty_mode_snapshot() for mode in DEFAULT_MODES},
        "summary": summary,
        "token_summary": _summary_token_summary(summary),
        "agent_profiles": agent_profiles,
        "errors": errors or [],
    }

    trace_states: list[dict[str, Any]] = []
    for event in trace_events:
        event_type = event.get("event_type", "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        mode = payload.get("mode")
        if mode not in snapshot["modes"]:
            if mode:
                snapshot["modes"][mode] = _empty_mode_snapshot()
            else:
                continue
        mode_view = snapshot["modes"][mode]
        mode_view["timeline"].append(_timeline_item(event))

        if event_type == "task_started":
            mode_view["task"] = {
                "task_id": payload.get("task_id"),
                "round_id": payload.get("round_id"),
                "title": payload.get("title"),
            }
        elif event_type == "agent_invoked":
            agent = _agent(mode_view, payload.get("agent_id"))
            agent["status"] = "running"
            agent["prompt_chars"] = payload.get("prompt_chars", 0)
            agent["invoked_at"] = event.get("ts")
        elif event_type == "contract_guard_checked":
            agent = _agent(mode_view, payload.get("agent_id"))
            agent["contract_guard"] = {
                "contract_status": payload.get("contract_status"),
                "schema_valid": payload.get("schema_valid"),
                "repair_actions": payload.get("repair_actions", []),
                "schema_errors": payload.get("schema_errors", []),
            }
        elif event_type == "contract_guard_retry":
            agent = _agent(mode_view, payload.get("agent_id"))
            agent.setdefault("retries", []).append(
                {
                    "retry_schema_valid": payload.get("retry_schema_valid"),
                    "retry_status": payload.get("retry_status"),
                    "ts": event.get("ts"),
                }
            )
        elif event_type == "final_quality_retry":
            agent = _agent(mode_view, payload.get("agent_id"))
            agent.setdefault("retries", []).append(
                {
                    "type": "final_quality_retry",
                    "missing_fields": payload.get("missing_fields", []),
                    "retry_output_chars": payload.get("retry_output_chars", 0),
                    "ts": event.get("ts"),
                }
            )
        elif event_type == "state_written":
            state = payload.get("state")
            if isinstance(state, dict):
                trace_states.append(state)
        elif event_type == "message_sent":
            _apply_message(mode_view, payload, event.get("ts"))
        elif event_type == "agent_output_received":
            agent = _agent(mode_view, payload.get("agent_id"))
            agent["status"] = "done"
            agent["output_chars"] = payload.get("content_chars", 0)
            agent["state_refs"] = payload.get("state_refs", [])
            agent["memory_refs"] = payload.get("memory_refs", [])
        elif event_type == "task_finished":
            mode_view["finished"] = {
                "success": payload.get("success"),
                "latency_ms": payload.get("latency_ms"),
                "ts": event.get("ts"),
            }

    state_pool = _state_pool_from_snapshot(pool_snapshot) or trace_states
    memory_snapshot = pool_snapshot.get("memory_store", {}) if isinstance(pool_snapshot, dict) else {}
    memory_graph = _memory_graph(memory_snapshot, state_pool)
    has_memory_nodes = any(
        node.get("type") != "StateObject" for node in memory_graph["nodes"]
    )
    if not has_memory_nodes:
        memory_graph = _fallback_memory_graph(snapshot)

    runtime_profile_rows = (
        pool_snapshot.get("capability_profiles", {})
        if isinstance(pool_snapshot, dict)
        else {}
    )
    if isinstance(runtime_profile_rows, dict):
        runtime_mode = snapshot["modes"].setdefault(
            "runtime_lite", _empty_mode_snapshot()
        )
        for agent_id, profile in runtime_profile_rows.items():
            if not isinstance(profile, dict):
                continue
            agent = _agent(runtime_mode, str(agent_id))
            agent["capability_profile"] = profile

    for mode, mode_view in snapshot["modes"].items():
        _finalize_agents(mode_view)
        if mode == "runtime_lite":
            mode_view["state_pool"] = state_pool
            mode_view["memory_graph"] = memory_graph
        else:
            mode_view["state_pool"] = []
            mode_view["memory_graph"] = {"nodes": [], "edges": [], "fallback": False}
    return snapshot


def list_runs(runs_dir: Path) -> list[dict[str, Any]]:
    if not runs_dir.exists():
        return []
    runs = []
    for path in runs_dir.iterdir():
        if not path.is_dir():
            continue
        trace_path = path / "trace.jsonl"
        summary_path = path / "summary.json"
        if not trace_path.exists() and not summary_path.exists():
            continue
        summary = _read_json(summary_path, default={})
        runs.append(
            {
                "run_id": path.name,
                "path": str(path),
                "has_trace": trace_path.exists(),
                "has_summary": summary_path.exists(),
                "has_pool_snapshot": (path / "pool_snapshot_latest.json").exists(),
                "status": _infer_status(path),
                "token_summary": _summary_token_summary(summary),
                "updated_at": path.stat().st_mtime,
            }
        )
    runs.sort(key=lambda item: item["updated_at"], reverse=True)
    return runs


def list_sessions(data_dir: Path) -> list[dict[str, Any]]:
    sessions_dir = data_dir / "sessions"
    if not sessions_dir.exists():
        return []
    sessions = []
    for path in sessions_dir.iterdir():
        if not path.is_dir():
            continue
        status_path = path / "bootstrap_status.json"
        launch_path = path / "launch.json"
        trace_path = path / "autogen_driver" / "trace.jsonl"
        if not status_path.exists() and not trace_path.exists():
            continue
        status = _read_json(status_path, default={})
        launch = _read_json(launch_path, default={})
        trace_events = _read_jsonl(trace_path)
        updated_at = max(
            item.stat().st_mtime
            for item in (path, status_path, launch_path, trace_path)
            if item.exists()
        )
        session_id = str(status.get("session_id") or launch.get("session_id") or path.name)
        sessions.append(
            {
                "session_id": session_id,
                "path": str(path),
                "framework": status.get("framework") or launch.get("framework") or "",
                "driver": status.get("driver") or "",
                "driver_status": status.get("driver_status") or "",
                "hooks_active": bool(status.get("hooks_active")),
                "has_trace": trace_path.exists(),
                "has_status": status_path.exists(),
                "token_summary": _autogen_token_summary(trace_events),
                "updated_at": updated_at,
            }
        )
    sessions.sort(key=lambda item: item["updated_at"], reverse=True)
    return sessions


def list_framework_runs(data_dir: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for session in list_sessions(data_dir):
        session_id = str(session.get("session_id") or "")
        session_path = Path(str(session.get("path") or ""))
        trace_path = session_path / "autogen_driver" / "trace.jsonl"
        events = _read_jsonl(trace_path)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            framework_run_id = _event_framework_run_id(event)
            if framework_run_id:
                grouped.setdefault(framework_run_id, []).append(event)
        for framework_run_id, run_events in grouped.items():
            details = _framework_run_details(run_events)
            updated_at = _iso_timestamp(
                details.get("finished_at") or details.get("started_at")
            )
            if updated_at <= 0 and trace_path.exists():
                updated_at = trace_path.stat().st_mtime
            runs.append(
                {
                    "run_id": framework_run_id,
                    "framework_run_id": framework_run_id,
                    "session_id": session_id,
                    "source": details.get("framework_run_source") or "",
                    "studio_run_id": details.get("studio_run_id") or "",
                    "studio_session_id": details.get("studio_session_id") or "",
                    "studio_team_id": details.get("studio_team_id") or "",
                    "studio_session_name": details.get("studio_session_name") or "",
                    "task_preview": details.get("task_preview") or "",
                    "status": details.get("status") or "running",
                    "event_count": len(run_events),
                    "token_summary": _autogen_token_summary(run_events),
                    "updated_at": updated_at,
                }
            )
    runs.sort(
        key=lambda item: (item["updated_at"], item["framework_run_id"]),
        reverse=True,
    )
    return runs


def build_session_snapshot(
    session_dir: Path,
    *,
    session_id: str | None = None,
    framework_run_id: str | None = None,
) -> dict[str, Any]:
    status = _read_json(session_dir / "bootstrap_status.json", default={})
    launch = _read_json(session_dir / "launch.json", default={})
    trace_path = session_dir / "autogen_driver" / "trace.jsonl"
    trace_events = _read_jsonl(trace_path)
    if framework_run_id:
        trace_events = [
            event
            for event in trace_events
            if _event_framework_run_id(event) == framework_run_id
        ]
        if not trace_events:
            raise FileNotFoundError(
                f"Framework run {framework_run_id!r} was not found in {trace_path}"
            )
    session_id = session_id or str(
        status.get("session_id") or launch.get("session_id") or session_dir.name
    )
    mode_view = _empty_mode_snapshot()
    run_details = _framework_run_details(trace_events)
    visible_run_id = framework_run_id or session_id
    mode_view["task"] = {
        "task_id": visible_run_id,
        "round_id": 1,
        "title": (
            run_details.get("task_preview")
            or f"AgentLite AutoGen session {session_id}"
        ),
    }
    state_pool: list[dict[str, Any]] = []
    event_counts: dict[str, int] = {}
    token_summary = _autogen_token_summary(trace_events)
    agent_profiles = _agent_profiles(trace_events)
    for event in trace_events:
        event_type = str(event.get("event_type", ""))
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        mode_view["timeline"].append(_timeline_item(event))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type in {"capability_profile_updated", "capability_profile_feedback"}:
            profile = payload.get("profile")
            agent_id = str(payload.get("agent_id") or "")
            if agent_id:
                agent = _agent(mode_view, agent_id)
                if isinstance(profile, dict):
                    agent["capability_profile"] = profile
            continue
        if event_type in {"state_written", "state_reused"}:
            state = payload.get("state")
            if isinstance(state, dict):
                state_id = str(state.get("state_id") or "")
                existing_index = next(
                    (
                        index
                        for index, item in enumerate(state_pool)
                        if str(item.get("state_id") or "") == state_id
                    ),
                    None,
                )
                if existing_index is None:
                    state_pool.append(state)
                else:
                    state_pool[existing_index] = {
                        **state_pool[existing_index],
                        **state,
                    }
            continue
        if event_type in {
            "autogen_agent_receive",
            "autogen_agent_output",
            "autogen_team_input_real_rewrite",
            "autogen_core_content_real_rewrite",
            "autogen_core_response_real_rewrite",
            "autogen_transport_input_state",
            "autogen_core_transport_shadow",
            "autogen_model_client_usage",
        }:
            _apply_autogen_event(mode_view, event)

    mode_view["state_pool"] = state_pool
    mode_view["memory_graph"] = _state_only_graph(state_pool)
    _finalize_agents(mode_view)
    return {
        "run_id": visible_run_id,
        "framework_run_id": framework_run_id or "",
        "framework_run_source": run_details.get("framework_run_source") or "",
        "studio_run_id": run_details.get("studio_run_id") or "",
        "studio_session_id": run_details.get("studio_session_id") or "",
        "studio_team_id": run_details.get("studio_team_id") or "",
        "studio_session_name": run_details.get("studio_session_name") or "",
        "session_id": session_id,
        "status": (
            run_details.get("status")
            if framework_run_id
            else _session_status(status, trace_events)
        ),
        "output_dir": str(session_dir),
        "modes": {
            "baseline_text": _empty_mode_snapshot(),
            "runtime_lite": mode_view,
        },
        "summary": {
            "kind": (
                "agentlite_autogen_framework_run"
                if framework_run_id
                else "agentlite_autogen_session"
            ),
            "framework": status.get("framework") or launch.get("framework") or "",
            "driver": status.get("driver") or "",
            "driver_status": status.get("driver_status") or "",
            "hooks_active": bool(status.get("hooks_active")),
            "event_counts": event_counts,
            "trace_path": str(trace_path) if trace_path.exists() else "",
            "token_summary": token_summary,
            "agent_profiles": agent_profiles,
            "by_mode": {
                "runtime_lite": token_summary,
                "baseline_text": {
                    "end_to_end_collaboration_tokens": token_summary.get(
                        "native_baseline_tokens", 0
                    ),
                    "direct_message_tokens": token_summary.get(
                        "native_baseline_tokens", 0
                    ),
                    "prompt_view_tokens": 0,
                    "retrieved_memory_tokens": 0,
                    "control_llm_tokens": 0,
                    "retry_tokens": 0,
                    "llm_prompt_tokens": 0,
                    "llm_completion_tokens": 0,
                    "llm_total_tokens": 0,
                    "llm_call_count": 0,
                },
            },
        },
        "agent_profiles": agent_profiles,
        "token_summary": token_summary,
        "errors": [status.get("error", "")] if status.get("error") else [],
        "bootstrap_status": status,
        "launch": launch,
    }


def _event_framework_run_id(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return str(payload.get("framework_run_id") or "").strip()


def _framework_run_details(events: list[dict[str, Any]]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for key in (
            "framework_run_id",
            "framework_run_source",
            "studio_run_id",
            "studio_session_id",
            "studio_team_id",
            "studio_session_name",
            "studio_database_path",
        ):
            if payload.get(key) and not details.get(key):
                details[key] = payload[key]
        event_type = str(event.get("event_type") or "")
        if event_type == "autogen_framework_run_started":
            details["status"] = "running"
            details["started_at"] = payload.get("started_at") or event.get("ts")
            details["task_preview"] = payload.get("task_preview") or ""
            details["team_participants"] = payload.get("team_participants") or []
        elif event_type == "autogen_framework_run_finished":
            details["status"] = payload.get("status") or "completed"
            details["finished_at"] = payload.get("finished_at") or event.get("ts")
            details["error_type"] = payload.get("error_type") or ""
            details["error"] = payload.get("error") or ""
    if not details.get("status"):
        details["status"] = "running"
    return details


def _iso_timestamp(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _summary_token_summary(summary: dict[str, Any]) -> dict[str, Any]:
    by_mode = summary.get("by_mode") if isinstance(summary, dict) else {}
    runtime = _mode_token_breakdown(
        by_mode.get("runtime_lite", {}) if isinstance(by_mode, dict) else {}
    )
    baseline = _mode_token_breakdown(
        by_mode.get("baseline_text", {}) if isinstance(by_mode, dict) else {}
    )
    native_baseline = baseline["end_to_end_collaboration_tokens"]
    runtime_total = runtime["end_to_end_collaboration_tokens"]
    savings = native_baseline - runtime_total if native_baseline else 0
    return {
        **runtime,
        "native_baseline_tokens": native_baseline,
        "runtime_tokens": runtime_total,
        "token_savings": savings,
        "token_savings_ratio": round(savings / native_baseline, 6)
        if native_baseline > 0
        else 0.0,
        "baseline": baseline,
        "runtime": runtime,
        "source": "summary_json",
    }


def _agent_profiles(
    trace_events: list[dict[str, Any]] | None = None,
    *,
    pool_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profiles = CapabilityProfileManagerLite([]).snapshot()
    profiles.update(
        {
            "team": {
                "agent_id": "team",
                "role": "AutoGenTeam",
                "registry_scope": "system",
                "summary": "AutoGen Team 入口，负责组级任务广播",
                "capabilities": ["Team 调度", "广播入口", "任务流转"],
                "accepted": ["user_task", "team_input"],
            },
            "team_manager": {
                "agent_id": "team_manager",
                "role": "AutoGenTeamManager",
                "registry_scope": "system",
                "summary": "AutoGen 组管理器，维护回合与参与者顺序",
                "capabilities": ["回合管理", "参与者编排"],
                "accepted": ["team_output", "handoff_message"],
            },
            "runtime_bridge": {
                "agent_id": "runtime_bridge",
                "role": "AutoGenRuntimeBridge",
                "registry_scope": "system",
                "summary": "AutoGen Core 运行时桥接层，承接底层消息投递",
                "capabilities": ["Core 桥接", "消息投递", "水合还原"],
                "accepted": ["core_request", "core_response"],
            },
            "autogen_driver": {
                "agent_id": "autogen_driver",
                "role": "AgentLiteDriver",
                "registry_scope": "system",
                "summary": "AgentLite 注入驱动，记录 hook、改写与 trace",
                "capabilities": ["运行时注入", "trace 记录", "协议改写"],
                "accepted": ["driver_event"],
            },
        }
    )
    snapshot_profiles = (
        pool_snapshot.get("capability_profiles", {})
        if isinstance(pool_snapshot, dict)
        else {}
    )
    if isinstance(snapshot_profiles, dict):
        profiles.update(
            {
                str(agent_id): _monitor_capability_profile(profile)
                for agent_id, profile in snapshot_profiles.items()
                if isinstance(profile, dict)
                and str(profile.get("registry_scope") or "business")
                == "business"
            }
        )
    for event in trace_events or []:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_type = str(event.get("event_type") or "")
        if event_type == "capability_profile_updated":
            profile = payload.get("profile")
        elif event_type == "autogen_agent_receive":
            profile = payload.get("capability_profile")
        else:
            continue
        agent_id = str(
            payload.get("agent_id")
            or (profile.get("agent_id") if isinstance(profile, dict) else "")
            or ""
        )
        if (
            agent_id and isinstance(profile, dict) and profile
            and str(profile.get("registry_scope") or "business") == "business"
        ):
            profiles[agent_id] = _monitor_capability_profile(profile)
    return profiles


def _monitor_capability_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(profile)
    capabilities = [str(item) for item in profile.get("capabilities", []) or []]
    accepted = list(
        dict.fromkeys(
            [
                *(str(item) for item in profile.get("accepted_state_types", []) or []),
                *(str(item) for item in profile.get("message_types", []) or []),
            ]
        )
    )
    normalized["capabilities"] = capabilities
    normalized["accepted"] = accepted
    normalized["summary"] = (
        f"画像 v{int(profile.get('profile_version', 0) or 0)}；"
        f"动作 {', '.join(profile.get('preferred_actions', []) or []) or '按任务推断'}；"
        f"工具 {', '.join(profile.get('available_tools', []) or []) or '无'}"
    )
    return normalized


def _mode_token_breakdown(row: dict[str, Any]) -> dict[str, int]:
    direct = _int(row.get("direct_text_tokens"))
    prompt_view = _int(row.get("prompt_view_tokens"))
    retrieved = _int(row.get("retrieved_memory_tokens"))
    control = _int(row.get("control_llm_tokens"))
    retry = _int(row.get("retry_tokens"))
    llm_prompt = _int(row.get("llm_prompt_tokens"))
    llm_completion = _int(row.get("llm_completion_tokens"))
    llm_total = _int(row.get("llm_total_tokens"))
    llm_call_count = _int(row.get("llm_call_count"))
    total = _int(row.get("end_to_end_collaboration_tokens"))
    if total <= 0:
        total = direct + prompt_view + retrieved + control + retry + llm_total
    if total <= 0:
        total = _int(row.get("prompt_tokens")) or llm_total
    return {
        "direct_message_tokens": direct,
        "prompt_view_tokens": prompt_view,
        "retrieved_memory_tokens": retrieved,
        "control_llm_tokens": control,
        "retry_tokens": retry,
        "llm_prompt_tokens": llm_prompt,
        "llm_completion_tokens": llm_completion,
        "llm_total_tokens": llm_total,
        "llm_call_count": llm_call_count,
        "end_to_end_collaboration_tokens": total,
    }


def _autogen_token_summary(trace_events: list[dict[str, Any]]) -> dict[str, Any]:
    breakdown = {
        "direct_message_tokens": 0,
        "prompt_view_tokens": 0,
        "retrieved_memory_tokens": 0,
        "control_llm_tokens": 0,
        "retry_tokens": 0,
        "llm_prompt_tokens": 0,
        "llm_completion_tokens": 0,
        "llm_total_tokens": 0,
        "llm_call_count": 0,
        "memory_query_count": 0,
        "memory_hit_count": 0,
        "memory_injected_count": 0,
        "useful_memory_hit_count": 0,
        "wrong_memory_hit_count": 0,
        "mixed_memory_hit_count": 0,
        "unassessed_memory_hit_count": 0,
        "memory_adoption_event_count": 0,
        "memory_adoption_guard_event_count": 0,
        "memory_adoption_rule_repair_count": 0,
        "memory_adoption_blocked_count": 0,
        "memory_adoption_enforcement_failure_count": 0,
        "memory_adoption_repaired_fact_count": 0,
        "memory_current_task_duplicate_fact_count": 0,
        "memory_attributed_fact_count": 0,
        "memory_supported_output_count": 0,
        "unique_retrieved_memory_tokens": 0,
        "fanout_retrieved_memory_tokens": 0,
        "end_to_end_collaboration_tokens": 0,
        "native_baseline_tokens": 0,
        "runtime_tokens": 0,
        "token_savings": 0,
        "token_savings_ratio": 0.0,
        "rewrite_audit_event_count": 0,
        "rewrite_costed_event_count": 0,
        "rewrite_applied_event_count": 0,
        "rewrite_fallback_event_count": 0,
        "rewrite_cost_gate_fallback_count": 0,
        "rewrite_contract_fallback_count": 0,
        "rewrite_ineligible_control_passthrough_count": 0,
        "rewrite_cost_guard_passthrough_count": 0,
        "rewrite_policy_guard_passthrough_count": 0,
        "rewrite_error_fallback_count": 0,
        "rewrite_eligible_event_count": 0,
        "rewrite_error_fallback_rate": 0.0,
        "actual_rewrite_event_count": 0,
        "continuity_required_event_count": 0,
        "continuity_cost_override_count": 0,
        "continuity_memory_injection_count": 0,
        "memory_source_view_tokens": 0,
        "minimal_role_view_tokens": 0,
        "memory_role_view_candidate_tokens": 0,
        "memory_no_expansion_fallback_count": 0,
        "role_view_saved_tokens": 0,
        "role_view_reduction_ratio": 0.0,
        "memory_field_fetch_count": 0,
        "memory_field_fetch_tokens": 0,
        "receiver_role_view_hydration_count": 0,
        "current_task_source_tokens": 0,
        "current_task_role_view_tokens": 0,
        "current_task_role_view_candidate_tokens": 0,
        "current_task_no_expansion_fallback_count": 0,
        "current_task_role_view_saved_tokens": 0,
        "current_task_role_view_reduction_ratio": 0.0,
        "current_task_fidelity_failure_count": 0,
        "current_task_identity_anchored_count": 0,
        "current_task_identity_guard_event_count": 0,
        "current_task_identity_guard_blocked_count": 0,
        "current_candidate_required_count": 0,
        "current_candidate_available_count": 0,
        "current_candidate_complete_count": 0,
        "current_candidate_missing_count": 0,
        "current_candidate_source_tokens": 0,
        "current_candidate_selected_tokens": 0,
        "final_delivery_assessed_count": 0,
        "final_delivery_valid_count": 0,
        "final_delivery_invalid_count": 0,
        "final_delivery_valid_rate": 0.0,
        "capability_profile_update_count": 0,
        "capability_profile_feedback_count": 0,
        "registered_capability_profile_count": 0,
        "registered_system_profile_count": 0,
        "registered_total_profile_count": 0,
        "capability_context_view_count": 0,
        "capability_action_counts": {},
        "memory_candidate_deduplicated_count": 0,
        "memory_candidate_deduplicated_tokens": 0,
        "memory_admission_deduplicated_claim_count": 0,
        "memory_admission_deduplicated_memory_count": 0,
        "memory_evidence_reference_merge_count": 0,
        "memory_epistemic_deferred_count": 0,
        "model_visible_protocol_marker_count": 0,
        "model_visible_input_protocol_marker_count": 0,
        "model_visible_agent_output_protocol_marker_count": 0,
        "model_visible_final_output_protocol_marker_count": 0,
        "state_memory_bridge_event_count": 0,
        "raw_claim_count": 0,
        "provisional_claim_count": 0,
        "slot_mapping_success_count": 0,
        "slot_mapping_success_rate": 0.0,
        "unresolved_scope_count": 0,
        "memory_conflict_detected_count": 0,
        "memory_conflict_resolved_count": 0,
        "memory_unresolved_conflict_count": 0,
        "active_memory_value_selection_count": 0,
        "review_governance_event_count": 0,
        "review_governance_authoritative_count": 0,
        "review_governance_blocking_count": 0,
        "review_governance_positive_count": 0,
        "review_governance_targeted_memory_count": 0,
        "review_governance_deprecated_memory_count": 0,
        "review_governance_blocker_admitted_count": 0,
        "review_governance_blocker_memory_count": 0,
        "review_governance_targeted_without_deprecation_count": 0,
        "review_governance_unexpected_deprecation_count": 0,
        "review_governance_blocking_without_admitted_blocker_count": 0,
        "review_governance_nonblocking_side_effect_count": 0,
        "review_governance_failure_event_count": 0,
        "review_governance_safe_event_count": 0,
        "shadow_native_tokens": 0,
        "shadow_candidate_tokens": 0,
        "shadow_potential_savings": 0,
        "shadow_potential_savings_ratio": 0.0,
        "shadow_event_count": 0,
        "event_count": 0,
        "source": "autogen_trace_fact_memory_v10",
    }
    actual_event_types = {
        "autogen_agent_input_real_rewrite",
        "autogen_team_input_real_rewrite",
        "autogen_core_content_real_rewrite",
        "autogen_core_response_real_rewrite",
    }
    shadow_event_types = {
        "autogen_shp_handoff_shadow",
        "autogen_core_transport_shadow",
        "autogen_broadcast_replacement_shadow",
    }
    seen_cost_events = 0
    rewrite_audit_events = 0
    rewrite_applied_events = 0
    rewrite_fallback_events = 0
    rewrite_cost_gate_fallbacks = 0
    rewrite_contract_fallbacks = 0
    rewrite_ineligible_control_passthroughs = 0
    rewrite_cost_guard_passthroughs = 0
    rewrite_policy_guard_passthroughs = 0
    rewrite_error_fallbacks = 0
    continuity_required_events = 0
    continuity_cost_overrides = 0
    continuity_memory_injections = 0
    memory_source_view_tokens = 0
    minimal_role_view_tokens = 0
    memory_role_view_candidate_tokens = 0
    memory_no_expansion_fallback_count = 0
    memory_field_fetch_count = 0
    memory_field_fetch_tokens = 0
    receiver_role_view_hydrations = 0
    current_task_source_tokens = 0
    current_task_role_view_tokens = 0
    current_task_role_view_candidate_tokens = 0
    current_task_no_expansion_fallback_count = 0
    current_task_fidelity_failure_count = 0
    final_delivery_assessed = 0
    final_delivery_valid = 0
    registered_business_profile_ids: set[str] = set()
    registered_system_profile_ids: set[str] = set()
    capability_action_counts: dict[str, int] = {}
    for event in trace_events:
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "capability_profile_updated":
            breakdown["capability_profile_update_count"] += 1
            agent_id = str(payload.get("agent_id") or "")
            profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
            registry_scope = str(
                profile.get("registry_scope")
                or payload.get("registry_scope")
                or "business"
            )
            if agent_id:
                if registry_scope == "system":
                    registered_system_profile_ids.add(agent_id)
                else:
                    registered_business_profile_ids.add(agent_id)
            continue
        if event_type == "capability_profile_feedback":
            breakdown["capability_profile_feedback_count"] += 1
            action = str(payload.get("action") or "HANDLE_TASK")
            capability_action_counts[action] = capability_action_counts.get(action, 0) + 1
            continue
        if event_type == "autogen_agent_receive":
            action = str(payload.get("semantic_action") or "HANDLE_TASK")
            capability_action_counts[action] = capability_action_counts.get(action, 0) + 1
        if event_type == "autogen_memory_retrieval":
            breakdown["memory_query_count"] += _int(
                payload.get("memory_query_count")
            )
            hit_count = _int(payload.get("memory_hit_count"))
            breakdown["memory_hit_count"] += hit_count
            if "memory_injected_count" in payload:
                breakdown["memory_injected_count"] += _int(
                    payload.get("memory_injected_count")
                )
                breakdown["useful_memory_hit_count"] += _int(
                    payload.get("useful_memory_hit_count")
                )
                breakdown["mixed_memory_hit_count"] += _int(
                    payload.get("mixed_memory_hit_count")
                )
                breakdown["unassessed_memory_hit_count"] += _int(
                    payload.get("unassessed_memory_hit_count")
                )
            else:
                # Older traces equated hits with useful hits. Preserve the
                # observed injection, but keep usefulness unassessed.
                breakdown["memory_injected_count"] += hit_count
                breakdown["unassessed_memory_hit_count"] += hit_count
            breakdown["wrong_memory_hit_count"] += _int(
                payload.get("wrong_memory_hit_count")
            )
            breakdown["unique_retrieved_memory_tokens"] += _int(
                payload.get("retrieved_memory_tokens")
            )
            continue
        if event_type == "autogen_memory_adoption":
            breakdown["memory_adoption_event_count"] += 1
            breakdown["useful_memory_hit_count"] += _int(
                payload.get("useful_memory_hit_count")
            )
            breakdown["wrong_memory_hit_count"] += _int(
                payload.get("wrong_memory_hit_count")
            )
            breakdown["mixed_memory_hit_count"] += _int(
                payload.get("mixed_memory_hit_count")
            )
            breakdown["memory_supported_output_count"] += _int(
                payload.get("memory_supported_output_count")
            )
            evidence_rows = payload.get("evidence")
            if isinstance(evidence_rows, list):
                for row in evidence_rows:
                    if not isinstance(row, dict):
                        continue
                    breakdown["memory_current_task_duplicate_fact_count"] += _int(
                        row.get("current_task_duplicate_fact_count")
                    )
                    breakdown["memory_attributed_fact_count"] += _int(
                        row.get("matched_fact_count")
                    )
            continue
        if event_type == "autogen_memory_adoption_guard":
            breakdown["memory_adoption_guard_event_count"] += 1
            status = str(payload.get("status") or "")
            if status == "rule_repaired":
                breakdown["memory_adoption_rule_repair_count"] += 1
            elif status == "blocked":
                breakdown["memory_adoption_blocked_count"] += 1
            elif status == "enforcement_failed":
                breakdown["memory_adoption_enforcement_failure_count"] += 1
            breakdown["memory_adoption_repaired_fact_count"] += _int(
                payload.get("repaired_fact_count")
            )
            continue
        if event_type == "autogen_memory_candidate":
            assessment = (
                payload.get("delivery_assessment")
                if isinstance(payload.get("delivery_assessment"), dict)
                else {}
            )
            if assessment:
                final_delivery_assessed += 1
                final_delivery_valid += int(bool(assessment.get("valid")))
            continue
        if event_type == "state_memory_bridge":
            breakdown["state_memory_bridge_event_count"] += 1
            breakdown["raw_claim_count"] += _int(
                payload.get("raw_claim_count")
            )
            breakdown["provisional_claim_count"] += _int(
                payload.get("provisional_claim_count")
            )
            breakdown["slot_mapping_success_count"] += _int(
                payload.get("slot_mapping_success_count")
            )
            breakdown["unresolved_scope_count"] += _int(
                payload.get("unresolved_scope_count")
            )
            breakdown["memory_conflict_detected_count"] += _int(
                payload.get("conflict_detected_count")
            )
            breakdown["memory_conflict_resolved_count"] += _int(
                payload.get("resolved_conflict_count")
            )
            breakdown["memory_unresolved_conflict_count"] += _int(
                payload.get("unresolved_conflict_count")
            )
            breakdown["active_memory_value_selection_count"] += _int(
                payload.get("active_value_selection_count")
            )
            breakdown["memory_admission_deduplicated_claim_count"] += _int(
                payload.get("deduplicated_claim_count")
            )
            breakdown["memory_admission_deduplicated_memory_count"] += _int(
                payload.get("deduplicated_memory_count")
            )
            breakdown["memory_evidence_reference_merge_count"] += _int(
                payload.get("evidence_reference_merge_count")
            )
            breakdown["memory_epistemic_deferred_count"] += _int(
                payload.get("epistemic_deferred_count")
            )
            continue
        if event_type == "autogen_review_conflict_governance":
            breakdown["review_governance_event_count"] += 1
            authoritative = bool(payload.get("authoritative"))
            blocking = bool(payload.get("blocking"))
            targeted = {
                str(value)
                for value in (payload.get("targeted_memory_ids") or [])
                if str(value)
            }
            deprecated = {
                str(value)
                for value in (payload.get("deprecated_memory_ids") or [])
                if str(value)
            }
            blocker_refs = [
                value
                for value in (payload.get("blocker_memory_refs") or [])
                if isinstance(value, dict)
            ]
            blocker_admitted = (
                str(payload.get("blocker_admission_status") or "")
                == "admitted"
            )
            targeted_without_deprecation = targeted - deprecated
            unexpected_deprecation = deprecated - targeted
            blocking_without_blocker = blocking and not blocker_admitted
            nonblocking_side_effect = not blocking and bool(
                deprecated
                or blocker_refs
                or payload.get("blocker_admission_status")
            )
            governance_failed = (
                not authoritative
                or bool(targeted_without_deprecation)
                or bool(unexpected_deprecation)
                or blocking_without_blocker
                or nonblocking_side_effect
            )

            breakdown["review_governance_authoritative_count"] += int(
                authoritative
            )
            breakdown["review_governance_blocking_count"] += int(blocking)
            breakdown["review_governance_positive_count"] += int(
                authoritative and not blocking
            )
            breakdown["review_governance_targeted_memory_count"] += len(
                targeted
            )
            breakdown["review_governance_deprecated_memory_count"] += len(
                deprecated
            )
            breakdown["review_governance_blocker_admitted_count"] += int(
                blocker_admitted
            )
            breakdown["review_governance_blocker_memory_count"] += len(
                blocker_refs
            )
            breakdown[
                "review_governance_targeted_without_deprecation_count"
            ] += len(targeted_without_deprecation)
            breakdown[
                "review_governance_unexpected_deprecation_count"
            ] += len(unexpected_deprecation)
            breakdown[
                "review_governance_blocking_without_admitted_blocker_count"
            ] += int(blocking_without_blocker)
            breakdown[
                "review_governance_nonblocking_side_effect_count"
            ] += int(nonblocking_side_effect)
            breakdown["review_governance_failure_event_count"] += int(
                governance_failed
            )
            breakdown["review_governance_safe_event_count"] += int(
                not governance_failed
            )
            continue
        if event_type == "autogen_model_client_usage":
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            prompt = _int(payload.get("llm_prompt_tokens")) or _int(
                usage.get("prompt_tokens")
            )
            completion = _int(payload.get("llm_completion_tokens")) or _int(
                usage.get("completion_tokens")
            )
            total = _int(payload.get("llm_total_tokens")) or _int(
                usage.get("total_tokens")
            )
            if total <= 0:
                total = prompt + completion
            breakdown["llm_prompt_tokens"] += prompt
            breakdown["llm_completion_tokens"] += completion
            breakdown["llm_total_tokens"] += total
            breakdown["llm_call_count"] += 1
            continue
        if event_type == "autogen_agent_output":
            marker_count = _int(
                payload.get("model_visible_protocol_marker_count")
            )
            breakdown["model_visible_protocol_marker_count"] += marker_count
            if str(payload.get("model_visible_surface") or "") == "final_output":
                breakdown[
                    "model_visible_final_output_protocol_marker_count"
                ] += marker_count
            else:
                breakdown[
                    "model_visible_agent_output_protocol_marker_count"
                ] += marker_count
            continue
        if event_type == "autogen_current_task_identity_guard":
            breakdown["current_task_identity_guard_event_count"] += 1
            if str(payload.get("status") or "") == "blocked_and_reanchored":
                breakdown["current_task_identity_guard_blocked_count"] += 1
            continue
        if event_type in shadow_event_types:
            native, runtime, _, _, _ = _autogen_event_cost(payload)
            if native > 0 or runtime > 0:
                breakdown["shadow_native_tokens"] += native
                breakdown["shadow_candidate_tokens"] += runtime
                breakdown["shadow_event_count"] += 1
            continue
        if event_type not in actual_event_types:
            continue
        rewrite_audit_events += 1
        continuity_required = bool(payload.get("continuity_context_required"))
        continuity_override = bool(payload.get("continuity_cost_override"))
        if continuity_required:
            continuity_required_events += 1
        if continuity_override:
            continuity_cost_overrides += 1
        applied = bool(payload.get("rewrite_applied")) or _int(
            payload.get("rewrite_applied_count")
        ) > 0
        if applied:
            rewrite_applied_events += 1
            if event_type == "autogen_agent_input_real_rewrite":
                input_marker_count = _int(
                    payload.get("model_visible_protocol_marker_count")
                )
                breakdown[
                    "model_visible_protocol_marker_count"
                ] += input_marker_count
                breakdown[
                    "model_visible_input_protocol_marker_count"
                ] += input_marker_count
            if (
                event_type == "autogen_agent_input_real_rewrite"
                and continuity_required
                and _int(payload.get("memory_injected_count")) > 0
            ):
                continuity_memory_injections += 1
        else:
            rewrite_fallback_events += 1
            fallback_buckets = {
                str(item)
                for item in (payload.get("fallback_buckets") or [])
                if str(item)
            }
            fallback_reasons = {
                str(item)
                for item in (payload.get("fallback_reasons") or [])
                if str(item)
            }
            classification = _rewrite_passthrough_classification(payload)
            if classification == "ineligible_control_passthrough":
                rewrite_ineligible_control_passthroughs += 1
            elif classification == "cost_guard_passthrough":
                rewrite_cost_guard_passthroughs += 1
            elif classification == "policy_guard_passthrough":
                rewrite_policy_guard_passthroughs += 1
            else:
                rewrite_error_fallbacks += 1
            if classification == "cost_guard_passthrough":
                rewrite_cost_gate_fallbacks += 1
            if classification == "error_fallback" and any(
                "contract" in bucket for bucket in fallback_buckets
            ):
                rewrite_contract_fallbacks += 1
        breakdown["memory_candidate_deduplicated_count"] += _int(
            payload.get("memory_candidate_deduplicated_fanout_count")
            or payload.get("memory_candidate_deduplicated_count")
        )
        breakdown["memory_candidate_deduplicated_tokens"] += _int(
            payload.get("memory_candidate_deduplicated_tokens")
        )
        memory_source_view_tokens += _int(
            payload.get("memory_source_view_tokens")
        )
        memory_role_view_candidate_tokens += _int(
            payload.get("memory_role_view_candidate_tokens")
        )
        memory_no_expansion_fallback_count += _int(
            payload.get("memory_no_expansion_fallback_count")
        )
        minimal_role_view_tokens += _int(
            payload.get("minimal_role_view_tokens")
        )
        memory_field_fetch_count += _int(payload.get("memory_field_fetch_count"))
        memory_field_fetch_tokens += _int(payload.get("memory_field_fetch_tokens"))
        current_task_source_tokens += _int(
            payload.get("current_task_source_tokens")
        )
        current_task_role_view_candidate_tokens += _int(
            payload.get("current_task_role_view_candidate_tokens")
        )
        current_task_no_expansion_fallback_count += _int(
            payload.get("current_task_no_expansion_fallback_count")
        )
        current_task_role_view_tokens += _int(
            payload.get("current_task_role_view_tokens")
        )
        current_task_fidelity_failure_count += _int(
            payload.get("current_task_fidelity_failure_count")
        )
        rewrite_safety = (
            payload.get("rewrite_safety")
            if isinstance(payload.get("rewrite_safety"), dict)
            else {}
        )
        candidate_required = bool(
            rewrite_safety.get("current_candidate_required")
        )
        candidate_available = bool(
            rewrite_safety.get("current_candidate_available")
        )
        candidate_complete = bool(
            rewrite_safety.get("current_candidate_complete")
        )
        if candidate_required:
            breakdown["current_candidate_required_count"] += 1
            breakdown["current_candidate_available_count"] += int(
                candidate_available
            )
            breakdown["current_candidate_missing_count"] += int(
                not candidate_available
            )
            breakdown["current_candidate_source_tokens"] += _int(
                rewrite_safety.get("current_candidate_source_tokens")
            )
        if applied and candidate_required and candidate_complete:
            breakdown["current_candidate_complete_count"] += 1
            breakdown["current_candidate_selected_tokens"] += _int(
                rewrite_safety.get("current_candidate_selected_tokens")
            )
        if applied and rewrite_safety.get("current_task_identity_anchored"):
            breakdown["current_task_identity_anchored_count"] += 1
        if applied and rewrite_safety.get("team_receiver_role_view_hydration"):
            receiver_role_view_hydrations += 1
        if (
            applied
            and "current_task_units_preserved" in rewrite_safety
            and not bool(rewrite_safety.get("current_task_units_preserved"))
        ):
            current_task_fidelity_failure_count += 1
        receiver_entries = payload.get("receiver_plans") or []
        if isinstance(receiver_entries, list):
            breakdown["capability_context_view_count"] += sum(
                1
                for entry in receiver_entries
                if isinstance(entry, dict)
                and entry.get("memory_view_mode")
                == "capability_action_context_view_v1"
            )
        native, runtime, direct, prompt_view, retrieved_memory = _autogen_event_cost(
            payload
        )
        if native <= 0 and runtime <= 0:
            continue
        seen_cost_events += 1
        if not applied:
            runtime = native
            direct = native
            prompt_view = 0
            retrieved_memory = 0
        else:
            injected_count = _int(payload.get("memory_injected_count"))
            if event_type == "autogen_agent_input_real_rewrite":
                breakdown["memory_injected_count"] += injected_count
                breakdown["unassessed_memory_hit_count"] += injected_count
        breakdown["native_baseline_tokens"] += native
        breakdown["direct_message_tokens"] += direct
        breakdown["prompt_view_tokens"] += prompt_view
        breakdown["retrieved_memory_tokens"] += retrieved_memory
        breakdown["fanout_retrieved_memory_tokens"] += retrieved_memory
        if runtime > 0:
            breakdown["end_to_end_collaboration_tokens"] += runtime
        else:
            breakdown["end_to_end_collaboration_tokens"] += direct + prompt_view
    runtime_total = breakdown["end_to_end_collaboration_tokens"]
    native_total = breakdown["native_baseline_tokens"]
    savings = native_total - runtime_total if native_total else 0
    breakdown["runtime_tokens"] = runtime_total
    breakdown["token_savings"] = savings
    breakdown["token_savings_ratio"] = (
        round(savings / native_total, 6) if native_total > 0 else 0.0
    )
    shadow_native = breakdown["shadow_native_tokens"]
    shadow_savings = shadow_native - breakdown["shadow_candidate_tokens"]
    breakdown["shadow_potential_savings"] = shadow_savings
    breakdown["shadow_potential_savings_ratio"] = (
        round(shadow_savings / shadow_native, 6) if shadow_native > 0 else 0.0
    )
    breakdown["rewrite_audit_event_count"] = rewrite_audit_events
    breakdown["rewrite_costed_event_count"] = seen_cost_events
    breakdown["rewrite_applied_event_count"] = rewrite_applied_events
    breakdown["rewrite_fallback_event_count"] = rewrite_fallback_events
    breakdown["rewrite_cost_gate_fallback_count"] = rewrite_cost_gate_fallbacks
    breakdown["rewrite_contract_fallback_count"] = rewrite_contract_fallbacks
    breakdown["rewrite_ineligible_control_passthrough_count"] = (
        rewrite_ineligible_control_passthroughs
    )
    breakdown["rewrite_cost_guard_passthrough_count"] = (
        rewrite_cost_guard_passthroughs
    )
    breakdown["rewrite_policy_guard_passthrough_count"] = (
        rewrite_policy_guard_passthroughs
    )
    breakdown["rewrite_error_fallback_count"] = rewrite_error_fallbacks
    rewrite_eligible_events = (
        rewrite_applied_events
        + rewrite_cost_guard_passthroughs
        + rewrite_error_fallbacks
    )
    breakdown["rewrite_eligible_event_count"] = rewrite_eligible_events
    breakdown["rewrite_error_fallback_rate"] = (
        round(rewrite_error_fallbacks / rewrite_eligible_events, 6)
        if rewrite_eligible_events
        else 0.0
    )
    breakdown["unassessed_memory_hit_count"] = max(
        0,
        breakdown["memory_injected_count"]
        - breakdown["useful_memory_hit_count"]
        - breakdown["wrong_memory_hit_count"]
        - breakdown["mixed_memory_hit_count"],
    )
    breakdown["actual_rewrite_event_count"] = rewrite_applied_events
    breakdown["continuity_required_event_count"] = continuity_required_events
    breakdown["continuity_cost_override_count"] = continuity_cost_overrides
    breakdown["continuity_memory_injection_count"] = continuity_memory_injections
    breakdown["memory_source_view_tokens"] = memory_source_view_tokens
    breakdown["minimal_role_view_tokens"] = minimal_role_view_tokens
    breakdown["memory_role_view_candidate_tokens"] = memory_role_view_candidate_tokens
    breakdown["memory_no_expansion_fallback_count"] = (
        memory_no_expansion_fallback_count
    )
    breakdown["role_view_saved_tokens"] = (
        memory_source_view_tokens - minimal_role_view_tokens
    )
    breakdown["role_view_reduction_ratio"] = (
        round(
            (memory_source_view_tokens - minimal_role_view_tokens)
            / memory_source_view_tokens,
            6,
        )
        if memory_source_view_tokens > 0
        else 0.0
    )
    breakdown["memory_field_fetch_count"] = memory_field_fetch_count
    breakdown["memory_field_fetch_tokens"] = memory_field_fetch_tokens
    breakdown["receiver_role_view_hydration_count"] = receiver_role_view_hydrations
    breakdown["current_task_source_tokens"] = current_task_source_tokens
    breakdown["current_task_role_view_tokens"] = current_task_role_view_tokens
    breakdown["current_task_role_view_candidate_tokens"] = (
        current_task_role_view_candidate_tokens
    )
    breakdown["current_task_no_expansion_fallback_count"] = (
        current_task_no_expansion_fallback_count
    )
    breakdown["current_task_role_view_saved_tokens"] = (
        current_task_source_tokens - current_task_role_view_tokens
    )
    breakdown["current_task_role_view_reduction_ratio"] = (
        round(
            (current_task_source_tokens - current_task_role_view_tokens)
            / current_task_source_tokens,
            6,
        )
        if current_task_source_tokens > 0
        else 0.0
    )
    breakdown["current_task_fidelity_failure_count"] = (
        current_task_fidelity_failure_count
    )
    breakdown["final_delivery_assessed_count"] = final_delivery_assessed
    breakdown["final_delivery_valid_count"] = final_delivery_valid
    breakdown["final_delivery_invalid_count"] = (
        final_delivery_assessed - final_delivery_valid
    )
    breakdown["final_delivery_valid_rate"] = (
        round(final_delivery_valid / final_delivery_assessed, 6)
        if final_delivery_assessed
        else 0.0
    )
    breakdown["registered_capability_profile_count"] = len(
        registered_business_profile_ids
    )
    breakdown["registered_system_profile_count"] = len(
        registered_system_profile_ids
    )
    breakdown["registered_total_profile_count"] = len(
        registered_business_profile_ids | registered_system_profile_ids
    )
    breakdown["capability_action_counts"] = dict(
        sorted(capability_action_counts.items())
    )
    breakdown["slot_mapping_success_rate"] = (
        round(
            breakdown["slot_mapping_success_count"]
            / breakdown["raw_claim_count"],
            6,
        )
        if breakdown["raw_claim_count"] > 0
        else 0.0
    )
    breakdown["minimal_context_view_tokens"] = breakdown[
        "minimal_role_view_tokens"
    ]
    breakdown["context_view_saved_tokens"] = breakdown["role_view_saved_tokens"]
    breakdown["context_view_reduction_ratio"] = breakdown[
        "role_view_reduction_ratio"
    ]
    breakdown["receiver_context_view_hydration_count"] = breakdown[
        "receiver_role_view_hydration_count"
    ]
    breakdown["current_task_context_view_tokens"] = breakdown[
        "current_task_role_view_tokens"
    ]
    breakdown["event_count"] = seen_cost_events
    return breakdown


def _rewrite_passthrough_classification(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("passthrough_classification") or "")
    if explicit:
        return explicit
    if bool(payload.get("rewrite_applied")) or _int(
        payload.get("rewrite_applied_count")
    ) > 0:
        return "rewrite_applied"
    reasons = {
        str(item)
        for item in (payload.get("fallback_reasons") or [])
        if str(item)
    }
    buckets = {
        str(item)
        for item in (payload.get("fallback_buckets") or [])
        if str(item)
    }
    native_tokens = max(
        _int(payload.get("native_content_tokens")),
        _int(payload.get("native_input_tokens")),
        _int(payload.get("native_task_tokens")),
        _int(payload.get("native_full_broadcast_tokens")),
    )
    if native_tokens <= 0 and reasons & {
        "empty_messages",
        "empty_text_payload",
        "empty_team_task_payload",
        "empty_core_message_payload",
        "empty_core_response_payload",
        "missing_core_message_argument",
        "missing_core_response_result",
        "unsupported_core_message_content_field",
        "unsupported_core_response_content_field",
    }:
        return "ineligible_control_passthrough"
    if "cost_gate_failed" in buckets or any(
        "token_not_reduced" in reason for reason in reasons
    ):
        return "cost_guard_passthrough"
    if any(
        marker in bucket
        for bucket in buckets
        for marker in (
            "env_guard",
            "dry_run_guard",
            "already_rewritten",
            "control_guard",
            "lineage_guard",
            "unsupported_message_type",
        )
    ):
        return "policy_guard_passthrough"
    return "error_fallback"


def _autogen_event_cost(payload: dict[str, Any]) -> tuple[int, int, int, int, int]:
    receiver_wire, receiver_prompt, receiver_memory = _receiver_plan_tokens(payload)
    if receiver_wire or receiver_prompt or receiver_memory:
        native = _int(payload.get("native_full_broadcast_tokens"))
        if native <= 0:
            native = _int(payload.get("native_tokens_per_receiver")) * _int(
                payload.get("receiver_count")
            )
        return (
            native,
            receiver_wire + receiver_prompt + receiver_memory,
            receiver_wire,
            receiver_prompt,
            receiver_memory,
        )

    native = _first_positive(
        payload,
        [
            "native_full_broadcast_tokens",
            "native_transport_tokens",
            "native_content_tokens",
            "native_input_tokens",
            "native_output_text_tokens",
            "native_task_tokens",
        ],
    )
    prompt_view = _int(payload.get("prompt_view_tokens"))
    retrieved_memory = _int(payload.get("retrieved_memory_tokens"))
    explicit_wire = _first_positive(
        payload,
        [
            "rewritten_wire_tokens",
            "shadow_wire_tokens",
            "shp_shadow_envelope_tokens",
        ],
    )
    runtime = _first_positive(
        payload,
        [
            "wire_plus_prompt_view_tokens",
            "rewritten_content_tokens",
            "rewritten_input_tokens",
            "rewritten_task_tokens",
            "candidate_input_tokens",
            "shp_shadow_envelope_tokens",
        ],
    )
    if runtime <= 0:
        runtime = explicit_wire + prompt_view + retrieved_memory
    direct = explicit_wire
    if direct <= 0:
        # Historical agent/core traces only recorded the complete rewritten
        # input. Prompt View and memory are contained in that value, so the
        # direct wire component must be the residual rather than the full input.
        direct = max(0, runtime - prompt_view - retrieved_memory)
    return native, runtime, direct, prompt_view, retrieved_memory


def _receiver_plan_tokens(payload: dict[str, Any]) -> tuple[int, int, int]:
    entries = payload.get("receiver_plans")
    if not isinstance(entries, list):
        return 0, 0, 0
    wire = 0
    prompt_view = 0
    retrieved_memory = 0
    for item in entries:
        if not isinstance(item, dict):
            continue
        wire += _int(item.get("shadow_wire_tokens"))
        prompt_view += _int(item.get("prompt_view_tokens"))
        retrieved_memory += _int(item.get("retrieved_memory_tokens"))
    return wire, prompt_view, retrieved_memory


def _first_positive(payload: dict[str, Any], keys: list[str]) -> int:
    for key in keys:
        value = _int(payload.get(key))
        if value > 0:
            return value
    return 0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _empty_mode_snapshot() -> dict[str, Any]:
    return {
        "task": {},
        "timeline": [],
        "agents": [],
        "messages": [],
        "state_pool": [],
        "memory_graph": {"nodes": [], "edges": [], "fallback": False},
    }


def _apply_autogen_event(mode_view: dict[str, Any], event: dict[str, Any]) -> None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    event_type = str(event.get("event_type", ""))
    agent_id = str(
        payload.get("agent_id")
        or payload.get("role")
        or payload.get("sender")
        or payload.get("method")
        or "autogen"
    )
    agent = _agent(mode_view, agent_id)
    agent["status"] = "done"
    if payload.get("input_chars") is not None:
        agent["prompt_chars"] = payload.get("input_chars", 0)
    summary = _autogen_event_summary(event)
    if event_type.endswith("_receive"):
        agent["received_summary"] = summary
    else:
        agent["output_summary"] = summary
    if payload.get("state_refs"):
        agent["state_refs"] = payload.get("state_refs", [])
    if payload.get("memory_refs"):
        agent["memory_refs"] = payload.get("memory_refs", [])
    mode_view["messages"].append(
        {
            "ts": event.get("ts"),
            "sender": payload.get("sender") or payload.get("role") or "autogen",
            "receiver": payload.get("declared_receiver")
            or payload.get("receiver")
            or agent_id,
            "handoff_to": payload.get("declared_receiver") or "",
            "content": summary,
            "summary": summary,
            "state_refs": payload.get("state_refs", []),
            "memory_refs": payload.get("memory_refs", []),
            "event_type": event_type,
            "payload": payload,
        }
    )


def _autogen_event_summary(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    event_type = str(event.get("event_type", ""))
    if event_type == "autogen_agent_receive":
        return (
            f"{payload.get('agent_id')} received via {payload.get('method')}; "
            f"input_chars={payload.get('input_chars', 0)}"
        )
    if event_type == "autogen_agent_output":
        return (
            f"{payload.get('agent_id')} output; "
            f"output_chars={payload.get('output_chars', payload.get('content_chars', 0))}"
        )
    if event_type == "autogen_model_client_usage":
        return (
            f"{payload.get('model') or payload.get('client_class')} LLM usage; "
            f"prompt={payload.get('llm_prompt_tokens', 0)}, "
            f"completion={payload.get('llm_completion_tokens', 0)}"
        )
    if event_type == "autogen_team_input_real_rewrite":
        return (
            "Team task rewritten; "
            f"applied={payload.get('rewrite_applied_count', 0)}, "
            f"fallback={payload.get('rewrite_fallback_count', 0)}, "
            f"delta={payload.get('token_delta_native_broadcast_minus_rewrite', 0)}"
        )
    if event_type in {
        "autogen_core_content_real_rewrite",
        "autogen_core_response_real_rewrite",
    }:
        return (
            f"{payload.get('method')} rewrite; "
            f"applied={payload.get('rewrite_applied_count', 0)}, "
            f"fallback={payload.get('rewrite_fallback_count', 0)}, "
            f"delta={payload.get('token_delta_native_minus_rewrite', 0)}"
        )
    if event_type == "autogen_transport_input_state":
        return (
            f"{payload.get('agent_id')} transported "
            f"{', '.join(payload.get('message_kinds', []) or [])}; "
            f"state_refs={len(payload.get('state_refs', []) or [])}"
        )
    if event_type == "autogen_core_transport_shadow":
        return (
            f"Core shadow {payload.get('method')} -> "
            f"{payload.get('declared_receiver', '')}; "
            f"delta={payload.get('token_delta_native_minus_wire_plus_prompt_view', 0)}"
        )
    return _event_summary(event)


def _state_only_graph(state_pool: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = []
    for state in state_pool:
        state_id = state.get("state_id")
        if not state_id:
            continue
        nodes.append(
            {
                "id": state_id,
                "type": "StateObject",
                "label": state_id,
                "summary": state.get("summary", ""),
                "status": state.get("lifecycle", ""),
            }
        )
    return {"nodes": nodes, "edges": [], "fallback": False}


def _session_status(status: dict[str, Any], trace_events: list[dict[str, Any]]) -> str:
    if status.get("error"):
        return "failed"
    if status.get("ok") is False:
        return "failed"
    if trace_events:
        return "succeeded"
    if status.get("ok"):
        return "active"
    return "unknown"


def _timeline_item(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return {
        "ts": event.get("ts"),
        "event_type": event.get("event_type"),
        "agent_id": payload.get("agent_id") or payload.get("receiver"),
        "task_id": payload.get("task_id"),
        "summary": _event_summary(event),
    }


def _event_summary(event: dict[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    event_type = event.get("event_type", "")
    if event_type == "agent_invoked":
        return f"{payload.get('agent_id')} invoked; prompt_chars={payload.get('prompt_chars')}"
    if event_type == "message_sent":
        return f"{payload.get('sender')} -> {payload.get('receiver')}"
    if event_type == "state_written":
        state = payload.get("state", {})
        return f"{state.get('source_agent')} wrote {state.get('state_type')}"
    if event_type == "autogen_model_client_usage":
        return (
            f"{payload.get('model') or payload.get('client_class')} LLM usage; "
            f"total={payload.get('llm_total_tokens', 0)}"
        )
    return event_type


def _agent(mode_view: dict[str, Any], agent_id: str | None) -> dict[str, Any]:
    agent_id = agent_id or "unknown"
    for item in mode_view["agents"]:
        if item["agent_id"] == agent_id:
            return item
    item = {
        "agent_id": agent_id,
        "status": "pending",
        "prompt_chars": 0,
        "received_summary": "",
        "output_summary": "",
        "output_content": "",
        "state_refs": [],
        "memory_refs": [],
        "contract_guard": {},
        "retries": [],
    }
    mode_view["agents"].append(item)
    return item


def _apply_message(mode_view: dict[str, Any], payload: dict[str, Any], ts: str | None) -> None:
    receiver = payload.get("receiver") or "unknown"
    agent = _agent(mode_view, receiver)
    content = str(payload.get("content") or "")
    packet = _parse_json_packet(content)
    handoff_to = packet.get("to") if isinstance(packet, dict) else ""
    summary = packet.get("summary") if isinstance(packet, dict) else ""
    if not summary:
        summary = _shorten(content, 220)

    agent["sender"] = payload.get("sender")
    agent["output_content"] = content
    agent["output_summary"] = summary
    agent["handoff_to"] = handoff_to
    agent["state_refs"] = payload.get("state_refs", [])
    agent["memory_refs"] = payload.get("memory_refs", [])
    agent["status"] = "done"
    mode_view["messages"].append(
        {
            "ts": ts,
            "sender": payload.get("sender"),
            "receiver": receiver,
            "handoff_to": handoff_to,
            "content": content,
            "summary": summary,
            "state_refs": payload.get("state_refs", []),
            "memory_refs": payload.get("memory_refs", []),
        }
    )


def _finalize_agents(mode_view: dict[str, Any]) -> None:
    order = {agent_id: index for index, agent_id in enumerate(AGENT_ORDER)}
    mode_view["agents"].sort(key=lambda item: order.get(item["agent_id"], 999))
    previous_output = mode_view.get("task", {}).get("title") or "User task"
    for agent in mode_view["agents"]:
        agent["received_summary"] = _shorten(previous_output, 220)
        previous_output = agent.get("output_summary") or agent.get("output_content") or previous_output


def _state_pool_from_snapshot(pool_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    state_pool = pool_snapshot.get("state_pool") if isinstance(pool_snapshot, dict) else None
    if not isinstance(state_pool, dict):
        return []
    states = state_pool.get("states", [])
    return states if isinstance(states, list) else []


def _memory_graph(memory_snapshot: dict[str, Any], state_pool: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    for state in state_pool:
        state_id = state.get("state_id")
        if state_id:
            nodes[state_id] = {
                "id": state_id,
                "type": "StateObject",
                "label": state_id,
                "summary": state.get("summary", ""),
                "status": state.get("lifecycle", ""),
            }

    for view in memory_snapshot.get("memory_views", []) if isinstance(memory_snapshot, dict) else []:
        view_id = view.get("memory_view_id")
        if not view_id:
            continue
        nodes[view_id] = {
            "id": view_id,
            "type": "MemoryView",
            "label": view_id,
            "summary": view.get("prompt_summary", ""),
            "status": view.get("status", ""),
        }
        for claim_id in view.get("active_claim_ids", []):
            edges.append({"source": view_id, "target": claim_id, "label": "active-claim"})

    for claim in memory_snapshot.get("claim_cards", []) if isinstance(memory_snapshot, dict) else []:
        claim_id = claim.get("claim_id")
        if not claim_id:
            continue
        nodes[claim_id] = {
            "id": claim_id,
            "type": "ClaimCard",
            "label": claim_id,
            "summary": claim.get("summary", ""),
            "status": claim.get("status", ""),
        }
        promotion_view_id = claim.get("promotion_view_id")
        if promotion_view_id:
            edges.append({"source": claim_id, "target": promotion_view_id, "label": "promoted-to"})

    for memory in memory_snapshot.get("memories", []) if isinstance(memory_snapshot, dict) else []:
        memory_id = memory.get("memory_id")
        if not memory_id:
            continue
        nodes[memory_id] = {
            "id": memory_id,
            "type": "MemoryObject",
            "label": memory_id,
            "summary": memory.get("summary", ""),
            "status": memory.get("status", ""),
        }
        if memory.get("memory_view_id"):
            edges.append({"source": memory_id, "target": memory["memory_view_id"], "label": "memory-view"})
        if memory.get("claim_id"):
            edges.append({"source": memory_id, "target": memory["claim_id"], "label": "claim"})
        if memory.get("promotion_view_id"):
            edges.append({"source": memory_id, "target": memory["promotion_view_id"], "label": "promotion"})

    for promotion in memory_snapshot.get("promotion_views", []) if isinstance(memory_snapshot, dict) else []:
        promotion_id = promotion.get("promotion_view_id")
        if not promotion_id:
            continue
        nodes[promotion_id] = {
            "id": promotion_id,
            "type": "PromotionView",
            "label": promotion_id,
            "summary": promotion.get("core_claim", ""),
            "status": "active",
        }
        for state_id in promotion.get("source_state_ids", []):
            edges.append({"source": promotion_id, "target": state_id, "label": "source"})
        for state_id in promotion.get("evidence_refs", []):
            edges.append({"source": promotion_id, "target": state_id, "label": "evidence"})

    for candidate in memory_snapshot.get("memory_candidates", []) if isinstance(memory_snapshot, dict) else []:
        candidate_id = candidate.get("candidate_id")
        if not candidate_id:
            continue
        nodes[candidate_id] = {
            "id": candidate_id,
            "type": "MemoryCandidate",
            "label": candidate_id,
            "summary": candidate.get("summary", ""),
            "status": candidate.get("admission_status", ""),
        }

    return {"nodes": list(nodes.values()), "edges": edges, "fallback": False}


def _fallback_memory_graph(snapshot: dict[str, Any]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    for mode_view in snapshot.get("modes", {}).values():
        for agent in mode_view.get("agents", []):
            agent_id = f"agent:{agent.get('agent_id')}"
            nodes.setdefault(
                agent_id,
                {
                    "id": agent_id,
                    "type": "Agent",
                    "label": agent.get("agent_id", "agent"),
                    "summary": "Trace-only fallback node",
                    "status": agent.get("status", ""),
                },
            )
            for ref in agent.get("memory_refs", []):
                memory_id = ref.get("memory_id") if isinstance(ref, dict) else ref
                if not memory_id:
                    continue
                nodes.setdefault(
                    memory_id,
                    {
                        "id": memory_id,
                        "type": "MemoryRef",
                        "label": memory_id,
                        "summary": "Memory detail unavailable in this older run",
                        "status": "referenced",
                    },
                )
                edges.append({"source": agent_id, "target": memory_id, "label": "references"})
    return {"nodes": list(nodes.values()), "edges": edges, "fallback": True}


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    events.append(value)
    except OSError:
        return []
    return events


def _infer_status(run_dir: Path) -> str:
    if (run_dir / "summary.json").exists():
        return "succeeded"
    if (run_dir / "trace.jsonl").exists():
        return "running"
    return "unknown"


def _parse_json_packet(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _shorten(text: Any, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "..."
