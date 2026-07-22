from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from agent_runtime.eval.experiment_archive import (
    ExperimentRunIdentity,
    complete_experiment_archive,
    fail_experiment_archive,
    initialize_experiment_archive,
)


DONE_TOKEN = "FINAL_ANSWER_READY"
DEFAULT_MODEL = "mimo-v2.5"

EVIDENCE_RECORDS = (
    {
        "evidence_id": "OE-STATE-01",
        "topic": "process_memory",
        "text": "纯进程内存延迟低，但进程退出后状态丢失，不能单独满足重启恢复要求。",
    },
    {
        "evidence_id": "OE-STATE-02",
        "topic": "sqlite_wal",
        "text": "SQLite WAL 允许读写并发，并可通过 busy_timeout、短事务和单写者队列降低锁竞争。",
    },
    {
        "evidence_id": "OE-STATE-03",
        "topic": "file_payload",
        "text": "大载荷适合写入内容寻址文件，SQLite 只保存元数据、版本、引用、哈希和生命周期状态。",
    },
    {
        "evidence_id": "OE-STATE-04",
        "topic": "external_services",
        "text": "Redis 加 PostgreSQL 能扩展到多节点，但会引入两个外部服务，不符合本实验的单机无外部依赖约束。",
    },
    {
        "evidence_id": "OE-STATE-05",
        "topic": "concurrency",
        "text": "50 个逻辑任务不应对应 50 个 SQLite 并发写事务；应限制写并发、批量提交并让读取与模型调用并行。",
    },
    {
        "evidence_id": "OE-STATE-06",
        "topic": "retention",
        "text": "审计数据应按创建时间分区或索引，先写 tombstone，再在无活跃读租约时分批物理清理。",
    },
    {
        "evidence_id": "OE-STATE-07",
        "topic": "recovery",
        "text": "恢复流程应校验 SQLite integrity_check、WAL checkpoint、载荷 SHA-256 和未完成任务状态。",
    },
)


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    question: str


@dataclass
class LLMResult:
    content: str
    usage: dict[str, int]
    wall_time_ms: int
    retry_count: int


class OpenAICompatibleClient:
    def __init__(self, *, temperature: float) -> None:
        self.api_key = (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENAI_COMPAT_API_KEY")
            or os.getenv("MIMO_API_KEY")
            or ""
        )
        if not self.api_key:
            raise RuntimeError("Set OPENAI_API_KEY or MIMO_API_KEY before running.")
        self.base_url = os.getenv(
            "OPENAI_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"
        ).rstrip("/")
        self.model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
        self.temperature = temperature
        self.timeout_seconds = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "300"))
        self.max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "6"))
        self.retry_backoff_seconds = float(
            os.getenv("OPENAI_RETRY_BACKOFF_SECONDS", "3")
        )

    def complete(self, messages: list[dict[str, str]]) -> LLMResult:
        url = self.base_url
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        started = time.perf_counter()
        retry_count = 0
        last_error: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(
                url,
                data=body,
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
                choices = raw.get("choices") or []
                content = str(
                    choices[0].get("message", {}).get("content") if choices else ""
                ).strip()
                if not choices or not content:
                    raise RuntimeError("Provider response has no usable choices/content")
                return LLMResult(
                    content=content,
                    usage=_normalize_usage(raw.get("usage", {})),
                    wall_time_ms=int((time.perf_counter() - started) * 1000),
                    retry_count=retry_count,
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
                if exc.code < 500 and exc.code != 429:
                    raise last_error from exc
            except (
                urllib.error.URLError,
                http.client.RemoteDisconnected,
                TimeoutError,
                ConnectionError,
                json.JSONDecodeError,
                RuntimeError,
            ) as exc:
                last_error = exc
            if attempt >= self.max_retries:
                break
            retry_count += 1
            time.sleep(self.retry_backoff_seconds * (attempt + 1))
        raise RuntimeError(
            f"LLM request failed after {self.max_retries + 1} attempts: {last_error}"
        ) from last_error


class CostLogger:
    def __init__(
        self, output_dir: Path, *, run_identity: ExperimentRunIdentity
    ) -> None:
        self.output_dir = output_dir
        self.run_identity = run_identity
        self.rows: list[dict[str, Any]] = []
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def add(self, row: dict[str, Any]) -> None:
        bound = {**row, **self.run_identity.binding()}
        self.rows.append(bound)
        with (self.output_dir / "llm_usage.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(bound, ensure_ascii=False) + "\n")

    def summary(self, rows: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
        source = list(rows if rows is not None else self.rows)
        by_agent: dict[str, dict[str, int]] = {}
        for row in source:
            bucket = by_agent.setdefault(
                str(row["agent"]),
                {
                    "calls": 0,
                    "llm_prompt_tokens": 0,
                    "llm_completion_tokens": 0,
                    "llm_total_tokens": 0,
                    "llm_wall_time_ms": 0,
                    "retry_count": 0,
                },
            )
            bucket["calls"] += 1
            for key in (
                "llm_prompt_tokens",
                "llm_completion_tokens",
                "llm_total_tokens",
                "llm_wall_time_ms",
                "retry_count",
            ):
                bucket[key] += int(row.get(key, 0))
        return {
            "calls": len(source),
            "llm_prompt_tokens": sum(
                int(row.get("llm_prompt_tokens", 0)) for row in source
            ),
            "llm_completion_tokens": sum(
                int(row.get("llm_completion_tokens", 0)) for row in source
            ),
            "llm_total_tokens": sum(
                int(row.get("llm_total_tokens", 0)) for row in source
            ),
            "llm_wall_time_ms": sum(
                int(row.get("llm_wall_time_ms", 0)) for row in source
            ),
            "retry_count": sum(int(row.get("retry_count", 0)) for row in source),
            "by_agent": by_agent,
        }

    def write_summary(self) -> Path:
        by_task: dict[str, dict[str, Any]] = {}
        for row in self.rows:
            by_task.setdefault(str(row["task_id"]), {"rows": []})["rows"].append(
                row
            )
        payload = {
            **self.summary(),
            "by_task": {
                task_id: self.summary(bucket["rows"])
                for task_id, bucket in by_task.items()
            },
            "binding": self.run_identity.binding(),
        }
        path = self.output_dir / "llm_usage_summary.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path


def local_evidence_lookup(query: str) -> str:
    """Return versioned local evidence for the openEuler state-service scenario."""
    lowered = str(query or "").casefold()
    selected = [
        record
        for record in EVIDENCE_RECORDS
        if str(record["topic"]).casefold() in lowered
        or any(
            cue in lowered
            for cue in (
                "sqlite",
                "wal",
                "并发",
                "恢复",
                "审计",
                "外部服务",
                "状态",
            )
        )
    ]
    if not selected:
        selected = list(EVIDENCE_RECORDS)
    return json.dumps(selected, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Real AutoGen dynamic-capability acceptance app."
    )
    parser.add_argument("--task-sequence", type=Path, required=True)
    parser.add_argument("--agent-config", type=Path, required=True)
    parser.add_argument("--experiment-mode", choices=("native", "observed", "managed"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-turns", type=int, default=8)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    scenario_id, tasks = _load_tasks(args.task_sequence)
    agent_configs = _load_agents(args.agent_config)
    run_identity = initialize_experiment_archive(
        output_dir=output_dir,
        scenario_id=scenario_id,
        experiment_mode=args.experiment_mode,
        source_paths=(args.task_sequence, args.agent_config),
        provider_model=os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        provider_base_url=os.getenv(
            "OPENAI_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"
        ),
        command=sys.argv,
    )
    try:
        payload = asyncio.run(
            run_sequence(
                scenario_id=scenario_id,
                tasks=tasks,
                agent_configs=agent_configs,
                output_dir=output_dir,
                temperature=args.temperature,
                max_turns=args.max_turns,
                experiment_mode=args.experiment_mode,
                run_identity=run_identity,
            )
        )
        complete_experiment_archive(
            run_identity,
            summary=payload["summary"],
            artifact_paths=(path for path in output_dir.rglob("*") if path.is_file()),
        )
    except BaseException as exc:
        fail_experiment_archive(
            run_identity,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        raise
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


async def run_sequence(
    *,
    scenario_id: str,
    tasks: list[TaskSpec],
    agent_configs: list[dict[str, Any]],
    output_dir: Path,
    temperature: float,
    max_turns: int,
    experiment_mode: str,
    run_identity: ExperimentRunIdentity,
) -> dict[str, Any]:
    from autogen_agentchat.agents import BaseChatAgent
    from autogen_agentchat.base import Response
    from autogen_agentchat.messages import BaseChatMessage, TextMessage
    from autogen_agentchat.teams import RoundRobinGroupChat
    from autogen_core import CancellationToken
    from autogen_core.tools import FunctionTool

    from agent_runtime.adapters.autogen_termination import ReviewerFinalTextTermination

    llm = OpenAICompatibleClient(temperature=temperature)
    logger = CostLogger(output_dir, run_identity=run_identity)
    current_task_id = "unassigned"
    tool_call_rows: list[dict[str, Any]] = []

    evidence_tool = FunctionTool(
        local_evidence_lookup,
        description=(
            "Search versioned local openEuler deployment evidence and return "
            "evidence_id, topic, and verified text."
        ),
        name="local_evidence_lookup",
    )
    evidence_tool.metadata = {
        "capability_tags": ["retrieval", "evidence_ranking"],
        "supported_actions": ["RETRIEVE_EVIDENCE", "VERIFY_CLAIM"],
        "cost_level": 0.05,
    }

    class AcceptanceAgent(BaseChatAgent):
        def __init__(self, config: dict[str, Any]) -> None:
            super().__init__(
                name=str(config["name"]),
                description=str(config.get("description") or config["name"]),
            )
            self.system_message = str(config["system_prompt"])
            self._history: list[BaseChatMessage] = []
            self._tools = [evidence_tool] if config.get("use_local_evidence_tool") else []

        @property
        def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
            return (TextMessage,)

        async def on_messages(
            self,
            messages: Sequence[BaseChatMessage],
            cancellation_token: CancellationToken,
        ) -> Response:
            self._history.extend(messages)
            tool_context = ""
            if self._tools:
                query = _latest_user_or_message(self._history)
                tool_started = time.perf_counter()
                tool_context = str(
                    await self._tools[0].run_json(
                        {"query": query}, cancellation_token
                    )
                )
                tool_call_rows.append(
                    {
                        "task_id": current_task_id,
                        "agent": self.name,
                        "tool_id": self._tools[0].name,
                        "query_chars": len(query),
                        "result_chars": len(tool_context),
                        "wall_time_ms": int(
                            (time.perf_counter() - tool_started) * 1000
                        ),
                    }
                )
            prompt = _team_prompt(
                agent_name=self.name,
                messages=self._history,
                tool_context=tool_context,
            )
            result = await asyncio.to_thread(
                llm.complete,
                [
                    {"role": "system", "content": self.system_message},
                    {"role": "user", "content": prompt},
                ],
            )
            logger.add(
                {
                    "task_id": current_task_id,
                    "agent": self.name,
                    "model": llm.model,
                    "llm_prompt_tokens": result.usage["prompt_tokens"],
                    "llm_completion_tokens": result.usage["completion_tokens"],
                    "llm_total_tokens": result.usage["total_tokens"],
                    "llm_wall_time_ms": result.wall_time_ms,
                    "retry_count": result.retry_count,
                    "prompt_chars": len(prompt),
                    "history_message_count": len(self._history),
                    "tool_context_chars": len(tool_context),
                    "ts": time.time(),
                }
            )
            return Response(
                chat_message=TextMessage(content=result.content, source=self.name)
            )

        async def on_reset(self, cancellation_token: CancellationToken) -> None:
            del cancellation_token
            self._history.clear()

    agents = [AcceptanceAgent(config) for config in agent_configs]
    finalizer_source = next(
        str(config["name"]) for config in agent_configs if config.get("finalizer")
    )
    termination = ReviewerFinalTextTermination(
        marker=DONE_TOKEN,
        source=finalizer_source,
        semantic_guard=True,
    )
    kwargs: dict[str, Any] = {"termination_condition": termination}
    if max_turns > 0:
        kwargs["max_turns"] = max_turns
    try:
        team = RoundRobinGroupChat(agents, **kwargs)
    except TypeError:
        kwargs.pop("max_turns", None)
        team = RoundRobinGroupChat(agents, **kwargs)

    started = time.perf_counter()
    task_rows: list[dict[str, Any]] = []
    blind_candidates: list[dict[str, str]] = []
    blind_mapping: list[dict[str, Any]] = []
    for index, task in enumerate(tasks, start=1):
        current_task_id = task.task_id
        usage_start = len(logger.rows)
        tool_start = len(tool_call_rows)
        task_started = time.perf_counter()
        result = await team.run(task=task.question)
        final_source, raw_final = _final_message(result)
        final_answer = _strip_done_token(raw_final)
        marker_present = _has_exact_done_token(raw_final)
        delivery_valid = bool(
            marker_present
            and final_source.casefold() == finalizer_source.casefold()
            and len(final_answer) >= 200
        )
        task_dir = output_dir / "tasks" / task.task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        final_path = task_dir / "final_answer.md"
        final_path.write_text(final_answer + "\n", encoding="utf-8")
        task_usage = logger.summary(logger.rows[usage_start:])
        task_payload = {
            "task_id": task.task_id,
            "task_index": index,
            "question": task.question,
            "wall_time_ms": int((time.perf_counter() - task_started) * 1000),
            "llm_usage": task_usage,
            "stop_reason": str(getattr(result, "stop_reason", "") or ""),
            "final_source": final_source,
            "final_marker_present": marker_present,
            "delivery_valid": delivery_valid,
            "delivery_status": "valid" if delivery_valid else "task_failed",
            "delivery_guard_reasons": [] if delivery_valid else ["invalid_final_delivery"],
            "missing_delivery_requirements": [],
            "semantic_retry_count": 0,
            "final_answer": final_answer,
            "final_answer_chars": len(final_answer),
            "final_answer_path": str(final_path),
            "tool_calls": tool_call_rows[tool_start:],
            "messages": _messages_to_dict(result),
        }
        (task_dir / "run_result.json").write_text(
            json.dumps(task_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        task_rows.append(task_payload)
        candidate_id = f"candidate_{uuid.uuid4().hex[:12]}"
        blind_candidates.append(
            {
                "candidate_id": candidate_id,
                "question": task.question,
                "answer": final_answer,
            }
        )
        blind_mapping.append(
            {
                "candidate_id": candidate_id,
                "scenario_id": scenario_id,
                "task_id": task.task_id,
                "experiment_mode": experiment_mode,
                "delivery_valid": delivery_valid,
            }
        )

    usage_path = logger.write_summary()
    tool_path = output_dir / "tool_calls.json"
    tool_path.write_text(
        json.dumps(tool_call_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "scenario_id": scenario_id,
        "sequence_run_id": uuid.uuid4().hex,
        "experiment_mode": experiment_mode,
        "same_team_instance": True,
        "task_count": len(task_rows),
        "valid_delivery_count": sum(bool(row["delivery_valid"]) for row in task_rows),
        "degraded_delivery_count": sum(not row["delivery_valid"] for row in task_rows),
        "semantic_retry_count": 0,
        "wall_time_ms": int((time.perf_counter() - started) * 1000),
        "llm_usage": logger.summary(),
        "llm_total_tokens": logger.summary()["llm_total_tokens"],
        "tool_call_count": len(tool_call_rows),
        "llm_usage_summary_path": str(usage_path),
        "experiment_binding": run_identity.binding(),
    }
    sequence_payload = {
        "summary": summary,
        "agent_configs": agent_configs,
        "tasks": task_rows,
    }
    (output_dir / "sequence_result.json").write_text(
        json.dumps(sequence_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "quality_blind_candidates.json").write_text(
        json.dumps(
            {"scenario_id": scenario_id, "candidates": blind_candidates},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "quality_blind_mapping.json").write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "sequence_run_id": summary["sequence_run_id"],
                "mapping": blind_mapping,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"summary": summary, "raw": sequence_payload}


def _team_prompt(
    *, agent_name: str, messages: Sequence[Any], tool_context: str
) -> str:
    sections = [
        f"当前 Agent：{agent_name}",
        "请基于当前用户任务、已有团队消息和有效共享记忆继续协作。用户当前要求优先。",
        "团队消息：",
    ]
    sections.extend(
        f"[{getattr(message, 'source', 'unknown')}] "
        f"{getattr(message, 'content', '')}"
        for message in messages
    )
    if tool_context:
        sections.extend(
            [
                "本轮本地工具结果（必须保留 evidence_id）：",
                tool_context,
            ]
        )
    sections.append("请输出本角色在当前轮次应该提供的内容。")
    return "\n\n".join(str(item) for item in sections)


def _latest_user_or_message(messages: Sequence[Any]) -> str:
    for message in reversed(messages):
        content = str(getattr(message, "content", "") or "").strip()
        if content and str(getattr(message, "source", "")).casefold() == "user":
            return content
    return str(getattr(messages[-1], "content", "") or "") if messages else ""


def _messages_to_dict(result: Any) -> list[dict[str, Any]]:
    rows = []
    for message in list(getattr(result, "messages", []) or []):
        rows.append(
            {
                "source": str(getattr(message, "source", "") or ""),
                "type": type(message).__name__,
                "content": str(getattr(message, "content", "") or ""),
            }
        )
    return rows


def _final_message(result: Any) -> tuple[str, str]:
    messages = list(getattr(result, "messages", []) or [])
    if not messages:
        return "", ""
    message = messages[-1]
    return (
        str(getattr(message, "source", "") or ""),
        str(getattr(message, "content", "") or ""),
    )


def _has_exact_done_token(text: str) -> bool:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return bool(lines and lines[-1] == DONE_TOKEN)


def _strip_done_token(text: str) -> str:
    lines = str(text or "").splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == DONE_TOKEN:
        lines.pop()
    return "\n".join(lines).strip()


def _normalize_usage(value: Any) -> dict[str, int]:
    usage = value if isinstance(value, dict) else {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    total = int(usage.get("total_tokens") or prompt + completion)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _load_tasks(path: Path) -> tuple[str, list[TaskSpec]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    tasks = [
        TaskSpec(task_id=str(item["task_id"]), question=str(item["question"]))
        for item in value.get("tasks", [])
    ]
    if len(tasks) < 2:
        raise ValueError("Acceptance sequence requires at least two tasks")
    return str(value.get("scenario_id") or "dynamic-capability"), tasks


def _load_agents(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError("Agent config must contain at least two agents")
    names = [str(item.get("name") or "") for item in value]
    if len(names) != len(set(names)) or any(not name for name in names):
        raise ValueError("Agent names must be non-empty and unique")
    if sum(bool(item.get("finalizer")) for item in value) != 1:
        raise ValueError("Agent config must declare exactly one finalizer")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
