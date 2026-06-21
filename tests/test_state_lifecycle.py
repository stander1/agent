import tempfile
import unittest
from pathlib import Path

from agent_runtime.core.readiness import ReadinessBarrierLite
from agent_runtime.state.state_pool import (
    ColdAccessBudget,
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


if __name__ == "__main__":
    unittest.main()
