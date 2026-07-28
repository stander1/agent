from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runtime.bootstrap.startup import BootstrapContext
from agent_runtime.core.kernel import AgentDescriptor
from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.drivers.autogen import (
    AutoGenHookManager,
    HookCallContext,
    _InjectedMemoryRecord,
    _memory_adoption_evidence,
)
from agent_runtime.memory.claim_extractor import extract_claim_cards
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.reliability.review_conflict_guard import (
    build_review_blocker_claim,
    decision_from_typed_review_event,
    evaluate_review_conflict,
)
from agent_runtime.reliability.typed_events import (
    ArtifactRef,
    DeliveryStatus,
    FindingRef,
    ReviewDecisionEvent,
    ReviewDecisionKind,
)


class ReviewConflictGuardTest(unittest.TestCase):
    def test_budget_extraction_prefers_currency_amount_over_party_count(self) -> None:
        cases = (
            (
                "预算：两人所有费用总计不超过3000元。",
                "3000",
                "CNY",
                "constraint.budget_upper_bound",
            ),
            (
                "总预算：两人合计2600元。",
                "2600",
                "CNY",
                "estimate.budget_total",
            ),
            (
                "budget for 2 people is USD 500.",
                "500",
                "USD",
                "constraint.budget_upper_bound",
            ),
            (
                "预算为3000。",
                "3000",
                "",
                "constraint.budget_upper_bound",
            ),
        )
        for text, expected_value, expected_unit, expected_scope in cases:
            with self.subTest(text=text):
                claims = self._budget_claims(text)
                self.assertEqual(len(claims), 1)
                self.assertEqual(claims[0]["value"], expected_value)
                self.assertEqual(claims[0]["unit"], expected_unit)
                self.assertEqual(claims[0]["scope"], expected_scope)

        self.assertFalse(self._budget_claims("预算需要两人共同确认。"))

    def test_budget_attribution_does_not_treat_party_count_as_old_budget(self) -> None:
        store = MemoryStoreLite()
        self._write_fact(
            store,
            task_id="T1",
            slot_id="slot.project.requirement",
            scope="constraint.budget_upper_bound",
            value="2",
            value_type="number",
            unit="CNY",
        )
        current = self._write_fact(
            store,
            task_id="T2",
            slot_id="slot.project.requirement",
            scope="constraint.budget_upper_bound",
            value="3000",
            value_type="number",
            unit="CNY",
            revision_kind="replaces",
        )
        ref = current.memory_ref
        prompt_view = store.render_prompt_view(ref, budget_chars=1600)
        evidence = _memory_adoption_evidence(
            memory_prompt_view=prompt_view,
            injected_prompt_view=prompt_view,
            current_task_text="请完成当前方案。",
            output_text="预算：两人所有费用总计不超过3000元。",
            memory_id=ref.memory_id,
            memory_view_id=ref.memory_view_id,
            revision_guard=store.revision_guard(ref),
        )

        self.assertEqual(evidence["status"], "useful")
        self.assertEqual(evidence["matched_historical_fact_count"], 0)

    def test_dynamic_review_authority_targets_only_referenced_active_fact(self) -> None:
        rows = [
            self._memory_row(
                memory_id="mem_choice",
                claim_id="claim_choice",
                semantic_key="project:demo|plan.selected_option",
                slot_id="slot.system.design_decision",
                scope="plan.selected_option",
                value="Option Alpha",
            ),
            self._memory_row(
                memory_id="mem_budget",
                claim_id="claim_budget",
                semantic_key="project:demo|constraint.budget",
                slot_id="slot.project.requirement",
                scope="constraint.budget",
                value="3000",
                value_type="number",
                unit="CNY",
            ),
        ]
        decision = evaluate_review_conflict(
            output_text=(
                "Validation failed. Option Alpha violates the hard latency "
                "constraint and must be replaced."
            ),
            semantic_action="REVIEW_OUTPUT",
            capabilities=("validation",),
            memory_rows=rows,
        )

        self.assertTrue(decision.authoritative)
        self.assertTrue(decision.blocking)
        self.assertEqual(decision.targeted_memory_ids, ("mem_choice",))
        self.assertNotIn("mem_budget", decision.targeted_memory_ids)

    def test_review_authority_does_not_depend_on_agent_name(self) -> None:
        rows = [
            self._memory_row(
                memory_id="mem_choice",
                claim_id="claim_choice",
                semantic_key="project:demo|plan.selected_option",
                slot_id="slot.system.design_decision",
                scope="plan.selected_option",
                value="Option Alpha",
            )
        ]
        dynamic = evaluate_review_conflict(
            output_text=(
                "审查不通过：Option Alpha 违反硬约束，必须更换。"
            ),
            semantic_action="HANDLE_TASK",
            capabilities=("final_deliverable_review",),
            memory_rows=rows,
        )
        ordinary = evaluate_review_conflict(
            output_text=(
                "审查不通过：Option Alpha 违反硬约束，必须更换。"
            ),
            semantic_action="WRITE_OUTPUT",
            capabilities=("writing",),
            memory_rows=rows,
        )
        approved = evaluate_review_conflict(
            output_text="审查通过，未发现阻断问题，Option Alpha 可以继续使用。",
            semantic_action="REVIEW_OUTPUT",
            capabilities=("validation",),
            memory_rows=rows,
        )

        self.assertTrue(dynamic.authoritative)
        self.assertTrue(dynamic.blocking)
        self.assertFalse(ordinary.authoritative)
        self.assertFalse(ordinary.blocking)
        self.assertTrue(approved.authoritative)
        self.assertFalse(approved.blocking)

    def test_soft_deprecation_refreshes_shared_memory_view(self) -> None:
        store = MemoryStoreLite()
        report = self._write_fact(
            store,
            task_id="T1",
            slot_id="slot.system.design_decision",
            scope="plan.selected_option",
            value="Option Alpha",
        )
        ref = report.memory_ref

        store.soft_deprecate(
            ref,
            reason="authoritative_review_blocked",
            created_by="QualityGate17",
        )
        guard = store.revision_guard(ref)

        self.assertFalse(guard["active_facts"])
        self.assertEqual(guard["historical_facts"][0]["value"], "Option Alpha")
        self.assertFalse(
            store.get_prompt_view(guard["semantic_key"], budget_chars=1200)
        )

    def test_manager_persists_blocker_and_soft_deprecates_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AutoGenHookManager(self._context(root))
            task = TaskSpec(
                task_id="T2",
                group_id="generic-chain",
                title="Validate prior option",
                prompt="Review the prior option against current constraints.",
            )
            old = self._write_fact(
                manager.kernel.memory_store,
                task_id="T1",
                slot_id="slot.system.design_decision",
                scope="plan.selected_option",
                value="Option Alpha",
            )
            old_ref = old.memory_ref
            record = _InjectedMemoryRecord(
                ref=old_ref,
                prompt_view=manager.kernel.memory_store.render_prompt_view(old_ref),
                current_task_text=task.prompt,
                current_task_source="team_task_by_group",
                injected_prompt_view="Option Alpha is the active prior option.",
                revision_guard=manager.kernel.memory_store.revision_guard(old_ref),
            )
            state_refs = manager.kernel.write_agent_state(
                task=task,
                round_id=1,
                mode="runtime_lite",
                agent=AgentDescriptor(
                    agent_id="QualityGate17",
                    role="CustomAgent",
                    capabilities=("validation",),
                    preferred_actions=("REVIEW_OUTPUT",),
                ),
                output=AgentOutput(
                    agent_id="QualityGate17",
                    content=(
                        "Validation failed. Option Alpha violates the hard "
                        "latency constraint and must be replaced."
                    ),
                ),
            )
            context = HookCallContext(
                call_id="call_review",
                task=task,
                agent=AgentDescriptor(
                    agent_id="QualityGate17",
                    role="CustomAgent",
                    capabilities=("validation",),
                    preferred_actions=("REVIEW_OUTPUT",),
                ),
                method_name="on_messages",
                target_kind="agentchat_agent",
                semantic_action="REVIEW_OUTPUT",
            )
            artifact_content = "Option Alpha is the submitted artifact."
            artifact = ArtifactRef.from_content(
                artifact_id="artifact_option_alpha",
                version=1,
                content=artifact_content,
                source_id="state_option_alpha",
                scope_id=task.group_id,
                task_id=task.task_id,
            )
            author_context = HookCallContext(
                call_id="call_author",
                task=task,
                agent=AgentDescriptor(
                    agent_id="DomainAuthor42",
                    role="CustomAgent",
                    capabilities=("writing",),
                    preferred_actions=("WRITE_OUTPUT",),
                ),
                method_name="on_messages",
                target_kind="agentchat_agent",
                semantic_action="WRITE_OUTPUT",
            )
            manager._process_typed_reliability_metadata(
                context=author_context,
                decoded_messages=[
                    manager.codec.decode(
                        {
                            "source": "DomainAuthor42",
                            "content": artifact_content,
                            "metadata": {
                                "agentlite_reliability": {
                                    "schema_version": (
                                        "agentlite.reliability.v1"
                                    ),
                                    "artifacts": [
                                        {
                                            "artifact_id": (
                                                artifact.artifact_id
                                            ),
                                            "version": artifact.version,
                                            "content_hash": (
                                                artifact.content_hash
                                            ),
                                            "source_id": artifact.source_id,
                                            "scope_id": artifact.scope_id,
                                            "task_id": artifact.task_id,
                                        }
                                    ],
                                }
                            },
                        }
                    )
                ],
                semantic_state_text=artifact_content,
                state_refs=[],
            )
            active_claim_id = (
                manager.kernel.memory_store.revision_guard(old_ref)[
                    "active_facts"
                ][0]["claim_id"]
            )
            review_processing = (
                manager._process_typed_reliability_metadata(
                    context=context,
                    decoded_messages=[
                        manager.codec.decode(
                            {
                                "source": "QualityGate17",
                                "content": (
                                    "Typed review details are carried in "
                                    "metadata."
                                ),
                                "metadata": {
                                    "agentlite_reliability": {
                                        "schema_version": (
                                            "agentlite.reliability.v1"
                                        ),
                                        "review_events": [
                                            {
                                                "event_id": "review_option",
                                                "target": {
                                                    "artifact_id": (
                                                        artifact.artifact_id
                                                    ),
                                                    "version": (
                                                        artifact.version
                                                    ),
                                                    "content_hash": (
                                                        artifact.content_hash
                                                    ),
                                                    "source_id": (
                                                        artifact.source_id
                                                    ),
                                                    "scope_id": (
                                                        artifact.scope_id
                                                    ),
                                                    "task_id": (
                                                        artifact.task_id
                                                    ),
                                                },
                                                "decision": (
                                                    "request_revision"
                                                ),
                                                "sequence": 1,
                                                "actor_id": "QualityGate17",
                                                "findings": [
                                                    {
                                                        "code": (
                                                            "constraint-failed"
                                                        ),
                                                        "severity": "blocking",
                                                        "target_kind": "claim",
                                                        "target_id": active_claim_id,
                                                        "summary": (
                                                            "Option Alpha "
                                                            "requires revision."
                                                        ),
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                },
                            }
                        )
                    ],
                    semantic_state_text=(
                        "Typed review details are carried in metadata."
                    ),
                    state_refs=state_refs,
                )
            )

            report = manager._apply_review_conflict_governance(
                context=context,
                output_text=(
                    "Validation failed. Option Alpha violates the hard "
                    "latency constraint and must be replaced."
                ),
                state_refs=state_refs,
                injected_records=[record],
                typed_processing=review_processing,
            )

            self.assertIsNotNone(report)
            self.assertEqual(report.admission_status, "admitted")
            self.assertEqual(
                manager.kernel.memory_store.resolve_ref(old_ref.memory_id).status,
                "deprecated",
            )
            blocker_ref = report.memory_refs[0]
            blocker_guard = manager.kernel.memory_store.revision_guard(blocker_ref)
            self.assertEqual(
                blocker_guard["active_facts"][0]["slot_id"],
                "slot.system.failure_pattern",
            )
            self.assertIn(
                "Option Alpha",
                manager.kernel.memory_store.render_prompt_view(
                    blocker_ref,
                    budget_chars=1600,
                ),
            )
            events = [
                json.loads(line)
                for line in (manager.output_dir / "trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            governance = [
                row
                for row in events
                if row["event_type"] == "autogen_review_conflict_governance"
            ][-1]["payload"]
            self.assertEqual(
                governance["deprecated_memory_ids"],
                [old_ref.memory_id],
            )
            self.assertEqual(
                governance["blocker_admission_status"],
                "admitted",
            )

    def test_manager_does_not_mutate_memory_from_legacy_review_text(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = AutoGenHookManager(self._context(Path(tmp)))
            task = TaskSpec(
                task_id="T2",
                group_id="generic-chain",
                title="Validate prior artifact",
                prompt="Validate the prior artifact.",
            )
            old = self._write_fact(
                manager.kernel.memory_store,
                task_id="T1",
                slot_id="slot.system.design_decision",
                scope="plan.selected_option",
                value="Option Alpha",
            )
            old_ref = old.memory_ref
            record = _InjectedMemoryRecord(
                ref=old_ref,
                prompt_view="Option Alpha",
                current_task_text=task.prompt,
                current_task_source="team_task_by_group",
                injected_prompt_view="Option Alpha",
                revision_guard=(
                    manager.kernel.memory_store.revision_guard(old_ref)
                ),
            )
            context = HookCallContext(
                call_id="call_legacy_review",
                task=task,
                agent=AgentDescriptor(
                    agent_id="QualityGate17",
                    role="CustomAgent",
                    capabilities=("validation",),
                    preferred_actions=("REVIEW_OUTPUT",),
                ),
                method_name="on_messages",
                target_kind="agentchat_agent",
                semantic_action="REVIEW_OUTPUT",
            )

            report = manager._apply_review_conflict_governance(
                context=context,
                output_text=(
                    "Validation failed. Option Alpha violates a hard "
                    "constraint and must be replaced."
                ),
                state_refs=[],
                injected_records=[record],
            )

            self.assertIsNone(report)
            self.assertEqual(
                manager.kernel.memory_store.resolve_ref(
                    old_ref.memory_id
                ).status,
                "active",
            )
            events = [
                json.loads(line)
                for line in (manager.output_dir / "trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            governance = [
                row["payload"]
                for row in events
                if row["event_type"]
                == "autogen_review_conflict_governance"
            ][-1]
            self.assertEqual(
                governance["decision_source"],
                "legacy_text_inference",
            )
            self.assertFalse(governance["mutation_authorized"])
            self.assertFalse(governance["deprecated_memory_ids"])

    def test_blocker_claim_is_domain_neutral(self) -> None:
        decision = evaluate_review_conflict(
            output_text=(
                "Validation failed. Option Alpha violates the hard latency "
                "constraint and must be replaced."
            ),
            semantic_action="REVIEW_OUTPUT",
            capabilities=("validation",),
            memory_rows=[
                self._memory_row(
                    memory_id="mem_choice",
                    claim_id="claim_choice",
                    semantic_key="project:demo|plan.selected_option",
                    slot_id="slot.system.design_decision",
                    scope="plan.selected_option",
                    value="Option Alpha",
                )
            ],
        )
        claim = build_review_blocker_claim(
            decision,
            subject="collaboration:generic-chain",
            source_pointer="state_1",
        )

        self.assertEqual(claim["slot_id"], "slot.system.failure_pattern")
        self.assertTrue(claim["scope"].startswith("review.blocking."))
        self.assertEqual(claim["temporal_scope"], "cross_task")

    def test_typed_review_targets_exact_memory_without_text_inference(
        self,
    ) -> None:
        rows = [
            self._memory_row(
                memory_id="mem_a",
                claim_id="claim_a",
                semantic_key="project:demo|arbitrary_field",
                slot_id="slot.open.arbitrary_field",
                scope="general",
                value="alpha",
            ),
            self._memory_row(
                memory_id="mem_b",
                claim_id="claim_b",
                semantic_key="project:demo|other_field",
                slot_id="slot.open.other_field",
                scope="general",
                value="beta",
            ),
        ]
        event = ReviewDecisionEvent(
            event_id="review_typed",
            target=ArtifactRef.from_content(
                artifact_id="artifact_1",
                version=1,
                content="artifact content",
            ),
            decision=ReviewDecisionKind.REQUEST_REVISION,
            sequence=1,
            actor_id="QualityGate17",
            findings=(
                FindingRef(
                    code="unsupported-claim",
                    severity="blocking",
                    target_kind="claim",
                    target_id="claim_a",
                    summary="Evidence does not support this claim.",
                ),
            ),
        )

        decision = decision_from_typed_review_event(
            event,
            memory_rows=rows,
        )

        self.assertTrue(decision.authoritative)
        self.assertTrue(decision.blocking)
        self.assertTrue(decision.mutation_authorized)
        self.assertEqual(
            decision.decision_source,
            "typed_reliability_event",
        )
        self.assertEqual(decision.targeted_memory_ids, ("mem_a",))
        self.assertNotIn("mem_b", decision.targeted_memory_ids)

    def test_legacy_review_is_observation_only_by_default(self) -> None:
        decision = evaluate_review_conflict(
            output_text=(
                "Validation failed. Option Alpha violates a hard constraint "
                "and must be replaced."
            ),
            semantic_action="REVIEW_OUTPUT",
            capabilities=("validation",),
            memory_rows=[
                self._memory_row(
                    memory_id="mem_choice",
                    claim_id="claim_choice",
                    semantic_key="project:demo|plan.selected_option",
                    slot_id="slot.system.design_decision",
                    scope="plan.selected_option",
                    value="Option Alpha",
                )
            ],
        )

        self.assertTrue(decision.blocking)
        self.assertFalse(decision.mutation_authorized)
        self.assertEqual(
            decision.decision_source,
            "legacy_text_inference",
        )

    def test_typed_delivery_resolves_exact_approved_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = AutoGenHookManager(self._context(Path(tmp)))
            manager.shared_memory_enabled = True
            task = TaskSpec(
                task_id="T-delivery",
                group_id="generic-delivery-chain",
                title="Produce final note",
                prompt="Provide the final deployment note.",
            )
            artifact_content = (
                "Final deployment note: the signed build is ready for "
                "the staged rollout."
            )
            artifact = ArtifactRef.from_content(
                artifact_id="artifact_final_note",
                version=2,
                content=artifact_content,
                source_id="DomainAuthor42",
                scope_id=task.group_id,
                task_id=task.task_id,
            )
            author_context = HookCallContext(
                call_id="call_delivery_author",
                task=task,
                agent=AgentDescriptor(
                    agent_id="DomainAuthor42",
                    role="AnyRole",
                    capabilities=("writing",),
                    preferred_actions=("WRITE_OUTPUT",),
                ),
                method_name="on_messages",
                target_kind="agentchat_agent",
                semantic_action="WRITE_OUTPUT",
            )
            manager._process_typed_reliability_metadata(
                context=author_context,
                decoded_messages=[
                    manager.codec.decode(
                        {
                            "source": "DomainAuthor42",
                            "content": artifact_content,
                            "metadata": {
                                "agentlite_reliability": {
                                    "schema_version": (
                                        "agentlite.reliability.v1"
                                    ),
                                    "artifacts": [
                                        {
                                            "artifact_id": (
                                                artifact.artifact_id
                                            ),
                                            "version": artifact.version,
                                            "content_hash": (
                                                artifact.content_hash
                                            ),
                                            "source_id": artifact.source_id,
                                            "scope_id": artifact.scope_id,
                                            "task_id": artifact.task_id,
                                        }
                                    ],
                                }
                            },
                        }
                    )
                ],
                semantic_state_text=artifact_content,
                state_refs=[],
            )
            gate_context = HookCallContext(
                call_id="call_delivery_gate",
                task=task,
                agent=AgentDescriptor(
                    agent_id="QualityGate17",
                    role="AnyRole",
                    capabilities=("validation",),
                    preferred_actions=("REVIEW_OUTPUT",),
                ),
                method_name="on_messages",
                target_kind="agentchat_agent",
                semantic_action="REVIEW_OUTPUT",
            )
            typed_processing = manager._process_typed_reliability_metadata(
                context=gate_context,
                decoded_messages=[
                    manager.codec.decode(
                        {
                            "source": "QualityGate17",
                            "content": "Control events are in metadata.",
                            "metadata": {
                                "agentlite_reliability": {
                                    "schema_version": (
                                        "agentlite.reliability.v1"
                                    ),
                                    "review_events": [
                                        {
                                            "event_id": "review_final_note",
                                            "target": {
                                                "artifact_id": (
                                                    artifact.artifact_id
                                                ),
                                                "version": artifact.version,
                                                "content_hash": (
                                                    artifact.content_hash
                                                ),
                                                "source_id": (
                                                    artifact.source_id
                                                ),
                                                "scope_id": (
                                                    artifact.scope_id
                                                ),
                                                "task_id": (
                                                    artifact.task_id
                                                ),
                                            },
                                            "decision": "approve",
                                            "sequence": 1,
                                            "actor_id": "QualityGate17",
                                        }
                                    ],
                                    "delivery_events": [
                                        {
                                            "event_id": "delivery_final_note",
                                            "artifact": {
                                                "artifact_id": (
                                                    artifact.artifact_id
                                                ),
                                                "version": artifact.version,
                                                "content_hash": (
                                                    artifact.content_hash
                                                ),
                                                "source_id": (
                                                    artifact.source_id
                                                ),
                                                "scope_id": (
                                                    artifact.scope_id
                                                ),
                                                "task_id": (
                                                    artifact.task_id
                                                ),
                                            },
                                            "status": (
                                                DeliveryStatus.VALIDATED.value
                                            ),
                                            "sequence": 1,
                                            "actor_id": "QualityGate17",
                                            "approved_by_event_id": (
                                                "review_final_note"
                                            ),
                                        }
                                    ],
                                }
                            },
                        }
                    )
                ],
                semantic_state_text="Control events are in metadata.",
                state_refs=[],
            )
            team_agent = AgentDescriptor(
                agent_id="team-controller",
                role="Team",
                capabilities=("orchestration",),
                preferred_actions=("ROUTE_TASK",),
            )
            team_context = HookCallContext(
                call_id="call_delivery_team",
                task=task,
                agent=team_agent,
                method_name="run_stream",
                target_kind="agentchat_team",
                semantic_action="HANDLE_TASK",
            )
            state_refs = manager.kernel.write_agent_state(
                task=task,
                round_id=1,
                mode="runtime_lite",
                agent=team_agent,
                output=AgentOutput(
                    agent_id=team_agent.agent_id,
                    content="Aggregated team result.",
                ),
            )

            report = manager._promote_autogen_output_to_memory(
                context=team_context,
                decoded_messages=[
                    manager.codec.decode(
                        {
                            "source": "QualityGate17",
                            "content": "Approved.",
                        }
                    )
                ],
                text="QualityGate17: Approved.",
                state_refs=state_refs,
                typed_processing=typed_processing,
            )

            self.assertIsNotNone(report)
            events = [
                json.loads(line)
                for line in (manager.output_dir / "trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            candidate = [
                row["payload"]
                for row in events
                if row["event_type"] == "autogen_memory_candidate"
            ][-1]
            self.assertEqual(
                candidate["candidate_kind"],
                "autogen_team_final",
            )
            self.assertEqual(
                candidate["resolution_kind"],
                "typed_validated_delivery",
            )
            self.assertEqual(
                candidate["typed_delivery_event_id"],
                "delivery_final_note",
            )
            self.assertEqual(
                candidate["resolved_candidate_source"],
                "DomainAuthor42",
            )

    def test_typed_delivery_content_is_isolated_by_task_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = AutoGenHookManager(self._context(Path(tmp)))
            manager.shared_memory_enabled = True
            first_task = TaskSpec(
                task_id="task-one",
                group_id="shared-group",
                title="First task",
                prompt="Deliver the first signed note.",
            )
            second_task = TaskSpec(
                task_id="task-two",
                group_id="shared-group",
                title="Second task",
                prompt="Deliver the second signed note.",
            )
            first_content = (
                "First signed note contains the exact alpha payload."
            )
            second_content = (
                "Second signed note contains the exact beta payload."
            )
            first_artifact = ArtifactRef.from_content(
                artifact_id="reused-artifact-name",
                version=1,
                content=first_content,
                source_id="FlexibleAuthor",
                scope_id=first_task.group_id,
                task_id=first_task.task_id,
            )
            second_artifact = ArtifactRef.from_content(
                artifact_id="reused-artifact-name",
                version=1,
                content=second_content,
                source_id="FlexibleAuthor",
                scope_id=second_task.group_id,
                task_id=second_task.task_id,
            )

            def author_context(task: TaskSpec) -> HookCallContext:
                return HookCallContext(
                    call_id=f"author-{task.task_id}",
                    task=task,
                    agent=AgentDescriptor(
                        agent_id="FlexibleAuthor",
                        role="ArbitraryRole",
                        capabilities=("writing",),
                        preferred_actions=("WRITE_OUTPUT",),
                    ),
                    method_name="on_messages",
                    target_kind="agentchat_agent",
                    semantic_action="WRITE_OUTPUT",
                )

            def artifact_message(
                artifact: ArtifactRef,
                content: str,
            ) -> list[object]:
                return [
                    manager.codec.decode(
                        {
                            "source": "FlexibleAuthor",
                            "content": content,
                            "metadata": {
                                "agentlite_reliability": {
                                    "schema_version": (
                                        "agentlite.reliability.v1"
                                    ),
                                    "artifacts": [
                                        {
                                            "artifact_id": (
                                                artifact.artifact_id
                                            ),
                                            "version": artifact.version,
                                            "content_hash": (
                                                artifact.content_hash
                                            ),
                                            "source_id": artifact.source_id,
                                            "scope_id": artifact.scope_id,
                                            "task_id": artifact.task_id,
                                        }
                                    ],
                                }
                            },
                        }
                    )
                ]

            manager._process_typed_reliability_metadata(
                context=author_context(first_task),
                decoded_messages=artifact_message(
                    first_artifact,
                    first_content,
                ),
                semantic_state_text=first_content,
                state_refs=[],
            )
            gate_context = HookCallContext(
                call_id="gate-task-one",
                task=first_task,
                agent=AgentDescriptor(
                    agent_id="FlexibleGate",
                    role="ArbitraryRole",
                    capabilities=("validation",),
                    preferred_actions=("REVIEW_OUTPUT",),
                ),
                method_name="on_messages",
                target_kind="agentchat_agent",
                semantic_action="REVIEW_OUTPUT",
            )
            first_processing = manager._process_typed_reliability_metadata(
                context=gate_context,
                decoded_messages=[
                    manager.codec.decode(
                        {
                            "source": "FlexibleGate",
                            "content": "Typed control metadata.",
                            "metadata": {
                                "agentlite_reliability": {
                                    "schema_version": (
                                        "agentlite.reliability.v1"
                                    ),
                                    "review_events": [
                                        {
                                            "event_id": "review-task-one",
                                            "target": {
                                                "artifact_id": (
                                                    first_artifact.artifact_id
                                                ),
                                                "version": (
                                                    first_artifact.version
                                                ),
                                                "content_hash": (
                                                    first_artifact.content_hash
                                                ),
                                                "source_id": (
                                                    first_artifact.source_id
                                                ),
                                                "scope_id": (
                                                    first_artifact.scope_id
                                                ),
                                                "task_id": (
                                                    first_artifact.task_id
                                                ),
                                            },
                                            "decision": "approve",
                                            "sequence": 1,
                                            "actor_id": "FlexibleGate",
                                        }
                                    ],
                                    "delivery_events": [
                                        {
                                            "event_id": "delivery-task-one",
                                            "artifact": {
                                                "artifact_id": (
                                                    first_artifact.artifact_id
                                                ),
                                                "version": (
                                                    first_artifact.version
                                                ),
                                                "content_hash": (
                                                    first_artifact.content_hash
                                                ),
                                                "source_id": (
                                                    first_artifact.source_id
                                                ),
                                                "scope_id": (
                                                    first_artifact.scope_id
                                                ),
                                                "task_id": (
                                                    first_artifact.task_id
                                                ),
                                            },
                                            "status": "validated",
                                            "sequence": 1,
                                            "actor_id": "FlexibleGate",
                                            "approved_by_event_id": (
                                                "review-task-one"
                                            ),
                                        }
                                    ],
                                }
                            },
                        }
                    )
                ],
                semantic_state_text="Typed control metadata.",
                state_refs=[],
            )
            manager._process_typed_reliability_metadata(
                context=author_context(second_task),
                decoded_messages=artifact_message(
                    second_artifact,
                    second_content,
                ),
                semantic_state_text=second_content,
                state_refs=[],
            )

            team_agent = AgentDescriptor(
                agent_id="FlexibleTeam",
                role="ArbitraryTeamRole",
                capabilities=("orchestration",),
                preferred_actions=("ROUTE_TASK",),
            )
            team_context = HookCallContext(
                call_id="team-task-one",
                task=first_task,
                agent=team_agent,
                method_name="run_stream",
                target_kind="agentchat_team",
                semantic_action="HANDLE_TASK",
            )
            state_refs = manager.kernel.write_agent_state(
                task=first_task,
                round_id=1,
                mode="runtime_lite",
                agent=team_agent,
                output=AgentOutput(
                    agent_id=team_agent.agent_id,
                    content="Aggregated result.",
                ),
            )

            with patch.object(
                manager.kernel,
                "promote_memory_candidate",
                return_value=None,
            ) as promote:
                manager._promote_autogen_output_to_memory(
                    context=team_context,
                    decoded_messages=[
                        manager.codec.decode(
                            {
                                "source": "FlexibleGate",
                                "content": "Approved.",
                            }
                        )
                    ],
                    text="FlexibleGate: Approved.",
                    state_refs=state_refs,
                    typed_processing=first_processing,
                )

            promoted_summary = promote.call_args.kwargs["summary"]
            self.assertEqual(
                manager._reliability_artifact_contents[
                    first_artifact.identity
                ],
                first_content,
            )
            self.assertEqual(
                manager._reliability_artifact_contents[
                    second_artifact.identity
                ],
                second_content,
            )
            self.assertIn("alpha payload", promoted_summary)
            self.assertNotIn("beta payload", promoted_summary)

    @staticmethod
    def _budget_claims(text: str) -> list[dict[str, object]]:
        return [
            claim
            for claim in extract_claim_cards(text, subject="project:demo")
            if "budget" in str(claim["scope"])
        ]

    @staticmethod
    def _memory_row(
        *,
        memory_id: str,
        claim_id: str,
        semantic_key: str,
        slot_id: str,
        scope: str,
        value: str,
        value_type: str = "string",
        unit: str = "",
    ) -> dict[str, object]:
        return {
            "memory_id": memory_id,
            "revision_guard": {
                "subject": "project:demo",
                "active_facts": [
                    {
                        "memory_id": memory_id,
                        "claim_id": claim_id,
                        "semantic_key": semantic_key,
                        "slot_id": slot_id,
                        "scope": scope,
                        "value": value,
                        "value_type": value_type,
                        "unit": unit,
                        "polarity": "positive",
                    }
                ],
            },
        }

    @staticmethod
    def _write_fact(
        store: MemoryStoreLite,
        *,
        task_id: str,
        slot_id: str,
        scope: str,
        value: str,
        value_type: str = "string",
        unit: str = "",
        revision_kind: str = "asserted",
    ):
        return store.write_memory_candidate_with_report(
            task_id=task_id,
            source_agent="FactAgent",
            task_topic="project:demo",
            memory_card={
                "summary": f"{scope}={value}",
                "confidence": 0.92,
                "importance_hint": 0.84,
                "coverage_score": 0.80,
                "reuse_scope": ["generic-chain"],
                "slot_hint": slot_id,
            },
            claim_cards=[
                {
                    "subject": "project:demo",
                    "raw_slot_text": scope,
                    "slot_id": slot_id,
                    "scope": scope,
                    "value": value,
                    "value_type": value_type,
                    "unit": unit,
                    "raw_text": f"{scope}={value}",
                    "summary": f"{scope}={value}",
                    "certainty": "confirmed",
                    "modality": "asserted",
                    "polarity": "positive",
                    "confidence": 0.92,
                    "revision_kind": revision_kind,
                    "temporal_scope": "cross_task",
                    "source_pointer": f"state:{task_id}",
                }
            ],
            tags=["generic-chain", task_id],
            slot_hint=slot_id,
            source_state_ids=[f"state_{task_id}"],
            evidence_refs=[f"state_{task_id}"],
            reuse_intent="reuse in the same generic task chain",
            fallback_summary=f"{scope}={value}",
        )

    @staticmethod
    def _context(root: Path) -> BootstrapContext:
        status_file = root / "sessions" / "launch_review" / "bootstrap_status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        target_cwd = root / "workspace"
        target_cwd.mkdir(exist_ok=True)
        return BootstrapContext(
            framework="autogen",
            session_id="launch_review",
            data_dir=root,
            status_file=status_file,
            target_cwd=target_cwd,
        )


if __name__ == "__main__":
    unittest.main()
