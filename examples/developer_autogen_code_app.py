from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


DEFAULT_QUESTION = (
    "请为 openEuler 上的多 Agent 协作运行时设计一个可落地方案，要求包含 Agent 分工、"
    "状态传递方式、低开销通信机制、风险控制和实验验证指标。"
)
DONE_TOKEN = "FINAL_ANSWER_READY"
DEFAULT_MODEL = "mimo-v2.5"


DEFAULT_AGENTS = [
    {
        "name": "planner",
        "description": "负责拆解任务、规划协作路线。",
        "system_prompt": (
            "你是 PlannerAgent。请把用户任务拆解为可执行步骤，明确每个 Agent 的职责、"
            "输入输出和协作顺序。不要写最终答案，只给规划。"
        ),
    },
    {
        "name": "writer",
        "description": "负责整合上下文并生成方案草案。",
        "system_prompt": (
            "你是 WriterAgent。请根据用户问题和已有团队消息，生成完整、可执行的方案草案。"
            "必须覆盖架构、运行流程、实验指标和预期结果。"
        ),
    },
    {
        "name": "reviewer",
        "description": "负责审查方案并给出最终答案。",
        "system_prompt": (
            "你是 ReviewerAgent。请检查团队方案是否完整、准确、可执行，补齐遗漏后输出最终答案。"
            f"最终答案末尾必须包含 {DONE_TOKEN}。"
        ),
    },
]


@dataclass
class LLMResult:
    content: str
    usage: dict[str, int]
    wall_time_ms: int
    retry_count: int = 0


class OpenAICompatibleClient:
    """Small OpenAI-compatible client a normal app can embed for cost logging."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float,
        timeout_seconds: int = 120,
        max_retries: int = 4,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)

    @classmethod
    def from_env(cls, *, temperature: float) -> "OpenAICompatibleClient":
        api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENAI_COMPAT_API_KEY")
            or os.getenv("MIMO_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or ""
        )
        if not api_key:
            raise RuntimeError(
                "Missing API key. Set OPENAI_API_KEY, OPENAI_COMPAT_API_KEY, "
                "MIMO_API_KEY, or DEEPSEEK_API_KEY."
            )
        return cls(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
            temperature=temperature,
            timeout_seconds=int(os.getenv("OPENAI_TIMEOUT_SECONDS", "120")),
            max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "4")),
            retry_backoff_seconds=float(
                os.getenv("OPENAI_RETRY_BACKOFF_SECONDS", "2")
            ),
        )

    def complete(self, messages: list[dict[str, str]]) -> LLMResult:
        url = self.base_url
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        started = time.perf_counter()
        raw: dict[str, Any] | None = None
        retry_count = 0
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_seconds
                ) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code != 429 and exc.code < 500:
                    raise RuntimeError(
                        f"LLM request failed: HTTP {exc.code}: {detail}"
                    ) from exc
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"LLM request failed after {attempt + 1} attempts: "
                        f"HTTP {exc.code}: {detail}"
                    ) from exc
            except (
                urllib.error.URLError,
                http.client.RemoteDisconnected,
                TimeoutError,
                ConnectionError,
            ) as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"LLM connection failed after {attempt + 1} attempts: {exc}"
                    ) from exc
            retry_count += 1
            time.sleep(self.retry_backoff_seconds * (attempt + 1))
        if raw is None:
            raise RuntimeError("LLM request failed without a provider response")
        wall_time_ms = int((time.perf_counter() - started) * 1000)
        choices = raw.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM response missing choices: {raw}")
        content = str(choices[0].get("message", {}).get("content") or "").strip()
        if not content:
            raise RuntimeError(f"LLM response content is empty: {raw}")
        return LLMResult(
            content=content,
            usage=_normalize_usage(raw.get("usage", {})),
            wall_time_ms=wall_time_ms,
            retry_count=retry_count,
        )


class CostLogger:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rows: list[dict[str, Any]] = []

    def add(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        with (self.output_dir / "llm_usage.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def summary(self) -> dict[str, Any]:
        by_agent: dict[str, dict[str, int]] = {}
        for row in self.rows:
            agent = str(row["agent"])
            bucket = by_agent.setdefault(
                agent,
                {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "wall_time_ms": 0,
                    "retry_count": 0,
                },
            )
            bucket["calls"] += 1
            bucket["prompt_tokens"] += int(row["prompt_tokens"])
            bucket["completion_tokens"] += int(row["completion_tokens"])
            bucket["total_tokens"] += int(row["total_tokens"])
            bucket["wall_time_ms"] += int(row["wall_time_ms"])
            bucket["retry_count"] += int(row.get("retry_count", 0))
        return {
            "calls": len(self.rows),
            "llm_prompt_tokens": sum(int(row["prompt_tokens"]) for row in self.rows),
            "llm_completion_tokens": sum(
                int(row["completion_tokens"]) for row in self.rows
            ),
            "llm_total_tokens": sum(int(row["total_tokens"]) for row in self.rows),
            "llm_wall_time_ms": sum(int(row["wall_time_ms"]) for row in self.rows),
            "retry_count": sum(int(row.get("retry_count", 0)) for row in self.rows),
            "by_agent": by_agent,
        }

    def write_summary(self) -> Path:
        path = self.output_dir / "llm_usage_summary.json"
        path.write_text(json.dumps(self.summary(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def main() -> int:
    parser = argparse.ArgumentParser(description="A normal developer-style AutoGen app.")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--question-file", type=Path)
    parser.add_argument("--agent-config", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("runs/developer-code-app"))
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-turns", type=int, default=6)
    args = parser.parse_args()

    question = args.question_file.read_text(encoding="utf-8").strip() if args.question_file else args.question
    output_dir = args.output_dir.expanduser().resolve()
    payload = asyncio.run(
        run_team(
            question=question,
            agent_configs=_load_agent_config(args.agent_config),
            output_dir=output_dir,
            temperature=args.temperature,
            max_turns=args.max_turns,
        )
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


async def run_team(
    *,
    question: str,
    agent_configs: list[dict[str, str]],
    output_dir: Path,
    temperature: float,
    max_turns: int,
) -> dict[str, Any]:
    from autogen_agentchat.agents import BaseChatAgent
    from autogen_agentchat.base import Response
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.messages import BaseChatMessage, TextMessage
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_core import CancellationToken

    llm = OpenAICompatibleClient.from_env(temperature=temperature)
    cost_logger = CostLogger(output_dir)

    class DeveloperAgent(BaseChatAgent):
        def __init__(self, config: dict[str, str]) -> None:
            super().__init__(
                name=config["name"],
                description=config.get("description", config["name"]),
            )
            self.system_prompt = config["system_prompt"]

        @property
        def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
            return (TextMessage,)

        async def on_messages(
            self,
            messages: Sequence[BaseChatMessage],
            cancellation_token: CancellationToken,
        ) -> Response:
            del cancellation_token
            prompt = _team_context_prompt(self.name, messages)
            result = await asyncio.to_thread(
                llm.complete,
                [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ],
            )
            cost_logger.add(
                {
                    "agent": self.name,
                    "model": llm.model,
                    "prompt_tokens": result.usage["prompt_tokens"],
                    "completion_tokens": result.usage["completion_tokens"],
                    "total_tokens": result.usage["total_tokens"],
                    "wall_time_ms": result.wall_time_ms,
                    "retry_count": result.retry_count,
                    "ts": time.time(),
                }
            )
            return Response(
                chat_message=TextMessage(content=result.content, source=self.name)
            )

        async def on_reset(self, cancellation_token: CancellationToken) -> None:
            del cancellation_token

    agents = [DeveloperAgent(config) for config in agent_configs]
    kwargs: dict[str, Any] = {"termination_condition": TextMentionTermination(DONE_TOKEN)}
    if max_turns > 0:
        kwargs["max_turns"] = max_turns
    try:
        team = RoundRobinGroupChat(agents, **kwargs)
    except TypeError:
        kwargs.pop("max_turns", None)
        team = RoundRobinGroupChat(agents, **kwargs)
    started = time.perf_counter()
    result = await team.run(task=question)
    wall_time_ms = int((time.perf_counter() - started) * 1000)
    usage_summary_path = cost_logger.write_summary()
    final_answer = _final_answer(result)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "final_answer.md").write_text(final_answer + "\n", encoding="utf-8")
    run_payload = {
        "question": question,
        "wall_time_ms": wall_time_ms,
        "llm_usage": cost_logger.summary(),
        "agent_configs": agent_configs,
        "messages": _messages_to_dict(result),
        "final_answer_path": str(output_dir / "final_answer.md"),
        "llm_usage_summary_path": str(usage_summary_path),
    }
    (output_dir / "run_result.json").write_text(
        json.dumps(run_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "summary": {
            "output_dir": str(output_dir),
            "wall_time_ms": wall_time_ms,
            "llm_total_tokens": cost_logger.summary()["llm_total_tokens"],
            "final_answer_path": str(output_dir / "final_answer.md"),
            "llm_usage_summary_path": str(usage_summary_path),
        },
        "raw": run_payload,
    }


def _team_context_prompt(
    agent_name: str,
    messages: Sequence[Any],
) -> str:
    lines = [
        f"当前 Agent：{agent_name}",
        "你正在参与一个多 Agent 团队。请基于已有团队消息继续推进任务。",
        "",
        "团队消息：",
    ]
    for message in messages:
        source = str(getattr(message, "source", "unknown") or "unknown")
        content = str(getattr(message, "content", "") or "")
        lines.append(f"[{source}] {content}")
    lines.append("")
    lines.append("请输出你的下一步内容。")
    return "\n".join(lines)


def _load_agent_config(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return DEFAULT_AGENTS
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("agent config must be a JSON list")
    return [
        {
            "name": str(item["name"]),
            "description": str(item.get("description") or item["name"]),
            "system_prompt": str(item["system_prompt"]),
        }
        for item in value
    ]


def _messages_to_dict(result: Any) -> list[dict[str, Any]]:
    return [
        {
            "type": type(message).__name__,
            "source": getattr(message, "source", ""),
            "content": str(getattr(message, "content", "") or ""),
        }
        for message in (getattr(result, "messages", []) or [])
    ]


def _final_answer(result: Any) -> str:
    for message in reversed(getattr(result, "messages", []) or []):
        content = str(getattr(message, "content", "") or "").strip()
        if content:
            return content
    return ""


def _normalize_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt = int(value.get("prompt_tokens", value.get("input_tokens", 0)) or 0)
    completion = int(
        value.get("completion_tokens", value.get("output_tokens", 0)) or 0
    )
    total = int(value.get("total_tokens", prompt + completion) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


if __name__ == "__main__":
    raise SystemExit(main())
