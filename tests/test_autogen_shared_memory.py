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
    _memory_view_facts_covered,
)
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


class AutoGenSharedMemoryTest(unittest.TestCase):
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
                self.assertEqual(rewritten.count("CURRENT_USER_TASK"), 1)
                self.assertIn(current_task, rewritten)
                self.assertIn("GROUNDING_RULE", rewritten)
                self.assertIn(prior_task, rewritten)
                self.assertIn("LATEST_WRITER_ARTIFACT_BEGIN", rewritten)
                self.assertIn("LATEST_WRITER_ARTIFACT_END", rewritten)
                self.assertNotIn("SHOULD_NOT_DOMINATE_CURRENT_VIEW", rewritten)
                self.assertNotIn("FINAL_ANSWER_READY", rewritten)

                events = self._events(manager.output_dir / "trace.jsonl")
                rewrite = next(
                    item
                    for item in reversed(events)
                    if item.get("event_type") == "autogen_agent_input_real_rewrite"
                    and item.get("payload", {}).get("call_id") == context.call_id
                )
                self.assertTrue(rewrite["payload"]["rewrite_applied"])
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
                self.assertIn(latest_writer, rewritten)
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
                self.assertEqual(rewritten.count(SHARED_MEMORY_MARKER), 1)

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
                self.assertTrue(
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
