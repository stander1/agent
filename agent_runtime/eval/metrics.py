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
    hot_state_count: int = 0
    warm_state_count: int = 0
    cold_state_count: int = 0
    hot_state_bytes: int = 0
    warm_state_bytes: int = 0
    cold_state_bytes: int = 0
    memory_refs_count: int = 0
    memory_query_count: int = 0
    memory_query_hit_count: int = 0
    memory_hit_count: int = 0
    memory_hit_rate: float = 0.0
    useful_memory_hit_count: int = 0
    useful_memory_hit_rate: float = 0.0
    wrong_memory_hit_count: int = 0
    memory_supported_output_count: int = 0
    memory_write_count: int = 0
    memory_candidate_count: int = 0
    claim_candidate_count: int = 0
    memory_admitted_count: int = 0
    memory_rejected_count: int = 0
    memory_pending_count: int = 0
    memory_audit_only_count: int = 0
    admission_unresolved_slot_count: int = 0
    claim_to_memoryview_count: int = 0
    memory_admission_rate: float = 0.0
    claim_card_count: int = 0
    memory_view_count: int = 0
    promotion_view_count: int = 0
    audit_view_expansion_count: int = 0
    context_pruned_retry_count: int = 0
    format_retry_success_count: int = 0
    unresolved_slot_count: int = 0
    alias_mapping_hit_count: int = 0
    vector_retrieval_count: int = 0
    retrieval_backend: str = ""
    deliverable_schema_required_count: int = 0
    deliverable_schema_hit_count: int = 0
    deliverable_schema_complete: bool = False
    final_quality_retry_count: int = 0
    provider_response_repair_count: int = 0
    provider_response_retry_count: int = 0
    provider_response_degraded_count: int = 0
    malformed_provider_response_count: int = 0
    contract_guard_checked_count: int = 0
    contract_schema_valid_count: int = 0
    contract_repair_success_count: int = 0
    contract_retry_count: int = 0
    contract_violation_count: int = 0
    fallback_count: int = 0
    retrieved_memory_tokens: int = 0
    control_llm_tokens: int = 0
    retry_tokens: int = 0
    llm_call_count: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_total_tokens: int = 0
    llm_latency_ms: float = 0.0
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
        tier: str = "",
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.state_payload_bytes += payload_bytes
        if state_type == "embedding_state":
            row.embedding_state_count += 1
        elif state_type == "retrieval_state":
            row.retrieval_state_count += 1
        elif state_type == "artifact_state":
            row.artifact_state_count += 1
        if tier == "hot":
            row.hot_state_count += 1
            row.hot_state_bytes += payload_bytes
        elif tier == "warm":
            row.warm_state_count += 1
            row.warm_state_bytes += payload_bytes
        elif tier == "cold":
            row.cold_state_count += 1
            row.cold_state_bytes += payload_bytes

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

    def record_memory_write(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        memory_write_count: int = 0,
        claim_card_count: int = 0,
        memory_view_count: int = 0,
        promotion_view_count: int = 0,
        alias_mapping_hit_count: int = 0,
        unresolved_slot_count: int = 0,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.memory_write_count += memory_write_count
        row.claim_card_count += claim_card_count
        row.memory_view_count += memory_view_count
        row.promotion_view_count += promotion_view_count
        row.alias_mapping_hit_count += alias_mapping_hit_count
        row.unresolved_slot_count += unresolved_slot_count

    def record_memory_admission(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        memory_candidate_count: int = 0,
        claim_candidate_count: int = 0,
        memory_admitted_count: int = 0,
        memory_rejected_count: int = 0,
        memory_pending_count: int = 0,
        memory_audit_only_count: int = 0,
        admission_unresolved_slot_count: int = 0,
        claim_to_memoryview_count: int = 0,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.memory_candidate_count += memory_candidate_count
        row.claim_candidate_count += claim_candidate_count
        row.memory_admitted_count += memory_admitted_count
        row.memory_rejected_count += memory_rejected_count
        row.memory_pending_count += memory_pending_count
        row.memory_audit_only_count += memory_audit_only_count
        row.admission_unresolved_slot_count += admission_unresolved_slot_count
        row.claim_to_memoryview_count += claim_to_memoryview_count

    def record_memory_search_backend(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        retrieval_backend: str,
        vector_retrieval_count: int = 0,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.retrieval_backend = retrieval_backend
        row.vector_retrieval_count += vector_retrieval_count

    def record_audit_view_expansion(
        self, *, task_id: str, round_id: int, mode: Mode, count: int = 1
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.audit_view_expansion_count += count

    def record_deliverable_schema(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        hit_count: int,
        required_count: int,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.deliverable_schema_hit_count = max(
            row.deliverable_schema_hit_count, hit_count
        )
        row.deliverable_schema_required_count = max(
            row.deliverable_schema_required_count, required_count
        )
        row.deliverable_schema_complete = row.deliverable_schema_complete or (
            required_count > 0 and hit_count >= required_count
        )

    def record_final_quality_retry(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        retry_prompt: str,
        retry_output: str,
        token_counter: TokenCounter,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        prompt_count = token_counter.count(retry_prompt)
        output_count = token_counter.count(retry_output)
        row.final_quality_retry_count += 1
        row.retry_tokens += prompt_count.token_count + output_count.token_count
        self._apply_token_meta(row, prompt_count)

    def record_retry_tokens(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        retry_prompt: str,
        retry_output: str,
        token_counter: TokenCounter,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        prompt_count = token_counter.count(retry_prompt)
        output_count = token_counter.count(retry_output)
        row.retry_tokens += prompt_count.token_count + output_count.token_count
        self._apply_token_meta(row, prompt_count)

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

    def record_llm_call(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        usage: dict[str, int],
        latency_ms: float,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.llm_call_count += 1
        row.llm_prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        row.llm_completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        row.llm_total_tokens += int(usage.get("total_tokens", 0) or 0)
        row.llm_latency_ms += latency_ms

    def record_provider_guard(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        provider_guard: dict[str, Any] | None,
    ) -> None:
        if not provider_guard:
            return
        row = self._row(task_id, round_id, mode)
        status = provider_guard.get("status", "")
        if status == "repaired":
            row.provider_response_repair_count += 1
        elif status == "degraded_fallback":
            row.provider_response_degraded_count += 1
        row.provider_response_retry_count += int(
            provider_guard.get("retry_attempts", 0) or 0
        )
        if provider_guard.get("schema_errors"):
            row.malformed_provider_response_count += 1

    def record_contract_guard(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        contract_report: dict[str, Any],
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.contract_guard_checked_count += 1
        if contract_report.get("schema_valid"):
            row.contract_schema_valid_count += 1
        status = contract_report.get("contract_status", "")
        if status == "repaired":
            row.contract_repair_success_count += 1
        if contract_report.get("retry_required") or contract_report.get(
            "retry_attempted"
        ):
            row.contract_retry_count += 1
        if status == "degraded_fallback":
            row.contract_violation_count += 1
            row.fallback_count += 1

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
        if row.memory_candidate_count:
            row.memory_admission_rate = (
                row.memory_admitted_count / row.memory_candidate_count
            )
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
                "llm_call_count": 0,
                "llm_prompt_tokens": 0,
                "llm_completion_tokens": 0,
                "llm_total_tokens": 0,
                "llm_latency_ms": 0.0,
                "end_to_end_collaboration_tokens": 0,
                "memory_hit_count": 0,
                "memory_query_count": 0,
                "memory_query_hit_count": 0,
                "useful_memory_hit_count": 0,
                "wrong_memory_hit_count": 0,
                "memory_supported_output_count": 0,
                "memory_write_count": 0,
                "memory_candidate_count": 0,
                "claim_candidate_count": 0,
                "memory_admitted_count": 0,
                "memory_rejected_count": 0,
                "memory_pending_count": 0,
                "memory_audit_only_count": 0,
                "admission_unresolved_slot_count": 0,
                "claim_to_memoryview_count": 0,
                "claim_card_count": 0,
                "memory_view_count": 0,
                "promotion_view_count": 0,
                "audit_view_expansion_count": 0,
                "context_pruned_retry_count": 0,
                "format_retry_success_count": 0,
                "unresolved_slot_count": 0,
                "alias_mapping_hit_count": 0,
                "vector_retrieval_count": 0,
                "retrieval_backend": "",
                "deliverable_schema_required_count": 0,
                "deliverable_schema_hit_count": 0,
                "deliverable_schema_complete_count": 0,
                "final_quality_retry_count": 0,
                "provider_response_repair_count": 0,
                "provider_response_retry_count": 0,
                "provider_response_degraded_count": 0,
                "malformed_provider_response_count": 0,
                "contract_guard_checked_count": 0,
                "contract_schema_valid_count": 0,
                "contract_repair_success_count": 0,
                "contract_retry_count": 0,
                "contract_violation_count": 0,
                "fallback_count": 0,
                "hot_state_count": 0,
                "warm_state_count": 0,
                "cold_state_count": 0,
                "hot_state_bytes": 0,
                "warm_state_bytes": 0,
                "cold_state_bytes": 0,
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
            bucket["llm_call_count"] += row.llm_call_count
            bucket["llm_prompt_tokens"] += row.llm_prompt_tokens
            bucket["llm_completion_tokens"] += row.llm_completion_tokens
            bucket["llm_total_tokens"] += row.llm_total_tokens
            bucket["llm_latency_ms"] += row.llm_latency_ms
            bucket["end_to_end_collaboration_tokens"] += (
                row.end_to_end_collaboration_tokens
            )
            bucket["memory_hit_count"] += row.memory_hit_count
            bucket["memory_query_count"] += row.memory_query_count
            bucket["memory_query_hit_count"] += row.memory_query_hit_count
            bucket["useful_memory_hit_count"] += row.useful_memory_hit_count
            bucket["wrong_memory_hit_count"] += row.wrong_memory_hit_count
            bucket["memory_supported_output_count"] += row.memory_supported_output_count
            bucket["memory_write_count"] += row.memory_write_count
            bucket["memory_candidate_count"] += row.memory_candidate_count
            bucket["claim_candidate_count"] += row.claim_candidate_count
            bucket["memory_admitted_count"] += row.memory_admitted_count
            bucket["memory_rejected_count"] += row.memory_rejected_count
            bucket["memory_pending_count"] += row.memory_pending_count
            bucket["memory_audit_only_count"] += row.memory_audit_only_count
            bucket["admission_unresolved_slot_count"] += (
                row.admission_unresolved_slot_count
            )
            bucket["claim_to_memoryview_count"] += row.claim_to_memoryview_count
            bucket["claim_card_count"] += row.claim_card_count
            bucket["memory_view_count"] += row.memory_view_count
            bucket["promotion_view_count"] += row.promotion_view_count
            bucket["audit_view_expansion_count"] += row.audit_view_expansion_count
            bucket["context_pruned_retry_count"] += row.context_pruned_retry_count
            bucket["format_retry_success_count"] += row.format_retry_success_count
            bucket["unresolved_slot_count"] += row.unresolved_slot_count
            bucket["alias_mapping_hit_count"] += row.alias_mapping_hit_count
            bucket["vector_retrieval_count"] += row.vector_retrieval_count
            if row.retrieval_backend:
                bucket["retrieval_backend"] = row.retrieval_backend
            bucket["deliverable_schema_required_count"] += (
                row.deliverable_schema_required_count
            )
            bucket["deliverable_schema_hit_count"] += row.deliverable_schema_hit_count
            bucket["deliverable_schema_complete_count"] += int(
                row.deliverable_schema_complete
            )
            bucket["final_quality_retry_count"] += row.final_quality_retry_count
            bucket["provider_response_repair_count"] += (
                row.provider_response_repair_count
            )
            bucket["provider_response_retry_count"] += row.provider_response_retry_count
            bucket["provider_response_degraded_count"] += (
                row.provider_response_degraded_count
            )
            bucket["malformed_provider_response_count"] += (
                row.malformed_provider_response_count
            )
            bucket["contract_guard_checked_count"] += row.contract_guard_checked_count
            bucket["contract_schema_valid_count"] += row.contract_schema_valid_count
            bucket["contract_repair_success_count"] += (
                row.contract_repair_success_count
            )
            bucket["contract_retry_count"] += row.contract_retry_count
            bucket["contract_violation_count"] += row.contract_violation_count
            bucket["fallback_count"] += row.fallback_count
            bucket["hot_state_count"] += row.hot_state_count
            bucket["warm_state_count"] += row.warm_state_count
            bucket["cold_state_count"] += row.cold_state_count
            bucket["hot_state_bytes"] += row.hot_state_bytes
            bucket["warm_state_bytes"] += row.warm_state_bytes
            bucket["cold_state_bytes"] += row.cold_state_bytes

        for bucket in by_mode.values():
            task_runs = max(1, bucket["task_runs"])
            bucket["avg_latency_ms"] = bucket["latency_ms"] / task_runs
            bucket["avg_llm_latency_ms"] = (
                bucket["llm_latency_ms"] / max(1, bucket["llm_call_count"])
            )
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
            bucket["memory_admission_rate"] = (
                bucket["memory_admitted_count"] / bucket["memory_candidate_count"]
                if bucket["memory_candidate_count"]
                else 0.0
            )
            bucket["schema_valid_rate"] = (
                bucket["contract_schema_valid_count"]
                / bucket["contract_guard_checked_count"]
                if bucket["contract_guard_checked_count"]
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
