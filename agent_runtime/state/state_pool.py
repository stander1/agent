from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class StateRef:
    state_id: str
    state_type: str
    version: int
    payload_kind: str
    contains_embedding_refs: bool
    usage_hint: str


@dataclass(slots=True)
class StateObject:
    state_id: str
    state_type: str
    task_id: str
    source_agent: str
    created_at: str
    payload_ref: str
    payload_kind: str
    contains_embedding_refs: bool
    summary: str
    size_bytes: int
    version: int = 1


class StatePoolLite:
    """File-backed v1 state pool.

    The pool stores structured payloads as JSON files and passes StateRef objects
    through SHP-lite messages. This is intentionally small but already separates
    runtime state from direct agent-to-agent text.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.payload_dir = self.root_dir / "state_payloads"
        self.payload_dir.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, StateObject] = {}

    def write_state(
        self,
        *,
        task_id: str,
        source_agent: str,
        state_type: str,
        payload: dict[str, Any],
        summary: str,
        usage_hint: str,
        payload_kind: str = "structured_non_text",
        contains_embedding_refs: bool = False,
    ) -> tuple[StateRef, StateObject]:
        state_id = self._next_state_id(task_id, source_agent, state_type, payload)
        path = self.payload_dir / f"{state_id}.json"
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        path.write_text(encoded, encoding="utf-8")
        size_bytes = len(encoded.encode("utf-8"))

        state = StateObject(
            state_id=state_id,
            state_type=state_type,
            task_id=task_id,
            source_agent=source_agent,
            created_at=datetime.now(timezone.utc).isoformat(),
            payload_ref=str(path),
            payload_kind=payload_kind,
            contains_embedding_refs=contains_embedding_refs,
            summary=summary,
            size_bytes=size_bytes,
        )
        self._states[state_id] = state
        return (
            StateRef(
                state_id=state.state_id,
                state_type=state.state_type,
                version=state.version,
                payload_kind=state.payload_kind,
                contains_embedding_refs=state.contains_embedding_refs,
                usage_hint=usage_hint,
            ),
            state,
        )

    def render_prompt_view(
        self, state_ref: StateRef, agent_role: str, budget_chars: int = 900
    ) -> str:
        state = self._states[state_ref.state_id]
        payload = json.loads(Path(state.payload_ref).read_text(encoding="utf-8"))

        if state.state_type == "retrieval_state":
            ranked = payload.get("evidence_rank", [])
            score_map = payload.get("score_map", {})
            snippets = []
            limit = 3 if agent_role in {"WriterAgent", "PlannerAgent"} else 5
            for chunk_id in ranked[:limit]:
                item = payload.get("chunks", {}).get(chunk_id, {})
                snippets.append(
                    f"- {chunk_id} score={score_map.get(chunk_id, 0):.2f}: "
                    f"{item.get('text', '')[:180]}"
                )
            rendered = (
                f"[retrieval_state:{state.state_id}] {state.summary}\n"
                + "\n".join(snippets)
            )
        elif state.state_type == "artifact_state":
            rendered = (
                f"[artifact_state:{state.state_id}] {state.summary}; "
                f"artifact={payload.get('code_artifact_id', payload.get('artifact_id', 'n/a'))}; "
                f"sha256={payload.get('sha256', 'n/a')[:12]}"
            )
        elif state.state_type == "failure_state":
            rendered = (
                f"[failure_state:{state.state_id}] {state.summary}; "
                f"error={payload.get('error_type', 'unknown')}"
            )
        else:
            rendered = f"[{state.state_type}:{state.state_id}] {state.summary}"

        return rendered[:budget_chars]

    def ref_to_dict(self, state_ref: StateRef) -> dict[str, Any]:
        return asdict(state_ref)

    def _next_state_id(
        self, task_id: str, source_agent: str, state_type: str, payload: dict[str, Any]
    ) -> str:
        seed = json.dumps(
            {
                "task_id": task_id,
                "source_agent": source_agent,
                "state_type": state_type,
                "payload": payload,
                "count": len(self._states),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"state_{digest}"

