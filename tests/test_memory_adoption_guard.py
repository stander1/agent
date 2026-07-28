import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from agent_runtime.bootstrap.startup import BootstrapContext
from agent_runtime.core.kernel import AgentDescriptor
from agent_runtime.core.models import TaskSpec
from agent_runtime.drivers.autogen import (
    AutoGenHookManager,
    HookCallContext,
    _InjectedMemoryRecord,
    _structured_memory_adoption_evidence,
)
from agent_runtime.memory.memory_store import MemoryRef, MemoryStoreLite
from agent_runtime.reliability.memory_adoption_guard import (
    guard_memory_adoption_output,
)
from web_monitor.parser import _autogen_token_summary


@dataclass
class ArbitraryTextMessage:
    content: str
    source: str
    type: str = "TextMessage"


class MemoryAdoptionGuardTest(unittest.TestCase):
    def test_signed_scalar_is_not_treated_as_logical_negation(self) -> None:
        signed = _structured_memory_adoption_evidence(
            revision_guard={
                "subject": "project:generic",
                "semantic_key": "generic|slot.open.offset|general",
                "active_facts": [
                    {
                        "slot_id": "slot.open.offset",
                        "scope": "general",
                        "value": "-18.7",
                        "value_type": "number",
                        "unit": "kilometres per second",
                        "polarity": "negative",
                        "operator": "eq",
                    }
                ],
                "historical_facts": [],
            },
            current_task_text="Use the earlier validated observation.",
            output_text=(
                "The current observation is -18.7 kilometres per second."
            ),
            explicit_reference=False,
        )
        boolean_false = _structured_memory_adoption_evidence(
            revision_guard={
                "subject": "project:generic",
                "semantic_key": "generic|slot.open.flag|general",
                "active_facts": [
                    {
                        "slot_id": "slot.open.flag",
                        "scope": "general",
                        "value": "false",
                        "value_type": "boolean",
                        "unit": "",
                        "polarity": "negative",
                        "operator": "eq",
                    }
                ],
                "historical_facts": [],
            },
            current_task_text="Use the earlier validated flag.",
            output_text="The current feature flag remains false.",
            explicit_reference=False,
        )
        logical_negative = _structured_memory_adoption_evidence(
            revision_guard={
                "subject": "project:generic",
                "semantic_key": "generic|slot.open.state|general",
                "active_facts": [
                    {
                        "slot_id": "slot.open.state",
                        "scope": "general",
                        "value": "dormant",
                        "value_type": "string",
                        "unit": "",
                        "polarity": "positive",
                        "operator": "ne",
                    }
                ],
                "historical_facts": [],
            },
            current_task_text="Use the earlier state constraint.",
            output_text="The state is not dormant.",
            explicit_reference=False,
        )

        self.assertEqual(signed["status"], "useful")
        self.assertEqual(boolean_false["status"], "useful")
        self.assertEqual(logical_negative["status"], "unassessed")

    def test_identical_active_and_historical_facts_are_not_a_conflict(self) -> None:
        evidence = _structured_memory_adoption_evidence(
            revision_guard={
                "subject": "project:generic",
                "semantic_key": (
                    "project:generic|slot.runtime.config|config.alert_id"
                ),
                "active_facts": [
                    {
                        "slot_id": "slot.runtime.config",
                        "scope": "config.alert_id",
                        "value": "alert_071",
                        "value_type": "string",
                        "unit": "",
                        "polarity": "positive",
                    }
                ],
                "historical_facts": [
                    {
                        "slot_id": "slot.runtime.config",
                        "scope": "config.alert_id",
                        "value": "alert_071",
                        "value_type": "string",
                        "unit": "",
                        "polarity": "positive",
                    }
                ],
            },
            current_task_text="Publish the current alert configuration.",
            output_text="Use alert_id=alert_071 in the final configuration.",
            explicit_reference=False,
        )

        self.assertEqual(evidence["status"], "useful")
        self.assertEqual(evidence["historical_fact_count"], 0)
        self.assertEqual(evidence["matched_historical_fact_count"], 0)

    def test_historical_value_provided_by_current_task_is_not_stale_adoption(
        self,
    ) -> None:
        evidence = _structured_memory_adoption_evidence(
            revision_guard={
                "subject": "project:generic",
                "semantic_key": "generic|slot.open.threshold|general",
                "active_facts": [
                    {
                        "slot_id": "slot.open.threshold",
                        "scope": "general",
                        "value": "31.8",
                        "value_type": "number",
                        "unit": "units",
                        "polarity": "positive",
                        "operator": "eq",
                    }
                ],
                "historical_facts": [
                    {
                        "slot_id": "slot.open.threshold",
                        "scope": "general",
                        "value": "32.4",
                        "value_type": "number",
                        "unit": "units",
                        "polarity": "positive",
                        "operator": "eq",
                    }
                ],
            },
            current_task_text=(
                "Publish a two-state record: current threshold 31.8 units; "
                "archived threshold 32.4 units."
            ),
            output_text=(
                "Current threshold: 31.8 units. "
                "Archived threshold: 32.4 units."
            ),
            explicit_reference=False,
        )
        decision = guard_memory_adoption_output(
            output_text=(
                "Current threshold: 31.8 units. "
                "Archived threshold: 32.4 units."
            ),
            evidence_rows=[evidence],
        )

        self.assertEqual(evidence["status"], "unassessed")
        self.assertEqual(evidence["matched_historical_fact_count"], 0)
        self.assertEqual(
            evidence["historical_current_task_duplicate_fact_count"],
            1,
        )
        self.assertEqual(decision.status, "safe")

    def test_unrequested_historical_value_remains_blocked(self) -> None:
        evidence = _structured_memory_adoption_evidence(
            revision_guard={
                "subject": "project:generic",
                "semantic_key": "generic|slot.open.threshold|general",
                "active_facts": [
                    {
                        "slot_id": "slot.open.threshold",
                        "scope": "general",
                        "value": "31.8",
                        "value_type": "number",
                        "unit": "units",
                        "polarity": "positive",
                        "operator": "eq",
                    }
                ],
                "historical_facts": [
                    {
                        "slot_id": "slot.open.threshold",
                        "scope": "general",
                        "value": "32.4",
                        "value_type": "number",
                        "unit": "units",
                        "polarity": "positive",
                        "operator": "eq",
                    }
                ],
            },
            current_task_text="Publish only the current threshold.",
            output_text="Current threshold: 32.4 units.",
            explicit_reference=False,
        )

        self.assertEqual(evidence["status"], "wrong")
        self.assertEqual(evidence["matched_historical_fact_count"], 1)

    def test_guard_ignores_unsafe_row_when_historical_value_equals_active(self) -> None:
        decision = guard_memory_adoption_output(
            output_text="Use alert_id=alert_071.",
            evidence_rows=[
                self._structured_row(
                    status="mixed",
                    active_value="alert_071",
                    historical_values=["alert_071"],
                    historical_spans=["Use alert_id=alert_071."],
                )
            ],
        )

        self.assertEqual(decision.status, "safe")
        self.assertEqual(decision.output_text, "Use alert_id=alert_071.")

    def test_partial_span_repair_is_blocked(self) -> None:
        decision = guard_memory_adoption_output(
            output_text=(
                "Apply service.capacity=20. "
                "Keep the legacy backlog at thirty."
            ),
            evidence_rows=[
                self._structured_row(
                    status="mixed",
                    active_value="50",
                    historical_values=["20", "30"],
                    historical_spans=[
                        "Apply service.capacity=20.",
                        "Keep the legacy backlog at thirty.",
                    ],
                )
            ],
        )

        self.assertEqual(decision.status, "blocked")
        self.assertIn("incomplete_historical_span_repair", decision.reasons)

    def test_rule_repair_replaces_only_the_historical_value_in_matched_span(
        self,
    ) -> None:
        decision = guard_memory_adoption_output(
            output_text=(
                "Legacy note ID 20 remains for audit. "
                "Apply service.capacity=20 before launch."
            ),
            evidence_rows=[
                self._structured_row(
                    status="wrong",
                    active_value="50",
                    historical_values=["20"],
                    historical_spans=[
                        "Apply service.capacity=20 before launch."
                    ],
                )
            ],
        )

        self.assertEqual(decision.status, "rule_repaired")
        self.assertIn("Legacy note ID 20", decision.output_text)
        self.assertIn("service.capacity=50", decision.output_text)
        self.assertEqual(decision.repaired_fact_count, 1)

    def test_mixed_output_is_normalized_to_the_active_value(self) -> None:
        decision = guard_memory_adoption_output(
            output_text=(
                "Primary service.capacity=50; fallback service.capacity=20."
            ),
            evidence_rows=[
                self._structured_row(
                    status="mixed",
                    active_value="50",
                    historical_values=["20"],
                    historical_spans=["fallback service.capacity=20."],
                )
            ],
        )

        self.assertEqual(decision.status, "rule_repaired")
        self.assertNotIn("capacity=20", decision.output_text)
        self.assertEqual(decision.output_text.count("capacity=50"), 2)

    def test_uncertain_conflict_is_blocked_without_replaying_original_body(
        self,
    ) -> None:
        decision = guard_memory_adoption_output(
            output_text="Deploy the legacy plan and expose its confidential body.",
            evidence_rows=[
                {
                    "status": "wrong",
                    "attribution_mode": "legacy_fuzzy_rules",
                    "semantic_key": "arbitrary.domain.release_channel",
                    "active_value": "stable",
                }
            ],
        )

        self.assertEqual(decision.status, "blocked")
        self.assertIn("Runtime safety hold", decision.output_text)
        self.assertNotIn("AGENTLITE_", decision.output_text)
        self.assertEqual(
            decision.to_dict()["allowed_next_step"],
            "review_or_retry_only",
        )
        self.assertNotIn("confidential body", decision.output_text)

    def test_arbitrary_agent_output_is_repaired_before_it_is_recorded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "generic-memory-guard",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(
                    self._bootstrap_context(root, "launch_guard")
                )
                context = HookCallContext(
                    call_id="call_arbitrary_specialist",
                    task=TaskSpec(
                        task_id="generic-2",
                        group_id="generic-chain",
                        title="Publish settings",
                        prompt="Publish the current service settings.",
                    ),
                    agent=AgentDescriptor(
                        "CapacitySpecialist",
                        "ArbitrarySpecialist",
                    ),
                    method_name="on_messages_stream",
                    target_kind="agentchat_agent",
                )
                manager._memory_injections_by_call[context.call_id] = [
                    self._injected_record(structured=True)
                ]

                original = ArbitraryTextMessage(
                    "Apply service.capacity=20 before launch.",
                    "CapacitySpecialist",
                )
                guarded = manager.guard_call_result_if_needed(context, original)

                self.assertIsInstance(guarded, ArbitraryTextMessage)
                self.assertIn("service.capacity=50", guarded.content)
                self.assertNotIn("service.capacity=20", guarded.content)
                manager.record_call_end(context, guarded)

                events = self._events(
                    manager.output_dir / "trace.jsonl"
                )
                guard_event = next(
                    event
                    for event in events
                    if event["event_type"] == "autogen_memory_adoption_guard"
                )
                self.assertEqual(
                    guard_event["payload"]["status"],
                    "rule_repaired",
                )
                adoption = next(
                    event
                    for event in events
                    if event["event_type"] == "autogen_memory_adoption"
                )
                self.assertEqual(
                    adoption["payload"]["wrong_memory_hit_count"],
                    1,
                )
                output = next(
                    event
                    for event in events
                    if event["event_type"] == "autogen_agent_output"
                )
                self.assertEqual(
                    output["payload"]["memory_adoption_guard"]["status"],
                    "rule_repaired",
                )
                self.assertTrue(
                    all(
                        ref["state_type"] == "artifact_state"
                        for ref in output["payload"]["state_refs"]
                    )
                )

    def test_unrepairable_adoption_writes_failure_state_and_skips_memory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "generic-memory-block",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(
                    self._bootstrap_context(root, "launch_block")
                )
                context = HookCallContext(
                    call_id="call_arbitrary_auditor",
                    task=TaskSpec(
                        task_id="generic-3",
                        group_id="generic-chain",
                        title="Audit settings",
                        prompt="Audit the current service settings.",
                    ),
                    agent=AgentDescriptor(
                        "IndependentAuditor",
                        "UnmappedExternalRole",
                    ),
                    method_name="on_messages",
                    target_kind="agentchat_agent",
                )
                manager._memory_injections_by_call[context.call_id] = [
                    self._injected_record(structured=False)
                ]

                guarded = manager.guard_call_result_if_needed(
                    context,
                    ArbitraryTextMessage(
                        "Use database busy_timeout=5000 ms.",
                        "IndependentAuditor",
                    ),
                )

                self.assertIn("Runtime safety hold", guarded.content)
                self.assertNotIn("AGENTLITE_", guarded.content)
                manager.record_call_end(context, guarded)
                events = self._events(manager.output_dir / "trace.jsonl")
                output = next(
                    event
                    for event in events
                    if event["event_type"] == "autogen_agent_output"
                )
                self.assertEqual(
                    [ref["state_type"] for ref in output["payload"]["state_refs"]],
                    ["failure_state"],
                )
                self.assertEqual(output["payload"]["memory_refs"], [])

    def test_guard_events_are_exposed_as_separate_report_metrics(self) -> None:
        summary = _autogen_token_summary(
            [
                {
                    "event_type": "autogen_memory_adoption_guard",
                    "payload": {
                        "status": "rule_repaired",
                        "repaired_fact_count": 2,
                    },
                },
                {
                    "event_type": "autogen_memory_adoption_guard",
                    "payload": {
                        "status": "blocked",
                        "repaired_fact_count": 0,
                    },
                },
            ]
        )

        self.assertEqual(summary["memory_adoption_guard_event_count"], 2)
        self.assertEqual(summary["memory_adoption_rule_repair_count"], 1)
        self.assertEqual(summary["memory_adoption_blocked_count"], 1)
        self.assertEqual(summary["memory_adoption_repaired_fact_count"], 2)

    def test_downstream_compensation_does_not_deprecate_active_memory(
        self,
    ) -> None:
        store = MemoryStoreLite()
        ref = store.write_memory(
            task_id="generic-prior",
            source_agent="ArbitrarySource",
            task_topic="generic.settings",
            summary="service.capacity=50",
            tags=["generic-chain"],
            slot_hint="runtime_config",
        )

        event = store.record_downstream_compensation(
            ref,
            reason="downstream output used a superseded value",
            created_by="GenericGuard",
        )

        self.assertEqual(event.event_type, "downstream_fact_correction")
        self.assertEqual(store.resolve_ref(ref.memory_id).status, "active")
        self.assertEqual(
            store.compensating_events(ref.memory_id)[-1].event_id,
            event.event_id,
        )

    @staticmethod
    def _structured_row(
        *,
        status: str,
        active_value: str,
        historical_values: list[str],
        historical_spans: list[str],
    ) -> dict[str, object]:
        return {
            "status": status,
            "attribution_mode": "ccf_v2_semantic_key_value_rules",
            "semantic_key": "arbitrary.domain.service_capacity",
            "active_value": active_value,
            "historical_values": historical_values,
            "matched_historical_output_spans": historical_spans,
        }

    @staticmethod
    def _injected_record(*, structured: bool) -> _InjectedMemoryRecord:
        ref = MemoryRef(
            memory_id="mem_capacity_123",
            version_id=2,
            status="active",
            task_topic="arbitrary.settings",
            memory_view_id="view_capacity_123",
            slot_id="slot.runtime.config",
        )
        if structured:
            revision_guard = {
                "required": True,
                "schema_version": "ccf.v2",
                "subject": "project:current",
                "semantic_key": (
                    "project:current|slot.runtime.config|"
                    "config.service.capacity"
                ),
                "active_facts": [
                    {
                        "semantic_key": (
                            "project:current|slot.runtime.config|"
                            "config.service.capacity"
                        ),
                        "slot_id": "slot.runtime.config",
                        "scope": "config.service.capacity",
                        "value": "50",
                        "value_type": "integer",
                        "unit": "",
                        "polarity": "positive",
                    }
                ],
                "historical_facts": [
                    {
                        "semantic_key": (
                            "project:current|slot.runtime.config|"
                            "config.service.capacity"
                        ),
                        "slot_id": "slot.runtime.config",
                        "scope": "config.service.capacity",
                        "value": "20",
                        "value_type": "integer",
                        "unit": "",
                        "polarity": "positive",
                    }
                ],
            }
            prompt_view = "service.capacity=50"
        else:
            revision_guard = {
                "required": True,
                "schema_version": "ccf.v1-lite",
                "historical_claims": [
                    {
                        "claim_id": "claim_timeout_old",
                        "summary": "database busy_timeout=5000 ms",
                        "value": "5000",
                        "status": "superseded",
                    }
                ],
            }
            prompt_view = "database busy_timeout=2000 ms"
        return _InjectedMemoryRecord(
            ref=ref,
            prompt_view=prompt_view,
            current_task_text="Publish the current service settings.",
            current_task_source="user_task",
            injected_prompt_view=prompt_view,
            revision_guard=revision_guard,
        )

    @staticmethod
    def _bootstrap_context(root: Path, session_id: str) -> BootstrapContext:
        status_file = root / "sessions" / session_id / "bootstrap_status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        target_cwd = root / "workspace"
        target_cwd.mkdir(exist_ok=True)
        return BootstrapContext(
            framework="autogen",
            session_id=session_id,
            data_dir=root,
            status_file=status_file,
            target_cwd=target_cwd,
        )

    @staticmethod
    def _events(path: Path) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


if __name__ == "__main__":
    unittest.main()
