from __future__ import annotations

from typing import Literal


SHPMessageType = Literal[
    "agent_output",
    "state_ref_handoff",
    "memory_ref_handoff",
    "retrieval_state",
    "embedding_state",
    "artifact_state",
    "failure_state",
    "retry_request",
    "retry_loop_summary",
    "cold_read_request",
    "cold_read_response",
    "readiness_report",
    "capability_advertisement",
    "route_decision",
    "memory_promotion",
    "final_deliverable",
]


MESSAGE_TYPES: tuple[str, ...] = (
    "agent_output",
    "state_ref_handoff",
    "memory_ref_handoff",
    "retrieval_state",
    "embedding_state",
    "artifact_state",
    "failure_state",
    "retry_request",
    "retry_loop_summary",
    "cold_read_request",
    "cold_read_response",
    "readiness_report",
    "capability_advertisement",
    "route_decision",
    "memory_promotion",
    "final_deliverable",
)


def validate_message_type(msg_type: str) -> str:
    if msg_type not in MESSAGE_TYPES:
        allowed = ", ".join(MESSAGE_TYPES)
        raise ValueError(f"Unknown SHP message type: {msg_type}. Allowed: {allowed}")
    return msg_type
