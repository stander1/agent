from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_runtime.bootstrap.startup import BootstrapContext
from agent_runtime.core.kernel import MemoryContext
from agent_runtime.drivers.autogen import (
    AutoGenHookManager,
    SHARED_MEMORY_MARKER,
    _continuity_cost_override_allowed,
    _continuity_requirement_reasons,
    _continuity_source_text,
    _fact_support_score,
    _has_semantic_payload,
    _memory_adoption_evidence,
    _memory_view_facts_covered,
    _semantic_autogen_state_text,
    _sanitize_model_visible_content,
    _select_token_nonexpanding_view,
)
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.memory.memory_store import MemoryRef


@dataclass
class FakeTextMessage:
    content: str
    source: str
    type: str = "TextMessage"


@dataclass
class FakeTaskResult:
    messages: list[FakeTextMessage]


class FakeTeam:
    _participant_names = ["planner", "writer", "reviewer"]


class OtherFakeTeam:
    _participant_names = ["researcher", "coder", "critic"]


class FakeAgent:
    name = "writer"


class ChatAgentContainer:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeTool:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


class ProfiledAgent:
    def __init__(self, name: str, description: str, system_prompt: str, tools=None) -> None:
        self.name = name
        self.description = description
        self._system_messages = [SimpleNamespace(content=system_prompt)]
        self._tools = list(tools or [])
        self._handoff_tools = []


class ProfiledTeam:
    def __init__(self, participants) -> None:
        self._participants = list(participants)
        self._participant_names = [item.name for item in self._participants]


class AutoGenSharedMemoryTest(unittest.TestCase):
    def test_autogen_routing_metadata_is_not_business_state(self) -> None:
        routing = (
            "082e0b93-0bc3-4863-a8b1-3e4c7cf51d50: "
            "DefaultTopicId(type='planner_082e0b93-0bc3-4863-a8b1-3e4c7cf51d50', "
            "source='082e0b93-0bc3-4863-a8b1-3e4c7cf51d50')"
        )

        self.assertFalse(_has_semantic_payload([], routing))
        self.assertTrue(
            _has_semantic_payload(
                [],
                f"{routing}\nUser confirmed the timeout is 30 seconds.",
            )
        )

    def test_autogen_control_objects_are_removed_from_semantic_state(self) -> None:
        routing_only = (
            "DefaultTopicId(type='planner', source='session')\n"
            "AgentId(type='planner', key='session')\n"
            "CancellationToken: "
            "<autogen_core.base._cancellation_token.CancellationToken "
            "object at 0x000001ABCDEF>"
        )
        mixed = f"user: Review the release plan.\n{routing_only}"

        self.assertEqual(_semantic_autogen_state_text([], routing_only), "")
        semantic = _semantic_autogen_state_text([], mixed)
        self.assertEqual(semantic, "user: Review the release plan.")
        self.assertNotIn("AgentId", semantic)
        self.assertNotIn("CancellationToken", semantic)

    def test_legacy_rewrite_metadata_is_removed_from_model_visible_content(self) -> None:
        content = (
            "AGENTLITE_REAL_REWRITE v1\n"
            "native_payload_moved_to_state_pool=true\n"
            'shp_wire={"state_refs":[{"state_id":"state_1"}]}\n'
            "prompt_view:\n"
            "CURRENT_USER_TASK:\n"
            "Review the release plan."
        )

        visible = _sanitize_model_visible_content(content)

        self.assertIn("Review the release plan.", visible)
        self.assertNotIn("AGENTLITE_", visible)
        self.assertNotIn("shp_wire", visible)

    def test_memory_adoption_requires_output_evidence_beyond_current_task(self) -> None:
        evidence = _memory_adoption_evidence(
            memory_prompt_view=(
                "[memory_view:view_budget] slot=slot.project.requirement; "
                "claim=claim_budget; 已确认总预算为2800元，偏好自然风景和轻徒步； "
                "tags=[requirement]"
            ),
            injected_prompt_view="已确认总预算为2800元，偏好自然风景和轻徒步。",
            current_task_text="请基于上一轮结果继续完善可执行方案。",
            output_text="方案保留已确认的2800元总预算，并安排自然风景轻徒步。",
            memory_id="mem_budget",
            memory_view_id="view_budget",
        )
        duplicate = _memory_adoption_evidence(
            memory_prompt_view=(
                "[memory_view:view_budget] slot=slot.project.requirement; "
                "claim=claim_budget; 已确认总预算为2800元，偏好自然风景和轻徒步； "
                "tags=[requirement]"
            ),
            injected_prompt_view="已确认总预算为2800元，偏好自然风景和轻徒步。",
            current_task_text="已确认总预算为2800元，偏好自然风景和轻徒步。",
            output_text="已确认总预算为2800元，偏好自然风景和轻徒步。",
            memory_id="mem_budget",
            memory_view_id="view_budget",
        )
        omitted = _memory_adoption_evidence(
            memory_prompt_view=(
                "[memory_view:view_budget] slot=slot.project.requirement; "
                "claim=claim_budget; 已确认总预算为2800元，偏好自然风景和轻徒步； "
                "tags=[requirement]"
            ),
            injected_prompt_view="当前角色只需要输出风险清单。",
            current_task_text="请继续完善可执行方案。",
            output_text="已确认总预算为2800元，偏好自然风景和轻徒步。",
            memory_id="mem_budget",
            memory_view_id="view_budget",
        )

        self.assertTrue(evidence["adopted"])
        self.assertGreater(evidence["matched_fact_count"], 0)
        self.assertFalse(duplicate["adopted"])
        self.assertGreater(duplicate["current_task_duplicate_fact_count"], 0)
        self.assertFalse(omitted["adopted"])
        self.assertEqual(omitted["candidate_fact_count"], 0)

    def test_memory_adoption_excludes_partial_current_task_overlap(self) -> None:
        evidence = _memory_adoption_evidence(
            memory_prompt_view=(
                "[memory_view:view_architecture] "
                "slot=slot.system.deliverable_requirement; "
                "claim=claim_architecture; "
                "已确认核心架构为 SQLite WAL 加文件载荷；"
                "峰值并发提升至50个任务；审计记录保留7天；"
                "tags=[requirement]"
            ),
            injected_prompt_view=(
                "已确认核心架构为 SQLite WAL 加文件载荷；"
                "峰值并发提升至50个任务；审计记录保留7天。"
            ),
            current_task_text=(
                "请基于前序结论形成部署清单，必须覆盖50并发控制和7天审计保留。"
            ),
            output_text=(
                "已确认核心架构为 SQLite WAL 加文件载荷，并实现50并发控制与7天审计保留。"
            ),
            memory_id="mem_architecture",
            memory_view_id="view_architecture",
        )

        self.assertTrue(evidence["adopted"])
        self.assertGreaterEqual(
            evidence["current_task_duplicate_fact_count"],
            2,
        )
        self.assertGreater(evidence["matched_fact_count"], 0)
        self.assertTrue(evidence["current_task_fingerprint"])
        self.assertTrue(
            all(score >= 0.68 for score in evidence["current_task_duplicate_scores"])
        )
        self.assertTrue(
            all(margin > 0 for margin in evidence["attribution_margins"])
        )

    def test_numeric_fact_match_rejects_generic_shared_words(self) -> None:
        score = _fact_support_score(
            "峰值并发提升至50个任务",
            "第50个任务需要用户确认",
        )

        self.assertLess(score, 0.68)

    def test_memory_adoption_classifies_active_historical_and_mixed_outputs(self) -> None:
        revision_guard = {
            "historical_claims": [
                {
                    "claim_id": "claim_old",
                    "summary": "database busy_timeout=5000 ms",
                    "status": "superseded",
                }
            ]
        }
        prompt_view = (
            "[memory_view:view_db] slot=slot.runtime.config; "
            "claim=claim_new; database busy_timeout=2000 ms; tags=[runtime]\n"
            "[revision_guard policy=use_active_claims_only] "
            "Use active claim values as authoritative."
        )
        common = {
            "memory_prompt_view": prompt_view,
            "injected_prompt_view": prompt_view,
            "current_task_text": "Provide the final database configuration.",
            "memory_id": "mem_db",
            "memory_view_id": "view_db",
            "revision_guard": revision_guard,
        }

        useful = _memory_adoption_evidence(
            output_text="Use database busy_timeout=2000 ms.",
            **common,
        )
        wrong = _memory_adoption_evidence(
            output_text="Use database busy_timeout=5000 ms.",
            **common,
        )
        mixed = _memory_adoption_evidence(
            output_text=(
                "Use database busy_timeout=2000 ms, while the fallback keeps "
                "busy_timeout=5000 ms."
            ),
            **common,
        )

        self.assertEqual(useful["status"], "useful")
        self.assertEqual(wrong["status"], "wrong")
        self.assertEqual(mixed["status"], "mixed")
        self.assertEqual(wrong["matched_historical_fact_count"], 1)
        self.assertEqual(mixed["matched_historical_fact_count"], 1)

    def test_negated_historical_value_is_not_counted_as_contamination(self) -> None:
        evidence = _memory_adoption_evidence(
            memory_prompt_view=(
                "[memory_view:view_db] slot=slot.runtime.config; "
                "claim=claim_new; database busy_timeout=2000 ms; tags=[runtime]\n"
                "[revision_guard policy=use_active_claims_only]"
            ),
            injected_prompt_view=(
                "database busy_timeout=2000 ms\n"
                "[revision_guard policy=use_active_claims_only]"
            ),
            current_task_text="Provide the final database configuration.",
            output_text=(
                "Use database busy_timeout=2000 ms, not the old 5000 ms value."
            ),
            memory_id="mem_db",
            memory_view_id="view_db",
            revision_guard={
                "historical_claims": [
                    {
                        "claim_id": "claim_old",
                        "summary": "database busy_timeout=5000 ms",
                    }
                ]
            },
        )

        self.assertEqual(evidence["status"], "useful")
        self.assertEqual(evidence["matched_historical_fact_count"], 0)

    def test_arbitrary_agent_output_persists_evidence_backed_memory_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "generic-adoption-test",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(self._context(root, "launch_adoption"))
                summary = "已确认总预算为2800元，偏好自然风景和轻徒步，避开拥挤商业景点。"
                ref = manager.kernel.memory_store.write_memory(
                    task_id="prior_task",
                    source_agent="RequirementAnalyst",
                    task_topic="generic.requirement",
                    summary=summary,
                    tags=["generic-adoption-test"],
                    slot_hint="slot.project.requirement",
                )
                messages = [{
                    "type": "TextMessage",
                    "source": "user",
                    "content": (
                        "请基于上一轮已经确认的要求继续形成完整方案。"
                        + "请保持事实一致并给出可执行细节。" * 500
                    ),
                }]
                agent = ProfiledAgent(
                    "DeliveryComposer",
                    "Compose a complete deliverable from confirmed facts.",
                    "Use supplied evidence and preserve confirmed constraints.",
                )
                context = manager.record_call_start(
                    instance=agent,
                    method_name="on_messages",
                    target_kind="agentchat_agent",
                    args=(messages,),
                    kwargs={},
                )
                context.memory_context = MemoryContext(
                    refs=[ref],
                    prompt_views=[manager.kernel.memory_store.render_prompt_view(ref)],
                )

                rewritten_args, _ = manager.rewrite_call_arguments_if_safe(
                    context,
                    (messages,),
                    {},
                )
                self.assertIn(SHARED_MEMORY_MARKER, rewritten_args[0][0]["content"])
                manager.record_call_end(
                    context,
                    FakeTextMessage(
                        "完整方案沿用已确认约束：总预算2800元，偏好自然风景和轻徒步，"
                        "并避开拥挤商业景点。",
                        "DeliveryComposer",
                    ),
                )

                events = self._events(manager.output_dir / "trace.jsonl")
                adoption = next(
                    event
                    for event in reversed(events)
                    if event.get("event_type") == "autogen_memory_adoption"
                )
                self.assertEqual(adoption["payload"]["useful_memory_hit_count"], 1)
                self.assertEqual(
                    adoption["payload"]["memory_supported_output_count"],
                    1,
                )
                memory = next(
                    item
                    for item in manager.kernel.memory_store.snapshot()["memories"]
                    if item["memory_id"] == ref.memory_id
                )
                self.assertEqual(memory["useful_hit_count"], 1)

    def test_autogen_runtime_uuid_instance_maps_to_logical_business_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = AutoGenHookManager(self._context(root, "launch_aliases"))
            business = ProfiledAgent(
                "EvidenceScout",
                "Research sources and rank evidence.",
                "Retrieve reliable citations and verify claims.",
            )
            business_descriptor = manager.describe_agent(
                business,
                target_kind="agentchat_agent",
            )
            manager.kernel.register_agent(business_descriptor)
            run_id = "12345678-1234-1234-1234-123456789abc"
            wrapper = ChatAgentContainer(
                f"EvidenceScout_{run_id}_{run_id}"
            )

            wrapper_descriptor = manager.describe_agent(
                wrapper,
                target_kind="core_agent",
            )
            manager.kernel.register_agent(wrapper_descriptor)
            profile = manager.kernel.capability_profiles.get("EvidenceScout")

            self.assertEqual(wrapper_descriptor.agent_id, "EvidenceScout")
            self.assertTrue(
                wrapper_descriptor.framework_metadata["profile_alias_only"]
            )
            self.assertEqual(profile.role, type(business).__name__)
            self.assertIn(wrapper.name, profile.instance_aliases)
            self.assertEqual(
                manager.kernel.capability_profiles.agent_ids(
                    registry_scope="business"
                ),
                ["EvidenceScout"],
            )

    def test_capability_view_never_expands_equivalent_source(self) -> None:
        counter = TokenCounter(allow_estimate=True)
        short_source = "预算已确认2000元。"
        expanded_candidate = (
            "[context_view:BudgetAgent;action=WRITE_OUTPUT;profile_version=3] "
            "预算已确认2000元。"
        )

        fallback = _select_token_nonexpanding_view(
            counter,
            source_views=[short_source],
            candidate_text=expanded_candidate,
        )
        minimized = _select_token_nonexpanding_view(
            counter,
            source_views=[
                "目标：形成预算方案。预算已确认2000元。"
                "重复说明：形成预算方案时必须遵守2000元预算。"
            ],
            candidate_text="预算已确认2000元。",
        )

        self.assertEqual(fallback.text, short_source)
        self.assertEqual(fallback.selection_mode, "source_no_expansion")
        self.assertTrue(fallback.no_expansion_fallback)
        self.assertLessEqual(fallback.selected_tokens, fallback.source_tokens)
        self.assertEqual(minimized.selection_mode, "capability_minimized")
        self.assertLess(minimized.selected_tokens, minimized.source_tokens)

    def test_autogen_registers_arbitrary_agents_and_syncs_runtime_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scout = ProfiledAgent(
                "EvidenceScout",
                "Research sources and rank evidence.",
                "Retrieve reliable citations and verify claims.",
                tools=[FakeTool("web_search", "Search sources and retrieve evidence")],
            )
            composer = ProfiledAgent(
                "DeliveryComposer",
                "Synthesize evidence and write deliverables.",
                "Integrate confirmed facts into the final answer.",
            )
            manager = AutoGenHookManager(self._context(root, "launch_profiles"))

            manager.record_call_start(
                instance=ProfiledTeam([scout, composer]),
                method_name="run_stream",
                target_kind="agentchat_team",
                args=(),
                kwargs={"task": "Research evidence and prepare a verified report."},
            )

            scout_profile = manager.kernel.capability_profiles.get("EvidenceScout")
            composer_profile = manager.kernel.capability_profiles.get(
                "DeliveryComposer"
            )
            self.assertIn("retrieval", scout_profile.capabilities)
            self.assertIn("web_search", scout_profile.available_tools)
            self.assertIn("synthesis", composer_profile.capabilities)
            self.assertIsNone(manager.kernel.capability_profiles.get("planner"))

            call = manager.record_call_start(
                instance=scout,
                method_name="on_messages",
                target_kind="agentchat_agent",
                args=([FakeTextMessage("Retrieve and verify the evidence", "user")],),
                kwargs={},
            )
            manager.record_call_end(
                call,
                FakeTextMessage("Verified evidence from source S1.", "EvidenceScout"),
            )
            scout_profile = manager.kernel.capability_profiles.get("EvidenceScout")
            self.assertEqual(scout_profile.success_count, 1)
            self.assertGreater(scout_profile.total_cost_tokens, 0)

            scout._tools = []
            manager.record_call_start(
                instance=scout,
                method_name="on_messages",
                target_kind="agentchat_agent",
                args=([FakeTextMessage("Review the existing evidence", "user")],),
                kwargs={},
            )
            scout_profile = manager.kernel.capability_profiles.get("EvidenceScout")
            self.assertEqual(scout_profile.available_tools, set())
            self.assertNotIn("tool_use", scout_profile.tool_capabilities)

    def test_continuity_override_never_bypasses_structural_guard(self) -> None:
        context = SimpleNamespace(
            continuity_context_required=True,
            continuity_context_reasons=("zh_prior_reference",),
        )
        self.assertTrue(
            _continuity_cost_override_allowed(
                context=context,
                memory_retained=True,
                fallback_reasons=["token_not_reduced"],
            )
        )
        self.assertFalse(
            _continuity_cost_override_allowed(
                context=context,
                memory_retained=True,
                fallback_reasons=["token_not_reduced", "message_clone_failed"],
            )
        )

    def test_continuity_cues_are_generic_and_do_not_match_self_contained_task(self) -> None:
        self.assertIn(
            "zh_named_step_reference",
            _continuity_requirement_reasons(
                "请基于 B7 的候选结果选择一个方案并继续执行。"
            ),
        )
        self.assertIn(
            "en_prior_reference",
            _continuity_requirement_reasons(
                "Refine the previous draft while keeping confirmed constraints."
            ),
        )
        self.assertEqual(
            _continuity_requirement_reasons(
                "请根据本文完整给出的三个候选项独立完成比较。"
            ),
            (),
        )

    def test_continuity_detection_uses_user_task_not_agent_narration(self) -> None:
        messages = [
            SimpleNamespace(
                source="user",
                content_text="请根据本文给出的三个候选项独立完成比较。",
            ),
            SimpleNamespace(
                source="planner",
                content_text="后续写作者可以沿用之前的结构。",
            ),
        ]
        source_text = _continuity_source_text(messages, fallback="fallback")
        self.assertEqual(source_text, messages[0].content_text)
        self.assertEqual(_continuity_requirement_reasons(source_text), ())

        team_payload = (
            "AGENTLITE_TEAM_REAL_REWRITE v1\n"
            "broadcast_manifest={}\n"
            "CURRENT_USER_TASK (highest priority):\n"
            "请根据本文给出的三个候选项独立完成比较。\n"
            "receiver_prompt_views:\n"
            "--- receiver: writer\n"
            "semantic_role: writer\n"
            "请沿用之前的结构。"
        )
        team_source = _continuity_source_text(
            [SimpleNamespace(source="user", content_text=team_payload)],
            fallback="fallback",
        )
        self.assertEqual(team_source, "请根据本文给出的三个候选项独立完成比较。")
        self.assertEqual(_continuity_requirement_reasons(team_source), ())

    def test_memory_fact_dedup_keeps_conflicting_numeric_revision(self) -> None:
        context = "当前方案总预算为2600元，住宿费用为900元。"
        memory_view = (
            "[memory_view:view_budget] slot=slot.system.deliverable_requirement; "
            "claim=claim_budget; 当前方案总预算为2800元，住宿费用为900元； "
            "tags=[budget]"
        )

        self.assertFalse(_memory_view_facts_covered(memory_view, context))

    def test_real_rewrite_prioritizes_current_task_and_latest_upstream_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "chronology-view-test",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(self._context(root, "launch_chronology"))
                prior_task = "我们偏好自然风景、轻徒步和当地美食。"
                manager.record_call_start(
                    instance=FakeTeam(),
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": prior_task},
                )
                current_task = "基于上一轮候选项，选择一个并交付完整三日计划。"
                manager.record_call_start(
                    instance=FakeTeam(),
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": current_task},
                )
                old_context = "旧轮次候选分析" + ("旧内容" * 1800)
                planner_middle_marker = "SHOULD_NOT_DOMINATE_CURRENT_VIEW"
                planner_output = (
                    "规划说明开头" + ("规划细节" * 700) + planner_middle_marker
                    + ("后续规划" * 700) + "规划说明结尾"
                )
                latest_writer = (
                    "LATEST_WRITER_ARTIFACT_BEGIN\n"
                    "第一天完整安排。\n第二天完整安排。\n第三天完整安排。\n"
                    "LATEST_WRITER_ARTIFACT_END\nFINAL_ANSWER_READY"
                )
                messages = [
                    {"type": "TextMessage", "content": old_context, "source": "reviewer"},
                    {"type": "TextMessage", "content": current_task, "source": "user"},
                    {"type": "TextMessage", "content": planner_output, "source": "planner"},
                    {"type": "TextMessage", "content": latest_writer, "source": "writer"},
                ]
                context = manager.record_call_start(
                    instance=FakeAgent(),
                    method_name="on_messages_stream",
                    target_kind="agentchat_agent",
                    args=(messages,),
                    kwargs={},
                )

                rewritten_args, _ = manager.rewrite_call_arguments_if_safe(
                    context,
                    (messages,),
                    {},
                )

                self.assertEqual(len(rewritten_args[0]), 1)
                rewritten = rewritten_args[0][0]["content"]
                self.assertGreaterEqual(
                    rewritten.count("CURRENT_USER_TASK")
                    + rewritten.count("CURRENT_TASK_CONTEXT"),
                    1,
                )
                self.assertIn(current_task, rewritten)
                self.assertIn(
                    "Only USER_REQUEST_HISTORY can establish what the user explicitly confirmed.",
                    rewritten,
                )
                self.assertIn(prior_task, rewritten)
                self.assertIn("LATEST_WRITER_ARTIFACT_BEGIN", rewritten)
                self.assertIn("LATEST_WRITER_ARTIFACT_END", rewritten)
                self.assertNotIn("SHOULD_NOT_DOMINATE_CURRENT_VIEW", rewritten)
                self.assertNotIn("FINAL_ANSWER_READY", rewritten)
                self.assertNotIn("AGENTLITE_", rewritten)
                self.assertNotIn("shp_wire=", rewritten)

                events = self._events(manager.output_dir / "trace.jsonl")
                rewrite = next(
                    item
                    for item in reversed(events)
                    if item.get("event_type") == "autogen_agent_input_real_rewrite"
                    and item.get("payload", {}).get("call_id") == context.call_id
                )
                self.assertTrue(rewrite["payload"]["rewrite_applied"])
                self.assertEqual(
                    rewrite["payload"]["model_visible_protocol_marker_count"],
                    0,
                )
                self.assertTrue(
                    rewrite["payload"]["rewrite_safety"][
                        "current_task_units_preserved"
                    ]
                )
                self.assertGreater(
                    rewrite["payload"]["native_input_tokens"],
                    rewrite["payload"]["rewritten_input_tokens"],
                )

    def test_real_rewrite_removes_memory_already_covered_by_latest_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "memory-dedup-test",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(self._context(root, "launch_dedup"))
                current_task = "基于最新完整草案检查预算并给出最终答复。"
                latest_writer = (
                    "最新完整草案：总预算为2800元。第一天安排自然步道和本地餐饮；"
                    "第二天安排轻徒步并避开拥挤景点；第三天返程。"
                )
                messages = [
                    {
                        "type": "TextMessage",
                        "content": "已经过时的旧草案" + ("旧内容" * 1800),
                        "source": "reviewer",
                    },
                    {
                        "type": "TextMessage",
                        "content": current_task,
                        "source": "user",
                    },
                    {
                        "type": "TextMessage",
                        "content": latest_writer,
                        "source": "writer",
                    },
                ]
                context = manager.record_call_start(
                    instance=FakeAgent(),
                    method_name="on_messages_stream",
                    target_kind="agentchat_agent",
                    args=(messages,),
                    kwargs={},
                )
                context.memory_context = MemoryContext(
                    refs=[
                        MemoryRef(
                            memory_id="mem_duplicate",
                            version_id=1,
                            status="active",
                            task_topic="generic.final_deliverable",
                            memory_view_id="view_duplicate",
                            slot_id="slot.system.deliverable_requirement",
                        )
                    ],
                    prompt_views=[
                        "[memory_view:view_duplicate] "
                        "slot=slot.system.deliverable_requirement; "
                        f"claim=claim_duplicate; {latest_writer}; tags=[final]"
                    ],
                )

                rewritten_args, _ = manager.rewrite_call_arguments_if_safe(
                    context,
                    (messages,),
                    {},
                )

                rewritten = rewritten_args[0][0]["content"]
                for expected_fact in (
                    "最新完整草案：总预算为2800元。",
                    "第一天安排自然步道和本地餐饮",
                    "第二天安排轻徒步并避开拥挤景点",
                    "第三天返程。",
                ):
                    self.assertIn(expected_fact, rewritten)
                self.assertNotIn(SHARED_MEMORY_MARKER, rewritten)
                self.assertNotIn("mem_duplicate", rewritten)
                events = self._events(manager.output_dir / "trace.jsonl")
                rewrite = next(
                    item
                    for item in reversed(events)
                    if item.get("event_type") == "autogen_agent_input_real_rewrite"
                    and item.get("payload", {}).get("call_id") == context.call_id
                )
                payload = rewrite["payload"]
                self.assertTrue(payload["rewrite_applied"])
                self.assertEqual(payload["memory_candidate_count"], 1)
                self.assertEqual(payload["memory_retained_count"], 0)
                self.assertEqual(payload["memory_candidate_deduplicated_count"], 1)
                self.assertGreater(payload["memory_candidate_deduplicated_tokens"], 0)

    def test_team_rewrite_removes_memory_already_covered_by_current_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "team-memory-dedup-test",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(self._context(root, "launch_team_dedup"))
                covered_fact = "当前需求已确认总预算为3000元并偏好自然风景。"
                task = covered_fact + ("请依据完整约束继续形成可执行方案。" * 180)
                context = manager.record_call_start(
                    instance=FakeTeam(),
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": task},
                )
                context.memory_context = MemoryContext(
                    refs=[
                        MemoryRef(
                            memory_id="mem_team_duplicate",
                            version_id=1,
                            status="active",
                            task_topic="generic.requirement",
                            memory_view_id="view_team_duplicate",
                            slot_id="slot.project.requirement",
                        )
                    ],
                    prompt_views=[
                        "[memory_view:view_team_duplicate] "
                        "slot=slot.project.requirement; claim=claim_team_duplicate; "
                        f"{covered_fact}; tags=[requirement]"
                    ],
                )

                _, rewritten_kwargs = manager.rewrite_call_arguments_if_safe(
                    context,
                    (),
                    {"task": task},
                )

                rewritten = rewritten_kwargs["task"]
                self.assertIn("AGENTLITE_TEAM_REAL_REWRITE v1", rewritten)
                self.assertNotIn(SHARED_MEMORY_MARKER, rewritten)
                self.assertNotIn("mem_team_duplicate", rewritten)
                events = self._events(manager.output_dir / "trace.jsonl")
                rewrite = next(
                    item
                    for item in reversed(events)
                    if item.get("event_type") == "autogen_team_input_real_rewrite"
                    and item.get("payload", {}).get("call_id") == context.call_id
                )
                payload = rewrite["payload"]
                self.assertTrue(payload["rewrite_applied"])
                self.assertEqual(payload["fallback_reasons"], [])
                self.assertGreater(
                    payload["current_task_role_view_reduction_ratio"],
                    0,
                )
                self.assertEqual(
                    payload["current_task_fidelity_failure_count"],
                    0,
                )
                self.assertTrue(
                    all(
                        item["current_task_units_preserved"]
                        for item in payload["receiver_plans"]
                    )
                )
                self.assertEqual(payload["memory_candidate_count"], 1)
                self.assertEqual(payload["memory_retained_count"], 0)
                self.assertEqual(payload["memory_candidate_deduplicated_count"], 1)
                self.assertEqual(
                    payload["memory_candidate_deduplicated_fanout_count"],
                    3,
                )

    def test_review_only_team_output_is_rejected_from_long_term_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "review-rejection-test",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(self._context(root, "launch_bad_final"))
                context = manager.record_call_start(
                    instance=FakeTeam(),
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": "请优化旅行预算并交付完整预算表"},
                )
                manager.record_call_end(
                    context,
                    FakeTaskResult(
                        messages=[
                            FakeTextMessage("请优化旅行预算并交付完整预算表", "user"),
                            FakeTextMessage(
                                "**ReviewerAgent 审查意见（第一轮）**\n"
                                "**重大问题清单：** 当前草案缺少预算表。\n"
                                "**可执行修订清单（供Planner/Writer遵循）：**\n"
                                "请下一轮补充完整预算表。\n"
                                "**当前产出不合格。请依据上述清单进行修订。**\n"
                                "FINAL_ANSWER_READY",
                                "reviewer",
                            ),
                        ]
                    ),
                )

                snapshot = manager.kernel.memory_store.snapshot()
                self.assertEqual(snapshot["memories"], [])
                self.assertEqual(
                    snapshot["memory_candidates"][-1]["admission_status"],
                    "rejected",
                )
                events = self._events(manager.output_dir / "trace.jsonl")
                candidate = next(
                    item
                    for item in reversed(events)
                    if item.get("event_type") == "autogen_memory_candidate"
                )
                self.assertEqual(
                    candidate["payload"]["candidate_kind"],
                    "autogen_team_unvalidated",
                )

    def test_ungrounded_user_confirmation_is_rejected_from_long_term_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "grounding-rejection-test",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(self._context(root, "launch_grounding"))
                task = "请从既有候选项中选择一个，并生成三天两晚完整行程"
                context = manager.record_call_start(
                    instance=FakeTeam(),
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": task},
                )
                manager.record_call_end(
                    context,
                    FakeTaskResult(
                        messages=[
                            FakeTextMessage(task, "user"),
                            FakeTextMessage(
                                "## 最终可交付成果\n"
                                "根据您的最新确认（“膝盖不好，不能爬陡坡”），"
                                "现给出三天两晚完整行程与预算。\n"
                                "FINAL_ANSWER_READY",
                                "reviewer",
                            ),
                        ]
                    ),
                )

                snapshot = manager.kernel.memory_store.snapshot()
                self.assertEqual(snapshot["memories"], [])
                self.assertEqual(
                    snapshot["memory_candidates"][-1]["admission_status"],
                    "rejected",
                )
                events = self._events(manager.output_dir / "trace.jsonl")
                candidate = next(
                    item
                    for item in reversed(events)
                    if item.get("event_type") == "autogen_memory_candidate"
                )
                self.assertEqual(
                    candidate["payload"]["candidate_kind"],
                    "autogen_team_unvalidated",
                )

    def test_rules_first_admission_then_cross_launch_memory_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "travel-team-test",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(self._context(root, "launch_one"))
                team = FakeTeam()

                intermediate = manager.record_call_start(
                    instance=FakeAgent(),
                    method_name="on_messages",
                    target_kind="agentchat_agent",
                    args=([FakeTextMessage("整理旅行偏好", "user")],),
                    kwargs={},
                )
                manager.record_call_end(
                    intermediate,
                    FakeTextMessage(
                        "草案：用户偏好自然风景、轻徒步和当地美食。",
                        "writer",
                    ),
                )
                pending_snapshot = manager.kernel.memory_store.snapshot()
                self.assertEqual(len(pending_snapshot["memories"]), 0)
                self.assertEqual(
                    pending_snapshot["memory_candidates"][-1]["admission_status"],
                    "pending",
                )

                first = manager.record_call_start(
                    instance=team,
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": "A1 整理三天两晚旅行偏好和预算约束"},
                )
                self.assertEqual(first.memory_context.refs, [])
                manager.record_call_end(
                    first,
                    FakeTaskResult(
                        messages=[
                            FakeTextMessage("整理旅行偏好", "user"),
                            FakeTextMessage(
                                "已确认：三天两晚，总预算三千元，偏好自然风景、轻徒步和当地美食，避开拥挤商业景点。\n"
                                "FINAL_ANSWER_READY",
                                "reviewer",
                            ),
                        ]
                    ),
                )

                admitted_snapshot = manager.kernel.memory_store.snapshot()
                self.assertEqual(len(admitted_snapshot["memories"]), 1)
                self.assertEqual(
                    admitted_snapshot["memory_candidates"][-1]["admission_status"],
                    "admitted",
                )
                self.assertTrue(
                    (manager.output_dir / "pool_snapshot_latest.json").exists()
                )

                long_follow_up = (
                    "A2 基于刚才偏好筛选三个目的地，并逐项比较交通、住宿、餐饮、"
                    "自然体验、步行强度和拥挤风险。"
                    + "请保留既有约束并给出可核查理由。" * 120
                )
                studio_task = [
                    {
                        "type": "TextMessage",
                        "content": long_follow_up,
                        "source": "user",
                    }
                ]
                second = manager.record_call_start(
                    instance=team,
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": studio_task},
                )
                self.assertGreaterEqual(len(second.memory_context.refs), 1)
                new_args, new_kwargs = manager.rewrite_call_arguments_if_safe(
                    second,
                    (),
                    {"task": studio_task},
                )
                self.assertEqual(new_args, ())
                rewritten_task = new_kwargs["task"]
                self.assertIsInstance(rewritten_task, list)
                self.assertIsInstance(rewritten_task[0], dict)
                self.assertEqual(rewritten_task[0]["source"], "user")
                self.assertEqual(
                    studio_task[0]["content"],
                    long_follow_up,
                )
                rewritten = rewritten_task[0]["content"]
                self.assertIn(SHARED_MEMORY_MARKER, rewritten)
                self.assertIn("自然风景", rewritten)
                self.assertEqual(rewritten.count(SHARED_MEMORY_MARKER), 3)
                self.assertIn("--- receiver: planner", rewritten)
                self.assertIn("--- receiver: writer", rewritten)
                self.assertIn("--- receiver: reviewer", rewritten)

                writer_context = manager.record_call_start(
                    instance=FakeAgent(),
                    method_name="on_messages",
                    target_kind="agentchat_agent",
                    args=(rewritten_task,),
                    kwargs={},
                )
                hydrated_args, _ = manager.rewrite_call_arguments_if_safe(
                    writer_context,
                    (rewritten_task,),
                    {},
                )
                hydrated = hydrated_args[0][0]["content"]
                self.assertIn("AGENTLITE_RECEIVER_CAPABILITY_VIEW v1", hydrated)
                self.assertIn("semantic_action=WRITE_OUTPUT", hydrated)
                self.assertIn("capabilities=", hydrated)
                self.assertEqual(hydrated.count(SHARED_MEMORY_MARKER), 1)
                self.assertNotIn("--- receiver: planner", hydrated)
                self.assertNotIn("--- receiver: reviewer", hydrated)

                self.assertTrue(second.display_restore_enabled)
                applied_events = self._events(manager.output_dir / "trace.jsonl")
                applied_rewrite = next(
                    item
                    for item in reversed(applied_events)
                    if item.get("event_type") == "autogen_team_input_real_rewrite"
                )
                self.assertEqual(
                    applied_rewrite["payload"]["memory_injected_count"],
                    1,
                )

                internal_stream_message = FakeTextMessage(rewritten, "user")
                display_stream_message = manager.restore_call_result_for_display(
                    second,
                    internal_stream_message,
                )
                self.assertEqual(
                    display_stream_message.content,
                    long_follow_up,
                )
                self.assertIn(
                    "AGENTLITE_TEAM_REAL_REWRITE v1",
                    internal_stream_message.content,
                )

                internal_result = FakeTaskResult(
                    messages=[
                        internal_stream_message,
                        FakeTextMessage("review complete", "reviewer"),
                    ]
                )
                display_result = manager.restore_call_result_for_display(
                    second,
                    internal_result,
                )
                self.assertEqual(
                    display_result.messages[0].content,
                    studio_task[0]["content"],
                )
                self.assertEqual(
                    display_result.messages[1].content,
                    "review complete",
                )
                self.assertIn(
                    "AGENTLITE_TEAM_REAL_REWRITE v1",
                    internal_result.messages[0].content,
                )

                persisted = AutoGenHookManager(
                    self._context(root, "launch_two")
                )
                third = persisted.record_call_start(
                    instance=team,
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": "A3 继续完善上一轮旅行方案"},
                )
                self.assertGreaterEqual(len(third.memory_context.refs), 1)
                _, third_kwargs = persisted.rewrite_call_arguments_if_safe(
                    third,
                    (),
                    {"task": "A3 继续完善上一轮旅行方案"},
                )
                self.assertIn(
                    "AGENTLITE_TEAM_REAL_REWRITE v1",
                    third_kwargs["task"],
                )
                self.assertIn(SHARED_MEMORY_MARKER, third_kwargs["task"])
                strict_gate_events = self._events(
                    persisted.output_dir / "trace.jsonl"
                )
                strict_gate = next(
                    item
                    for item in reversed(strict_gate_events)
                    if item.get("event_type") == "autogen_team_input_real_rewrite"
                )
                self.assertTrue(strict_gate["payload"]["rewrite_applied"])
                self.assertEqual(
                    strict_gate["payload"]["memory_injected_count"],
                    1,
                )
                self.assertTrue(
                    strict_gate["payload"]["continuity_context_required"]
                )
                self.assertTrue(
                    strict_gate["payload"]["continuity_cost_override"]
                )
                self.assertEqual(strict_gate["payload"]["fallback_reasons"], [])
                internal_string_message = FakeTextMessage(
                    third_kwargs["task"],
                    "user",
                )
                display_string_message = persisted.restore_call_result_for_display(
                    third,
                    internal_string_message,
                )
                self.assertEqual(
                    display_string_message.content,
                    "A3 继续完善上一轮旅行方案",
                )
                trace = self._events(persisted.output_dir / "trace.jsonl")
                retrievals = [
                    item
                    for item in trace
                    if item.get("event_type") == "autogen_memory_retrieval"
                ]
                self.assertEqual(retrievals[-1]["payload"]["memory_hit_count"], 1)
                self.assertGreater(
                    retrievals[-1]["payload"]["retrieved_memory_tokens"],
                    0,
                )

                isolated = persisted.record_call_start(
                    instance=OtherFakeTeam(),
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": "A4 继续完善上一轮旅行方案"},
                )
                self.assertEqual(isolated.memory_context.refs, [])
                self.assertNotEqual(third.task.group_id, isolated.task.group_id)

                direct = persisted.record_call_start(
                    instance=FakeAgent(),
                    method_name="on_messages_stream",
                    target_kind="agentchat_agent",
                    args=([{
                        "type": "TextMessage",
                        "source": "user",
                        "content": "请保留莫干山自然体验、既有预算和全部旅行约束。" * 600,
                    }],),
                    kwargs={},
                )
                self.assertEqual(direct.task.group_id, third.task.group_id)
                self.assertGreaterEqual(len(direct.memory_context.refs), 1)
                rewritten_args, _ = persisted.rewrite_call_arguments_if_safe(
                    direct,
                    ([{
                        "type": "TextMessage",
                        "source": "user",
                        "content": "请保留莫干山自然体验、既有预算和全部旅行约束。" * 600,
                    }],),
                    {},
                )
                self.assertIsInstance(rewritten_args[0], list)
                self.assertIn(
                    SHARED_MEMORY_MARKER,
                    rewritten_args[0][0]["content"],
                )
                direct_events = self._events(persisted.output_dir / "trace.jsonl")
                direct_rewrite = next(
                    item
                    for item in reversed(direct_events)
                    if item.get("event_type") == "autogen_agent_input_real_rewrite"
                    and item.get("payload", {}).get("call_id") == direct.call_id
                )
                self.assertTrue(direct_rewrite["payload"]["rewrite_applied"])
                self.assertEqual(
                    direct_rewrite["payload"]["memory_injected_count"],
                    1,
                )
                self.assertFalse(
                    direct_rewrite["payload"]["continuity_cost_override"]
                )
                self.assertEqual(direct_rewrite["payload"]["fallback_reasons"], [])

                short_messages = [
                    {
                        "type": "TextMessage",
                        "source": "user",
                        "content": "继续优化莫干山预算",
                    }
                ]
                short = persisted.record_call_start(
                    instance=FakeAgent(),
                    method_name="on_messages_stream",
                    target_kind="agentchat_agent",
                    args=(short_messages,),
                    kwargs={},
                )
                self.assertGreaterEqual(len(short.memory_context.refs), 1)
                short_args, _ = persisted.rewrite_call_arguments_if_safe(
                    short,
                    (short_messages,),
                    {},
                )
                self.assertIn(
                    SHARED_MEMORY_MARKER,
                    short_args[0][0]["content"],
                )
                short_events = self._events(persisted.output_dir / "trace.jsonl")
                short_rewrite = next(
                    item
                    for item in reversed(short_events)
                    if item.get("event_type") == "autogen_agent_input_real_rewrite"
                    and item.get("payload", {}).get("call_id") == short.call_id
                )
                self.assertTrue(short_rewrite["payload"]["rewrite_applied"])
                self.assertEqual(short_rewrite["payload"]["memory_injected_count"], 1)
                self.assertTrue(
                    short_rewrite["payload"]["continuity_cost_override"]
                )

    def test_team_output_without_exact_marker_cannot_enter_long_term_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
                "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "missing-marker-test",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(self._context(root, "launch_no_marker"))
                context = manager.record_call_start(
                    instance=FakeTeam(),
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": "整理并确认当前需求"},
                )
                manager.record_call_end(
                    context,
                    FakeTaskResult(
                        messages=[
                            FakeTextMessage("整理并确认当前需求", "user"),
                            FakeTextMessage(
                                "当前草案尚未达到交付标准，需 Planner 与 Writer 协同修订。",
                                "reviewer",
                            ),
                        ]
                    ),
                )

                snapshot = manager.kernel.memory_store.snapshot()
                self.assertEqual(snapshot["memories"], [])
                candidate = snapshot["memory_candidates"][-1]
                self.assertEqual(candidate["admission_status"], "rejected")
                events = self._events(manager.output_dir / "trace.jsonl")
                event = next(
                    item
                    for item in reversed(events)
                    if item.get("event_type") == "autogen_memory_candidate"
                )
                assessment = event["payload"]["delivery_assessment"]
                self.assertIn("exact_final_marker_missing", assessment["reasons"])

    def test_observe_mode_does_not_activate_shared_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "AGENTLITE_AUTOGEN_BROADCAST_MODE": "shadow-only",
                "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
                "AGENTLITE_MEMORY_SCOPE": "observe-test",
            }
            with patch.dict("os.environ", env, clear=False):
                manager = AutoGenHookManager(self._context(root, "launch_observe"))
                self.assertFalse(manager.shared_memory_enabled)
                context = manager.record_call_start(
                    instance=FakeTeam(),
                    method_name="run_stream",
                    target_kind="agentchat_team",
                    args=(),
                    kwargs={"task": "A1 观察模式任务"},
                )
                self.assertEqual(context.memory_context.refs, [])

    @staticmethod
    def _context(root: Path, session_id: str) -> BootstrapContext:
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
