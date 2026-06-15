from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_runtime.core.models import Mode, RuntimeMessage
from agent_runtime.eval.token_counter import TokenCounter


@dataclass(slots=True)
class TaskMetricRow:
    task_id: str
    round_id: int
    mode: str
    message_count: int = 0
    agent_call_count: int = 0
    direct_text_chars: int = 0
    direct_text_tokens: int = 0
    prompt_chars: int = 0
    prompt_tokens: int = 0
    prompt_view_tokens: int = 0
    state_refs_count: int = 0
    state_payload_bytes: int = 0
    embedding_state_count: int = 0
    retrieval_state_count: int = 0
    artifact_state_count: int = 0
    memory_refs_count: int = 0
    memory_query_count: int = 0
    memory_query_hit_count: int = 0
    memory_hit_count: int = 0
    memory_hit_rate: float = 0.0
    useful_memory_hit_count: int = 0
    useful_memory_hit_rate: float = 0.0
    wrong_memory_hit_count: int = 0
    memory_supported_output_count: int = 0
    retrieved_memory_tokens: int = 0
    control_llm_tokens: int = 0
    retry_tokens: int = 0
    end_to_end_collaboration_tokens: int = 0
    latency_ms: float = 0.0
    success: bool = False
    token_count_method: str = ""
    tokenizer_name: str = ""
    tokenizer_version: str = ""


class MetricsCollector:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, int, str], TaskMetricRow] = {}
        self._tokenizer_meta: dict[str, str] = {}

    def record_message(
        self, message: RuntimeMessage, token_counter: TokenCounter
    ) -> None:
        row = self._row(message.task_id, message.round_id, message.mode)
        token_count = token_counter.count(message.content)
        row.message_count += 1
        row.agent_call_count += 1
        row.direct_text_chars += len(message.content)
        row.direct_text_tokens += token_count.token_count
        row.state_refs_count += len(message.state_refs)
        row.memory_refs_count += len(message.memory_refs)
        self._apply_token_meta(row, token_count)

    def record_state_write(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        state_type: str,
        payload_bytes: int,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.state_payload_bytes += payload_bytes
        if state_type == "embedding_state":
            row.embedding_state_count += 1
        elif state_type == "retrieval_state":
            row.retrieval_state_count += 1
        elif state_type == "artifact_state":
            row.artifact_state_count += 1

    def record_memory_retrieval(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        hit_count: int,
        useful_hit_count: int,
        wrong_hit_count: int,
        prompt_view: str,
        token_counter: TokenCounter,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        token_count = token_counter.count(prompt_view) if prompt_view else None
        row.memory_query_count += 1
        if hit_count:
            row.memory_query_hit_count += 1
        row.memory_hit_count += hit_count
        row.useful_memory_hit_count += useful_hit_count
        row.wrong_memory_hit_count += wrong_hit_count
        if token_count is not None:
            row.retrieved_memory_tokens += token_count.token_count
            self._apply_token_meta(row, token_count)

    def record_memory_supported_output(
        self, *, task_id: str, round_id: int, mode: Mode, count: int
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.memory_supported_output_count += count

    def record_prompt(
        self,
        task_id: str,
        round_id: int,
        mode: Mode,
        agent_id: str,
        prompt: str,
        token_counter: TokenCounter,
    ) -> None:
        del agent_id
        row = self._row(task_id, round_id, mode)
        token_count = token_counter.count(prompt)
        row.prompt_chars += len(prompt)
        row.prompt_tokens += token_count.token_count
        row.prompt_view_tokens += token_count.token_count
        self._apply_token_meta(row, token_count)

    def finish_task(
        self,
        task_id: str,
        round_id: int,
        mode: Mode,
        latency_ms: float,
        success: bool,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.latency_ms = latency_ms
        row.success = success
        if row.memory_query_count:
            row.memory_hit_rate = row.memory_query_hit_count / row.memory_query_count
        if row.memory_hit_count:
            row.useful_memory_hit_rate = row.useful_memory_hit_count / row.memory_hit_count
        row.end_to_end_collaboration_tokens = (
            row.direct_text_tokens
            + row.prompt_view_tokens
            + row.retrieved_memory_tokens
            + row.control_llm_tokens
            + row.retry_tokens
        )

    def rows(self) -> list[TaskMetricRow]:
        return list(self._rows.values())

    def summary(self) -> dict[str, Any]:
        by_mode: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "task_runs": 0,
                "message_count": 0,
                "agent_call_count": 0,
                "direct_text_chars": 0,
                "direct_text_tokens": 0,
                "prompt_chars": 0,
                "prompt_tokens": 0,
                "prompt_view_tokens": 0,
                "latency_ms": 0.0,
                "success_count": 0,
                "retrieved_memory_tokens": 0,
                "control_llm_tokens": 0,
                "retry_tokens": 0,
                "end_to_end_collaboration_tokens": 0,
                "memory_hit_count": 0,
                "memory_query_count": 0,
                "memory_query_hit_count": 0,
                "useful_memory_hit_count": 0,
                "wrong_memory_hit_count": 0,
                "memory_supported_output_count": 0,
            }
        )
        for row in self.rows():
            bucket = by_mode[row.mode]
            bucket["task_runs"] += 1
            bucket["message_count"] += row.message_count
            bucket["agent_call_count"] += row.agent_call_count
            bucket["direct_text_chars"] += row.direct_text_chars
            bucket["direct_text_tokens"] += row.direct_text_tokens
            bucket["prompt_chars"] += row.prompt_chars
            bucket["prompt_tokens"] += row.prompt_tokens
            bucket["prompt_view_tokens"] += row.prompt_view_tokens
            bucket["latency_ms"] += row.latency_ms
            bucket["success_count"] += int(row.success)
            bucket["retrieved_memory_tokens"] += row.retrieved_memory_tokens
            bucket["control_llm_tokens"] += row.control_llm_tokens
            bucket["retry_tokens"] += row.retry_tokens
            bucket["end_to_end_collaboration_tokens"] += (
                row.end_to_end_collaboration_tokens
            )
            bucket["memory_hit_count"] += row.memory_hit_count
            bucket["memory_query_count"] += row.memory_query_count
            bucket["memory_query_hit_count"] += row.memory_query_hit_count
            bucket["useful_memory_hit_count"] += row.useful_memory_hit_count
            bucket["wrong_memory_hit_count"] += row.wrong_memory_hit_count
            bucket["memory_supported_output_count"] += row.memory_supported_output_count

        for bucket in by_mode.values():
            task_runs = max(1, bucket["task_runs"])
            bucket["avg_latency_ms"] = bucket["latency_ms"] / task_runs
            bucket["success_rate"] = bucket["success_count"] / task_runs
            memory_hits = max(1, bucket["memory_hit_count"])
            bucket["memory_hit_rate"] = (
                bucket["memory_query_hit_count"] / bucket["memory_query_count"]
                if bucket["memory_query_count"]
                else 0.0
            )
            bucket["useful_memory_hit_rate"] = (
                bucket["useful_memory_hit_count"] / memory_hits
                if bucket["memory_hit_count"]
                else 0.0
            )

        return {"by_mode": dict(by_mode), "tokenizer": self._tokenizer_meta}

    def export(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        rows = [asdict(row) for row in self.rows()]
        fieldnames = list(TaskMetricRow.__dataclass_fields__.keys())

        with (output_dir / "metrics.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        payload = {"rows": rows, "summary": self.summary()}
        with (output_dir / "metrics.json").open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def _row(self, task_id: str, round_id: int, mode: Mode) -> TaskMetricRow:
        key = (task_id, round_id, mode)
        if key not in self._rows:
            self._rows[key] = TaskMetricRow(task_id=task_id, round_id=round_id, mode=mode)
        return self._rows[key]

    def _apply_token_meta(self, row: TaskMetricRow, token_count: Any) -> None:
        row.token_count_method = token_count.token_count_method
        row.tokenizer_name = token_count.tokenizer_name
        row.tokenizer_version = token_count.tokenizer_version
        self._tokenizer_meta = {
            "token_count_method": token_count.token_count_method,
            "tokenizer_name": token_count.tokenizer_name,
            "tokenizer_version": token_count.tokenizer_version,
        }
