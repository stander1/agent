from __future__ import annotations

import unittest

from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.eval.llm_benchmark_runner import build_deliverable_record


class DeliverablePersistenceTests(unittest.TestCase):
    def test_builds_auditable_deliverable_record(self) -> None:
        task = TaskSpec(
            task_id="A10",
            group_id="travel",
            title="最终交付",
            prompt="生成完整方案",
        )
        output = AgentOutput(
            agent_id="writer",
            content="完整最终方案正文",
            metadata={
                "llm": {"model": "mimo-v2.5"},
                "deliverable_role": "final_answer",
                "deliverable_roles": {
                    "draft_answer": {
                        "agent_id": "writer",
                        "content": "writer 初稿",
                        "content_chars": len("writer 初稿"),
                    },
                    "review_report": {
                        "agent_id": "reviewer",
                        "content": "reviewer 审查报告",
                        "content_chars": len("reviewer 审查报告"),
                    },
                    "final_answer": {
                        "agent_id": "writer",
                        "content": "完整最终方案正文",
                        "content_chars": len("完整最终方案正文"),
                    },
                },
            },
        )

        record = build_deliverable_record(
            task=task,
            round_id=1,
            mode="runtime_lite",
            output=output,
        )

        self.assertEqual(record["task_id"], "A10")
        self.assertEqual(record["mode"], "runtime_lite")
        self.assertEqual(record["agent_id"], "writer")
        self.assertEqual(record["deliverable_role"], "final_answer")
        self.assertEqual(record["content"], "完整最终方案正文")
        self.assertEqual(record["content_chars"], len("完整最终方案正文"))
        self.assertEqual(record["draft_answer"]["agent_id"], "writer")
        self.assertEqual(record["review_report"]["agent_id"], "reviewer")
        self.assertEqual(record["final_answer"]["content"], "完整最终方案正文")
        self.assertEqual(record["metadata"]["llm"]["model"], "mimo-v2.5")


if __name__ == "__main__":
    unittest.main()
