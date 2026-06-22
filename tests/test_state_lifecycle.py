import tempfile
import unittest
from pathlib import Path

from agent_runtime.core.readiness import ReadinessBarrierLite
from agent_runtime.state.state_pool import (
    AccessEscalationReport,
    ColdAccessBudget,
    LeaseRegistry,
    LoopBudgetConfig,
    StateAdmissionPolicy,
    StateRef,
    StatePoolLite,
    StateQuotaConfig,
)


class StateLifecycleTest(unittest.TestCase):
    def test_quota_gc_logically_evicts_then_sweeps_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(
                Path(tmp),
                quota_config=StateQuotaConfig(max_task_states=1, max_task_bytes=10_000),
            )
            first_ref, first = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "a1"},
                summary="old cold state",
                usage_hint="artifact_summary",
                tier="cold",
                audit_payload={"content": "old"},
            )
            pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "a2"},
                summary="new cold state",
                usage_hint="artifact_summary",
                tier="cold",
                audit_payload={"content": "new"},
            )

            report = pool.collect_garbage(max_deleted=1)
            self.assertEqual(report.quota_evicted_count, 1)
            self.assertEqual(first.lifecycle, "evicted")
            self.assertIn(first_ref.state_id, pool._tombstones)

            swept = pool.sweep_tombstones(max_swept=1, min_age_seconds=0)
            self.assertEqual(swept.physical_delete_count, 1)
            self.assertEqual(first.lifecycle, "deleted")
            self.assertNotIn(first_ref.state_id, pool._states)
            self.assertIn("state_tombstone", pool.render_prompt_view(first_ref, "ReviewerAgent"))

    def test_cold_access_budget_allows_then_blocks_raw_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(
                Path(tmp),
                cold_access_budget=ColdAccessBudget(max_cold_reads_per_task=1),
            )
            state_ref, _ = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "a1"},
                summary="cold artifact",
                usage_hint="artifact_summary",
                tier="cold",
                audit_payload={"content": "raw artifact"},
            )

            first = pool.request_cold_access(state_ref, reason="review_raw")
            second = pool.request_cold_access(state_ref, reason="review_raw_again")

            self.assertTrue(first.allowed)
            self.assertEqual(first.payload["content"], "raw artifact")
            self.assertFalse(second.allowed)
            self.assertEqual(second.reason, "cold_read_count_budget_exceeded")

    def test_cold_access_uses_raw_view_cache_after_first_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(
                Path(tmp),
                cold_access_budget=ColdAccessBudget(max_cold_reads_per_task=2),
            )
            state_ref, _ = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "a1"},
                summary="cold artifact",
                usage_hint="artifact_summary",
                tier="cold",
                audit_payload={"content": "raw artifact"},
            )

            first = pool.request_cold_access(state_ref, reason="review_raw")
            second = pool.request_cold_access(state_ref, reason="review_raw_again")

            self.assertTrue(first.allowed)
            self.assertFalse(first.cache_hit)
            self.assertTrue(second.allowed)
            self.assertTrue(second.cache_hit)

    def test_progressive_access_escalates_only_when_raw_is_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(
                Path(tmp),
                cold_access_budget=ColdAccessBudget(max_cold_reads_per_task=1),
            )
            state_ref, _ = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "a1"},
                summary="cold artifact",
                usage_hint="artifact_summary",
                tier="cold",
                audit_payload={"content": "raw artifact"},
            )

            summary_first = pool.request_progressive_access(
                state_ref,
                agent_role="ReviewerAgent",
                reason="inspect_summary",
                need_raw=False,
            )
            full_read = pool.request_progressive_access(
                state_ref,
                agent_role="ReviewerAgent",
                reason="inspect_raw",
                need_raw=True,
            )

            self.assertIsInstance(summary_first, AccessEscalationReport)
            self.assertEqual(summary_first.selected_level, "summary")
            self.assertEqual(summary_first.reason, "prompt_view_sufficient")
            self.assertEqual(full_read.selected_level, "full_cold_read")
            self.assertTrue(full_read.cold_access.allowed)
            self.assertEqual(full_read.cold_access.payload["content"], "raw artifact")

    def test_retry_loop_summary_is_compacted_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(
                Path(tmp),
                loop_budget_config=LoopBudgetConfig(
                    max_attempts=2,
                    max_carried_states=1,
                    keep_last_k_attempts=1,
                ),
            )
            carried_ref, _ = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "a1"},
                summary="carried",
                usage_hint="artifact_summary",
                tier="warm",
            )
            retry_ref = pool.write_retry_loop_summary(
                task_id="T1",
                source_agent="runtime",
                attempts=[
                    {"attempt_id": 1, "status": "failed", "summary": "bad json"},
                    {"attempt_id": 2, "status": "fixed", "summary": "valid json"},
                    {"attempt_id": 3, "status": "fixed", "summary": "valid json again"},
                ],
                carried_state_refs=[carried_ref],
            )

            self.assertEqual(retry_ref.state_type, "retry_loop_summary")
            self.assertEqual(pool._states[retry_ref.state_id].lifecycle, "retry_carried")
            view = pool.render_prompt_view(retry_ref, "ReviewerAgent")
            self.assertIn("retry loop summary", view)
            payload = Path(pool._states[retry_ref.state_id].payload_ref).read_text(encoding="utf-8")
            self.assertIn("max_attempts_exceeded", payload)
            self.assertIn('"compressed_attempt_count": 1', payload)

    def test_state_admission_scores_low_value_states_as_audit_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(
                Path(tmp),
                admission_policy=StateAdmissionPolicy(admit_threshold=0.5),
            )
            rejected = pool.assess_state_admission(
                state_type="artifact_state",
                payload_bytes=1024,
                downstream_need=0.1,
                confidence=0.2,
                novelty=0.1,
            )
            state_ref, state = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "low_value"},
                summary="low value artifact",
                usage_hint="artifact_summary",
                downstream_need=0.1,
                confidence=0.2,
                novelty=0.1,
            )

            self.assertFalse(rejected.admitted)
            self.assertEqual(rejected.status, "audit_only")
            self.assertEqual(state_ref.state_type, "artifact_state")
            self.assertEqual(state.admission_status, "audit_only")
            self.assertLess(state.admission_score, 0.5)
            payload = Path(state.payload_ref).read_text(encoding="utf-8")
            self.assertIn('"admission_status": "audit_only"', payload)
            self.assertNotIn('"artifact_id": "low_value"', payload)

    def test_fencing_token_validation_rejects_released_and_stale_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(Path(tmp))
            state_ref, state = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "a1"},
                summary="artifact",
                usage_hint="artifact_summary",
                tier="cold",
            )
            lease = pool.leases.acquire_read_lease(state_ref.state_id)
            self.assertTrue(
                pool.validate_fencing_token(
                    state_ref, lease.fencing_token, expected_version=state.version
                )
            )
            state.version += 1
            self.assertFalse(
                pool.validate_fencing_token(
                    state_ref, lease.fencing_token, expected_version=state_ref.version
                )
            )
            pool.leases.release_read_lease(lease.fencing_token)
            self.assertFalse(pool.validate_fencing_token(state_ref, lease.fencing_token))

    def test_state_lifecycle_sweep_and_supersede_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(Path(tmp))
            old_ref, old_state = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "old"},
                summary="old artifact",
                usage_hint="artifact_summary",
                tier="cold",
            )
            report = pool.apply_lifecycle_transitions(
                dormant_after_seconds=0,
                outdated_after_seconds=999999,
            )
            self.assertEqual(report.lifecycle_transition_count, 1)
            self.assertEqual(old_state.lifecycle, "dormant")

            new_ref, _ = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "new"},
                summary="new artifact",
                usage_hint="artifact_summary",
                tier="cold",
            )
            transition = pool.supersede_state(old_ref, new_ref, reason="newer_artifact")
            self.assertTrue(transition.transitioned)
            self.assertEqual(old_state.lifecycle, "outdated")
            self.assertEqual(old_state.replacement_state_id, new_ref.state_id)

    def test_cold_access_chunk_index_span_prefetch_and_expected_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(
                Path(tmp),
                cold_access_budget=ColdAccessBudget(max_cold_reads_per_task=3),
            )
            state_ref, _ = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={"artifact_id": "a1"},
                summary="cold artifact",
                usage_hint="artifact_summary",
                tier="cold",
                audit_payload={
                    "content": "alpha evidence. needle finding is here. beta appendix.",
                    "stderr": "no error",
                },
            )

            chunks = pool.build_raw_chunk_index(state_ref, chunk_chars=24)
            span = pool.resolve_raw_span(state_ref, query="needle finding", max_chunks=1)
            prefetch = pool.prefetch_raw_view([state_ref], query="needle", max_chunks=1)
            access = pool.request_progressive_access(
                state_ref,
                agent_role="ReviewerAgent",
                reason="verify needle",
                need_raw=True,
                expected_summary_tokens=10,
                expected_request_tokens=10,
                expected_raw_tokens=100,
                expected_recovery_tokens=20,
                raw_need_probability=1.0,
            )

            self.assertGreaterEqual(len(chunks), 1)
            self.assertIn("needle", span.content)
            self.assertEqual(prefetch.prefetched_count, 1)
            self.assertIsInstance(access, AccessEscalationReport)
            self.assertEqual(access.decision_basis, "raw_first_expected_cost_model")
            self.assertEqual(access.reason, "raw_first_expected_cost_lower")
            self.assertTrue(access.cold_access.chunk_ids)

    def test_gc_protects_retry_and_dag_live_states_and_prunes_io_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(
                Path(tmp),
                quota_config=StateQuotaConfig(max_task_states=1, max_task_bytes=10_000),
                cold_access_budget=ColdAccessBudget(max_raw_view_tokens=1, max_cold_reads_per_task=3),
            )
            retry_ref, retry_state = pool.write_state(
                task_id="T1",
                source_agent="runtime",
                state_type="artifact_state",
                payload={"artifact_id": "retry"},
                summary="retry protected",
                usage_hint="artifact_summary",
                tier="cold",
                audit_payload={"content": "retry raw"},
                retry_ref_count=1,
            )
            dag_ref, dag_state = pool.write_state(
                task_id="T1",
                source_agent="runtime",
                state_type="artifact_state",
                payload={"artifact_id": "dag"},
                summary="dag protected",
                usage_hint="artifact_summary",
                tier="cold",
                audit_payload={"content": "dag raw"},
                dependency_ref_count=1,
            )
            pool._raw_view_cache["oversized"] = (100, {"content": "x" * 100})

            report = pool.collect_garbage(max_deleted=2)

            self.assertGreaterEqual(report.retry_carried_protected_count, 1)
            self.assertGreaterEqual(report.dag_liveness_protected_count, 1)
            self.assertGreaterEqual(report.raw_cache_evicted_count, 1)
            self.assertNotEqual(retry_state.lifecycle, "evicted")
            self.assertNotEqual(dag_state.lifecycle, "evicted")
            self.assertIn(retry_ref.state_id, pool._states)
            self.assertIn(dag_ref.state_id, pool._states)


class ReadinessBarrierTest(unittest.TestCase):
    def test_readiness_blocks_missing_state_and_degrades_failure_state(self) -> None:
        barrier = ReadinessBarrierLite()
        blocked = barrier.assess(state_refs=[])
        degraded = barrier.assess(
            state_refs=[
                StateRef(
                    state_id="state_fail",
                    state_type="failure_state",
                    version=1,
                    payload_kind="structured_non_text",
                    contains_embedding_refs=False,
                    usage_hint="review_or_retry_only",
                    tier="hot",
                )
            ],
            degraded=True,
        )

        self.assertEqual(blocked.readiness, "blocked")
        self.assertEqual(degraded.readiness, "degraded")
        self.assertEqual(degraded.allowed_next_step, "review_or_retry_only")


class ReadLeaseProtocolTest(unittest.TestCase):
    def test_read_lease_heartbeat_release_and_ttl_recovery(self) -> None:
        leases = LeaseRegistry(default_ttl_seconds=0.01)
        record = leases.acquire_read_lease("state_1", owner="reader")

        self.assertEqual(leases.active_readers("state_1"), 1)
        self.assertTrue(leases.heartbeat(record.fencing_token))
        self.assertIn(record.fencing_token, leases.active_fencing_tokens("state_1"))
        self.assertTrue(leases.release_read_lease(record.fencing_token))
        self.assertEqual(leases.active_readers("state_1"), 0)

        expired = leases.acquire_read_lease("state_1", owner="reader", ttl_seconds=0.0)
        self.assertEqual(leases.active_readers("state_1"), 0)
        self.assertFalse(leases.heartbeat(expired.fencing_token))


if __name__ == "__main__":
    unittest.main()
