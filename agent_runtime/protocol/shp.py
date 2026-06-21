from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any


PROTOCOL_VERSION = "shp.v1-lite"


@dataclass(slots=True)
class SHPHeader:
    shp_id: str
    protocol_version: str
    task_id: str
    round_id: int
    sender: str
    receiver: str
    msg_type: str
    created_at: str


@dataclass(slots=True)
class SHPControl:
    action: str
    readiness: str = "ready"
    allowed_next_step: str = "continue"
    access_policy: str = "prompt_view_first"
    capability_hint: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SHPEnvelope:
    header: SHPHeader
    control: SHPControl
    summary: str
    state_refs: list[dict[str, Any]]
    memory_refs: list[dict[str, Any]]
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def build_handoff_envelope(
    *,
    task_id: str,
    round_id: int,
    sender: str,
    receiver: str,
    summary: str,
    action: str,
    state_refs: list[Any] | None = None,
    memory_refs: list[Any] | None = None,
    metrics: dict[str, Any] | None = None,
    readiness: str = "ready",
    allowed_next_step: str = "continue",
    access_policy: str = "prompt_view_first",
    capability_hint: list[str] | None = None,
    parameters: dict[str, Any] | None = None,
    msg_type: str = "agent_output",
) -> SHPEnvelope:
    header = SHPHeader(
        shp_id=f"shp_{task_id}_{round_id}_{sender}_{receiver}",
        protocol_version=PROTOCOL_VERSION,
        task_id=task_id,
        round_id=round_id,
        sender=sender,
        receiver=receiver,
        msg_type=msg_type,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    control = SHPControl(
        action=action,
        readiness=readiness,
        allowed_next_step=allowed_next_step,
        access_policy=access_policy,
        capability_hint=capability_hint or [],
        parameters=parameters or {},
    )
    return SHPEnvelope(
        header=header,
        control=control,
        summary=summary,
        state_refs=[_ref_to_dict(ref) for ref in state_refs or []],
        memory_refs=[_ref_to_dict(ref) for ref in memory_refs or []],
        metrics=metrics or {},
    )


def _ref_to_dict(ref: Any) -> dict[str, Any]:
    if is_dataclass(ref):
        return asdict(ref)
    if isinstance(ref, dict):
        return dict(ref)
    raise TypeError(f"Unsupported SHP ref type: {type(ref)!r}")

