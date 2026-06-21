import tempfile
import unittest
from pathlib import Path

from agent_runtime.core.readiness import ReadinessBarrierLite
from agent_runtime.state.state_pool import (
    AccessEscalationReport,
    ColdAccessBudget,
    LeaseRegistry,
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

            swept = pool.sweep_tombstones(max_swept=1)
            self.assertEqual(swept.physical_delete_count, 1)
            self.assertEqual(first.lifecycle, "deleted")

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
            pool = StatePoolLite(Path(tmp))
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
                ],
                carried_state_refs=[carried_ref],
            )

            self.assertEqual(retry_ref.state_type, "retry_loop_summary")
            self.assertEqual(pool._states[retry_ref.state_id].lifecycle, "retry_carried")
            view = pool.render_prompt_view(retry_ref, "ReviewerAgent")
            self.assertIn("retry loop summary", view)

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
