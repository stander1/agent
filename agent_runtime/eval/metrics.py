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
    mixed_memory_hit_count: int = 0
    memory_supported_output_count: int = 0
    memory_write_count: int = 0
    memory_candidate_count: int = 0
    claim_candidate_count: int = 0
    raw_claim_count: int = 0
    provisional_claim_count: int = 0
    slot_mapping_success_count: int = 0
    slot_mapping_success_rate: float = 0.0
    memory_admitted_count: int = 0
    memory_rejected_count: int = 0
    memory_pending_count: int = 0
    memory_audit_only_count: int = 0
    memory_deduplicated_claim_count: int = 0
    memory_deduplicated_memory_count: int = 0
    memory_evidence_reference_merge_count: int = 0
    admission_unresolved_slot_count: int = 0
    claim_to_memoryview_count: int = 0
    memory_conflict_detected_count: int = 0
    memory_conflict_resolved_count: int = 0
    memory_unresolved_conflict_count: int = 0
    active_memory_value_selection_count: int = 0
    memory_admission_rate: float = 0.0
    claim_card_count: int = 0
    memory_view_count: int = 0
    promotion_view_count: int = 0
    audit_view_expansion_count: int = 0
    context_pruned_retry_count: int = 0
    format_retry_success_count: int = 0
    unresolved_slot_count: int = 0
    unresolved_scope_count: int = 0
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
    raw_access_count: int = 0
    summary_access_count: int = 0
    evidence_snippet_access_count: int = 0
    access_escalation_count: int = 0
    state_gc_count: int = 0
    read_lease_acquire_count: int = 0
    read_lease_blocked_gc_count: int = 0
    state_tombstone_count: int = 0
    memory_status_transition_count: int = 0
    background_memory_job_count: int = 0
    background_memory_completed_count: int = 0
    background_memory_error_count: int = 0
    background_memory_wait_ms: float = 0.0
    summary_update_count: int = 0
    summary_update_prompt_tokens: int = 0
    summary_update_completion_tokens: int = 0
    summary_update_total_tokens: int = 0
    summary_update_estimated_tokens: int = 0
    summary_update_method: str = ""
    stale_read_detected_count: int = 0
    preflight_validation_count: int = 0
    preflight_block_count: int = 0
    control_decision_count: int = 0
    route_override_count: int = 0
    communication_gate_block_count: int = 0
    communication_gate_degraded_count: int = 0
    control_budget_exhausted_count: int = 0
    retry_input_tokens: int = 0
    retry_budget_exhausted_count: int = 0
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


@dataclass(slots=True)
class AgentMetricRow:
    task_id: str
    round_id: int
    mode: str
    agent: str
    role: str = ""
    prompt_chars: int = 0
    prompt_tokens: int = 0
    output_chars: int = 0
    llm_call_count: int = 0
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_total_tokens: int = 0
    llm_wall_time_ms: float = 0.0
    ttft_ms: float = 0.0
    tokens_per_second: float = 0.0
    local_state_read_ms: float = 0.0
    local_memory_search_ms: float = 0.0
    artifact_digest_ms: float = 0.0
    schema_check_ms: float = 0.0
    retry_count: int = 0
    raw_access_count: int = 0


class MetricsCollector:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, int, str], TaskMetricRow] = {}
        self._agent_rows: dict[tuple[str, int, str, str], AgentMetricRow] = {}
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

    def record_state_access(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        raw_access_count: int = 0,
        summary_access_count: int = 0,
        evidence_snippet_access_count: int = 0,
        access_escalation_count: int = 0,
        read_lease_acquire_count: int = 0,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.raw_access_count += raw_access_count
        row.summary_access_count += summary_access_count
        row.evidence_snippet_access_count += evidence_snippet_access_count
        row.access_escalation_count += access_escalation_count
        row.read_lease_acquire_count += read_lease_acquire_count

    def record_state_gc(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        state_gc_count: int = 0,
        read_lease_blocked_gc_count: int = 0,
        state_tombstone_count: int = 0,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.state_gc_count += state_gc_count
        row.read_lease_blocked_gc_count += read_lease_blocked_gc_count
        row.state_tombstone_count += state_tombstone_count

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

    def record_memory_use_feedback(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        useful_hit_count: int = 0,
        wrong_hit_count: int = 0,
        mixed_hit_count: int = 0,
    ) -> None:
        """Record post-output memory evidence without counting another retrieval."""
        row = self._row(task_id, round_id, mode)
        row.useful_memory_hit_count += useful_hit_count
        row.wrong_memory_hit_count += wrong_hit_count
        row.mixed_memory_hit_count += mixed_hit_count

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
        raw_claim_count: int = 0,
        provisional_claim_count: int = 0,
        slot_mapping_success_count: int = 0,
        memory_admitted_count: int = 0,
        memory_rejected_count: int = 0,
        memory_pending_count: int = 0,
        memory_audit_only_count: int = 0,
        admission_unresolved_slot_count: int = 0,
        claim_to_memoryview_count: int = 0,
        memory_conflict_detected_count: int = 0,
        memory_conflict_resolved_count: int = 0,
        memory_unresolved_conflict_count: int = 0,
        active_memory_value_selection_count: int = 0,
        unresolved_scope_count: int = 0,
        memory_deduplicated_claim_count: int = 0,
        memory_deduplicated_memory_count: int = 0,
        memory_evidence_reference_merge_count: int = 0,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.memory_candidate_count += memory_candidate_count
        row.claim_candidate_count += claim_candidate_count
        row.raw_claim_count += raw_claim_count
        row.provisional_claim_count += provisional_claim_count
        row.slot_mapping_success_count += slot_mapping_success_count
        row.memory_admitted_count += memory_admitted_count
        row.memory_rejected_count += memory_rejected_count
        row.memory_pending_count += memory_pending_count
        row.memory_audit_only_count += memory_audit_only_count
        row.admission_unresolved_slot_count += admission_unresolved_slot_count
        row.claim_to_memoryview_count += claim_to_memoryview_count
        row.memory_conflict_detected_count += memory_conflict_detected_count
        row.memory_conflict_resolved_count += memory_conflict_resolved_count
        row.memory_unresolved_conflict_count += (
            memory_unresolved_conflict_count
        )
        row.active_memory_value_selection_count += (
            active_memory_value_selection_count
        )
        row.unresolved_scope_count += unresolved_scope_count
        row.memory_deduplicated_claim_count += memory_deduplicated_claim_count
        row.memory_deduplicated_memory_count += memory_deduplicated_memory_count
        row.memory_evidence_reference_merge_count += (
            memory_evidence_reference_merge_count
        )

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
        row.retry_input_tokens += prompt_count.token_count
        self._apply_token_meta(row, prompt_count)

    def record_retry_budget(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        retry_input_tokens: int,
        budget_exhausted: bool,
    ) -> None:
        del retry_input_tokens
        row = self._row(task_id, round_id, mode)
        row.retry_budget_exhausted_count += int(budget_exhausted)

    def record_prompt(
        self,
        task_id: str,
        round_id: int,
        mode: Mode,
        agent_id: str,
        prompt: str,
        token_counter: TokenCounter,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        token_count = token_counter.count(prompt)
        row.prompt_chars += len(prompt)
        row.prompt_tokens += token_count.token_count
        row.prompt_view_tokens += token_count.token_count
        self._apply_token_meta(row, token_count)
        agent_row = self._agent_row(task_id, round_id, mode, agent_id)
        agent_row.prompt_chars += len(prompt)
        agent_row.prompt_tokens += token_count.token_count

    def record_llm_call(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        usage: dict[str, int],
        latency_ms: float,
        agent_id: str = "",
        output_chars: int = 0,
        ttft_ms: float = 0.0,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.llm_call_count += 1
        row.llm_prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        row.llm_completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        row.llm_total_tokens += int(usage.get("total_tokens", 0) or 0)
        row.llm_latency_ms += latency_ms
        if agent_id:
            agent_row = self._agent_row(task_id, round_id, mode, agent_id)
            agent_row.llm_call_count += 1
            agent_row.llm_prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            agent_row.llm_completion_tokens += int(
                usage.get("completion_tokens", 0) or 0
            )
            agent_row.llm_total_tokens += int(usage.get("total_tokens", 0) or 0)
            agent_row.llm_wall_time_ms += latency_ms
            agent_row.ttft_ms += ttft_ms
            agent_row.output_chars += output_chars
            self._refresh_agent_tokens_per_second(agent_row)

    def record_provider_guard(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        provider_guard: dict[str, Any] | None,
        agent_id: str = "",
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
        if agent_id:
            self.record_agent_retry(
                task_id=task_id,
                round_id=round_id,
                mode=mode,
                agent_id=agent_id,
                retry_count=int(provider_guard.get("retry_attempts", 0) or 0),
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
        agent_id: str = "",
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
            row.context_pruned_retry_count += 1
            if agent_id:
                self.record_agent_retry(
                    task_id=task_id,
                    round_id=round_id,
                    mode=mode,
                    agent_id=agent_id,
                )
            if status == "repaired":
                row.format_retry_success_count += 1
        if status == "degraded_fallback":
            row.contract_violation_count += 1
            row.fallback_count += 1

    def record_agent_role(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        agent_id: str,
        role: str = "",
    ) -> None:
        row = self._agent_row(task_id, round_id, mode, agent_id)
        if role and not row.role:
            row.role = role

    def record_agent_local_timing(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        agent_id: str,
        local_state_read_ms: float = 0.0,
        local_memory_search_ms: float = 0.0,
        artifact_digest_ms: float = 0.0,
        schema_check_ms: float = 0.0,
        raw_access_count: int = 0,
    ) -> None:
        row = self._agent_row(task_id, round_id, mode, agent_id)
        row.local_state_read_ms += local_state_read_ms
        row.local_memory_search_ms += local_memory_search_ms
        row.artifact_digest_ms += artifact_digest_ms
        row.schema_check_ms += schema_check_ms
        row.raw_access_count += raw_access_count

    def record_agent_retry(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        agent_id: str,
        retry_count: int = 1,
    ) -> None:
        if retry_count <= 0:
            return
        row = self._agent_row(task_id, round_id, mode, agent_id)
        row.retry_count += retry_count

    def record_preflight_validation(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        validation_count: int = 1,
        block_count: int = 0,
        stale_read_detected_count: int = 0,
        memory_status_transition_count: int = 0,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.preflight_validation_count += validation_count
        row.preflight_block_count += block_count
        row.stale_read_detected_count += stale_read_detected_count
        row.memory_status_transition_count += memory_status_transition_count

    def record_control_decision(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        route_changed: bool = False,
        gate_status: str = "ready",
        budget_allowed: bool = True,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.control_decision_count += 1
        row.route_override_count += int(route_changed)
        row.communication_gate_block_count += int(gate_status == "blocked")
        row.communication_gate_degraded_count += int(gate_status == "degraded")
        row.control_budget_exhausted_count += int(not budget_allowed)

    def record_background_memory_job(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        scheduled_count: int = 0,
        completed_count: int = 0,
        error_count: int = 0,
        wait_ms: float = 0.0,
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.background_memory_job_count += scheduled_count
        row.background_memory_completed_count += completed_count
        row.background_memory_error_count += error_count
        row.background_memory_wait_ms += wait_ms

    def record_summary_update(
        self,
        *,
        task_id: str,
        round_id: int,
        mode: Mode,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        estimated_tokens: int = 0,
        method: str = "",
    ) -> None:
        row = self._row(task_id, round_id, mode)
        row.summary_update_count += 1
        row.summary_update_prompt_tokens += prompt_tokens
        row.summary_update_completion_tokens += completion_tokens
        row.summary_update_total_tokens += total_tokens
        row.summary_update_estimated_tokens += estimated_tokens
        if method:
            row.summary_update_method = (
                method
                if not row.summary_update_method
                or row.summary_update_method == method
                else "mixed"
            )

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
        if row.raw_claim_count:
            row.slot_mapping_success_rate = (
                row.slot_mapping_success_count / row.raw_claim_count
            )
        row.end_to_end_collaboration_tokens = (
            row.direct_text_tokens
            + row.prompt_view_tokens
            + row.retrieved_memory_tokens
            + row.control_llm_tokens
            + row.retry_tokens
            + row.summary_update_total_tokens
            + row.summary_update_estimated_tokens
        )

    def rows(self) -> list[TaskMetricRow]:
        return list(self._rows.values())

    def agent_rows(self) -> list[AgentMetricRow]:
        return list(self._agent_rows.values())

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
                "mixed_memory_hit_count": 0,
                "memory_supported_output_count": 0,
                "memory_write_count": 0,
                "memory_candidate_count": 0,
                "claim_candidate_count": 0,
                "raw_claim_count": 0,
                "provisional_claim_count": 0,
                "slot_mapping_success_count": 0,
                "memory_admitted_count": 0,
                "memory_rejected_count": 0,
                "memory_pending_count": 0,
                "memory_audit_only_count": 0,
                "admission_unresolved_slot_count": 0,
                "claim_to_memoryview_count": 0,
                "memory_conflict_detected_count": 0,
                "memory_conflict_resolved_count": 0,
                "memory_unresolved_conflict_count": 0,
                "active_memory_value_selection_count": 0,
                "claim_card_count": 0,
                "memory_view_count": 0,
                "promotion_view_count": 0,
                "audit_view_expansion_count": 0,
                "context_pruned_retry_count": 0,
                "format_retry_success_count": 0,
                "unresolved_slot_count": 0,
                "unresolved_scope_count": 0,
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
                "raw_access_count": 0,
                "summary_access_count": 0,
                "evidence_snippet_access_count": 0,
                "access_escalation_count": 0,
                "state_gc_count": 0,
                "read_lease_acquire_count": 0,
                "read_lease_blocked_gc_count": 0,
                "state_tombstone_count": 0,
                "memory_status_transition_count": 0,
                "background_memory_job_count": 0,
                "background_memory_completed_count": 0,
                "background_memory_error_count": 0,
                "background_memory_wait_ms": 0.0,
                "summary_update_count": 0,
                "summary_update_prompt_tokens": 0,
                "summary_update_completion_tokens": 0,
                "summary_update_total_tokens": 0,
                "summary_update_estimated_tokens": 0,
                "summary_update_method": "",
                "stale_read_detected_count": 0,
                "preflight_validation_count": 0,
                "preflight_block_count": 0,
                "control_decision_count": 0,
                "route_override_count": 0,
                "communication_gate_block_count": 0,
                "communication_gate_degraded_count": 0,
                "control_budget_exhausted_count": 0,
                "retry_input_tokens": 0,
                "retry_budget_exhausted_count": 0,
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
            bucket["mixed_memory_hit_count"] += row.mixed_memory_hit_count
            bucket["memory_supported_output_count"] += row.memory_supported_output_count
            bucket["memory_write_count"] += row.memory_write_count
            bucket["memory_candidate_count"] += row.memory_candidate_count
            bucket["claim_candidate_count"] += row.claim_candidate_count
            bucket["raw_claim_count"] += row.raw_claim_count
            bucket["provisional_claim_count"] += row.provisional_claim_count
            bucket["slot_mapping_success_count"] += (
                row.slot_mapping_success_count
            )
            bucket["memory_admitted_count"] += row.memory_admitted_count
            bucket["memory_rejected_count"] += row.memory_rejected_count
            bucket["memory_pending_count"] += row.memory_pending_count
            bucket["memory_audit_only_count"] += row.memory_audit_only_count
            bucket["admission_unresolved_slot_count"] += (
                row.admission_unresolved_slot_count
            )
            bucket["claim_to_memoryview_count"] += row.claim_to_memoryview_count
            bucket["memory_conflict_detected_count"] += (
                row.memory_conflict_detected_count
            )
            bucket["memory_conflict_resolved_count"] += (
                row.memory_conflict_resolved_count
            )
            bucket["memory_unresolved_conflict_count"] += (
                row.memory_unresolved_conflict_count
            )
            bucket["active_memory_value_selection_count"] += (
                row.active_memory_value_selection_count
            )
            bucket["claim_card_count"] += row.claim_card_count
            bucket["memory_view_count"] += row.memory_view_count
            bucket["promotion_view_count"] += row.promotion_view_count
            bucket["audit_view_expansion_count"] += row.audit_view_expansion_count
            bucket["context_pruned_retry_count"] += row.context_pruned_retry_count
            bucket["format_retry_success_count"] += row.format_retry_success_count
            bucket["unresolved_slot_count"] += row.unresolved_slot_count
            bucket["unresolved_scope_count"] += row.unresolved_scope_count
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
            bucket["raw_access_count"] += row.raw_access_count
            bucket["summary_access_count"] += row.summary_access_count
            bucket["evidence_snippet_access_count"] += row.evidence_snippet_access_count
            bucket["access_escalation_count"] += row.access_escalation_count
            bucket["state_gc_count"] += row.state_gc_count
            bucket["read_lease_acquire_count"] += row.read_lease_acquire_count
            bucket["read_lease_blocked_gc_count"] += row.read_lease_blocked_gc_count
            bucket["state_tombstone_count"] += row.state_tombstone_count
            bucket["memory_status_transition_count"] += (
                row.memory_status_transition_count
            )
            bucket["background_memory_job_count"] += row.background_memory_job_count
            bucket["background_memory_completed_count"] += (
                row.background_memory_completed_count
            )
            bucket["background_memory_error_count"] += row.background_memory_error_count
            bucket["background_memory_wait_ms"] += row.background_memory_wait_ms
            bucket["summary_update_count"] += row.summary_update_count
            bucket["summary_update_prompt_tokens"] += row.summary_update_prompt_tokens
            bucket["summary_update_completion_tokens"] += (
                row.summary_update_completion_tokens
            )
            bucket["summary_update_total_tokens"] += row.summary_update_total_tokens
            bucket["summary_update_estimated_tokens"] += (
                row.summary_update_estimated_tokens
            )
            if row.summary_update_method:
                if not bucket["summary_update_method"]:
                    bucket["summary_update_method"] = row.summary_update_method
                elif bucket["summary_update_method"] != row.summary_update_method:
                    bucket["summary_update_method"] = "mixed"
            bucket["stale_read_detected_count"] += row.stale_read_detected_count
            bucket["preflight_validation_count"] += row.preflight_validation_count
            bucket["preflight_block_count"] += row.preflight_block_count
            bucket["control_decision_count"] += row.control_decision_count
            bucket["route_override_count"] += row.route_override_count
            bucket["communication_gate_block_count"] += row.communication_gate_block_count
            bucket["communication_gate_degraded_count"] += (
                row.communication_gate_degraded_count
            )
            bucket["control_budget_exhausted_count"] += (
                row.control_budget_exhausted_count
            )
            bucket["retry_input_tokens"] += row.retry_input_tokens
            bucket["retry_budget_exhausted_count"] += row.retry_budget_exhausted_count
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
            bucket["slot_mapping_success_rate"] = (
                bucket["slot_mapping_success_count"] / bucket["raw_claim_count"]
                if bucket["raw_claim_count"]
                else 0.0
            )
            bucket["schema_valid_rate"] = (
                bucket["contract_schema_valid_count"]
                / bucket["contract_guard_checked_count"]
                if bucket["contract_guard_checked_count"]
                else 0.0
            )

        return {
            "by_mode": dict(by_mode),
            "by_agent": self.agent_summary(),
            "tokenizer": self._tokenizer_meta,
        }

    def agent_summary(self) -> dict[str, Any]:
        by_mode_agent: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        for row in self.agent_rows():
            agent_bucket = by_mode_agent[row.mode].setdefault(
                row.agent,
                {
                    "agent": row.agent,
                    "role": row.role,
                    "rows": 0,
                    "prompt_chars": 0,
                    "prompt_tokens": 0,
                    "output_chars": 0,
                    "llm_call_count": 0,
                    "llm_prompt_tokens": 0,
                    "llm_completion_tokens": 0,
                    "llm_total_tokens": 0,
                    "llm_wall_time_ms": 0.0,
                    "ttft_ms": 0.0,
                    "tokens_per_second": 0.0,
                    "local_state_read_ms": 0.0,
                    "local_memory_search_ms": 0.0,
                    "artifact_digest_ms": 0.0,
                    "schema_check_ms": 0.0,
                    "retry_count": 0,
                    "raw_access_count": 0,
                    "total_wall_time_ms": 0.0,
                },
            )
            agent_bucket["rows"] += 1
            if row.role and not agent_bucket["role"]:
                agent_bucket["role"] = row.role
            agent_bucket["prompt_chars"] += row.prompt_chars
            agent_bucket["prompt_tokens"] += row.prompt_tokens
            agent_bucket["output_chars"] += row.output_chars
            agent_bucket["llm_call_count"] += row.llm_call_count
            agent_bucket["llm_prompt_tokens"] += row.llm_prompt_tokens
            agent_bucket["llm_completion_tokens"] += row.llm_completion_tokens
            agent_bucket["llm_total_tokens"] += row.llm_total_tokens
            agent_bucket["llm_wall_time_ms"] += row.llm_wall_time_ms
            agent_bucket["ttft_ms"] += row.ttft_ms
            agent_bucket["local_state_read_ms"] += row.local_state_read_ms
            agent_bucket["local_memory_search_ms"] += row.local_memory_search_ms
            agent_bucket["artifact_digest_ms"] += row.artifact_digest_ms
            agent_bucket["schema_check_ms"] += row.schema_check_ms
            agent_bucket["retry_count"] += row.retry_count
            agent_bucket["raw_access_count"] += row.raw_access_count

        for agent_buckets in by_mode_agent.values():
            for bucket in agent_buckets.values():
                llm_seconds = bucket["llm_wall_time_ms"] / 1000.0
                bucket["tokens_per_second"] = (
                    bucket["llm_completion_tokens"] / llm_seconds
                    if llm_seconds > 0
                    else 0.0
                )
                bucket["total_wall_time_ms"] = (
                    bucket["llm_wall_time_ms"]
                    + bucket["local_state_read_ms"]
                    + bucket["local_memory_search_ms"]
                    + bucket["artifact_digest_ms"]
                    + bucket["schema_check_ms"]
                )
        return {mode: dict(agents) for mode, agents in by_mode_agent.items()}

    def export(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        rows = [asdict(row) for row in self.rows()]
        agent_rows = [asdict(row) for row in self.agent_rows()]
        fieldnames = list(TaskMetricRow.__dataclass_fields__.keys())
        agent_fieldnames = list(AgentMetricRow.__dataclass_fields__.keys())

        with (output_dir / "metrics.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        with (output_dir / "agent_metrics.csv").open(
            "w", newline="", encoding="utf-8"
        ) as fh:
            writer = csv.DictWriter(fh, fieldnames=agent_fieldnames)
            writer.writeheader()
            writer.writerows(agent_rows)

        agent_payload = {
            "rows": agent_rows,
            "summary": self.agent_summary(),
            "note": "ttft_ms is 0 for the current non-streaming client.",
        }
        with (output_dir / "agent_metrics.json").open("w", encoding="utf-8") as fh:
            json.dump(agent_payload, fh, ensure_ascii=False, indent=2)

        payload = {
            "rows": rows,
            "summary": self.summary(),
            "agent_rows": agent_rows,
        }
        with (output_dir / "metrics.json").open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def _row(self, task_id: str, round_id: int, mode: Mode) -> TaskMetricRow:
        key = (task_id, round_id, mode)
        if key not in self._rows:
            self._rows[key] = TaskMetricRow(task_id=task_id, round_id=round_id, mode=mode)
        return self._rows[key]

    def _agent_row(
        self, task_id: str, round_id: int, mode: Mode, agent_id: str
    ) -> AgentMetricRow:
        key = (task_id, round_id, mode, agent_id)
        if key not in self._agent_rows:
            self._agent_rows[key] = AgentMetricRow(
                task_id=task_id, round_id=round_id, mode=mode, agent=agent_id
            )
        return self._agent_rows[key]

    def _refresh_agent_tokens_per_second(self, row: AgentMetricRow) -> None:
        llm_seconds = row.llm_wall_time_ms / 1000.0
        row.tokens_per_second = (
            row.llm_completion_tokens / llm_seconds if llm_seconds > 0 else 0.0
        )

    def _apply_token_meta(self, row: TaskMetricRow, token_count: Any) -> None:
        row.token_count_method = token_count.token_count_method
        row.tokenizer_name = token_count.tokenizer_name
        row.tokenizer_version = token_count.tokenizer_version
        self._tokenizer_meta = {
            "token_count_method": token_count.token_count_method,
            "tokenizer_name": token_count.tokenizer_name,
            "tokenizer_version": token_count.tokenizer_version,
        }
