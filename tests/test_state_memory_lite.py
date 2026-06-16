import tempfile
import unittest
from pathlib import Path

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
        self.assertIn("结构化 SHP", view)


if __name__ == "__main__":
    unittest.main()
