from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agent_runtime.core.kernel import AgentDescriptor, CollaborationKernel


@runtime_checkable
class FrameworkAdapter(Protocol):
    """Boundary implemented by AutoGen, LangGraph, and other drivers."""

    @property
    def framework_name(self) -> str:
        """Stable framework identifier used in kernel sessions."""

    def activate(self, kernel: CollaborationKernel) -> None:
        """Attach framework hooks to a collaboration kernel."""

    def deactivate(self) -> None:
        """Detach hooks and release adapter-owned resources."""

    def describe_agent(self, framework_agent: Any) -> AgentDescriptor:
        """Map a framework-specific agent object to the neutral descriptor."""
