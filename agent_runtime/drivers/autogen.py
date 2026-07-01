from __future__ import annotations

import functools
import importlib.abc
import importlib.machinery
import importlib.util
import inspect
import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Iterable

from agent_runtime.core.kernel import AgentDescriptor, CollaborationKernel
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
from agent_runtime.state.state_pool import StatePoolLite

if TYPE_CHECKING:
    from agent_runtime.bootstrap.startup import BootstrapContext


SUPPORTED_MODULE_ROOTS = (
    "autogen_agentchat",
    "autogen_core",
    "autogen",
)
PATCH_TARGETS = {
    "agentchat_agent": ("on_messages", "on_messages_stream"),
    "agentchat_team": ("run", "run_stream"),
    "core_runtime": ("send_message", "publish_message"),
}
BROADCAST_MODE_ENV = "AGENTLITE_AUTOGEN_BROADCAST_MODE"
TEAM_REWRITE_ENV = "AGENTLITE_AUTOGEN_TEAM_REWRITE"
HANDOFF_REWRITE_ENV = "AGENTLITE_AUTOGEN_HANDOFF_REWRITE"
TOOL_SUMMARY_REWRITE_ENV = "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE"
BROADCAST_MODES = ("shadow-only", "dry-run-rewrite", "real-rewrite")
DRIVER_PHASE = "v5.12x"
TEAM_REAL_REWRITE_DISABLED_REASON = (
    "team_level_real_rewrite_not_enabled_for_guarded_agent_input"
)
FALLBACK_REASON_BUCKETS = {
    "missing_messages_argument": "input_contract_missing",
    "messages_not_sequence": "input_contract_invalid",
    "empty_messages": "input_contract_empty",
    "non_text_message_present": "unsupported_message_type",
    "empty_text_payload": "empty_payload",
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
    "already_team_rewritten": "team_task_already_rewritten",
    "empty_team_task_payload": "empty_payload",
    "missing_team_participants": "team_participant_missing",
    "team_task_token_not_reduced": "cost_gate_failed",
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
        self.kernel = CollaborationKernel(
            agents=[],
            token_counter=self.token_counter,
            metrics=self.metrics,
            trace=self.trace,
            state_pool=StatePoolLite(self.output_dir / "state"),
            memory_store=None,
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
            },
        )
        self._sequence = 0
        self._lock = threading.Lock()
        self._patched_methods: set[str] = set()
        self._patched_modules: set[str] = set()
        self._import_finder = AutoGenImportFinder(self)

    @property
    def patched_methods(self) -> list[str]:
        return sorted(self._patched_methods)

    @property
    def patched_modules(self) -> list[str]:
        return sorted(self._patched_modules)

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
        decoded_messages = self.codec.decode_many({"args": args, "kwargs": kwargs})
        prompt = self.codec.render_text(decoded_messages) or _extract_text(
            {"args": args, "kwargs": kwargs}
        )
        task = TaskSpec(
            task_id=call_id,
            group_id=self.context.session_id,
            title=f"AutoGen {target_kind} {method_name}",
            prompt=prompt,
            expected_agents=[agent.agent_id],
        )
        hook_context = HookCallContext(
            call_id=call_id,
            task=task,
            agent=agent,
            method_name=method_name,
            target_kind=target_kind,
            team_participants=self.describe_team_participants(
                instance,
                target_kind=target_kind,
            ),
        )
        self._safe_kernel_call(
            "before_agent_receive",
            lambda: self.kernel.before_agent_receive(
                task=task,
                round_id=1,
                mode="runtime_lite",
                agent=agent,
                state_refs=[],
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
                memory_refs=[],
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
        if not (message_kinds & {"tool_call", "tool_result"}):
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
                        "native_result_type": "transport_input",
                        "transport_input_state": True,
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
                "state_refs": state_ref_payload,
            },
        )
        self._record_shadow_handoff(
            context=context,
            decoded_messages=decoded_messages,
            native_text=native_text,
            state_refs=list(state_refs or []),
            state_ref_payload=state_ref_payload,
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
        if context.target_kind != "agentchat_agent":
            return args, kwargs
        if context.method_name not in {"on_messages", "on_messages_stream"}:
            return args, kwargs
        rewritten = self._rewrite_agent_text_messages(context, args, kwargs)
        return rewritten if rewritten is not None else (args, kwargs)

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
        if not isinstance(task_value, str):
            self._record_team_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["unsupported_team_task_type"],
                native_text=_extract_text(task_value),
            )
            return None
        native_text = task_value
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

        decoded_messages = self.codec.decode_many(native_text)
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

        fallback_reasons = _broadcast_fallback_reasons(
            expected_receivers=list(context.team_participants),
            receiver_entries=receiver_entries,
            native_tokens_per_receiver=_count_tokens(self.token_counter, native_text),
            total_wire_tokens=total_wire_tokens,
            total_prompt_view_tokens=total_prompt_view_tokens,
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

        new_args, new_kwargs = _replace_team_task_argument(
            args,
            kwargs,
            source=source,
            replacement=rewritten_content,
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
                ),
            )
            if isinstance(view, str) and view:
                prompt_views.append(view)
        prompt_view_text = "\n".join(prompt_views)
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
                memory_refs=[],
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
        rewritten_messages = [
            _clone_text_message_with_content(message, rewritten_content)
            for message in messages
        ]
        if any(message is None for message in rewritten_messages):
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
                wire_envelope=wire_envelope,
                prompt_view_text=prompt_view_text,
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
            wire_envelope=wire_envelope,
            prompt_view_text=prompt_view_text,
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
        wire_envelope: dict[str, Any] | None = None,
        prompt_view_text: str = "",
        rewrite_safety: dict[str, Any] | None = None,
        typed_rewrite_candidate: dict[str, Any] | None = None,
    ) -> None:
        native_tokens = _count_tokens(self.token_counter, native_text)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
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
                "shadow_wire_envelope": wire_envelope or {},
                "prompt_view_available": bool(prompt_view_text.strip()),
                "native_input_tokens": native_tokens,
                "rewritten_input_tokens": rewritten_tokens,
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
        wire_plus_prompt_view_tokens = sum(
            int(entry.get("shadow_wire_tokens", 0) or 0)
            + int(entry.get("prompt_view_tokens", 0) or 0)
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
        fallback_reasons = _broadcast_fallback_reasons(
            expected_receivers=list(context.team_participants),
            receiver_entries=receiver_entries,
            native_tokens_per_receiver=_count_tokens(self.token_counter, native_text),
            total_wire_tokens=total_wire_tokens,
            total_prompt_view_tokens=total_prompt_view_tokens,
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
        native_full_broadcast_tokens = native_tokens_per_receiver * receiver_count
        wire_plus_prompt_view_tokens = total_wire_tokens + total_prompt_view_tokens
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
                "receiver_count": receiver_count,
                "receiver_plans": receiver_entries,
                "native_tokens_per_receiver": native_tokens_per_receiver,
                "native_full_broadcast_tokens": native_full_broadcast_tokens,
                "shadow_wire_tokens": total_wire_tokens,
                "prompt_view_tokens": total_prompt_view_tokens,
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
    ) -> dict[str, Any]:
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
                memory_refs=[],
            ),
        )
        if not isinstance(envelope_json, str) or not envelope_json:
            return {}
        envelope_payload = _json_object_or_empty(envelope_json)
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        wire_envelope_json = json.dumps(
            wire_envelope, ensure_ascii=False, separators=(",", ":")
        )
        prompt_views = []
        for state_ref in state_refs:
            view = self._safe_kernel_call(
                "autogen_broadcast_prompt_view",
                lambda ref=state_ref: self.kernel.state_pool.render_prompt_view(
                    ref,
                    receiver_plan.declared_receiver,
                ),
            )
            if isinstance(view, str) and view:
                prompt_views.append(view)
        prompt_view_text = "\n".join(prompt_views)
        schema_valid = _wire_envelope_schema_valid(wire_envelope)
        return {
            "receiver": receiver_plan.declared_receiver,
            "receiver_source": receiver_plan.receiver_source,
            "message_kinds": receiver_plan.message_kinds,
            "summary": receiver_plan.summary,
            "state_refs": state_ref_payload,
            "shadow_wire_envelope": wire_envelope,
            "schema_valid": schema_valid,
            "shadow_wire_tokens": _count_tokens(
                self.token_counter,
                wire_envelope_json,
            ),
            "prompt_view_tokens": _count_tokens(
                self.token_counter,
                prompt_view_text,
            ),
            "prompt_view_available": bool(prompt_view_text.strip()),
            "prompt_view_text": prompt_view_text,
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
                        yield item
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
                    result = await original(instance, *args, **kwargs)
                    self.record_call_end(context, result)
                    return result
                except Exception as exc:
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
                        yield item
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
                return result
            except Exception as exc:
                self.record_call_error(context, exc)
                raise

        setattr(sync_wrapper, "__agentlite_wrapped__", True)
        return sync_wrapper

    @staticmethod
    def _target_methods_for(module_name: str) -> dict[str, tuple[str, ...]]:
        if module_name.startswith("autogen_agentchat.agents"):
            return {"agentchat_agent": PATCH_TARGETS["agentchat_agent"]}
        if module_name.startswith("autogen_agentchat.teams"):
            return {"agentchat_team": PATCH_TARGETS["agentchat_team"]}
        if module_name.startswith("autogen_core"):
            return {"core_runtime": PATCH_TARGETS["core_runtime"]}
        if module_name == "autogen":
            return {
                "agentchat_agent": PATCH_TARGETS["agentchat_agent"],
                "agentchat_team": PATCH_TARGETS["agentchat_team"],
            }
        return {}

    @staticmethod
    def _target_kind_for(module_name: str, cls: type) -> str | None:
        class_name = cls.__name__
        if module_name.startswith("autogen_agentchat.agents"):
            return "agentchat_agent"
        if module_name.startswith("autogen_agentchat.teams"):
            return "agentchat_team"
        if module_name.startswith("autogen_core") and "Runtime" in class_name:
            return "core_runtime"
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
    return ("agent_message_handling", "artifact_generation")


def _resolve_broadcast_mode(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if not normalized:
        return "shadow-only"
    if normalized in BROADCAST_MODES:
        return normalized
    return "shadow-only"


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
    if isinstance(message, dict):
        cloned = dict(message)
        cloned["content"] = content
        return cloned
    model_copy = getattr(message, "model_copy", None)
    if callable(model_copy):
        try:
            return model_copy(update={"content": content})
        except Exception:
            return None
    copy_method = getattr(message, "copy", None)
    if callable(copy_method):
        try:
            return copy_method(update={"content": content})
        except Exception:
            return None
    return None


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
    manifest = {
        "protocol": "agentlite.team_rewrite.v1",
        "receivers": [entry.get("receiver", "") for entry in receiver_entries],
        "state_refs": state_refs,
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
        prompt_view_text = str(entry.get("prompt_view_text", "") or "")
        sections.extend(
            [
                f"--- receiver: {receiver}",
                prompt_view_text,
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
                "state_ref_count": len(entry.get("state_refs", []) or []),
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
