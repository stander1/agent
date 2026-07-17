from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from agent_runtime.reliability.final_delivery_guard import (
    FinalDeliveryAssessment,
    assess_final_delivery,
    has_exact_last_line_marker,
    strip_exact_last_line_marker,
)


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


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    question: str


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
        self.usage_path = self.output_dir / "llm_usage.jsonl"
        self.usage_path.write_text("", encoding="utf-8")
        self.rows: list[dict[str, Any]] = []

    def add(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        with self.usage_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def summary(self) -> dict[str, Any]:
        summary = _summarize_usage_rows(self.rows)
        by_task: dict[str, dict[str, Any]] = {}
        for row in self.rows:
            task_id = str(row.get("task_id") or "single")
            by_task.setdefault(task_id, {"rows": []})["rows"].append(row)
        summary["by_task"] = {
            task_id: _summarize_usage_rows(bucket["rows"])
            for task_id, bucket in by_task.items()
        }
        return summary

    def write_summary(self) -> Path:
        path = self.output_dir / "llm_usage_summary.json"
        path.write_text(json.dumps(self.summary(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def main() -> int:
    parser = argparse.ArgumentParser(description="A normal developer-style AutoGen app.")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--question-file", type=Path)
    parser.add_argument(
        "--question-sequence-file",
        type=Path,
        help="JSON task sequence executed by one stateful AutoGen team.",
    )
    parser.add_argument("--agent-config", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("runs/developer-code-app"))
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument(
        "--experiment-mode",
        choices=("native", "observed", "managed", "unspecified"),
        default="unspecified",
        help="Metadata only; AgentLite behavior is still selected by the launcher.",
    )
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    agent_configs = _load_agent_config(args.agent_config)
    if args.question_sequence_file:
        scenario_id, tasks = _load_question_sequence(args.question_sequence_file)
        payload = asyncio.run(
            run_task_sequence(
                scenario_id=scenario_id,
                tasks=tasks,
                agent_configs=agent_configs,
                output_dir=output_dir,
                temperature=args.temperature,
                max_turns=args.max_turns,
                experiment_mode=args.experiment_mode,
            )
        )
    else:
        question = (
            args.question_file.read_text(encoding="utf-8").strip()
            if args.question_file
            else args.question
        )
        payload = asyncio.run(
            run_team(
                question=question,
                agent_configs=agent_configs,
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
    payload = await _run_tasks(
        scenario_id="single",
        tasks=[TaskSpec(task_id="task_001", question=question)],
        agent_configs=agent_configs,
        output_dir=output_dir,
        temperature=temperature,
        max_turns=max_turns,
        experiment_mode="unspecified",
    )
    task_payload = payload["raw"]["tasks"][0]
    final_answer_path = output_dir / "final_answer.md"
    final_answer_path.write_text(
        task_payload["final_answer"] + "\n", encoding="utf-8"
    )
    run_payload = {
        "question": question,
        "wall_time_ms": task_payload["wall_time_ms"],
        "llm_usage": task_payload["llm_usage"],
        "agent_configs": agent_configs,
        "messages": task_payload["messages"],
        "stop_reason": task_payload["stop_reason"],
        "delivery_valid": task_payload["delivery_valid"],
        "delivery_status": task_payload["delivery_status"],
        "delivery_guard_reasons": task_payload["delivery_guard_reasons"],
        "missing_delivery_requirements": task_payload[
            "missing_delivery_requirements"
        ],
        "semantic_retry_count": task_payload["semantic_retry_count"],
        "final_answer_path": str(final_answer_path),
        "llm_usage_summary_path": str(output_dir / "llm_usage_summary.json"),
    }
    (output_dir / "run_result.json").write_text(
        json.dumps(run_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "summary": {
            "output_dir": str(output_dir),
            "wall_time_ms": task_payload["wall_time_ms"],
            "llm_total_tokens": task_payload["llm_usage"]["llm_total_tokens"],
            "delivery_valid": task_payload["delivery_valid"],
            "delivery_status": task_payload["delivery_status"],
            "semantic_retry_count": task_payload["semantic_retry_count"],
            "final_answer_path": str(final_answer_path),
            "llm_usage_summary_path": str(output_dir / "llm_usage_summary.json"),
        },
        "raw": run_payload,
    }


async def run_task_sequence(
    *,
    scenario_id: str,
    tasks: list[TaskSpec],
    agent_configs: list[dict[str, str]],
    output_dir: Path,
    temperature: float,
    max_turns: int,
    experiment_mode: str,
) -> dict[str, Any]:
    if len(tasks) < 2:
        raise ValueError("a stateful sequence requires at least two tasks")
    return await _run_tasks(
        scenario_id=scenario_id,
        tasks=tasks,
        agent_configs=agent_configs,
        output_dir=output_dir,
        temperature=temperature,
        max_turns=max_turns,
        experiment_mode=experiment_mode,
    )


async def _run_tasks(
    *,
    scenario_id: str,
    tasks: list[TaskSpec],
    agent_configs: list[dict[str, str]],
    output_dir: Path,
    temperature: float,
    max_turns: int,
    experiment_mode: str,
) -> dict[str, Any]:
    from autogen_agentchat.agents import BaseChatAgent
    from autogen_agentchat.base import Response
    from autogen_agentchat.messages import BaseChatMessage, TextMessage
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_core import CancellationToken

    from agent_runtime.adapters.autogen_termination import (
        ReviewerFinalTextTermination,
    )

    llm = OpenAICompatibleClient.from_env(temperature=temperature)
    cost_logger = CostLogger(output_dir)
    current_task_id = "unassigned"

    class DeveloperAgent(BaseChatAgent):
        def __init__(self, config: dict[str, str]) -> None:
            super().__init__(
                name=config["name"],
                description=config.get("description", config["name"]),
            )
            self.system_prompt = config["system_prompt"]
            self._history: list[BaseChatMessage] = []

        @property
        def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
            return (TextMessage,)

        async def on_messages(
            self,
            messages: Sequence[BaseChatMessage],
            cancellation_token: CancellationToken,
        ) -> Response:
            del cancellation_token
            self._history.extend(messages)
            prompt = _team_context_prompt(self.name, self._history)
            result = await asyncio.to_thread(
                llm.complete,
                [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ],
            )
            cost_logger.add(
                {
                    "task_id": current_task_id,
                    "agent": self.name,
                    "model": llm.model,
                    "prompt_tokens": result.usage["prompt_tokens"],
                    "completion_tokens": result.usage["completion_tokens"],
                    "total_tokens": result.usage["total_tokens"],
                    "wall_time_ms": result.wall_time_ms,
                    "retry_count": result.retry_count,
                    "prompt_chars": len(prompt),
                    "history_message_count": len(self._history),
                    "ts": time.time(),
                }
            )
            return Response(
                chat_message=TextMessage(content=result.content, source=self.name)
            )

        async def on_reset(self, cancellation_token: CancellationToken) -> None:
            del cancellation_token
            self._history.clear()

        def remember_repaired_final(self, content: str) -> None:
            self._history.append(TextMessage(content=content, source="reviewer"))

    agents = [DeveloperAgent(config) for config in agent_configs]
    kwargs: dict[str, Any] = {
        "termination_condition": ReviewerFinalTextTermination(
            marker=DONE_TOKEN,
            source="reviewer",
        )
    }
    if max_turns > 0:
        kwargs["max_turns"] = max_turns
    try:
        team = RoundRobinGroupChat(agents, **kwargs)
    except TypeError:
        kwargs.pop("max_turns", None)
        team = RoundRobinGroupChat(agents, **kwargs)

    sequence_started = time.perf_counter()
    sequence_run_id = uuid.uuid4().hex
    task_payloads: list[dict[str, Any]] = []
    quality_candidates: list[dict[str, Any]] = []
    quality_mapping: list[dict[str, Any]] = []
    reviewer_config = next(
        (config for config in agent_configs if config.get("name") == "reviewer"),
        agent_configs[-1],
    )
    for task_index, task in enumerate(tasks, start=1):
        current_task_id = task.task_id
        usage_start = len(cost_logger.rows)
        task_started = time.perf_counter()
        result = await team.run(task=task.question)
        final_source, raw_final_answer = _final_message(result)
        assessment = _assess_task_delivery(
            request=task.question,
            source=final_source,
            content=raw_final_answer,
        )
        semantic_retry_count = 0
        messages = _messages_to_dict(result)
        if not assessment.valid:
            semantic_retry_count = 1
            writer_draft = _latest_message_content(result, "writer")
            repaired = await _context_pruned_reviewer_retry(
                llm=llm,
                cost_logger=cost_logger,
                task_id=current_task_id,
                reviewer_system_prompt=reviewer_config["system_prompt"],
                request=task.question,
                writer_draft=writer_draft,
                previous_reviewer_output=raw_final_answer,
                assessment=assessment,
            )
            raw_final_answer = repaired
            final_source = "reviewer"
            assessment = _assess_task_delivery(
                request=task.question,
                source=final_source,
                content=raw_final_answer,
            )
            messages.append(
                {
                    "type": "ContextPrunedRetryTextMessage",
                    "source": "reviewer",
                    "content": raw_final_answer,
                }
            )
            for agent in agents:
                agent.remember_repaired_final(raw_final_answer)

        task_wall_time_ms = int((time.perf_counter() - task_started) * 1000)
        task_usage = _summarize_usage_rows(cost_logger.rows[usage_start:])
        delivery_valid = assessment.valid
        final_answer = assessment.body
        stop_reason = str(getattr(result, "stop_reason", "") or "")
        task_dir = output_dir / "tasks" / task.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        final_answer_path = task_dir / "final_answer.md"
        final_answer_path.write_text(final_answer + "\n", encoding="utf-8")
        task_payload = {
            "task_id": task.task_id,
            "task_index": task_index,
            "question": task.question,
            "wall_time_ms": task_wall_time_ms,
            "llm_usage": task_usage,
            "stop_reason": stop_reason,
            "final_source": final_source,
            "final_marker_present": _has_exact_done_token(raw_final_answer),
            "delivery_valid": delivery_valid,
            "delivery_status": assessment.status,
            "delivery_guard_reasons": list(assessment.reasons),
            "missing_delivery_requirements": list(
                assessment.missing_requirements
            ),
            "semantic_retry_count": semantic_retry_count,
            "final_answer": final_answer,
            "final_answer_chars": len(final_answer),
            "final_answer_path": str(final_answer_path),
            "messages": messages,
        }
        (task_dir / "run_result.json").write_text(
            json.dumps(task_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        task_payloads.append(task_payload)
        blind_id = f"candidate_{uuid.uuid4().hex[:12]}"
        quality_candidates.append(
            {
                "candidate_id": blind_id,
                "question": task.question,
                "answer": final_answer,
            }
        )
        quality_mapping.append(
            {
                "candidate_id": blind_id,
                "scenario_id": scenario_id,
                "task_id": task.task_id,
                "experiment_mode": experiment_mode,
                "delivery_valid": delivery_valid,
            }
        )

    wall_time_ms = int((time.perf_counter() - sequence_started) * 1000)
    usage_summary_path = cost_logger.write_summary()
    summary = {
        "scenario_id": scenario_id,
        "sequence_run_id": sequence_run_id,
        "experiment_mode": experiment_mode,
        "same_team_instance": True,
        "task_count": len(task_payloads),
        "valid_delivery_count": sum(
            int(task["delivery_valid"]) for task in task_payloads
        ),
        "degraded_delivery_count": sum(
            int(not task["delivery_valid"]) for task in task_payloads
        ),
        "semantic_retry_count": sum(
            int(task["semantic_retry_count"]) for task in task_payloads
        ),
        "wall_time_ms": wall_time_ms,
        "llm_usage": cost_logger.summary(),
        "llm_total_tokens": cost_logger.summary()["llm_total_tokens"],
        "llm_usage_summary_path": str(usage_summary_path),
    }
    sequence_payload = {
        "summary": summary,
        "agent_configs": agent_configs,
        "tasks": task_payloads,
    }
    (output_dir / "sequence_result.json").write_text(
        json.dumps(sequence_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "quality_blind_candidates.json").write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "candidates": quality_candidates,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "quality_blind_mapping.json").write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "sequence_run_id": sequence_run_id,
                "mapping": quality_mapping,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "summary": summary,
        "raw": sequence_payload,
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


def _load_question_sequence(path: Path) -> tuple[str, list[TaskSpec]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        scenario_id = str(value.get("scenario_id") or path.stem)
        raw_tasks = value.get("tasks")
    else:
        scenario_id = path.stem
        raw_tasks = value
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("question sequence must contain a non-empty tasks list")
    tasks: list[TaskSpec] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_tasks, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"task {index} must be a JSON object")
        task_id = str(item.get("task_id") or "").strip()
        question = str(item.get("question") or "").strip()
        if not task_id or not question:
            raise ValueError(f"task {index} requires task_id and question")
        if task_id in seen_ids:
            raise ValueError(f"duplicate task_id: {task_id}")
        seen_ids.add(task_id)
        tasks.append(TaskSpec(task_id=task_id, question=question))
    return scenario_id, tasks


def _summarize_usage_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_agent: dict[str, dict[str, int]] = {}
    for row in rows:
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
        "calls": len(rows),
        "llm_prompt_tokens": sum(int(row["prompt_tokens"]) for row in rows),
        "llm_completion_tokens": sum(
            int(row["completion_tokens"]) for row in rows
        ),
        "llm_total_tokens": sum(int(row["total_tokens"]) for row in rows),
        "llm_wall_time_ms": sum(int(row["wall_time_ms"]) for row in rows),
        "retry_count": sum(int(row.get("retry_count", 0)) for row in rows),
        "by_agent": by_agent,
    }


def _messages_to_dict(result: Any) -> list[dict[str, Any]]:
    return [
        {
            "type": type(message).__name__,
            "source": getattr(message, "source", ""),
            "content": str(getattr(message, "content", "") or ""),
        }
        for message in (getattr(result, "messages", []) or [])
    ]


async def _context_pruned_reviewer_retry(
    *,
    llm: OpenAICompatibleClient,
    cost_logger: CostLogger,
    task_id: str,
    reviewer_system_prompt: str,
    request: str,
    writer_draft: str,
    previous_reviewer_output: str,
    assessment: FinalDeliveryAssessment,
) -> str:
    missing = "、".join(assessment.missing_requirements) or "完整、可直接交付的正文"
    reasons = "、".join(assessment.reasons) or "最终交付语义校验失败"
    prompt = "\n".join(
        [
            "这是一次短上下文最终交付修复，只保留当前问题和本轮必要证据。",
            "不要输出审查过程、修订意见或要求其他 Agent 继续修改。",
            "请直接给用户一份自包含、完整、可执行的最终交付物。",
            "",
            "当前用户请求：",
            request,
            "",
            "Writer 最新草案：",
            writer_draft or "（本轮没有可用 Writer 草案，请根据用户请求直接完成。）",
            "",
            "Reviewer 上次未通过的输出：",
            previous_reviewer_output,
            "",
            f"未通过原因：{reasons}",
            f"必须补齐：{missing}",
            f"最后一行必须单独写 {DONE_TOKEN}",
        ]
    )
    started = time.perf_counter()
    result = await asyncio.to_thread(
        llm.complete,
        [
            {"role": "system", "content": reviewer_system_prompt},
            {"role": "user", "content": prompt},
        ],
    )
    cost_logger.add(
        {
            "task_id": task_id,
            "agent": "reviewer",
            "model": llm.model,
            "prompt_tokens": result.usage["prompt_tokens"],
            "completion_tokens": result.usage["completion_tokens"],
            "total_tokens": result.usage["total_tokens"],
            "wall_time_ms": result.wall_time_ms,
            "retry_count": result.retry_count,
            "semantic_delivery_retry": True,
            "prompt_chars": len(prompt),
            "history_message_count": 3,
            "local_wall_time_ms": int((time.perf_counter() - started) * 1000),
            "ts": time.time(),
        }
    )
    return result.content


def _latest_message_content(result: Any, source: str) -> str:
    for message in reversed(getattr(result, "messages", []) or []):
        if str(getattr(message, "source", "") or "") != source:
            continue
        content = str(getattr(message, "content", "") or "").strip()
        if content:
            return content
    return ""


def _final_message(result: Any) -> tuple[str, str]:
    for message in reversed(getattr(result, "messages", []) or []):
        content = str(getattr(message, "content", "") or "").strip()
        if content:
            source = str(getattr(message, "source", "") or "")
            return source, content
    return "", ""


def _has_exact_done_token(content: str) -> bool:
    return has_exact_last_line_marker(content, DONE_TOKEN)


def _assess_task_delivery(
    *,
    request: str,
    source: str,
    content: str,
) -> FinalDeliveryAssessment:
    return assess_final_delivery(
        request=request,
        source=source,
        expected_source="reviewer",
        content=content,
        marker=DONE_TOKEN,
        require_marker=True,
        minimum_body_chars=80,
    )


def _is_valid_final_delivery(
    *,
    source: str,
    content: str,
    request: str = "",
) -> bool:
    return _assess_task_delivery(
        request=request,
        source=source,
        content=content,
    ).valid


def _strip_done_token(content: str) -> str:
    return strip_exact_last_line_marker(content, DONE_TOKEN)


def _final_answer(result: Any) -> str:
    return _final_message(result)[1]


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
