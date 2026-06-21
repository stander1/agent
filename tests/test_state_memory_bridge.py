import unittest

from agent_runtime.bridge.state_memory_bridge import StateToMemoryBridgeLite
from agent_runtime.memory.memory_store import MemoryStoreLite


class StateToMemoryBridgeLiteTest(unittest.TestCase):
    def test_bridge_promotes_valid_state_into_admitted_memory_candidate(self) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(store)

        report, validation = bridge.promote(
            task_id="A1",
            source_agent="writer",
            task_topic="travel plan",
            fallback_summary="保留预算约束和交通偏好，供后续行程复用。",
            tags=["travel_A", "A1", "writer"],
            slot_hint="travel_preference",
            source_state_ids=["state_writer"],
            evidence_refs=["state_writer"],
            reuse_intent="供 travel_A 后续连续任务复用",
            control=None,
            degraded=False,
        )

        self.assertTrue(validation.allowed)
        self.assertEqual(report.admission_status, "admitted")
        self.assertEqual(report.memory_write_count, 1)
        self.assertEqual(report.promotion_view_count, 1)
        self.assertIsNotNone(report.memory_ref)

    def test_degraded_contract_becomes_audit_only_candidate(self) -> None:
        store = MemoryStoreLite()
        bridge = StateToMemoryBridgeLite(store)

        report, validation = bridge.promote(
            task_id="B1",
            source_agent="reviewer",
            task_topic="security audit",
            fallback_summary="控制头降级，只允许审查或重试。",
            tags=["security_B", "B1", "reviewer"],
            slot_hint="security_audit",
            source_state_ids=["state_failure"],
            evidence_refs=["state_failure"],
            reuse_intent="供 security_B 后续连续任务复用",
            control=None,
            degraded=True,
        )

        self.assertTrue(validation.allowed)
        self.assertEqual(report.admission_status, "audit_only")
        self.assertEqual(report.memory_write_count, 0)
        self.assertIsNone(report.memory_ref)


if __name__ == "__main__":
    unittest.main()

