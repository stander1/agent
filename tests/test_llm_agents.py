from __future__ import annotations

import unittest

from agent_runtime.core.models import TaskSpec
from agent_runtime.llm.agents import is_final_task


class LlmAgentFinalTaskTest(unittest.TestCase):
    def test_first_draft_final_title_is_not_global_final_task(self) -> None:
        self.assertFalse(
            is_final_task(
                TaskSpec(
                    "A5",
                    "travel_A",
                    "\u7b2c\u4e00\u7248\u6700\u7ec8\u65c5\u884c\u624b\u518c\u751f\u6210",
                    "draft manual",
                )
            )
        )

    def test_explicit_final_id_and_final_prefix_are_global_final_tasks(self) -> None:
        self.assertTrue(is_final_task(TaskSpec("A10", "travel_A", "step", "x")))
        self.assertTrue(
            is_final_task(
                TaskSpec(
                    "A9",
                    "travel_A",
                    "\u6700\u7ec8\u4fee\u8ba2\u7248\u65c5\u884c\u624b\u518c\u4e0e\u51b3\u7b56\u65e5\u5fd7",
                    "x",
                )
            )
        )
        self.assertFalse(is_final_task(TaskSpec("A110", "travel_A", "step", "x")))


if __name__ == "__main__":
    unittest.main()
