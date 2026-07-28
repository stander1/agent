from __future__ import annotations

import unittest

from agent_runtime.reliability.typed_events import (
    ArtifactRef,
    DeliveryEvent,
    DeliveryStatus,
    EvidenceRef,
    FindingRef,
    ReliabilityEventLedger,
    ReviewDecisionEvent,
    ReviewDecisionKind,
    VerifiedEventAuthority,
    content_digest,
    parse_reliability_metadata,
)


class TypedReliabilityEventsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.content = "Release candidate uses the verified source snapshot."
        self.artifact = ArtifactRef.from_content(
            artifact_id="artifact_release",
            version=3,
            content=self.content,
            source_id="arbitrary-author",
            scope_id="scope-release",
            task_id="task-release",
        )
        self.ledger = ReliabilityEventLedger()
        self.assertTrue(
            self.ledger.register_artifact(
                self.artifact,
                content=self.content,
                expected_scope_id=self.artifact.scope_id,
                expected_task_id=self.artifact.task_id,
            ).accepted
        )
        self.authority = VerifiedEventAuthority.from_runtime(
            actor_id="QualityGate17",
            semantic_action="HANDLE_TASK",
            capabilities=("validation",),
            profile_version=4,
        )

    def test_authority_uses_runtime_capability_not_role_name(self) -> None:
        event = self._review(
            event_id="review_1",
            decision=ReviewDecisionKind.APPROVE,
            sequence=1,
        )
        result = self.ledger.record_review(
            event,
            authority=self.authority,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(
            self.ledger.active_review(self.artifact).event_id,
            "review_1",
        )

    def test_self_declared_actor_cannot_bypass_runtime_authority(self) -> None:
        event = self._review(
            event_id="review_1",
            decision=ReviewDecisionKind.APPROVE,
            sequence=1,
        )
        authority = VerifiedEventAuthority.from_runtime(
            actor_id="ordinary-author",
            semantic_action="WRITE_OUTPUT",
            capabilities=("writing",),
        )

        result = self.ledger.record_review(event, authority=authority)

        self.assertFalse(result.accepted)
        self.assertIn("review_actor_authority_mismatch", result.reasons)
        self.assertIn("review_authority_not_verified", result.reasons)

    def test_review_requires_registered_exact_artifact_version(self) -> None:
        wrong_target = ArtifactRef.from_content(
            artifact_id=self.artifact.artifact_id,
            version=self.artifact.version,
            content="different content",
            scope_id=self.artifact.scope_id,
            task_id=self.artifact.task_id,
        )
        event = ReviewDecisionEvent(
            event_id="review_wrong_hash",
            target=wrong_target,
            decision=ReviewDecisionKind.BLOCK,
            sequence=1,
            actor_id=self.authority.actor_id,
        )

        result = self.ledger.record_review(
            event,
            authority=self.authority,
        )

        self.assertFalse(result.accepted)
        self.assertIn("review_target_hash_mismatch", result.reasons)

    def test_review_supersession_is_monotonic_and_explicit(self) -> None:
        first = self._review(
            event_id="review_1",
            decision=ReviewDecisionKind.REQUEST_REVISION,
            sequence=1,
        )
        self.assertTrue(
            self.ledger.record_review(
                first,
                authority=self.authority,
            ).accepted
        )
        implicit = self._review(
            event_id="review_2",
            decision=ReviewDecisionKind.APPROVE,
            sequence=2,
        )
        implicit_result = self.ledger.record_review(
            implicit,
            authority=self.authority,
        )
        explicit = self._review(
            event_id="review_3",
            decision=ReviewDecisionKind.APPROVE,
            sequence=2,
            supersedes_event_id="review_1",
        )
        explicit_result = self.ledger.record_review(
            explicit,
            authority=self.authority,
        )

        self.assertFalse(implicit_result.accepted)
        self.assertIn(
            "review_supersedes_active_event_required",
            implicit_result.reasons,
        )
        self.assertTrue(explicit_result.accepted)
        self.assertEqual(
            self.ledger.active_review(self.artifact).decision,
            ReviewDecisionKind.APPROVE,
        )

    def test_evidence_span_is_checked_against_registered_source(self) -> None:
        source = "Measurements show 42 ms at steady state."
        start = source.index("42 ms")
        evidence = EvidenceRef.from_text(
            source_id="measurement_1",
            source_text=source,
            start=start,
            end=start + len("42 ms"),
        )
        finding = FindingRef(
            code="measurement-supported",
            severity="info",
            target_kind="artifact",
            target_id=self.artifact.artifact_id,
            evidence=evidence,
        )
        event = self._review(
            event_id="review_evidence",
            decision=ReviewDecisionKind.APPROVE,
            sequence=1,
            findings=(finding,),
        )

        bad = self.ledger.record_review(
            event,
            authority=self.authority,
            evidence_sources={"measurement_1": source.replace("42", "41")},
        )
        good = self.ledger.record_review(
            event,
            authority=self.authority,
            evidence_sources={"measurement_1": source},
        )

        self.assertFalse(bad.accepted)
        self.assertIn("review_evidence_span_mismatch", bad.reasons)
        self.assertTrue(good.accepted)

    def test_validated_delivery_requires_active_exact_approval(self) -> None:
        unapproved = DeliveryEvent(
            event_id="delivery_1",
            artifact=self.artifact,
            status=DeliveryStatus.VALIDATED,
            sequence=1,
            actor_id="release-controller",
        )
        before = self.ledger.record_delivery(
            unapproved,
            content=self.content,
        )
        review = self._review(
            event_id="review_approve",
            decision=ReviewDecisionKind.APPROVE,
            sequence=1,
        )
        self.assertTrue(
            self.ledger.record_review(
                review,
                authority=self.authority,
            ).accepted
        )
        approved = DeliveryEvent(
            event_id="delivery_2",
            artifact=self.artifact,
            status=DeliveryStatus.VALIDATED,
            sequence=1,
            actor_id="release-controller",
            approved_by_event_id=review.event_id,
        )
        after = self.ledger.record_delivery(
            approved,
            content=self.content,
        )

        self.assertFalse(before.accepted)
        self.assertIn("validated_delivery_review_missing", before.reasons)
        self.assertTrue(after.accepted)

    def test_protocol_is_domain_neutral(self) -> None:
        domains = {
            "clinical-note": "Dose schedule was reconciled.",
            "compiler-report": "The lowered module passed verification.",
            "procurement": "The signed offer matches the frozen specification.",
        }
        for index, (artifact_id, content) in enumerate(domains.items(), start=1):
            with self.subTest(artifact_id=artifact_id):
                ledger = ReliabilityEventLedger()
                artifact = ArtifactRef.from_content(
                    artifact_id=artifact_id,
                    version=1,
                    content=content,
                )
                self.assertTrue(
                    ledger.register_artifact(artifact, content=content).accepted
                )
                event = ReviewDecisionEvent(
                    event_id=f"review_{index}",
                    target=artifact,
                    decision=ReviewDecisionKind.APPROVE,
                    sequence=1,
                    actor_id=self.authority.actor_id,
                )
                self.assertTrue(
                    ledger.record_review(
                        event,
                        authority=self.authority,
                    ).accepted
                )

    def test_reserved_metadata_is_parsed_without_reading_visible_text(
        self,
    ) -> None:
        content = (
            "The prose says rejected, but only the typed event is authoritative."
        )
        artifact = ArtifactRef.from_content(
            artifact_id="artifact_metadata",
            version=1,
            content=content,
            source_id="source_metadata",
        )
        message = {
            "content_text": content,
            "metadata": {
                "agentlite_reliability": {
                    "schema_version": "agentlite.reliability.v1",
                    "artifacts": [
                        {
                            "artifact_id": artifact.artifact_id,
                            "version": artifact.version,
                            "content_hash": artifact.content_hash,
                            "source_id": artifact.source_id,
                        }
                    ],
                    "review_events": [
                        {
                            "event_id": "review_metadata",
                            "target": {
                                "artifact_id": artifact.artifact_id,
                                "version": artifact.version,
                                "content_hash": artifact.content_hash,
                                "source_id": artifact.source_id,
                            },
                            "decision": "approve",
                            "sequence": 1,
                            "actor_id": "QualityGate17",
                        }
                    ],
                }
            },
        }

        parsed = parse_reliability_metadata([message])

        self.assertTrue(parsed.metadata_present)
        self.assertFalse(parsed.errors)
        self.assertEqual(parsed.artifacts[0].content, content)
        self.assertEqual(
            parsed.artifacts[0].artifact.content_hash,
            content_digest(content),
        )
        self.assertEqual(
            parsed.review_events[0].decision,
            ReviewDecisionKind.APPROVE,
        )

    def test_invalid_reserved_metadata_is_fail_closed(self) -> None:
        parsed = parse_reliability_metadata(
            [
                {
                    "content_text": "ordinary output",
                    "metadata": {
                        "agentlite_reliability": {
                            "schema_version": "unknown.version",
                            "review_events": [
                                {
                                    "decision": "block",
                                }
                            ],
                        }
                    },
                }
            ]
        )

        self.assertTrue(parsed.metadata_present)
        self.assertFalse(parsed.review_events)
        self.assertIn(
            "message_0:reliability_schema_unsupported",
            parsed.errors,
        )

    def test_exact_event_replay_is_idempotent(self) -> None:
        review = self._review(
            event_id="review_idempotent",
            decision=ReviewDecisionKind.APPROVE,
            sequence=1,
        )
        first = self.ledger.record_review(
            review,
            authority=self.authority,
        )
        second = self.ledger.record_review(
            review,
            authority=self.authority,
        )

        self.assertTrue(first.accepted)
        self.assertTrue(second.accepted)
        self.assertEqual(second.reasons, ("idempotent_event_replay",))

    def test_review_replay_is_bound_to_original_scope_and_task(self) -> None:
        review = self._review(
            event_id="review_scope_bound",
            decision=ReviewDecisionKind.APPROVE,
            sequence=1,
        )
        first = self.ledger.record_review(
            review,
            authority=self.authority,
            expected_scope_id=self.artifact.scope_id,
            expected_task_id=self.artifact.task_id,
        )
        same_task = self.ledger.record_review(
            review,
            authority=self.authority,
            expected_scope_id=self.artifact.scope_id,
            expected_task_id=self.artifact.task_id,
        )
        wrong_scope = self.ledger.record_review(
            review,
            authority=self.authority,
            expected_scope_id="scope-other",
            expected_task_id=self.artifact.task_id,
        )
        wrong_task = self.ledger.record_review(
            review,
            authority=self.authority,
            expected_scope_id=self.artifact.scope_id,
            expected_task_id="task-other",
        )

        self.assertTrue(first.accepted)
        self.assertTrue(same_task.accepted)
        self.assertEqual(
            same_task.reasons,
            ("idempotent_event_replay",),
        )
        self.assertFalse(wrong_scope.accepted)
        self.assertIn("artifact_scope_mismatch", wrong_scope.reasons)
        self.assertFalse(wrong_task.accepted)
        self.assertIn("artifact_task_mismatch", wrong_task.reasons)

    def test_delivery_replay_is_bound_to_original_scope_and_task(self) -> None:
        review = self._review(
            event_id="review_delivery_scope",
            decision=ReviewDecisionKind.APPROVE,
            sequence=1,
        )
        self.assertTrue(
            self.ledger.record_review(
                review,
                authority=self.authority,
                expected_scope_id=self.artifact.scope_id,
                expected_task_id=self.artifact.task_id,
            ).accepted
        )
        delivery = DeliveryEvent(
            event_id="delivery_scope_bound",
            artifact=self.artifact,
            status=DeliveryStatus.VALIDATED,
            sequence=1,
            actor_id="release-controller",
            approved_by_event_id=review.event_id,
        )
        first = self.ledger.record_delivery(
            delivery,
            content=self.content,
            expected_scope_id=self.artifact.scope_id,
            expected_task_id=self.artifact.task_id,
        )
        wrong_task = self.ledger.record_delivery(
            delivery,
            content=self.content,
            expected_scope_id=self.artifact.scope_id,
            expected_task_id="task-other",
        )

        self.assertTrue(first.accepted)
        self.assertFalse(wrong_task.accepted)
        self.assertIn("artifact_task_mismatch", wrong_task.reasons)

    def test_artifact_registration_requires_runtime_scope_binding(self) -> None:
        ledger = ReliabilityEventLedger()

        wrong_scope = ledger.register_artifact(
            self.artifact,
            content=self.content,
            expected_scope_id="scope-other",
            expected_task_id=self.artifact.task_id,
        )
        wrong_task = ledger.register_artifact(
            self.artifact,
            content=self.content,
            expected_scope_id=self.artifact.scope_id,
            expected_task_id="task-other",
        )

        self.assertFalse(wrong_scope.accepted)
        self.assertIn("artifact_scope_mismatch", wrong_scope.reasons)
        self.assertFalse(wrong_task.accepted)
        self.assertIn("artifact_task_mismatch", wrong_task.reasons)

    def test_delivery_actor_is_bound_to_runtime_identity(self) -> None:
        event = DeliveryEvent(
            event_id="delivery_spoofed",
            artifact=self.artifact,
            status=DeliveryStatus.CANDIDATE,
            sequence=1,
            actor_id="spoofed-actor",
        )

        result = self.ledger.record_delivery(
            event,
            content=self.content,
            authority_actor_id="actual-runtime-actor",
        )

        self.assertFalse(result.accepted)
        self.assertIn("delivery_actor_authority_mismatch", result.reasons)

    def _review(
        self,
        *,
        event_id: str,
        decision: ReviewDecisionKind,
        sequence: int,
        supersedes_event_id: str = "",
        findings: tuple[FindingRef, ...] = (),
    ) -> ReviewDecisionEvent:
        return ReviewDecisionEvent(
            event_id=event_id,
            target=self.artifact,
            decision=decision,
            sequence=sequence,
            actor_id=self.authority.actor_id,
            findings=findings,
            supersedes_event_id=supersedes_event_id,
        )


if __name__ == "__main__":
    unittest.main()
