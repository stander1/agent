import tempfile
import unittest
from pathlib import Path

from agent_runtime.drivers.autogen import _memory_adoption_evidence
from agent_runtime.memory.memory_store import MemoryStoreLite


class FactLevelMemoryTest(unittest.TestCase):
    def _write_claim(
        self,
        store: MemoryStoreLite,
        *,
        task_id: str,
        subject: str = "project:demo",
        slot_id: str = "slot.runtime.config",
        scope: str = "config.busy_timeout",
        value: str,
        value_type: str = "integer",
        unit: str = "ms",
        confidence: float = 0.9,
        revision_kind: str = "asserted",
        operator: str | None = None,
        polarity: str = "positive",
    ):
        claim_card = {
            "subject": subject,
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
            "polarity": polarity,
            "confidence": confidence,
            "revision_kind": revision_kind,
            "source_pointer": f"state:{task_id}",
        }
        if operator is not None:
            claim_card["operator"] = operator
        return store.write_memory_candidate_with_report(
            task_id=task_id,
            source_agent="ConfigurationAgent",
            task_topic=subject,
            memory_card={
                "summary": f"{scope}={value}",
                "confidence": 0.9,
                "importance_hint": 0.8,
                "coverage_score": 0.8,
                "reuse_scope": ["demo-chain"],
                "slot_hint": slot_id,
            },
            claim_cards=[claim_card],
            tags=["demo-chain", task_id],
            slot_hint=slot_id,
            source_state_ids=[f"state_{task_id}"],
            evidence_refs=[f"evidence_{task_id}"],
            reuse_intent="reuse in the same project chain",
            fallback_summary=f"{scope}={value}",
        )

    def test_store_canonicalizes_logical_polarity_from_explicit_operator(
        self,
    ) -> None:
        store = MemoryStoreLite()
        signed = self._write_claim(
            store,
            task_id="P1",
            scope="measurement.offset",
            value="-18.7",
            value_type="number",
            unit="m",
            operator="eq",
            polarity="negative",
        )
        excluded = self._write_claim(
            store,
            task_id="P2",
            scope="measurement.excluded_value",
            value="4",
            value_type="number",
            unit="m",
            operator="ne",
            polarity="positive",
        )

        signed_claim = store._claims[signed.claim_ids[0]]
        excluded_claim = store._claims[excluded.claim_ids[0]]
        self.assertEqual(signed_claim.operator, "eq")
        self.assertEqual(signed_claim.polarity, "positive")
        self.assertEqual(excluded_claim.operator, "ne")
        self.assertEqual(excluded_claim.polarity, "negative")

    def test_same_fact_merges_evidence_and_search_returns_one_view(self) -> None:
        store = MemoryStoreLite()
        first = self._write_claim(store, task_id="D1", value="2000")
        second = self._write_claim(store, task_id="D2", value="2000")

        self.assertEqual(first.memory_view_ids, second.memory_view_ids)
        view = store._views[first.memory_view_ids[0]]
        self.assertEqual(len(view.active_claim_ids), 1)
        self.assertFalse(view.historical_claim_ids)
        self.assertEqual(second.conflict_detected_count, 0)
        self.assertEqual(second.deduplicated_claim_count, 1)
        self.assertEqual(second.deduplicated_memory_count, 1)
        claim = store._claims[view.active_claim_ids[0]]
        self.assertTrue(
            {
                "state_D1",
                "evidence_D1",
                "state_D2",
                "evidence_D2",
            }.issubset(set(claim.supported_by)),
        )

        refs = store.search_memory(
            "busy_timeout configuration",
            tags=["demo-chain"],
            top_k=5,
        )
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].memory_view_id, view.memory_view_id)

    def test_new_value_supersedes_old_value_without_prompt_contamination(self) -> None:
        store = MemoryStoreLite()
        old = self._write_claim(store, task_id="D1", value="5000")
        new = self._write_claim(
            store,
            task_id="D2",
            value="2000",
            revision_kind="replaces",
        )

        self.assertEqual(new.conflict_detected_count, 1)
        self.assertEqual(new.resolved_conflict_count, 1)
        self.assertEqual(new.unresolved_conflict_count, 0)
        self.assertEqual(new.active_value_selection_count, 1)

        prompt = store.render_prompt_view(new.memory_ref, budget_chars=1600)
        guard = store.revision_guard(new.memory_ref)
        self.assertIn("active_value=2000", prompt)
        self.assertNotIn("active_value=5000", prompt)
        self.assertEqual(guard["active_facts"][0]["value"], "2000")
        self.assertEqual(guard["historical_facts"][0]["value"], "5000")
        self.assertEqual(
            store.resolve_ref(old.memory_ref.memory_id).status,
            "superseded",
        )

    def test_scope_and_subject_are_part_of_the_semantic_identity(self) -> None:
        store = MemoryStoreLite()
        first = self._write_claim(
            store,
            task_id="S1",
            subject="project:alpha",
            scope="config.worker_timeout",
            value="2000",
        )
        different_scope = self._write_claim(
            store,
            task_id="S2",
            subject="project:alpha",
            scope="config.connection_timeout",
            value="5000",
        )
        different_subject = self._write_claim(
            store,
            task_id="S3",
            subject="project:beta",
            scope="config.worker_timeout",
            value="8000",
        )

        view_ids = {
            first.memory_view_ids[0],
            different_scope.memory_view_ids[0],
            different_subject.memory_view_ids[0],
        }
        self.assertEqual(len(view_ids), 3)
        self.assertEqual(different_scope.conflict_detected_count, 0)
        self.assertEqual(different_subject.conflict_detected_count, 0)

    def test_unresolved_conflict_is_blocked_from_prompt_retrieval(self) -> None:
        store = MemoryStoreLite()
        first = self._write_claim(
            store,
            task_id="E1",
            slot_id="slot.paper.evidence",
            scope="evidence.result",
            value="supported",
            value_type="string",
            unit="",
            confidence=0.8,
        )
        second = self._write_claim(
            store,
            task_id="E2",
            slot_id="slot.paper.evidence",
            scope="evidence.result",
            value="rejected",
            value_type="string",
            unit="",
            confidence=0.8,
        )

        self.assertEqual(second.conflict_detected_count, 1)
        self.assertEqual(second.resolved_conflict_count, 0)
        self.assertEqual(second.unresolved_conflict_count, 1)
        view = store._views[first.memory_view_ids[0]]
        self.assertEqual(view.resolution_status, "unresolved_conflict")
        self.assertFalse(view.active_claim_ids)
        self.assertEqual(len(view.conflicting_claim_ids), 2)
        self.assertFalse(
            store.search_memory(
                "evidence result",
                tags=["demo-chain"],
                top_k=5,
            )
        )

    def test_required_scope_is_rejected_before_formal_memory_view(self) -> None:
        store = MemoryStoreLite()
        report = self._write_claim(
            store,
            task_id="U1",
            slot_id="slot.project.requirement",
            scope="general",
            value="50",
        )

        self.assertEqual(report.admission_status, "unresolved_scope")
        self.assertEqual(report.unresolved_scope_count, 1)
        self.assertEqual(report.memory_write_count, 0)
        self.assertFalse(report.memory_refs)

    def test_field_level_prompt_and_audit_expansion_are_separate(self) -> None:
        store = MemoryStoreLite()
        old = self._write_claim(store, task_id="F1", value="5000")
        current = self._write_claim(
            store,
            task_id="F2",
            value="2000",
            revision_kind="replaces",
        )
        guard = store.revision_guard(current.memory_ref)
        semantic_key = guard["semantic_key"]
        prompt_views = store.get_prompt_view(semantic_key, budget_chars=1600)
        audit_view = store.get_audit_view(
            current.memory_ref.memory_view_id,
            budget_chars=12000,
        )
        evidence = store.expand_evidence(
            current.memory_ref.memory_view_id,
            store._memories[old.memory_ref.memory_id].claim_id,
            budget_chars=12000,
        )

        self.assertEqual(len(prompt_views), 1)
        self.assertIn("active_value=2000", prompt_views[0])
        self.assertNotIn("active_value=5000", prompt_views[0])
        self.assertIn('"value": "5000"', audit_view)
        self.assertIn('"view_type": "evidence_expansion"', evidence)
        self.assertIn('"value": "5000"', evidence)

    def test_loaded_legacy_document_claim_is_audit_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage_dir = Path(tmp)
            original = MemoryStoreLite(storage_dir=storage_dir)
            report = original.write_memory_with_report(
                task_id="L1",
                source_agent="LegacyWriter",
                task_topic="legacy deliverable",
                summary="The old complete deliverable was stored as one claim.",
                tags=["legacy"],
                slot_hint="final_deliverable",
                confidence=0.8,
                schema_version="ccf.v1-lite",
            )

            restored = MemoryStoreLite(storage_dir=storage_dir)
            restored_ref = restored.resolve_ref(report.memory_ref.memory_id)
            claim = restored._claims[
                restored._memories[report.memory_ref.memory_id].claim_id
            ]

            self.assertIsNotNone(restored_ref)
            self.assertEqual(restored_ref.status, "legacy_audit")
            self.assertEqual(claim.claim_type, "legacy_document_claim")
            self.assertFalse(
                restored.search_memory(
                    "complete deliverable",
                    tags=["legacy"],
                    top_k=5,
                )
            )
            self.assertIn(
                "legacy_document_claim",
                restored.render_audit_view(restored_ref, budget_chars=8000),
            )

    def test_structured_adoption_distinguishes_active_old_and_mixed_values(self) -> None:
        store = MemoryStoreLite()
        self._write_claim(store, task_id="A1", value="5000")
        current = self._write_claim(
            store,
            task_id="A2",
            value="2000",
            revision_kind="replaces",
        )
        prompt = store.render_prompt_view(current.memory_ref, budget_chars=1600)
        guard = store.revision_guard(current.memory_ref)
        common = {
            "memory_prompt_view": prompt,
            "injected_prompt_view": prompt,
            "current_task_text": "Return the final runtime configuration.",
            "memory_id": current.memory_ref.memory_id,
            "memory_view_id": current.memory_ref.memory_view_id,
            "revision_guard": guard,
        }

        useful = _memory_adoption_evidence(
            output_text="Use busy_timeout=2000.",
            **common,
        )
        wrong = _memory_adoption_evidence(
            output_text="Use busy_timeout=5000.",
            **common,
        )
        mixed = _memory_adoption_evidence(
            output_text="Use busy_timeout=2000 and busy_timeout=5000.",
            **common,
        )
        negated_old = _memory_adoption_evidence(
            output_text="Use busy_timeout=2000. Do not use busy_timeout=5000.",
            **common,
        )

        self.assertEqual(useful["status"], "useful")
        self.assertEqual(wrong["status"], "wrong")
        self.assertEqual(mixed["status"], "mixed")
        self.assertEqual(negated_old["status"], "useful")
        self.assertEqual(
            useful["attribution_mode"],
            "ccf_v2_semantic_key_value_rules",
        )

    def test_legacy_document_claim_can_be_audited_without_false_penalty(self) -> None:
        prompt = (
            "[memory_view:view_legacy] slot=slot.system.deliverable_requirement; "
            "claim=claim_new; busy_timeout=2000; tags=[legacy]"
        )
        evidence = _memory_adoption_evidence(
            memory_prompt_view=prompt,
            injected_prompt_view=prompt,
            current_task_text="Return the final configuration.",
            output_text="Use busy_timeout=5000.",
            memory_id="memory_legacy",
            memory_view_id="view_legacy",
            revision_guard={
                "historical_claims": [
                    {
                        "claim_id": "claim_old_document",
                        "summary": "busy_timeout=5000",
                        "exclude_from_negative_attribution": True,
                    }
                ]
            },
        )

        self.assertNotEqual(evidence["status"], "wrong")
        self.assertEqual(
            evidence["excluded_legacy_historical_claim_count"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
