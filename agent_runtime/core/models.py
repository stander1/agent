from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Mode = Literal["baseline_text", "runtime_stub"]


@dataclass(slots=True)
class TaskSpec:
    task_id: str
    group_id: str
    title: str
    prompt: str
    documents: list[str] = field(default_factory=list)
    expected_agents: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentProfile:
    agent_id: str
    role: str
    capabilities: list[str]


@dataclass(slots=True)
class RuntimeMessage:
    task_id: str
    round_id: int
    mode: Mode
    sender: str
    receiver: str
    content: str
    state_refs: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    cost_report: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentOutput:
    agent_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

