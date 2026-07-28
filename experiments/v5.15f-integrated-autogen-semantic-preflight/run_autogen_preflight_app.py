from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

from agent_runtime.llm.client import OpenAICompatibleChatClient
from agent_runtime.llm.config import LlmConfig


def _git_commit(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _provider_config() -> LlmConfig:
    return LlmConfig(
        provider="openai-compatible-team",
        base_url=os.environ["OPENAI_BASE_URL"],
        model=os.environ["OPENAI_MODEL"],
        api_key_env="OPENAI_API_KEY",
        auth_scheme="authorization_bearer",
        timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "300")),
        max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "0")),
        retry_backoff_seconds=float(
            os.getenv("OPENAI_RETRY_BACKOFF_SECONDS", "3")
        ),
        temperature=0.0,
        top_p=1.0,
    )


class UsageRecorder:
    def __init__(self, output_dir: Path) -> None:
        self.rows: list[dict[str, Any]] = []
        self.path = output_dir / "llm_usage.jsonl"
        self.path.touch(exist_ok=False)

    def add(self, row: dict[str, Any]) -> None:
        self.rows.append(dict(row))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def summary(self) -> dict[str, Any]:
        prompt = sum(int(row["prompt_tokens"]) for row in self.rows)
        completion = sum(
            int(row["completion_tokens"]) for row in self.rows
        )
        total = sum(int(row["total_tokens"]) for row in self.rows)
        return {
            "llm_call_count": len(self.rows),
            "llm_prompt_tokens": prompt,
            "llm_completion_tokens": completion,
            "llm_total_tokens": total,
            "retry_count": sum(
                int(row.get("retry_count", 0)) for row in self.rows
            ),
            "latency_ms": sum(
                float(row.get("latency_ms", 0.0)) for row in self.rows
            ),
        }


async def run_workflow(
    *,
    scenario: dict[str, Any],
    team_config: dict[str, Any],
    output_dir: Path,
    implementation_commit: str,
) -> dict[str, Any]:
    from autogen_agentchat.agents import BaseChatAgent
    from autogen_agentchat.base import Response
    from autogen_agentchat.messages import BaseChatMessage, TextMessage
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_core import CancellationToken

    tasks = scenario.get("tasks")
    agents_payload = team_config.get("agents")
    if not isinstance(tasks, list) or len(tasks) < 2:
        raise ValueError("integrated scenario requires at least two tasks")
    if not isinstance(agents_payload, list) or len(agents_payload) < 2:
        raise ValueError("integrated team requires at least two agents")

    output_dir.mkdir(parents=True, exist_ok=False)
    recorder = UsageRecorder(output_dir)
    client = OpenAICompatibleChatClient(_provider_config())
    active_task_id = ""

    class WorkflowAgent(BaseChatAgent):
        def __init__(self, config: dict[str, Any]) -> None:
            super().__init__(
                name=str(config["name"]),
                description=str(
                    config.get("description") or config["name"]
                ),
            )
            self.system_prompt = str(config["system_prompt"])

        @property
        def produced_message_types(
            self,
        ) -> Sequence[type[BaseChatMessage]]:
            return (TextMessage,)

        async def on_messages(
            self,
            messages: Sequence[BaseChatMessage],
            cancellation_token: CancellationToken,
        ) -> Response:
            del cancellation_token
            lines = [
                "Continue the current team task from the received messages.",
                "Preserve exact evidence values and distinguish active, "
                "historical, proposed, and unresolved facts.",
                "",
            ]
            for message in messages:
                source = str(
                    getattr(message, "source", "unknown") or "unknown"
                )
                content = str(getattr(message, "content", "") or "")
                lines.append(f"[{source}] {content}")
            prompt = "\n".join(lines)
            result = await asyncio.to_thread(
                client.complete,
                system_prompt=self.system_prompt,
                user_prompt=prompt,
            )
            guard = result.provider_guard or {}
            recorder.add(
                {
                    "task_id": active_task_id,
                    "agent": self.name,
                    "model": result.model,
                    "prompt_tokens": int(
                        result.usage.get("prompt_tokens", 0)
                    ),
                    "completion_tokens": int(
                        result.usage.get("completion_tokens", 0)
                    ),
                    "total_tokens": int(
                        result.usage.get("total_tokens", 0)
                    ),
                    "retry_count": int(
                        guard.get("retry_attempts", 0) or 0
                    ),
                    "latency_ms": float(result.latency_ms),
                    "output_chars": len(result.content),
                }
            )
            return Response(
                chat_message=TextMessage(
                    content=result.content,
                    source=self.name,
                )
            )

        async def on_reset(
            self,
            cancellation_token: CancellationToken,
        ) -> None:
            del cancellation_token

    agents = [
        WorkflowAgent(item)
        for item in agents_payload
        if isinstance(item, dict)
    ]
    if len(agents) != len(agents_payload):
        raise ValueError("every team agent must be a JSON object")
    team = RoundRobinGroupChat(agents, max_turns=len(agents))

    task_rows: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            raise ValueError(f"task {index} must be a JSON object")
        task_id = str(task.get("task_id") or "").strip()
        question = str(task.get("question") or "").strip()
        if not task_id or not question:
            raise ValueError(f"task {index} requires task_id and question")
        active_task_id = task_id
        usage_start = len(recorder.rows)
        started = time.perf_counter()
        result = await team.run(task=question)
        messages = [
            {
                "type": type(message).__name__,
                "source": str(getattr(message, "source", "") or ""),
                "content": str(getattr(message, "content", "") or ""),
            }
            for message in (getattr(result, "messages", []) or [])
        ]
        task_rows.append(
            {
                "task_id": task_id,
                "task_index": index,
                "question": question,
                "messages": messages,
                "stop_reason": str(
                    getattr(result, "stop_reason", "") or ""
                ),
                "wall_time_ms": int(
                    (time.perf_counter() - started) * 1000
                ),
                "provider_calls": len(recorder.rows) - usage_start,
                "provider_outputs": [
                    message
                    for message in messages
                    if message["source"] not in {"", "user"}
                ],
            }
        )
        if index < len(tasks):
            await team.reset()

    usage_summary = recorder.summary()
    (output_dir / "llm_usage_summary.json").write_text(
        json.dumps(usage_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload = {
        "schema_version": "agentlite.v515f.workflow-result.v1",
        "binding": {
            "scenario_id": str(scenario.get("scenario_id") or ""),
            "scenario_authored_after_commit": str(
                scenario.get("authored_after_commit") or ""
            ),
            "team_authored_after_commit": str(
                team_config.get("authored_after_commit") or ""
            ),
            "implementation_commit": implementation_commit,
        },
        "team": {
            "agent_names": [agent.name for agent in agents],
            "agent_count": len(agents),
            "same_team_instance": True,
            "reset_between_tasks": True,
        },
        "summary": {
            "task_count": len(task_rows),
            "provider_usage": usage_summary,
            "complete_agent_output_count": sum(
                len(row["provider_outputs"]) for row in task_rows
            ),
        },
        "tasks": task_rows,
    }
    (output_dir / "workflow_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a generic integrated AutoGen semantic preflight."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--scenario-file", type=Path, required=True)
    parser.add_argument("--team-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    scenario = _load_object(args.scenario_file.resolve())
    team_config = _load_object(args.team_config.resolve())
    commit = _git_commit(repo_root)
    if scenario.get("schema_version") != (
        "agentlite.v515f.integrated-scenario.v1"
    ):
        raise ValueError("unsupported integrated scenario schema")
    if team_config.get("schema_version") != (
        "agentlite.v515f.team-config.v1"
    ):
        raise ValueError("unsupported integrated team schema")
    if scenario.get("authored_after_commit") != commit:
        raise ValueError("scenario is not bound to the current commit")
    if team_config.get("authored_after_commit") != commit:
        raise ValueError("team config is not bound to the current commit")
    asyncio.run(
        run_workflow(
            scenario=scenario,
            team_config=team_config,
            output_dir=args.output_dir.resolve(),
            implementation_commit=commit,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
