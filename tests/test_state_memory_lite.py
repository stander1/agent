import tempfile
import unittest
from pathlib import Path

from agent_runtime.core.deliverable_schema import schema_coverage, schema_for_task
from agent_runtime.core.models import TaskSpec
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.state.state_pool import StatePoolLite


class StatePoolLiteTest(unittest.TestCase):
    def test_write_and_render_retrieval_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(Path(tmp))
            state_ref, state = pool.write_state(
                task_id="T1",
                source_agent="retriever",
                state_type="retrieval_state",
                payload={
                    "chunk_ids": ["c1"],
                    "source_ids": ["s1"],
                    "score_map": {"c1": 0.9},
                    "evidence_rank": ["c1"],
                    "chunks": {"c1": {"source_id": "s1", "text": "结构化通信证据"}},
                },
                summary="检索状态",
                usage_hint="summary_context_selection",
            )

            self.assertEqual(state_ref.state_type, "retrieval_state")
            self.assertEqual(state_ref.tier, "hot")
            self.assertEqual(state.tier, "hot")
            self.assertGreater(state.size_bytes, 0)
            view = pool.render_prompt_view(state_ref, "WriterAgent")
            self.assertIn("retrieval_state", view)
            self.assertIn("结构化通信证据", view)

    def test_artifact_raw_content_is_cold_audit_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(Path(tmp))
            raw_content = "完整产物正文不应该进入普通 Prompt View。"
            state_ref, state = pool.write_state(
                task_id="T1",
                source_agent="writer",
                state_type="artifact_state",
                payload={
                    "artifact_id": "artifact_T1_writer",
                    "sha256": "abc123",
                    "summary": "产物摘要",
                },
                summary="writer 产物状态",
                usage_hint="artifact_summary",
                tier="cold",
                access_policy="prompt_view_with_audit_cold_access",
                audit_payload={"content": raw_content},
            )

            self.assertEqual(state_ref.tier, "cold")
            self.assertEqual(state.tier, "cold")
            self.assertIsNotNone(state.audit_payload_ref)
            prompt_view = pool.render_prompt_view(state_ref, "ReviewerAgent")
            self.assertIn("raw_content=cold_audit_only", prompt_view)
            self.assertNotIn(raw_content, prompt_view)
            audit_payload = pool.load_audit_payload(state_ref)
            self.assertIsNotNone(audit_payload)
            self.assertEqual(audit_payload["content"], raw_content)


class MemoryStoreLiteTest(unittest.TestCase):
    def test_write_search_and_render_memory(self) -> None:
        store = MemoryStoreLite()
        ref = store.write_memory(
            task_id="T1",
            source_agent="writer",
            task_topic="低开销通信",
            summary="结构化 SHP 可以减少 Agent 间长文本传递。",
            tags=["A", "communication"],
        )

        hits = store.search_memory("结构化通信如何减少长文本", tags=["A"])
        self.assertTrue(hits)
        self.assertEqual(hits[0].memory_id, ref.memory_id)
        view = store.render_prompt_view(hits[0])
        self.assertIn("memory_view", view)
        self.assertIn("结构化 SHP", view)

    def test_claim_memory_view_and_deliverable_view(self) -> None:
        store = MemoryStoreLite()
        report = store.write_memory_with_report(
            task_id="A10",
            source_agent="writer",
            task_topic="最终修订版旅行手册与决策日志",
            summary="最终手册需要覆盖预算表、修订日志和决策日志。",
            tags=["travel_A", "A10", "writer"],
            slot_hint="final_deliverable",
            source_state_ids=["state_a", "state_b"],
        )

        self.assertEqual(report.claim_card_count, 1)
        self.assertEqual(report.memory_view_count, 1)
        self.assertEqual(report.promotion_view_count, 1)
        self.assertEqual(report.unresolved_slot_count, 0)
        self.assertEqual(report.memory_ref.slot_id, "slot.system.deliverable_requirement")

        hits = store.search_memory("最终手册预算表决策日志", tags=["travel_A"])
        self.assertTrue(hits)
        deliverable_view = store.render_deliverable_view(
            hits,
            task_title="最终修订版旅行手册与决策日志",
        )
        self.assertIn("Deliverable View", deliverable_view)
        self.assertIn("预算表", deliverable_view)


class DeliverableSchemaTest(unittest.TestCase):
    def test_travel_final_task_has_required_schema(self) -> None:
        task = TaskSpec(
            task_id="A10",
            group_id="travel_A",
            title="最终修订版旅行手册与决策日志",
            prompt="生成最终旅行手册",
        )
        schema = schema_for_task(task)
        self.assertIsNotNone(schema)
        self.assertIn("budget_table", schema.required_fields)
        hits, required = schema_coverage(
            schema,
            "selected_destination duration day1_plan day2_plan day3_plan "
            "itinerary_table budget_table total_budget transport_plan lodging_plan local_food_plan "
            "non_spicy_option souvenir_budget weather_fallback "
            "motion_sickness_guard decision_log",
        )
        self.assertEqual(hits, required)
        chinese_hits, chinese_required = schema_coverage(
            schema,
            "最终旅行手册包含每日行程表、预算表、目的地、3 天 2 晚、第一天、第二天、"
            "第三天、总预算、交通、住宿、当地特色餐、不吃辣、伴手礼、雨天备选、"
            "晕车和决策日志。",
        )
        self.assertEqual(chinese_hits, chinese_required)

    def test_first_draft_final_title_does_not_trigger_final_schema(self) -> None:
        task = TaskSpec(
            task_id="A5",
            group_id="travel_A",
            title="第一版最终旅行手册生成",
            prompt="生成第一版旅行手册",
        )
        self.assertIsNone(schema_for_task(task))

    def test_security_final_task_has_required_schema(self) -> None:
        task = TaskSpec(
            task_id="B10",
            group_id="security_B",
            title="最终审计手册与系统决策日志",
            prompt="生成最终合成安全审计手册",
        )
        schema = schema_for_task(task)
        self.assertIsNotNone(schema)
        self.assertIn("evidence_refs", schema.required_fields)


if __name__ == "__main__":
    unittest.main()
