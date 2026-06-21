import tempfile
import unittest
from pathlib import Path

from agent_runtime.core.agents import DeterministicAgent
from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.core.runtime import V0Runtime
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.state.non_text import (
    build_embedding_state_payload,
    build_retrieval_state_payload,
)
from agent_runtime.state.state_pool import StatePoolLite


class RetrieverAgent(DeterministicAgent):
    agent_id = "retriever"
    role = "RetrieverAgent"

    def run(self, task: TaskSpec, context: list[AgentOutput]) -> AgentOutput:
        del context
        return AgentOutput(agent_id=self.agent_id, content=f"retrieved {task.task_id}")


class NonTextStateTest(unittest.TestCase):
    def test_retrieval_payload_carries_embedding_metadata(self) -> None:
        task = TaskSpec(
            task_id="A1",
            group_id="travel_A",
            title="检索任务",
            prompt="检索证据",
            documents=["证据一", "证据二"],
        )

        embedding = build_embedding_state_payload(task)
        retrieval = build_retrieval_state_payload(task, embedding_state_id="state_emb")

        self.assertEqual(embedding["vector_dim"], 384)
        self.assertEqual(len(embedding["chunk_embedding_ids"]), 2)
        self.assertTrue(retrieval["contains_embedding_refs"])
        self.assertEqual(retrieval["embedding_state_id"], "state_emb")
        self.assertEqual(retrieval["query_embedding_id"], "emb_A1_query")
        self.assertEqual(len(retrieval["similarity_scores"]), 2)

    def test_runtime_retriever_writes_embedding_and_retrieval_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            runtime = V0Runtime(
                agents=[RetrieverAgent()],
                token_counter=TokenCounter(allow_estimate=True),
                metrics=MetricsCollector(),
                trace=TraceLogger(output_dir),
                state_pool=StatePoolLite(output_dir),
                memory_store=MemoryStoreLite(),
            )
            task = TaskSpec(
                task_id="A1",
                group_id="travel_A",
                title="检索任务",
                prompt="检索证据",
                documents=["证据一", "证据二"],
            )

            refs = runtime._write_agent_state(
                task,
                round_id=1,
                mode="runtime_lite",
                agent=RetrieverAgent(),
                output=AgentOutput(agent_id="retriever", content="retrieved"),
            )

            self.assertEqual([ref.state_type for ref in refs], ["embedding_state", "retrieval_state"])
            self.assertTrue(all(ref.contains_embedding_refs for ref in refs))
            retrieval_view = runtime.state_pool.render_prompt_view(refs[1], "WriterAgent")
            self.assertIn("query_embedding", retrieval_view)
            self.assertIn("vector_dim=384", retrieval_view)


if __name__ == "__main__":
    unittest.main()
