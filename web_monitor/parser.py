from __future__ import annotations

import json
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
    if isinstance(summary, dict):
        summary = {**summary, "agent_profiles": _agent_profiles()}
    pool_snapshot = _read_json(run_dir / "pool_snapshot_latest.json", default={})

    snapshot: dict[str, Any] = {
        "run_id": run_id,
        "status": status or _infer_status(run_dir),
        "output_dir": str(run_dir),
        "modes": {mode: _empty_mode_snapshot() for mode in DEFAULT_MODES},
        "summary": summary,
        "token_summary": _summary_token_summary(summary),
        "agent_profiles": _agent_profiles(),
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


def build_session_snapshot(
    session_dir: Path,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    status = _read_json(session_dir / "bootstrap_status.json", default={})
    launch = _read_json(session_dir / "launch.json", default={})
    trace_path = session_dir / "autogen_driver" / "trace.jsonl"
    trace_events = _read_jsonl(trace_path)
    session_id = session_id or str(
        status.get("session_id") or launch.get("session_id") or session_dir.name
    )
    mode_view = _empty_mode_snapshot()
    mode_view["task"] = {
        "task_id": session_id,
        "round_id": 1,
        "title": f"AgentLite AutoGen session {session_id}",
    }
    state_pool: list[dict[str, Any]] = []
    event_counts: dict[str, int] = {}
    token_summary = _autogen_token_summary(trace_events)
    for event in trace_events:
        event_type = str(event.get("event_type", ""))
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        mode_view["timeline"].append(_timeline_item(event))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "state_written":
            state = payload.get("state")
            if isinstance(state, dict):
                state_pool.append(state)
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
        "run_id": session_id,
        "session_id": session_id,
        "status": _session_status(status, trace_events),
        "output_dir": str(session_dir),
        "modes": {
            "baseline_text": _empty_mode_snapshot(),
            "runtime_lite": mode_view,
        },
        "summary": {
            "kind": "agentlite_autogen_session",
            "framework": status.get("framework") or launch.get("framework") or "",
            "driver": status.get("driver") or "",
            "driver_status": status.get("driver_status") or "",
            "hooks_active": bool(status.get("hooks_active")),
            "event_counts": event_counts,
            "trace_path": str(trace_path) if trace_path.exists() else "",
            "token_summary": token_summary,
            "agent_profiles": _agent_profiles(),
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
        "token_summary": token_summary,
        "errors": [status.get("error", "")] if status.get("error") else [],
        "bootstrap_status": status,
        "launch": launch,
    }


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


def _agent_profiles() -> dict[str, Any]:
    profiles = CapabilityProfileManagerLite([]).snapshot()
    profiles.update(
        {
            "team": {
                "agent_id": "team",
                "role": "AutoGenTeam",
                "summary": "AutoGen Team 入口，负责组级任务广播",
                "capabilities": ["Team 调度", "广播入口", "任务流转"],
                "accepted": ["user_task", "team_input"],
            },
            "team_manager": {
                "agent_id": "team_manager",
                "role": "AutoGenTeamManager",
                "summary": "AutoGen 组管理器，维护回合与参与者顺序",
                "capabilities": ["回合管理", "参与者编排"],
                "accepted": ["team_output", "handoff_message"],
            },
            "runtime_bridge": {
                "agent_id": "runtime_bridge",
                "role": "AutoGenRuntimeBridge",
                "summary": "AutoGen Core 运行时桥接层，承接底层消息投递",
                "capabilities": ["Core 桥接", "消息投递", "水合还原"],
                "accepted": ["core_request", "core_response"],
            },
            "autogen_driver": {
                "agent_id": "autogen_driver",
                "role": "AgentLiteDriver",
                "summary": "AgentLite 注入驱动，记录 hook、改写与 trace",
                "capabilities": ["运行时注入", "trace 记录", "协议改写"],
                "accepted": ["driver_event"],
            },
        }
    )
    return profiles


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
        "useful_memory_hit_count": 0,
        "wrong_memory_hit_count": 0,
        "end_to_end_collaboration_tokens": 0,
        "native_baseline_tokens": 0,
        "runtime_tokens": 0,
        "token_savings": 0,
        "token_savings_ratio": 0.0,
        "event_count": 0,
        "source": "autogen_trace",
    }
    seen_cost_events = 0
    for event in trace_events:
        event_type = str(event.get("event_type", ""))
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "autogen_memory_retrieval":
            breakdown["memory_query_count"] += _int(
                payload.get("memory_query_count")
            )
            breakdown["memory_hit_count"] += _int(payload.get("memory_hit_count"))
            breakdown["useful_memory_hit_count"] += _int(
                payload.get("useful_memory_hit_count")
            )
            breakdown["wrong_memory_hit_count"] += _int(
                payload.get("wrong_memory_hit_count")
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
        if event_type == "autogen_agent_receive":
            continue
        native, runtime, direct, prompt_view, retrieved_memory = _autogen_event_cost(
            payload
        )
        if native <= 0 and runtime <= 0:
            continue
        seen_cost_events += 1
        breakdown["native_baseline_tokens"] += native
        breakdown["direct_message_tokens"] += direct
        breakdown["prompt_view_tokens"] += prompt_view
        breakdown["retrieved_memory_tokens"] += retrieved_memory
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
    breakdown["event_count"] = seen_cost_events
    return breakdown


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
    direct = _first_positive(
        payload,
        [
            "shadow_wire_tokens",
            "shp_shadow_envelope_tokens",
            "rewritten_content_tokens",
            "rewritten_input_tokens",
            "rewritten_task_tokens",
            "candidate_input_tokens",
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
        runtime = direct + prompt_view + retrieved_memory
    if direct <= 0 and runtime > prompt_view + retrieved_memory:
        direct = runtime - prompt_view - retrieved_memory
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
