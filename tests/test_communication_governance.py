import json
import unittest
from pathlib import Path

from agent_runtime.core.agents import build_default_agents
from agent_runtime.core.communication import (
    CapabilityProfileManagerLite,
    CapabilityRouterLite,
    CommunicationGateLite,
    ControlBudgetLite,
)
from agent_runtime.core.runtime import V0Runtime
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.state.state_pool import StateRef


class CommunicationGovernanceTest(unittest.TestCase):
    def test_router_sends_retrieval_state_to_writer(self) -> None:
        profiles = CapabilityProfileManagerLite(build_default_agents())
        router = CapabilityRouterLite(profiles)
        state_ref = StateRef(
            state_id="state_retrieval",
            state_type="retrieval_state",
            version=1,
            payload_kind="structured_non_text",
            contains_embedding_refs=True,
            usage_hint="summary_context_selection",
            tier="hot",
        )

        decision = router.route(
            sender="retriever",
            declared_receiver="reviewer",
            state_refs=[state_ref],
            readiness="ready",
        )

        self.assertEqual(decision.receiver, "writer")
        self.assertEqual(decision.msg_type, "state_ref_handoff")
        self.assertTrue(decision.route_changed)
        self.assertIn("synthesis", decision.capability_hint)

    def test_gate_blocks_when_control_budget_is_exhausted(self) -> None:
        budget = ControlBudgetLite(max_decisions_per_task=1, max_control_tokens_per_task=64)
        first = budget.record_decision(task_id="A1", estimated_control_tokens=1)
        second = budget.record_decision(task_id="A1", estimated_control_tokens=1)
        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)

        profiles = CapabilityProfileManagerLite(build_default_agents())
        route = CapabilityRouterLite(profiles).route(
            sender="writer",
            declared_receiver="reviewer",
            state_refs=[],
            readiness="ready",
        )
        gate = CommunicationGateLite().assess(
            readiness="ready",
            route_decision=route,
            budget_report=second,
        )

        self.assertFalse(gate.allowed)
        self.assertEqual(gate.status, "blocked")
        self.assertEqual(gate.allowed_next_step, "control_budget_review")

    def test_runtime_shp_contains_route_gate_and_budget_metrics(self) -> None:
        trace = TraceLogger(Path("runs/test_communication_governance"))
        runtime = V0Runtime(
            agents=build_default_agents(),
            token_counter=TokenCounter(),
            metrics=MetricsCollector(),
            trace=trace,
        )
        state_ref = StateRef(
            state_id="state_failure",
            state_type="failure_state",
            version=1,
            payload_kind="structured_non_text",
            contains_embedding_refs=False,
            usage_hint="review_or_retry_only",
            tier="hot",
        )

        payload = json.loads(
            runtime._build_shp_message(
                task=type(
                    "Task",
                    (),
                    {"task_id": "A1", "title": "demo", "group_id": "A", "prompt": ""},
                )(),
                round_id=1,
                mode="runtime_lite",
                from_agent="writer",
                next_receiver="runtime",
                summary="bad output",
                state_refs=[state_ref],
                memory_refs=[],
            )
        )

        self.assertEqual(payload["header"]["receiver"], "reviewer")
        self.assertEqual(payload["header"]["msg_type"], "failure_state")
        self.assertEqual(payload["control"]["readiness"], "degraded")
        self.assertEqual(payload["control"]["allowed_next_step"], "review_or_retry_only")
        self.assertIn("route_decision", payload["metrics"])
        self.assertIn("communication_gate", payload["metrics"])
        self.assertIn("control_budget", payload["metrics"])


if __name__ == "__main__":
    unittest.main()
