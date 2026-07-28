from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "agentlite.reliability.v1"


class ReviewDecisionKind(str, Enum):
    APPROVE = "approve"
    BLOCK = "block"
    REQUEST_REVISION = "request_revision"
    ABSTAIN = "abstain"


class DeliveryStatus(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    version: int
    content_hash: str
    source_id: str = ""
    scope_id: str = ""
    task_id: str = ""

    @classmethod
    def from_content(
        cls,
        *,
        artifact_id: str,
        version: int,
        content: str,
        source_id: str = "",
        scope_id: str = "",
        task_id: str = "",
    ) -> ArtifactRef:
        return cls(
            artifact_id=str(artifact_id).strip(),
            version=int(version),
            content_hash=content_digest(content),
            source_id=str(source_id).strip(),
            scope_id=str(scope_id).strip(),
            task_id=str(task_id).strip(),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ArtifactRef:
        return cls(
            artifact_id=str(value.get("artifact_id") or "").strip(),
            version=_positive_int(value.get("version")),
            content_hash=str(value.get("content_hash") or "").strip(),
            source_id=str(value.get("source_id") or "").strip(),
            scope_id=str(value.get("scope_id") or "").strip(),
            task_id=str(value.get("task_id") or "").strip(),
        )

    @property
    def identity(self) -> tuple[str, str, str, int]:
        return (
            self.scope_id,
            self.task_id,
            self.artifact_id,
            self.version,
        )

    def matches_content(self, content: str) -> bool:
        return bool(self.content_hash) and self.content_hash == content_digest(content)


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    source_id: str
    start: int
    end: int
    text_hash: str
    quote: str = ""

    @classmethod
    def from_text(
        cls,
        *,
        source_id: str,
        source_text: str,
        start: int,
        end: int,
    ) -> EvidenceRef:
        if start < 0 or end < start or end > len(source_text):
            raise ValueError("evidence_span_out_of_bounds")
        quote = source_text[start:end]
        return cls(
            source_id=str(source_id).strip(),
            start=start,
            end=end,
            text_hash=content_digest(quote),
            quote=quote,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> EvidenceRef:
        return cls(
            source_id=str(value.get("source_id") or "").strip(),
            start=_nonnegative_int(value.get("start")),
            end=_nonnegative_int(value.get("end")),
            text_hash=str(value.get("text_hash") or "").strip(),
            quote=str(value.get("quote") or ""),
        )

    def matches(self, source_text: str) -> bool:
        if self.start < 0 or self.end < self.start or self.end > len(source_text):
            return False
        selected = source_text[self.start : self.end]
        if self.quote and selected != self.quote:
            return False
        return bool(self.text_hash) and content_digest(selected) == self.text_hash


@dataclass(frozen=True, slots=True)
class FindingRef:
    code: str
    severity: str
    target_kind: str
    target_id: str
    evidence: EvidenceRef | None = None
    summary: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> FindingRef:
        evidence_value = value.get("evidence")
        evidence = (
            EvidenceRef.from_mapping(evidence_value)
            if isinstance(evidence_value, Mapping)
            else None
        )
        return cls(
            code=str(value.get("code") or "").strip(),
            severity=str(value.get("severity") or "").strip().lower(),
            target_kind=str(value.get("target_kind") or "").strip().lower(),
            target_id=str(value.get("target_id") or "").strip(),
            evidence=evidence,
            summary=str(value.get("summary") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class VerifiedEventAuthority:
    actor_id: str
    semantic_action: str
    capabilities: tuple[str, ...] = ()
    profile_version: int = 0

    @classmethod
    def from_runtime(
        cls,
        *,
        actor_id: str,
        semantic_action: str,
        capabilities: Iterable[str],
        profile_version: int = 0,
    ) -> VerifiedEventAuthority:
        return cls(
            actor_id=str(actor_id).strip(),
            semantic_action=str(semantic_action).strip().upper(),
            capabilities=tuple(
                dict.fromkeys(
                    str(item).strip().lower()
                    for item in capabilities
                    if str(item).strip()
                )
            ),
            profile_version=max(0, int(profile_version)),
        )

    @property
    def can_review(self) -> bool:
        if self.semantic_action in {
            "REVIEW_OUTPUT",
            "REVIEW_SCHEMA",
            "VERIFY_CLAIM",
            "DIAGNOSE_FAILURE",
        }:
            return True
        return bool(
            {
                "validation",
                "schema_review",
                "failure_review",
                "final_deliverable_review",
            }
            & set(self.capabilities)
        )


@dataclass(frozen=True, slots=True)
class ReviewDecisionEvent:
    event_id: str
    target: ArtifactRef
    decision: ReviewDecisionKind
    sequence: int
    actor_id: str
    findings: tuple[FindingRef, ...] = ()
    supersedes_event_id: str = ""
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ReviewDecisionEvent:
        target = value.get("target")
        if not isinstance(target, Mapping):
            raise ValueError("review_target_missing")
        findings = value.get("findings")
        return cls(
            event_id=str(value.get("event_id") or "").strip(),
            target=ArtifactRef.from_mapping(target),
            decision=ReviewDecisionKind(
                str(value.get("decision") or "").strip().lower()
            ),
            sequence=_positive_int(value.get("sequence")),
            actor_id=str(value.get("actor_id") or "").strip(),
            findings=tuple(
                FindingRef.from_mapping(item)
                for item in (findings if isinstance(findings, list) else ())
                if isinstance(item, Mapping)
            ),
            supersedes_event_id=str(
                value.get("supersedes_event_id") or ""
            ).strip(),
            schema_version=str(
                value.get("schema_version") or SCHEMA_VERSION
            ).strip(),
        )


@dataclass(frozen=True, slots=True)
class DeliveryEvent:
    event_id: str
    artifact: ArtifactRef
    status: DeliveryStatus
    sequence: int
    actor_id: str
    approved_by_event_id: str = ""
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> DeliveryEvent:
        artifact = value.get("artifact")
        if not isinstance(artifact, Mapping):
            raise ValueError("delivery_artifact_missing")
        return cls(
            event_id=str(value.get("event_id") or "").strip(),
            artifact=ArtifactRef.from_mapping(artifact),
            status=DeliveryStatus(
                str(value.get("status") or "").strip().lower()
            ),
            sequence=_positive_int(value.get("sequence")),
            actor_id=str(value.get("actor_id") or "").strip(),
            approved_by_event_id=str(
                value.get("approved_by_event_id") or ""
            ).strip(),
            schema_version=str(
                value.get("schema_version") or SCHEMA_VERSION
            ).strip(),
        )


@dataclass(frozen=True, slots=True)
class EventValidation:
    accepted: bool
    reasons: tuple[str, ...] = ()
    active_review_event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ArtifactDeclaration:
    artifact: ArtifactRef
    content: str


@dataclass(frozen=True, slots=True)
class ParsedReliabilityMetadata:
    metadata_present: bool = False
    review_events_declared: bool = False
    delivery_events_declared: bool = False
    artifacts: tuple[ArtifactDeclaration, ...] = ()
    review_events: tuple[ReviewDecisionEvent, ...] = ()
    delivery_events: tuple[DeliveryEvent, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(slots=True)
class ReliabilityEventLedger:
    """Validate artifact, review, and delivery transitions without text inference."""

    artifacts: dict[tuple[str, str, str, int], ArtifactRef] = field(
        default_factory=dict
    )
    reviews: dict[str, ReviewDecisionEvent] = field(default_factory=dict)
    deliveries: dict[str, DeliveryEvent] = field(default_factory=dict)
    _active_review_by_artifact: dict[
        tuple[str, str, str, int],
        str,
    ] = field(
        default_factory=dict
    )
    _latest_delivery_sequence: dict[
        tuple[str, str, str, int],
        int,
    ] = field(
        default_factory=dict
    )

    def register_artifact(
        self,
        artifact: ArtifactRef,
        *,
        content: str | None = None,
        expected_scope_id: str = "",
        expected_task_id: str = "",
    ) -> EventValidation:
        reasons = _artifact_ref_reasons(artifact)
        reasons.extend(
            _scope_binding_reasons(
                artifact,
                expected_scope_id=expected_scope_id,
                expected_task_id=expected_task_id,
            )
        )
        if content is not None and not artifact.matches_content(content):
            reasons.append("artifact_content_hash_mismatch")
        existing = self.artifacts.get(artifact.identity)
        if existing is not None and existing.content_hash != artifact.content_hash:
            reasons.append("artifact_version_hash_conflict")
        if reasons:
            return EventValidation(False, tuple(dict.fromkeys(reasons)))
        self.artifacts[artifact.identity] = artifact
        return EventValidation(True)

    def record_review(
        self,
        event: ReviewDecisionEvent,
        *,
        authority: VerifiedEventAuthority,
        evidence_sources: Mapping[str, str] | None = None,
        expected_scope_id: str = "",
        expected_task_id: str = "",
    ) -> EventValidation:
        scope_reasons = _scope_binding_reasons(
            event.target,
            expected_scope_id=expected_scope_id,
            expected_task_id=expected_task_id,
        )
        if scope_reasons:
            return EventValidation(
                False,
                tuple(dict.fromkeys(scope_reasons)),
            )
        existing_event = self.reviews.get(event.event_id)
        if existing_event == event:
            return EventValidation(
                True,
                reasons=("idempotent_event_replay",),
                active_review_event_id=(
                    self._active_review_by_artifact.get(
                        event.target.identity,
                        "",
                    )
                ),
            )
        reasons = _event_identity_reasons(
            event_id=event.event_id,
            schema_version=event.schema_version,
            actor_id=event.actor_id,
            sequence=event.sequence,
        )
        if event.actor_id != authority.actor_id:
            reasons.append("review_actor_authority_mismatch")
        if not authority.can_review:
            reasons.append("review_authority_not_verified")
        if event.target.identity not in self.artifacts:
            reasons.append("review_target_not_registered")
        elif (
            self.artifacts[event.target.identity].content_hash
            != event.target.content_hash
        ):
            reasons.append("review_target_hash_mismatch")
        if event.event_id in self.reviews or event.event_id in self.deliveries:
            reasons.append("event_id_already_registered")

        active_event = self.active_review(event.target)
        if active_event is not None:
            if event.sequence <= active_event.sequence:
                reasons.append("review_sequence_not_monotonic")
            if event.supersedes_event_id != active_event.event_id:
                reasons.append("review_supersedes_active_event_required")
        elif event.supersedes_event_id:
            reasons.append("review_supersedes_unknown_event")

        if event.supersedes_event_id:
            superseded = self.reviews.get(event.supersedes_event_id)
            if superseded is None:
                reasons.append("review_supersedes_unknown_event")
            elif superseded.target.identity != event.target.identity:
                reasons.append("review_supersedes_different_artifact")

        sources = evidence_sources or {}
        for finding in event.findings:
            if not finding.code:
                reasons.append("review_finding_code_missing")
            if not finding.target_kind or not finding.target_id:
                reasons.append("review_finding_target_missing")
            if finding.evidence is not None:
                source_text = sources.get(finding.evidence.source_id)
                if source_text is None:
                    reasons.append("review_evidence_source_missing")
                elif not finding.evidence.matches(source_text):
                    reasons.append("review_evidence_span_mismatch")

        if reasons:
            return EventValidation(
                False,
                tuple(dict.fromkeys(reasons)),
                active_event.event_id if active_event is not None else "",
            )
        self.reviews[event.event_id] = event
        self._active_review_by_artifact[event.target.identity] = event.event_id
        return EventValidation(True, active_review_event_id=event.event_id)

    def record_delivery(
        self,
        event: DeliveryEvent,
        *,
        content: str | None = None,
        authority_actor_id: str = "",
        expected_scope_id: str = "",
        expected_task_id: str = "",
    ) -> EventValidation:
        scope_reasons = _scope_binding_reasons(
            event.artifact,
            expected_scope_id=expected_scope_id,
            expected_task_id=expected_task_id,
        )
        if scope_reasons:
            return EventValidation(
                False,
                tuple(dict.fromkeys(scope_reasons)),
            )
        existing_event = self.deliveries.get(event.event_id)
        if existing_event == event:
            active_review = self.active_review(event.artifact)
            return EventValidation(
                True,
                reasons=("idempotent_event_replay",),
                active_review_event_id=(
                    active_review.event_id
                    if active_review is not None
                    else ""
                ),
            )
        reasons = _event_identity_reasons(
            event_id=event.event_id,
            schema_version=event.schema_version,
            actor_id=event.actor_id,
            sequence=event.sequence,
        )
        if authority_actor_id and event.actor_id != authority_actor_id:
            reasons.append("delivery_actor_authority_mismatch")
        registered = self.artifacts.get(event.artifact.identity)
        if registered is None:
            reasons.append("delivery_artifact_not_registered")
        elif registered.content_hash != event.artifact.content_hash:
            reasons.append("delivery_artifact_hash_mismatch")
        if content is not None and not event.artifact.matches_content(content):
            reasons.append("delivery_content_hash_mismatch")
        if event.event_id in self.reviews or event.event_id in self.deliveries:
            reasons.append("event_id_already_registered")

        latest_sequence = self._latest_delivery_sequence.get(
            event.artifact.identity,
            0,
        )
        if event.sequence <= latest_sequence:
            reasons.append("delivery_sequence_not_monotonic")

        active_review = self.active_review(event.artifact)
        if event.status is DeliveryStatus.VALIDATED:
            if active_review is None:
                reasons.append("validated_delivery_review_missing")
            elif active_review.decision is not ReviewDecisionKind.APPROVE:
                reasons.append("validated_delivery_not_approved")
            elif event.approved_by_event_id != active_review.event_id:
                reasons.append("validated_delivery_approval_ref_mismatch")
        elif event.approved_by_event_id:
            reasons.append("nonvalidated_delivery_has_approval_ref")

        if event.status is DeliveryStatus.REJECTED:
            if active_review is None or active_review.decision not in {
                ReviewDecisionKind.BLOCK,
                ReviewDecisionKind.REQUEST_REVISION,
            }:
                reasons.append("rejected_delivery_blocking_review_missing")

        if reasons:
            return EventValidation(
                False,
                tuple(dict.fromkeys(reasons)),
                active_review.event_id if active_review is not None else "",
            )
        self.deliveries[event.event_id] = event
        self._latest_delivery_sequence[event.artifact.identity] = event.sequence
        return EventValidation(
            True,
            active_review_event_id=(
                active_review.event_id if active_review is not None else ""
            ),
        )

    def active_review(
        self,
        artifact: ArtifactRef,
    ) -> ReviewDecisionEvent | None:
        event_id = self._active_review_by_artifact.get(artifact.identity)
        return self.reviews.get(event_id) if event_id else None


def parse_reliability_metadata(
    messages: Iterable[Any],
) -> ParsedReliabilityMetadata:
    """Parse only the reserved metadata namespace, never visible message text."""

    metadata_present = False
    review_events_declared = False
    delivery_events_declared = False
    artifacts: list[ArtifactDeclaration] = []
    review_events: list[ReviewDecisionEvent] = []
    delivery_events: list[DeliveryEvent] = []
    errors: list[str] = []
    for message_index, message in enumerate(messages):
        metadata = (
            message.get("metadata")
            if isinstance(message, Mapping)
            else getattr(message, "metadata", None)
        )
        if not isinstance(metadata, Mapping):
            continue
        envelope = metadata.get("agentlite_reliability")
        if envelope is None:
            continue
        metadata_present = True
        if not isinstance(envelope, Mapping):
            errors.append(
                f"message_{message_index}:reliability_envelope_invalid"
            )
            continue
        envelope_version = str(
            envelope.get("schema_version") or SCHEMA_VERSION
        ).strip()
        if envelope_version != SCHEMA_VERSION:
            errors.append(
                f"message_{message_index}:reliability_schema_unsupported"
            )
            continue
        review_events_declared = (
            review_events_declared or "review_events" in envelope
        )
        delivery_events_declared = (
            delivery_events_declared or "delivery_events" in envelope
        )
        content = (
            str(message.get("content_text") or "")
            if isinstance(message, Mapping)
            else str(getattr(message, "content_text", "") or "")
        )
        for item_index, item in enumerate(
            _mapping_sequence(envelope.get("artifacts"))
        ):
            try:
                artifact = ArtifactRef.from_mapping(item)
            except (TypeError, ValueError) as exc:
                errors.append(
                    f"message_{message_index}:artifact_{item_index}:"
                    f"{_error_code(exc)}"
                )
                continue
            artifacts.append(
                ArtifactDeclaration(
                    artifact=artifact,
                    content=content,
                )
            )
        for item_index, item in enumerate(
            _mapping_sequence(envelope.get("review_events"))
        ):
            try:
                review_events.append(
                    ReviewDecisionEvent.from_mapping(item)
                )
            except (TypeError, ValueError) as exc:
                errors.append(
                    f"message_{message_index}:review_{item_index}:"
                    f"{_error_code(exc)}"
                )
        for item_index, item in enumerate(
            _mapping_sequence(envelope.get("delivery_events"))
        ):
            try:
                delivery_events.append(DeliveryEvent.from_mapping(item))
            except (TypeError, ValueError) as exc:
                errors.append(
                    f"message_{message_index}:delivery_{item_index}:"
                    f"{_error_code(exc)}"
                )
    return ParsedReliabilityMetadata(
        metadata_present=metadata_present,
        review_events_declared=review_events_declared,
        delivery_events_declared=delivery_events_declared,
        artifacts=tuple(artifacts),
        review_events=tuple(review_events),
        delivery_events=tuple(delivery_events),
        errors=tuple(errors),
    )


def content_digest(content: str) -> str:
    return "sha256:" + hashlib.sha256(
        str(content).encode("utf-8")
    ).hexdigest()


def _artifact_ref_reasons(artifact: ArtifactRef) -> list[str]:
    reasons: list[str] = []
    if not artifact.artifact_id:
        reasons.append("artifact_id_missing")
    if artifact.version <= 0:
        reasons.append("artifact_version_invalid")
    if not artifact.content_hash.startswith("sha256:"):
        reasons.append("artifact_content_hash_invalid")
    return reasons


def _scope_binding_reasons(
    artifact: ArtifactRef,
    *,
    expected_scope_id: str,
    expected_task_id: str,
) -> list[str]:
    reasons: list[str] = []
    if expected_scope_id and artifact.scope_id != expected_scope_id:
        reasons.append("artifact_scope_mismatch")
    if expected_task_id and artifact.task_id != expected_task_id:
        reasons.append("artifact_task_mismatch")
    return reasons


def _event_identity_reasons(
    *,
    event_id: str,
    schema_version: str,
    actor_id: str,
    sequence: int,
) -> list[str]:
    reasons: list[str] = []
    if not event_id:
        reasons.append("event_id_missing")
    if schema_version != SCHEMA_VERSION:
        reasons.append("event_schema_version_unsupported")
    if not actor_id:
        reasons.append("event_actor_missing")
    if sequence <= 0:
        reasons.append("event_sequence_invalid")
    return reasons


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("positive_integer_required") from exc
    if parsed <= 0:
        raise ValueError("positive_integer_required")
    return parsed


def _nonnegative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("nonnegative_integer_required") from exc
    if parsed < 0:
        raise ValueError("nonnegative_integer_required")
    return parsed


def _mapping_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _error_code(error: Exception) -> str:
    text = str(error).strip()
    return text or type(error).__name__
