"""AutoGen termination conditions used by AgentLite Team templates."""

from __future__ import annotations

from dataclasses import dataclass
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
    has_explicit_delivery_boundary,
    has_exact_last_line_marker,
)


@dataclass(frozen=True, slots=True)
class ResolvedFinalArtifact:
    content: str
    origin_source: str
    resolution_kind: str


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
        self._last_resolved_artifact: ResolvedFinalArtifact | None = None
        self._grounding_contexts: list[str] = []
        self._latest_candidate: tuple[str, str] | None = None

    @property
    def terminated(self) -> bool:
        return self._terminated

    @property
    def last_assessment(self) -> FinalDeliveryAssessment | None:
        return self._last_assessment

    @property
    def last_resolved_artifact(self) -> ResolvedFinalArtifact | None:
        return self._last_resolved_artifact

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
            if message.source != self._source:
                candidate = str(message.content or "").strip()
                if candidate:
                    self._latest_candidate = (message.source, candidate)
                continue
            marker_present = has_exact_last_line_marker(
                message.content,
                self._marker,
            )
            if not marker_present and not has_explicit_delivery_boundary(
                message.content
            ):
                continue
            assessment = assess_final_delivery(
                request="",
                content=message.content,
                source=message.source,
                expected_source=self._source,
                marker=self._marker,
                require_marker=marker_present,
                minimum_body_chars=0 if marker_present else 80,
                grounding_contexts=self._grounding_contexts,
            )
            self._last_assessment = assessment
            if assessment.valid or not self._semantic_guard:
                resolution_kind = (
                    "reviewer_artifact"
                    if marker_present
                    else "reviewer_marker_repaired"
                )
                self._last_resolved_artifact = ResolvedFinalArtifact(
                    content=_with_exact_marker(
                        assessment.body,
                        self._marker,
                    ),
                    origin_source=message.source,
                    resolution_kind=resolution_kind,
                )
                return self._stop_message(resolution_kind)
            if (
                assessment.approved_prior_artifact
                and self._latest_candidate is not None
            ):
                candidate_source, candidate_content = self._latest_candidate
                candidate_assessment = assess_final_delivery(
                    request="",
                    content=candidate_content,
                    source=candidate_source,
                    minimum_body_chars=0,
                    grounding_contexts=self._grounding_contexts,
                )
                if candidate_assessment.valid:
                    self._last_resolved_artifact = ResolvedFinalArtifact(
                        content=_with_exact_marker(
                            candidate_assessment.body,
                            self._marker,
                        ),
                        origin_source=candidate_source,
                        resolution_kind="prior_artifact_approved",
                    )
                    return self._stop_message("prior_artifact_approved")
        return None

    async def reset(self) -> None:
        self._terminated = False
        self._last_assessment = None
        self._last_resolved_artifact = None
        self._latest_candidate = None

    def _stop_message(self, resolution_kind: str) -> StopMessage:
        self._terminated = True
        if resolution_kind == "prior_artifact_approved":
            detail = "approved and promoted the preceding agent artifact"
        elif resolution_kind == "reviewer_marker_repaired":
            detail = "emitted a valid final artifact whose missing marker was repaired"
        else:
            detail = "emitted a validated final artifact with the exact final marker"
        return StopMessage(
            content=f"Reviewer '{self._source}' {detail}",
            source="ReviewerFinalTextTermination",
        )

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


def resolve_final_artifact(
    messages: Sequence[BaseAgentEvent | BaseChatMessage],
    *,
    marker: str,
    reviewer_source: str = "reviewer",
    grounding_contexts: Sequence[str] = (),
) -> ResolvedFinalArtifact | None:
    latest_candidate: tuple[str, str] | None = None
    for message in messages:
        if not isinstance(message, TextMessage):
            continue
        content = str(message.content or "").strip()
        if not content or message.source == "user":
            continue
        if message.source != reviewer_source:
            latest_candidate = (message.source, content)
            continue
        marker_present = has_exact_last_line_marker(content, marker)
        if not marker_present and not has_explicit_delivery_boundary(content):
            continue
        assessment = assess_final_delivery(
            request="",
            content=content,
            source=message.source,
            expected_source=reviewer_source,
            marker=marker,
            require_marker=marker_present,
            minimum_body_chars=0 if marker_present else 80,
            grounding_contexts=grounding_contexts,
        )
        if assessment.valid:
            return ResolvedFinalArtifact(
                content=_with_exact_marker(assessment.body, marker),
                origin_source=message.source,
                resolution_kind=(
                    "reviewer_artifact"
                    if marker_present
                    else "reviewer_marker_repaired"
                ),
            )
        if assessment.approved_prior_artifact and latest_candidate is not None:
            candidate_source, candidate_content = latest_candidate
            candidate_assessment = assess_final_delivery(
                request="",
                content=candidate_content,
                source=candidate_source,
                minimum_body_chars=0,
                grounding_contexts=grounding_contexts,
            )
            if candidate_assessment.valid:
                return ResolvedFinalArtifact(
                    content=_with_exact_marker(
                        candidate_assessment.body,
                        marker,
                    ),
                    origin_source=candidate_source,
                    resolution_kind="prior_artifact_approved",
                )
    return None


def _with_exact_marker(body: str, marker: str) -> str:
    return f"{body.rstrip()}\n\n{marker.strip()}"
