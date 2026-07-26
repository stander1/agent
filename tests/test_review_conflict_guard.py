from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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
    evaluate_review_conflict,
)


class ReviewConflictGuardTest(unittest.TestCase):
    def test_budget_extraction_prefers_currency_amount_over_party_count(self) -> None:
        cases = (
            ("预算：两人所有费用总计不超过3000元。", "3000", "CNY"),
            ("总预算：两人合计2600元。", "2600", "CNY"),
            ("budget for 2 people is USD 500.", "500", "USD"),
            ("预算为3000。", "3000", ""),
        )
        for text, expected_value, expected_unit in cases:
            with self.subTest(text=text):
                claims = self._budget_claims(text)
                self.assertEqual(len(claims), 1)
                self.assertEqual(claims[0]["value"], expected_value)
                self.assertEqual(claims[0]["unit"], expected_unit)

        self.assertFalse(self._budget_claims("预算需要两人共同确认。"))

    def test_budget_attribution_does_not_treat_party_count_as_old_budget(self) -> None:
        store = MemoryStoreLite()
        self._write_fact(
            store,
            task_id="T1",
            slot_id="slot.project.requirement",
            scope="constraint.budget",
            value="2",
            value_type="number",
            unit="CNY",
        )
        current = self._write_fact(
            store,
            task_id="T2",
            slot_id="slot.project.requirement",
            scope="constraint.budget",
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

            report = manager._apply_review_conflict_governance(
                context=context,
                output_text=(
                    "Validation failed. Option Alpha violates the hard "
                    "latency constraint and must be replaced."
                ),
                state_refs=state_refs,
                injected_records=[record],
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

    @staticmethod
    def _budget_claims(text: str) -> list[dict[str, object]]:
        return [
            claim
            for claim in extract_claim_cards(text, subject="project:demo")
            if claim["scope"] == "constraint.budget"
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
