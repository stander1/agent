from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from agent_runtime.drivers.autogen_codec import DecodedAutoGenMessage


@dataclass(slots=True, frozen=True)
class AutoGenShadowHandoffPlan:
    """A conservative SHP handoff plan derived from observed AutoGen messages."""

    declared_receiver: str
    receiver_source: str
    summary: str
    message_kinds: list[str]
    message_sources: list[str]
    handoff_targets: list[str]
    native_text_chars: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True, frozen=True)
class AutoGenShadowBroadcastPlan:
    """Per-receiver SHP replacement plan for an observed AutoGen team broadcast."""

    sender: str
    target_kind: str
    method_name: str
    receiver_plans: list[AutoGenShadowHandoffPlan]
    participant_names: list[str]
    native_text_chars: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def plan_shadow_handoff(
    *,
    sender: str,
    target_kind: str,
    method_name: str,
    decoded_messages: list[DecodedAutoGenMessage],
    native_text: str,
    summary_limit: int = 320,
) -> AutoGenShadowHandoffPlan:
    """Build a SHP shadow plan without claiming ownership of AutoGen routing."""

    handoff_targets = _unique_nonempty(
        message.target
        for message in decoded_messages
        if message.message_kind == "handoff"
    )
    if handoff_targets:
        declared_receiver = handoff_targets[0]
        receiver_source = "handoff_message_target"
    elif target_kind == "agentchat_agent":
        declared_receiver = "autogen_team"
        receiver_source = "agentchat_agent_default"
    elif target_kind == "agentchat_team":
        declared_receiver = "autogen_next"
        receiver_source = "agentchat_team_default"
    elif target_kind == "core_runtime":
        declared_receiver = "autogen_runtime_peer"
        receiver_source = "core_runtime_default"
    else:
        declared_receiver = "autogen_next"
        receiver_source = "unknown_target_default"

    kinds = _unique_nonempty(message.message_kind for message in decoded_messages)
    sources = _unique_nonempty(message.source for message in decoded_messages)
    summary = _build_summary(
        sender=sender,
        method_name=method_name,
        kinds=kinds,
        native_text=native_text,
        limit=summary_limit,
    )
    return AutoGenShadowHandoffPlan(
        declared_receiver=declared_receiver,
        receiver_source=receiver_source,
        summary=summary,
        message_kinds=kinds,
        message_sources=sources,
        handoff_targets=handoff_targets,
        native_text_chars=len(native_text),
    )


def plan_shadow_broadcast(
    *,
    sender: str,
    target_kind: str,
    method_name: str,
    decoded_messages: list[DecodedAutoGenMessage],
    native_text: str,
    participant_names: list[str],
    summary_limit: int = 240,
) -> AutoGenShadowBroadcastPlan:
    """Build one conservative SHP plan per observed AutoGen team participant."""

    participants = _unique_nonempty(participant_names)
    kinds = _unique_nonempty(message.message_kind for message in decoded_messages)
    sources = _unique_nonempty(message.source for message in decoded_messages)
    receiver_plans: list[AutoGenShadowHandoffPlan] = []
    for receiver in participants:
        summary = _build_broadcast_summary(
            sender=sender,
            method_name=method_name,
            receiver=receiver,
            kinds=kinds,
            native_text=native_text,
            limit=summary_limit,
        )
        receiver_plans.append(
            AutoGenShadowHandoffPlan(
                declared_receiver=receiver,
                receiver_source="team_participant_names",
                summary=summary,
                message_kinds=kinds,
                message_sources=sources,
                handoff_targets=[],
                native_text_chars=len(native_text),
            )
        )
    return AutoGenShadowBroadcastPlan(
        sender=sender,
        target_kind=target_kind,
        method_name=method_name,
        receiver_plans=receiver_plans,
        participant_names=participants,
        native_text_chars=len(native_text),
    )


def _build_summary(
    *,
    sender: str,
    method_name: str,
    kinds: list[str],
    native_text: str,
    limit: int,
) -> str:
    kind_text = ",".join(kinds) if kinds else "unknown"
    preview = " ".join(native_text.split())
    if preview:
        body = f"{sender}.{method_name} produced {kind_text}: {preview}"
    else:
        body = f"{sender}.{method_name} produced {kind_text} with empty text payload"
    return body[:limit]


def _build_broadcast_summary(
    *,
    sender: str,
    method_name: str,
    receiver: str,
    kinds: list[str],
    native_text: str,
    limit: int,
) -> str:
    kind_text = ",".join(kinds) if kinds else "unknown"
    preview = " ".join(native_text.split())
    if preview:
        body = (
            f"{sender}.{method_name} broadcast candidate to {receiver} "
            f"with {kind_text}: {preview}"
        )
    else:
        body = (
            f"{sender}.{method_name} broadcast candidate to {receiver} "
            f"with {kind_text} and empty text payload"
        )
    return body[:limit]


def _unique_nonempty(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:  # type: ignore[assignment]
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items
