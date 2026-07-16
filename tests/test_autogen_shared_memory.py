from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from agent_runtime.bootstrap.startup import BootstrapContext
from agent_runtime.drivers.autogen import AutoGenHookManager, SHARED_MEMORY_MARKER


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
                                "已确认：三天两晚，总预算三千元，偏好自然风景、轻徒步和当地美食，避开拥挤商业景点。",
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

                studio_task = [
                    {
                        "type": "TextMessage",
                        "content": "A2 基于刚才偏好筛选三个目的地",
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
                    "A2 基于刚才偏好筛选三个目的地",
                )
                rewritten = rewritten_task[0]["content"]
                self.assertIn(SHARED_MEMORY_MARKER, rewritten)
                self.assertIn("自然风景", rewritten)
                self.assertEqual(rewritten.count(SHARED_MEMORY_MARKER), 1)

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
                    args=([{"type": "TextMessage", "source": "user", "content": "x" * 800}],),
                    kwargs={},
                )
                rewritten_args, _ = persisted.rewrite_call_arguments_if_safe(
                    direct,
                    ([{"type": "TextMessage", "source": "user", "content": "x" * 800}],),
                    {},
                )
                self.assertIsInstance(rewritten_args[0], list)

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
