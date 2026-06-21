import json
import unittest

from agent_runtime.protocol.shp import PROTOCOL_VERSION, build_handoff_envelope
from agent_runtime.state.state_pool import StateRef


class SHPProtocolTest(unittest.TestCase):
    def test_handoff_envelope_has_header_control_refs_and_metrics(self) -> None:
        state_ref = StateRef(
            state_id="state_a",
            state_type="retrieval_state",
            version=1,
            payload_kind="structured_non_text",
            contains_embedding_refs=True,
            usage_hint="summary_context_selection",
            tier="hot",
        )

        envelope = build_handoff_envelope(
            task_id="A1",
            round_id=1,
            sender="retriever",
            receiver="writer",
            summary="retrieval complete",
            action="retriever_completed",
            state_refs=[state_ref],
            memory_refs=[],
            metrics={"state_ref_count": 1},
            capability_hint=["writer"],
        )
        payload = json.loads(envelope.to_json())

        self.assertEqual(payload["header"]["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(payload["header"]["msg_type"], "agent_output")
        self.assertEqual(payload["control"]["action"], "retriever_completed")
        self.assertEqual(payload["control"]["readiness"], "ready")
        self.assertEqual(payload["state_refs"][0]["state_id"], "state_a")
        self.assertTrue(payload["state_refs"][0]["contains_embedding_refs"])
        self.assertEqual(payload["metrics"]["state_ref_count"], 1)


if __name__ == "__main__":
    unittest.main()

