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

from agent_runtime.reliability.final_delivery_guard import (
    FinalDeliveryAssessment,
    assess_final_delivery,
    has_exact_last_line_marker,
)


class ReviewerFinalTextTerminationConfig(BaseModel):
    marker: str
    source: str = "reviewer"
    semantic_guard: bool = True


class ReviewerFinalTextTermination(
    TerminationCondition,
    Component[ReviewerFinalTextTerminationConfig],
):
    """Stop only on a reviewer marker attached to a valid final artifact."""

    component_config_schema = ReviewerFinalTextTerminationConfig
    component_provider_override = (
        "agent_runtime.adapters.autogen_termination.ReviewerFinalTextTermination"
    )
    component_description = (
        "Stops on an exact final marker in a reviewer's visible TextMessage only "
        "after a rules-first semantic delivery check. Internal events are ignored."
    )
    component_label = "Reviewer Final Answer Termination"

    def __init__(
        self,
        marker: str,
        source: str = "reviewer",
        semantic_guard: bool = True,
    ) -> None:
        if not marker.strip():
            raise ValueError("marker must not be empty")
        if not source.strip():
            raise ValueError("source must not be empty")
        self._marker = marker.strip()
        self._source = source.strip()
        self._semantic_guard = semantic_guard
        self._terminated = False
        self._last_assessment: FinalDeliveryAssessment | None = None
        self._grounding_contexts: list[str] = []

    @property
    def terminated(self) -> bool:
        return self._terminated

    @property
    def last_assessment(self) -> FinalDeliveryAssessment | None:
        return self._last_assessment

    def record_user_task(self, task: str) -> None:
        normalized = str(task or "").strip()
        if normalized and normalized not in self._grounding_contexts:
            self._grounding_contexts.append(normalized)

    async def __call__(
        self,
        messages: Sequence[BaseAgentEvent | BaseChatMessage],
    ) -> StopMessage | None:
        if self._terminated:
            raise TerminatedException("Termination condition has already been reached")

        for message in messages:
            if isinstance(message, TextMessage) and message.source == "user":
                self.record_user_task(message.content)

        for message in messages:
            if not isinstance(message, TextMessage):
                continue
            if message.source == "user":
                continue
            if message.source != self._source or not has_exact_last_line_marker(
                message.content,
                self._marker,
            ):
                continue
            assessment = assess_final_delivery(
                request="",
                content=message.content,
                source=message.source,
                expected_source=self._source,
                marker=self._marker,
                require_marker=True,
                grounding_contexts=self._grounding_contexts,
            )
            self._last_assessment = assessment
            if assessment.valid or not self._semantic_guard:
                self._terminated = True
                return StopMessage(
                    content=(
                        f"Reviewer '{self._source}' emitted a validated final artifact "
                        "with the exact final answer marker"
                    ),
                    source="ReviewerFinalTextTermination",
                )
        return None

    async def reset(self) -> None:
        self._terminated = False
        self._last_assessment = None

    def _to_config(self) -> ReviewerFinalTextTerminationConfig:
        return ReviewerFinalTextTerminationConfig(
            marker=self._marker,
            source=self._source,
            semantic_guard=self._semantic_guard,
        )

    @classmethod
    def _from_config(
        cls,
        config: ReviewerFinalTextTerminationConfig,
    ) -> Self:
        return cls(
            marker=config.marker,
            source=config.source,
            semantic_guard=config.semantic_guard,
        )
