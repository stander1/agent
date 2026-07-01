from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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

    snapshot: dict[str, Any] = {
        "run_id": run_id,
        "status": status or _infer_status(run_dir),
        "output_dir": str(run_dir),
        "modes": {mode: _empty_mode_snapshot() for mode in DEFAULT_MODES},
        "summary": summary,
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
        runs.append(
            {
                "run_id": path.name,
                "path": str(path),
                "has_trace": trace_path.exists(),
                "has_summary": summary_path.exists(),
                "has_pool_snapshot": (path / "pool_snapshot_latest.json").exists(),
                "status": _infer_status(path),
                "updated_at": path.stat().st_mtime,
            }
        )
    runs.sort(key=lambda item: item["updated_at"], reverse=True)
    return runs


def _empty_mode_snapshot() -> dict[str, Any]:
    return {
        "task": {},
        "timeline": [],
        "agents": [],
        "messages": [],
        "state_pool": [],
        "memory_graph": {"nodes": [], "edges": [], "fallback": False},
    }


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
