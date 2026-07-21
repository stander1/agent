from __future__ import annotations

import functools
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import json
import os
import re
import sys
import threading
import time
from copy import copy as shallow_copy
from dataclasses import dataclass, field, is_dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Iterable

from agent_runtime.core.kernel import AgentDescriptor, CollaborationKernel, MemoryContext
from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.drivers.autogen_codec import AutoGenMessageCodec
from agent_runtime.drivers.autogen_shp import (
    AutoGenShadowHandoffPlan,
    plan_shadow_broadcast,
    plan_shadow_handoff,
)
from agent_runtime.drivers.loader import DriverActivation
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.reliability.final_delivery_guard import assess_final_delivery
from agent_runtime.state.state_pool import StatePoolLite, StateRef

if TYPE_CHECKING:
    from agent_runtime.bootstrap.startup import BootstrapContext


SUPPORTED_MODULE_ROOTS = (
    "autogen_agentchat",
    "autogen_core",
    "autogen_ext",
    "autogen",
)
PATCH_TARGETS = {
    "agentchat_agent": ("on_messages", "on_messages_stream"),
    "agentchat_team": ("run", "run_stream"),
    "core_runtime": ("send_message", "publish_message"),
    "core_agent": ("on_message",),
    "model_client": ("create", "create_stream"),
}
BROADCAST_MODE_ENV = "AGENTLITE_AUTOGEN_BROADCAST_MODE"
TEAM_REWRITE_ENV = "AGENTLITE_AUTOGEN_TEAM_REWRITE"
HANDOFF_REWRITE_ENV = "AGENTLITE_AUTOGEN_HANDOFF_REWRITE"
TOOL_SUMMARY_REWRITE_ENV = "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE"
CORE_CONTENT_REWRITE_ENV = "AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE"
CORE_RECEIVER_HYDRATE_ENV = "AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE"
SHARED_MEMORY_ENV = "AGENTLITE_AUTOGEN_SHARED_MEMORY"
MEMORY_SCOPE_ENV = "AGENTLITE_MEMORY_SCOPE"
FINAL_DELIVERY_MARKER_ENV = "AGENTLITE_AUTOGEN_FINAL_MARKER"
BROADCAST_MODES = ("shadow-only", "dry-run-rewrite", "real-rewrite")
CORE_RECEIVER_HYDRATE_MODES = ("off", "prompt-view")
DRIVER_PHASE = "v5.13o"
TEAM_REAL_REWRITE_DISABLED_REASON = (
    "team_level_real_rewrite_not_enabled_for_guarded_agent_input"
)
CORE_REAL_REWRITE_DISABLED_REASON = (
    "core_content_real_rewrite_not_enabled_for_guarded_runtime_input"
)
CORE_REWRITE_FIELDS = ("content", "body", "text")
CORE_REWRITE_MARKER = "AGENTLITE_CORE_CONTENT_REWRITE v1"
CORE_PROMPT_VIEW_MARKER = "AGENTLITE_CORE_PROMPT_VIEW v1"
SHARED_MEMORY_MARKER = "AGENTLITE_SHARED_MEMORY v1"
FALLBACK_REASON_BUCKETS = {
    "missing_messages_argument": "input_contract_missing",
    "messages_not_sequence": "input_contract_invalid",
    "empty_messages": "input_contract_empty",
    "non_text_message_present": "unsupported_message_type",
    "empty_text_payload": "empty_payload",
    "already_agentlite_rewritten": "agentlite_packet_already_rewritten",
    "missing_state_refs": "state_ref_unavailable",
    "schema_invalid": "schema_guard_failed",
    "prompt_view_missing": "prompt_view_unavailable",
    "message_clone_failed": "message_clone_failed",
    "token_not_reduced": "cost_gate_failed",
    "handoff_rewrite_requires_target_preservation": "handoff_control_guard",
    "handoff_rewrite_requires_context_preservation": "handoff_control_guard",
    "handoff_typed_rewrite_candidate_dry_run_only": "typed_rewrite_dry_run_guard",
    "tool_summary_typed_rewrite_candidate_dry_run_only": "typed_rewrite_dry_run_guard",
    "tool_rewrite_requires_call_lineage": "tool_lineage_guard",
    "tool_rewrite_requires_result_lineage": "tool_lineage_guard",
    TEAM_REAL_REWRITE_DISABLED_REASON: "team_rewrite_env_guard",
    "missing_team_task_argument": "team_task_contract_missing",
    "unsupported_team_task_type": "team_task_contract_invalid",
    "empty_team_task_sequence": "input_contract_empty",
    "team_task_sequence_missing_text_message": "unsupported_message_type",
    "team_task_clone_failed": "message_clone_failed",
    "already_team_rewritten": "team_task_already_rewritten",
    "empty_team_task_payload": "empty_payload",
    "missing_team_participants": "team_participant_missing",
    "team_task_token_not_reduced": "cost_gate_failed",
    CORE_REAL_REWRITE_DISABLED_REASON: "core_rewrite_env_guard",
    "missing_core_message_argument": "core_message_contract_missing",
    "unsupported_core_message_content_field": "core_message_contract_invalid",
    "already_core_rewritten": "core_message_already_rewritten",
    "empty_core_message_payload": "empty_payload",
    "core_message_clone_failed": "message_clone_failed",
    "core_message_type_not_preserved": "typed_rewrite_type_guard",
    "core_message_field_not_replaced": "typed_rewrite_content_guard",
    "core_message_token_not_reduced": "cost_gate_failed",
    "missing_core_response_result": "core_response_contract_missing",
    "unsupported_core_response_content_field": "core_response_contract_invalid",
    "already_core_response_rewritten": "core_response_already_rewritten",
    "empty_core_response_payload": "empty_payload",
    "core_response_clone_failed": "message_clone_failed",
    "core_response_type_not_preserved": "typed_rewrite_type_guard",
    "core_response_field_not_replaced": "typed_rewrite_content_guard",
    "core_response_token_not_reduced": "cost_gate_failed",
    "core_hydration_disabled": "core_hydration_env_guard",
    "core_hydration_marker_missing": "core_hydration_not_applicable",
    "core_hydration_state_ref_missing": "state_ref_unavailable",
    "core_hydration_prompt_view_missing": "prompt_view_unavailable",
    "core_hydration_clone_failed": "message_clone_failed",
    "core_hydration_type_not_preserved": "typed_rewrite_type_guard",
    "core_hydration_field_not_replaced": "typed_rewrite_content_guard",
}

_MANAGER: AutoGenHookManager | None = None
_MANAGER_LOCK = threading.Lock()


@dataclass(slots=True)
class HookCallContext:
    call_id: str
    task: TaskSpec
    agent: AgentDescriptor
    method_name: str
    target_kind: str
    team_participants: tuple[str, ...] = ()
    transport_metadata: dict[str, Any] = field(default_factory=dict)
    memory_context: MemoryContext = field(
        default_factory=lambda: MemoryContext([], [])
    )
    display_restore_enabled: bool = False
    display_original_text: str = ""
    display_original_source: str = "user"


@dataclass(slots=True)
class _MemoryContextSelection:
    refs: list[Any]
    prompt_views: list[str]
    candidate_count: int = 0
    deduplicated_count: int = 0
    deduplicated_views: list[str] = field(default_factory=list)


class AutoGenHookManager:
    """Transparent AutoGen instrumentation owned by the managed process."""

    def __init__(self, context: BootstrapContext) -> None:
        self.context = context
        self.session_dir = context.status_file.parent
        self.output_dir = self.session_dir / "autogen_driver"
        self.trace = TraceLogger(self.output_dir)
        self.metrics = MetricsCollector()
        self.token_counter = TokenCounter(allow_estimate=True)
        self.codec = AutoGenMessageCodec()
        self.broadcast_mode = _resolve_broadcast_mode(
            os.getenv(BROADCAST_MODE_ENV, "")
        )
        self.team_rewrite_enabled = _truthy_env(os.getenv(TEAM_REWRITE_ENV, ""))
        self.handoff_rewrite_enabled = _truthy_env(
            os.getenv(HANDOFF_REWRITE_ENV, "")
        )
        self.tool_summary_rewrite_enabled = _truthy_env(
            os.getenv(TOOL_SUMMARY_REWRITE_ENV, "")
        )
        self.core_content_rewrite_enabled = _truthy_env(
            os.getenv(CORE_CONTENT_REWRITE_ENV, "")
        )
        self.core_receiver_hydrate_mode = _resolve_core_receiver_hydrate_mode(
            os.getenv(CORE_RECEIVER_HYDRATE_ENV, "")
        )
        self.shared_memory_enabled = _resolve_shared_memory_enabled(
            broadcast_mode=self.broadcast_mode,
            raw_value=os.getenv(SHARED_MEMORY_ENV),
        )
        self.final_delivery_marker = (
            os.getenv(FINAL_DELIVERY_MARKER_ENV, "FINAL_ANSWER_READY").strip()
            or "FINAL_ANSWER_READY"
        )
        self.memory_scope_id = _resolve_memory_scope_id(context)
        memory_store = (
            MemoryStoreLite(
                context.data_dir
                / "shared_memory"
                / "autogen"
                / self.memory_scope_id
            )
            if self.shared_memory_enabled
            else MemoryStoreLite()
        )
        self.kernel = CollaborationKernel(
            agents=[],
            token_counter=self.token_counter,
            metrics=self.metrics,
            trace=self.trace,
            state_pool=StatePoolLite(self.output_dir / "state"),
            memory_store=memory_store,
        )
        self.kernel_session = self.kernel.open_session(
            framework="autogen",
            external_session_id=context.session_id,
            metadata={
                "target_cwd": str(context.target_cwd),
                "runtime_endpoint": context.runtime_endpoint,
                "driver_phase": DRIVER_PHASE,
                "broadcast_mode": self.broadcast_mode,
                "team_rewrite_enabled": self.team_rewrite_enabled,
                "handoff_rewrite_enabled": self.handoff_rewrite_enabled,
                "tool_summary_rewrite_enabled": self.tool_summary_rewrite_enabled,
                "shared_memory_enabled": self.shared_memory_enabled,
                "memory_scope_id": self.memory_scope_id,
                "final_delivery_marker": self.final_delivery_marker,
            },
        )
        self._sequence = 0
        self._lock = threading.Lock()
        self._memory_lock = threading.RLock()
        self._promoted_memory_fingerprints: set[str] = set()
        self._collaboration_group_by_agent: dict[str, str] = {}
        self._current_team_task_by_group: dict[str, str] = {}
        self._user_task_history_by_group: dict[str, list[str]] = {}
        self._core_response_rewrite_depth = 0
        self._patched_methods: set[str] = set()
        self._patched_modules: set[str] = set()
        self._import_finder = AutoGenImportFinder(self)

    @property
    def patched_methods(self) -> list[str]:
        return sorted(self._patched_methods)

    @property
    def patched_modules(self) -> list[str]:
        return sorted(self._patched_modules)

    def _prepare_shared_memory_context(
        self,
        *,
        task: TaskSpec,
        target_kind: str,
        method_name: str,
        prompt: str,
    ) -> MemoryContext:
        empty = MemoryContext([], [])
        if not self.shared_memory_enabled or not prompt.strip():
            return empty
        if SHARED_MEMORY_MARKER in prompt or _contains_agentlite_rewrite_marker(prompt):
            return empty
        supported_call = (
            target_kind == "agentchat_team" and method_name == "run_stream"
        ) or (
            target_kind == "agentchat_agent"
            and method_name in {"on_messages", "on_messages_stream"}
        )
        if not supported_call:
            return empty
        with self._memory_lock:
            memory_context = self._safe_kernel_call(
                "autogen_prepare_shared_memory",
                lambda: self.kernel.prepare_memory_context(
                    task=task,
                    round_id=1,
                    mode="runtime_lite",
                    final_task=_looks_like_final_task(prompt),
                    deliverable_budget_chars=1800,
                    strict_group_scope=True,
                ),
            )
        if not isinstance(memory_context, MemoryContext):
            return empty
        memory_text = "\n".join(memory_context.prompt_views)
        self.trace.write(
            "autogen_memory_retrieval",
            {
                "call_id": task.task_id,
                "task_id": task.task_id,
                "group_id": task.group_id,
                "target_kind": target_kind,
                "method": method_name,
                "memory_scope_id": self.memory_scope_id,
                "memory_query_count": 1,
                "memory_hit_count": len(memory_context.refs),
                "memory_injected_count": 0,
                "useful_memory_hit_count": 0,
                "wrong_memory_hit_count": 0,
                "unassessed_memory_hit_count": 0,
                "memory_use_status": "retrieved_pending_cost_gate",
                "retrieved_memory_tokens": _count_tokens(
                    self.token_counter,
                    memory_text,
                ),
                "memory_refs": [
                    _memory_ref_payload(ref) for ref in memory_context.refs
                ],
                "prompt_view_preview": _preview(memory_text),
            },
        )
        return memory_context

    def _promote_autogen_output_to_memory(
        self,
        *,
        context: HookCallContext,
        decoded_messages: list[Any],
        text: str,
        state_refs: list[StateRef],
    ) -> Any | None:
        if not self.shared_memory_enabled or not state_refs:
            return None
        delivery_assessment = None
        if context.target_kind == "agentchat_team" and context.method_name == "run_stream":
            with self._memory_lock:
                grounding_contexts = tuple(
                    self._user_task_history_by_group.get(
                        context.task.group_id,
                        (),
                    )
                )
            candidate_text, candidate_source = _latest_visible_message(
                decoded_messages,
                text,
            )
            delivery_assessment = assess_final_delivery(
                request=context.task.prompt,
                content=candidate_text,
                source=candidate_source,
                marker=self.final_delivery_marker,
                require_marker=True,
                grounding_contexts=grounding_contexts,
            )
            if delivery_assessment.valid:
                candidate_kind = "autogen_team_final"
                confidence, importance, coverage = 0.86, 0.82, 0.78
            else:
                candidate_kind = "autogen_team_unvalidated"
                confidence, importance, coverage = 0.30, 0.30, 0.20
        elif context.target_kind == "agentchat_agent" and context.method_name in {
            "on_messages",
            "on_messages_stream",
        }:
            candidate_kind = "autogen_agent_intermediate"
            confidence, importance, coverage = 0.50, 0.40, 0.60
        else:
            return None

        summary = _memory_summary(decoded_messages, text, limit=900)
        if len(summary.strip()) < 12:
            return None
        fingerprint = hashlib.sha256(
            f"{candidate_kind}:{context.agent.agent_id}:{summary}".encode("utf-8")
        ).hexdigest()
        with self._memory_lock:
            if fingerprint in self._promoted_memory_fingerprints:
                return None
            self._promoted_memory_fingerprints.add(fingerprint)
            slot_hint = _autogen_memory_slot_hint(context.task.prompt, summary)
            report = self._safe_kernel_call(
                "autogen_promote_memory_candidate",
                lambda: self.kernel.promote_memory_candidate(
                    task=context.task,
                    round_id=1,
                    mode="runtime_lite",
                    agent=context.agent,
                    summary=summary,
                    state_refs=state_refs,
                    slot_hint=slot_hint,
                    task_topic=(
                        f"autogen.{_safe_identifier(context.task.group_id)}."
                        f"{slot_hint}"
                    ),
                    candidate_kind=candidate_kind,
                    confidence=confidence,
                    importance_hint=importance,
                    coverage_score=coverage,
                ),
            )
            self._write_pool_snapshot(context.task)
        if report is not None:
            self.trace.write(
                "autogen_memory_candidate",
                {
                    "call_id": context.call_id,
                    "task_id": context.task.task_id,
                    "agent_id": context.agent.agent_id,
                    "candidate_kind": candidate_kind,
                    "candidate_id": getattr(report, "candidate_id", ""),
                    "admission_status": getattr(report, "admission_status", ""),
                    "admission_reasons": getattr(report, "admission_reasons", []),
                    "delivery_assessment": (
                        delivery_assessment.to_dict()
                        if delivery_assessment is not None
                        else {}
                    ),
                    "memory_refs": (
                        [_memory_ref_payload(report.memory_ref)]
                        if getattr(report, "memory_ref", None) is not None
                        else []
                    ),
                },
            )
        return report

    def _write_pool_snapshot(self, task: TaskSpec) -> None:
        snapshot = {
            "task_id": task.task_id,
            "group_id": task.group_id,
            "mode": "runtime_lite",
            "memory_scope_id": self.memory_scope_id,
            "state_pool": self.kernel.state_pool.snapshot(),
            "memory_store": self.kernel.memory_store.snapshot(),
        }
        path = self.output_dir / "pool_snapshot_latest.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _enter_core_send_message(self, *, response_rewrite_enabled: bool) -> None:
        if not response_rewrite_enabled:
            return
        with self._lock:
            self._core_response_rewrite_depth += 1

    def _exit_core_send_message(self, *, response_rewrite_enabled: bool) -> None:
        if not response_rewrite_enabled:
            return
        with self._lock:
            self._core_response_rewrite_depth = max(
                0,
                self._core_response_rewrite_depth - 1,
            )

    def _core_response_rewrite_active(self) -> bool:
        with self._lock:
            return self._core_response_rewrite_depth > 0

    def install(self) -> None:
        if not any(item is self._import_finder for item in sys.meta_path):
            sys.meta_path.insert(0, self._import_finder)
        for module in list(sys.modules.values()):
            self.patch_module(module)
        self.trace.write(
            "autogen_driver_installed",
            {
                "session_id": self.context.session_id,
                "supported_module_roots": list(SUPPORTED_MODULE_ROOTS),
                "patch_targets": PATCH_TARGETS,
                "broadcast_mode": self.broadcast_mode,
            },
        )

    def patch_module(self, module: ModuleType | None) -> None:
        if module is None:
            return
        module_name = getattr(module, "__name__", "")
        target_methods = self._target_methods_for(module_name)
        if not target_methods:
            return

        patched_count = 0
        for _, value in list(vars(module).items()):
            if inspect.isclass(value):
                patched_count += self._patch_class(
                    value, module_name=module_name, target_methods=target_methods
                )
        if patched_count:
            self._patched_modules.add(module_name)
            self.trace.write(
                "autogen_module_patched",
                {
                    "module": module_name,
                    "patched_method_count": patched_count,
                },
            )

    def record_call_start(
        self,
        *,
        instance: object,
        method_name: str,
        target_kind: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> HookCallContext:
        call_id = self._next_call_id()
        agent = self.describe_agent(instance, target_kind=target_kind)
        team_participants = self.describe_team_participants(
            instance,
            target_kind=target_kind,
        )
        decoded_messages = self.codec.decode_many({"args": args, "kwargs": kwargs})
        prompt = self.codec.render_text(decoded_messages) or _extract_text(
            {"args": args, "kwargs": kwargs}
        )
        collaboration_group_id = _resolve_collaboration_group_id(
            memory_scope_id=self.memory_scope_id,
            target_kind=target_kind,
            agent_id=agent.agent_id,
            team_participants=team_participants,
        )
        if self.shared_memory_enabled:
            normalized_agent_id = _safe_identifier(agent.agent_id)
            with self._memory_lock:
                if target_kind == "agentchat_team":
                    for participant in team_participants:
                        self._collaboration_group_by_agent[
                            _safe_identifier(participant)
                        ] = collaboration_group_id
                elif target_kind == "agentchat_agent":
                    collaboration_group_id = self._collaboration_group_by_agent.get(
                        normalized_agent_id,
                        collaboration_group_id,
                    )
        task = TaskSpec(
            task_id=call_id,
            group_id=(
                collaboration_group_id
                if self.shared_memory_enabled
                else self.context.session_id
            ),
            title=f"AutoGen {target_kind} {method_name}",
            prompt=prompt,
            expected_agents=[agent.agent_id],
        )
        if target_kind == "agentchat_team":
            task_value, _ = _extract_team_task_argument(args, kwargs)
            task_text, _ = _team_task_display_identity(task_value)
            if not task_text:
                task_text = _extract_text(task_value)
            if task_text.strip():
                with self._memory_lock:
                    self._current_team_task_by_group[task.group_id] = task_text.strip()
                    history = self._user_task_history_by_group.setdefault(
                        task.group_id,
                        [],
                    )
                    if not history or history[-1] != task_text.strip():
                        history.append(task_text.strip())
                        del history[:-8]
                termination = getattr(instance, "_termination_condition", None)
                recorder = getattr(termination, "record_user_task", None)
                if callable(recorder):
                    recorder(task_text.strip())
        memory_context = self._prepare_shared_memory_context(
            task=task,
            target_kind=target_kind,
            method_name=method_name,
            prompt=prompt,
        )
        hook_context = HookCallContext(
            call_id=call_id,
            task=task,
            agent=agent,
            method_name=method_name,
            target_kind=target_kind,
            team_participants=team_participants,
            transport_metadata=(
                _core_transport_metadata(
                    method_name=method_name,
                    args=args,
                    kwargs=kwargs,
                )
                if target_kind == "core_runtime"
                else _core_agent_receive_metadata(
                    method_name=method_name,
                    args=args,
                    kwargs=kwargs,
                )
                if target_kind == "core_agent"
                else {}
            ),
            memory_context=memory_context,
        )
        self._safe_kernel_call(
            "before_agent_receive",
            lambda: self.kernel.before_agent_receive(
                task=task,
                round_id=1,
                mode="runtime_lite",
                agent=agent,
                state_refs=[],
                memory_context=memory_context,
            ),
        )
        self.trace.write(
            "autogen_agent_receive",
            {
                "call_id": call_id,
                "agent_id": agent.agent_id,
                "role": agent.role,
                "target_kind": target_kind,
                "method": method_name,
                "input_chars": len(prompt),
                "input_preview": _preview(prompt),
                "team_participants": list(hook_context.team_participants),
                "transport_metadata": hook_context.transport_metadata,
                "memory_refs": [
                    _memory_ref_payload(ref) for ref in memory_context.refs
                ],
                "memory_hit_count": len(memory_context.refs),
                "retrieved_memory_tokens": _count_tokens(
                    self.token_counter,
                    "\n".join(memory_context.prompt_views),
                ),
                "decoded_messages": [
                    message.to_dict() for message in decoded_messages
                ],
            },
        )
        self._record_team_broadcast_input_state(
            context=hook_context,
            decoded_messages=decoded_messages,
            native_text=prompt,
        )
        self._record_transport_input_state(
            context=hook_context,
            decoded_messages=decoded_messages,
            native_text=prompt,
        )
        return hook_context

    def record_stream_item(self, context: HookCallContext, item: Any) -> None:
        decoded_messages = self.codec.decode_many(item)
        text = self.codec.render_text(decoded_messages) or _extract_text(item)
        self.trace.write(
            "autogen_stream_item",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "output_chars": len(text),
                "output_preview": _preview(text),
                "decoded_messages": [
                    message.to_dict() for message in decoded_messages
                ],
            },
        )

    def restore_call_result_for_display(
        self,
        context: HookCallContext,
        result: Any,
    ) -> Any:
        """Hide the internal Team rewrite envelope from caller-facing results."""
        if not context.display_restore_enabled:
            return result
        restored, restored_count = _restore_team_display_item(
            result,
            original_text=context.display_original_text,
            original_source=context.display_original_source,
        )
        if restored_count:
            self.trace.write(
                "autogen_team_display_restored",
                {
                    "call_id": context.call_id,
                    "agent_id": context.agent.agent_id,
                    "target_kind": context.target_kind,
                    "method": context.method_name,
                    "result_type": type(result).__name__,
                    "restored_message_count": restored_count,
                },
            )
        return restored

    def record_call_end(self, context: HookCallContext, result: Any) -> None:
        decoded_messages = self.codec.decode_many(result)
        text = self.codec.render_text(decoded_messages) or _extract_text(result)
        state_refs: list[Any] = []
        if _has_semantic_payload(decoded_messages, text):
            written_state_refs = self._safe_kernel_call(
                "write_agent_state",
                lambda: self.kernel.write_agent_state(
                    task=context.task,
                    round_id=1,
                    mode="runtime_lite",
                    agent=context.agent,
                    output=AgentOutput(
                        agent_id=context.agent.agent_id,
                        content=text,
                        metadata={
                            "framework": "autogen",
                            "target_kind": context.target_kind,
                            "method": context.method_name,
                            "native_result_type": type(result).__name__,
                            "autogen_decoded_messages": [
                                message.to_dict() for message in decoded_messages
                            ],
                        },
                    ),
                ),
            )
            state_refs = list(written_state_refs or [])
        state_ref_payload = []
        for state_ref in state_refs:
            ref_payload = self._safe_kernel_call(
                "state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)
        admission_report = self._promote_autogen_output_to_memory(
            context=context,
            decoded_messages=decoded_messages,
            text=text,
            state_refs=list(state_refs),
        )
        admitted_memory_refs = (
            [_memory_ref_payload(admission_report.memory_ref)]
            if getattr(admission_report, "memory_ref", None) is not None
            else []
        )
        self.trace.write(
            "autogen_agent_output",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "output_chars": len(text),
                "output_preview": _preview(text),
                "state_refs": state_ref_payload,
                "memory_refs": admitted_memory_refs,
                "memory_admission_status": getattr(
                    admission_report,
                    "admission_status",
                    "",
                ),
                "decoded_messages": [
                    message.to_dict() for message in decoded_messages
                ],
            },
        )
        self._record_shadow_handoff(
            context=context,
            decoded_messages=decoded_messages,
            native_text=text,
            state_refs=list(state_refs or []),
            state_ref_payload=state_ref_payload,
            memory_refs=(
                [admission_report.memory_ref]
                if getattr(admission_report, "memory_ref", None) is not None
                else []
            ),
        )
        self._record_shadow_broadcast_replacement(
            context=context,
            decoded_messages=decoded_messages,
            native_text=text,
            state_refs=list(state_refs or []),
            state_ref_payload=state_ref_payload,
            native_scope="team_output",
        )

    def _record_shadow_handoff(
        self,
        *,
        context: HookCallContext,
        decoded_messages: list[Any],
        native_text: str,
        state_refs: list[Any],
        state_ref_payload: list[dict[str, Any]],
        memory_refs: list[Any],
    ) -> None:
        if not state_refs:
            return
        plan = plan_shadow_handoff(
            sender=context.agent.agent_id,
            target_kind=context.target_kind,
            method_name=context.method_name,
            decoded_messages=decoded_messages,
            native_text=native_text,
        )
        envelope_json = self._safe_kernel_call(
            "autogen_build_shp_shadow_handoff",
            lambda: self.kernel.build_handoff(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                sender=context.agent.agent_id,
                declared_receiver=plan.declared_receiver,
                summary=plan.summary,
                state_refs=state_refs,
                memory_refs=memory_refs,
            ),
        )
        if not isinstance(envelope_json, str) or not envelope_json:
            return
        gate = self._safe_kernel_call(
            "autogen_shp_shadow_gate",
            lambda: self.kernel.handoff_gate(envelope_json),
        )
        envelope_payload = _json_object_or_empty(envelope_json)
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        wire_envelope_json = json.dumps(
            wire_envelope, ensure_ascii=False, separators=(",", ":")
        )
        native_tokens = _count_tokens(self.token_counter, native_text)
        audit_envelope_tokens = _count_tokens(self.token_counter, envelope_json)
        wire_tokens = _count_tokens(self.token_counter, wire_envelope_json)
        token_delta = native_tokens - wire_tokens
        reduction_ratio = (
            token_delta / native_tokens if native_tokens > 0 else 0.0
        )
        self.trace.write(
            "autogen_shp_handoff_shadow",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "plan": plan.to_dict(),
                "state_refs": state_ref_payload,
                "memory_refs": [
                    _memory_ref_payload(ref) for ref in memory_refs
                ],
                "state_ref_count": len(state_ref_payload),
                "shadow_envelope": envelope_payload,
                "shadow_envelope_json_chars": len(envelope_json),
                "shadow_wire_envelope": wire_envelope,
                "shadow_wire_json_chars": len(wire_envelope_json),
                "native_output_text_tokens": native_tokens,
                "shp_shadow_envelope_tokens": wire_tokens,
                "shp_shadow_audit_envelope_tokens": audit_envelope_tokens,
                "token_delta_native_minus_shp": token_delta,
                "token_reduction_ratio": round(reduction_ratio, 6),
                "cost_scope": (
                    "decoded_native_output_text_vs_compact_shadow_wire_envelope"
                ),
                "communication_gate": gate if isinstance(gate, dict) else {},
            },
        )

    def _record_transport_input_state(
        self,
        *,
        context: HookCallContext,
        decoded_messages: list[Any],
        native_text: str,
    ) -> None:
        message_kinds = {
            str(getattr(message, "message_kind", "") or "")
            for message in decoded_messages
        }
        if context.target_kind != "core_runtime":
            return
        if not _has_semantic_payload(decoded_messages, native_text):
            return
        state_refs = self._safe_kernel_call(
            "write_autogen_transport_input_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=native_text,
                    metadata={
                        "framework": "autogen",
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_result_type": "core_transport_input",
                        "transport_input_state": True,
                        "core_transport_metadata": context.transport_metadata,
                        "autogen_decoded_messages": [
                            message.to_dict() for message in decoded_messages
                        ],
                    },
                ),
            ),
        )
        state_ref_payload = []
        for state_ref in state_refs or []:
            ref_payload = self._safe_kernel_call(
                "transport_input_state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)
        self.trace.write(
            "autogen_transport_input_state",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "message_kinds": sorted(message_kinds),
                "input_chars": len(native_text),
                "input_preview": _preview(native_text),
                "transport_metadata": context.transport_metadata,
                "state_refs": state_ref_payload,
            },
        )
        self._record_core_transport_shadow(
            context=context,
            decoded_messages=decoded_messages,
            native_text=native_text,
            state_refs=list(state_refs or []),
            state_ref_payload=state_ref_payload,
        )

    def _record_core_transport_shadow(
        self,
        *,
        context: HookCallContext,
        decoded_messages: list[Any],
        native_text: str,
        state_refs: list[Any],
        state_ref_payload: list[dict[str, Any]],
    ) -> None:
        if not state_refs:
            return
        route = context.transport_metadata
        receiver = str(route.get("declared_receiver") or "autogen_runtime_peer")
        sender = str(route.get("sender") or context.agent.agent_id)
        summary = _build_core_transport_summary(
            sender=sender,
            method_name=context.method_name,
            receiver=receiver,
            message_kinds=[
                str(getattr(message, "message_kind", "") or "")
                for message in decoded_messages
            ],
            native_text=native_text,
        )
        envelope_json = self._safe_kernel_call(
            "autogen_build_core_transport_shadow",
            lambda: self.kernel.build_handoff(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                sender=sender,
                declared_receiver=receiver,
                summary=summary,
                state_refs=state_refs,
                memory_refs=[],
            ),
        )
        if not isinstance(envelope_json, str) or not envelope_json:
            return
        gate = self._safe_kernel_call(
            "autogen_core_transport_shadow_gate",
            lambda: self.kernel.handoff_gate(envelope_json),
        )
        envelope_payload = _json_object_or_empty(envelope_json)
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        wire_envelope_json = json.dumps(
            wire_envelope, ensure_ascii=False, separators=(",", ":")
        )
        state_prompt_views = []
        for state_ref in state_refs:
            view = self._safe_kernel_call(
                "autogen_core_transport_prompt_view",
                lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                    ref,
                    receiver,
                ),
            )
            if isinstance(view, str) and view:
                state_prompt_views.append(view)
        memory_refs = list(context.memory_context.refs)
        memory_prompt_views = list(context.memory_context.prompt_views)
        prompt_view_text = _join_state_and_memory_views(
            state_prompt_views,
            memory_prompt_views,
        )
        native_tokens = _count_tokens(self.token_counter, native_text)
        wire_tokens = _count_tokens(self.token_counter, wire_envelope_json)
        prompt_view_tokens = _count_tokens(self.token_counter, prompt_view_text)
        wire_plus_prompt_view_tokens = wire_tokens + prompt_view_tokens
        token_delta = native_tokens - wire_plus_prompt_view_tokens
        reduction_ratio = (
            token_delta / native_tokens if native_tokens > 0 else 0.0
        )
        self.trace.write(
            "autogen_core_transport_shadow",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "transport_metadata": route,
                "sender": sender,
                "declared_receiver": receiver,
                "message_kinds": sorted(
                    {
                        str(getattr(message, "message_kind", "") or "")
                        for message in decoded_messages
                    }
                ),
                "state_refs": state_ref_payload,
                "state_ref_count": len(state_ref_payload),
                "shadow_envelope": envelope_payload,
                "shadow_wire_envelope": wire_envelope,
                "schema_valid": _wire_envelope_schema_valid(wire_envelope),
                "prompt_view_available": bool(prompt_view_text.strip()),
                "prompt_view_preview": _preview(prompt_view_text),
                "native_transport_tokens": native_tokens,
                "shp_shadow_envelope_tokens": wire_tokens,
                "prompt_view_tokens": prompt_view_tokens,
                "wire_plus_prompt_view_tokens": wire_plus_prompt_view_tokens,
                "token_delta_native_minus_wire_plus_prompt_view": token_delta,
                "token_reduction_ratio": round(reduction_ratio, 6),
                "cost_scope": (
                    "core_runtime_native_transport_text_vs_compact_shadow_wire_"
                    "plus_prompt_view"
                ),
                "communication_gate": gate if isinstance(gate, dict) else {},
            },
        )

    def _rewrite_core_message_if_safe(
        self,
        context: HookCallContext,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        message, source = _extract_core_message_argument(args, kwargs)
        field_name, native_content = _core_message_text_field(message)
        native_type = type(message).__name__ if message is not None else ""
        fallback_reasons: list[str] = []
        if not self.core_content_rewrite_enabled:
            fallback_reasons.append(CORE_REAL_REWRITE_DISABLED_REASON)
        if message is None:
            fallback_reasons.append("missing_core_message_argument")
        if message is not None and not field_name:
            fallback_reasons.append("unsupported_core_message_content_field")
        if native_content and CORE_REWRITE_MARKER in native_content:
            fallback_reasons.append("already_core_rewritten")
        if field_name and not native_content.strip():
            fallback_reasons.append("empty_core_message_payload")
        if fallback_reasons:
            self._record_core_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=fallback_reasons,
                native_type=native_type,
                field_name=field_name,
                native_text=native_content,
            )
            return None

        receiver = str(
            context.transport_metadata.get("declared_receiver")
            or "autogen_runtime_peer"
        )
        sender = str(
            context.transport_metadata.get("sender")
            or context.agent.agent_id
        )
        decoded_messages = self.codec.decode_many(message)
        state_refs = self._safe_kernel_call(
            "write_autogen_core_content_real_rewrite_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=native_content,
                    metadata={
                        "framework": "autogen",
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_result_type": "core_content_real_rewrite_input",
                        "core_content_real_rewrite": True,
                        "core_message_native_type": native_type,
                        "core_message_field": field_name,
                        "core_transport_metadata": context.transport_metadata,
                        "autogen_decoded_messages": [
                            message.to_dict() for message in decoded_messages
                        ],
                    },
                ),
            ),
        )
        state_refs = list(state_refs or [])
        state_ref_payload = []
        for state_ref in state_refs:
            ref_payload = self._safe_kernel_call(
                "core_content_real_rewrite_state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)
        state_prompt_views = []
        for state_ref in state_refs:
            view = self._safe_kernel_call(
                "core_content_real_rewrite_prompt_view",
                lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                    ref,
                    receiver,
                ),
            )
            if isinstance(view, str) and view:
                state_prompt_views.append(view)
        memory_refs = list(context.memory_context.refs)
        memory_prompt_views = list(context.memory_context.prompt_views)
        prompt_view_text = _join_state_and_memory_views(
            state_prompt_views,
            memory_prompt_views,
        )
        envelope_json = self._safe_kernel_call(
            "autogen_build_core_content_real_rewrite_handoff",
            lambda: self.kernel.build_handoff(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                sender=sender,
                declared_receiver=receiver,
                summary=(
                    "AutoGen Core message text field moved to StatePool while "
                    "preserving the native Python message type"
                ),
                state_refs=state_refs,
                memory_refs=memory_refs,
            ),
        )
        envelope_payload = (
            _json_object_or_empty(envelope_json)
            if isinstance(envelope_json, str)
            else {}
        )
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        rewritten_content = _build_core_content_rewrite_content(
            wire_envelope=wire_envelope,
            prompt_view_text=prompt_view_text,
        )
        replacement_message = _clone_message_with_text_field(
            message,
            field_name=field_name,
            content=rewritten_content,
        )
        replacement_field = (
            _field_value(replacement_message, field_name)
            if replacement_message is not None
            else ""
        )
        semantic_checks = {
            "message_type_preserved": bool(
                replacement_message is not None
                and type(replacement_message) is type(message)
            ),
            "content_field_preserved": field_name in CORE_REWRITE_FIELDS,
            "content_replaced_only": replacement_field == rewritten_content,
            "state_ref_available": bool(state_refs),
            "schema_valid": _wire_envelope_schema_valid(wire_envelope),
            "prompt_view_available": bool(prompt_view_text.strip()),
        }
        if not state_refs:
            fallback_reasons.append("missing_state_refs")
        if not semantic_checks["schema_valid"]:
            fallback_reasons.append("schema_invalid")
        if not semantic_checks["prompt_view_available"]:
            fallback_reasons.append("prompt_view_missing")
        if replacement_message is None:
            fallback_reasons.append("core_message_clone_failed")
        if not semantic_checks["message_type_preserved"]:
            fallback_reasons.append("core_message_type_not_preserved")
        if not semantic_checks["content_replaced_only"]:
            fallback_reasons.append("core_message_field_not_replaced")
        native_tokens = _count_tokens(self.token_counter, native_content)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        if native_tokens <= rewritten_tokens:
            fallback_reasons.append("core_message_token_not_reduced")
        if fallback_reasons:
            self._record_core_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=fallback_reasons,
                native_type=native_type,
                field_name=field_name,
                native_text=native_content,
                rewritten_content=rewritten_content,
                state_refs=state_ref_payload,
                wire_envelope=wire_envelope,
                prompt_view_text=prompt_view_text,
                semantic_checks=semantic_checks,
            )
            return None

        new_args, new_kwargs = _replace_core_message_argument(
            args,
            kwargs,
            source=source,
            replacement=replacement_message,
        )
        self._record_core_rewrite_audit(
            context=context,
            applied=True,
            fallback_reasons=[],
            native_type=native_type,
            field_name=field_name,
            native_text=native_content,
            rewritten_content=rewritten_content,
            state_refs=state_ref_payload,
            wire_envelope=wire_envelope,
            prompt_view_text=prompt_view_text,
            semantic_checks=semantic_checks,
        )
        return new_args, new_kwargs

    def _record_core_rewrite_audit(
        self,
        *,
        context: HookCallContext,
        applied: bool,
        fallback_reasons: list[str],
        native_type: str = "",
        field_name: str = "",
        native_text: str = "",
        rewritten_content: str = "",
        state_refs: list[dict[str, Any]] | None = None,
        memory_refs: list[dict[str, Any]] | None = None,
        wire_envelope: dict[str, Any] | None = None,
        prompt_view_text: str = "",
        retrieved_memory_tokens: int = 0,
        semantic_checks: dict[str, Any] | None = None,
    ) -> None:
        native_tokens = _count_tokens(self.token_counter, native_text)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        total_prompt_view_tokens = _count_tokens(
            self.token_counter,
            prompt_view_text,
        )
        rewritten_wire_tokens = max(
            0,
            rewritten_tokens - total_prompt_view_tokens,
        )
        fallback_buckets = _fallback_buckets(fallback_reasons)
        self.trace.write(
            "autogen_core_content_real_rewrite",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "transport_metadata": context.transport_metadata,
                "broadcast_mode": self.broadcast_mode,
                "core_content_rewrite_enabled": self.core_content_rewrite_enabled,
                "candidate_generated": bool(rewritten_content),
                "rewrite_attempt_count": 1,
                "rewrite_applied_count": 1 if applied else 0,
                "rewrite_fallback_count": 0 if applied else 1,
                "rewrite_applied": applied,
                "real_message_mutation": applied,
                "fallback_required": not applied,
                "fallback_reasons": sorted(set(fallback_reasons)),
                "fallback_buckets": fallback_buckets,
                "fallback_bucket_counts": _count_values(fallback_buckets),
                "native_message_type": native_type,
                "rewritten_field": field_name,
                "state_refs": state_refs or [],
                "state_ref_count": len(state_refs or []),
                "shadow_wire_envelope": wire_envelope or {},
                "schema_valid": _wire_envelope_schema_valid(wire_envelope or {}),
                "prompt_view_available": bool(prompt_view_text.strip()),
                "prompt_view_tokens": max(
                    0,
                    total_prompt_view_tokens - retrieved_memory_tokens,
                ),
                "retrieved_memory_tokens": retrieved_memory_tokens,
                "native_content_tokens": native_tokens,
                "rewritten_content_tokens": rewritten_tokens,
                "rewritten_wire_tokens": rewritten_wire_tokens,
                "token_delta_native_minus_rewrite": native_tokens - rewritten_tokens,
                "rewritten_preview": _preview(rewritten_content),
                "semantic_checks": semantic_checks or {},
            },
        )

    def _hydrate_core_agent_message_if_needed(
        self,
        context: HookCallContext,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        message, source = _extract_core_message_argument(args, kwargs)
        field_name, content = _core_message_text_field(message)
        if not field_name or CORE_REWRITE_MARKER not in content:
            return None
        native_type = type(message).__name__ if message is not None else ""
        fallback_reasons: list[str] = []
        if self.core_receiver_hydrate_mode == "off":
            fallback_reasons.append("core_hydration_disabled")

        wire_envelope = _extract_core_rewrite_wire_envelope(content)
        state_refs = _state_refs_from_wire_envelope(wire_envelope)
        prompt_views = []
        if self.core_receiver_hydrate_mode == "prompt-view":
            for state_ref in state_refs:
                view = self._safe_kernel_call(
                    "core_receiver_hydrate_prompt_view",
                    lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                        ref,
                        context.agent.agent_id,
                    ),
                )
                if isinstance(view, str) and view:
                    prompt_views.append(view)
        embedded_prompt_view = _extract_core_embedded_prompt_view(content)
        hydrated_content = "\n".join(prompt_views).strip() or embedded_prompt_view
        if not state_refs:
            fallback_reasons.append("core_hydration_state_ref_missing")
        if not hydrated_content.strip():
            fallback_reasons.append("core_hydration_prompt_view_missing")

        replacement_message = _clone_message_with_text_field(
            message,
            field_name=field_name,
            content=hydrated_content,
        )
        replacement_field = (
            _field_value(replacement_message, field_name)
            if replacement_message is not None
            else ""
        )
        semantic_checks = {
            "message_type_preserved": bool(
                replacement_message is not None
                and type(replacement_message) is type(message)
            ),
            "content_field_preserved": field_name in CORE_REWRITE_FIELDS,
            "content_replaced_with_prompt_view": replacement_field == hydrated_content,
            "state_ref_available": bool(state_refs),
            "prompt_view_available": bool(hydrated_content.strip()),
            "wire_marker_removed": CORE_REWRITE_MARKER not in str(replacement_field),
        }
        if replacement_message is None:
            fallback_reasons.append("core_hydration_clone_failed")
        if not semantic_checks["message_type_preserved"]:
            fallback_reasons.append("core_hydration_type_not_preserved")
        if not semantic_checks["content_replaced_with_prompt_view"]:
            fallback_reasons.append("core_hydration_field_not_replaced")
        if fallback_reasons:
            self._record_core_hydration_audit(
                context=context,
                applied=False,
                fallback_reasons=fallback_reasons,
                native_type=native_type,
                field_name=field_name,
                wire_envelope=wire_envelope,
                state_refs=[self.kernel.state_pool.ref_to_dict(ref) for ref in state_refs],
                original_rewritten_content=content,
                hydrated_content=hydrated_content,
                semantic_checks=semantic_checks,
            )
            return None

        new_args, new_kwargs = _replace_core_message_argument(
            args,
            kwargs,
            source=source,
            replacement=replacement_message,
        )
        self._record_core_hydration_audit(
            context=context,
            applied=True,
            fallback_reasons=[],
            native_type=native_type,
            field_name=field_name,
            wire_envelope=wire_envelope,
            state_refs=[self.kernel.state_pool.ref_to_dict(ref) for ref in state_refs],
            original_rewritten_content=content,
            hydrated_content=hydrated_content,
            semantic_checks=semantic_checks,
        )
        return new_args, new_kwargs

    def _record_core_hydration_audit(
        self,
        *,
        context: HookCallContext,
        applied: bool,
        fallback_reasons: list[str],
        native_type: str = "",
        field_name: str = "",
        wire_envelope: dict[str, Any] | None = None,
        state_refs: list[dict[str, Any]] | None = None,
        original_rewritten_content: str = "",
        hydrated_content: str = "",
        semantic_checks: dict[str, Any] | None = None,
    ) -> None:
        fallback_buckets = _fallback_buckets(fallback_reasons)
        self.trace.write(
            "autogen_core_receiver_hydration",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "core_receiver_hydrate_mode": self.core_receiver_hydrate_mode,
                "candidate_detected": bool(original_rewritten_content),
                "hydration_attempt_count": 1,
                "hydration_applied_count": 1 if applied else 0,
                "hydration_fallback_count": 0 if applied else 1,
                "hydration_applied": applied,
                "fallback_required": not applied,
                "fallback_reasons": sorted(set(fallback_reasons)),
                "fallback_buckets": fallback_buckets,
                "fallback_bucket_counts": _count_values(fallback_buckets),
                "native_message_type": native_type,
                "hydrated_field": field_name,
                "state_refs": state_refs or [],
                "state_ref_count": len(state_refs or []),
                "shadow_wire_envelope": wire_envelope or {},
                "prompt_view_available": bool(hydrated_content.strip()),
                "wire_marker_removed": CORE_REWRITE_MARKER not in hydrated_content,
                "original_rewritten_tokens": _count_tokens(
                    self.token_counter,
                    original_rewritten_content,
                ),
                "hydrated_content_tokens": _count_tokens(
                    self.token_counter,
                    hydrated_content,
                ),
                "hydrated_preview": _preview(hydrated_content),
                "semantic_checks": semantic_checks or {},
            },
        )

    def _rewrite_core_response_if_safe(
        self,
        context: HookCallContext,
        result: Any,
    ) -> Any | None:
        field_name, native_content = _core_message_text_field(result)
        native_type = type(result).__name__ if result is not None else ""
        fallback_reasons: list[str] = []
        if not self.core_content_rewrite_enabled:
            fallback_reasons.append(CORE_REAL_REWRITE_DISABLED_REASON)
        if result is None:
            fallback_reasons.append("missing_core_response_result")
        if result is not None and not field_name:
            fallback_reasons.append("unsupported_core_response_content_field")
        if native_content and CORE_REWRITE_MARKER in native_content:
            fallback_reasons.append("already_core_response_rewritten")
        if field_name and not native_content.strip():
            fallback_reasons.append("empty_core_response_payload")
        if fallback_reasons:
            self._record_core_response_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=fallback_reasons,
                native_type=native_type,
                field_name=field_name,
                native_text=native_content,
            )
            return None

        sender = context.agent.agent_id
        receiver = str(
            context.transport_metadata.get("sender")
            or context.transport_metadata.get("sender_raw")
            or "autogen_runtime_caller"
        )
        decoded_messages = self.codec.decode_many(result)
        state_refs = self._safe_kernel_call(
            "write_autogen_core_response_real_rewrite_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=native_content,
                    metadata={
                        "framework": "autogen",
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_result_type": "core_response_real_rewrite_output",
                        "core_response_real_rewrite": True,
                        "core_response_native_type": native_type,
                        "core_response_field": field_name,
                        "core_response_receiver": receiver,
                        "core_receive_metadata": context.transport_metadata,
                        "autogen_decoded_messages": [
                            message.to_dict() for message in decoded_messages
                        ],
                    },
                ),
            ),
        )
        state_refs = list(state_refs or [])
        state_ref_payload = []
        for state_ref in state_refs:
            ref_payload = self._safe_kernel_call(
                "core_response_real_rewrite_state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)
        prompt_views = []
        for state_ref in state_refs:
            view = self._safe_kernel_call(
                "core_response_real_rewrite_prompt_view",
                lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                    ref,
                    receiver,
                ),
            )
            if isinstance(view, str) and view:
                prompt_views.append(view)
        prompt_view_text = "\n".join(prompt_views)
        envelope_json = self._safe_kernel_call(
            "autogen_build_core_response_real_rewrite_handoff",
            lambda: self.kernel.build_handoff(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                sender=sender,
                declared_receiver=receiver,
                summary=(
                    "AutoGen Core response text field moved to StatePool while "
                    "preserving the native Python response type"
                ),
                state_refs=state_refs,
                memory_refs=[],
            ),
        )
        envelope_payload = (
            _json_object_or_empty(envelope_json)
            if isinstance(envelope_json, str)
            else {}
        )
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        rewritten_content = _build_core_content_rewrite_content(
            wire_envelope=wire_envelope,
            prompt_view_text=prompt_view_text,
        )
        replacement_result = _clone_message_with_text_field(
            result,
            field_name=field_name,
            content=rewritten_content,
        )
        replacement_field = (
            _field_value(replacement_result, field_name)
            if replacement_result is not None
            else ""
        )
        semantic_checks = {
            "response_type_preserved": bool(
                replacement_result is not None
                and type(replacement_result) is type(result)
            ),
            "content_field_preserved": field_name in CORE_REWRITE_FIELDS,
            "content_replaced_only": replacement_field == rewritten_content,
            "state_ref_available": bool(state_refs),
            "schema_valid": _wire_envelope_schema_valid(wire_envelope),
            "prompt_view_available": bool(prompt_view_text.strip()),
        }
        if not state_refs:
            fallback_reasons.append("missing_state_refs")
        if not semantic_checks["schema_valid"]:
            fallback_reasons.append("schema_invalid")
        if not semantic_checks["prompt_view_available"]:
            fallback_reasons.append("prompt_view_missing")
        if replacement_result is None:
            fallback_reasons.append("core_response_clone_failed")
        if not semantic_checks["response_type_preserved"]:
            fallback_reasons.append("core_response_type_not_preserved")
        if not semantic_checks["content_replaced_only"]:
            fallback_reasons.append("core_response_field_not_replaced")
        native_tokens = _count_tokens(self.token_counter, native_content)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        if native_tokens <= rewritten_tokens:
            fallback_reasons.append("core_response_token_not_reduced")
        if fallback_reasons:
            self._record_core_response_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=fallback_reasons,
                native_type=native_type,
                field_name=field_name,
                native_text=native_content,
                rewritten_content=rewritten_content,
                state_refs=state_ref_payload,
                wire_envelope=wire_envelope,
                prompt_view_text=prompt_view_text,
                semantic_checks=semantic_checks,
            )
            return None

        self._record_core_response_rewrite_audit(
            context=context,
            applied=True,
            fallback_reasons=[],
            native_type=native_type,
            field_name=field_name,
            native_text=native_content,
            rewritten_content=rewritten_content,
            state_refs=state_ref_payload,
            wire_envelope=wire_envelope,
            prompt_view_text=prompt_view_text,
            semantic_checks=semantic_checks,
        )
        return replacement_result

    def _record_core_response_rewrite_audit(
        self,
        *,
        context: HookCallContext,
        applied: bool,
        fallback_reasons: list[str],
        native_type: str = "",
        field_name: str = "",
        native_text: str = "",
        rewritten_content: str = "",
        state_refs: list[dict[str, Any]] | None = None,
        wire_envelope: dict[str, Any] | None = None,
        prompt_view_text: str = "",
        semantic_checks: dict[str, Any] | None = None,
    ) -> None:
        native_tokens = _count_tokens(self.token_counter, native_text)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        fallback_buckets = _fallback_buckets(fallback_reasons)
        self.trace.write(
            "autogen_core_response_real_rewrite",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "transport_metadata": context.transport_metadata,
                "broadcast_mode": self.broadcast_mode,
                "core_content_rewrite_enabled": self.core_content_rewrite_enabled,
                "candidate_generated": bool(rewritten_content),
                "rewrite_attempt_count": 1,
                "rewrite_applied_count": 1 if applied else 0,
                "rewrite_fallback_count": 0 if applied else 1,
                "rewrite_applied": applied,
                "real_response_mutation": applied,
                "fallback_required": not applied,
                "fallback_reasons": sorted(set(fallback_reasons)),
                "fallback_buckets": fallback_buckets,
                "fallback_bucket_counts": _count_values(fallback_buckets),
                "native_response_type": native_type,
                "rewritten_field": field_name,
                "state_refs": state_refs or [],
                "state_ref_count": len(state_refs or []),
                "shadow_wire_envelope": wire_envelope or {},
                "schema_valid": _wire_envelope_schema_valid(wire_envelope or {}),
                "prompt_view_available": bool(prompt_view_text.strip()),
                "native_content_tokens": native_tokens,
                "rewritten_content_tokens": rewritten_tokens,
                "token_delta_native_minus_rewrite": native_tokens - rewritten_tokens,
                "rewritten_preview": _preview(rewritten_content),
                "semantic_checks": semantic_checks or {},
            },
        )

    def _hydrate_core_runtime_response_if_needed(
        self,
        context: HookCallContext,
        result: Any,
    ) -> Any | None:
        field_name, content = _core_message_text_field(result)
        if not field_name or CORE_REWRITE_MARKER not in content:
            return None
        native_type = type(result).__name__ if result is not None else ""
        fallback_reasons: list[str] = []
        if self.core_receiver_hydrate_mode == "off":
            fallback_reasons.append("core_hydration_disabled")

        wire_envelope = _extract_core_rewrite_wire_envelope(content)
        state_refs = _state_refs_from_wire_envelope(wire_envelope)
        receiver = str(
            context.transport_metadata.get("sender")
            or context.transport_metadata.get("sender_raw")
            or "autogen_runtime_caller"
        )
        prompt_views = []
        if self.core_receiver_hydrate_mode == "prompt-view":
            for state_ref in state_refs:
                view = self._safe_kernel_call(
                    "core_response_hydrate_prompt_view",
                    lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                        ref,
                        receiver,
                    ),
                )
                if isinstance(view, str) and view:
                    prompt_views.append(view)
        embedded_prompt_view = _extract_core_embedded_prompt_view(content)
        hydrated_content = "\n".join(prompt_views).strip() or embedded_prompt_view
        if not state_refs:
            fallback_reasons.append("core_hydration_state_ref_missing")
        if not hydrated_content.strip():
            fallback_reasons.append("core_hydration_prompt_view_missing")

        replacement_result = _clone_message_with_text_field(
            result,
            field_name=field_name,
            content=hydrated_content,
        )
        replacement_field = (
            _field_value(replacement_result, field_name)
            if replacement_result is not None
            else ""
        )
        semantic_checks = {
            "response_type_preserved": bool(
                replacement_result is not None
                and type(replacement_result) is type(result)
            ),
            "content_field_preserved": field_name in CORE_REWRITE_FIELDS,
            "content_replaced_with_prompt_view": replacement_field == hydrated_content,
            "state_ref_available": bool(state_refs),
            "prompt_view_available": bool(hydrated_content.strip()),
            "wire_marker_removed": CORE_REWRITE_MARKER not in str(replacement_field),
        }
        if replacement_result is None:
            fallback_reasons.append("core_hydration_clone_failed")
        if not semantic_checks["response_type_preserved"]:
            fallback_reasons.append("core_hydration_type_not_preserved")
        if not semantic_checks["content_replaced_with_prompt_view"]:
            fallback_reasons.append("core_hydration_field_not_replaced")
        state_ref_payload = [
            self.kernel.state_pool.ref_to_dict(ref) for ref in state_refs
        ]
        if fallback_reasons:
            self._record_core_response_hydration_audit(
                context=context,
                applied=False,
                fallback_reasons=fallback_reasons,
                native_type=native_type,
                field_name=field_name,
                wire_envelope=wire_envelope,
                state_refs=state_ref_payload,
                original_rewritten_content=content,
                hydrated_content=hydrated_content,
                semantic_checks=semantic_checks,
            )
            return None

        self._record_core_response_hydration_audit(
            context=context,
            applied=True,
            fallback_reasons=[],
            native_type=native_type,
            field_name=field_name,
            wire_envelope=wire_envelope,
            state_refs=state_ref_payload,
            original_rewritten_content=content,
            hydrated_content=hydrated_content,
            semantic_checks=semantic_checks,
        )
        return replacement_result

    def _record_core_response_hydration_audit(
        self,
        *,
        context: HookCallContext,
        applied: bool,
        fallback_reasons: list[str],
        native_type: str = "",
        field_name: str = "",
        wire_envelope: dict[str, Any] | None = None,
        state_refs: list[dict[str, Any]] | None = None,
        original_rewritten_content: str = "",
        hydrated_content: str = "",
        semantic_checks: dict[str, Any] | None = None,
    ) -> None:
        fallback_buckets = _fallback_buckets(fallback_reasons)
        self.trace.write(
            "autogen_core_response_hydration",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "core_receiver_hydrate_mode": self.core_receiver_hydrate_mode,
                "candidate_detected": bool(original_rewritten_content),
                "hydration_attempt_count": 1,
                "hydration_applied_count": 1 if applied else 0,
                "hydration_fallback_count": 0 if applied else 1,
                "hydration_applied": applied,
                "fallback_required": not applied,
                "fallback_reasons": sorted(set(fallback_reasons)),
                "fallback_buckets": fallback_buckets,
                "fallback_bucket_counts": _count_values(fallback_buckets),
                "native_response_type": native_type,
                "hydrated_field": field_name,
                "state_refs": state_refs or [],
                "state_ref_count": len(state_refs or []),
                "shadow_wire_envelope": wire_envelope or {},
                "prompt_view_available": bool(hydrated_content.strip()),
                "wire_marker_removed": CORE_REWRITE_MARKER not in hydrated_content,
                "original_rewritten_tokens": _count_tokens(
                    self.token_counter,
                    original_rewritten_content,
                ),
                "hydrated_content_tokens": _count_tokens(
                    self.token_counter,
                    hydrated_content,
                ),
                "hydrated_preview": _preview(hydrated_content),
                "semantic_checks": semantic_checks or {},
            },
        )

    def rewrite_call_arguments_if_safe(
        self,
        context: HookCallContext,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        if self.broadcast_mode != "real-rewrite":
            return args, kwargs
        if (
            context.target_kind == "agentchat_team"
            and context.method_name == "run_stream"
        ):
            rewritten = self._rewrite_team_task_if_safe(context, args, kwargs)
            return rewritten if rewritten is not None else (args, kwargs)
        if (
            context.target_kind == "core_runtime"
            and context.method_name in {"send_message", "publish_message"}
        ):
            rewritten = self._rewrite_core_message_if_safe(context, args, kwargs)
            return rewritten if rewritten is not None else (args, kwargs)
        if context.target_kind == "core_agent" and context.method_name == "on_message":
            rewritten = self._hydrate_core_agent_message_if_needed(
                context,
                args,
                kwargs,
            )
            return rewritten if rewritten is not None else (args, kwargs)
        if context.target_kind != "agentchat_agent":
            return args, kwargs
        if context.method_name not in {"on_messages", "on_messages_stream"}:
            return args, kwargs
        rewritten = self._rewrite_agent_text_messages(context, args, kwargs)
        return rewritten if rewritten is not None else (args, kwargs)

    def rewrite_call_result_if_safe(
        self,
        context: HookCallContext,
        result: Any,
    ) -> Any:
        if self.broadcast_mode != "real-rewrite":
            return result
        if (
            context.target_kind == "core_agent"
            and context.method_name == "on_message"
            and self._core_response_rewrite_active()
        ):
            rewritten = self._rewrite_core_response_if_safe(context, result)
            return rewritten if rewritten is not None else result
        if (
            context.target_kind == "core_runtime"
            and context.method_name == "send_message"
        ):
            hydrated = self._hydrate_core_runtime_response_if_needed(context, result)
            return hydrated if hydrated is not None else result
        return result

    def _rewrite_team_task_if_safe(
        self,
        context: HookCallContext,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        task_value, source = _extract_team_task_argument(args, kwargs)
        native_task_text = _extract_text(task_value) if task_value is not None else ""
        if not self.team_rewrite_enabled:
            self._record_team_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=[TEAM_REAL_REWRITE_DISABLED_REASON],
                native_text=native_task_text,
            )
            return None
        if task_value is None:
            self._record_team_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["missing_team_task_argument"],
            )
            return None
        if isinstance(task_value, str):
            pass
        elif isinstance(task_value, (list, tuple)):
            if not task_value:
                self._record_team_rewrite_audit(
                    context=context,
                    applied=False,
                    fallback_reasons=["empty_team_task_sequence"],
                )
                return None
            if not any(_is_simple_text_message(message) for message in task_value):
                self._record_team_rewrite_audit(
                    context=context,
                    applied=False,
                    fallback_reasons=["team_task_sequence_missing_text_message"],
                    native_text=native_task_text,
                )
                return None
        else:
            self._record_team_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["unsupported_team_task_type"],
                native_text=_extract_text(task_value),
            )
            return None
        native_text = native_task_text
        if "AGENTLITE_TEAM_REAL_REWRITE v1" in native_text:
            self._record_team_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["already_team_rewritten"],
                native_text=native_text,
            )
            return None
        if not native_text.strip():
            self._record_team_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["empty_team_task_payload"],
                native_text=native_text,
            )
            return None
        if not context.team_participants:
            self._record_team_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["missing_team_participants"],
                native_text=native_text,
            )
            return None

        decoded_messages = self.codec.decode_many(task_value)
        state_refs = self._safe_kernel_call(
            "write_autogen_team_real_rewrite_task_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=native_text,
                    metadata={
                        "framework": "autogen",
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_result_type": "team_real_rewrite_task",
                        "team_real_rewrite_task_state": True,
                        "team_participants": list(context.team_participants),
                        "autogen_decoded_messages": [
                            message.to_dict() for message in decoded_messages
                        ],
                    },
                ),
            ),
        )
        state_refs = list(state_refs or [])
        state_ref_payload = []
        for state_ref in state_refs:
            ref_payload = self._safe_kernel_call(
                "team_real_rewrite_state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)

        broadcast_plan = plan_shadow_broadcast(
            sender=context.agent.agent_id,
            target_kind=context.target_kind,
            method_name=context.method_name,
            decoded_messages=decoded_messages,
            native_text=native_text,
            participant_names=list(context.team_participants),
        )
        receiver_entries: list[dict[str, Any]] = []
        total_wire_tokens = 0
        total_prompt_view_tokens = 0
        total_retrieved_memory_tokens = 0
        for receiver_plan in broadcast_plan.receiver_plans:
            entry = self._build_receiver_broadcast_entry(
                context=context,
                receiver_plan=receiver_plan,
                state_refs=state_refs,
                state_ref_payload=state_ref_payload,
            )
            if not entry:
                continue
            receiver_entries.append(entry)
            total_wire_tokens += int(entry.get("shadow_wire_tokens", 0) or 0)
            total_prompt_view_tokens += int(entry.get("prompt_view_tokens", 0) or 0)
            total_retrieved_memory_tokens += int(
                entry.get("retrieved_memory_tokens", 0) or 0
            )

        fallback_reasons = _broadcast_fallback_reasons(
            expected_receivers=list(context.team_participants),
            receiver_entries=receiver_entries,
            native_tokens_per_receiver=_count_tokens(self.token_counter, native_text),
            total_wire_tokens=total_wire_tokens,
            total_prompt_view_tokens=(
                total_prompt_view_tokens + total_retrieved_memory_tokens
            ),
        )
        rewritten_content = _build_team_real_rewrite_content(
            receiver_entries=receiver_entries,
            state_refs=state_ref_payload,
        )
        native_tokens = _count_tokens(self.token_counter, native_text)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        if native_tokens <= rewritten_tokens:
            fallback_reasons.append("team_task_token_not_reduced")
        if fallback_reasons:
            self._record_team_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=fallback_reasons,
                native_text=native_text,
                rewritten_content=rewritten_content,
                state_refs=state_ref_payload,
                receiver_entries=receiver_entries,
                broadcast_plan=broadcast_plan.to_dict(),
            )
            return None

        rewritten_task = _clone_team_task_with_text_content(
            task_value,
            rewritten_content,
        )
        if rewritten_task is None:
            self._record_team_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["team_task_clone_failed"],
                native_text=native_text,
                rewritten_content=rewritten_content,
                state_refs=state_ref_payload,
                receiver_entries=receiver_entries,
                broadcast_plan=broadcast_plan.to_dict(),
            )
            return None
        display_text, display_source = _team_task_display_identity(task_value)
        if display_text:
            context.display_restore_enabled = True
            context.display_original_text = display_text
            context.display_original_source = display_source
        new_args, new_kwargs = _replace_team_task_argument(
            args,
            kwargs,
            source=source,
            replacement=rewritten_task,
        )
        self._record_team_rewrite_audit(
            context=context,
            applied=True,
            fallback_reasons=[],
            native_text=native_text,
            rewritten_content=rewritten_content,
            state_refs=state_ref_payload,
            receiver_entries=receiver_entries,
            broadcast_plan=broadcast_plan.to_dict(),
        )
        return new_args, new_kwargs

    def _rewrite_agent_text_messages(
        self,
        context: HookCallContext,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        messages, source = _extract_messages_argument(args, kwargs)
        if messages is None:
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["missing_messages_argument"],
            )
            return None
        if isinstance(messages, (str, bytes)) or not isinstance(messages, (list, tuple)):
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["messages_not_sequence"],
            )
            return None
        if not messages:
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["empty_messages"],
            )
            return None
        if not all(_is_simple_text_message(message) for message in messages):
            decoded_messages = self.codec.decode_many(messages)
            native_text = self.codec.render_text(decoded_messages) or _extract_text(
                messages
            )
            safety = _build_non_text_rewrite_safety(decoded_messages)
            typed_candidate = self._build_handoff_typed_rewrite_candidate(
                context=context,
                native_messages=messages,
                decoded_messages=decoded_messages,
                native_text=native_text,
            ) or self._build_tool_summary_typed_rewrite_candidate(
                context=context,
                native_messages=messages,
                decoded_messages=decoded_messages,
                native_text=native_text,
            )
            rewritten_content = ""
            state_ref_payload: list[dict[str, Any]] = []
            wire_envelope: dict[str, Any] = {}
            prompt_view_text = ""
            if typed_candidate:
                safety["typed_rewrite_candidate_generated"] = bool(
                    typed_candidate.get("candidate_generated")
                )
                safety["typed_rewrite_candidate_safe"] = bool(
                    typed_candidate.get("candidate_safe")
                )
                safety["typed_rewrite_mutation_supported"] = False
                safety["recommended_action"] = (
                    "keep_native_autogen_message_until_typed_rewrite_mutation_gate_exists"
                )
                safety["fallback_reasons"] = sorted(
                    set(safety.get("fallback_reasons", []))
                    | {_typed_candidate_dry_run_reason(typed_candidate)}
                )
                safety["fallback_buckets"] = _fallback_buckets(
                    safety.get("fallback_reasons", [])
                )
                rewritten_content = str(typed_candidate.get("candidate_content", ""))
                state_ref_payload = list(typed_candidate.get("state_refs", []) or [])
                wire_envelope = dict(typed_candidate.get("shadow_wire_envelope", {}) or {})
                prompt_view_text = str(typed_candidate.get("prompt_view_text", ""))
                if (
                    _typed_candidate_contract(typed_candidate)
                    == "autogen_handoff_typed_rewrite_candidate.v1"
                    and self._handoff_typed_candidate_can_mutate(typed_candidate)
                ):
                    if self.handoff_rewrite_enabled:
                        rewritten = self._replace_handoff_with_typed_candidate(
                            messages=messages,
                            source=source,
                            args=args,
                            kwargs=kwargs,
                            candidate=typed_candidate,
                        )
                        if rewritten is not None:
                            typed_candidate["mutation_applied"] = True
                            typed_candidate["mutation_gate"] = "env_enabled"
                            safety["safe_to_mutate"] = True
                            safety["native_preservation_satisfied"] = True
                            safety["typed_rewrite_mutation_supported"] = True
                            safety["fallback_reasons"] = []
                            safety["fallback_buckets"] = []
                            self._record_agent_rewrite_audit(
                                context=context,
                                applied=True,
                                fallback_reasons=[],
                                native_text=native_text,
                                rewritten_content=rewritten_content,
                                state_refs=state_ref_payload,
                                wire_envelope=wire_envelope,
                                prompt_view_text=prompt_view_text,
                                rewrite_safety=safety,
                                typed_rewrite_candidate=typed_candidate,
                            )
                            return rewritten
                    else:
                        safety["rewrite_gate_blocked_by_env"] = HANDOFF_REWRITE_ENV
                if (
                    _typed_candidate_contract(typed_candidate)
                    == "autogen_tool_summary_typed_rewrite_candidate.v1"
                    and self._tool_summary_typed_candidate_can_mutate(typed_candidate)
                ):
                    if self.tool_summary_rewrite_enabled:
                        rewritten = self._replace_tool_summary_with_typed_candidate(
                            messages=messages,
                            source=source,
                            args=args,
                            kwargs=kwargs,
                            candidate=typed_candidate,
                        )
                        if rewritten is not None:
                            typed_candidate["mutation_applied"] = True
                            typed_candidate["mutation_gate"] = "env_enabled"
                            safety["safe_to_mutate"] = True
                            safety["native_preservation_satisfied"] = True
                            safety["typed_rewrite_mutation_supported"] = True
                            safety["fallback_reasons"] = []
                            safety["fallback_buckets"] = []
                            self._record_agent_rewrite_audit(
                                context=context,
                                applied=True,
                                fallback_reasons=[],
                                native_text=native_text,
                                rewritten_content=rewritten_content,
                                state_refs=state_ref_payload,
                                wire_envelope=wire_envelope,
                                prompt_view_text=prompt_view_text,
                                rewrite_safety=safety,
                                typed_rewrite_candidate=typed_candidate,
                            )
                            return rewritten
                    else:
                        safety["rewrite_gate_blocked_by_env"] = (
                            TOOL_SUMMARY_REWRITE_ENV
                        )
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=list(safety["fallback_reasons"]),
                native_text=native_text,
                rewritten_content=rewritten_content,
                state_refs=state_ref_payload,
                wire_envelope=wire_envelope,
                prompt_view_text=prompt_view_text,
                rewrite_safety=safety,
                typed_rewrite_candidate=typed_candidate,
            )
            return None
        if not any(_text_message_content(message).strip() for message in messages):
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["empty_text_payload"],
            )
            return None

        decoded_messages = self.codec.decode_many(messages)
        native_text = self.codec.render_text(decoded_messages) or _extract_text(messages)
        if not native_text.strip():
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["empty_text_payload"],
            )
            return None
        if _contains_agentlite_rewrite_marker(native_text):
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["already_agentlite_rewritten"],
                native_text=native_text,
            )
            return None
        with self._memory_lock:
            current_team_task = self._current_team_task_by_group.get(
                context.task.group_id,
                "",
            )
            user_task_history = list(
                self._user_task_history_by_group.get(context.task.group_id, ())
            )
        chronology_view = _build_chronology_prompt_view(
            decoded_messages,
            current_task=current_team_task,
            user_task_history=user_task_history,
            final_delivery_marker=self.final_delivery_marker,
        )
        state_refs = self._safe_kernel_call(
            "write_autogen_real_rewrite_input_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=native_text,
                    metadata={
                        "framework": "autogen",
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_result_type": "agent_real_rewrite_input",
                        "real_rewrite_input_state": True,
                        "prompt_view_summary": chronology_view,
                        "autogen_decoded_messages": [
                            message.to_dict() for message in decoded_messages
                        ],
                    },
                ),
            ),
        )
        state_refs = list(state_refs or [])
        state_ref_payload = []
        for state_ref in state_refs:
            ref_payload = self._safe_kernel_call(
                "real_rewrite_state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)
        prompt_views = []
        for state_ref in state_refs:
            view = self._safe_kernel_call(
                "real_rewrite_prompt_view",
                lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                    ref,
                    context.agent.agent_id,
                    budget_chars=max(900, len(chronology_view) + 512),
                ),
            )
            if isinstance(view, str) and view:
                prompt_views.append(view)
        memory_selection = _select_nonredundant_memory_context(
            state_prompt_views=prompt_views,
            memory_refs=list(context.memory_context.refs),
            memory_prompt_views=list(context.memory_context.prompt_views),
        )
        memory_refs = memory_selection.refs
        memory_prompt_views = memory_selection.prompt_views
        prompt_view_text = _join_state_and_memory_views(
            prompt_views,
            memory_prompt_views,
        )
        envelope_json = self._safe_kernel_call(
            "autogen_build_real_rewrite_handoff",
            lambda: self.kernel.build_handoff(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                sender="autogen_team",
                declared_receiver=context.agent.agent_id,
                summary=(
                    "AutoGen incoming TextMessage payload moved to StatePool; "
                    "agent receives compact SHP view"
                ),
                state_refs=state_refs,
                memory_refs=memory_refs,
            ),
        )
        envelope_payload = (
            _json_object_or_empty(envelope_json)
            if isinstance(envelope_json, str)
            else {}
        )
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        wire_json = json.dumps(wire_envelope, ensure_ascii=False, separators=(",", ":"))
        fallback_reasons = []
        if not state_refs:
            fallback_reasons.append("missing_state_refs")
        if not _wire_envelope_schema_valid(wire_envelope):
            fallback_reasons.append("schema_invalid")
        if not prompt_view_text.strip():
            fallback_reasons.append("prompt_view_missing")

        rewritten_content = _build_real_rewrite_content(
            wire_envelope=wire_envelope,
            prompt_view_text=prompt_view_text,
        )
        rewritten_message = _clone_text_message_with_content(
            messages[-1],
            rewritten_content,
        )
        rewritten_messages = [rewritten_message]
        if rewritten_message is None:
            fallback_reasons.append("message_clone_failed")
        original_tokens = _count_tokens(self.token_counter, native_text)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        if original_tokens <= rewritten_tokens:
            fallback_reasons.append("token_not_reduced")
        if fallback_reasons:
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=fallback_reasons,
                native_text=native_text,
                rewritten_content=rewritten_content,
                state_refs=state_ref_payload,
                memory_refs=[_memory_ref_payload(ref) for ref in memory_refs],
                wire_envelope=wire_envelope,
                prompt_view_text=prompt_view_text,
                retrieved_memory_tokens=_count_tokens(
                    self.token_counter,
                    "\n".join(memory_prompt_views),
                ),
                memory_candidate_count=memory_selection.candidate_count,
                memory_deduplicated_count=memory_selection.deduplicated_count,
                memory_deduplicated_tokens=_count_tokens(
                    self.token_counter,
                    "\n".join(memory_selection.deduplicated_views),
                ),
            )
            return None

        replaced_messages = (
            tuple(rewritten_messages)
            if isinstance(messages, tuple)
            else list(rewritten_messages)
        )
        new_args, new_kwargs = _replace_messages_argument(
            args,
            kwargs,
            source=source,
            replacement=replaced_messages,
        )
        self._record_agent_rewrite_audit(
            context=context,
            applied=True,
            fallback_reasons=[],
            native_text=native_text,
            rewritten_content=rewritten_content,
            state_refs=state_ref_payload,
            memory_refs=[_memory_ref_payload(ref) for ref in memory_refs],
            wire_envelope=wire_envelope,
            prompt_view_text=prompt_view_text,
            retrieved_memory_tokens=_count_tokens(
                self.token_counter,
                "\n".join(memory_prompt_views),
            ),
            memory_candidate_count=memory_selection.candidate_count,
            memory_deduplicated_count=memory_selection.deduplicated_count,
            memory_deduplicated_tokens=_count_tokens(
                self.token_counter,
                "\n".join(memory_selection.deduplicated_views),
            ),
        )
        return new_args, new_kwargs

    def _build_handoff_typed_rewrite_candidate(
        self,
        *,
        context: HookCallContext,
        native_messages: Any,
        decoded_messages: list[Any],
        native_text: str,
    ) -> dict[str, Any]:
        handoff_messages = [
            message
            for message in decoded_messages
            if str(getattr(message, "message_kind", "") or "") == "handoff"
        ]
        if len(decoded_messages) != 1 or len(handoff_messages) != 1:
            return {}
        decoded = handoff_messages[0]
        receiver = str(getattr(decoded, "target", "") or "").strip()
        sender = str(getattr(decoded, "source", "") or "autogen_team").strip()
        if not receiver or not native_text.strip():
            return {}
        state_refs = self._safe_kernel_call(
            "write_autogen_handoff_typed_candidate_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=native_text,
                    metadata={
                        "framework": "autogen",
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_result_type": "handoff_typed_rewrite_candidate",
                        "typed_rewrite_candidate": True,
                        "autogen_decoded_messages": [
                            message.to_dict() for message in decoded_messages
                        ],
                    },
                ),
            ),
        )
        state_refs = list(state_refs or [])
        state_ref_payload = []
        for state_ref in state_refs:
            ref_payload = self._safe_kernel_call(
                "handoff_typed_candidate_state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)
        prompt_views = []
        for state_ref in state_refs:
            view = self._safe_kernel_call(
                "handoff_typed_candidate_prompt_view",
                lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                    ref,
                    receiver,
                ),
            )
            if isinstance(view, str) and view:
                prompt_views.append(view)
        prompt_view_text = "\n".join(prompt_views)
        envelope_json = self._safe_kernel_call(
            "autogen_build_handoff_typed_candidate",
            lambda: self.kernel.build_handoff(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                sender=sender,
                declared_receiver=receiver,
                summary=(
                    "AutoGen HandoffMessage content can be represented as "
                    "compact SHP wire plus receiver Prompt View"
                ),
                state_refs=state_refs,
                memory_refs=[],
            ),
        )
        envelope_payload = (
            _json_object_or_empty(envelope_json)
            if isinstance(envelope_json, str)
            else {}
        )
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        candidate_content = _build_handoff_typed_rewrite_content(
            wire_envelope=wire_envelope,
            prompt_view_text=prompt_view_text,
        )
        native_message = _first_message(native_messages)
        cloned_message = (
            _clone_message_with_content(native_message, candidate_content)
            if native_message is not None
            else None
        )
        cloned_decoded = self.codec.decode(cloned_message) if cloned_message else None
        native_raw = getattr(decoded, "raw", {}) or {}
        cloned_raw = getattr(cloned_decoded, "raw", {}) if cloned_decoded else {}
        context_count = _list_length(native_raw.get("context"))
        cloned_context_count = _list_length(cloned_raw.get("context"))
        native_tokens = _count_tokens(self.token_counter, native_text)
        candidate_tokens = _count_tokens(self.token_counter, candidate_content)
        semantic_checks = {
            "message_type_preserved": bool(
                cloned_decoded
                and getattr(cloned_decoded, "message_kind", "") == "handoff"
            ),
            "source_preserved": bool(
                cloned_decoded and getattr(cloned_decoded, "source", "") == sender
            ),
            "target_preserved": bool(
                cloned_decoded and getattr(cloned_decoded, "target", "") == receiver
            ),
            "id_preserved": _field_equal(native_raw, cloned_raw, "id"),
            "metadata_preserved": _field_equal(native_raw, cloned_raw, "metadata"),
            "context_preserved": context_count == cloned_context_count,
            "content_replaced_only": bool(
                cloned_decoded
                and getattr(cloned_decoded, "content_text", "") == candidate_content
            ),
            "state_ref_available": bool(state_refs),
            "schema_valid": _wire_envelope_schema_valid(wire_envelope),
            "prompt_view_available": bool(prompt_view_text.strip()),
            "token_reduced": native_tokens > candidate_tokens,
        }
        semantic_safe = all(
            bool(semantic_checks[key])
            for key in (
                "message_type_preserved",
                "source_preserved",
                "target_preserved",
                "id_preserved",
                "metadata_preserved",
                "context_preserved",
                "content_replaced_only",
                "state_ref_available",
                "schema_valid",
                "prompt_view_available",
            )
        )
        return {
            "contract": "autogen_handoff_typed_rewrite_candidate.v1",
            "candidate_generated": True,
            "candidate_safe": semantic_safe,
            "mutation_applied": False,
            "mutation_gate": "dry_run_only",
            "message_kind": "handoff",
            "native_type": getattr(decoded, "native_type", ""),
            "source": sender,
            "target": receiver,
            "native_id": str(native_raw.get("id", "") or ""),
            "metadata_keys": sorted(
                str(key) for key in (native_raw.get("metadata", {}) or {}).keys()
            )
            if isinstance(native_raw.get("metadata", {}), dict)
            else [],
            "context_count": context_count,
            "state_refs": state_ref_payload,
            "shadow_wire_envelope": wire_envelope,
            "prompt_view_text": prompt_view_text,
            "prompt_view_available": bool(prompt_view_text.strip()),
            "candidate_content": candidate_content,
            "candidate_preview": _preview(candidate_content),
            "native_input_tokens": native_tokens,
            "candidate_input_tokens": candidate_tokens,
            "token_delta_native_minus_candidate": native_tokens - candidate_tokens,
            "semantic_checks": semantic_checks,
        }

    def _build_tool_summary_typed_rewrite_candidate(
        self,
        *,
        context: HookCallContext,
        native_messages: Any,
        decoded_messages: list[Any],
        native_text: str,
    ) -> dict[str, Any]:
        tool_summary_messages = [
            message
            for message in decoded_messages
            if str(getattr(message, "message_kind", "") or "") == "tool_summary"
        ]
        if len(decoded_messages) != 1 or len(tool_summary_messages) != 1:
            return {}
        decoded = tool_summary_messages[0]
        receiver = context.agent.agent_id
        sender = str(getattr(decoded, "source", "") or "autogen_tool_runner").strip()
        if not receiver or not native_text.strip():
            return {}
        state_refs = self._safe_kernel_call(
            "write_autogen_tool_summary_typed_candidate_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=native_text,
                    metadata={
                        "framework": "autogen",
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_result_type": "tool_summary_typed_rewrite_candidate",
                        "typed_rewrite_candidate": True,
                        "tool_summary_typed_rewrite_candidate": True,
                        "autogen_decoded_messages": [
                            message.to_dict() for message in decoded_messages
                        ],
                    },
                ),
            ),
        )
        state_refs = list(state_refs or [])
        state_ref_payload = []
        for state_ref in state_refs:
            ref_payload = self._safe_kernel_call(
                "tool_summary_typed_candidate_state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)
        prompt_views = []
        for state_ref in state_refs:
            view = self._safe_kernel_call(
                "tool_summary_typed_candidate_prompt_view",
                lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                    ref,
                    receiver,
                ),
            )
            if isinstance(view, str) and view:
                prompt_views.append(view)
        prompt_view_text = "\n".join(prompt_views)
        envelope_json = self._safe_kernel_call(
            "autogen_build_tool_summary_typed_candidate",
            lambda: self.kernel.build_handoff(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                sender=sender,
                declared_receiver=receiver,
                summary=(
                    "AutoGen ToolCallSummaryMessage content can be represented "
                    "as compact SHP wire plus receiver Prompt View while keeping "
                    "tool call lineage native"
                ),
                state_refs=state_refs,
                memory_refs=[],
            ),
        )
        envelope_payload = (
            _json_object_or_empty(envelope_json)
            if isinstance(envelope_json, str)
            else {}
        )
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        candidate_content = _build_tool_summary_typed_rewrite_content(
            wire_envelope=wire_envelope,
            prompt_view_text=prompt_view_text,
        )
        native_message = _first_message(native_messages)
        cloned_message = (
            _clone_message_with_content(native_message, candidate_content)
            if native_message is not None
            else None
        )
        cloned_decoded = self.codec.decode(cloned_message) if cloned_message else None
        native_raw = getattr(decoded, "raw", {}) or {}
        cloned_raw = getattr(cloned_decoded, "raw", {}) if cloned_decoded else {}
        native_tool_calls = list(getattr(decoded, "tool_calls", []) or [])
        native_tool_results = list(getattr(decoded, "tool_results", []) or [])
        cloned_tool_calls = (
            list(getattr(cloned_decoded, "tool_calls", []) or [])
            if cloned_decoded
            else []
        )
        cloned_tool_results = (
            list(getattr(cloned_decoded, "tool_results", []) or [])
            if cloned_decoded
            else []
        )
        native_call_ids = _tool_call_ids(native_tool_calls)
        cloned_call_ids = _tool_call_ids(cloned_tool_calls)
        native_result_call_ids = _tool_result_call_ids(native_tool_results)
        cloned_result_call_ids = _tool_result_call_ids(cloned_tool_results)
        native_tokens = _count_tokens(self.token_counter, native_text)
        candidate_tokens = _count_tokens(self.token_counter, candidate_content)
        semantic_checks = {
            "message_type_preserved": bool(
                cloned_decoded
                and getattr(cloned_decoded, "message_kind", "") == "tool_summary"
            ),
            "source_preserved": bool(
                cloned_decoded and getattr(cloned_decoded, "source", "") == sender
            ),
            "id_preserved": _field_equal(native_raw, cloned_raw, "id"),
            "metadata_preserved": _field_equal(native_raw, cloned_raw, "metadata"),
            "tool_calls_preserved": native_tool_calls == cloned_tool_calls,
            "tool_results_preserved": native_tool_results == cloned_tool_results,
            "tool_call_ids_preserved": native_call_ids == cloned_call_ids,
            "tool_result_call_ids_preserved": (
                native_result_call_ids == cloned_result_call_ids
            ),
            "tool_result_lineage_complete": _tool_result_lineage_complete(
                native_tool_calls,
                native_tool_results,
            ),
            "content_replaced_only": bool(
                cloned_decoded
                and getattr(cloned_decoded, "content_text", "") == candidate_content
            ),
            "state_ref_available": bool(state_refs),
            "schema_valid": _wire_envelope_schema_valid(wire_envelope),
            "prompt_view_available": bool(prompt_view_text.strip()),
            "token_reduced": native_tokens > candidate_tokens,
        }
        semantic_safe = all(
            bool(semantic_checks[key])
            for key in (
                "message_type_preserved",
                "source_preserved",
                "id_preserved",
                "metadata_preserved",
                "tool_calls_preserved",
                "tool_results_preserved",
                "tool_call_ids_preserved",
                "tool_result_call_ids_preserved",
                "tool_result_lineage_complete",
                "content_replaced_only",
                "state_ref_available",
                "schema_valid",
                "prompt_view_available",
            )
        )
        return {
            "contract": "autogen_tool_summary_typed_rewrite_candidate.v1",
            "candidate_generated": True,
            "candidate_safe": semantic_safe,
            "mutation_applied": False,
            "mutation_gate": "dry_run_only",
            "message_kind": "tool_summary",
            "native_type": getattr(decoded, "native_type", ""),
            "source": sender,
            "target": receiver,
            "native_id": str(native_raw.get("id", "") or ""),
            "metadata_keys": sorted(
                str(key) for key in (native_raw.get("metadata", {}) or {}).keys()
            )
            if isinstance(native_raw.get("metadata", {}), dict)
            else [],
            "context_count": 0,
            "tool_call_ids": native_call_ids,
            "tool_call_names": _tool_call_names(native_tool_calls),
            "tool_result_call_ids": native_result_call_ids,
            "tool_result_names": _tool_result_names(native_tool_results),
            "tool_result_error_flags": _tool_result_error_flags(native_tool_results),
            "state_refs": state_ref_payload,
            "shadow_wire_envelope": wire_envelope,
            "prompt_view_text": prompt_view_text,
            "prompt_view_available": bool(prompt_view_text.strip()),
            "candidate_content": candidate_content,
            "candidate_preview": _preview(candidate_content),
            "native_input_tokens": native_tokens,
            "candidate_input_tokens": candidate_tokens,
            "token_delta_native_minus_candidate": native_tokens - candidate_tokens,
            "semantic_checks": semantic_checks,
        }

    @staticmethod
    def _handoff_typed_candidate_can_mutate(candidate: dict[str, Any]) -> bool:
        if not candidate.get("candidate_generated"):
            return False
        if not candidate.get("candidate_safe"):
            return False
        checks = candidate.get("semantic_checks", {})
        if not isinstance(checks, dict):
            return False
        required = (
            "message_type_preserved",
            "source_preserved",
            "target_preserved",
            "id_preserved",
            "metadata_preserved",
            "context_preserved",
            "content_replaced_only",
            "state_ref_available",
            "schema_valid",
            "prompt_view_available",
            "token_reduced",
        )
        return all(bool(checks.get(key)) for key in required)

    @staticmethod
    def _tool_summary_typed_candidate_can_mutate(candidate: dict[str, Any]) -> bool:
        if not candidate.get("candidate_generated"):
            return False
        if not candidate.get("candidate_safe"):
            return False
        checks = candidate.get("semantic_checks", {})
        if not isinstance(checks, dict):
            return False
        required = (
            "message_type_preserved",
            "source_preserved",
            "id_preserved",
            "metadata_preserved",
            "tool_calls_preserved",
            "tool_results_preserved",
            "tool_call_ids_preserved",
            "tool_result_call_ids_preserved",
            "tool_result_lineage_complete",
            "content_replaced_only",
            "state_ref_available",
            "schema_valid",
            "prompt_view_available",
            "token_reduced",
        )
        return all(bool(checks.get(key)) for key in required)

    def _replace_handoff_with_typed_candidate(
        self,
        *,
        messages: Any,
        source: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        candidate: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        if not isinstance(messages, (list, tuple)) or len(messages) != 1:
            return None
        content = str(candidate.get("candidate_content", ""))
        if not content:
            return None
        replacement_message = _clone_message_with_content(messages[0], content)
        if replacement_message is None:
            return None
        replacement = (
            (replacement_message,) if isinstance(messages, tuple) else [replacement_message]
        )
        return _replace_messages_argument(
            args,
            kwargs,
            source=source,
            replacement=replacement,
        )

    def _replace_tool_summary_with_typed_candidate(
        self,
        *,
        messages: Any,
        source: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        candidate: dict[str, Any],
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        if not isinstance(messages, (list, tuple)) or len(messages) != 1:
            return None
        content = str(candidate.get("candidate_content", ""))
        if not content:
            return None
        replacement_message = _clone_message_with_content(messages[0], content)
        if replacement_message is None:
            return None
        replacement = (
            (replacement_message,) if isinstance(messages, tuple) else [replacement_message]
        )
        return _replace_messages_argument(
            args,
            kwargs,
            source=source,
            replacement=replacement,
        )

    def _record_agent_rewrite_audit(
        self,
        *,
        context: HookCallContext,
        applied: bool,
        fallback_reasons: list[str],
        native_text: str = "",
        rewritten_content: str = "",
        state_refs: list[dict[str, Any]] | None = None,
        memory_refs: list[dict[str, Any]] | None = None,
        wire_envelope: dict[str, Any] | None = None,
        prompt_view_text: str = "",
        retrieved_memory_tokens: int = 0,
        memory_candidate_count: int = 0,
        memory_deduplicated_count: int = 0,
        memory_deduplicated_tokens: int = 0,
        rewrite_safety: dict[str, Any] | None = None,
        typed_rewrite_candidate: dict[str, Any] | None = None,
    ) -> None:
        native_tokens = _count_tokens(self.token_counter, native_text)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        total_prompt_view_tokens = _count_tokens(
            self.token_counter,
            prompt_view_text,
        )
        rewritten_wire_tokens = max(
            0,
            rewritten_tokens - total_prompt_view_tokens,
        )
        fallback_buckets = _fallback_buckets(fallback_reasons)
        self.trace.write(
            "autogen_agent_input_real_rewrite",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "broadcast_mode": self.broadcast_mode,
                "candidate_generated": bool(rewritten_content),
                "rewrite_attempt_count": 1,
                "rewrite_applied_count": 1 if applied else 0,
                "rewrite_fallback_count": 0 if applied else 1,
                "rewrite_applied": applied,
                "real_message_mutation": applied,
                "fallback_required": not applied,
                "fallback_reasons": sorted(set(fallback_reasons)),
                "fallback_buckets": fallback_buckets,
                "fallback_bucket_counts": _count_values(fallback_buckets),
                "state_refs": state_refs or [],
                "memory_refs": memory_refs or [],
                "memory_injected_count": (
                    _unique_memory_ref_count(memory_refs or []) if applied else 0
                ),
                "memory_candidate_count": memory_candidate_count,
                "memory_retained_count": len(memory_refs or []),
                "memory_candidate_deduplicated_count": memory_deduplicated_count,
                "memory_candidate_deduplicated_tokens": memory_deduplicated_tokens,
                "memory_deduplication_mode": "fact_overlap_rules_v1",
                "shadow_wire_envelope": wire_envelope or {},
                "prompt_view_available": bool(prompt_view_text.strip()),
                "prompt_view_tokens": max(
                    0,
                    total_prompt_view_tokens - retrieved_memory_tokens,
                ),
                "retrieved_memory_tokens": retrieved_memory_tokens,
                "native_input_tokens": native_tokens,
                "rewritten_input_tokens": rewritten_tokens,
                "rewritten_wire_tokens": rewritten_wire_tokens,
                "token_delta_native_minus_rewrite": native_tokens - rewritten_tokens,
                "rewritten_preview": _preview(rewritten_content),
                "rewrite_safety": rewrite_safety or {},
                "typed_rewrite_candidate": _candidate_audit_view(
                    typed_rewrite_candidate or {}
                ),
            },
        )

    def _record_team_rewrite_audit(
        self,
        *,
        context: HookCallContext,
        applied: bool,
        fallback_reasons: list[str],
        native_text: str = "",
        rewritten_content: str = "",
        state_refs: list[dict[str, Any]] | None = None,
        receiver_entries: list[dict[str, Any]] | None = None,
        broadcast_plan: dict[str, Any] | None = None,
    ) -> None:
        native_tokens = _count_tokens(self.token_counter, native_text)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        receiver_entries = list(receiver_entries or [])
        native_full_broadcast_tokens = native_tokens * len(receiver_entries)
        retrieved_memory_tokens = sum(
            int(entry.get("retrieved_memory_tokens", 0) or 0)
            for entry in receiver_entries
        )
        memory_candidate_count = max(
            (
                int(entry.get("memory_candidate_count", 0) or 0)
                for entry in receiver_entries
            ),
            default=0,
        )
        memory_retained_count = max(
            (
                int(entry.get("memory_retained_count", 0) or 0)
                for entry in receiver_entries
            ),
            default=0,
        )
        memory_deduplicated_count = max(
            (
                int(
                    entry.get("memory_candidate_deduplicated_count", 0) or 0
                )
                for entry in receiver_entries
            ),
            default=0,
        )
        memory_deduplicated_fanout_count = sum(
            int(entry.get("memory_candidate_deduplicated_count", 0) or 0)
            for entry in receiver_entries
        )
        memory_deduplicated_tokens = sum(
            int(entry.get("memory_candidate_deduplicated_tokens", 0) or 0)
            for entry in receiver_entries
        )
        wire_plus_prompt_view_tokens = sum(
            int(entry.get("shadow_wire_tokens", 0) or 0)
            + int(entry.get("prompt_view_tokens", 0) or 0)
            + int(entry.get("retrieved_memory_tokens", 0) or 0)
            for entry in receiver_entries
        )
        fallback_buckets = _fallback_buckets(fallback_reasons)
        self.trace.write(
            "autogen_team_input_real_rewrite",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "broadcast_mode": self.broadcast_mode,
                "team_rewrite_enabled": self.team_rewrite_enabled,
                "team_participants": list(context.team_participants),
                "candidate_generated": bool(rewritten_content),
                "rewrite_attempt_count": 1,
                "rewrite_applied_count": 1 if applied else 0,
                "rewrite_fallback_count": 0 if applied else 1,
                "rewrite_applied": applied,
                "real_message_mutation": applied,
                "fallback_required": not applied,
                "fallback_reasons": sorted(set(fallback_reasons)),
                "fallback_buckets": fallback_buckets,
                "fallback_bucket_counts": _count_values(fallback_buckets),
                "state_refs": state_refs or [],
                "memory_refs": [
                    ref
                    for entry in receiver_entries
                    for ref in (entry.get("memory_refs", []) or [])
                ],
                "memory_injected_count": (
                    _unique_memory_ref_count(
                        [
                            ref
                            for entry in receiver_entries
                            for ref in (entry.get("memory_refs", []) or [])
                        ]
                    )
                    if applied
                    else 0
                ),
                "memory_candidate_count": memory_candidate_count,
                "memory_retained_count": memory_retained_count,
                "memory_candidate_deduplicated_count": memory_deduplicated_count,
                "memory_candidate_deduplicated_fanout_count": (
                    memory_deduplicated_fanout_count
                ),
                "memory_candidate_deduplicated_tokens": (
                    memory_deduplicated_tokens
                ),
                "memory_deduplication_mode": "fact_overlap_rules_v1",
                "retrieved_memory_tokens": retrieved_memory_tokens,
                "receiver_count": len(receiver_entries),
                "receiver_plans": _team_rewrite_receiver_audit(receiver_entries),
                "broadcast_plan": broadcast_plan or {},
                "native_task_tokens": native_tokens,
                "rewritten_task_tokens": rewritten_tokens,
                "token_delta_native_task_minus_rewrite": (
                    native_tokens - rewritten_tokens
                ),
                "native_full_broadcast_tokens": native_full_broadcast_tokens,
                "wire_plus_prompt_view_tokens": wire_plus_prompt_view_tokens,
                "token_delta_native_broadcast_minus_rewrite": (
                    native_full_broadcast_tokens - wire_plus_prompt_view_tokens
                ),
                "rewritten_preview": _preview(rewritten_content),
            },
        )

    def _record_team_broadcast_input_state(
        self,
        *,
        context: HookCallContext,
        decoded_messages: list[Any],
        native_text: str,
    ) -> None:
        if context.target_kind != "agentchat_team":
            return
        if context.method_name != "run_stream":
            return
        if not context.team_participants:
            return
        if not _has_semantic_payload(decoded_messages, native_text):
            return
        state_refs = self._safe_kernel_call(
            "write_autogen_team_input_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=native_text,
                    metadata={
                        "framework": "autogen",
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_result_type": "team_broadcast_input",
                        "team_broadcast_input_state": True,
                        "team_participants": list(context.team_participants),
                        "autogen_decoded_messages": [
                            message.to_dict() for message in decoded_messages
                        ],
                    },
                ),
            ),
        )
        state_ref_payload = []
        for state_ref in state_refs or []:
            ref_payload = self._safe_kernel_call(
                "team_input_state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)
        self.trace.write(
            "autogen_team_input_state",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "team_participants": list(context.team_participants),
                "input_chars": len(native_text),
                "input_preview": _preview(native_text),
                "state_refs": state_ref_payload,
            },
        )
        self._record_shadow_broadcast_replacement(
            context=context,
            decoded_messages=decoded_messages,
            native_text=native_text,
            state_refs=list(state_refs or []),
            state_ref_payload=state_ref_payload,
            native_scope="team_input",
        )

    def _record_shadow_broadcast_replacement(
        self,
        *,
        context: HookCallContext,
        decoded_messages: list[Any],
        native_text: str,
        state_refs: list[Any],
        state_ref_payload: list[dict[str, Any]],
        native_scope: str,
    ) -> None:
        if context.target_kind != "agentchat_team":
            return
        if context.method_name != "run_stream":
            return
        if not state_refs or not context.team_participants:
            fallback_reasons = []
            if not state_refs:
                fallback_reasons.append("missing_state_refs")
            if not context.team_participants:
                fallback_reasons.append("missing_team_participants")
            self._write_broadcast_shadow_event(
                context=context,
                native_scope=native_scope,
                broadcast_plan={},
                state_ref_payload=state_ref_payload,
                receiver_entries=[],
                native_text=native_text,
                fallback_reasons=fallback_reasons,
            )
            return
        broadcast_plan = plan_shadow_broadcast(
            sender=context.agent.agent_id,
            target_kind=context.target_kind,
            method_name=context.method_name,
            decoded_messages=decoded_messages,
            native_text=native_text,
            participant_names=list(context.team_participants),
        )
        receiver_entries: list[dict[str, Any]] = []
        total_wire_tokens = 0
        total_prompt_view_tokens = 0
        total_retrieved_memory_tokens = 0
        include_memory = False
        for receiver_plan in broadcast_plan.receiver_plans:
            entry = self._build_receiver_broadcast_entry(
                context=context,
                receiver_plan=receiver_plan,
                state_refs=state_refs,
                state_ref_payload=state_ref_payload,
                include_memory=include_memory,
            )
            if not entry:
                continue
            receiver_entries.append(entry)
            total_wire_tokens += int(entry.get("shadow_wire_tokens", 0) or 0)
            total_prompt_view_tokens += int(entry.get("prompt_view_tokens", 0) or 0)
            total_retrieved_memory_tokens += int(
                entry.get("retrieved_memory_tokens", 0) or 0
            )
        fallback_reasons = _broadcast_fallback_reasons(
            expected_receivers=list(context.team_participants),
            receiver_entries=receiver_entries,
            native_tokens_per_receiver=_count_tokens(self.token_counter, native_text),
            total_wire_tokens=total_wire_tokens,
            total_prompt_view_tokens=(
                total_prompt_view_tokens + total_retrieved_memory_tokens
            ),
        )
        self._write_broadcast_shadow_event(
            context=context,
            native_scope=native_scope,
            broadcast_plan=broadcast_plan.to_dict(),
            state_ref_payload=state_ref_payload,
            receiver_entries=receiver_entries,
            native_text=native_text,
            fallback_reasons=fallback_reasons,
        )

    def _write_broadcast_shadow_event(
        self,
        *,
        context: HookCallContext,
        native_scope: str,
        broadcast_plan: dict[str, Any],
        state_ref_payload: list[dict[str, Any]],
        receiver_entries: list[dict[str, Any]],
        native_text: str,
        fallback_reasons: list[str],
    ) -> None:
        receiver_count = len(receiver_entries)
        native_tokens_per_receiver = _count_tokens(self.token_counter, native_text)
        total_wire_tokens = sum(
            int(entry.get("shadow_wire_tokens", 0) or 0)
            for entry in receiver_entries
        )
        total_prompt_view_tokens = sum(
            int(entry.get("prompt_view_tokens", 0) or 0)
            for entry in receiver_entries
        )
        total_retrieved_memory_tokens = sum(
            int(entry.get("retrieved_memory_tokens", 0) or 0)
            for entry in receiver_entries
        )
        native_full_broadcast_tokens = native_tokens_per_receiver * receiver_count
        wire_plus_prompt_view_tokens = (
            total_wire_tokens
            + total_prompt_view_tokens
            + total_retrieved_memory_tokens
        )
        delta = native_full_broadcast_tokens - wire_plus_prompt_view_tokens
        reduction_ratio = (
            delta / native_full_broadcast_tokens
            if native_full_broadcast_tokens > 0
            else 0.0
        )
        dry_run = _build_broadcast_dry_run_diff(
            mode=self.broadcast_mode,
            team_rewrite_enabled=self.team_rewrite_enabled,
            expected_receivers=list(context.team_participants),
            receiver_entries=receiver_entries,
            native_tokens_per_receiver=native_tokens_per_receiver,
            native_full_broadcast_tokens=native_full_broadcast_tokens,
            shadow_wire_tokens=total_wire_tokens,
            prompt_view_tokens=total_prompt_view_tokens,
            wire_plus_prompt_view_tokens=wire_plus_prompt_view_tokens,
            fallback_reasons=fallback_reasons,
        )
        self.trace.write(
            "autogen_broadcast_replacement_shadow",
            {
                "call_id": context.call_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "native_scope": native_scope,
                "broadcast_mode": self.broadcast_mode,
                "plan": broadcast_plan,
                "team_participants": list(context.team_participants),
                "state_refs": state_ref_payload,
                "memory_refs": [
                    ref
                    for entry in receiver_entries
                    for ref in (entry.get("memory_refs", []) or [])
                ],
                "receiver_count": receiver_count,
                "receiver_plans": receiver_entries,
                "native_tokens_per_receiver": native_tokens_per_receiver,
                "native_full_broadcast_tokens": native_full_broadcast_tokens,
                "shadow_wire_tokens": total_wire_tokens,
                "prompt_view_tokens": total_prompt_view_tokens,
                "retrieved_memory_tokens": total_retrieved_memory_tokens,
                "wire_plus_prompt_view_tokens": wire_plus_prompt_view_tokens,
                "token_delta_native_broadcast_minus_shadow": delta,
                "token_reduction_ratio": round(reduction_ratio, 6),
                "cost_scope": (
                    "team_native_text_tokens_per_receiver_vs_per_receiver_"
                    "compact_shadow_wire_plus_prompt_view"
                ),
                "rewrite_dry_run": dry_run,
            },
        )

    def _build_receiver_broadcast_entry(
        self,
        *,
        context: HookCallContext,
        receiver_plan: AutoGenShadowHandoffPlan,
        state_refs: list[Any],
        state_ref_payload: list[dict[str, Any]],
        include_memory: bool = True,
    ) -> dict[str, Any]:
        state_prompt_views = []
        for state_ref in state_refs:
            view = self._safe_kernel_call(
                "autogen_broadcast_prompt_view",
                lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                    ref,
                    receiver_plan.declared_receiver,
                ),
            )
            if isinstance(view, str) and view:
                state_prompt_views.append(view)
        memory_selection = _select_nonredundant_memory_context(
            state_prompt_views=state_prompt_views,
            memory_refs=(
                list(context.memory_context.refs) if include_memory else []
            ),
            memory_prompt_views=(
                list(context.memory_context.prompt_views) if include_memory else []
            ),
        )
        memory_refs = memory_selection.refs
        memory_prompt_views = memory_selection.prompt_views
        envelope_json = self._safe_kernel_call(
            "autogen_build_broadcast_shadow_handoff",
            lambda: self.kernel.build_handoff(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                sender=context.agent.agent_id,
                declared_receiver=receiver_plan.declared_receiver,
                summary=receiver_plan.summary,
                state_refs=state_refs,
                memory_refs=memory_refs,
            ),
        )
        if not isinstance(envelope_json, str) or not envelope_json:
            return {}
        envelope_payload = _json_object_or_empty(envelope_json)
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        wire_envelope_json = json.dumps(
            wire_envelope, ensure_ascii=False, separators=(",", ":")
        )
        prompt_view_text = _join_state_and_memory_views(
            state_prompt_views,
            memory_prompt_views,
        )
        schema_valid = _wire_envelope_schema_valid(wire_envelope)
        return {
            "receiver": receiver_plan.declared_receiver,
            "receiver_source": receiver_plan.receiver_source,
            "message_kinds": receiver_plan.message_kinds,
            "summary": receiver_plan.summary,
            "state_refs": state_ref_payload,
            "memory_refs": [
                _memory_ref_payload(ref) for ref in memory_refs
            ],
            "memory_candidate_count": memory_selection.candidate_count,
            "memory_retained_count": len(memory_refs),
            "memory_candidate_deduplicated_count": (
                memory_selection.deduplicated_count
            ),
            "memory_candidate_deduplicated_tokens": _count_tokens(
                self.token_counter,
                "\n".join(memory_selection.deduplicated_views),
            ),
            "memory_deduplication_mode": "fact_overlap_rules_v1",
            "shadow_wire_envelope": wire_envelope,
            "schema_valid": schema_valid,
            "shadow_wire_tokens": _count_tokens(
                self.token_counter,
                wire_envelope_json,
            ),
            "prompt_view_tokens": _count_tokens(
                self.token_counter,
                "\n".join(state_prompt_views),
            ),
            "retrieved_memory_tokens": _count_tokens(
                self.token_counter,
                "\n".join(memory_prompt_views),
            ),
            "prompt_view_available": bool(prompt_view_text.strip()),
            "prompt_view_text": prompt_view_text,
            "state_prompt_view_text": "\n".join(state_prompt_views),
            "memory_prompt_view_text": "\n".join(memory_prompt_views),
            "prompt_view_preview": _preview(prompt_view_text),
        }

    def record_call_error(
        self, context: HookCallContext | None, error: BaseException
    ) -> None:
        self.trace.write(
            "autogen_hooked_call_error",
            {
                "call_id": context.call_id if context else "",
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )

    def record_model_client_usage(
        self,
        *,
        instance: object,
        method_name: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        result: Any,
        latency_ms: float,
    ) -> None:
        call_id = self._next_call_id()
        usage, usage_available = _extract_model_usage(result)
        output_text = _extract_text(result)
        agent_id = _model_client_agent_id(instance)
        self.metrics.record_llm_call(
            task_id=call_id,
            round_id=1,
            mode="runtime_lite",
            usage=usage,
            latency_ms=latency_ms,
            agent_id=agent_id,
            output_chars=len(output_text),
        )
        self.trace.write(
            "autogen_model_client_usage",
            {
                "call_id": call_id,
                "agent_id": agent_id,
                "role": "model_client",
                "target_kind": "model_client",
                "method": method_name,
                "client_class": (
                    f"{type(instance).__module__}.{type(instance).__name__}"
                ),
                "model": _model_client_model_name(instance, kwargs),
                "cached": _extract_bool(result, "cached"),
                "usage_available": usage_available,
                "usage": usage,
                "llm_prompt_tokens": usage["prompt_tokens"],
                "llm_completion_tokens": usage["completion_tokens"],
                "llm_total_tokens": usage["total_tokens"],
                "llm_wall_time_ms": round(latency_ms, 3),
                "output_chars": len(output_text),
                "output_preview": _preview(output_text),
                "request_preview": _preview(_extract_text({"args": args, "kwargs": kwargs})),
            },
        )

    def record_model_client_error(
        self,
        *,
        instance: object,
        method_name: str,
        error: BaseException,
        latency_ms: float,
    ) -> None:
        self.trace.write(
            "autogen_model_client_error",
            {
                "call_id": self._next_call_id(),
                "agent_id": _model_client_agent_id(instance),
                "role": "model_client",
                "target_kind": "model_client",
                "method": method_name,
                "client_class": (
                    f"{type(instance).__module__}.{type(instance).__name__}"
                ),
                "model": _model_client_model_name(instance, {}),
                "error_type": type(error).__name__,
                "error": str(error),
                "llm_wall_time_ms": round(latency_ms, 3),
            },
        )

    def describe_agent(self, instance: object, *, target_kind: str) -> AgentDescriptor:
        raw_name = (
            getattr(instance, "name", None)
            or getattr(instance, "_name", None)
            or getattr(instance, "id", None)
            or getattr(instance, "_id", None)
            or type(instance).__name__
        )
        agent_id = _safe_identifier(str(raw_name))
        role = "team" if target_kind == "agentchat_team" else type(instance).__name__
        capabilities = _capabilities_for(target_kind)
        return AgentDescriptor(
            agent_id=agent_id,
            role=role,
            capabilities=capabilities,
            framework_metadata={
                "framework": "autogen",
                "native_class": f"{type(instance).__module__}.{type(instance).__name__}",
                "target_kind": target_kind,
            },
        )

    def describe_team_participants(
        self,
        instance: object,
        *,
        target_kind: str,
    ) -> tuple[str, ...]:
        if target_kind != "agentchat_team":
            return ()
        participant_names = _string_sequence(
            getattr(instance, "_participant_names", None)
        )
        if not participant_names:
            participant_names = _string_sequence(
                getattr(instance, "participant_names", None)
            )
        if not participant_names:
            participants = getattr(instance, "_participants", None)
            participant_names = [
                _safe_identifier(
                    str(
                        getattr(participant, "name", None)
                        or getattr(participant, "_name", None)
                        or getattr(participant, "id", None)
                        or getattr(participant, "_id", None)
                        or type(participant).__name__
                    )
                )
                for participant in participants or []
            ]
        return tuple(_unique_strings(participant_names))

    def _patch_class(
        self,
        cls: type,
        *,
        module_name: str,
        target_methods: dict[str, tuple[str, ...]],
    ) -> int:
        target_kind = self._target_kind_for(module_name, cls)
        if target_kind is None:
            return 0
        patched_count = 0
        for method_name in target_methods.get(target_kind, ()):
            original = getattr(cls, method_name, None)
            if original is None or getattr(original, "__agentlite_wrapped__", False):
                continue
            if not callable(original):
                continue
            wrapped = self._wrap_method(
                original,
                method_name=method_name,
                target_kind=target_kind,
            )
            setattr(cls, method_name, wrapped)
            self._patched_methods.add(
                f"{module_name}.{cls.__name__}.{method_name}"
            )
            patched_count += 1
        return patched_count

    def _wrap_method(
        self,
        original: Any,
        *,
        method_name: str,
        target_kind: str,
    ) -> Any:
        if target_kind == "model_client":
            return self._wrap_model_client_method(
                original,
                method_name=method_name,
            )

        if inspect.isasyncgenfunction(original):

            @functools.wraps(original)
            async def asyncgen_wrapper(instance: object, *args: Any, **kwargs: Any):
                context: HookCallContext | None = None
                last_item: Any = None
                try:
                    context = self.record_call_start(
                        instance=instance,
                        method_name=method_name,
                        target_kind=target_kind,
                        args=args,
                        kwargs=kwargs,
                    )
                    args, kwargs = self.rewrite_call_arguments_if_safe(
                        context,
                        args,
                        kwargs,
                    )
                    async for item in original(instance, *args, **kwargs):
                        last_item = item
                        self.record_stream_item(context, item)
                        yield self.restore_call_result_for_display(context, item)
                    self.record_call_end(context, last_item)
                except Exception as exc:
                    self.record_call_error(context, exc)
                    raise

            setattr(asyncgen_wrapper, "__agentlite_wrapped__", True)
            return asyncgen_wrapper

        if inspect.iscoroutinefunction(original):

            @functools.wraps(original)
            async def coroutine_wrapper(
                instance: object, *args: Any, **kwargs: Any
            ) -> Any:
                context: HookCallContext | None = None
                send_message_entered = False
                response_rewrite_enabled = False
                try:
                    context = self.record_call_start(
                        instance=instance,
                        method_name=method_name,
                        target_kind=target_kind,
                        args=args,
                        kwargs=kwargs,
                    )
                    args, kwargs = self.rewrite_call_arguments_if_safe(
                        context,
                        args,
                        kwargs,
                    )
                    if (
                        context.target_kind == "core_runtime"
                        and context.method_name == "send_message"
                    ):
                        response_rewrite_enabled = bool(
                            context.transport_metadata.get("sender")
                            or context.transport_metadata.get("sender_raw")
                        )
                        self._enter_core_send_message(
                            response_rewrite_enabled=response_rewrite_enabled,
                        )
                        send_message_entered = True
                    result = await original(instance, *args, **kwargs)
                    if send_message_entered:
                        self._exit_core_send_message(
                            response_rewrite_enabled=response_rewrite_enabled,
                        )
                        send_message_entered = False
                    result = self.rewrite_call_result_if_safe(context, result)
                    self.record_call_end(context, result)
                    return self.restore_call_result_for_display(context, result)
                except Exception as exc:
                    if send_message_entered:
                        self._exit_core_send_message(
                            response_rewrite_enabled=response_rewrite_enabled,
                        )
                    self.record_call_error(context, exc)
                    raise

            setattr(coroutine_wrapper, "__agentlite_wrapped__", True)
            return coroutine_wrapper

        if inspect.isgeneratorfunction(original):

            @functools.wraps(original)
            def generator_wrapper(instance: object, *args: Any, **kwargs: Any):
                context: HookCallContext | None = None
                last_item: Any = None
                try:
                    context = self.record_call_start(
                        instance=instance,
                        method_name=method_name,
                        target_kind=target_kind,
                        args=args,
                        kwargs=kwargs,
                    )
                    args, kwargs = self.rewrite_call_arguments_if_safe(
                        context,
                        args,
                        kwargs,
                    )
                    for item in original(instance, *args, **kwargs):
                        last_item = item
                        self.record_stream_item(context, item)
                        yield self.restore_call_result_for_display(context, item)
                    self.record_call_end(context, last_item)
                except Exception as exc:
                    self.record_call_error(context, exc)
                    raise

            setattr(generator_wrapper, "__agentlite_wrapped__", True)
            return generator_wrapper

        @functools.wraps(original)
        def sync_wrapper(instance: object, *args: Any, **kwargs: Any) -> Any:
            context: HookCallContext | None = None
            try:
                context = self.record_call_start(
                    instance=instance,
                    method_name=method_name,
                    target_kind=target_kind,
                    args=args,
                    kwargs=kwargs,
                )
                args, kwargs = self.rewrite_call_arguments_if_safe(
                    context,
                    args,
                    kwargs,
                )
                result = original(instance, *args, **kwargs)
                self.record_call_end(context, result)
                return self.restore_call_result_for_display(context, result)
            except Exception as exc:
                self.record_call_error(context, exc)
                raise

        setattr(sync_wrapper, "__agentlite_wrapped__", True)
        return sync_wrapper

    def _wrap_model_client_method(
        self,
        original: Any,
        *,
        method_name: str,
    ) -> Any:
        if inspect.isasyncgenfunction(original):

            @functools.wraps(original)
            async def asyncgen_wrapper(instance: object, *args: Any, **kwargs: Any):
                started = time.perf_counter()
                last_item: Any = None
                try:
                    async for item in original(instance, *args, **kwargs):
                        last_item = item
                        yield item
                    self.record_model_client_usage(
                        instance=instance,
                        method_name=method_name,
                        args=args,
                        kwargs=kwargs,
                        result=last_item,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                    )
                except Exception as exc:
                    self.record_model_client_error(
                        instance=instance,
                        method_name=method_name,
                        error=exc,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                    )
                    raise

            setattr(asyncgen_wrapper, "__agentlite_wrapped__", True)
            return asyncgen_wrapper

        if inspect.iscoroutinefunction(original):

            @functools.wraps(original)
            async def coroutine_wrapper(
                instance: object, *args: Any, **kwargs: Any
            ) -> Any:
                started = time.perf_counter()
                try:
                    result = await original(instance, *args, **kwargs)
                    self.record_model_client_usage(
                        instance=instance,
                        method_name=method_name,
                        args=args,
                        kwargs=kwargs,
                        result=result,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                    )
                    return result
                except Exception as exc:
                    self.record_model_client_error(
                        instance=instance,
                        method_name=method_name,
                        error=exc,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                    )
                    raise

            setattr(coroutine_wrapper, "__agentlite_wrapped__", True)
            return coroutine_wrapper

        if inspect.isgeneratorfunction(original):

            @functools.wraps(original)
            def generator_wrapper(instance: object, *args: Any, **kwargs: Any):
                started = time.perf_counter()
                last_item: Any = None
                try:
                    for item in original(instance, *args, **kwargs):
                        last_item = item
                        yield item
                    self.record_model_client_usage(
                        instance=instance,
                        method_name=method_name,
                        args=args,
                        kwargs=kwargs,
                        result=last_item,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                    )
                except Exception as exc:
                    self.record_model_client_error(
                        instance=instance,
                        method_name=method_name,
                        error=exc,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                    )
                    raise

            setattr(generator_wrapper, "__agentlite_wrapped__", True)
            return generator_wrapper

        @functools.wraps(original)
        def sync_wrapper(instance: object, *args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                result = original(instance, *args, **kwargs)
                self.record_model_client_usage(
                    instance=instance,
                    method_name=method_name,
                    args=args,
                    kwargs=kwargs,
                    result=result,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                )
                return result
            except Exception as exc:
                self.record_model_client_error(
                    instance=instance,
                    method_name=method_name,
                    error=exc,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                )
                raise

        setattr(sync_wrapper, "__agentlite_wrapped__", True)
        return sync_wrapper

    @staticmethod
    def _target_methods_for(module_name: str) -> dict[str, tuple[str, ...]]:
        if module_name.startswith("autogen_ext.models") or module_name.startswith(
            "autogen_core.models"
        ):
            return {"model_client": PATCH_TARGETS["model_client"]}
        if module_name.startswith("autogen_agentchat.agents"):
            return {"agentchat_agent": PATCH_TARGETS["agentchat_agent"]}
        if module_name.startswith("autogen_agentchat.teams"):
            return {"agentchat_team": PATCH_TARGETS["agentchat_team"]}
        if module_name.startswith("autogen_core"):
            return {
                "core_runtime": PATCH_TARGETS["core_runtime"],
                "core_agent": PATCH_TARGETS["core_agent"],
            }
        if module_name == "autogen":
            return {
                "agentchat_agent": PATCH_TARGETS["agentchat_agent"],
                "agentchat_team": PATCH_TARGETS["agentchat_team"],
            }
        return {}

    @staticmethod
    def _target_kind_for(module_name: str, cls: type) -> str | None:
        class_name = cls.__name__
        if module_name.startswith("autogen_ext.models") or module_name.startswith(
            "autogen_core.models"
        ):
            if any(
                callable(getattr(cls, method_name, None))
                for method_name in PATCH_TARGETS["model_client"]
            ):
                return "model_client"
        if module_name.startswith("autogen_agentchat.agents"):
            return "agentchat_agent"
        if module_name.startswith("autogen_agentchat.teams"):
            return "agentchat_team"
        if module_name.startswith("autogen_core") and "Runtime" in class_name:
            return "core_runtime"
        if module_name.startswith("autogen_core") and class_name in {
            "BaseAgent",
            "RoutedAgent",
        }:
            return "core_agent"
        if module_name == "autogen":
            if "GroupChat" in class_name or "Team" in class_name:
                return "agentchat_team"
            if "Agent" in class_name:
                return "agentchat_agent"
        return None

    def _next_call_id(self) -> str:
        with self._lock:
            self._sequence += 1
            return f"autogen_{self.context.session_id}_{self._sequence:06d}"

    def _safe_kernel_call(self, operation: str, callback: Any) -> Any:
        try:
            return callback()
        except Exception as exc:
            self.trace.write(
                "autogen_kernel_bridge_error",
                {
                    "operation": operation,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None


class AutoGenImportFinder(importlib.abc.MetaPathFinder):
    def __init__(self, manager: AutoGenHookManager) -> None:
        self.manager = manager

    def find_spec(
        self,
        fullname: str,
        path: Iterable[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if not _is_supported_autogen_module(fullname):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.loader is None:
            return spec
        if isinstance(spec.loader, AutoGenLoader):
            return spec
        spec.loader = AutoGenLoader(spec.loader, self.manager)
        return spec


class AutoGenLoader(importlib.abc.Loader):
    def __init__(
        self,
        wrapped: importlib.abc.Loader,
        manager: AutoGenHookManager,
    ) -> None:
        self.wrapped = wrapped
        self.manager = manager

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        create_module = getattr(self.wrapped, "create_module", None)
        if create_module is None:
            return None
        return create_module(spec)

    def exec_module(self, module: ModuleType) -> None:
        self.wrapped.exec_module(module)
        self.manager.patch_module(module)


def activate(context: BootstrapContext) -> DriverActivation:
    manager = _activate_manager(context)
    os.environ["AGENTLITE_AUTOGEN_DRIVER_ACTIVE"] = "1"
    os.environ["AGENTLITE_ACTIVE_SESSION_ID"] = context.session_id
    available_modules = [
        module_name
        for module_name in SUPPORTED_MODULE_ROOTS
        if importlib.util.find_spec(module_name) is not None
    ]
    warnings: list[str] = []
    if not available_modules:
        warnings.append(
            "No supported AutoGen package was detected yet; the import hook "
            "will remain active and patch supported modules if the target app "
            "imports them later."
        )
    return DriverActivation(
        driver="autogen",
        status="active",
        hooks_active=True,
        framework_available=bool(available_modules),
        warnings=warnings,
        details={
            "phase": DRIVER_PHASE,
            "hook_mode": "managed_import_patch",
            "available_modules": available_modules,
            "supported_module_roots": list(SUPPORTED_MODULE_ROOTS),
            "patch_targets": PATCH_TARGETS,
            "broadcast_mode": manager.broadcast_mode,
            "broadcast_mode_env": BROADCAST_MODE_ENV,
            "team_rewrite_enabled": manager.team_rewrite_enabled,
            "team_rewrite_env": TEAM_REWRITE_ENV,
            "handoff_rewrite_enabled": manager.handoff_rewrite_enabled,
            "handoff_rewrite_env": HANDOFF_REWRITE_ENV,
            "tool_summary_rewrite_enabled": manager.tool_summary_rewrite_enabled,
            "tool_summary_rewrite_env": TOOL_SUMMARY_REWRITE_ENV,
            "core_content_rewrite_enabled": manager.core_content_rewrite_enabled,
            "core_content_rewrite_env": CORE_CONTENT_REWRITE_ENV,
            "core_receiver_hydrate_mode": manager.core_receiver_hydrate_mode,
            "core_receiver_hydrate_env": CORE_RECEIVER_HYDRATE_ENV,
            "shared_memory_enabled": manager.shared_memory_enabled,
            "shared_memory_env": SHARED_MEMORY_ENV,
            "memory_scope_id": manager.memory_scope_id,
            "memory_scope_env": MEMORY_SCOPE_ENV,
            "memory_store_dir": str(
                manager.kernel.memory_store.storage_dir or ""
            ),
            "patched_modules": manager.patched_modules,
            "patched_methods": manager.patched_methods,
            "trace_path": str(manager.trace.path),
            "state_dir": str(manager.kernel.state_pool.root_dir),
        },
    )


def _activate_manager(context: BootstrapContext) -> AutoGenHookManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = AutoGenHookManager(context)
            _MANAGER.install()
        return _MANAGER


def _is_supported_autogen_module(fullname: str) -> bool:
    return any(
        fullname == root or fullname.startswith(f"{root}.")
        for root in SUPPORTED_MODULE_ROOTS
    )


def _capabilities_for(target_kind: str) -> tuple[str, ...]:
    if target_kind == "agentchat_team":
        return ("team_orchestration", "message_broadcast")
    if target_kind == "core_runtime":
        return ("runtime_message_transport",)
    if target_kind == "core_agent":
        return ("runtime_message_receive", "prompt_view_hydration")
    return ("agent_message_handling", "artifact_generation")


def _resolve_broadcast_mode(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if not normalized:
        return "shadow-only"
    if normalized in BROADCAST_MODES:
        return normalized
    return "shadow-only"


def _resolve_core_receiver_hydrate_mode(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if not normalized:
        return "off"
    if normalized in {"1", "true", "yes", "on"}:
        return "prompt-view"
    if normalized in CORE_RECEIVER_HYDRATE_MODES:
        return normalized
    return "off"


def _truthy_env(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _fallback_buckets(reasons: Iterable[str]) -> list[str]:
    buckets = []
    for reason in reasons:
        key = str(reason)
        buckets.append(FALLBACK_REASON_BUCKETS.get(key, "unknown_fallback"))
    return sorted(set(buckets))


def _build_non_text_rewrite_safety(decoded_messages: list[Any]) -> dict[str, Any]:
    message_kinds = _unique_strings(
        str(getattr(message, "message_kind", "") or "")
        for message in decoded_messages
    )
    fallback_reasons: set[str] = {"non_text_message_present"}
    required_native_fields: set[str] = set()
    handoff_targets: set[str] = set()
    handoff_sources: set[str] = set()
    handoff_context_count = 0
    tool_call_ids: set[str] = set()
    tool_call_names: set[str] = set()
    tool_result_call_ids: set[str] = set()
    tool_result_names: set[str] = set()
    for message in decoded_messages:
        kind = str(getattr(message, "message_kind", "") or "")
        raw = getattr(message, "raw", {}) or {}
        if kind == "handoff":
            fallback_reasons.add("handoff_rewrite_requires_target_preservation")
            required_native_fields.update(("source", "target", "content"))
            target = str(getattr(message, "target", "") or "")
            source = str(getattr(message, "source", "") or "")
            if target:
                handoff_targets.add(target)
            if source:
                handoff_sources.add(source)
            context = raw.get("context") if isinstance(raw, dict) else None
            if isinstance(context, list) and context:
                fallback_reasons.add("handoff_rewrite_requires_context_preservation")
                required_native_fields.add("context")
                handoff_context_count += len(context)
        if kind in {"tool_call", "tool_summary"}:
            fallback_reasons.add("tool_rewrite_requires_call_lineage")
            required_native_fields.update(("tool_calls.id", "tool_calls.name"))
            for call in getattr(message, "tool_calls", []) or []:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id", "") or "")
                name = str(call.get("name", "") or "")
                if call_id:
                    tool_call_ids.add(call_id)
                if name:
                    tool_call_names.add(name)
        if kind in {"tool_result", "tool_summary"}:
            fallback_reasons.add("tool_rewrite_requires_result_lineage")
            required_native_fields.update(("results.call_id", "results.name", "results.is_error"))
            for result in getattr(message, "tool_results", []) or []:
                if not isinstance(result, dict):
                    continue
                call_id = str(result.get("call_id", "") or "")
                name = str(result.get("name", "") or "")
                if call_id:
                    tool_result_call_ids.add(call_id)
                if name:
                    tool_result_names.add(name)
    result_lineage_complete = (
        bool(tool_result_call_ids)
        and bool(tool_call_ids)
        and tool_result_call_ids <= tool_call_ids
    )
    if not tool_result_call_ids:
        result_lineage_complete = True
    return {
        "contract": "autogen_non_text_real_rewrite_guard.v1",
        "safe_to_mutate": False,
        "native_preservation_required": True,
        "recommended_action": "keep_native_autogen_message_until_typed_rewrite_contract_exists",
        "message_kinds": message_kinds,
        "fallback_reasons": sorted(fallback_reasons),
        "fallback_buckets": _fallback_buckets(fallback_reasons),
        "required_native_fields": sorted(required_native_fields),
        "handoff_targets": sorted(handoff_targets),
        "handoff_sources": sorted(handoff_sources),
        "handoff_context_count": handoff_context_count,
        "tool_call_ids": sorted(tool_call_ids),
        "tool_call_names": sorted(tool_call_names),
        "tool_result_call_ids": sorted(tool_result_call_ids),
        "tool_result_names": sorted(tool_result_names),
        "tool_result_lineage_complete": result_lineage_complete,
        "typed_rewrite_supported": False,
    }


def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _broadcast_fallback_reasons(
    *,
    expected_receivers: list[str],
    receiver_entries: list[dict[str, Any]],
    native_tokens_per_receiver: int,
    total_wire_tokens: int,
    total_prompt_view_tokens: int,
) -> list[str]:
    reasons: list[str] = []
    actual_receivers = {
        str(entry.get("receiver", ""))
        for entry in receiver_entries
        if entry.get("receiver")
    }
    expected = {str(receiver) for receiver in expected_receivers if receiver}
    missing_receivers = sorted(expected - actual_receivers)
    if missing_receivers:
        reasons.append("missing_receivers")
    if not receiver_entries:
        reasons.append("empty_receiver_plans")
    if any(not entry.get("state_refs") for entry in receiver_entries):
        reasons.append("missing_state_refs")
    if any(not entry.get("schema_valid") for entry in receiver_entries):
        reasons.append("schema_invalid")
    if any(not entry.get("prompt_view_available") for entry in receiver_entries):
        reasons.append("prompt_view_missing")
    native_full_tokens = native_tokens_per_receiver * len(receiver_entries)
    if native_full_tokens <= (total_wire_tokens + total_prompt_view_tokens):
        reasons.append("token_not_reduced")
    return reasons


def _build_broadcast_dry_run_diff(
    *,
    mode: str,
    team_rewrite_enabled: bool,
    expected_receivers: list[str],
    receiver_entries: list[dict[str, Any]],
    native_tokens_per_receiver: int,
    native_full_broadcast_tokens: int,
    shadow_wire_tokens: int,
    prompt_view_tokens: int,
    wire_plus_prompt_view_tokens: int,
    fallback_reasons: list[str],
) -> dict[str, Any]:
    actual_receivers = [
        str(entry.get("receiver", ""))
        for entry in receiver_entries
        if entry.get("receiver")
    ]
    missing_receivers = sorted(
        {receiver for receiver in expected_receivers if receiver}
        - set(actual_receivers)
    )
    prompt_view_missing = sorted(
        {
            str(entry.get("receiver", ""))
            for entry in receiver_entries
            if not entry.get("prompt_view_available")
        }
    )
    schema_invalid = sorted(
        {
            str(entry.get("receiver", ""))
            for entry in receiver_entries
            if not entry.get("schema_valid")
        }
    )
    mode_requires_rewrite_candidate = mode in {"dry-run-rewrite", "real-rewrite"}
    reasons = list(fallback_reasons)
    if mode == "real-rewrite" and not team_rewrite_enabled:
        reasons.append(TEAM_REAL_REWRITE_DISABLED_REASON)
    fallback_required = bool(reasons)
    if mode == "shadow-only":
        applied_action = "shadow_only_keep_native_autogen_broadcast"
    elif mode == "real-rewrite" and team_rewrite_enabled and not fallback_required:
        applied_action = "real_rewrite_team_task_broadcast"
    elif fallback_required:
        applied_action = "fallback_keep_native_autogen_broadcast"
    else:
        applied_action = "dry_run_only_keep_native_autogen_broadcast"
    return {
        "mode": mode,
        "candidate_generated": mode_requires_rewrite_candidate and bool(receiver_entries),
        "rewrite_safe": mode in {"dry-run-rewrite", "real-rewrite"}
        and not fallback_required,
        "real_rewrite_requested": mode == "real-rewrite",
        "team_rewrite_enabled": team_rewrite_enabled,
        "real_message_mutation": (
            mode == "real-rewrite" and team_rewrite_enabled and not fallback_required
        ),
        "applied_action": applied_action,
        "fallback_required": fallback_required,
        "fallback_reasons": sorted(set(reasons)),
        "expected_receivers": expected_receivers,
        "actual_receivers": actual_receivers,
        "missing_receivers": missing_receivers,
        "prompt_view_missing_receivers": prompt_view_missing,
        "schema_invalid_receivers": schema_invalid,
        "native_message_tokens_per_receiver": native_tokens_per_receiver,
        "native_full_broadcast_tokens": native_full_broadcast_tokens,
        "rewritten_wire_tokens": shadow_wire_tokens,
        "prompt_view_tokens": prompt_view_tokens,
        "wire_plus_prompt_view_tokens": wire_plus_prompt_view_tokens,
        "token_delta_native_broadcast_minus_rewrite": (
            native_full_broadcast_tokens - wire_plus_prompt_view_tokens
        ),
    }


def _extract_messages_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Any | None, str]:
    if args:
        return args[0], "args0"
    if "messages" in kwargs:
        return kwargs["messages"], "kw_messages"
    return None, "missing"


def _replace_messages_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    source: str,
    replacement: Any,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if source == "args0":
        return (replacement, *args[1:]), kwargs
    if source == "kw_messages":
        new_kwargs = dict(kwargs)
        new_kwargs["messages"] = replacement
        return args, new_kwargs
    return args, kwargs


def _extract_core_message_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Any | None, str]:
    if "message" in kwargs:
        return kwargs["message"], "kw_message"
    if args:
        return args[0], "args0"
    return None, "missing"


def _replace_core_message_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    source: str,
    replacement: Any,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if source == "kw_message":
        new_kwargs = dict(kwargs)
        new_kwargs["message"] = replacement
        return args, new_kwargs
    if source == "args0":
        return (replacement, *args[1:]), kwargs
    return args, kwargs


def _extract_team_task_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Any | None, str]:
    if "task" in kwargs:
        return kwargs["task"], "kw_task"
    if args:
        return args[0], "args0"
    return None, "missing"


def _replace_team_task_argument(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    source: str,
    replacement: Any,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if source == "kw_task":
        new_kwargs = dict(kwargs)
        new_kwargs["task"] = replacement
        return args, new_kwargs
    if source == "args0":
        return (replacement, *args[1:]), kwargs
    return args, kwargs


def _clone_team_task_with_text_content(task: Any, content: str) -> Any | None:
    if isinstance(task, str):
        return content
    if not isinstance(task, (list, tuple)):
        return None

    cloned_messages = []
    replaced = False
    for message in task:
        if not replaced and _is_simple_text_message(message):
            cloned = _clone_text_message_with_content(message, content)
            if cloned is None:
                return None
            cloned_messages.append(cloned)
            replaced = True
        else:
            cloned_messages.append(message)
    if not replaced:
        return None
    if isinstance(task, tuple):
        return tuple(cloned_messages)
    return cloned_messages


def _team_task_display_identity(task: Any) -> tuple[str, str]:
    if isinstance(task, str):
        return task, "user"
    if not isinstance(task, (list, tuple)):
        return "", "user"
    for message in task:
        if not _is_simple_text_message(message):
            continue
        source = str(_field_value(message, "source") or "user")
        return _text_message_content(message), source
    return "", "user"


def _restore_team_display_item(
    item: Any,
    *,
    original_text: str,
    original_source: str,
) -> tuple[Any, int]:
    if _is_internal_team_rewrite_message(
        item,
        original_source=original_source,
    ):
        restored = _clone_text_message_with_content(item, original_text)
        return (restored, 1) if restored is not None else (item, 0)

    messages = _field_value(item, "messages")
    if not isinstance(messages, (list, tuple)):
        return item, 0
    restored_messages = []
    restored_count = 0
    for message in messages:
        if _is_internal_team_rewrite_message(
            message,
            original_source=original_source,
        ):
            restored = _clone_text_message_with_content(message, original_text)
            if restored is not None:
                restored_messages.append(restored)
                restored_count += 1
                continue
        restored_messages.append(message)
    if not restored_count:
        return item, 0
    replacement = (
        tuple(restored_messages) if isinstance(messages, tuple) else restored_messages
    )
    restored_item = _clone_message_with_text_field(
        item,
        field_name="messages",
        content=replacement,
    )
    return (
        (restored_item, restored_count)
        if restored_item is not None
        else (item, 0)
    )


def _is_internal_team_rewrite_message(
    message: Any,
    *,
    original_source: str,
) -> bool:
    content = _field_value(message, "content")
    if not isinstance(content, str):
        return False
    source = str(_field_value(message, "source") or "")
    if source != original_source:
        return False
    return content.lstrip().startswith(
        "AGENTLITE_TEAM_REAL_REWRITE v1"
    )


def _is_simple_text_message(message: Any) -> bool:
    if isinstance(message, dict):
        message_type = str(message.get("type") or "")
        return message_type in {"TextMessage", ""} and isinstance(
            message.get("content"),
            str,
        )
    if type(message).__name__ != "TextMessage":
        return False
    return isinstance(getattr(message, "content", None), str)


def _text_message_content(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _clone_text_message_with_content(message: Any, content: str) -> Any | None:
    return _clone_message_with_content(message, content)


def _clone_message_with_content(message: Any, content: str) -> Any | None:
    return _clone_message_with_text_field(
        message,
        field_name="content",
        content=content,
    )


def _core_message_text_field(message: Any) -> tuple[str, str]:
    if message is None:
        return "", ""
    if isinstance(message, dict):
        for field_name in CORE_REWRITE_FIELDS:
            value = message.get(field_name)
            if isinstance(value, str):
                return field_name, value
        return "", ""
    for field_name in CORE_REWRITE_FIELDS:
        if not hasattr(message, field_name):
            continue
        try:
            value = getattr(message, field_name)
        except Exception:
            continue
        if isinstance(value, str):
            return field_name, value
    return "", ""


def _field_value(message: Any, field_name: str) -> Any:
    if message is None or not field_name:
        return None
    if isinstance(message, dict):
        return message.get(field_name)
    try:
        return getattr(message, field_name)
    except Exception:
        return None


def _clone_message_with_text_field(
    message: Any,
    *,
    field_name: str,
    content: Any,
) -> Any | None:
    if not field_name:
        return None
    if isinstance(message, dict):
        cloned = dict(message)
        cloned[field_name] = content
        return cloned
    model_copy = getattr(message, "model_copy", None)
    if callable(model_copy):
        try:
            return model_copy(update={field_name: content})
        except Exception:
            pass
    copy_method = getattr(message, "copy", None)
    if callable(copy_method):
        try:
            return copy_method(update={field_name: content})
        except Exception:
            pass
    if is_dataclass(message) and not isinstance(message, type):
        try:
            return dataclass_replace(message, **{field_name: content})
        except Exception:
            pass
    replace_method = getattr(message, "_replace", None)
    if callable(replace_method):
        try:
            return replace_method(**{field_name: content})
        except Exception:
            pass
    try:
        cloned = shallow_copy(message)
        setattr(cloned, field_name, content)
        return cloned
    except Exception:
        return None


def _extract_core_rewrite_wire_envelope(content: str) -> dict[str, Any]:
    for line in str(content or "").splitlines():
        if not line.startswith("shp_wire="):
            continue
        return _json_object_or_empty(line[len("shp_wire=") :])
    return {}


def _extract_core_embedded_prompt_view(content: str) -> str:
    marker = "\nprompt_view:\n"
    text = str(content or "")
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].strip()


def _state_refs_from_wire_envelope(wire_envelope: dict[str, Any]) -> list[StateRef]:
    raw_refs = wire_envelope.get("state_refs", [])
    if not isinstance(raw_refs, list):
        return []
    refs: list[StateRef] = []
    for raw_ref in raw_refs:
        if not isinstance(raw_ref, dict):
            continue
        state_id = str(raw_ref.get("state_id", "") or "")
        state_type = str(raw_ref.get("state_type", "") or "")
        if not state_id or not state_type:
            continue
        try:
            version = int(raw_ref.get("version", 1) or 1)
        except (TypeError, ValueError):
            version = 1
        refs.append(
            StateRef(
                state_id=state_id,
                state_type=state_type,
                version=version,
                payload_kind=str(raw_ref.get("payload_kind", "") or ""),
                contains_embedding_refs=bool(
                    raw_ref.get("contains_embedding_refs", False)
                ),
                usage_hint=str(raw_ref.get("usage_hint", "") or ""),
                tier=str(raw_ref.get("tier", "") or "cold"),
            )
        )
    return refs


def _first_message(messages: Any) -> Any | None:
    if isinstance(messages, (list, tuple)):
        return messages[0] if messages else None
    return messages


def _list_length(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _field_equal(left: dict[str, Any], right: dict[str, Any], key: str) -> bool:
    default = {} if key == "metadata" else None
    return left.get(key, default) == right.get(key, default)


def _candidate_audit_view(candidate: dict[str, Any]) -> dict[str, Any]:
    if not candidate:
        return {}
    compact = {
        key: value
        for key, value in candidate.items()
        if key not in {"candidate_content", "prompt_view_text"}
    }
    if "state_refs" in compact and isinstance(compact["state_refs"], list):
        compact["state_ref_count"] = len(compact["state_refs"])
    return compact


def _resolve_shared_memory_enabled(*, broadcast_mode: str, raw_value: str | None) -> bool:
    if broadcast_mode != "real-rewrite":
        return False
    if raw_value is None or not raw_value.strip():
        return True
    return _truthy_env(raw_value)


def _resolve_memory_scope_id(context: Any) -> str:
    explicit = os.getenv(MEMORY_SCOPE_ENV, "").strip()
    if explicit:
        return _safe_identifier(explicit)[:80]
    target_cwd = Path(context.target_cwd).expanduser().resolve()
    digest = hashlib.sha256(str(target_cwd).casefold().encode("utf-8")).hexdigest()[:12]
    label = _safe_identifier(target_cwd.name or "workspace")[:40]
    return f"{label}_{digest}"


def _resolve_collaboration_group_id(
    *,
    memory_scope_id: str,
    target_kind: str,
    agent_id: str,
    team_participants: tuple[str, ...],
) -> str:
    if target_kind == "agentchat_team":
        normalized = sorted(_safe_identifier(item) for item in team_participants)
        signature = "|".join(normalized) or "unnamed_team"
        digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
        return f"{memory_scope_id}.team_{digest}"
    if target_kind == "agentchat_agent":
        return f"{memory_scope_id}.agent_{_safe_identifier(agent_id)[:48]}"
    return memory_scope_id


def _memory_ref_payload(ref: Any) -> dict[str, Any]:
    return {
        "memory_id": str(getattr(ref, "memory_id", "") or ""),
        "version_id": int(getattr(ref, "version_id", 0) or 0),
        "status": str(getattr(ref, "status", "") or ""),
        "task_topic": str(getattr(ref, "task_topic", "") or ""),
        "memory_view_id": str(getattr(ref, "memory_view_id", "") or ""),
        "slot_id": str(getattr(ref, "slot_id", "") or ""),
    }


def _unique_memory_ref_count(refs: list[dict[str, Any]]) -> int:
    keys = {
        (
            str(ref.get("memory_id", "") or ""),
            int(ref.get("version_id", 0) or 0),
        )
        for ref in refs
        if isinstance(ref, dict) and ref.get("memory_id")
    }
    return len(keys)


def _memory_summary(messages: list[Any], fallback: str, *, limit: int) -> str:
    for message in reversed(messages):
        content = str(getattr(message, "content_text", "") or "").strip()
        source = str(getattr(message, "source", "") or "").lower()
        native_type = str(getattr(message, "native_type", "") or "")
        message_kind = str(getattr(message, "message_kind", "") or "")
        if not content or source == "user" or message_kind != "text":
            continue
        if native_type.endswith("Event") or native_type == "ThoughtEvent":
            continue
        return " ".join(content.split())[:limit]
    return " ".join(fallback.split())[:limit]


def _latest_visible_message(
    messages: list[Any],
    fallback: str,
) -> tuple[str, str]:
    for message in reversed(messages):
        content = str(getattr(message, "content_text", "") or "").strip()
        source = str(getattr(message, "source", "") or "").strip()
        native_type = str(getattr(message, "native_type", "") or "")
        message_kind = str(getattr(message, "message_kind", "") or "")
        if not content or source == "user" or message_kind != "text":
            continue
        if native_type.endswith("Event") or native_type == "ThoughtEvent":
            continue
        return content, source
    return fallback.strip(), ""


def _autogen_memory_slot_hint(prompt: str, summary: str) -> str:
    text = f"{prompt.lower()}\n{summary.lower()}"
    if _looks_like_final_task(text):
        return "final_deliverable"
    if any(term in text for term in ("review", "failure", "审查", "失败")):
        return "failure_reason"
    return "reuse_strategy"


def _looks_like_final_task(text: str) -> bool:
    lowered = text.lower()
    return bool(
        "最终" in lowered
        or "final deliverable" in lowered
        or "final answer" in lowered
    )


def _join_state_and_memory_views(
    state_prompt_views: list[str],
    memory_prompt_views: list[str],
) -> str:
    sections = [view for view in state_prompt_views if view.strip()]
    if memory_prompt_views:
        sections.extend(
            [
                SHARED_MEMORY_MARKER,
                "以下 MemoryView 是同一协作作用域中已通过规则准入的共享记忆，请在当前任务中复用并服从用户的新修订。",
                *[view for view in memory_prompt_views if view.strip()],
            ]
        )
    return "\n".join(sections)


def _select_nonredundant_memory_context(
    *,
    state_prompt_views: list[str],
    memory_refs: list[Any],
    memory_prompt_views: list[str],
) -> _MemoryContextSelection:
    selected_refs: list[Any] = []
    selected_views: list[str] = []
    deduplicated_views: list[str] = []
    comparison_text = "\n".join(
        view for view in state_prompt_views if str(view).strip()
    )
    for index, prompt_view in enumerate(memory_prompt_views):
        view = str(prompt_view or "").strip()
        if not view:
            continue
        if _memory_view_facts_covered(view, comparison_text):
            deduplicated_views.append(view)
            continue
        selected_views.append(view)
        if index < len(memory_refs):
            selected_refs.append(memory_refs[index])
        comparison_text = f"{comparison_text}\n{view}".strip()
    return _MemoryContextSelection(
        refs=selected_refs,
        prompt_views=selected_views,
        candidate_count=len([view for view in memory_prompt_views if str(view).strip()]),
        deduplicated_count=len(deduplicated_views),
        deduplicated_views=deduplicated_views,
    )


def _memory_view_facts_covered(memory_prompt_view: str, context_text: str) -> bool:
    if not context_text.strip():
        return False
    body = _memory_view_body(memory_prompt_view)
    body_normalized = _normalize_fact_text(body)
    context_normalized = _normalize_fact_text(context_text)
    if len(body_normalized) >= 12 and body_normalized in context_normalized:
        return True

    memory_units = _fact_units(body)
    context_units = _fact_units(context_text)
    if not memory_units or not context_units:
        return False
    covered_weight = 0
    total_weight = sum(len(unit) for unit in memory_units)
    for unit in memory_units:
        if any(_fact_unit_covered(unit, candidate) for candidate in context_units):
            covered_weight += len(unit)
    return total_weight >= 24 and (covered_weight / total_weight) >= 0.85


def _memory_view_body(prompt_view: str) -> str:
    body = re.sub(r"^\[memory_view:[^\]]+\]\s*", "", prompt_view.strip())
    body = re.sub(r"^slot=[^;]*;\s*", "", body)
    body = re.sub(r"^claim=[^;]*;\s*", "", body)
    body = re.sub(r";\s*tags=\[[^\]]*\]\s*$", "", body)
    return body.strip()


def _fact_units(text: str) -> list[str]:
    without_markdown = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    parts = re.split(r"(?:\r?\n)+|(?<=[。！？!?；;])", without_markdown)
    units = [_normalize_fact_text(part) for part in parts]
    return [unit for unit in units if len(unit) >= 8]


def _normalize_fact_text(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.casefold())


def _fact_unit_covered(memory_unit: str, context_unit: str) -> bool:
    if memory_unit in context_unit:
        return True
    if len(memory_unit) < 16 or len(context_unit) < 16:
        return False
    if _number_signature(memory_unit) != _number_signature(context_unit):
        return False
    memory_grams = _character_ngrams(memory_unit, size=3)
    context_grams = _character_ngrams(context_unit, size=3)
    if not memory_grams or not context_grams:
        return False
    forward = len(memory_grams & context_grams) / len(memory_grams)
    reverse = len(memory_grams & context_grams) / len(context_grams)
    return forward >= 0.92 and reverse >= 0.78


def _number_signature(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\d+(?:\.\d+)?", text))


def _character_ngrams(text: str, *, size: int) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _build_chronology_prompt_view(
    messages: list[Any],
    *,
    current_task: str = "",
    user_task_history: list[str] | tuple[str, ...] = (),
    final_delivery_marker: str = "",
) -> str:
    """Build a receiver view that preserves task and newest upstream semantics."""

    visible: list[tuple[str, str]] = []
    for message in messages:
        content = str(getattr(message, "content_text", "") or "").strip()
        source = str(getattr(message, "source", "") or "").strip() or "unknown"
        native_type = str(getattr(message, "native_type", "") or "")
        message_kind = str(getattr(message, "message_kind", "") or "")
        if not content or message_kind != "text":
            continue
        if native_type.endswith("Event") or native_type == "ThoughtEvent":
            continue
        visible.append(
            (
                source,
                _strip_exact_control_line(content, final_delivery_marker),
            )
        )

    latest_user = next(
        (content for source, content in reversed(visible) if source.lower() == "user"),
        "",
    )
    task_text = _strip_exact_control_line(
        latest_user or current_task,
        final_delivery_marker,
    ).strip()
    upstream = [
        (source, content)
        for source, content in visible
        if source.lower() != "user" and content.strip()
    ]

    sections: list[str] = []
    if task_text:
        sections.extend(
            [
                "CURRENT_USER_TASK (highest priority):",
                task_text,
            ]
        )
    prior_user_tasks = [
        _strip_exact_control_line(str(task), final_delivery_marker).strip()
        for task in user_task_history
        if str(task).strip() and str(task).strip() != task_text
    ]
    if prior_user_tasks:
        sections.extend(
            [
                "GROUNDING_RULE:",
                "Only USER_REQUEST_HISTORY can establish what the user explicitly confirmed. Agent assumptions must remain assumptions.",
                "USER_REQUEST_HISTORY (authoritative, oldest to newest):",
                *[
                    f"[{index}] {task}"
                    for index, task in enumerate(prior_user_tasks[-7:], start=1)
                ],
            ]
        )
    if upstream:
        latest_source, latest_content = upstream[-1]
        sections.extend(
            [
                f"LATEST_UPSTREAM_MESSAGE [{latest_source}] (use in full):",
                latest_content,
            ]
        )
    if len(upstream) > 1:
        previous_source, previous_content = upstream[-2]
        sections.extend(
            [
                f"PRIOR_UPSTREAM_DIGEST [{previous_source}]:",
                _head_tail_digest(previous_content, limit=360),
            ]
        )
    if sections:
        return "\n".join(sections)
    return ""


def _strip_exact_control_line(content: str, marker: str) -> str:
    if not marker.strip():
        return content.strip()
    return "\n".join(
        line for line in content.splitlines() if line.strip() != marker.strip()
    ).strip()


def _head_tail_digest(content: str, *, limit: int) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= limit:
        return normalized
    head = max(1, (limit - 5) // 2)
    tail = max(1, limit - head - 5)
    return f"{normalized[:head]} ... {normalized[-tail:]}"


def _build_real_rewrite_content(
    *,
    wire_envelope: dict[str, Any],
    prompt_view_text: str,
) -> str:
    wire_json = json.dumps(
        wire_envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "AGENTLITE_REAL_REWRITE v1\n"
        "native_payload_moved_to_state_pool=true\n"
        f"shp_wire={wire_json}\n"
        "prompt_view:\n"
        f"{prompt_view_text}"
    )


def _contains_agentlite_rewrite_marker(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "AGENTLITE_REAL_REWRITE v1",
            "AGENTLITE_TEAM_REAL_REWRITE v1",
            "AGENTLITE_HANDOFF_TYPED_REWRITE_CANDIDATE v1",
            "AGENTLITE_TOOL_SUMMARY_TYPED_REWRITE_CANDIDATE v1",
            CORE_REWRITE_MARKER,
        )
    )


def _build_core_content_rewrite_content(
    *,
    wire_envelope: dict[str, Any],
    prompt_view_text: str,
) -> str:
    wire_json = json.dumps(
        wire_envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"{CORE_REWRITE_MARKER}\n"
        "native_core_message_content_moved_to_state_pool=true\n"
        "native_python_message_type_preserved=true\n"
        f"shp_wire={wire_json}\n"
        "prompt_view:\n"
        f"{prompt_view_text}"
    )


def _build_handoff_typed_rewrite_content(
    *,
    wire_envelope: dict[str, Any],
    prompt_view_text: str,
) -> str:
    wire_json = json.dumps(
        wire_envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "AGENTLITE_HANDOFF_TYPED_REWRITE_CANDIDATE v1\n"
        "native_handoff_content_moved_to_state_pool=true\n"
        f"shp_wire={wire_json}\n"
        "prompt_view:\n"
        f"{prompt_view_text}"
    )


def _build_tool_summary_typed_rewrite_content(
    *,
    wire_envelope: dict[str, Any],
    prompt_view_text: str,
) -> str:
    wire_json = json.dumps(
        wire_envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "AGENTLITE_TOOL_SUMMARY_TYPED_REWRITE_CANDIDATE v1\n"
        "native_tool_summary_content_moved_to_state_pool=true\n"
        "tool_call_lineage_kept_native=true\n"
        f"shp_wire={wire_json}\n"
        "prompt_view:\n"
        f"{prompt_view_text}"
    )


def _build_team_real_rewrite_content(
    *,
    receiver_entries: list[dict[str, Any]],
    state_refs: list[dict[str, Any]],
) -> str:
    memory_refs = []
    seen_memory_ids: set[str] = set()
    memory_prompt_view_text = ""
    for entry in receiver_entries:
        for ref in entry.get("memory_refs", []) or []:
            memory_id = str(ref.get("memory_id", "")) if isinstance(ref, dict) else ""
            if memory_id and memory_id not in seen_memory_ids:
                seen_memory_ids.add(memory_id)
                memory_refs.append(ref)
        if not memory_prompt_view_text:
            memory_prompt_view_text = str(
                entry.get("memory_prompt_view_text", "") or ""
            )
    manifest = {
        "protocol": "agentlite.team_rewrite.v1",
        "receivers": [entry.get("receiver", "") for entry in receiver_entries],
        "state_refs": state_refs,
        "memory_refs": memory_refs,
        "receiver_wires": [
            {
                "receiver": entry.get("receiver", ""),
                "shp_wire": entry.get("shadow_wire_envelope", {}),
            }
            for entry in receiver_entries
        ],
    }
    sections = [
        "AGENTLITE_TEAM_REAL_REWRITE v1",
        "native_team_task_moved_to_state_pool=true",
        "broadcast_manifest="
        + json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        "receiver_prompt_views:",
    ]
    for entry in receiver_entries:
        receiver = str(entry.get("receiver", "") or "")
        prompt_view_text = str(
            entry.get("state_prompt_view_text")
            or entry.get("prompt_view_text", "")
            or ""
        )
        sections.extend(
            [
                f"--- receiver: {receiver}",
                prompt_view_text,
            ]
        )
    if memory_prompt_view_text:
        sections.extend(
            [
                "--- shared_memory",
                SHARED_MEMORY_MARKER,
                "以下 MemoryView 是同一协作作用域中已通过规则准入的共享记忆，请在当前任务中复用并服从用户的新修订。",
                memory_prompt_view_text,
            ]
        )
    return "\n".join(sections)


def _team_rewrite_receiver_audit(
    receiver_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for entry in receiver_entries:
        rows.append(
            {
                "receiver": entry.get("receiver", ""),
                "receiver_source": entry.get("receiver_source", ""),
                "message_kinds": entry.get("message_kinds", []),
                "schema_valid": bool(entry.get("schema_valid")),
                "prompt_view_available": bool(entry.get("prompt_view_available")),
                "shadow_wire_tokens": int(entry.get("shadow_wire_tokens", 0) or 0),
                "prompt_view_tokens": int(entry.get("prompt_view_tokens", 0) or 0),
                "retrieved_memory_tokens": int(
                    entry.get("retrieved_memory_tokens", 0) or 0
                ),
                "state_ref_count": len(entry.get("state_refs", []) or []),
                "memory_ref_count": len(entry.get("memory_refs", []) or []),
                "memory_refs": entry.get("memory_refs", []) or [],
                "memory_candidate_count": int(
                    entry.get("memory_candidate_count", 0) or 0
                ),
                "memory_retained_count": int(
                    entry.get("memory_retained_count", 0) or 0
                ),
                "memory_candidate_deduplicated_count": int(
                    entry.get("memory_candidate_deduplicated_count", 0) or 0
                ),
                "memory_candidate_deduplicated_tokens": int(
                    entry.get("memory_candidate_deduplicated_tokens", 0) or 0
                ),
                "prompt_view_preview": entry.get("prompt_view_preview", ""),
            }
        )
    return rows


def _typed_candidate_contract(candidate: dict[str, Any]) -> str:
    return str(candidate.get("contract", "") or "")


def _typed_candidate_dry_run_reason(candidate: dict[str, Any]) -> str:
    if _typed_candidate_contract(candidate) == (
        "autogen_tool_summary_typed_rewrite_candidate.v1"
    ):
        return "tool_summary_typed_rewrite_candidate_dry_run_only"
    return "handoff_typed_rewrite_candidate_dry_run_only"


def _tool_call_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    return sorted(str(call.get("id", "") or "") for call in tool_calls if call.get("id"))


def _tool_call_names(tool_calls: list[dict[str, Any]]) -> list[str]:
    return sorted(
        str(call.get("name", "") or "") for call in tool_calls if call.get("name")
    )


def _tool_result_call_ids(tool_results: list[dict[str, Any]]) -> list[str]:
    return sorted(
        str(result.get("call_id", "") or "")
        for result in tool_results
        if result.get("call_id")
    )


def _tool_result_names(tool_results: list[dict[str, Any]]) -> list[str]:
    return sorted(
        str(result.get("name", "") or "") for result in tool_results if result.get("name")
    )


def _tool_result_error_flags(tool_results: list[dict[str, Any]]) -> list[str]:
    return [
        f"{str(result.get('call_id', '') or '')}:{str(result.get('is_error'))}"
        for result in tool_results
        if result.get("call_id")
    ]


def _tool_result_lineage_complete(
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> bool:
    call_ids = set(_tool_call_ids(tool_calls))
    result_call_ids = set(_tool_result_call_ids(tool_results))
    return bool(call_ids) and bool(result_call_ids) and result_call_ids <= call_ids


def _core_transport_metadata(
    *,
    method_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    message = kwargs.get("message") if "message" in kwargs else _item_at(args, 0)
    route_value = None
    route_kind = "unknown"
    if method_name == "send_message":
        route_value = (
            kwargs.get("recipient") if "recipient" in kwargs else _item_at(args, 1)
        )
        route_kind = "direct"
    elif method_name == "publish_message":
        route_value = (
            kwargs.get("topic_id") if "topic_id" in kwargs else _item_at(args, 1)
        )
        route_kind = "publish"
    route_raw = _route_value_text(route_value)
    sender_raw = _route_value_text(kwargs.get("sender"))
    return {
        "transport_kind": route_kind,
        "method": method_name,
        "message_native_type": type(message).__name__ if message is not None else "",
        "route_raw": route_raw,
        "declared_receiver": _core_declared_receiver(
            method_name=method_name,
            route_raw=route_raw,
        ),
        "sender": _safe_identifier(sender_raw) if sender_raw else "",
        "sender_raw": sender_raw,
        "message_id": str(kwargs.get("message_id") or ""),
    }


def _core_agent_receive_metadata(
    *,
    method_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    message = kwargs.get("message") if "message" in kwargs else _item_at(args, 0)
    ctx = (
        kwargs.get("ctx")
        if "ctx" in kwargs
        else kwargs.get("context")
        if "context" in kwargs
        else _item_at(args, 1)
    )
    sender_value = getattr(ctx, "sender", None) if ctx is not None else None
    topic_value = getattr(ctx, "topic_id", None) if ctx is not None else None
    sender_raw = _route_value_text(sender_value)
    topic_raw = _route_value_text(topic_value)
    return {
        "transport_kind": "receive",
        "method": method_name,
        "message_native_type": type(message).__name__ if message is not None else "",
        "sender_raw": sender_raw,
        "sender": _safe_identifier(sender_raw) if sender_raw else "",
        "topic_raw": topic_raw,
        "topic": _safe_identifier(topic_raw) if topic_raw else "",
    }


def _item_at(values: tuple[Any, ...], index: int) -> Any:
    return values[index] if len(values) > index else None


def _route_value_text(value: Any) -> str:
    if value is None:
        return ""
    type_value = getattr(value, "type", None)
    key_value = getattr(value, "key", None)
    source_value = getattr(value, "source", None)
    if type_value is not None and key_value is not None:
        return f"{type_value}/{key_value}"
    if type_value is not None and source_value is not None:
        return f"{type_value}/{source_value}"
    if type_value is not None:
        return str(type_value)
    return str(value)


def _core_declared_receiver(*, method_name: str, route_raw: str) -> str:
    if route_raw:
        prefix = "topic" if method_name == "publish_message" else "agent"
        return _safe_identifier(f"{prefix}_{route_raw}")
    if method_name == "publish_message":
        return "topic_unknown"
    if method_name == "send_message":
        return "agent_unknown"
    return "autogen_runtime_peer"


def _build_core_transport_summary(
    *,
    sender: str,
    method_name: str,
    receiver: str,
    message_kinds: list[str],
    native_text: str,
    limit: int = 320,
) -> str:
    kinds = sorted({kind for kind in message_kinds if kind}) or ["unknown"]
    preview = " ".join(native_text.split())
    body = (
        f"{sender}.{method_name} -> {receiver} transported "
        f"{','.join(kinds)}"
    )
    if preview:
        body = f"{body}: {preview}"
    return body[:limit]


def _extract_text(value: Any, *, _depth: int = 0) -> str:
    if value is None:
        return ""
    if _depth > 4:
        return _preview(repr(value), limit=500)
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            item_text = _extract_text(item, _depth=_depth + 1)
            if item_text:
                parts.append(f"{key}: {item_text}")
        return "\n".join(parts)
    if isinstance(value, (list, tuple, set)):
        return "\n".join(
            item_text
            for item_text in (
                _extract_text(item, _depth=_depth + 1) for item in value
            )
            if item_text
        )
    for attr in ("content", "text", "message", "messages", "body", "source"):
        if hasattr(value, attr):
            try:
                item_text = _extract_text(
                    getattr(value, attr), _depth=_depth + 1
                )
            except Exception:
                continue
            if item_text:
                return item_text
    return _preview(repr(value), limit=1000)


def _has_semantic_payload(decoded_messages: list[Any], text: str) -> bool:
    if text.strip():
        return True
    for message in decoded_messages:
        if str(getattr(message, "target", "") or "").strip():
            return True
        if str(getattr(message, "content_text", "") or "").strip():
            return True
        if getattr(message, "tool_calls", None):
            return True
        if getattr(message, "tool_results", None):
            return True
    return False


def _safe_identifier(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)
    return cleaned.strip("_") or "autogen_agent"


def _string_sequence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [_safe_identifier(value)]
    if isinstance(value, (list, tuple, set)):
        return [_safe_identifier(str(item)) for item in value if str(item).strip()]
    return []


def _unique_strings(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = _safe_identifier(str(value))
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _preview(text: str, *, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def _count_tokens(token_counter: TokenCounter, text: str) -> int:
    if not text:
        return 0
    return int(token_counter.count(text).token_count)


def _extract_model_usage(value: Any) -> tuple[dict[str, int], bool]:
    for usage_key in ("usage", "_usage", "usage_metadata"):
        found, usage_value = _lookup_field(value, usage_key)
        if found:
            return _normalize_provider_usage(usage_value), True
    return _empty_provider_usage(), False


def _normalize_provider_usage(value: Any) -> dict[str, int]:
    if value is None:
        return _empty_provider_usage()
    prompt = _usage_int(
        value,
        (
            "prompt_tokens",
            "input_tokens",
            "prompt_token_count",
            "input_token_count",
        ),
    )
    completion = _usage_int(
        value,
        (
            "completion_tokens",
            "output_tokens",
            "completion_token_count",
            "candidates_token_count",
        ),
    )
    total = _usage_int(value, ("total_tokens", "total_token_count"))
    if total <= 0:
        total = prompt + completion
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _empty_provider_usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _usage_int(value: Any, names: tuple[str, ...]) -> int:
    for name in names:
        found, raw_value = _lookup_field(value, name)
        if found:
            return _safe_int(raw_value)
    return 0


def _lookup_field(value: Any, name: str) -> tuple[bool, Any]:
    if isinstance(value, dict):
        if name in value:
            return True, value.get(name)
        return False, None
    if value is not None and hasattr(value, name):
        return True, getattr(value, name)
    return False, None


def _extract_bool(value: Any, name: str) -> bool:
    found, raw_value = _lookup_field(value, name)
    return bool(raw_value) if found else False


def _model_client_agent_id(instance: object) -> str:
    label = (
        _model_client_model_name(instance, {})
        or getattr(instance, "name", None)
        or getattr(instance, "_name", None)
        or type(instance).__name__
    )
    return _safe_identifier(f"model_{label}")


def _model_client_model_name(instance: object, kwargs: dict[str, Any]) -> str:
    for source in (
        kwargs,
        getattr(instance, "_config", None),
        getattr(instance, "config", None),
        getattr(instance, "_client_config", None),
        instance,
    ):
        for name in (
            "model",
            "_model",
            "model_name",
            "_model_name",
            "deployment_name",
            "azure_deployment",
        ):
            found, value = _lookup_field(source, name)
            if found and value:
                return str(value)
    return ""


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _json_object_or_empty(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _wire_envelope_schema_valid(envelope: dict[str, Any]) -> bool:
    header = envelope.get("header", {})
    state_refs = envelope.get("state_refs", [])
    if not isinstance(header, dict):
        return False
    if header.get("protocol_version") != "shp.v1-lite":
        return False
    if not header.get("receiver"):
        return False
    return isinstance(state_refs, list) and bool(state_refs)


def _build_shadow_wire_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    header = envelope.get("header", {})
    control = envelope.get("control", {})
    metrics = envelope.get("metrics", {})
    state_refs = envelope.get("state_refs", [])
    memory_refs = envelope.get("memory_refs", [])
    wire_metrics = {
        "state_ref_count": len(state_refs) if isinstance(state_refs, list) else 0,
        "memory_ref_count": len(memory_refs) if isinstance(memory_refs, list) else 0,
    }
    if isinstance(metrics, dict):
        gate = metrics.get("communication_gate", {})
        if isinstance(gate, dict) and gate.get("status"):
            wire_metrics["gate_status"] = gate["status"]
    return {
        "header": _copy_keys(
            header,
            ("protocol_version", "task_id", "round_id", "sender", "receiver", "msg_type"),
        ),
        "control": _copy_keys(
            control,
            ("action", "readiness", "allowed_next_step", "access_policy"),
        ),
        "summary": str(envelope.get("summary", ""))[:160],
        "state_refs": state_refs if isinstance(state_refs, list) else [],
        "memory_refs": memory_refs if isinstance(memory_refs, list) else [],
        "metrics": wire_metrics,
    }


def _copy_keys(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if key in value}
