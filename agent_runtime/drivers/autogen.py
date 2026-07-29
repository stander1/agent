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
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from agent_runtime.adapters.autogen_studio import (
    FrameworkRunBinding,
    activate_run_binding,
    configured_studio_appdir,
    current_run_binding,
    enrich_studio_binding,
    reset_run_binding,
)
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
from agent_runtime.llm.client import OpenAICompatibleChatClient
from agent_runtime.llm.config import LlmConfig
from agent_runtime.memory.claim_extractor import (
    extract_claim_cards,
    normalized_value,
    requests_historical_state,
    value_has_temporal_status,
)
from agent_runtime.memory.memory_store import (
    MemoryRef,
    MemoryStoreLite,
    classify_memory_quality_envelope,
)
from agent_runtime.memory.schema_registry import canonical_claim_polarity
from agent_runtime.memory.semantic_disambiguator import (
    ControlledSemanticDependencyAnalyzer,
    ControlledSemanticDisambiguator,
    SemanticDependencyBudget,
    SemanticDependencyRequest,
    SemanticDisambiguationBudget,
)
from agent_runtime.memory.context_views import (
    build_minimal_context_view,
    consumer_context_from_profile,
    field_fetch_query,
    infer_semantic_action,
)
from agent_runtime.reliability.final_delivery_guard import assess_final_delivery
from agent_runtime.reliability.memory_adoption_guard import (
    guard_memory_adoption_output,
)
from agent_runtime.reliability.review_conflict_guard import (
    build_review_blocker_claim,
    decision_from_typed_review_event,
    evaluate_review_conflict,
)
from agent_runtime.reliability.typed_events import (
    ArtifactRef,
    DeliveryEvent,
    DeliveryStatus,
    ReliabilityEventLedger,
    ReviewDecisionEvent,
    VerifiedEventAuthority,
    parse_reliability_metadata,
)
from agent_runtime.state.state_pool import StatePoolLite, StateRef
from agent_runtime.state.structured_output import structured_state_payloads

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
LEGACY_TEXT_REVIEW_MUTATION_ENV = (
    "AGENTLITE_AUTOGEN_LEGACY_TEXT_REVIEW_MUTATION"
)
SEMANTIC_DISAMBIGUATION_ENV = (
    "AGENTLITE_AUTOGEN_SEMANTIC_DISAMBIGUATION"
)
SEMANTIC_DISAMBIGUATION_BASE_URL_ENV = (
    "AGENTLITE_SEMANTIC_DISAMBIGUATION_BASE_URL"
)
SEMANTIC_DISAMBIGUATION_MODEL_ENV = (
    "AGENTLITE_SEMANTIC_DISAMBIGUATION_MODEL"
)
SEMANTIC_DISAMBIGUATION_API_KEY_ENV = (
    "AGENTLITE_SEMANTIC_DISAMBIGUATION_API_KEY_ENV"
)
SEMANTIC_DISAMBIGUATION_AUTH_SCHEME_ENV = (
    "AGENTLITE_SEMANTIC_DISAMBIGUATION_AUTH_SCHEME"
)
SEMANTIC_DISAMBIGUATION_MAX_CALLS_ENV = (
    "AGENTLITE_SEMANTIC_DISAMBIGUATION_MAX_CALLS_PER_TASK"
)
SEMANTIC_DISAMBIGUATION_MAX_SOURCE_CHARS_ENV = (
    "AGENTLITE_SEMANTIC_DISAMBIGUATION_MAX_SOURCE_CHARS"
)
SEMANTIC_DISAMBIGUATION_MAX_CANDIDATES_ENV = (
    "AGENTLITE_SEMANTIC_DISAMBIGUATION_MAX_CANDIDATES_PER_CALL"
)
SEMANTIC_DISAMBIGUATION_MAX_TOKENS_ENV = (
    "AGENTLITE_SEMANTIC_DISAMBIGUATION_MAX_CONTROL_TOKENS_PER_TASK"
)
SEMANTIC_DEPENDENCY_MAX_TOKENS_ENV = (
    "AGENTLITE_SEMANTIC_DEPENDENCY_MAX_CONTROL_TOKENS_PER_TASK"
)
BROADCAST_MODES = ("shadow-only", "dry-run-rewrite", "real-rewrite")
CORE_RECEIVER_HYDRATE_MODES = ("off", "prompt-view")
DRIVER_PHASE = "v5.15p"
_AUTOGEN_REPEATED_INSTANCE_ID_RE = re.compile(
    r"^(?P<logical>.+)_(?P<run>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})_(?P=run)$",
    re.IGNORECASE,
)
CONTINUITY_COST_GATE_REASONS = frozenset(
    {"token_not_reduced", "team_task_token_not_reduced"}
)
CURRENT_CANDIDATE_REQUIRED_ACTIONS = frozenset(
    {
        "REVIEW_OUTPUT",
        "REVIEW_SCHEMA",
        "VERIFY_CLAIM",
        "DIAGNOSE_FAILURE",
    }
)
CURRENT_CANDIDATE_REQUIRED_CAPABILITIES = frozenset(
    {
        "validation",
        "final_deliverable_review",
        "schema_review",
        "failure_review",
    }
)
_TASK_LABEL_RE = re.compile(
    r"\b(?P<prefix>[A-Za-z][A-Za-z0-9_.-]*?)(?P<number>\d+)\b"
)
_CURRENT_TASK_ASSERTION_PATTERNS = (
    re.compile(
        r"(?:本轮|当前(?:任务|步骤|阶段)|本任务|本阶段)"
        r"\s*[（(]?\s*(?P<label>[A-Za-z][A-Za-z0-9_.-]*?\d+)"
        r"\s*[）)]?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:current\s+(?:task|round|step|stage)|this\s+(?:task|round|step|stage))"
        r"\s*(?:is|=|:)?\s*(?P<label>[A-Za-z][A-Za-z0-9_.-]*?\d+)\b",
        re.IGNORECASE,
    ),
)
_CURRENT_INTERACTION_ASSERTION_PATTERNS = (
    re.compile(
        r"(?:本轮|当前用户指令|当前任务|本任务|本次(?:交互|任务)|"
        r"当前(?:交互|轮次))"
        r"\s*(?:[（(]\s*(?:交互|轮次|任务)?\s*#?|"
        r"(?:交互|轮次)\s*#?|#\s*)"
        r"(?P<number>\d+)\s*[）)]?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:current|this)\s+(?:user\s+)?"
        r"(?:interaction|round|task|request)"
        r"\s*(?:is|=|:)?\s*#?\s*(?P<number>\d+)\b",
        re.IGNORECASE,
    ),
)
_REQUIRED_ARTIFACT_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?P<ref>(?:[A-Za-z0-9_.-]+[\\/])*[A-Za-z0-9_.-]+"
    r"\.(?:md|txt|json|jsonl|csv|tsv|yaml|yml|toml|xml|pdf|docx))"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_EXPLICIT_EVIDENCE_FALLBACK_RE = re.compile(
    r"(?:(?:若|如果|当|when|if).{0,72}?)?"
    r"(?:证据|规则|资料|文件|artifact|evidence|rule|source).{0,36}?"
    r"(?:不足|缺失|不可用|未提供|无法读取|insufficient|missing|unavailable)"
    r"(?:时|则|必须|应当|应该|需|must|should|required|then|otherwise)?"
    r".{0,48}?"
    r"(?:返回|输出|使用|设为|结论(?:为)?|return|output|use|set)"
    r"\s*[`'\"“”]*"
    r"(?P<value>[A-Za-z][A-Za-z0-9_.-]{0,62}[A-Za-z0-9_-])",
    re.IGNORECASE,
)
_EVIDENCE_READ_CLAIM_RE = re.compile(
    r"(?:已|已经|成功)?(?:读取|加载|查阅|解析|核对|依据|根据|基于)"
    r"|(?:read|loaded|parsed|consulted|according\s+to|based\s+on)",
    re.IGNORECASE,
)
_GENERIC_NUMERIC_CONCEPT_BIGRAMS = frozenset(
    {
        "任务",
        "系统",
        "方案",
        "数据",
        "配置",
        "支持",
        "用户",
        "当前",
        "已经",
        "需要",
        "进行",
        "使用",
        "实现",
        "完成",
        "提供",
    }
)
_GENERIC_NUMERIC_CONCEPT_CHARS = frozenset(
    "任务系统方案数据配置支持用户当前已经需要进行使用实现完成提供个第项目"
)
CONTINUITY_CUE_PATTERNS = (
    (
        "zh_prior_reference",
        re.compile(
            r"(?:基于|根据|依据|参考|结合|沿用|承接)\s*"
            r"(?:刚才|之前|前面|上述|以上|先前|此前|上一(?:轮|步|个|阶段)|"
            r"前一(?:轮|步|个|阶段)|已有|已确认)"
        ),
    ),
    (
        "zh_named_step_reference",
        re.compile(
            r"(?:基于|根据|依据|参考|结合|沿用)\s*"
            r"(?:任务|步骤|阶段|版本|方案)?\s*"
            r"[A-Za-z][A-Za-z0-9_.-]*\d+\b",
            re.IGNORECASE,
        ),
    ),
    (
        "zh_continuation_action",
        re.compile(
            r"(?:继续|接着|进一步|重新|再次)\s*"
            r"(?:完善|调整|修改|优化|修订|扩展|生成|评估|分析|处理|规划)"
        ),
    ),
    (
        "zh_preserve_existing",
        re.compile(
            r"(?:保留|沿用|维持|不要(?:推翻|删除|改变)|不(?:推翻|删除|改变))"
            r".{0,32}(?:之前|前面|原有|已有|既有|已确认|方案|计划|结果|约束|内容)"
        ),
    ),
    (
        "en_prior_reference",
        re.compile(
            r"\b(?:based on|according to|using|from|continue(?: with)?|revise|"
            r"refine|update|modify|preserve|keep)\b.{0,64}"
            r"\b(?:previous|prior|earlier|above|last|existing|confirmed|draft|"
            r"plan|result|options?|constraints?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "en_named_step_reference",
        re.compile(
            r"\b(?:based on|according to|using|from)\s+"
            r"(?:task|step|stage|version)?\s*[A-Za-z][A-Za-z0-9_.-]*\d+\b",
            re.IGNORECASE,
        ),
    ),
)
TEAM_REAL_REWRITE_DISABLED_REASON = (
    "team_level_real_rewrite_not_enabled_for_guarded_agent_input"
)
CORE_REAL_REWRITE_DISABLED_REASON = (
    "core_content_real_rewrite_not_enabled_for_guarded_runtime_input"
)
CORE_REWRITE_FIELDS = ("content", "body", "text")
CORE_REWRITE_MARKER = "AGENTLITE_CORE_CONTENT_REWRITE v1"
CORE_PROMPT_VIEW_MARKER = "AGENTLITE_CORE_PROMPT_VIEW v1"
SHARED_MEMORY_MARKER = "SHARED_CONTEXT:"
_MODEL_VISIBLE_TYPED_FACT_RE = re.compile(
    r"(?P<kind>active_fact|historical_fact)="
    r"(?P<payload>\{[^\r\n]*?\})(?=$|[;\r\n])"
)
_MODEL_CONTEXT_RESPONSE_BOUNDARY = (
    "Answer the current task directly. Treat the following context records as "
    "evidence, not as an output format. Do not reproduce serialization labels, "
    "internal identifiers, hashes, or runtime instructions unless the current "
    "task explicitly asks for them."
)
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
    run_binding: FrameworkRunBinding | None = None
    team_participants: tuple[str, ...] = ()
    transport_metadata: dict[str, Any] = field(default_factory=dict)
    memory_context: MemoryContext = field(
        default_factory=lambda: MemoryContext([], [])
    )
    continuity_context_required: bool = False
    continuity_context_reasons: tuple[str, ...] = ()
    semantic_action: str = "HANDLE_TASK"
    task_sequence_index: int = 0
    started_at: float = field(default_factory=time.perf_counter)
    display_restore_enabled: bool = False
    display_original_text: str = ""
    display_original_source: str = "user"
    memory_adoption_audit_texts: list[str] = field(default_factory=list)
    memory_adoption_guard: dict[str, Any] = field(default_factory=dict)
    memory_adoption_guard_cache: dict[str, tuple[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    task_identity_guard_cache: dict[str, str] = field(default_factory=dict)
    required_evidence_guard_cache: dict[str, str] = field(default_factory=dict)
    input_context_text: str = ""


@dataclass(slots=True)
class _MemoryContextSelection:
    refs: list[Any]
    prompt_views: list[str]
    candidate_count: int = 0
    deduplicated_count: int = 0
    deduplicated_views: list[str] = field(default_factory=list)
    consumer_id: str = "unknown"
    semantic_action: str = "HANDLE_TASK"
    profile_version: int = 0
    capabilities: tuple[str, ...] = ()
    information_fields: tuple[str, ...] = ()
    source_prompt_views: list[str] = field(default_factory=list)
    requested_fields: tuple[str, ...] = ()
    covered_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    role_view_source_chars: int = 0
    role_view_selected_chars: int = 0
    role_view_reduction_ratio: float = 0.0
    role_view_source_tokens: int = 0
    role_view_selected_tokens: int = 0
    field_fetch_count: int = 0
    field_fetch_tokens: int = 0
    field_fetch_state_ids: list[str] = field(default_factory=list)
    role_view_candidate_tokens: int = 0
    role_view_selection_mode: str = "empty"
    role_view_no_expansion_fallback: bool = False


@dataclass(slots=True)
class _InjectedMemoryRecord:
    ref: MemoryRef
    prompt_view: str
    current_task_text: str
    current_task_source: str
    injected_prompt_view: str
    revision_guard: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _TokenBoundViewSelection:
    text: str
    source_tokens: int
    candidate_tokens: int
    selected_tokens: int
    selection_mode: str
    no_expansion_fallback: bool

    @property
    def reduction_ratio(self) -> float:
        if self.source_tokens <= 0:
            return 0.0
        return round(1 - (self.selected_tokens / self.source_tokens), 6)


@dataclass(frozen=True, slots=True)
class _TypedReliabilityProcessing:
    metadata_present: bool = False
    review_events_declared: bool = False
    delivery_events_declared: bool = False
    accepted_review_events: tuple[ReviewDecisionEvent, ...] = ()
    accepted_delivery_events: tuple[DeliveryEvent, ...] = ()
    errors: tuple[str, ...] = ()


class AutoGenHookManager:
    """Transparent AutoGen instrumentation owned by the managed process."""

    def __init__(self, context: BootstrapContext) -> None:
        self.context = context
        self.session_dir = context.status_file.parent
        self.output_dir = self.session_dir / "autogen_driver"
        self.trace = TraceLogger(self.output_dir)
        self.trace.set_context_provider(self._trace_run_context)
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
        self.legacy_text_review_mutation_enabled = _truthy_env(
            os.getenv(LEGACY_TEXT_REVIEW_MUTATION_ENV, "")
        )
        self.semantic_disambiguation_enabled = _truthy_env(
            os.getenv(SEMANTIC_DISAMBIGUATION_ENV, "")
        )
        semantic_disambiguator = (
            _build_semantic_disambiguator_from_env()
            if self.semantic_disambiguation_enabled
            else None
        )
        self.semantic_dependency_analyzer = (
            ControlledSemanticDependencyAnalyzer(
                semantic_disambiguator.client,
                budget=SemanticDependencyBudget(
                    max_control_tokens_per_task=_positive_int_env(
                        SEMANTIC_DEPENDENCY_MAX_TOKENS_ENV,
                        1_536,
                    ),
                ),
            )
            if semantic_disambiguator is not None
            else None
        )
        self.memory_scope_id = _resolve_memory_scope_id(context)
        self.studio_appdir = configured_studio_appdir()
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
            semantic_disambiguator=semantic_disambiguator,
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
                "semantic_disambiguation_enabled": (
                    self.semantic_disambiguation_enabled
                ),
                "memory_scope_id": self.memory_scope_id,
                "final_delivery_marker": self.final_delivery_marker,
                "studio_appdir": str(self.studio_appdir or ""),
                "framework_run_binding": "contextvar",
            },
        )
        self._sequence = 0
        self._lock = threading.Lock()
        self._memory_lock = threading.RLock()
        self._promoted_memory_fingerprints: set[str] = set()
        self._rewritten_prompt_fingerprints: set[str] = set()
        self._collaboration_group_by_agent: dict[str, str] = {}
        self._current_team_task_by_group: dict[str, str] = {}
        self._user_task_history_by_group: dict[str, list[str]] = {}
        self._team_task_sequence_by_group: dict[str, int] = {}
        self._continuity_reasons_by_group_sequence: dict[
            tuple[str, int],
            tuple[str, ...],
        ] = {}
        self._memory_injections_by_call: dict[str, list[_InjectedMemoryRecord]] = {}
        self._reliability_ledger = ReliabilityEventLedger()
        self._reliability_evidence_sources: dict[
            tuple[str, str],
            dict[str, str],
        ] = {}
        self._reliability_artifact_contents: dict[
            tuple[str, str, str, int],
            str,
        ] = {}
        self._typed_delivery_by_task: dict[
            tuple[str, str],
            DeliveryEvent,
        ] = {}
        self._core_response_rewrite_depth = 0
        self._patched_methods: set[str] = set()
        self._patched_modules: set[str] = set()
        self._run_binding_cache: dict[str, FrameworkRunBinding] = {}
        self._active_run_calls: dict[str, dict[str, Any]] = {}
        self._import_finder = AutoGenImportFinder(self)

    @property
    def patched_methods(self) -> list[str]:
        return sorted(self._patched_methods)

    @property
    def patched_modules(self) -> list[str]:
        return sorted(self._patched_modules)

    def _is_known_rewritten_prompt(self, decoded_messages: list[Any]) -> bool:
        with self._memory_lock:
            known = set(self._rewritten_prompt_fingerprints)
        return any(
            _content_fingerprint(
                str(getattr(message, "content_text", "") or "").strip()
            )
            in known
            for message in decoded_messages
            if str(getattr(message, "content_text", "") or "").strip()
        )

    def _remember_rewritten_prompt(self, content: str) -> None:
        fingerprint = _content_fingerprint(content)
        if not fingerprint:
            return
        with self._memory_lock:
            if len(self._rewritten_prompt_fingerprints) >= 8192:
                self._rewritten_prompt_fingerprints.clear()
            self._rewritten_prompt_fingerprints.add(fingerprint)

    def _trace_run_context(self) -> dict[str, Any]:
        binding = current_run_binding()
        return binding.trace_fields() if binding is not None else {}

    def _resolve_call_run_binding(
        self,
        *,
        target_kind: str,
        call_id: str,
    ) -> FrameworkRunBinding | None:
        binding = current_run_binding()
        if binding is None and target_kind == "agentchat_team":
            binding = FrameworkRunBinding(
                framework_run_id=f"autogen:{call_id}",
                source="agentlite_team_call",
            )
        if binding is None:
            return None
        with self._lock:
            cached = self._run_binding_cache.get(binding.framework_run_id)
        if cached is not None:
            return cached
        enriched = enrich_studio_binding(binding, appdir=self.studio_appdir)
        with self._lock:
            self._run_binding_cache[enriched.framework_run_id] = enriched
        return enriched

    def _record_framework_run_start(self, context: HookCallContext) -> None:
        binding = context.run_binding
        if context.target_kind != "agentchat_team" or binding is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            active = self._active_run_calls.get(binding.framework_run_id)
            if active is not None:
                active["depth"] = int(active.get("depth", 0)) + 1
                return
            manifest = {
                "schema_version": "agentlite.framework_run.v1",
                "agentlite_session_id": self.context.session_id,
                **binding.trace_fields(),
                "status": "running",
                "started_at": now,
                "finished_at": "",
                "root_call_id": context.call_id,
                "team_method": context.method_name,
                "team_agent_id": context.agent.agent_id,
                "team_participants": list(context.team_participants),
                "task_preview": _preview(context.task.prompt),
                "error_type": "",
                "error": "",
            }
            self._active_run_calls[binding.framework_run_id] = {
                "depth": 1,
                "manifest": manifest,
                "failed": False,
            }
        self.trace.write("autogen_framework_run_started", dict(manifest))
        self._write_framework_run_manifest(binding.framework_run_id, manifest)

    def _record_framework_run_end(
        self,
        context: HookCallContext | None,
        *,
        error: BaseException | None = None,
    ) -> None:
        if context is None or context.target_kind != "agentchat_team":
            return
        binding = context.run_binding
        if binding is None:
            return
        finished: dict[str, Any] | None = None
        with self._lock:
            active = self._active_run_calls.get(binding.framework_run_id)
            if active is None:
                return
            if error is not None:
                active["failed"] = True
                active["error_type"] = type(error).__name__
                active["error"] = str(error)
            active["depth"] = max(0, int(active.get("depth", 1)) - 1)
            if active["depth"] > 0:
                return
            manifest = dict(active["manifest"])
            failed = bool(active.get("failed"))
            manifest.update(
                {
                    "status": "failed" if failed else "completed",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error_type": str(active.get("error_type") or ""),
                    "error": str(active.get("error") or ""),
                }
            )
            finished = manifest
            self._active_run_calls.pop(binding.framework_run_id, None)
        self.trace.write("autogen_framework_run_finished", dict(finished))
        self._write_framework_run_manifest(binding.framework_run_id, finished)

    def _write_framework_run_manifest(
        self,
        framework_run_id: str,
        manifest: dict[str, Any],
    ) -> None:
        run_dir = self.output_dir / "runs" / _filesystem_identifier(framework_run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / "run.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)

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
                "mixed_memory_hit_count": 0,
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

    def _semantic_dependency_requirement_reasons(
        self,
        *,
        task: TaskSpec,
        task_sequence_index: int,
        current_text: str,
        memory_context: MemoryContext,
    ) -> tuple[str, ...]:
        if not current_text.strip() or not memory_context.refs:
            return ()
        memory_items = tuple(
            {
                "memory_id": str(getattr(ref, "memory_id", "") or ""),
                "summary": str(prompt_view or "")[:1_500],
            }
            for ref, prompt_view in zip(
                memory_context.refs,
                memory_context.prompt_views,
            )
            if str(getattr(ref, "memory_id", "") or "")
        )
        if not memory_items:
            return ()
        structured_reasons = _structured_memory_dependency_reasons(current_text)
        if structured_reasons:
            self.trace.write(
                "autogen_semantic_dependency",
                {
                    "task_id": task.task_id,
                    "group_id": task.group_id,
                    "task_sequence_index": task_sequence_index,
                    "status": "structured_required",
                    "required": True,
                    "selected_memory_ids": [
                        item["memory_id"] for item in memory_items
                    ],
                    "source_quote_fingerprint": _text_fingerprint(current_text),
                    "confidence": 1.0,
                    "reasons": list(structured_reasons),
                    "call_count": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "usage_estimated": False,
                    "model": "",
                    "latency_ms": 0.0,
                    "retry_count": 0,
                    "memory_candidate_count": len(memory_items),
                },
            )
            return structured_reasons
        analyzer = self.semantic_dependency_analyzer
        if analyzer is None:
            return ()
        result = analyzer.analyze(
            SemanticDependencyRequest(
                scope_id=task.group_id,
                task_id=task.task_id,
                current_text=current_text.strip(),
                memory_items=memory_items,
            )
        )
        if result.call_count:
            self.metrics.record_control_llm(
                task_id=task.task_id,
                round_id=1,
                mode="runtime_lite",
                call_count=result.call_count,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                retry_count=result.retry_count,
                latency_ms=result.latency_ms,
            )
        self.trace.write(
            "autogen_semantic_dependency",
            {
                "task_id": task.task_id,
                "group_id": task.group_id,
                "task_sequence_index": task_sequence_index,
                "status": result.status,
                "required": result.required,
                "selected_memory_ids": list(result.memory_ids),
                "source_quote_fingerprint": (
                    _text_fingerprint(result.source_quote)
                    if result.source_quote
                    else ""
                ),
                "confidence": result.confidence,
                "reasons": list(result.reasons),
                "call_count": result.call_count,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
                "usage_estimated": result.usage_estimated,
                "model": result.model,
                "latency_ms": result.latency_ms,
                "retry_count": result.retry_count,
                "memory_candidate_count": len(memory_items),
            },
        )
        if result.accepted and result.required:
            return ("semantic_dependency_required",)
        return ()

    def _register_memory_injection(
        self,
        *,
        context: HookCallContext,
        memory_refs: list[dict[str, Any]],
        injected_prompt_view: str,
    ) -> None:
        records: list[_InjectedMemoryRecord] = []
        with self._memory_lock:
            seen_refs: set[tuple[str, int]] = set()
            team_task = self._current_team_task_by_group.get(
                context.task.group_id,
                "",
            )
            current_task = team_task or context.task.prompt
            current_task_source = (
                "team_task_by_group" if team_task else "call_prompt"
            )
            for payload in memory_refs:
                memory_id = str(payload.get("memory_id", "") or "")
                if not memory_id:
                    continue
                ref = self.kernel.memory_store.resolve_ref(memory_id)
                if ref is None:
                    continue
                ref_key = (ref.memory_id, ref.version_id)
                if ref_key in seen_refs:
                    continue
                seen_refs.add(ref_key)
                try:
                    prompt_view = self.kernel.memory_store.render_prompt_view(
                        ref,
                        budget_chars=4000,
                    )
                    revision_guard = self.kernel.memory_store.revision_guard(ref)
                except (KeyError, ValueError):
                    continue
                records.append(
                    _InjectedMemoryRecord(
                        ref=ref,
                        prompt_view=prompt_view,
                        current_task_text=current_task,
                        current_task_source=current_task_source,
                        injected_prompt_view=injected_prompt_view,
                        revision_guard=revision_guard,
                    )
                )
            if records:
                self._memory_injections_by_call[context.call_id] = records

    def _guard_current_task_identity_if_needed(
        self,
        context: HookCallContext,
        result: Any,
    ) -> Any:
        if self.broadcast_mode != "real-rewrite":
            return result
        if context.target_kind != "agentchat_agent":
            return result
        if context.method_name not in {"on_messages", "on_messages_stream"}:
            return result
        output_text = _memory_guard_result_text(result)
        if not output_text.strip():
            return result
        output_fingerprint = _text_fingerprint(output_text)
        cached_text = context.task_identity_guard_cache.get(output_fingerprint)
        if cached_text is not None:
            cloned = _clone_memory_guard_result(result, cached_text)
            return cloned if cloned is not None else result

        with self._memory_lock:
            current_task = self._current_team_task_by_group.get(
                context.task.group_id,
                "",
            )
            user_task_history = tuple(
                self._user_task_history_by_group.get(
                    context.task.group_id,
                    (),
                )
            )
        assessment = _current_task_identity_assessment(
            current_task=current_task or context.task.prompt,
            output_text=output_text,
            task_sequence_index=context.task_sequence_index,
            user_task_history=user_task_history,
        )
        if not assessment["blocked"]:
            return result

        correction = "\n".join(
            (
                "Current-task correction required.",
                (
                    f"This is collaboration interaction "
                    f"#{context.task_sequence_index}. The preceding response "
                    "asserted an unsupported task label or interaction ordinal "
                    "that is not grounded in the current user request."
                ),
                "Continue from this exact current task without advancing to a "
                "different task or round:",
                current_task or context.task.prompt,
            )
        )
        guarded_result = _clone_memory_guard_result(result, correction)
        if guarded_result is None:
            raise RuntimeError(
                "AgentLite blocked current-task identity drift but could not "
                "preserve the native AutoGen result type."
            )
        context.task_identity_guard_cache[output_fingerprint] = correction
        self.trace.write(
            "autogen_current_task_identity_guard",
            {
                "call_id": context.call_id,
                "task_id": context.task.task_id,
                "group_id": context.task.group_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "task_sequence_index": context.task_sequence_index,
                "status": "blocked_and_reanchored",
                **assessment,
                "original_output_fingerprint": output_fingerprint,
                "guarded_output_fingerprint": _text_fingerprint(correction),
            },
        )
        return guarded_result

    def _guard_required_evidence_if_needed(
        self,
        context: HookCallContext,
        result: Any,
    ) -> Any:
        if self.broadcast_mode != "real-rewrite":
            return result
        if context.target_kind != "agentchat_agent":
            return result
        if context.method_name not in {"on_messages", "on_messages_stream"}:
            return result
        output_text = _memory_guard_result_text(result)
        if not output_text.strip():
            return result
        output_fingerprint = _text_fingerprint(output_text)
        cached_text = context.required_evidence_guard_cache.get(
            output_fingerprint
        )
        if cached_text is not None:
            cloned = _clone_memory_guard_result(result, cached_text)
            return cloned if cloned is not None else result

        with self._memory_lock:
            current_task = self._current_team_task_by_group.get(
                context.task.group_id,
                "",
            )
            user_history = tuple(
                self._user_task_history_by_group.get(
                    context.task.group_id,
                    (),
                )
            )
        assessment = _required_evidence_assessment(
            current_task=current_task or context.task.prompt,
            evidence_context="\n".join(
                (*user_history[:-1], context.input_context_text)
            ),
            output_text=output_text,
            target_cwd=self.context.target_cwd,
        )
        if not assessment["blocked"]:
            return result

        correction = _required_evidence_fallback_artifact(
            fallback_value=str(assessment["fallback_value"]),
            unavailable_artifacts=assessment["unavailable_artifacts"],
        )
        guarded_result = _clone_memory_guard_result(result, correction)
        if guarded_result is None:
            raise RuntimeError(
                "AgentLite blocked an ungrounded evidence claim but could not "
                "preserve the native AutoGen result type."
            )
        context.required_evidence_guard_cache[output_fingerprint] = correction
        self.trace.write(
            "autogen_required_evidence_guard",
            {
                "call_id": context.call_id,
                "task_id": context.task.task_id,
                "group_id": context.task.group_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "status": "blocked_with_explicit_fallback",
                **assessment,
                "original_output_fingerprint": output_fingerprint,
                "guarded_output_fingerprint": _text_fingerprint(correction),
            },
        )
        return guarded_result

    def guard_call_result_if_needed(
        self,
        context: HookCallContext,
        result: Any,
    ) -> Any:
        """Prevent superseded memory facts from leaving an Agent call."""

        result = self._guard_required_evidence_if_needed(context, result)
        result = self._guard_current_task_identity_if_needed(context, result)
        if context.target_kind != "agentchat_agent":
            return result
        if context.method_name not in {"on_messages", "on_messages_stream"}:
            return result
        output_text = _memory_guard_result_text(result)
        if not output_text.strip():
            return result
        with self._memory_lock:
            records = list(
                self._memory_injections_by_call.get(context.call_id, ())
            )
        if not records:
            return result

        output_fingerprint = _text_fingerprint(output_text)
        cached = context.memory_adoption_guard_cache.get(output_fingerprint)
        if cached is not None:
            guarded_text, guard_payload = cached
            context.memory_adoption_guard = dict(guard_payload)
            cloned = _clone_memory_guard_result(result, guarded_text)
            if cloned is None:
                raise RuntimeError(
                    "AgentLite could not reapply a cached memory-adoption "
                    "guard while preserving the native AutoGen result type."
                )
            return cloned

        evidence_rows = [
            {
                "memory_ref": _memory_ref_payload(record.ref),
                **_memory_adoption_evidence(
                    memory_prompt_view=record.prompt_view,
                    injected_prompt_view=record.injected_prompt_view,
                    current_task_text=record.current_task_text,
                    output_text=output_text,
                    memory_id=record.ref.memory_id,
                    memory_view_id=record.ref.memory_view_id,
                    revision_guard=record.revision_guard,
                ),
            }
            for record in records
        ]
        decision = guard_memory_adoption_output(
            output_text=output_text,
            evidence_rows=evidence_rows,
        )
        if not decision.changed:
            return result

        guarded_result = _clone_memory_guard_result(
            result,
            decision.output_text,
        )
        guard_payload = {
            **decision.to_dict(),
            "original_output_fingerprint": output_fingerprint,
            "guarded_output_fingerprint": _text_fingerprint(
                decision.output_text
            ),
            "compensation_event_count": 0,
            "compensation_event_ids": [],
        }
        if guarded_result is None:
            guard_payload.update(
                {
                    "status": "enforcement_failed",
                    "blocked": True,
                    "safe_to_continue": False,
                    "allowed_next_step": "review_or_retry_only",
                    "reasons": [
                        *guard_payload.get("reasons", []),
                        "native_result_clone_failed",
                    ],
                }
            )
            self.trace.write(
                "autogen_memory_adoption_guard",
                {
                    "call_id": context.call_id,
                    "task_id": context.task.task_id,
                    "group_id": context.task.group_id,
                    "agent_id": context.agent.agent_id,
                    "role": context.agent.role,
                    "target_kind": context.target_kind,
                    "method": context.method_name,
                    **guard_payload,
                },
            )
            raise RuntimeError(
                "AgentLite blocked an unsafe memory-derived output but could "
                "not preserve the native AutoGen result type."
            )

        compensation_event_ids: list[str] = []
        for record, evidence in zip(records, evidence_rows):
            if str(evidence.get("status") or "") not in {"wrong", "mixed"}:
                continue
            resolved_ref = self.kernel.memory_store.resolve_ref(
                record.ref.memory_id
            )
            if resolved_ref is None:
                continue
            event = self._safe_kernel_call(
                "record_autogen_memory_downstream_compensation",
                lambda ref=resolved_ref: (
                    self.kernel.memory_store.record_downstream_compensation(
                        ref,
                        reason=(
                            f"task={context.task.task_id};"
                            f"call={context.call_id};"
                            f"guard={decision.status};"
                            f"output={output_fingerprint}"
                        ),
                        created_by="AgentLiteMemoryAdoptionGuard",
                    )
                ),
            )
            event_id = str(getattr(event, "event_id", "") or "")
            if event_id:
                compensation_event_ids.append(event_id)
        guard_payload["compensation_event_count"] = len(
            compensation_event_ids
        )
        guard_payload["compensation_event_ids"] = compensation_event_ids

        context.memory_adoption_audit_texts.append(output_text)
        context.memory_adoption_guard = guard_payload
        context.memory_adoption_guard_cache[output_fingerprint] = (
            decision.output_text,
            dict(guard_payload),
        )
        self.trace.write(
            "autogen_memory_adoption_guard",
            {
                "call_id": context.call_id,
                "task_id": context.task.task_id,
                "group_id": context.task.group_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                **guard_payload,
            },
        )
        return guarded_result

    def _record_memory_adoption_feedback(
        self,
        *,
        context: HookCallContext,
        output_text: str,
    ) -> None:
        with self._memory_lock:
            records = self._memory_injections_by_call.pop(context.call_id, [])
        if not records:
            return

        useful_records: list[_InjectedMemoryRecord] = []
        wrong_records: list[_InjectedMemoryRecord] = []
        mixed_records: list[_InjectedMemoryRecord] = []
        evidence_rows: list[dict[str, Any]] = []
        for record in records:
            evidence = _memory_adoption_evidence(
                memory_prompt_view=record.prompt_view,
                injected_prompt_view=record.injected_prompt_view,
                current_task_text=record.current_task_text,
                output_text=output_text,
                memory_id=record.ref.memory_id,
                memory_view_id=record.ref.memory_view_id,
                revision_guard=record.revision_guard,
            )
            if evidence["status"] == "useful":
                useful_records.append(record)
            elif evidence["status"] == "wrong":
                wrong_records.append(record)
            elif evidence["status"] == "mixed":
                mixed_records.append(record)
            evidence_rows.append(
                {
                    "memory_ref": _memory_ref_payload(record.ref),
                    **evidence,
                }
            )

        useful_refs = [record.ref for record in useful_records]
        wrong_refs = [record.ref for record in wrong_records]
        mixed_refs = [record.ref for record in mixed_records]
        feedback = self._safe_kernel_call(
            "record_autogen_memory_use_feedback",
            lambda: self.kernel.record_memory_use_feedback(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                useful_refs=useful_refs,
                wrong_refs=wrong_refs,
                mixed_refs=mixed_refs,
                supported_output=bool(useful_refs or mixed_refs),
            ),
        )
        unique_injected = {
            (record.ref.memory_id, record.ref.version_id) for record in records
        }
        unique_useful = {
            (record.ref.memory_id, record.ref.version_id) for record in useful_records
        }
        unique_wrong = {
            (record.ref.memory_id, record.ref.version_id) for record in wrong_records
        }
        unique_mixed = {
            (record.ref.memory_id, record.ref.version_id) for record in mixed_records
        }
        self.trace.write(
            "autogen_memory_adoption",
            {
                "call_id": context.call_id,
                "task_id": context.task.task_id,
                "group_id": context.task.group_id,
                "agent_id": context.agent.agent_id,
                "role": context.agent.role,
                "target_kind": context.target_kind,
                "method": context.method_name,
                "memory_injected_count": len(unique_injected),
                "useful_memory_hit_count": len(unique_useful),
                "wrong_memory_hit_count": len(unique_wrong),
                "mixed_memory_hit_count": len(unique_mixed),
                "unassessed_memory_hit_count": max(
                    0,
                    len(unique_injected)
                    - len(unique_useful)
                    - len(unique_wrong)
                    - len(unique_mixed),
                ),
                "memory_supported_output_count": int(
                    bool(unique_useful or unique_mixed)
                ),
                "attribution_mode": (
                    "ccf_v2_semantic_key_value_rules"
                    if any(
                        row.get("attribution_mode")
                        == "ccf_v2_semantic_key_value_rules"
                        for row in evidence_rows
                    )
                    else "active_and_historical_fact_rules_v3"
                ),
                "current_task_source": records[0].current_task_source,
                "current_task_fingerprint": _text_fingerprint(
                    records[0].current_task_text
                ),
                "feedback": feedback if isinstance(feedback, dict) else {},
                "evidence": evidence_rows,
            },
        )

    def _process_typed_reliability_metadata(
        self,
        *,
        context: HookCallContext,
        decoded_messages: list[Any],
        semantic_state_text: str,
        state_refs: list[StateRef],
    ) -> _TypedReliabilityProcessing:
        parsed = parse_reliability_metadata(decoded_messages)
        artifact_rows: list[dict[str, Any]] = []
        review_rows: list[dict[str, Any]] = []
        delivery_rows: list[dict[str, Any]] = []
        errors = list(parsed.errors)
        accepted_reviews: list[ReviewDecisionEvent] = []
        accepted_deliveries: list[DeliveryEvent] = []

        with self._memory_lock:
            task_key = (
                context.task.group_id,
                context.task.task_id,
            )
            task_evidence_sources = (
                self._reliability_evidence_sources.setdefault(
                    task_key,
                    {},
                )
            )
            for state_ref in state_refs:
                artifact = ArtifactRef.from_content(
                    artifact_id=state_ref.state_id,
                    version=state_ref.version,
                    content=semantic_state_text,
                    source_id=state_ref.state_id,
                    scope_id=context.task.group_id,
                    task_id=context.task.task_id,
                )
                validation = self._reliability_ledger.register_artifact(
                    artifact,
                    content=semantic_state_text,
                    expected_scope_id=context.task.group_id,
                    expected_task_id=context.task.task_id,
                )
                if validation.accepted:
                    self._reliability_artifact_contents[
                        artifact.identity
                    ] = semantic_state_text
                    task_evidence_sources[
                        artifact.artifact_id
                    ] = semantic_state_text
                    task_evidence_sources[
                        artifact.source_id
                    ] = semantic_state_text
                else:
                    errors.extend(
                        f"runtime_artifact:{artifact.artifact_id}:{reason}"
                        for reason in validation.reasons
                    )
                artifact_rows.append(
                    {
                        "source": "runtime_state",
                        "artifact_id": artifact.artifact_id,
                        "version": artifact.version,
                        **validation.to_dict(),
                    }
                )

            for declaration in parsed.artifacts:
                validation = self._reliability_ledger.register_artifact(
                    declaration.artifact,
                    content=declaration.content,
                    expected_scope_id=context.task.group_id,
                    expected_task_id=context.task.task_id,
                )
                if validation.accepted:
                    self._reliability_artifact_contents[
                        declaration.artifact.identity
                    ] = declaration.content
                    task_evidence_sources[
                        declaration.artifact.artifact_id
                    ] = declaration.content
                    if declaration.artifact.source_id:
                        task_evidence_sources[
                            declaration.artifact.source_id
                        ] = declaration.content
                else:
                    errors.extend(
                        "declared_artifact:"
                        f"{declaration.artifact.artifact_id}:{reason}"
                        for reason in validation.reasons
                    )
                artifact_rows.append(
                    {
                        "source": "message_metadata",
                        "artifact_id": declaration.artifact.artifact_id,
                        "version": declaration.artifact.version,
                        **validation.to_dict(),
                    }
                )

            profile = self.kernel.capability_profiles.get(
                context.agent.agent_id
            )
            capabilities = tuple(
                dict.fromkeys(
                    [
                        *context.agent.capabilities,
                        *(
                            tuple(
                                getattr(profile, "capabilities", ()) or ()
                            )
                            if profile is not None
                            else ()
                        ),
                    ]
                )
            )
            authority = VerifiedEventAuthority.from_runtime(
                actor_id=context.agent.agent_id,
                semantic_action=context.semantic_action,
                capabilities=capabilities,
                profile_version=int(
                    getattr(profile, "profile_version", 0) or 0
                ),
            )
            evidence_sources = dict(task_evidence_sources)
            for event in parsed.review_events:
                validation = self._reliability_ledger.record_review(
                    event,
                    authority=authority,
                    evidence_sources=evidence_sources,
                    expected_scope_id=context.task.group_id,
                    expected_task_id=context.task.task_id,
                )
                if validation.accepted:
                    accepted_reviews.append(event)
                else:
                    errors.extend(
                        f"review_event:{event.event_id}:{reason}"
                        for reason in validation.reasons
                    )
                review_rows.append(
                    {
                        "event_id": event.event_id,
                        "decision": event.decision.value,
                        "target_artifact_id": event.target.artifact_id,
                        "target_version": event.target.version,
                        **validation.to_dict(),
                    }
                )
            for event in parsed.delivery_events:
                artifact_content = self._reliability_artifact_contents.get(
                    event.artifact.identity
                )
                validation = self._reliability_ledger.record_delivery(
                    event,
                    content=artifact_content,
                    authority_actor_id=context.agent.agent_id,
                    expected_scope_id=context.task.group_id,
                    expected_task_id=context.task.task_id,
                )
                if validation.accepted:
                    accepted_deliveries.append(event)
                    key = (
                        context.task.group_id,
                        context.task.task_id,
                    )
                    previous = self._typed_delivery_by_task.get(key)
                    if previous is None or event.sequence > previous.sequence:
                        self._typed_delivery_by_task[key] = event
                else:
                    errors.extend(
                        f"delivery_event:{event.event_id}:{reason}"
                        for reason in validation.reasons
                    )
                delivery_rows.append(
                    {
                        "event_id": event.event_id,
                        "status": event.status.value,
                        "artifact_id": event.artifact.artifact_id,
                        "artifact_version": event.artifact.version,
                        **validation.to_dict(),
                    }
                )

        if (
            parsed.metadata_present
            or parsed.errors
            or review_rows
            or delivery_rows
        ):
            self.trace.write(
                "autogen_typed_reliability_metadata",
                {
                    "call_id": context.call_id,
                    "task_id": context.task.task_id,
                    "group_id": context.task.group_id,
                    "agent_id": context.agent.agent_id,
                    "semantic_action": context.semantic_action,
                    "metadata_present": parsed.metadata_present,
                    "parse_errors": list(parsed.errors),
                    "validation_errors": errors,
                    "artifacts": artifact_rows,
                    "review_events": review_rows,
                    "delivery_events": delivery_rows,
                    "accepted_review_event_count": len(accepted_reviews),
                    "accepted_delivery_event_count": len(
                        accepted_deliveries
                    ),
                },
            )
        return _TypedReliabilityProcessing(
            metadata_present=parsed.metadata_present,
            review_events_declared=parsed.review_events_declared,
            delivery_events_declared=parsed.delivery_events_declared,
            accepted_review_events=tuple(accepted_reviews),
            accepted_delivery_events=tuple(accepted_deliveries),
            errors=tuple(errors),
        )

    def _apply_review_conflict_governance(
        self,
        *,
        context: HookCallContext,
        output_text: str,
        state_refs: list[StateRef],
        injected_records: list[_InjectedMemoryRecord],
        typed_processing: _TypedReliabilityProcessing | None = None,
    ) -> Any | None:
        if context.target_kind != "agentchat_agent":
            return None
        if context.method_name not in {"on_messages", "on_messages_stream"}:
            return None

        profile = self.kernel.capability_profiles.get(
            context.agent.agent_id
        )
        capabilities = tuple(
            dict.fromkeys(
                [
                    *context.agent.capabilities,
                    *(
                        tuple(getattr(profile, "capabilities", ()) or ())
                        if profile is not None
                        else ()
                    ),
                ]
            )
        )
        memory_rows = [
            {
                "memory_id": record.ref.memory_id,
                "revision_guard": record.revision_guard,
            }
            for record in injected_records
        ]
        accepted_typed_reviews = (
            typed_processing.accepted_review_events
            if typed_processing is not None
            else ()
        )
        if accepted_typed_reviews:
            event = max(
                accepted_typed_reviews,
                key=lambda item: item.sequence,
            )
            decision = decision_from_typed_review_event(
                event,
                memory_rows=memory_rows,
            )
        elif typed_processing is not None and typed_processing.metadata_present:
            decision = dataclass_replace(
                evaluate_review_conflict(
                    output_text="",
                    semantic_action=context.semantic_action,
                    capabilities=capabilities,
                    memory_rows=memory_rows,
                ),
                decision_source="typed_event_invalid_or_nonreview",
            )
        else:
            decision = evaluate_review_conflict(
                output_text=output_text,
                semantic_action=context.semantic_action,
                capabilities=capabilities,
                memory_rows=memory_rows,
            )
            if (
                decision.authoritative
                and self.legacy_text_review_mutation_enabled
            ):
                decision = dataclass_replace(
                    decision,
                    mutation_authorized=True,
                )
        if not decision.authoritative:
            return None

        deprecation_event_ids: list[str] = []
        deprecated_memory_ids: list[str] = []
        if decision.blocking and decision.mutation_authorized:
            for memory_id in decision.targeted_memory_ids:
                resolved_ref = self.kernel.memory_store.resolve_ref(memory_id)
                if resolved_ref is None or resolved_ref.status not in {
                    "active",
                    "provisional_active",
                }:
                    continue
                event = self._safe_kernel_call(
                    "autogen_review_soft_deprecate",
                    lambda ref=resolved_ref: (
                        self.kernel.memory_store.soft_deprecate(
                            ref,
                            reason=(
                                f"authoritative_review_blocked;"
                                f"task={context.task.task_id};"
                                f"call={context.call_id};"
                                f"summary={decision.blocking_summary[:240]}"
                            ),
                            created_by=context.agent.agent_id,
                        )
                    ),
                )
                event_id = str(getattr(event, "event_id", "") or "")
                if event_id:
                    deprecation_event_ids.append(event_id)
                    deprecated_memory_ids.append(memory_id)

        admission_report = None
        if (
            decision.blocking
            and decision.mutation_authorized
            and state_refs
        ):
            source_pointer = ",".join(ref.state_id for ref in state_refs)
            subject = f"collaboration:{context.task.group_id}"
            claim_card = build_review_blocker_claim(
                decision,
                subject=subject,
                source_pointer=source_pointer,
            )
            admission_report = self._safe_kernel_call(
                "autogen_promote_review_blocker",
                lambda: self.kernel.promote_memory_candidate(
                    task=context.task,
                    round_id=1,
                    mode="runtime_lite",
                    agent=context.agent,
                    summary=decision.blocking_summary,
                    state_refs=state_refs,
                    slot_hint="slot.system.failure_pattern",
                    task_topic=(
                        f"autogen.{_safe_identifier(context.task.group_id)}."
                        "slot.system.failure_pattern"
                    ),
                    candidate_kind="autogen_review_blocker",
                    confidence=0.94,
                    importance_hint=0.90,
                    coverage_score=0.76,
                    claim_cards=[claim_card],
                ),
            )

        self.trace.write(
            "autogen_review_conflict_governance",
            {
                "call_id": context.call_id,
                "task_id": context.task.task_id,
                "group_id": context.task.group_id,
                "agent_id": context.agent.agent_id,
                "semantic_action": context.semantic_action,
                "capabilities": list(capabilities),
                "legacy_text_mutation_enabled": (
                    self.legacy_text_review_mutation_enabled
                ),
                "state_ref_count": len(state_refs),
                **decision.to_dict(),
                "deprecated_memory_ids": deprecated_memory_ids,
                "deprecation_event_ids": deprecation_event_ids,
                "blocker_admission_status": getattr(
                    admission_report,
                    "admission_status",
                    "",
                ),
                "blocker_memory_refs": [
                    _memory_ref_payload(ref)
                    for ref in _admission_memory_refs(admission_report)
                ],
            },
        )
        return admission_report

    def _promote_autogen_output_to_memory(
        self,
        *,
        context: HookCallContext,
        decoded_messages: list[Any],
        text: str,
        state_refs: list[StateRef],
        typed_processing: _TypedReliabilityProcessing | None = None,
    ) -> Any | None:
        if not self.shared_memory_enabled or not state_refs:
            return None
        delivery_assessment = None
        approval_assessment = None
        memory_source_text = text
        resolution_kind = "latest_visible_message"
        candidate_source = ""
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
            memory_source_text = candidate_text
            typed_delivery_event = None
            if typed_processing is not None:
                validated_events = [
                    event
                    for event in typed_processing.accepted_delivery_events
                    if event.status is DeliveryStatus.VALIDATED
                ]
                if validated_events:
                    typed_delivery_event = max(
                        validated_events,
                        key=lambda item: item.sequence,
                    )
            if typed_delivery_event is None:
                with self._memory_lock:
                    cached_delivery = self._typed_delivery_by_task.get(
                        (
                            context.task.group_id,
                            context.task.task_id,
                        )
                    )
                if (
                    cached_delivery is not None
                    and cached_delivery.status is DeliveryStatus.VALIDATED
                ):
                    typed_delivery_event = cached_delivery

            if typed_delivery_event is not None:
                with self._memory_lock:
                    typed_content = self._reliability_artifact_contents.get(
                        typed_delivery_event.artifact.identity
                    )
                if typed_content:
                    candidate_text = typed_content
                    candidate_source = (
                        typed_delivery_event.artifact.source_id
                        or typed_delivery_event.actor_id
                    )
                    memory_source_text = typed_content
                    resolution_kind = "typed_validated_delivery"
                    delivery_assessment = assess_final_delivery(
                        request=context.task.prompt,
                        content=typed_content,
                        source=candidate_source,
                        minimum_body_chars=0,
                        grounding_contexts=grounding_contexts,
                    )
                else:
                    resolution_kind = "typed_delivery_content_missing"
                    delivery_assessment = assess_final_delivery(
                        request=context.task.prompt,
                        content="",
                        source=candidate_source,
                        minimum_body_chars=0,
                        grounding_contexts=grounding_contexts,
                    )
            else:
                delivery_assessment = assess_final_delivery(
                    request=context.task.prompt,
                    content=candidate_text,
                    source=candidate_source,
                    marker=self.final_delivery_marker,
                    require_marker=True,
                    grounding_contexts=grounding_contexts,
                )
                typed_delivery_declared = bool(
                    typed_processing is not None
                    and typed_processing.delivery_events_declared
                )
                if (
                    delivery_assessment.approved_prior_artifact
                    and not typed_delivery_declared
                ):
                    approval_assessment = delivery_assessment
                    resolved = _resolve_approved_prior_artifact(
                        messages=decoded_messages,
                        request=context.task.prompt,
                        marker=self.final_delivery_marker,
                        grounding_contexts=grounding_contexts,
                    )
                    if resolved is not None:
                        (
                            candidate_text,
                            candidate_source,
                            delivery_assessment,
                        ) = resolved
                        memory_source_text = candidate_text
                        resolution_kind = "approved_prior_artifact"
                if typed_delivery_declared:
                    resolution_kind = "typed_delivery_not_validated"
            typed_delivery_required_but_missing = bool(
                typed_processing is not None
                and typed_processing.delivery_events_declared
                and typed_delivery_event is None
            )
            if (
                delivery_assessment.valid
                and not typed_delivery_required_but_missing
            ):
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

        quality_status, _ = classify_memory_quality_envelope(
            confidence=confidence,
            importance_hint=importance,
            coverage_score=coverage,
        )
        disambiguation_policy = (
            "fallback"
            if quality_status == "admitted"
            else "rules_only"
        )
        summary = (
            _decision_preserving_summary(memory_source_text, limit=900)
            if candidate_kind == "autogen_team_final"
            else _memory_summary(decoded_messages, text, limit=900)
        )
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
                    disambiguation_policy=disambiguation_policy,
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
                    "quality_envelope_status": quality_status,
                    "disambiguation_policy": disambiguation_policy,
                    "candidate_id": getattr(report, "candidate_id", ""),
                    "admission_status": getattr(report, "admission_status", ""),
                    "admission_reasons": getattr(report, "admission_reasons", []),
                    "delivery_assessment": (
                        delivery_assessment.to_dict()
                        if delivery_assessment is not None
                        else {}
                    ),
                    "approval_assessment": (
                        approval_assessment.to_dict()
                        if approval_assessment is not None
                        else {}
                    ),
                    "resolution_kind": resolution_kind,
                    "typed_delivery_event_id": (
                        typed_delivery_event.event_id
                        if (
                            context.target_kind == "agentchat_team"
                            and context.method_name == "run_stream"
                            and typed_delivery_event is not None
                        )
                        else ""
                    ),
                    "resolved_candidate_source": candidate_source,
                    "memory_refs": [
                        _memory_ref_payload(ref)
                        for ref in _admission_memory_refs(report)
                    ],
                },
            )
        return report

    def _promote_team_task_source_evidence(
        self,
        *,
        context: HookCallContext,
        task_text: str,
    ) -> Any | None:
        if (
            not self.shared_memory_enabled
            or not self.semantic_disambiguation_enabled
            or context.target_kind != "agentchat_team"
            or context.method_name != "run_stream"
            or not task_text.strip()
        ):
            return None
        source_id = f"{context.call_id}:framework_user_task"
        report = self._safe_kernel_call(
            "autogen_promote_team_task_source_evidence",
            lambda: self.kernel.promote_source_evidence(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                source_id=source_id,
                source_kind="framework_user_task",
                content=task_text,
                task_topic=(
                    f"autogen.{_safe_identifier(context.task.group_id)}."
                    "source_evidence"
                ),
            ),
        )
        if report is not None:
            self.trace.write(
                "autogen_source_evidence_promotion",
                {
                    "call_id": context.call_id,
                    "task_id": context.task.task_id,
                    "group_id": context.task.group_id,
                    "source_id": source_id,
                    "source_kind": "framework_user_task",
                    "candidate_id": getattr(report, "candidate_id", ""),
                    "admission_status": getattr(
                        report,
                        "admission_status",
                        "",
                    ),
                    "admission_reasons": getattr(
                        report,
                        "admission_reasons",
                        [],
                    ),
                    "memory_refs": [
                        _memory_ref_payload(ref)
                        for ref in _admission_memory_refs(report)
                    ],
                },
            )
            self._write_pool_snapshot(context.task)
        return report

    def _write_pool_snapshot(self, task: TaskSpec) -> None:
        snapshot = {
            "task_id": task.task_id,
            "group_id": task.group_id,
            "mode": "runtime_lite",
            "memory_scope_id": self.memory_scope_id,
            "state_pool": self.kernel.state_pool.snapshot(),
            "memory_store": self.kernel.memory_store.snapshot(),
            "capability_profiles": self.kernel.capability_profiles.snapshot(),
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
                "phase": DRIVER_PHASE,
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
        run_binding = self._resolve_call_run_binding(
            target_kind=target_kind,
            call_id=call_id,
        )
        agent = self.describe_agent(instance, target_kind=target_kind)
        team_participants = self.describe_team_participants(
            instance,
            target_kind=target_kind,
        )
        discovered_agents = self.describe_team_agents(
            instance,
            target_kind=target_kind,
        )
        for discovered in discovered_agents:
            self._safe_kernel_call(
                "register_autogen_team_agent",
                lambda descriptor=discovered: self.kernel.register_agent(descriptor),
            )
        self._safe_kernel_call(
            "register_autogen_call_agent",
            lambda: self.kernel.register_agent(agent),
        )
        decoded_messages = self.codec.decode_many({"args": args, "kwargs": kwargs})
        prompt = self.codec.render_text(decoded_messages) or _extract_text(
            {"args": args, "kwargs": kwargs}
        )
        semantic_source_text = _continuity_source_text(
            decoded_messages,
            fallback=prompt,
        )
        continuity_context_reasons = _continuity_requirement_reasons(
            semantic_source_text
        )
        agent_profile = self.kernel.capability_profiles.get(agent.agent_id)
        semantic_action = infer_semantic_action(
            semantic_source_text,
            preferred_actions=(
                agent_profile.preferred_actions if agent_profile is not None else ()
            ),
            method_name=method_name,
            target_kind=target_kind,
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
        task_sequence_index = 0
        task_text = ""
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
                        self._team_task_sequence_by_group[task.group_id] = (
                            self._team_task_sequence_by_group.get(task.group_id, 0)
                            + 1
                        )
                    task_sequence_index = self._team_task_sequence_by_group.get(
                        task.group_id,
                        0,
                    )
                termination = getattr(instance, "_termination_condition", None)
                recorder = getattr(termination, "record_user_task", None)
                if callable(recorder):
                    recorder(task_text.strip())
        else:
            with self._memory_lock:
                task_sequence_index = self._team_task_sequence_by_group.get(
                    task.group_id,
                    0,
                )
        memory_context = self._prepare_shared_memory_context(
            task=task,
            target_kind=target_kind,
            method_name=method_name,
            prompt=prompt,
        )
        continuity_cache_key = (task.group_id, task_sequence_index)
        if target_kind == "agentchat_team" and task_sequence_index:
            if (
                not continuity_context_reasons
                and memory_context.refs
                and task_sequence_index > 1
            ):
                continuity_context_reasons = (
                    self._semantic_dependency_requirement_reasons(
                        task=task,
                        task_sequence_index=task_sequence_index,
                        current_text=task_text or semantic_source_text,
                        memory_context=memory_context,
                    )
                )
            with self._memory_lock:
                self._continuity_reasons_by_group_sequence[
                    continuity_cache_key
                ] = continuity_context_reasons
        elif target_kind == "agentchat_agent" and not continuity_context_reasons:
            with self._memory_lock:
                continuity_context_reasons = (
                    self._continuity_reasons_by_group_sequence.get(
                        continuity_cache_key,
                        (),
                    )
                )
        hook_context = HookCallContext(
            call_id=call_id,
            task=task,
            agent=agent,
            method_name=method_name,
            target_kind=target_kind,
            run_binding=run_binding,
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
            continuity_context_required=bool(continuity_context_reasons),
            continuity_context_reasons=continuity_context_reasons,
            semantic_action=semantic_action,
            task_sequence_index=task_sequence_index,
            input_context_text="\n".join(
                _sanitize_model_visible_content(
                    str(getattr(message, "content_text", "") or "")
                )
                for message in decoded_messages
                if str(getattr(message, "source", "") or "").casefold()
                != "user"
                and str(getattr(message, "content_text", "") or "").strip()
            ),
        )
        self._promote_team_task_source_evidence(
            context=hook_context,
            task_text=task_text,
        )
        if target_kind == "agentchat_agent":
            self._safe_kernel_call(
                "begin_autogen_capability_execution",
                lambda: self.kernel.begin_agent_execution(
                    agent_id=agent.agent_id,
                    memory_keys=(ref.memory_id for ref in memory_context.refs),
                ),
            )
        self._record_framework_run_start(hook_context)
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
                "task_sequence_index": hook_context.task_sequence_index,
                "memory_refs": [
                    _memory_ref_payload(ref) for ref in memory_context.refs
                ],
                "memory_hit_count": len(memory_context.refs),
                "continuity_context_required": bool(continuity_context_reasons),
                "continuity_context_reasons": list(continuity_context_reasons),
                "semantic_action": semantic_action,
                "capability_profile": (
                    agent_profile.to_dict() if agent_profile is not None else {}
                ),
                "retrieved_memory_tokens": _count_tokens(
                    self.token_counter,
                    "\n".join(memory_context.prompt_views),
                ),
                "decoded_messages": [
                    message.to_dict() for message in decoded_messages
                ],
                **(run_binding.trace_fields() if run_binding is not None else {}),
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
        with self._memory_lock:
            injected_records = list(
                self._memory_injections_by_call.get(context.call_id, ())
            )
        adoption_audit_text = "\n".join(
            dict.fromkeys(context.memory_adoption_audit_texts)
        )
        self._record_memory_adoption_feedback(
            context=context,
            output_text=adoption_audit_text or text,
        )
        guard_metadata = dict(context.memory_adoption_guard)
        guard_blocked = guard_metadata.get("status") in {
            "blocked",
            "enforcement_failed",
        }
        state_refs: list[Any] = []
        semantic_state_text = text
        if _has_semantic_payload(decoded_messages, text):
            semantic_state_text = _semantic_autogen_state_text(
                decoded_messages,
                text,
            )
            extracted_state_payloads = (
                structured_state_payloads(
                    semantic_state_text,
                    semantic_action=context.semantic_action,
                )
                if (
                    context.target_kind == "agentchat_team"
                    and context.method_name == "run_stream"
                )
                else []
            )
            written_state_refs = self._safe_kernel_call(
                "write_agent_state",
                lambda: self.kernel.write_agent_state(
                    task=context.task,
                    round_id=1,
                    mode="runtime_lite",
                    agent=context.agent,
                    output=AgentOutput(
                        agent_id=context.agent.agent_id,
                        content=semantic_state_text,
                        metadata={
                            "framework": "autogen",
                            "target_kind": context.target_kind,
                            "method": context.method_name,
                            "native_result_type": type(result).__name__,
                            "memory_adoption_guard": guard_metadata,
                            "memory_adoption_original_text": adoption_audit_text,
                            "semantic_action": context.semantic_action,
                            "structured_state_payloads": (
                                extracted_state_payloads
                            ),
                            "autogen_decoded_messages": [
                                message.to_dict() for message in decoded_messages
                            ],
                        },
                    ),
                ),
            )
            state_refs = list(written_state_refs or [])
        typed_processing = self._process_typed_reliability_metadata(
            context=context,
            decoded_messages=decoded_messages,
            semantic_state_text=semantic_state_text,
            state_refs=list(state_refs),
        )
        state_ref_payload = []
        for state_ref in state_refs:
            ref_payload = self._safe_kernel_call(
                "state_ref_to_dict",
                lambda ref=state_ref: self.kernel.state_pool.ref_to_dict(ref),
            )
            if isinstance(ref_payload, dict):
                state_ref_payload.append(ref_payload)
        admission_report = None
        if not guard_blocked:
            admission_report = self._apply_review_conflict_governance(
                context=context,
                output_text=text,
                state_refs=list(state_refs),
                injected_records=injected_records,
                typed_processing=typed_processing,
            )
            if admission_report is None:
                admission_report = self._promote_autogen_output_to_memory(
                    context=context,
                    decoded_messages=decoded_messages,
                    text=text,
                    state_refs=list(state_refs),
                    typed_processing=typed_processing,
                )
        admitted_refs = _admission_memory_refs(admission_report)
        admitted_memory_refs = [
            _memory_ref_payload(ref) for ref in admitted_refs
        ]
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
                "model_visible_protocol_marker_count": (
                    _protocol_marker_count(text)
                ),
                "model_visible_surface": (
                    "final_output"
                    if context.target_kind == "agentchat_team"
                    else "agent_output"
                ),
                "state_refs": state_ref_payload,
                "memory_refs": admitted_memory_refs,
                "memory_admission_status": getattr(
                    admission_report,
                    "admission_status",
                    "",
                ),
                "memory_adoption_guard": guard_metadata,
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
            memory_refs=admitted_refs,
        )
        self._record_shadow_broadcast_replacement(
            context=context,
            decoded_messages=decoded_messages,
            native_text=text,
            state_refs=list(state_refs or []),
            state_ref_payload=state_ref_payload,
            native_scope="team_output",
        )
        if context.target_kind == "agentchat_agent":
            self._safe_kernel_call(
                "record_autogen_capability_feedback",
                lambda: self.kernel.record_agent_execution(
                    agent_id=context.agent.agent_id,
                    success=(
                        _has_semantic_payload(decoded_messages, text)
                        and not guard_blocked
                    ),
                    schema_valid=bool(text.strip() or decoded_messages),
                    action=context.semantic_action,
                    cost_tokens=(
                        _count_tokens(self.token_counter, context.task.prompt)
                        + _count_tokens(self.token_counter, text)
                    ),
                    latency_ms=(time.perf_counter() - context.started_at) * 1000,
                ),
            )
        self._write_pool_snapshot(context.task)
        self._record_framework_run_end(context)

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
                required_action=context.semantic_action,
                route_candidates=context.team_participants or None,
                routing_mode="advisory",
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
        semantic_text = _semantic_autogen_state_text(
            decoded_messages,
            native_text,
        )
        if not semantic_text:
            if native_text.strip():
                self.trace.write(
                    "autogen_routing_metadata_filtered",
                    {
                        "call_id": context.call_id,
                        "agent_id": context.agent.agent_id,
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_chars": len(native_text),
                        "semantic_chars": 0,
                        "routing_only": True,
                    },
                )
            return
        if semantic_text != native_text.strip():
            self.trace.write(
                "autogen_routing_metadata_filtered",
                {
                    "call_id": context.call_id,
                    "agent_id": context.agent.agent_id,
                    "target_kind": context.target_kind,
                    "method": context.method_name,
                    "native_chars": len(native_text),
                    "semantic_chars": len(semantic_text),
                    "routing_only": False,
                },
            )
        state_refs = self._safe_kernel_call(
            "write_autogen_transport_input_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=semantic_text,
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
                required_action=context.semantic_action,
                route_candidates=context.team_participants or None,
                routing_mode="advisory",
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
                lambda ref=state_ref: self._render_state_prompt_view(
                    state_ref=ref,
                    receiver_id=receiver,
                    context=context,
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
                    content=_semantic_autogen_state_text(
                        decoded_messages,
                        native_content,
                    ),
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
                lambda ref=state_ref: self._render_state_prompt_view(
                    state_ref=ref,
                    receiver_id=receiver,
                    context=context,
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
                required_action=context.semantic_action,
                route_candidates=context.team_participants or None,
                routing_mode="advisory",
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
        rewrite_outcome = _classify_rewrite_outcome(
            applied=applied,
            fallback_reasons=fallback_reasons,
            native_tokens=native_tokens,
        )
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
                **rewrite_outcome,
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
                    lambda ref=state_ref: self._render_state_prompt_view(
                        state_ref=ref,
                        receiver_id=context.agent.agent_id,
                        context=context,
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
                    content=_semantic_autogen_state_text(
                        decoded_messages,
                        native_content,
                    ),
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
                lambda ref=state_ref: self._render_state_prompt_view(
                    state_ref=ref,
                    receiver_id=receiver,
                    context=context,
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
                required_action=context.semantic_action,
                route_candidates=context.team_participants or None,
                routing_mode="advisory",
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
        rewrite_outcome = _classify_rewrite_outcome(
            applied=applied,
            fallback_reasons=fallback_reasons,
            native_tokens=native_tokens,
        )
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
                **rewrite_outcome,
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
                    lambda ref=state_ref: self._render_state_prompt_view(
                        state_ref=ref,
                        receiver_id=receiver,
                        context=context,
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
                    content=_semantic_autogen_state_text(
                        decoded_messages,
                        native_text,
                    ),
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
        continuity_cost_override = _continuity_cost_override_allowed(
            context=context,
            memory_retained=any(
                entry.get("memory_refs") for entry in receiver_entries
            ),
            fallback_reasons=fallback_reasons,
        )
        if continuity_cost_override:
            fallback_reasons = [
                reason
                for reason in fallback_reasons
                if reason not in CONTINUITY_COST_GATE_REASONS
            ]
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
                continuity_cost_override=continuity_cost_override,
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
            continuity_cost_override=continuity_cost_override,
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
            if "AGENTLITE_TEAM_REAL_REWRITE v1" in native_text:
                hydrated = self._hydrate_team_receiver_context_view(
                    context=context,
                    messages=messages,
                    source=source,
                    args=args,
                    kwargs=kwargs,
                    decoded_messages=decoded_messages,
                    native_text=native_text,
                )
                if hydrated is not None:
                    return hydrated
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["already_agentlite_rewritten"],
                native_text=native_text,
            )
            return None
        if self._is_known_rewritten_prompt(decoded_messages):
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
            task_sequence_index=context.task_sequence_index,
            target_cwd=self.context.target_cwd,
        )
        receiver_profile = self.kernel.capability_profiles.get(
            context.agent.agent_id
        )
        receiver_consumer = consumer_context_from_profile(
            receiver_profile,
            consumer_id=context.agent.agent_id,
        )
        receiver_action = context.semantic_action or infer_semantic_action(
            current_team_task or context.task.prompt,
            preferred_actions=receiver_consumer.preferred_actions,
            method_name=context.method_name,
            target_kind=context.target_kind,
        )
        current_candidate = _current_candidate_artifact_view(
            decoded_messages,
            semantic_action=receiver_action,
            capabilities=receiver_consumer.capabilities,
            final_delivery_marker=self.final_delivery_marker,
        )
        chronology_source_view = (
            _without_latest_upstream_message(chronology_view)
            if current_candidate["available"]
            else chronology_view
        )
        minimal_chronology = build_minimal_context_view(
            query=current_team_task or context.task.prompt,
            prompt_views=[chronology_source_view],
            consumer=receiver_consumer,
            action=receiver_action,
        )
        chronology_selection = _select_token_nonexpanding_view(
            self.token_counter,
            source_views=[chronology_source_view],
            candidate_text=minimal_chronology.text,
        )
        compact_chronology_view = (
            chronology_selection.text or chronology_view
        )
        receiver_chronology_view = "\n".join(
            section
            for section in (
                compact_chronology_view,
                str(current_candidate["text"]),
            )
            if section.strip()
        )
        chronology_safety = {
            "current_task_unit_count": minimal_chronology.current_task_unit_count,
            "current_task_units_preserved": (
                chronology_selection.selection_mode == "source_no_expansion"
                or minimal_chronology.current_task_units_preserved
            ),
            "current_task_view_selection_mode": (
                chronology_selection.selection_mode
            ),
            "task_sequence_index": context.task_sequence_index,
            "current_task_identity_anchored": (
                "[current_task_identity]" in receiver_chronology_view
            ),
            "current_candidate_required": bool(
                current_candidate["required"]
            ),
            "current_candidate_available": bool(
                current_candidate["available"]
            ),
            "current_candidate_complete": bool(
                current_candidate["complete"]
            ),
            "current_candidate_source": str(
                current_candidate["source"]
            ),
            "current_candidate_source_chars": len(
                str(current_candidate["content"])
            ),
            "current_candidate_selected_chars": len(
                str(current_candidate["content"])
            )
            if current_candidate["complete"]
            else 0,
            "current_candidate_source_tokens": _count_tokens(
                self.token_counter,
                str(current_candidate["content"]),
            ),
            "current_candidate_selected_tokens": _count_tokens(
                self.token_counter,
                str(current_candidate["content"]),
            )
            if current_candidate["complete"]
            else 0,
            "current_candidate_view_mode": (
                "capability_required_full_latest_artifact"
                if current_candidate["complete"]
                else "not_required"
                if not current_candidate["required"]
                else "required_candidate_missing"
            ),
        }
        state_refs = self._safe_kernel_call(
            "write_autogen_real_rewrite_input_state",
            lambda: self.kernel.write_agent_state(
                task=context.task,
                round_id=1,
                mode="runtime_lite",
                agent=context.agent,
                output=AgentOutput(
                    agent_id=context.agent.agent_id,
                    content=_semantic_autogen_state_text(
                        decoded_messages,
                        native_text,
                    ),
                    metadata={
                        "framework": "autogen",
                        "target_kind": context.target_kind,
                        "method": context.method_name,
                        "native_result_type": "agent_real_rewrite_input",
                        "real_rewrite_input_state": True,
                        "prompt_view_summary": receiver_chronology_view,
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
                lambda ref=state_ref: self._render_state_prompt_view(
                    state_ref=ref,
                    receiver_id=context.agent.agent_id,
                    context=context,
                    budget_chars=max(
                        900,
                        len(receiver_chronology_view) + 256,
                    ),
                ),
            )
            if isinstance(view, str) and view:
                prompt_views.append(view)
        memory_selection = self._select_role_memory_context(
            context=context,
            receiver_id=context.agent.agent_id,
            state_prompt_views=prompt_views,
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
                required_action=context.semantic_action,
                route_candidates=context.team_participants or None,
                routing_mode="advisory",
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
        continuity_cost_override = _continuity_cost_override_allowed(
            context=context,
            memory_retained=bool(memory_refs),
            fallback_reasons=fallback_reasons,
        )
        if continuity_cost_override:
            fallback_reasons = [
                reason
                for reason in fallback_reasons
                if reason not in CONTINUITY_COST_GATE_REASONS
            ]
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
                rewrite_safety=chronology_safety,
                role_memory_selection=memory_selection,
                continuity_cost_override=continuity_cost_override,
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
            rewrite_safety=chronology_safety,
            role_memory_selection=memory_selection,
            continuity_cost_override=continuity_cost_override,
        )
        self._remember_rewritten_prompt(rewritten_content)
        return new_args, new_kwargs

    def _hydrate_team_receiver_context_view(
        self,
        *,
        context: HookCallContext,
        messages: list[Any] | tuple[Any, ...],
        source: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        decoded_messages: list[Any],
        native_text: str,
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        team_payload = next(
            (
                str(getattr(message, "content_text", "") or "")
                for message in decoded_messages
                if "AGENTLITE_TEAM_REAL_REWRITE v1"
                in str(getattr(message, "content_text", "") or "")
            ),
            "",
        )
        receiver_view = _extract_team_receiver_context_view(
            team_payload,
            receiver_id=context.agent.agent_id,
        )
        if not receiver_view:
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=["team_receiver_context_view_missing"],
                native_text=native_text,
            )
            return None
        upstream = [
            (
                str(getattr(message, "source", "") or "unknown"),
                str(getattr(message, "content_text", "") or "").strip(),
            )
            for message in decoded_messages
            if str(getattr(message, "content_text", "") or "").strip()
            and "AGENTLITE_TEAM_REAL_REWRITE v1"
            not in str(getattr(message, "content_text", "") or "")
            and str(getattr(message, "source", "") or "").casefold() != "user"
        ]
        sections = [
            _MODEL_CONTEXT_RESPONSE_BOUNDARY,
            "CURRENT_USER_TASK (highest priority):",
            receiver_view["current_task"],
            _required_evidence_prompt_rule(
                current_task=receiver_view["current_task"],
                evidence_context="\n".join(
                    content for _, content in upstream
                ),
                target_cwd=self.context.target_cwd,
            ),
            "CAPABILITY_PROMPT_VIEW:",
            receiver_view["prompt_view"],
        ]
        wire_envelope = receiver_view.get("wire_envelope", {})
        if upstream:
            latest_source, latest_content = upstream[-1]
            sections.extend(
                [
                    f"LATEST_UPSTREAM_MESSAGE [{latest_source}] (use in full):",
                    latest_content,
                ]
            )
        if len(upstream) > 1:
            prior_source, prior_content = upstream[-2]
            sections.extend(
                [
                    f"PRIOR_UPSTREAM_DIGEST [{prior_source}]:",
                    _head_tail_digest(prior_content, limit=360),
                ]
            )
        rewritten_content = "\n".join(section for section in sections if section)
        replacement_message = _clone_text_message_with_content(
            messages[-1],
            rewritten_content,
        )
        fallback_reasons: list[str] = []
        if replacement_message is None:
            fallback_reasons.append("message_clone_failed")
        if wire_envelope and not _wire_envelope_schema_valid(wire_envelope):
            fallback_reasons.append("schema_invalid")
        native_tokens = _count_tokens(self.token_counter, native_text)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        if native_tokens <= rewritten_tokens:
            fallback_reasons.append("token_not_reduced")
        memory_refs = list(receiver_view.get("memory_refs", []) or [])
        continuity_cost_override = _continuity_cost_override_allowed(
            context=context,
            memory_retained=bool(memory_refs),
            fallback_reasons=fallback_reasons,
        )
        if continuity_cost_override:
            fallback_reasons = [
                reason
                for reason in fallback_reasons
                if reason not in CONTINUITY_COST_GATE_REASONS
            ]
        if fallback_reasons:
            self._record_agent_rewrite_audit(
                context=context,
                applied=False,
                fallback_reasons=fallback_reasons,
                native_text=native_text,
                rewritten_content=rewritten_content,
                memory_refs=memory_refs,
                wire_envelope=wire_envelope,
                prompt_view_text=receiver_view["prompt_view"],
                retrieved_memory_tokens=_count_tokens(
                    self.token_counter,
                    receiver_view.get("memory_prompt_view", ""),
                ),
                rewrite_safety={
                    "team_receiver_context_view_hydration": True,
                    "team_receiver_role_view_hydration": True,
                    "task_sequence_index": context.task_sequence_index,
                    "current_task_units_preserved": bool(
                        receiver_view.get("current_task_units_preserved")
                    ),
                },
                continuity_cost_override=continuity_cost_override,
            )
            return None
        replacement = (
            (replacement_message,)
            if isinstance(messages, tuple)
            else [replacement_message]
        )
        new_args, new_kwargs = _replace_messages_argument(
            args,
            kwargs,
            source=source,
            replacement=replacement,
        )
        self._record_agent_rewrite_audit(
            context=context,
            applied=True,
            fallback_reasons=[],
            native_text=native_text,
            rewritten_content=rewritten_content,
            memory_refs=memory_refs,
            wire_envelope=wire_envelope,
            prompt_view_text=receiver_view["prompt_view"],
            retrieved_memory_tokens=_count_tokens(
                self.token_counter,
                receiver_view.get("memory_prompt_view", ""),
            ),
            rewrite_safety={
                "team_receiver_context_view_hydration": True,
                "team_receiver_role_view_hydration": True,
                "task_sequence_index": context.task_sequence_index,
                "current_task_units_preserved": bool(
                    receiver_view.get("current_task_units_preserved")
                ),
            },
            continuity_cost_override=continuity_cost_override,
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
                    content=_semantic_autogen_state_text(
                        decoded_messages,
                        native_text,
                    ),
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
                lambda ref=state_ref: self._render_state_prompt_view(
                    state_ref=ref,
                    receiver_id=receiver,
                    context=context,
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
                required_action=context.semantic_action,
                route_candidates=context.team_participants or None,
                routing_mode="advisory",
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
                    content=_semantic_autogen_state_text(
                        decoded_messages,
                        native_text,
                    ),
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
                lambda ref=state_ref: self._render_state_prompt_view(
                    state_ref=ref,
                    receiver_id=receiver,
                    context=context,
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
                required_action=context.semantic_action,
                route_candidates=context.team_participants or None,
                routing_mode="advisory",
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
        continuity_cost_override: bool = False,
        role_memory_selection: _MemoryContextSelection | None = None,
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
        rewrite_outcome = _classify_rewrite_outcome(
            applied=applied,
            fallback_reasons=fallback_reasons,
            native_tokens=native_tokens,
        )
        if applied and memory_refs:
            self._register_memory_injection(
                context=context,
                memory_refs=memory_refs,
                injected_prompt_view=prompt_view_text,
            )
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
                **rewrite_outcome,
                "continuity_context_required": (
                    context.continuity_context_required
                ),
                "continuity_context_reasons": list(
                    context.continuity_context_reasons
                ),
                "continuity_cost_override": continuity_cost_override,
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
                "memory_view_mode": (
                    "capability_action_context_view_v1"
                    if role_memory_selection is not None
                    else "native_prompt_view"
                ),
                "consumer_id": (
                    role_memory_selection.consumer_id
                    if role_memory_selection
                    else ""
                ),
                "semantic_action": (
                    role_memory_selection.semantic_action
                    if role_memory_selection
                    else context.semantic_action
                ),
                "capability_profile_version": (
                    role_memory_selection.profile_version
                    if role_memory_selection
                    else 0
                ),
                "capabilities": (
                    list(role_memory_selection.capabilities)
                    if role_memory_selection
                    else []
                ),
                "information_fields": (
                    list(role_memory_selection.information_fields)
                    if role_memory_selection
                    else []
                ),
                "requested_memory_fields": (
                    list(role_memory_selection.requested_fields)
                    if role_memory_selection
                    else []
                ),
                "covered_memory_fields": (
                    list(role_memory_selection.covered_fields)
                    if role_memory_selection
                    else []
                ),
                "missing_memory_fields": (
                    list(role_memory_selection.missing_fields)
                    if role_memory_selection
                    else []
                ),
                "memory_source_view_tokens": (
                    role_memory_selection.role_view_source_tokens
                    if role_memory_selection
                    else 0
                ),
                "minimal_role_view_tokens": retrieved_memory_tokens,
                "memory_role_view_candidate_tokens": (
                    role_memory_selection.role_view_candidate_tokens
                    if role_memory_selection
                    else 0
                ),
                "memory_view_selection_mode": (
                    role_memory_selection.role_view_selection_mode
                    if role_memory_selection
                    else "empty"
                ),
                "memory_no_expansion_fallback": (
                    role_memory_selection.role_view_no_expansion_fallback
                    if role_memory_selection
                    else False
                ),
                "role_view_reduction_ratio": (
                    role_memory_selection.role_view_reduction_ratio
                    if role_memory_selection
                    else 0.0
                ),
                "memory_field_fetch_count": (
                    role_memory_selection.field_fetch_count
                    if role_memory_selection
                    else 0
                ),
                "memory_field_fetch_tokens": (
                    role_memory_selection.field_fetch_tokens
                    if role_memory_selection
                    else 0
                ),
                "memory_field_fetch_state_ids": (
                    role_memory_selection.field_fetch_state_ids
                    if role_memory_selection
                    else []
                ),
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
                "model_visible_protocol_marker_count": (
                    _protocol_marker_count(rewritten_content)
                ),
                "token_delta_native_minus_rewrite": native_tokens - rewritten_tokens,
                "rewritten_preview": _preview(rewritten_content),
                "model_visible_memory_facts": (
                    _model_visible_memory_facts(prompt_view_text)
                    if applied
                    else []
                ),
                "model_response_boundary_applied": bool(
                    applied
                    and rewritten_content.startswith(
                        _MODEL_CONTEXT_RESPONSE_BOUNDARY
                    )
                ),
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
        continuity_cost_override: bool = False,
    ) -> None:
        native_tokens = _count_tokens(self.token_counter, native_text)
        rewritten_tokens = _count_tokens(self.token_counter, rewritten_content)
        receiver_entries = list(receiver_entries or [])
        native_full_broadcast_tokens = native_tokens * len(receiver_entries)
        retrieved_memory_tokens = sum(
            int(entry.get("retrieved_memory_tokens", 0) or 0)
            for entry in receiver_entries
        )
        memory_source_view_tokens = sum(
            int(entry.get("memory_source_view_tokens", 0) or 0)
            for entry in receiver_entries
        )
        minimal_role_view_tokens = sum(
            int(entry.get("minimal_role_view_tokens", 0) or 0)
            for entry in receiver_entries
        )
        memory_role_view_candidate_tokens = sum(
            int(entry.get("memory_role_view_candidate_tokens", 0) or 0)
            for entry in receiver_entries
        )
        memory_no_expansion_fallback_count = sum(
            int(bool(entry.get("memory_no_expansion_fallback")))
            for entry in receiver_entries
        )
        memory_field_fetch_count = sum(
            int(entry.get("memory_field_fetch_count", 0) or 0)
            for entry in receiver_entries
        )
        memory_field_fetch_tokens = sum(
            int(entry.get("memory_field_fetch_tokens", 0) or 0)
            for entry in receiver_entries
        )
        current_task_source_tokens = sum(
            int(entry.get("current_task_source_tokens", 0) or 0)
            for entry in receiver_entries
        )
        current_task_role_view_tokens = sum(
            int(entry.get("current_task_role_view_tokens", 0) or 0)
            for entry in receiver_entries
        )
        current_task_role_view_candidate_tokens = sum(
            int(entry.get("current_task_role_view_candidate_tokens", 0) or 0)
            for entry in receiver_entries
        )
        current_task_no_expansion_fallback_count = sum(
            int(bool(entry.get("current_task_no_expansion_fallback")))
            for entry in receiver_entries
        )
        current_task_fidelity_failure_count = sum(
            int(not bool(entry.get("current_task_units_preserved")))
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
        rewrite_outcome = _classify_rewrite_outcome(
            applied=applied,
            fallback_reasons=fallback_reasons,
            native_tokens=native_tokens,
        )
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
                **rewrite_outcome,
                "continuity_context_required": (
                    context.continuity_context_required
                ),
                "continuity_context_reasons": list(
                    context.continuity_context_reasons
                ),
                "continuity_cost_override": continuity_cost_override,
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
                "memory_view_mode": "capability_action_context_view_v1",
                "memory_source_view_tokens": memory_source_view_tokens,
                "minimal_role_view_tokens": minimal_role_view_tokens,
                "memory_role_view_candidate_tokens": memory_role_view_candidate_tokens,
                "memory_no_expansion_fallback_count": (
                    memory_no_expansion_fallback_count
                ),
                "role_view_reduction_ratio": (
                    round(
                        1 - (minimal_role_view_tokens / memory_source_view_tokens),
                        6,
                    )
                    if memory_source_view_tokens
                    else 0.0
                ),
                "memory_field_fetch_count": memory_field_fetch_count,
                "memory_field_fetch_tokens": memory_field_fetch_tokens,
                "current_task_source_tokens": current_task_source_tokens,
                "current_task_role_view_tokens": current_task_role_view_tokens,
                "current_task_role_view_candidate_tokens": (
                    current_task_role_view_candidate_tokens
                ),
                "current_task_no_expansion_fallback_count": (
                    current_task_no_expansion_fallback_count
                ),
                "current_task_role_view_saved_tokens": (
                    current_task_source_tokens - current_task_role_view_tokens
                ),
                "current_task_role_view_reduction_ratio": (
                    round(
                        1
                        - (
                            current_task_role_view_tokens
                            / current_task_source_tokens
                        ),
                        6,
                    )
                    if current_task_source_tokens
                    else 0.0
                ),
                "current_task_fidelity_failure_count": (
                    current_task_fidelity_failure_count
                ),
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
                    content=_semantic_autogen_state_text(
                        decoded_messages,
                        native_text,
                    ),
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

    def _render_state_prompt_view(
        self,
        *,
        state_ref: Any,
        receiver_id: str,
        context: HookCallContext,
        budget_chars: int = 900,
        action: str = "",
    ) -> str:
        profile = self.kernel.capability_profiles.get(
            _safe_identifier(receiver_id)
        ) or self.kernel.capability_profiles.get(receiver_id)
        capabilities = profile.capabilities if profile is not None else ()
        return self.kernel.state_pool.render_prompt_view(
            state_ref,
            receiver_id,
            budget_chars=budget_chars,
            capabilities=capabilities,
            action=action or context.semantic_action,
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
        with self._memory_lock:
            current_task = self._current_team_task_by_group.get(
                context.task.group_id,
                "",
            )
        current_task = current_task or context.task.prompt
        receiver_profile = self.kernel.capability_profiles.get(
            receiver_plan.declared_receiver
        )
        consumer = consumer_context_from_profile(
            receiver_profile,
            consumer_id=receiver_plan.declared_receiver,
        )
        receiver_action = infer_semantic_action(
            current_task,
            preferred_actions=consumer.preferred_actions,
            method_name=context.method_name,
            target_kind="agentchat_agent",
        )
        state_prompt_views = []
        for state_ref in state_refs:
            view = self._safe_kernel_call(
                "autogen_broadcast_prompt_view",
                lambda ref=state_ref: self._render_state_prompt_view(
                    state_ref=ref,
                    receiver_id=receiver_plan.declared_receiver,
                    context=context,
                    action=receiver_action,
                ),
            )
            if isinstance(view, str) and view:
                state_prompt_views.append(view)
        memory_selection = self._select_role_memory_context(
            context=context,
            receiver_id=receiver_plan.declared_receiver,
            state_prompt_views=state_prompt_views,
            include_memory=include_memory,
            semantic_action=receiver_action,
        )
        memory_refs = memory_selection.refs
        memory_prompt_views = memory_selection.prompt_views
        current_task_source_view = "\n".join(
            section
            for section in (
                "CURRENT_USER_TASK (highest priority):",
                current_task,
                "CURRENT_TASK_IDENTITY_RULE:",
                _current_task_identity_rule(context.task_sequence_index),
                _required_evidence_prompt_rule(
                    current_task=current_task,
                    evidence_context="",
                    target_cwd=self.context.target_cwd,
                ),
            )
            if section
        )
        current_task_view = build_minimal_context_view(
            query=current_task,
            prompt_views=[current_task_source_view],
            consumer=consumer,
            action=memory_selection.semantic_action,
        )
        current_task_selection = _select_token_nonexpanding_view(
            self.token_counter,
            source_views=[current_task_source_view],
            candidate_text=current_task_view.text,
        )
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
                required_action=context.semantic_action,
                route_candidates=context.team_participants or None,
                routing_mode="advisory",
            ),
        )
        if not isinstance(envelope_json, str) or not envelope_json:
            return {}
        envelope_payload = _json_object_or_empty(envelope_json)
        wire_envelope = _build_shadow_wire_envelope(envelope_payload)
        wire_envelope_json = json.dumps(
            wire_envelope, ensure_ascii=False, separators=(",", ":")
        )
        prompt_view_text = "\n".join(
            section
            for section in (
                "CURRENT_TASK_VIEW:",
                current_task_selection.text,
                _join_state_and_memory_views(
                    state_prompt_views,
                    memory_prompt_views,
                ),
            )
            if section.strip()
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
            "memory_view_mode": "capability_action_context_view_v1",
            "consumer_id": memory_selection.consumer_id,
            "semantic_action": memory_selection.semantic_action,
            "capability_profile_version": memory_selection.profile_version,
            "capabilities": list(memory_selection.capabilities),
            "information_fields": list(memory_selection.information_fields),
            "requested_memory_fields": list(memory_selection.requested_fields),
            "covered_memory_fields": list(memory_selection.covered_fields),
            "missing_memory_fields": list(memory_selection.missing_fields),
            "memory_source_view_tokens": memory_selection.role_view_source_tokens,
            "minimal_role_view_tokens": memory_selection.role_view_selected_tokens,
            "memory_role_view_candidate_tokens": (
                memory_selection.role_view_candidate_tokens
            ),
            "memory_view_selection_mode": memory_selection.role_view_selection_mode,
            "memory_no_expansion_fallback": (
                memory_selection.role_view_no_expansion_fallback
            ),
            "role_view_reduction_ratio": memory_selection.role_view_reduction_ratio,
            "memory_field_fetch_count": memory_selection.field_fetch_count,
            "memory_field_fetch_tokens": memory_selection.field_fetch_tokens,
            "memory_field_fetch_state_ids": memory_selection.field_fetch_state_ids,
            "current_task_source_tokens": current_task_selection.source_tokens,
            "current_task_role_view_tokens": current_task_selection.selected_tokens,
            "current_task_role_view_candidate_tokens": (
                current_task_selection.candidate_tokens
            ),
            "current_task_view_selection_mode": current_task_selection.selection_mode,
            "current_task_no_expansion_fallback": (
                current_task_selection.no_expansion_fallback
            ),
            "current_task_role_view_reduction_ratio": current_task_selection.reduction_ratio,
            "current_task_units_preserved": (
                current_task_selection.selection_mode == "source_no_expansion"
                or current_task_view.current_task_units_preserved
            ),
            "task_sequence_index": context.task_sequence_index,
            "current_task_identity_anchored": (
                "[current_task_identity]" in current_task_selection.text
            ),
            "shadow_wire_envelope": wire_envelope,
            "schema_valid": schema_valid,
            "shadow_wire_tokens": _count_tokens(
                self.token_counter,
                wire_envelope_json,
            ),
            "prompt_view_tokens": _count_tokens(
                self.token_counter,
                "\n".join([current_task_selection.text, *state_prompt_views]),
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

    def _select_role_memory_context(
        self,
        *,
        context: HookCallContext,
        receiver_id: str,
        state_prompt_views: list[str],
        include_memory: bool = True,
        semantic_action: str = "",
    ) -> _MemoryContextSelection:
        base = _select_nonredundant_memory_context(
            state_prompt_views=state_prompt_views,
            memory_refs=(list(context.memory_context.refs) if include_memory else []),
            memory_prompt_views=(
                list(context.memory_context.prompt_views) if include_memory else []
            ),
        )
        profile = self.kernel.capability_profiles.get(receiver_id)
        consumer = consumer_context_from_profile(
            profile,
            consumer_id=receiver_id,
        )
        base.consumer_id = receiver_id
        base.profile_version = consumer.profile_version
        base.capabilities = consumer.capabilities
        base.source_prompt_views = list(base.prompt_views)
        if not base.prompt_views:
            return base
        with self._memory_lock:
            current_task = self._current_team_task_by_group.get(
                context.task.group_id,
                "",
            )
        query = current_task or _continuity_source_text([], fallback=context.task.prompt)
        semantic_action = semantic_action or infer_semantic_action(
            query,
            preferred_actions=consumer.preferred_actions,
            method_name=context.method_name,
            target_kind=context.target_kind,
        )
        base.semantic_action = semantic_action
        historical_prompt_views: list[str] = []
        historical_memory_view_ids: list[str] = []
        if requests_historical_state(query):
            for memory_ref in base.refs:
                prompt_view = self._safe_kernel_call(
                    "autogen_memory_historical_prompt_view",
                    lambda ref=memory_ref: (
                        self.kernel.memory_store.render_historical_prompt_view(
                            ref,
                        )
                    ),
                )
                if not str(prompt_view or "").strip():
                    continue
                historical_prompt_views.append(str(prompt_view))
                historical_memory_view_ids.append(
                    str(getattr(memory_ref, "memory_view_id", "") or "")
                )
        initial_prompt_views = [
            *base.prompt_views,
            *historical_prompt_views,
        ]
        initial = build_minimal_context_view(
            query=query,
            prompt_views=initial_prompt_views,
            consumer=consumer,
            action=semantic_action,
        )
        fetched_views: list[str] = []
        fetched_state_ids: list[str] = []
        if initial.missing_fields and context.continuity_context_required:
            fetch_query = field_fetch_query(query, initial.missing_fields)
            for memory_ref in base.refs:
                state_ids = self._safe_kernel_call(
                    "autogen_memory_source_state_ids",
                    lambda ref=memory_ref: self.kernel.memory_store.source_state_ids(ref),
                )
                for state_id in list(state_ids or []):
                    state_ref = self._safe_kernel_call(
                        "autogen_memory_field_state_ref",
                        lambda sid=state_id: self.kernel.state_pool.resolve_ref(sid),
                    )
                    if state_ref is None:
                        continue
                    span = self._safe_kernel_call(
                        "autogen_memory_field_span",
                        lambda ref=state_ref: self.kernel.state_pool.resolve_raw_span(
                            ref,
                            query=fetch_query,
                            max_chunks=1,
                        ),
                    )
                    content = str(getattr(span, "content", "") or "").strip()
                    if not content:
                        continue
                    fetched_views.append(content)
                    fetched_state_ids.append(state_id)
                    self.metrics.record_state_access(
                        task_id=context.task.task_id,
                        round_id=1,
                        mode="runtime_lite",
                        raw_access_count=0,
                        summary_access_count=0,
                        evidence_snippet_access_count=1,
                        access_escalation_count=1,
                        read_lease_acquire_count=1,
                    )
                    if len(fetched_views) >= 2:
                        break
                if len(fetched_views) >= 2:
                    break
        source_prompt_views = [*initial_prompt_views, *fetched_views]
        final_view = build_minimal_context_view(
            query=query,
            prompt_views=source_prompt_views,
            consumer=consumer,
            action=semantic_action,
        )
        selected_view = _select_token_nonexpanding_view(
            self.token_counter,
            source_views=source_prompt_views,
            candidate_text=final_view.text,
        )
        base.source_prompt_views = source_prompt_views
        base.prompt_views = [selected_view.text] if selected_view.text else []
        base.requested_fields = final_view.requested_fields
        base.covered_fields = final_view.covered_fields
        base.missing_fields = final_view.missing_fields
        base.information_fields = final_view.information_fields
        base.role_view_source_chars = len("\n".join(source_prompt_views))
        base.role_view_selected_chars = len(selected_view.text)
        base.role_view_source_tokens = selected_view.source_tokens
        base.role_view_candidate_tokens = selected_view.candidate_tokens
        base.role_view_selected_tokens = selected_view.selected_tokens
        base.role_view_selection_mode = selected_view.selection_mode
        base.role_view_no_expansion_fallback = selected_view.no_expansion_fallback
        base.role_view_reduction_ratio = selected_view.reduction_ratio
        base.field_fetch_count = len(fetched_views) + len(
            historical_prompt_views
        )
        base.field_fetch_tokens = _count_tokens(
            self.token_counter,
            "\n".join([*historical_prompt_views, *fetched_views]),
        )
        base.field_fetch_state_ids = fetched_state_ids
        if fetched_views or historical_prompt_views:
            self.trace.write(
                "autogen_memory_field_fetch",
                {
                    "call_id": context.call_id,
                    "task_id": context.task.task_id,
                    "receiver": receiver_id,
                    "consumer_id": receiver_id,
                    "semantic_action": semantic_action,
                    "capability_profile_version": consumer.profile_version,
                    "capabilities": list(consumer.capabilities),
                    "information_fields": list(final_view.information_fields),
                    "requested_fields": list(initial.requested_fields),
                    "initial_missing_fields": list(initial.missing_fields),
                    "final_missing_fields": list(final_view.missing_fields),
                    "field_fetch_count": (
                        len(fetched_views) + len(historical_prompt_views)
                    ),
                    "field_fetch_tokens": base.field_fetch_tokens,
                    "state_ids": fetched_state_ids,
                    "historical_field_fetch_count": len(
                        historical_prompt_views
                    ),
                    "historical_field_fetch_memory_view_ids": [
                        item
                        for item in historical_memory_view_ids
                        if item
                    ],
                },
            )
        return base

    def record_call_error(
        self, context: HookCallContext | None, error: BaseException
    ) -> None:
        if context is not None:
            self._record_memory_adoption_feedback(
                context=context,
                output_text="",
            )
        self.trace.write(
            "autogen_hooked_call_error",
            {
                "call_id": context.call_id if context else "",
                "error_type": type(error).__name__,
                "error": str(error),
                **(
                    context.run_binding.trace_fields()
                    if context is not None and context.run_binding is not None
                    else {}
                ),
            },
        )
        if context is not None and context.target_kind == "agentchat_agent":
            self._safe_kernel_call(
                "record_autogen_capability_failure",
                lambda: self.kernel.record_agent_execution(
                    agent_id=context.agent.agent_id,
                    success=False,
                    schema_valid=None,
                    action=context.semantic_action,
                    cost_tokens=_count_tokens(
                        self.token_counter,
                        context.task.prompt,
                    ),
                    latency_ms=(time.perf_counter() - context.started_at) * 1000,
                ),
            )
        self._record_framework_run_end(context, error=error)

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
        raw_agent_id = _safe_identifier(str(raw_name))
        agent_id, profile_alias_only = _logical_autogen_agent_id(
            raw_agent_id,
            instance=instance,
            target_kind=target_kind,
        )
        native_class_name = type(instance).__name__
        registry_scope = _autogen_registry_scope(
            target_kind=target_kind,
            native_class_name=native_class_name,
            profile_alias_only=profile_alias_only,
        )
        role = "team" if target_kind == "agentchat_team" else native_class_name
        metadata = _mapping_or_empty(
            getattr(instance, "metadata", None)
            or getattr(instance, "_metadata", None)
        )
        declared_capabilities = _string_sequence(
            metadata.get("capabilities")
            or metadata.get("capability_tags")
        )
        capabilities = tuple(
            dict.fromkeys([*_capabilities_for(target_kind), *declared_capabilities])
        )
        role_description = str(
            getattr(instance, "description", None)
            or getattr(instance, "_description", None)
            or metadata.get("description")
            or ""
        ).strip()
        system_prompt = _autogen_system_prompt(instance)
        tools = tuple(_autogen_tool_descriptors(instance))
        output_type = _autogen_output_type(instance)
        return AgentDescriptor(
            agent_id=agent_id,
            role=role,
            capabilities=capabilities,
            role_description=role_description,
            system_prompt=system_prompt,
            tools=tools,
            preferred_actions=tuple(
                _string_sequence(metadata.get("preferred_actions"))
            ),
            input_preference=tuple(
                _string_sequence(metadata.get("input_preference"))
            ),
            output_types=tuple(
                dict.fromkeys(
                    [
                        *_string_sequence(metadata.get("output_types")),
                        *([output_type] if output_type else []),
                    ]
                )
            ),
            framework_metadata={
                "framework": "autogen",
                "native_class": f"{type(instance).__module__}.{native_class_name}",
                "target_kind": target_kind,
                "accepted_state_types": _string_sequence(
                    metadata.get("accepted_state_types")
                ),
                "message_types": _string_sequence(metadata.get("message_types")),
                "tool_count": len(tools),
                "registry_scope": registry_scope,
                "logical_agent_id": agent_id,
                "framework_instance_id": raw_agent_id,
                "instance_aliases": (
                    [raw_agent_id] if raw_agent_id != agent_id else []
                ),
                "profile_alias_only": profile_alias_only,
            },
        )

    def describe_team_agents(
        self,
        instance: object,
        *,
        target_kind: str,
    ) -> tuple[AgentDescriptor, ...]:
        if target_kind != "agentchat_team":
            return ()
        raw_participants = getattr(instance, "_participants", None)
        if raw_participants is None:
            raw_participants = getattr(instance, "participants", None)
        descriptors = [
            self.describe_agent(participant, target_kind="agentchat_agent")
            for participant in list(raw_participants or [])
        ]
        if descriptors:
            return tuple(descriptors)
        return tuple(
            AgentDescriptor(
                agent_id=name,
                role=name,
                role_description=name,
                framework_metadata={
                    "framework": "autogen",
                    "target_kind": "agentchat_agent",
                    "registry_scope": "business",
                    "discovery_source": "participant_name_only",
                },
            )
            for name in self.describe_team_participants(
                instance,
                target_kind=target_kind,
            )
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
                run_binding_token = None
                last_item: Any = None
                try:
                    context = self.record_call_start(
                        instance=instance,
                        method_name=method_name,
                        target_kind=target_kind,
                        args=args,
                        kwargs=kwargs,
                    )
                    run_binding_token = activate_run_binding(context.run_binding)
                    args, kwargs = self.rewrite_call_arguments_if_safe(
                        context,
                        args,
                        kwargs,
                    )
                    async for item in original(instance, *args, **kwargs):
                        guarded_item = self.guard_call_result_if_needed(
                            context,
                            item,
                        )
                        visible_item = self.restore_call_result_for_display(
                            context,
                            guarded_item,
                        )
                        last_item = visible_item
                        self.record_stream_item(context, visible_item)
                        yield visible_item
                    self.record_call_end(context, last_item)
                except Exception as exc:
                    self.record_call_error(context, exc)
                    raise
                finally:
                    if run_binding_token is not None:
                        reset_run_binding(run_binding_token)

            setattr(asyncgen_wrapper, "__agentlite_wrapped__", True)
            return asyncgen_wrapper

        if inspect.iscoroutinefunction(original):

            @functools.wraps(original)
            async def coroutine_wrapper(
                instance: object, *args: Any, **kwargs: Any
            ) -> Any:
                context: HookCallContext | None = None
                run_binding_token = None
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
                    run_binding_token = activate_run_binding(context.run_binding)
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
                    result = self.guard_call_result_if_needed(context, result)
                    visible_result = self.restore_call_result_for_display(
                        context,
                        result,
                    )
                    self.record_call_end(context, visible_result)
                    return visible_result
                except Exception as exc:
                    if send_message_entered:
                        self._exit_core_send_message(
                            response_rewrite_enabled=response_rewrite_enabled,
                        )
                    self.record_call_error(context, exc)
                    raise
                finally:
                    if run_binding_token is not None:
                        reset_run_binding(run_binding_token)

            setattr(coroutine_wrapper, "__agentlite_wrapped__", True)
            return coroutine_wrapper

        if inspect.isgeneratorfunction(original):

            @functools.wraps(original)
            def generator_wrapper(instance: object, *args: Any, **kwargs: Any):
                context: HookCallContext | None = None
                run_binding_token = None
                last_item: Any = None
                try:
                    context = self.record_call_start(
                        instance=instance,
                        method_name=method_name,
                        target_kind=target_kind,
                        args=args,
                        kwargs=kwargs,
                    )
                    run_binding_token = activate_run_binding(context.run_binding)
                    args, kwargs = self.rewrite_call_arguments_if_safe(
                        context,
                        args,
                        kwargs,
                    )
                    for item in original(instance, *args, **kwargs):
                        guarded_item = self.guard_call_result_if_needed(
                            context,
                            item,
                        )
                        visible_item = self.restore_call_result_for_display(
                            context,
                            guarded_item,
                        )
                        last_item = visible_item
                        self.record_stream_item(context, visible_item)
                        yield visible_item
                    self.record_call_end(context, last_item)
                except Exception as exc:
                    self.record_call_error(context, exc)
                    raise
                finally:
                    if run_binding_token is not None:
                        reset_run_binding(run_binding_token)

            setattr(generator_wrapper, "__agentlite_wrapped__", True)
            return generator_wrapper

        @functools.wraps(original)
        def sync_wrapper(instance: object, *args: Any, **kwargs: Any) -> Any:
            context: HookCallContext | None = None
            run_binding_token = None
            try:
                context = self.record_call_start(
                    instance=instance,
                    method_name=method_name,
                    target_kind=target_kind,
                    args=args,
                    kwargs=kwargs,
                )
                run_binding_token = activate_run_binding(context.run_binding)
                args, kwargs = self.rewrite_call_arguments_if_safe(
                    context,
                    args,
                    kwargs,
                )
                result = original(instance, *args, **kwargs)
                result = self.rewrite_call_result_if_safe(context, result)
                result = self.guard_call_result_if_needed(context, result)
                visible_result = self.restore_call_result_for_display(
                    context,
                    result,
                )
                self.record_call_end(context, visible_result)
                return visible_result
            except Exception as exc:
                self.record_call_error(context, exc)
                raise
            finally:
                if run_binding_token is not None:
                    reset_run_binding(run_binding_token)

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
            "studio_appdir": str(manager.studio_appdir or ""),
            "studio_run_context_module": (
                "autogenstudio.web.managers.run_context.RunContext"
            ),
            "framework_run_binding": "contextvar",
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


def _mapping_or_empty(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _autogen_system_prompt(instance: object) -> str:
    messages = getattr(instance, "_system_messages", None)
    if messages is None:
        raw = getattr(instance, "system_message", None)
        return str(raw or "").strip()
    parts = []
    for message in list(messages or []):
        content = getattr(message, "content", None)
        text = _extract_text(content if content is not None else message).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _autogen_tool_descriptors(instance: object) -> list[dict[str, Any]]:
    raw_tools = [
        *list(getattr(instance, "_tools", None) or []),
        *list(getattr(instance, "_handoff_tools", None) or []),
    ]
    descriptors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in raw_tools:
        name = str(
            getattr(tool, "name", None)
            or getattr(tool, "_name", None)
            or type(tool).__name__
        ).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        metadata = _mapping_or_empty(
            getattr(tool, "metadata", None)
            or getattr(tool, "_metadata", None)
        )
        descriptors.append(
            {
                "tool_id": name,
                "description": str(
                    getattr(tool, "description", None)
                    or getattr(tool, "_description", None)
                    or metadata.get("description")
                    or ""
                ),
                "capability_tags": _string_sequence(
                    metadata.get("capability_tags")
                    or getattr(tool, "capability_tags", None)
                ),
                "supported_actions": _string_sequence(
                    metadata.get("supported_actions")
                    or getattr(tool, "supported_actions", None)
                ),
                "cost_level": float(
                    metadata.get("cost_level")
                    or getattr(tool, "cost_level", 0.0)
                    or 0.0
                ),
            }
        )
    return descriptors


def _autogen_output_type(instance: object) -> str:
    output_type = getattr(instance, "_output_content_type", None)
    if output_type is None:
        return ""
    return str(getattr(output_type, "__name__", None) or output_type).strip()


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


def _build_semantic_disambiguator_from_env(
) -> ControlledSemanticDisambiguator:
    defaults = LlmConfig()
    api_key_env = os.getenv(
        SEMANTIC_DISAMBIGUATION_API_KEY_ENV,
        "",
    ).strip()
    if not api_key_env:
        if os.getenv("MIMO_API_KEY"):
            api_key_env = "MIMO_API_KEY"
        elif os.getenv("OPENAI_API_KEY"):
            api_key_env = "OPENAI_API_KEY"
        else:
            api_key_env = defaults.api_key_env

    auth_scheme = os.getenv(
        SEMANTIC_DISAMBIGUATION_AUTH_SCHEME_ENV,
        "authorization_bearer",
    ).strip()
    if auth_scheme not in {"authorization_bearer", "api_key"}:
        auth_scheme = "authorization_bearer"

    config = LlmConfig(
        provider="openai-compatible-control",
        base_url=(
            os.getenv(SEMANTIC_DISAMBIGUATION_BASE_URL_ENV, "").strip()
            or os.getenv("OPENAI_BASE_URL", "").strip()
            or defaults.base_url
        ),
        model=(
            os.getenv(SEMANTIC_DISAMBIGUATION_MODEL_ENV, "").strip()
            or os.getenv("OPENAI_MODEL", "").strip()
            or defaults.model
        ),
        api_key_env=api_key_env,
        auth_scheme=auth_scheme,
        timeout_seconds=_positive_float_env(
            "OPENAI_TIMEOUT_SECONDS",
            defaults.timeout_seconds,
        ),
        max_retries=_nonnegative_int_env(
            "OPENAI_MAX_RETRIES",
            defaults.max_retries,
        ),
        retry_backoff_seconds=_positive_float_env(
            "OPENAI_RETRY_BACKOFF_SECONDS",
            defaults.retry_backoff_seconds,
        ),
        temperature=0.0,
        top_p=1.0,
    )
    budget = SemanticDisambiguationBudget(
        max_calls_per_task=_positive_int_env(
            SEMANTIC_DISAMBIGUATION_MAX_CALLS_ENV,
            1,
        ),
        max_source_chars=_positive_int_env(
            SEMANTIC_DISAMBIGUATION_MAX_SOURCE_CHARS_ENV,
            8_000,
        ),
        max_candidates_per_call=_positive_int_env(
            SEMANTIC_DISAMBIGUATION_MAX_CANDIDATES_ENV,
            8,
        ),
        max_control_tokens_per_task=_positive_int_env(
            SEMANTIC_DISAMBIGUATION_MAX_TOKENS_ENV,
            2_048,
        ),
    )
    return ControlledSemanticDisambiguator(
        OpenAICompatibleChatClient(config),
        budget=budget,
    )


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _nonnegative_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _fallback_buckets(reasons: Iterable[str]) -> list[str]:
    buckets = []
    for reason in reasons:
        key = str(reason)
        buckets.append(FALLBACK_REASON_BUCKETS.get(key, "unknown_fallback"))
    return sorted(set(buckets))


def _classify_rewrite_outcome(
    *,
    applied: bool,
    fallback_reasons: Iterable[str],
    native_tokens: int,
) -> dict[str, Any]:
    """Separate safe native passthrough from a real rewrite failure."""
    reasons = {str(reason) for reason in fallback_reasons if str(reason)}
    buckets = set(_fallback_buckets(reasons))
    if applied:
        classification = "rewrite_applied"
        eligible = True
    elif native_tokens <= 0 and reasons & {
        "empty_messages",
        "empty_text_payload",
        "empty_team_task_payload",
        "empty_core_message_payload",
        "empty_core_response_payload",
        "missing_core_message_argument",
        "missing_core_response_result",
        "unsupported_core_message_content_field",
        "unsupported_core_response_content_field",
    }:
        classification = "ineligible_control_passthrough"
        eligible = False
    elif reasons and all("token_not_reduced" in reason for reason in reasons):
        classification = "cost_guard_passthrough"
        eligible = True
    elif "cost_gate_failed" in buckets and not any(
        "contract" in bucket or "schema" in bucket for bucket in buckets
    ):
        classification = "cost_guard_passthrough"
        eligible = True
    elif any(
        marker in bucket
        for bucket in buckets
        for marker in (
            "env_guard",
            "dry_run_guard",
            "already_rewritten",
            "control_guard",
            "lineage_guard",
            "unsupported_message_type",
        )
    ):
        classification = "policy_guard_passthrough"
        eligible = False
    else:
        classification = "error_fallback"
        eligible = bool(native_tokens > 0)
    return {
        "rewrite_eligible": eligible,
        "rewrite_outcome": classification,
        "passthrough_classification": classification,
        "error_fallback_required": classification == "error_fallback",
    }


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
    message_type = str(getattr(message, "type", "") or type(message).__name__)
    if message_type != "TextMessage":
        return False
    return isinstance(getattr(message, "content", None), str)


def _text_message_content(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _clone_text_message_with_content(message: Any, content: str) -> Any | None:
    return _clone_message_with_content(message, content)


def _memory_guard_result_text(result: Any) -> str:
    if result is None:
        return ""
    result_type = str(
        _field_value(result, "type") or type(result).__name__
    )
    if any(
        marker in result_type.casefold()
        for marker in ("chunk", "delta", "streaming")
    ):
        return ""
    if isinstance(result, str):
        return result
    if _is_simple_text_message(result):
        return _text_message_content(result)
    chat_message = _field_value(result, "chat_message")
    if _is_simple_text_message(chat_message):
        return _text_message_content(chat_message)
    return ""


def _clone_memory_guard_result(result: Any, content: str) -> Any | None:
    if isinstance(result, str):
        return content
    if _is_simple_text_message(result):
        return _clone_text_message_with_content(result, content)
    chat_message = _field_value(result, "chat_message")
    if not _is_simple_text_message(chat_message):
        return None
    cloned_message = _clone_text_message_with_content(chat_message, content)
    if cloned_message is None:
        return None
    return _clone_message_with_text_field(
        result,
        field_name="chat_message",
        content=cloned_message,
    )


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


def _structured_memory_dependency_reasons(text: str) -> tuple[str, ...]:
    if requests_historical_state(text):
        return ("explicit_historical_state_request",)
    return ()


def _continuity_requirement_reasons(text: str) -> tuple[str, ...]:
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return ()
    return tuple(
        label
        for label, pattern in CONTINUITY_CUE_PATTERNS
        if pattern.search(normalized)
    )


def _continuity_source_text(
    decoded_messages: Iterable[Any],
    *,
    fallback: str,
) -> str:
    messages = list(decoded_messages)
    user_texts = [
        _sanitize_model_visible_content(
            _team_rewrite_current_task(
                str(getattr(message, "content_text", "") or "").strip()
            )
        )
        for message in messages
        if str(getattr(message, "source", "") or "").strip().lower() == "user"
        and str(getattr(message, "content_text", "") or "").strip()
    ]
    if user_texts:
        return user_texts[-1]
    unscoped_texts = [
        _sanitize_model_visible_content(
            _team_rewrite_current_task(
                str(getattr(message, "content_text", "") or "").strip()
            )
        )
        for message in messages
        if not str(getattr(message, "source", "") or "").strip()
        and str(getattr(message, "content_text", "") or "").strip()
    ]
    if unscoped_texts:
        return unscoped_texts[-1]
    return fallback


def _team_rewrite_current_task(text: str) -> str:
    if "AGENTLITE_TEAM_REAL_REWRITE v1" not in text:
        return text
    legacy_match = re.search(
        r"CURRENT_USER_TASK \(highest priority\):\s*\n(.*?)\nreceiver_prompt_views:\s*\n",
        text,
        flags=re.DOTALL,
    )
    if legacy_match:
        return legacy_match.group(1).strip()
    task_view, _ = _split_current_task_role_view(text)
    return task_view or text


def _continuity_cost_override_allowed(
    *,
    context: HookCallContext,
    memory_retained: bool,
    fallback_reasons: Iterable[str],
) -> bool:
    reasons = {str(reason) for reason in fallback_reasons}
    return bool(
        context.continuity_context_required
        and context.continuity_context_reasons
        and memory_retained
        and reasons
        and reasons.issubset(CONTINUITY_COST_GATE_REASONS)
    )


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


def _admission_memory_refs(report: Any) -> list[Any]:
    if report is None:
        return []
    refs = getattr(report, "memory_refs", None)
    if isinstance(refs, list) and refs:
        return list(refs)
    ref = getattr(report, "memory_ref", None)
    return [ref] if ref is not None else []


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
        content = _sanitize_model_visible_content(
            str(getattr(message, "content_text", "") or "")
        )
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
        content = _sanitize_model_visible_content(
            str(getattr(message, "content_text", "") or "")
        )
        source = str(getattr(message, "source", "") or "").strip()
        native_type = str(getattr(message, "native_type", "") or "")
        message_kind = str(getattr(message, "message_kind", "") or "")
        if not content or source == "user" or message_kind != "text":
            continue
        if native_type.endswith("Event") or native_type == "ThoughtEvent":
            continue
        return content, source
    return fallback.strip(), ""


def _resolve_approved_prior_artifact(
    *,
    messages: list[Any],
    request: str,
    marker: str,
    grounding_contexts: Iterable[str],
) -> tuple[str, str, Any] | None:
    visible: list[tuple[str, str]] = []
    for message in messages:
        content = _sanitize_model_visible_content(
            str(getattr(message, "content_text", "") or "")
        )
        source = str(getattr(message, "source", "") or "").strip()
        native_type = str(getattr(message, "native_type", "") or "")
        message_kind = str(getattr(message, "message_kind", "") or "")
        if not content or source.casefold() == "user" or message_kind != "text":
            continue
        if native_type.endswith("Event") or native_type == "ThoughtEvent":
            continue
        visible.append((content, source))

    for content, source in reversed(visible[:-1]):
        assessment = assess_final_delivery(
            request=request,
            content=content,
            source=source,
            marker=marker,
            require_marker=False,
            grounding_contexts=tuple(grounding_contexts),
        )
        if assessment.approved_prior_artifact:
            continue
        if assessment.valid:
            return content, source, assessment
        break
    return None


def _decision_preserving_summary(content: str, *, limit: int) -> str:
    visible = _sanitize_model_visible_content(str(content or ""))
    normalized = " ".join(visible.split())
    if len(normalized) <= limit:
        return normalized

    claims = extract_claim_cards(
        visible,
        subject="validated_team_artifact",
        source_pointer="autogen.team.final",
        default_confidence=0.86,
    )
    priority_slots = (
        "slot.system.design_decision",
        "slot.project.requirement",
    )
    fact_texts: list[str] = []
    for slot_id in priority_slots:
        for claim in claims:
            if str(claim.get("slot_id", "")) != slot_id:
                continue
            if str(claim.get("polarity", "positive")) != "positive":
                continue
            fact = " ".join(
                str(claim.get("raw_text") or claim.get("summary") or "").split()
            )
            if fact and fact not in fact_texts:
                fact_texts.append(fact)
            if len(fact_texts) >= 6:
                break
        if len(fact_texts) >= 6:
            break

    prefix = (
        "Confirmed facts: " + " | ".join(fact_texts)
        if fact_texts
        else ""
    )
    remaining = max(0, limit - len(prefix) - (1 if prefix else 0))
    compact_artifact = _head_tail_digest(normalized, limit=remaining)
    return "\n".join(
        part for part in (prefix, compact_artifact) if part
    )[:limit]


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
    body = re.sub(r"[;；]\s*tags=\[[^\]]*\](?=\s*(?:\n|$))", "", body)
    return body.strip()


def _active_memory_view_body(prompt_view: str) -> str:
    body = _memory_view_body(prompt_view)
    return "\n".join(
        line
        for line in body.splitlines()
        if not line.strip().startswith("[revision_guard")
    ).strip()


def _memory_adoption_evidence(
    *,
    memory_prompt_view: str,
    injected_prompt_view: str,
    current_task_text: str,
    output_text: str,
    memory_id: str,
    memory_view_id: str,
    revision_guard: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify active-memory adoption and superseded-fact contamination."""
    adoption_threshold = 0.68
    current_task_overlap_threshold = adoption_threshold
    normalized_output = _normalize_fact_text(output_text)
    explicit_reference = any(
        identifier and _normalize_fact_text(identifier) in normalized_output
        for identifier in (memory_id, memory_view_id)
    )
    if (
        isinstance(revision_guard, Mapping)
        and str(revision_guard.get("schema_version") or "").startswith("ccf.v2")
        and revision_guard.get("active_facts")
    ):
        return _structured_memory_adoption_evidence(
            revision_guard=revision_guard,
            current_task_text=current_task_text,
            output_text=output_text,
            explicit_reference=explicit_reference,
        )

    active_body = _active_memory_view_body(memory_prompt_view)
    source_units = _adoption_fact_units(active_body)
    candidate_units = [
        unit
        for unit in source_units
        if _fact_support_score(unit, injected_prompt_view) >= 0.68
    ]
    matched: list[tuple[str, float, float]] = []
    excluded_as_current_task: list[tuple[str, float]] = []
    for unit in candidate_units:
        baseline_score = _fact_support_score(unit, current_task_text)
        if baseline_score >= current_task_overlap_threshold:
            excluded_as_current_task.append((unit, baseline_score))
            continue
        output_score = _fact_support_score(unit, output_text)
        if output_score >= adoption_threshold:
            matched.append((unit, output_score, baseline_score))

    adopted = explicit_reference or bool(matched)
    conflict_evidence = _revision_conflict_evidence(
        active_text=active_body,
        historical_claims=(
            revision_guard.get("historical_claims", [])
            if isinstance(revision_guard, Mapping)
            else []
        ),
        output_text=output_text,
    )
    historical_conflict = bool(conflict_evidence["matched_historical_fact_count"])
    if matched and historical_conflict:
        status = "mixed"
    elif historical_conflict:
        status = "wrong"
    elif adopted:
        status = "useful"
    else:
        status = "unassessed"
    return {
        "adopted": adopted,
        "status": status,
        "explicit_reference": explicit_reference,
        "attribution_threshold": adoption_threshold,
        "current_task_overlap_threshold": current_task_overlap_threshold,
        "current_task_fingerprint": _text_fingerprint(current_task_text),
        "current_task_fact_count": len(_adoption_fact_units(current_task_text)),
        "source_fact_count": len(source_units),
        "candidate_fact_count": len(candidate_units),
        "not_injected_fact_count": len(source_units) - len(candidate_units),
        "current_task_duplicate_fact_count": len(excluded_as_current_task),
        "current_task_duplicate_fact_fingerprints": [
            hashlib.sha256(unit.encode("utf-8")).hexdigest()[:16]
            for unit, _ in excluded_as_current_task[:5]
        ],
        "current_task_duplicate_fact_previews": [
            _preview(unit, limit=120)
            for unit, _ in excluded_as_current_task[:3]
        ],
        "current_task_duplicate_scores": [
            round(score, 6) for _, score in excluded_as_current_task[:5]
        ],
        "matched_fact_count": len(matched),
        "matched_fact_fingerprints": [
            hashlib.sha256(unit.encode("utf-8")).hexdigest()[:16]
            for unit, _, _ in matched[:5]
        ],
        "matched_fact_previews": [
            _preview(unit, limit=120) for unit, _, _ in matched[:3]
        ],
        "match_scores": [round(score, 6) for _, score, _ in matched[:5]],
        "current_task_match_scores": [
            round(score, 6) for _, _, score in matched[:5]
        ],
        "attribution_margins": [
            round(output_score - baseline_score, 6)
            for _, output_score, baseline_score in matched[:5]
        ],
        **conflict_evidence,
    }


def _revision_conflict_evidence(
    *,
    active_text: str,
    historical_claims: Any,
    output_text: str,
) -> dict[str, Any]:
    active_units = _adoption_fact_units(active_text)
    historical_units: list[str] = []
    historical_claim_ids: list[str] = []
    excluded_legacy_claim_ids: list[str] = []
    if isinstance(historical_claims, list):
        for claim in historical_claims:
            if not isinstance(claim, Mapping):
                continue
            claim_id = str(claim.get("claim_id", "") or "")
            if claim_id:
                historical_claim_ids.append(claim_id)
            if bool(claim.get("exclude_from_negative_attribution", False)):
                if claim_id:
                    excluded_legacy_claim_ids.append(claim_id)
                continue
            summary = str(claim.get("summary", "") or claim.get("value", "") or "")
            historical_units.extend(_adoption_fact_units(summary))

    matched: list[tuple[str, str, float]] = []
    for historical_unit in dict.fromkeys(historical_units):
        if any(
            _fact_support_score(historical_unit, active_unit) >= 0.92
            for active_unit in active_units
        ):
            continue
        conflicting_active = next(
            (
                active_unit
                for active_unit in active_units
                if _facts_have_revision_conflict(historical_unit, active_unit)
            ),
            "",
        )
        if not conflicting_active:
            continue
        output_score = _fact_support_score(historical_unit, output_text)
        if output_score < 0.68:
            continue
        if _historical_fact_is_negated(historical_unit, output_text):
            continue
        matched.append((historical_unit, conflicting_active, output_score))

    return {
        "revision_guard_present": bool(historical_claim_ids),
        "historical_claim_count": len(historical_claim_ids),
        "excluded_legacy_historical_claim_count": len(
            excluded_legacy_claim_ids
        ),
        "excluded_legacy_historical_claim_fingerprints": [
            hashlib.sha256(claim_id.encode("utf-8")).hexdigest()[:16]
            for claim_id in excluded_legacy_claim_ids[:5]
        ],
        "historical_claim_fingerprints": [
            hashlib.sha256(claim_id.encode("utf-8")).hexdigest()[:16]
            for claim_id in historical_claim_ids[:5]
        ],
        "historical_fact_count": len(dict.fromkeys(historical_units)),
        "matched_historical_fact_count": len(matched),
        "matched_historical_fact_fingerprints": [
            hashlib.sha256(unit.encode("utf-8")).hexdigest()[:16]
            for unit, _, _ in matched[:5]
        ],
        "matched_historical_fact_previews": [
            _preview(unit, limit=120) for unit, _, _ in matched[:3]
        ],
        "conflicting_active_fact_fingerprints": [
            hashlib.sha256(unit.encode("utf-8")).hexdigest()[:16]
            for _, unit, _ in matched[:5]
        ],
        "historical_output_match_scores": [
            round(score, 6) for _, _, score in matched[:5]
        ],
    }


def _structured_memory_adoption_evidence(
    *,
    revision_guard: Mapping[str, Any],
    current_task_text: str,
    output_text: str,
    explicit_reference: bool,
) -> dict[str, Any]:
    subject = str(revision_guard.get("subject") or "project:current")
    output_claims = extract_claim_cards(
        output_text,
        subject=subject,
        source_pointer="agent_output",
        default_confidence=0.8,
    )
    task_claims = extract_claim_cards(
        current_task_text,
        subject=subject,
        source_pointer="current_task",
        default_confidence=0.95,
    )
    active_facts = _unique_structured_facts(
        revision_guard.get("active_facts", [])
    )
    active_fact_identities = {
        _structured_fact_value_identity(fact) for fact in active_facts
    }
    historical_facts = [
        fact
        for fact in _unique_structured_facts(
            revision_guard.get("historical_facts", [])
        )
        if _structured_fact_value_identity(fact) not in active_fact_identities
    ]

    matched_active: list[tuple[dict[str, Any], dict[str, Any]]] = []
    observed_active: list[tuple[dict[str, Any], dict[str, Any]]] = []
    matched_historical: list[tuple[dict[str, Any], dict[str, Any]]] = []
    excluded_current: list[dict[str, Any]] = []
    excluded_historical_current: list[dict[str, Any]] = []
    authorized_historical: list[dict[str, Any]] = []
    for fact in active_facts:
        current_match = _matching_structured_claim(
            fact,
            task_claims,
        ) or _matching_structured_fact_literal(fact, current_task_text)
        output_match = _matching_structured_claim(
            fact,
            output_claims,
        ) or _matching_structured_fact_literal(fact, output_text)
        if output_match is not None and str(output_match.get("polarity")) != "negative":
            observed_active.append((fact, output_match))
        if current_match is not None:
            excluded_current.append(fact)
            continue
        if output_match is not None and str(output_match.get("polarity")) != "negative":
            matched_active.append((fact, output_match))

    for fact in historical_facts:
        current_match = _matching_structured_claim(
            fact,
            task_claims,
        ) or _matching_structured_fact_literal(fact, current_task_text)
        if current_match is not None:
            # This value is grounded directly in the current user request.
            # It is therefore not evidence of adopting stale memory, even
            # though the same value is historical in the MemoryView.
            excluded_historical_current.append(fact)
            continue
        output_match = _matching_structured_claim(
            fact,
            output_claims,
        ) or _matching_structured_fact_literal(fact, output_text)
        if output_match is None or str(output_match.get("polarity")) == "negative":
            continue
        if (
            requests_historical_state(current_task_text)
            and any(
                _same_structured_fact_slot(active_fact, fact)
                for active_fact, _ in observed_active
            )
            and value_has_temporal_status(
                output_text,
                fact.get("value"),
                status="historical",
            )
        ):
            authorized_historical.append(fact)
            continue
        matched_historical.append((fact, output_match))

    active_adopted = explicit_reference or bool(matched_active)
    historical_adopted = bool(matched_historical)
    if active_adopted and historical_adopted:
        status = "mixed"
    elif historical_adopted:
        status = "wrong"
    elif active_adopted:
        status = "useful"
    else:
        status = "unassessed"

    return {
        "adopted": active_adopted or historical_adopted,
        "status": status,
        "explicit_reference": explicit_reference,
        "attribution_threshold": 1.0,
        "current_task_overlap_threshold": 1.0,
        "current_task_fingerprint": _text_fingerprint(current_task_text),
        "current_task_fact_count": len(task_claims),
        "source_fact_count": len(active_facts),
        "candidate_fact_count": len(active_facts),
        "not_injected_fact_count": 0,
        "current_task_duplicate_fact_count": len(excluded_current),
        "current_task_duplicate_fact_fingerprints": [
            _structured_fact_fingerprint(fact)
            for fact in excluded_current[:5]
        ],
        "current_task_duplicate_fact_previews": [
            _structured_fact_preview(fact) for fact in excluded_current[:3]
        ],
        "current_task_duplicate_scores": [1.0] * min(5, len(excluded_current)),
        "matched_fact_count": len(matched_active),
        "matched_fact_fingerprints": [
            _structured_fact_fingerprint(fact)
            for fact, _ in matched_active[:5]
        ],
        "matched_fact_previews": [
            _structured_fact_preview(fact) for fact, _ in matched_active[:3]
        ],
        "match_scores": [1.0] * min(5, len(matched_active)),
        "current_task_match_scores": [0.0] * min(5, len(matched_active)),
        "attribution_margins": [1.0] * min(5, len(matched_active)),
        "revision_guard_present": bool(historical_facts),
        "historical_claim_count": len(historical_facts),
        "historical_claim_fingerprints": [
            _structured_fact_fingerprint(fact)
            for fact in historical_facts[:5]
        ],
        "historical_fact_count": len(historical_facts),
        "historical_current_task_duplicate_fact_count": len(
            excluded_historical_current
        ),
        "historical_current_task_duplicate_fact_fingerprints": [
            _structured_fact_fingerprint(fact)
            for fact in excluded_historical_current[:5]
        ],
        "historical_current_task_duplicate_fact_previews": [
            _structured_fact_preview(fact)
            for fact in excluded_historical_current[:3]
        ],
        "authorized_historical_fact_count": len(authorized_historical),
        "authorized_historical_fact_fingerprints": [
            _structured_fact_fingerprint(fact)
            for fact in authorized_historical[:5]
        ],
        "authorized_historical_fact_previews": [
            _structured_fact_preview(fact)
            for fact in authorized_historical[:3]
        ],
        "matched_historical_fact_count": len(matched_historical),
        "matched_historical_fact_fingerprints": [
            _structured_fact_fingerprint(fact)
            for fact, _ in matched_historical[:5]
        ],
        "matched_historical_fact_previews": [
            _structured_fact_preview(fact)
            for fact, _ in matched_historical[:3]
        ],
        "conflicting_active_fact_fingerprints": [
            _structured_fact_fingerprint(fact)
            for fact, _ in matched_active[:5]
        ],
        "historical_output_match_scores": [1.0]
        * min(5, len(matched_historical)),
        "semantic_key": str(revision_guard.get("semantic_key") or ""),
        "active_value": (
            active_facts[0].get("value") if active_facts else None
        ),
        "historical_values": [
            fact.get("value") for fact in historical_facts
        ],
        "matched_output_spans": [
            str(match.get("raw_text") or match.get("summary") or "")
            for _, match in [*matched_active, *matched_historical][:5]
        ],
        "matched_active_output_spans": [
            str(match.get("raw_text") or match.get("summary") or "")
            for _, match in matched_active[:5]
        ],
        "matched_historical_output_spans": [
            str(match.get("raw_text") or match.get("summary") or "")
            for _, match in matched_historical[:5]
        ],
        "classification_reason": (
            "structured_active_and_historical_values"
            if status == "mixed"
            else "structured_historical_value_only"
            if status == "wrong"
            else "structured_active_value"
            if status == "useful"
            else "structured_value_not_observed"
        ),
        "attribution_mode": "ccf_v2_semantic_key_value_rules",
    }


def _unique_structured_facts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        fact = dict(item)
        key = (
            str(fact.get("slot_id") or ""),
            str(fact.get("scope") or "general"),
            normalized_value(
                fact.get("value"),
                str(fact.get("value_type") or "string"),
                str(fact.get("unit") or ""),
            ),
            str(fact.get("polarity") or "positive"),
        )
        unique[key] = fact
    return list(unique.values())


def _structured_fact_value_identity(
    fact: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    return (
        str(fact.get("slot_id") or ""),
        str(fact.get("scope") or "general"),
        normalized_value(
            fact.get("value"),
            str(fact.get("value_type") or "string"),
            str(fact.get("unit") or ""),
        ),
        _structured_fact_polarity(fact),
    )


def _structured_fact_operator(fact: Mapping[str, Any]) -> str:
    operator = str(fact.get("operator") or "").strip().casefold()
    if operator in {"eq", "ne", "lt", "le", "gt", "ge"}:
        return operator
    return (
        "ne"
        if str(fact.get("polarity") or "positive") == "negative"
        else "eq"
    )


def _structured_fact_polarity(fact: Mapping[str, Any]) -> str:
    return canonical_claim_polarity(_structured_fact_operator(fact))


def _same_structured_fact_slot(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    left_key = str(left.get("semantic_key") or "").strip()
    right_key = str(right.get("semantic_key") or "").strip()
    if left_key and right_key:
        return left_key == right_key
    return (
        str(left.get("slot_id") or "") == str(right.get("slot_id") or "")
        and str(left.get("scope") or "general")
        == str(right.get("scope") or "general")
    )


def _matching_structured_claim(
    fact: Mapping[str, Any],
    claims: list[dict[str, Any]],
) -> dict[str, Any] | None:
    expected_slot = str(fact.get("slot_id") or "")
    expected_scope = str(fact.get("scope") or "general")
    expected_value = normalized_value(
        fact.get("value"),
        str(fact.get("value_type") or "string"),
        str(fact.get("unit") or ""),
    )
    expected_unit = str(fact.get("unit") or "")
    expected_value_type = str(fact.get("value_type") or "string")
    expected_polarity = _structured_fact_polarity(fact)
    for claim in claims:
        if str(claim.get("slot_id") or "") != expected_slot:
            continue
        if str(claim.get("scope") or "general") != expected_scope:
            continue
        actual_value = normalized_value(
            claim.get("value"),
            str(claim.get("value_type") or expected_value_type),
            str(claim.get("unit") or expected_unit),
        )
        if actual_value != expected_value:
            continue
        if expected_polarity == "negative" and str(
            claim.get("polarity") or "positive"
        ) != "negative":
            continue
        return claim
    return None


def _matching_structured_fact_literal(
    fact: Mapping[str, Any],
    text: str,
) -> dict[str, Any] | None:
    """Match a canonical value literally when no domain extractor recognizes it."""
    raw_value = str(fact.get("value") or "").strip()
    if not raw_value or not text.strip():
        return None
    value_type = str(fact.get("value_type") or "string").casefold()
    unit = str(fact.get("unit") or "").strip()
    normalized_text = _normalize_fact_text(text)
    normalized_literal = _normalize_fact_text(raw_value)
    if value_type in {"integer", "number", "float"}:
        number_match = re.search(r"-?\d+(?:\.\d+)?", raw_value.replace(",", ""))
        if number_match is None:
            return None
        number = number_match.group(0)
        if re.search(
            rf"(?<![\d.]){re.escape(number)}(?![\d.])",
            text.replace(",", ""),
        ) is None:
            return None
        if unit and _normalize_fact_text(unit) not in normalized_text:
            return None
    elif value_type == "boolean":
        if normalized_literal not in {"true", "false"}:
            return None
        if re.search(
            rf"(?<![0-9A-Za-z]){re.escape(normalized_literal)}"
            rf"(?![0-9A-Za-z])",
            text.casefold(),
        ) is None:
            return None
    else:
        if len(normalized_literal) < 4 or normalized_literal not in normalized_text:
            return None
    return {
        "raw_text": raw_value,
        "summary": raw_value,
        "polarity": _structured_fact_polarity(fact),
        "operator": _structured_fact_operator(fact),
        "match_mode": "canonical_value_literal",
    }


def _structured_fact_fingerprint(fact: Mapping[str, Any]) -> str:
    raw = "|".join(
        (
            str(fact.get("semantic_key") or ""),
            str(fact.get("slot_id") or ""),
            str(fact.get("scope") or ""),
            str(fact.get("value") or ""),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _structured_fact_preview(fact: Mapping[str, Any]) -> str:
    return (
        f"{fact.get('slot_id', '')}/{fact.get('scope', '')}="
        f"{fact.get('value', '')}"
    )


def _facts_have_revision_conflict(historical: str, active: str) -> bool:
    historical_numbers = set(_number_signature(historical))
    active_numbers = set(_number_signature(active))
    if historical_numbers and active_numbers and historical_numbers != active_numbers:
        return _revision_concept_similarity(historical, active) >= 0.42

    historical_assignments = _assignment_signature(historical)
    active_assignments = _assignment_signature(active)
    for key in historical_assignments.keys() & active_assignments.keys():
        if historical_assignments[key] != active_assignments[key]:
            return True
    return False


def _revision_concept_similarity(left: str, right: str) -> float:
    left_concept = re.sub(r"\d+(?:\.\d+)?", "#", _normalize_fact_text(left))
    right_concept = re.sub(r"\d+(?:\.\d+)?", "#", _normalize_fact_text(right))
    left_grams = _character_ngrams(left_concept, size=2)
    right_grams = _character_ngrams(right_concept, size=2)
    if not left_grams or not right_grams:
        return 0.0
    intersection = len(left_grams & right_grams)
    return intersection / min(len(left_grams), len(right_grams))


def _assignment_signature(text: str) -> dict[str, str]:
    pattern = re.compile(
        r"([a-z_][a-z0-9_.-]{1,48})\s*"
        r"(?:=|:|：|is\s+|to\s+|设置为|改为|调整为)\s*"
        r"[\"']?([a-z0-9_.-]{1,48})",
        re.IGNORECASE,
    )
    return {
        _normalize_fact_text(key): _normalize_fact_text(value)
        for key, value in pattern.findall(text)
    }


def _historical_fact_is_negated(historical: str, output_text: str) -> bool:
    scalars = list(_number_signature(historical))
    scalars.extend(_assignment_signature(historical).values())
    if not scalars:
        return False
    lowered = output_text.casefold()
    negation_cues = (
        "不是",
        "并非",
        "而非",
        "不再",
        "旧值",
        "过期",
        "已废弃",
        "not ",
        "instead of",
        "obsolete",
        "superseded",
    )
    matched_occurrence = False
    for scalar in dict.fromkeys(scalars):
        for match in re.finditer(re.escape(str(scalar).casefold()), lowered):
            matched_occurrence = True
            prefix = lowered[max(0, match.start() - 18) : match.start()]
            if not any(cue in prefix for cue in negation_cues):
                return False
    return matched_occurrence


def _text_fingerprint(text: str) -> str:
    normalized = _normalize_fact_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _adoption_fact_units(text: str) -> list[str]:
    without_markdown = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|[-*])\s*", "", text)
    parts = re.split(r"(?:\r?\n)+|(?<=[。！？!?；;，,])", without_markdown)
    units = [_normalize_fact_text(part) for part in parts]
    return list(dict.fromkeys(unit for unit in units if len(unit) >= 8))


def _fact_support_score(memory_unit: str, candidate_text: str) -> float:
    candidate = _normalize_fact_text(candidate_text)
    if not memory_unit or not candidate:
        return 0.0
    if memory_unit in candidate:
        return 1.0
    memory_numbers = set(_number_signature(memory_unit))
    candidate_numbers = set(_number_signature(candidate))
    if memory_numbers and not memory_numbers.issubset(candidate_numbers):
        return 0.0
    memory_grams = _character_ngrams(memory_unit, size=3)
    candidate_grams = _character_ngrams(candidate, size=3)
    if not memory_grams or not candidate_grams:
        return 0.0
    ngram_score = len(memory_grams & candidate_grams) / len(memory_grams)
    memory_chars = set(memory_unit)
    candidate_chars = set(candidate)
    character_score = (
        len(memory_chars & candidate_chars) / len(memory_chars)
        if memory_chars
        else 0.0
    )
    shared_anchor = bool(
        _character_ngrams(memory_unit, size=4)
        & _character_ngrams(candidate, size=4)
    )
    shared_bigrams = (
        _character_ngrams(memory_unit, size=2)
        & _character_ngrams(candidate, size=2)
    )
    numeric_concept_anchor = bool(
        memory_numbers
        and any(
            gram not in _GENERIC_NUMERIC_CONCEPT_BIGRAMS
            and not any(character.isdigit() for character in gram)
            and not set(gram) <= _GENERIC_NUMERIC_CONCEPT_CHARS
            for gram in shared_bigrams
        )
    )
    if numeric_concept_anchor:
        return max(0.75, ngram_score, character_score)
    if memory_numbers or shared_anchor:
        return max(ngram_score, character_score)
    return ngram_score


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


def _content_fingerprint(content: str) -> str:
    normalized = str(content or "").strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _protocol_marker_count(content: str) -> int:
    text = str(content or "")
    return sum(
        text.count(marker)
        for marker in (
            "AGENTLITE_",
            "shp_wire=",
            "native_payload_moved_to_state_pool=",
            "native_core_message_content_moved_to_state_pool=",
        )
    )


def _sanitize_model_visible_content(content: str) -> str:
    """Remove runtime transport metadata while preserving business content."""

    visible = str(content or "").strip()
    if not visible:
        return ""
    prompt_marker = "\nprompt_view:\n"
    if _contains_agentlite_rewrite_marker(visible) and prompt_marker in visible:
        visible = visible.split(prompt_marker, 1)[1]

    internal_markers = (
        "AGENTLITE_REAL_REWRITE v1",
        "AGENTLITE_TEAM_REAL_REWRITE v1",
        "AGENTLITE_HANDOFF_TYPED_REWRITE_CANDIDATE v1",
        "AGENTLITE_TOOL_SUMMARY_TYPED_REWRITE_CANDIDATE v1",
        CORE_REWRITE_MARKER,
    )
    internal_prefixes = (
        "native_payload_moved_to_state_pool=",
        "native_core_message_content_moved_to_state_pool=",
        "native_python_message_type_preserved=",
        "shp_wire=",
    )
    lines = []
    for line in visible.splitlines():
        stripped = line.strip()
        if any(marker in stripped for marker in internal_markers):
            continue
        if stripped.startswith(internal_prefixes):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _build_chronology_prompt_view(
    messages: list[Any],
    *,
    current_task: str = "",
    user_task_history: list[str] | tuple[str, ...] = (),
    final_delivery_marker: str = "",
    task_sequence_index: int = 0,
    target_cwd: Path | str = ".",
) -> str:
    """Build a receiver view that preserves task and newest upstream semantics."""

    visible: list[tuple[str, str]] = []
    for message in messages:
        content = _sanitize_model_visible_content(
            str(getattr(message, "content_text", "") or "")
        )
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
    evidence_context = "\n".join(
        [
            *[
                str(task)
                for task in user_task_history
                if str(task).strip() and str(task).strip() != task_text
            ],
            *[content for _, content in upstream],
        ]
    )

    sections: list[str] = []
    if task_text:
        sections.extend(
            [
                "CURRENT_USER_TASK (highest priority):",
                task_text,
                "CURRENT_TASK_IDENTITY_RULE:",
                _current_task_identity_rule(task_sequence_index),
                _required_evidence_prompt_rule(
                    current_task=task_text,
                    evidence_context=evidence_context,
                    target_cwd=target_cwd,
                ),
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
                f"PRIOR_UPSTREAM_MESSAGE [{previous_source}]:",
                previous_content,
            ]
        )
    if sections:
        return "\n".join(sections)
    return ""


def _current_task_identity_rule(task_sequence_index: int) -> str:
    sequence = (
        f" interaction #{task_sequence_index}"
        if task_sequence_index > 0
        else ""
    )
    return (
        "[current_task_identity] The exact CURRENT_USER_TASK above is"
        f"{sequence} and is the only current task. Historical labels and "
        "agent-generated ordinals are context only; never rename, renumber, "
        "or replace the current task."
    )


def _required_evidence_contract(
    *,
    current_task: str,
    evidence_context: str,
    target_cwd: Path | str,
) -> dict[str, Any]:
    task_text = str(current_task or "")
    fallback_match = _EXPLICIT_EVIDENCE_FALLBACK_RE.search(task_text)
    artifact_refs = list(
        dict.fromkeys(
            match.group("ref").replace("\\", "/")
            for match in _REQUIRED_ARTIFACT_REF_RE.finditer(task_text)
        )
    )
    if fallback_match is None or not artifact_refs:
        return {
            "required": False,
            "artifact_refs": artifact_refs,
            "available_artifacts": [],
            "unavailable_artifacts": [],
            "fallback_value": "",
        }

    root = Path(target_cwd).expanduser().resolve()
    available: list[str] = []
    unavailable: list[str] = []
    context_text = str(evidence_context or "")
    for artifact_ref in artifact_refs:
        supplied = _artifact_content_supplied(
            artifact_ref,
            context_text,
        )
        candidate = (root / artifact_ref).resolve()
        workspace_file = (
            candidate.is_relative_to(root)
            and candidate.is_file()
        )
        if supplied or workspace_file:
            available.append(artifact_ref)
        else:
            unavailable.append(artifact_ref)
    return {
        "required": bool(unavailable),
        "artifact_refs": artifact_refs,
        "available_artifacts": available,
        "unavailable_artifacts": unavailable,
        "fallback_value": fallback_match.group("value"),
    }


def _artifact_content_supplied(
    artifact_ref: str,
    evidence_context: str,
) -> bool:
    basename = Path(artifact_ref).name
    marker = re.compile(
        rf"(?:BEGIN\s+{re.escape(basename)}|"
        rf"{re.escape(basename)}\s*(?:内容|content)\s*[:：])",
        re.IGNORECASE,
    )
    return bool(marker.search(str(evidence_context or "")))


def _required_evidence_prompt_rule(
    *,
    current_task: str,
    evidence_context: str,
    target_cwd: Path | str,
) -> str:
    contract = _required_evidence_contract(
        current_task=current_task,
        evidence_context=evidence_context,
        target_cwd=target_cwd,
    )
    if not contract["required"]:
        return ""
    artifacts = ", ".join(contract["unavailable_artifacts"])
    return "\n".join(
        (
            "REQUIRED_EVIDENCE_PREFLIGHT:",
            (
                "The following task-required artifacts are not available in "
                f"the supplied context or workspace: {artifacts}."
            ),
            "Do not claim they were read, loaded, or verified.",
            (
                "Follow the user's explicit evidence-insufficient fallback: "
                f"{contract['fallback_value']}."
            ),
            (
                "Produce a complete evidence-insufficient deliverable now. "
                "Do not merely request the artifact, delegate the correction, "
                "or describe what a later agent should do."
            ),
            (
                "The deliverable must state the fallback decision, list the "
                "missing artifact, distinguish unavailable from verified "
                "evidence, and state the resulting uncertainty."
            ),
        )
    )


def _required_evidence_assessment(
    *,
    current_task: str,
    evidence_context: str,
    output_text: str,
    target_cwd: Path | str,
) -> dict[str, Any]:
    contract = _required_evidence_contract(
        current_task=current_task,
        evidence_context=evidence_context,
        target_cwd=target_cwd,
    )
    if not contract["required"]:
        return {
            **contract,
            "blocked": False,
            "claimed_unavailable_artifacts": [],
            "conflicting_decision_values": [],
        }

    output = str(output_text or "")
    claimed: list[str] = []
    for artifact_ref in contract["unavailable_artifacts"]:
        basename = Path(artifact_ref).name
        for match in re.finditer(re.escape(basename), output, re.IGNORECASE):
            prefix = output[max(0, match.start() - 48) : match.start()]
            if not _EVIDENCE_READ_CLAIM_RE.search(prefix):
                continue
            if re.search(
                r"(?:未|没有|无法|尚未|不能|不可|not|never|unable)"
                r".{0,20}$",
                prefix,
                re.IGNORECASE,
            ):
                continue
            claimed.append(artifact_ref)
            break

    fallback = str(contract["fallback_value"])
    decisions = [
        claim
        for claim in extract_claim_cards(
            output,
            subject="project:current",
            source_pointer="required_evidence_guard",
            default_confidence=0.9,
        )
        if (
            str(claim.get("scope") or "").startswith("decision.")
            or str(claim.get("scope") or "").startswith("config.decision")
        )
        and str(claim.get("polarity") or "positive") != "negative"
    ]
    conflicting_values = [
        str(claim.get("value") or "")
        for claim in decisions
        if str(claim.get("value") or "").casefold() != fallback.casefold()
    ]
    return {
        **contract,
        "blocked": bool(claimed or conflicting_values),
        "claimed_unavailable_artifacts": list(dict.fromkeys(claimed)),
        "conflicting_decision_values": list(
            dict.fromkeys(conflicting_values)
        ),
    }


def _required_evidence_fallback_artifact(
    *,
    fallback_value: str,
    unavailable_artifacts: Iterable[str],
) -> str:
    artifacts = [
        str(item).strip()
        for item in unavailable_artifacts
        if str(item).strip()
    ]
    evidence_rows = "\n".join(
        f"- `{artifact}`: unavailable; no dependent claim was verified."
        for artifact in artifacts
    )
    return "\n".join(
        (
            "## Evidence-insufficient result",
            "",
            f"- Decision: `{fallback_value}`",
            "- Contract status: `degraded_fallback`",
            "- Schema valid: `false`",
            "",
            "### Evidence assessment",
            evidence_rows or "- Required evidence: unavailable.",
            "",
            "### Uncertainty",
            (
                "The task-required evidence was not supplied in the current "
                "context or workspace. Evidence-dependent classifications, "
                "attributions, and factual conclusions remain unverified."
            ),
            "",
            "### Allowed next step",
            (
                "Provide the missing artifact or an equivalent traceable "
                "source, then retry the evidence-dependent analysis."
            ),
        )
    )


def _current_task_identity_assessment(
    *,
    current_task: str,
    output_text: str,
    task_sequence_index: int,
    user_task_history: Iterable[str] = (),
) -> dict[str, Any]:
    if task_sequence_index <= 0:
        return {
            "blocked": False,
            "expected_identity": "",
            "asserted_identities": [],
            "unsupported_claims": [],
            "asserted_interaction_indices": [],
            "unsupported_interaction_indices": [],
            "inference_basis": "sequence_index_unavailable",
        }

    identity_sources = [
        str(task)
        for task in user_task_history
        if str(task or "").strip()
    ]
    identity_sources.append(str(current_task or ""))
    task_labels = [
        (match.group("prefix"), int(match.group("number")))
        for source in identity_sources
        for match in _TASK_LABEL_RE.finditer(source)
    ]
    prefix_numbers: dict[str, set[int]] = {}
    prefix_display: dict[str, str] = {}
    for prefix, number in task_labels:
        normalized_prefix = prefix.casefold()
        prefix_numbers.setdefault(normalized_prefix, set()).add(number)
        prefix_display.setdefault(normalized_prefix, prefix)
    eligible_prefixes = [
        prefix
        for prefix, numbers in prefix_numbers.items()
        if len(numbers) >= 2
        and all(number <= task_sequence_index for number in numbers)
    ]
    expected_identity = ""
    inference_basis = "task_label_family_ambiguous"
    if len(eligible_prefixes) == 1:
        expected_prefix = prefix_display[eligible_prefixes[0]]
        expected_identity = f"{expected_prefix}{task_sequence_index}"
        inference_basis = (
            "history_grounded_label_family_plus_collaboration_sequence"
        )
    asserted = []
    for pattern in _CURRENT_TASK_ASSERTION_PATTERNS:
        asserted.extend(
            match.group("label")
            for match in pattern.finditer(str(output_text or ""))
        )
    asserted = list(dict.fromkeys(asserted))
    unsupported = [
        label
        for label in asserted
        if expected_identity
        and label.casefold() != expected_identity.casefold()
    ]
    asserted_indices = [
        int(match.group("number"))
        for pattern in _CURRENT_INTERACTION_ASSERTION_PATTERNS
        for match in pattern.finditer(str(output_text or ""))
    ]
    asserted_indices = list(dict.fromkeys(asserted_indices))
    unsupported_indices = [
        number
        for number in asserted_indices
        if number != task_sequence_index
    ]
    return {
        "blocked": bool(unsupported or unsupported_indices),
        "expected_identity": expected_identity,
        "asserted_identities": asserted,
        "unsupported_claims": unsupported,
        "asserted_interaction_indices": asserted_indices,
        "unsupported_interaction_indices": unsupported_indices,
        "inference_basis": inference_basis,
    }


def _requires_current_candidate_artifact(
    *,
    semantic_action: str,
    capabilities: Iterable[str],
) -> bool:
    normalized_action = str(semantic_action or "").strip().upper()
    normalized_capabilities = {
        str(capability or "").strip().casefold()
        for capability in capabilities
        if str(capability or "").strip()
    }
    return (
        normalized_action in CURRENT_CANDIDATE_REQUIRED_ACTIONS
        or bool(
            normalized_capabilities
            & CURRENT_CANDIDATE_REQUIRED_CAPABILITIES
        )
    )


def _current_candidate_artifact_view(
    messages: list[Any],
    *,
    semantic_action: str,
    capabilities: Iterable[str],
    final_delivery_marker: str = "",
) -> dict[str, Any]:
    required = _requires_current_candidate_artifact(
        semantic_action=semantic_action,
        capabilities=capabilities,
    )
    if not required:
        return {
            "required": False,
            "available": False,
            "complete": False,
            "source": "",
            "content": "",
            "text": "",
        }

    candidates: list[tuple[str, str]] = []
    for message in messages:
        source = str(getattr(message, "source", "") or "").strip() or "unknown"
        native_type = str(getattr(message, "native_type", "") or "")
        message_kind = str(getattr(message, "message_kind", "") or "")
        if source.casefold() == "user" or message_kind != "text":
            continue
        if native_type.endswith("Event") or native_type == "ThoughtEvent":
            continue
        content = _sanitize_model_visible_content(
            str(getattr(message, "content_text", "") or "")
        )
        content = _strip_exact_control_line(
            content,
            final_delivery_marker,
        ).strip()
        if content:
            candidates.append((source, content))

    if not candidates:
        return {
            "required": True,
            "available": False,
            "complete": False,
            "source": "",
            "content": "",
            "text": "",
        }
    source, content = candidates[-1]
    text = "\n".join(
        (
            f"CURRENT_CANDIDATE_ARTIFACT [{source}] "
            "(authoritative candidate to validate; use in full):",
            content,
        )
    )
    return {
        "required": True,
        "available": True,
        "complete": True,
        "source": source,
        "content": content,
        "text": text,
    }


def _without_latest_upstream_message(view: str) -> str:
    return re.sub(
        r"(?ms)^LATEST_UPSTREAM_MESSAGE(?:\s*\[[^\]]+\])?"
        r"(?:\s*\([^)]*\))?:\s*\n.*?"
        r"(?=^PRIOR_UPSTREAM_DIGEST(?:\s*\[[^\]]+\])?:|\Z)",
        "",
        str(view or ""),
    ).strip()


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


def _model_visible_memory_facts(prompt_view_text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _MODEL_VISIBLE_TYPED_FACT_RE.finditer(
        str(prompt_view_text or "")
    ):
        try:
            payload = json.loads(match.group("payload"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        fact = {"kind": match.group("kind"), **payload}
        identity = json.dumps(
            fact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if identity in seen:
            continue
        seen.add(identity)
        facts.append(fact)
    return facts


def _build_real_rewrite_content(
    *,
    wire_envelope: dict[str, Any],
    prompt_view_text: str,
) -> str:
    # The SHP envelope remains in trace/audit storage. Only the receiver's
    # prompt-safe view is visible to the model.
    del wire_envelope
    return (
        f"{_MODEL_CONTEXT_RESPONSE_BOUNDARY}\n\n"
        f"{prompt_view_text.strip()}"
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
    for entry in receiver_entries:
        for ref in entry.get("memory_refs", []) or []:
            memory_id = str(ref.get("memory_id", "")) if isinstance(ref, dict) else ""
            if memory_id and memory_id not in seen_memory_ids:
                seen_memory_ids.add(memory_id)
                memory_refs.append(ref)
    manifest = {
        "protocol": "agentlite.team_rewrite.v2",
        "receivers": [entry.get("receiver", "") for entry in receiver_entries],
        "state_refs": state_refs,
        "memory_refs": memory_refs,
        "receiver_wires": [
            {
                "receiver": entry.get("receiver", ""),
                "shp_wire": entry.get("shadow_wire_envelope", {}),
                "memory_refs": entry.get("memory_refs", []) or [],
                "consumer_id": entry.get("consumer_id", ""),
                "semantic_action": entry.get("semantic_action", "HANDLE_TASK"),
                "capability_profile_version": int(
                    entry.get("capability_profile_version", 0) or 0
                ),
                "capabilities": entry.get("capabilities", []) or [],
                "information_fields": entry.get("information_fields", []) or [],
                "current_task_units_preserved": bool(
                    entry.get("current_task_units_preserved")
                ),
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
            entry.get("prompt_view_text", "")
            or ""
        )
        sections.extend(
            [
                f"--- receiver: {receiver}",
                "context_profile: "
                + json.dumps(
                    {
                        "consumer_id": entry.get("consumer_id", receiver),
                        "semantic_action": entry.get(
                            "semantic_action", "HANDLE_TASK"
                        ),
                        "capability_profile_version": int(
                            entry.get("capability_profile_version", 0) or 0
                        ),
                        "capabilities": entry.get("capabilities", []) or [],
                        "information_fields": entry.get(
                            "information_fields", []
                        )
                        or [],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                prompt_view_text,
            ]
        )
    return "\n".join(sections)


def _extract_team_receiver_context_view(
    content: str,
    *,
    receiver_id: str,
) -> dict[str, Any]:
    if "AGENTLITE_TEAM_REAL_REWRITE v1" not in content:
        return {}
    target = _safe_identifier(receiver_id)
    sections = re.finditer(
        r"(?:^|\n)--- receiver:\s*([^\n]+)\n(.*?)(?=\n--- receiver:|\Z)",
        content,
        flags=re.DOTALL,
    )
    selected_text = ""
    for match in sections:
        if _safe_identifier(match.group(1).strip()) == target:
            selected_text = match.group(2).strip()
            break
    if not selected_text:
        return {}
    context_match = re.match(r"context_profile:\s*(\{[^\n]*\})\n?", selected_text)
    context_profile = (
        _json_object_or_empty(context_match.group(1)) if context_match else {}
    )
    # Compatibility only: archived v5.13r payloads used semantic_role.
    legacy_role_match = (
        None
        if context_match
        else re.match(r"semantic_role:\s*([^\n]+)\n?", selected_text)
    )
    prompt_start = (
        context_match.end()
        if context_match
        else legacy_role_match.end()
        if legacy_role_match
        else 0
    )
    prompt_view = selected_text[prompt_start:].strip()
    current_task_view, receiver_prompt_view = _split_current_task_role_view(
        prompt_view
    )
    manifest: dict[str, Any] = {}
    manifest_match = re.search(r"(?m)^broadcast_manifest=(\{.*\})$", content)
    if manifest_match:
        manifest = _json_object_or_empty(manifest_match.group(1))
    receiver_wire = next(
        (
            item
            for item in manifest.get("receiver_wires", [])
            if isinstance(item, dict)
            and _safe_identifier(str(item.get("receiver", ""))) == target
        ),
        {},
    )
    memory_prompt_view = ""
    marker_index = prompt_view.find(SHARED_MEMORY_MARKER)
    if marker_index >= 0:
        memory_prompt_view = prompt_view[marker_index:].strip()
    return {
        "current_task": current_task_view,
        "consumer_id": str(
            context_profile.get("consumer_id")
            or receiver_wire.get("consumer_id")
            or receiver_id
        ),
        "semantic_action": str(
            context_profile.get("semantic_action")
            or receiver_wire.get("semantic_action")
            or "HANDLE_TASK"
        ),
        "capability_profile_version": int(
            context_profile.get("capability_profile_version")
            or receiver_wire.get("capability_profile_version")
            or 0
        ),
        "capabilities": list(
            context_profile.get("capabilities")
            or receiver_wire.get("capabilities")
            or []
        ),
        "information_fields": list(
            context_profile.get("information_fields")
            or receiver_wire.get("information_fields")
            or []
        ),
        "prompt_view": receiver_prompt_view,
        "memory_prompt_view": memory_prompt_view,
        "wire_envelope": dict(receiver_wire.get("shp_wire", {}) or {}),
        "memory_refs": list(receiver_wire.get("memory_refs", []) or []),
        "current_task_units_preserved": bool(
            receiver_wire.get("current_task_units_preserved")
        ),
    }


def _split_current_task_role_view(content: str) -> tuple[str, str]:
    marker = "CURRENT_TASK_VIEW:"
    marker_index = content.find(marker)
    if marker_index < 0:
        return "", content.strip()
    remainder = content[marker_index + len(marker) :].lstrip("\r\n ")
    boundary = re.search(
        r"\n(?=(?:\[(?:artifact|retrieval|embedding|failure)_state:|"
        r"\[state_tombstone:|AGENTLITE_SHARED_MEMORY))",
        remainder,
    )
    if boundary is None:
        return remainder.strip(), ""
    task_view = remainder[: boundary.start()].strip()
    prompt_view = remainder[boundary.start() :].strip()
    return task_view, prompt_view


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
                "memory_view_mode": entry.get("memory_view_mode", ""),
                "consumer_id": entry.get("consumer_id", ""),
                "semantic_action": entry.get("semantic_action", "HANDLE_TASK"),
                "capability_profile_version": int(
                    entry.get("capability_profile_version", 0) or 0
                ),
                "capabilities": entry.get("capabilities", []) or [],
                "information_fields": entry.get("information_fields", []) or [],
                "requested_memory_fields": entry.get(
                    "requested_memory_fields", []
                ),
                "covered_memory_fields": entry.get(
                    "covered_memory_fields", []
                ),
                "missing_memory_fields": entry.get("missing_memory_fields", []),
                "memory_source_view_tokens": int(
                    entry.get("memory_source_view_tokens", 0) or 0
                ),
                "minimal_role_view_tokens": int(
                    entry.get("minimal_role_view_tokens", 0) or 0
                ),
                "memory_role_view_candidate_tokens": int(
                    entry.get("memory_role_view_candidate_tokens", 0) or 0
                ),
                "memory_view_selection_mode": entry.get(
                    "memory_view_selection_mode", ""
                ),
                "memory_no_expansion_fallback": bool(
                    entry.get("memory_no_expansion_fallback")
                ),
                "role_view_reduction_ratio": float(
                    entry.get("role_view_reduction_ratio", 0.0) or 0.0
                ),
                "memory_field_fetch_count": int(
                    entry.get("memory_field_fetch_count", 0) or 0
                ),
                "memory_field_fetch_tokens": int(
                    entry.get("memory_field_fetch_tokens", 0) or 0
                ),
                "memory_field_fetch_state_ids": entry.get(
                    "memory_field_fetch_state_ids", []
                ),
                "current_task_source_tokens": int(
                    entry.get("current_task_source_tokens", 0) or 0
                ),
                "current_task_role_view_candidate_tokens": int(
                    entry.get("current_task_role_view_candidate_tokens", 0) or 0
                ),
                "current_task_view_selection_mode": entry.get(
                    "current_task_view_selection_mode", ""
                ),
                "current_task_no_expansion_fallback": bool(
                    entry.get("current_task_no_expansion_fallback")
                ),
                "current_task_role_view_tokens": int(
                    entry.get("current_task_role_view_tokens", 0) or 0
                ),
                "current_task_role_view_reduction_ratio": float(
                    entry.get("current_task_role_view_reduction_ratio", 0.0)
                    or 0.0
                ),
                "current_task_units_preserved": bool(
                    entry.get("current_task_units_preserved")
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


def _semantic_autogen_state_text(
    decoded_messages: list[Any],
    text: str,
) -> str:
    cleaned_text = _sanitize_autogen_routing_metadata(text)
    if cleaned_text:
        return cleaned_text

    semantic_parts: list[str] = []
    for message in decoded_messages:
        content = str(getattr(message, "content_text", "") or "").strip()
        cleaned_content = _sanitize_autogen_routing_metadata(content)
        if cleaned_content:
            semantic_parts.append(cleaned_content)
        for attribute in ("tool_calls", "tool_results"):
            tool_payload = getattr(message, attribute, None)
            if not tool_payload:
                continue
            cleaned_tool_payload = _sanitize_autogen_routing_metadata(
                _extract_text(tool_payload)
            )
            if cleaned_tool_payload:
                semantic_parts.append(cleaned_tool_payload)
    return "\n".join(dict.fromkeys(semantic_parts)).strip()


def _has_semantic_payload(decoded_messages: list[Any], text: str) -> bool:
    return bool(_semantic_autogen_state_text(decoded_messages, text))


def _sanitize_autogen_routing_metadata(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"DefaultTopicId\([^)]*\)", " ", raw)
    cleaned = re.sub(r"AgentId\([^)]*\)", " ", cleaned)
    cleaned = re.sub(
        r"CancellationToken\s*:\s*<[^>]*>",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"<autogen_core\.[^>]+>",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned_lines: list[str] = []
    for line in cleaned.splitlines():
        line = re.sub(
            r"^\s*[0-9a-f]{8}-[0-9a-f-]{27,}\s*:\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )
        line = re.sub(
            r"(?:AgentId|CancellationToken|DefaultTopicId)\s*:\s*$",
            "",
            line,
            flags=re.IGNORECASE,
        )
        normalized = line.strip()
        if normalized and not _is_autogen_routing_metadata_only(normalized):
            cleaned_lines.append(normalized)
    return "\n".join(cleaned_lines).strip()


def _is_autogen_routing_metadata_only(text: str) -> bool:
    normalized = " ".join(str(text or "").split())
    if not normalized or len(normalized) > 1600:
        return False
    if "DefaultTopicId(" not in normalized and "AgentId(" not in normalized:
        return False
    if re.search(r"[\u4e00-\u9fff]", normalized):
        return False

    residue = re.sub(r"DefaultTopicId\([^)]*\)", " ", normalized)
    residue = re.sub(r"AgentId\([^)]*\)", " ", residue)
    residue = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        " ",
        residue,
        flags=re.IGNORECASE,
    )
    residue = re.sub(
        r"\b(?:SingleThreadedAgentRuntime|RoundRobinGroupChatManager|"
        r"RoundRobinGroupChat|DefaultTopicId|AgentId)\b",
        " ",
        residue,
    )
    residue = re.sub(r"[^a-zA-Z0-9_]+", " ", residue)
    tokens = {
        token.casefold()
        for token in residue.split()
        if token and not token.isdigit()
    }
    routing_tokens = {
        "args",
        "kwargs",
        "message",
        "messages",
        "sender",
        "recipient",
        "target",
        "topic",
        "topic_id",
        "agent",
        "agent_id",
        "type",
        "source",
        "id",
        "cancellationtoken",
        "cancellation_token",
        "autogen_core",
        "object",
        "at",
    }
    return not tokens or tokens.issubset(routing_tokens)


def _logical_autogen_agent_id(
    raw_agent_id: str,
    *,
    instance: object,
    target_kind: str,
) -> tuple[str, bool]:
    native_class_name = type(instance).__name__
    if target_kind != "core_agent" or not native_class_name.endswith(
        "ChatAgentContainer"
    ):
        return raw_agent_id, False
    match = _AUTOGEN_REPEATED_INSTANCE_ID_RE.fullmatch(raw_agent_id)
    if match is None:
        return raw_agent_id, False
    logical_id = _safe_identifier(match.group("logical"))
    return logical_id, logical_id != raw_agent_id


def _autogen_registry_scope(
    *,
    target_kind: str,
    native_class_name: str,
    profile_alias_only: bool,
) -> str:
    if profile_alias_only or target_kind == "agentchat_agent":
        return "business"
    if target_kind in {"agentchat_team", "core_runtime"}:
        return "system"
    if target_kind == "core_agent" and (
        native_class_name.endswith("Manager")
        or native_class_name.endswith("ChatAgentContainer")
    ):
        return "system"
    return "business"


def _safe_identifier(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)
    return cleaned.strip("_") or "autogen_agent"


def _filesystem_identifier(value: str, *, max_chars: int = 16) -> str:
    cleaned = _safe_identifier(value)
    if len(cleaned) <= max_chars:
        return cleaned
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    prefix_chars = max(1, max_chars - len(digest) - 1)
    return f"{cleaned[:prefix_chars]}_{digest}"


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


def _select_token_nonexpanding_view(
    token_counter: TokenCounter,
    *,
    source_views: Iterable[str],
    candidate_text: str,
) -> _TokenBoundViewSelection:
    source_text = "\n".join(
        str(view or "").strip()
        for view in source_views
        if str(view or "").strip()
    ).strip()
    candidate_text = str(candidate_text or "").strip()
    source_tokens = _count_tokens(token_counter, source_text)
    candidate_tokens = _count_tokens(token_counter, candidate_text)

    if not source_text:
        return _TokenBoundViewSelection(
            text=candidate_text,
            source_tokens=0,
            candidate_tokens=candidate_tokens,
            selected_tokens=candidate_tokens,
            selection_mode="capability_minimized" if candidate_text else "empty",
            no_expansion_fallback=False,
        )
    if candidate_text and candidate_tokens < source_tokens:
        return _TokenBoundViewSelection(
            text=candidate_text,
            source_tokens=source_tokens,
            candidate_tokens=candidate_tokens,
            selected_tokens=candidate_tokens,
            selection_mode="capability_minimized",
            no_expansion_fallback=False,
        )
    return _TokenBoundViewSelection(
        text=source_text,
        source_tokens=source_tokens,
        candidate_tokens=candidate_tokens,
        selected_tokens=source_tokens,
        selection_mode="source_no_expansion",
        no_expansion_fallback=bool(candidate_text),
    )


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
