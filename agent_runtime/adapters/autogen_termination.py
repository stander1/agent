"""AutoGen termination conditions used by AgentLite Team templates."""

from __future__ import annotations

from typing import Self, Sequence

from autogen_agentchat.base import TerminatedException, TerminationCondition
from autogen_agentchat.messages import (
    BaseAgentEvent,
    BaseChatMessage,
    StopMessage,
    TextMessage,
)
from autogen_core import Component
from pydantic import BaseModel


class ReviewerFinalTextTerminationConfig(BaseModel):
    marker: str
    source: str = "reviewer"


class ReviewerFinalTextTermination(
    TerminationCondition,
    Component[ReviewerFinalTextTerminationConfig],
):
    """Stop only when a reviewer's visible answer ends with an exact marker."""

    component_config_schema = ReviewerFinalTextTerminationConfig
    component_provider_override = (
        "agent_runtime.adapters.autogen_termination.ReviewerFinalTextTermination"
    )
    component_description = (
        "Stops on an exact final marker in the last non-empty line of a reviewer's "
        "visible TextMessage. Internal reasoning events are ignored."
    )
    component_label = "Reviewer Final Answer Termination"

    def __init__(self, marker: str, source: str = "reviewer") -> None:
        if not marker.strip():
            raise ValueError("marker must not be empty")
        if not source.strip():
            raise ValueError("source must not be empty")
        self._marker = marker.strip()
        self._source = source.strip()
        self._terminated = False

    @property
    def terminated(self) -> bool:
        return self._terminated

    async def __call__(
        self,
        messages: Sequence[BaseAgentEvent | BaseChatMessage],
    ) -> StopMessage | None:
        if self._terminated:
            raise TerminatedException("Termination condition has already been reached")

        for message in messages:
            if not isinstance(message, TextMessage) or message.source != self._source:
                continue
            lines = [line.strip() for line in message.content.splitlines() if line.strip()]
            if lines and lines[-1] == self._marker:
                self._terminated = True
                return StopMessage(
                    content=(
                        f"Reviewer '{self._source}' emitted the exact final answer marker"
                    ),
                    source="ReviewerFinalTextTermination",
                )
        return None

    async def reset(self) -> None:
        self._terminated = False

    def _to_config(self) -> ReviewerFinalTextTerminationConfig:
        return ReviewerFinalTextTerminationConfig(
            marker=self._marker,
            source=self._source,
        )

    @classmethod
    def _from_config(
        cls,
        config: ReviewerFinalTextTerminationConfig,
    ) -> Self:
        return cls(marker=config.marker, source=config.source)
