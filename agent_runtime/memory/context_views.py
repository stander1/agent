from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


FIELD_CUES: dict[str, tuple[str, ...]] = {
    "objectives": ("目标", "任务", "需求", "objective", "goal", "request"),
    "candidates": ("候选", "选项", "备选", "candidate", "option", "alternative"),
    "preferences": ("偏好", "倾向", "喜好", "preference", "prefer"),
    "constraints": (
        "约束",
        "限制",
        "预算",
        "时间",
        "要求",
        "必须",
        "constraint",
        "limit",
        "budget",
        "deadline",
        "requirement",
        "must",
    ),
    "decisions": (
        "决定",
        "选择",
        "确认",
        "结论",
        "decision",
        "selected",
        "confirmed",
        "agreed",
    ),
    "evidence": ("证据", "来源", "引用", "评分", "evidence", "source", "citation", "score"),
    "risks": ("风险", "冲突", "问题", "未解决", "risk", "conflict", "issue", "unresolved"),
    "deliverables": ("交付", "输出", "草案", "最终", "deliverable", "output", "draft", "final"),
    "revisions": ("修改", "修订", "调整", "变更", "revise", "revision", "update", "change"),
    "artifacts": (
        "产物",
        "文件",
        "代码",
        "日志",
        "标准输出",
        "标准错误",
        "artifact",
        "file",
        "code",
        "stdout",
        "stderr",
    ),
    "execution": (
        "执行",
        "运行",
        "工具调用",
        "轨迹",
        "重试",
        "execute",
        "runtime",
        "tool call",
        "trace",
        "retry",
    ),
    "coverage": (
        "覆盖",
        "缺失方向",
        "完整性",
        "就绪",
        "coverage",
        "missing area",
        "completeness",
        "readiness",
    ),
    "failures": (
        "失败",
        "错误",
        "异常",
        "阻断",
        "failure",
        "error",
        "exception",
        "blocked",
    ),
    "memory": (
        "记忆",
        "复用",
        "候选记忆",
        "生命周期",
        "memory",
        "reuse",
        "memory candidate",
        "lifecycle",
    ),
}

REQUEST_FIELD_CUES: dict[str, tuple[str, ...]] = {
    "objectives": ("原目标", "既定目标", "上一轮目标", "prior objective", "existing goal"),
    "candidates": ("候选", "选项", "备选", "candidate", "option", "alternative"),
    "preferences": ("偏好", "倾向", "喜好", "preference", "prefer"),
    "constraints": (
        "约束",
        "限制",
        "预算",
        "已确认要求",
        "constraint",
        "limit",
        "budget",
        "confirmed requirement",
    ),
    "decisions": (
        "已决定",
        "已选择",
        "已确认结论",
        "已确认方案",
        "已确认选择",
        "既有结论",
        "上一轮结论",
        "previous decision",
        "prior decision",
        "selected result",
        "confirmed decision",
        "confirmed selection",
    ),
    "evidence": ("证据", "来源", "引用", "评分", "evidence", "source", "citation", "score"),
    "risks": ("风险", "冲突", "未解决", "risk", "conflict", "unresolved"),
    "deliverables": (
        "既有草案",
        "上一版输出",
        "前序交付",
        "previous draft",
        "prior output",
        "existing deliverable",
    ),
    "revisions": ("修改", "修订", "调整", "变更", "revise", "revision", "update", "change"),
    "artifacts": (
        "既有产物",
        "前序文件",
        "代码产物",
        "执行日志",
        "artifact",
        "existing file",
        "code output",
        "execution log",
    ),
    "execution": (
        "执行结果",
        "运行结果",
        "工具结果",
        "重试记录",
        "execution result",
        "tool result",
        "retry history",
    ),
    "coverage": ("覆盖情况", "缺失方向", "是否就绪", "coverage", "missing area", "readiness"),
    "failures": ("失败原因", "错误信息", "异常记录", "failure", "error", "exception"),
    "memory": ("前序记忆", "既有记忆", "记忆候选", "memory", "prior memory"),
}

CAPABILITY_FIELDS: dict[str, tuple[str, ...]] = {
    "task_decomposition": ("objectives", "candidates", "constraints", "coverage"),
    "routing": ("objectives", "constraints", "coverage", "failures", "risks"),
    "orchestration": ("objectives", "coverage", "failures", "risks"),
    "retrieval": ("objectives", "constraints", "evidence", "coverage", "failures"),
    "evidence_ranking": ("objectives", "evidence", "coverage", "risks"),
    "embedding_generation": ("objectives", "artifacts", "execution"),
    "synthesis": (
        "objectives",
        "preferences",
        "constraints",
        "decisions",
        "evidence",
        "deliverables",
        "revisions",
    ),
    "writing": (
        "objectives",
        "preferences",
        "constraints",
        "decisions",
        "evidence",
        "artifacts",
        "deliverables",
        "revisions",
    ),
    "summarization": ("objectives", "decisions", "evidence", "artifacts", "deliverables"),
    "validation": (
        "constraints",
        "decisions",
        "evidence",
        "risks",
        "failures",
        "deliverables",
        "revisions",
    ),
    "schema_review": ("constraints", "deliverables", "failures", "revisions"),
    "failure_review": ("failures", "execution", "artifacts", "risks", "revisions"),
    "execution": ("objectives", "constraints", "artifacts", "execution", "failures"),
    "coding": (
        "objectives",
        "constraints",
        "decisions",
        "artifacts",
        "execution",
        "failures",
        "revisions",
    ),
    "tool_use": ("objectives", "constraints", "artifacts", "execution", "failures"),
    "memory_governance": ("memory", "decisions", "evidence", "risks", "coverage"),
    "claim_compaction": ("memory", "decisions", "evidence", "risks"),
    "data_analysis": ("objectives", "evidence", "artifacts", "coverage", "risks"),
    "final_deliverable_draft": ("objectives", "constraints", "decisions", "deliverables"),
    "final_deliverable_review": ("constraints", "evidence", "risks", "deliverables"),
    "memory_use": ("memory", "constraints", "decisions", "evidence"),
}

ACTION_FIELDS: dict[str, tuple[str, ...]] = {
    "DECOMPOSE_TASK": ("objectives", "constraints", "candidates", "coverage"),
    "ROUTE_TASK": ("objectives", "coverage", "failures", "risks"),
    "RETRIEVE_EVIDENCE": ("objectives", "constraints", "evidence", "coverage"),
    "VERIFY_CLAIM": ("decisions", "evidence", "risks"),
    "WRITE_OUTPUT": ("objectives", "constraints", "decisions", "evidence", "deliverables"),
    "MERGE_SUMMARY": ("objectives", "decisions", "evidence", "deliverables"),
    "SUMMARIZE_CONTENT": ("objectives", "evidence", "artifacts"),
    "REVIEW_OUTPUT": ("constraints", "evidence", "risks", "failures", "deliverables"),
    "REVIEW_SCHEMA": ("constraints", "failures", "deliverables"),
    "DIAGNOSE_FAILURE": ("failures", "execution", "artifacts", "risks"),
    "EXECUTE_TOOL": ("objectives", "constraints", "artifacts", "execution", "failures"),
    "RUN_CODE": ("objectives", "constraints", "artifacts", "execution", "failures"),
    "WRITE_CODE": ("objectives", "constraints", "decisions", "artifacts", "revisions"),
    "UPDATE_MEMORY": ("memory", "decisions", "evidence", "risks"),
    "MERGE_MEMORY": ("memory", "decisions", "evidence", "risks"),
    "RESOLVE_CONFLICT": ("memory", "decisions", "evidence", "risks"),
    "ANALYZE_DATA": ("objectives", "evidence", "artifacts", "coverage"),
    "READ_MEMORY": ("memory", "constraints", "decisions", "evidence"),
}

ACTION_CAPABILITY_FOCUS: dict[str, tuple[str, ...]] = {
    "DECOMPOSE_TASK": ("task_decomposition", "orchestration"),
    "ROUTE_TASK": ("routing", "orchestration"),
    "RETRIEVE_EVIDENCE": ("retrieval", "evidence_ranking"),
    "VERIFY_CLAIM": ("evidence_ranking", "validation"),
    "WRITE_OUTPUT": ("writing", "synthesis", "final_deliverable_draft"),
    "MERGE_SUMMARY": ("synthesis", "summarization"),
    "SUMMARIZE_CONTENT": ("summarization",),
    "REVIEW_OUTPUT": ("validation", "final_deliverable_review"),
    "REVIEW_SCHEMA": ("schema_review", "validation"),
    "DIAGNOSE_FAILURE": ("failure_review", "validation"),
    "EXECUTE_TOOL": ("tool_use", "execution"),
    "RUN_CODE": ("coding", "execution"),
    "WRITE_CODE": ("coding",),
    "UPDATE_MEMORY": ("memory_governance",),
    "MERGE_MEMORY": ("memory_governance", "claim_compaction"),
    "RESOLVE_CONFLICT": ("claim_compaction", "memory_governance"),
    "ANALYZE_DATA": ("data_analysis",),
    "READ_MEMORY": ("memory_use",),
}

INPUT_PREFERENCE_FIELDS: dict[str, tuple[str, ...]] = {
    "task_goal": ("objectives",),
    "query": ("objectives",),
    "constraints": ("constraints",),
    "requirements": ("constraints",),
    "candidate_options": ("candidates",),
    "confirmed_decisions": ("decisions",),
    "evidence_snippets": ("evidence",),
    "sources": ("evidence",),
    "scores": ("evidence",),
    "risks": ("risks",),
    "deliverable": ("deliverables",),
    "deliverable_schema": ("deliverables", "constraints"),
    "artifact_refs": ("artifacts",),
    "code_artifacts": ("artifacts",),
    "execution_trace": ("execution",),
    "tool_inputs": ("execution", "artifacts"),
    "failure_state": ("failures",),
    "validation_errors": ("failures",),
    "coverage": ("coverage",),
    "readiness": ("coverage",),
    "promotion_view": ("memory",),
    "memory_candidates": ("memory",),
    "claim_cards": ("memory", "evidence"),
    "conflicts": ("risks", "memory"),
}

ACTION_CUES: dict[str, tuple[str, ...]] = {
    "DECOMPOSE_TASK": ("拆解", "规划步骤", "decompose", "plan tasks"),
    "ROUTE_TASK": ("调度", "分配给", "route", "assign agent"),
    "RETRIEVE_EVIDENCE": ("检索", "搜索", "调研", "retrieve", "search", "research"),
    "VERIFY_CLAIM": ("核验结论", "验证主张", "verify claim", "fact check"),
    "WRITE_OUTPUT": ("撰写", "生成方案", "完成报告", "write", "draft", "deliver"),
    "MERGE_SUMMARY": ("整合", "汇总", "merge", "synthesize"),
    "SUMMARIZE_CONTENT": ("总结", "摘要", "summarize"),
    "REVIEW_OUTPUT": ("审查", "评审", "验收", "review", "audit", "validate"),
    "REVIEW_SCHEMA": ("格式校验", "结构校验", "schema", "format validation"),
    "DIAGNOSE_FAILURE": ("诊断错误", "分析失败", "debug", "diagnose"),
    "EXECUTE_TOOL": ("调用工具", "执行工具", "run tool", "execute tool"),
    "RUN_CODE": ("运行代码", "执行代码", "run code", "execute code"),
    "WRITE_CODE": ("编写代码", "修复代码", "write code", "implement"),
    "UPDATE_MEMORY": ("更新记忆", "写入记忆", "update memory"),
    "MERGE_MEMORY": ("合并记忆", "压缩记忆", "merge memory", "compact memory"),
    "RESOLVE_CONFLICT": ("解决冲突", "消解冲突", "resolve conflict"),
    "ANALYZE_DATA": ("分析数据", "统计", "analyze data", "statistics"),
    "READ_MEMORY": ("读取记忆", "复用记忆", "read memory", "reuse memory"),
}

ROLE_FIELDS: dict[str, tuple[str, ...]] = {
    "planner": ("objectives", "candidates", "preferences", "constraints", "decisions"),
    "writer": (
        "objectives",
        "preferences",
        "constraints",
        "decisions",
        "evidence",
        "deliverables",
        "revisions",
    ),
    "reviewer": (
        "objectives",
        "constraints",
        "decisions",
        "evidence",
        "risks",
        "deliverables",
        "revisions",
    ),
    "general": tuple(FIELD_CUES),
}

ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "planner": (
        "planner",
        "planning",
        "coordinator",
        "orchestrator",
        "strategist",
        "规划",
        "计划",
        "协调",
    ),
    "writer": (
        "writer",
        "author",
        "executor",
        "implementer",
        "builder",
        "coder",
        "撰写",
        "写作",
        "执行",
        "实现",
    ),
    "reviewer": (
        "reviewer",
        "critic",
        "verifier",
        "validator",
        "auditor",
        "checker",
        "审查",
        "评审",
        "验证",
        "检查",
    ),
}

DEFAULT_ROLE_BUDGETS = {
    "planner": 620,
    "writer": 780,
    "reviewer": 680,
    "general": 700,
}

_LATIN_OR_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*|\d+(?:\.\d+)?")
_CHINESE_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}")
_VIEW_ID_RE = re.compile(r"\[memory_view:([^\]]+)\]")
_SLOT_RE = re.compile(r"\bslot=([^;]+)")
_CLAIM_RE = re.compile(r"\bclaim=([^;]+)")


@dataclass(frozen=True, slots=True)
class ConsumerCapabilityContext:
    consumer_id: str
    profile_version: int = 0
    capabilities: tuple[str, ...] = ()
    preferred_actions: tuple[str, ...] = ()
    input_preference: tuple[str, ...] = ()
    output_types: tuple[str, ...] = ()
    accepted_state_types: tuple[str, ...] = ()
    field_preferences: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MinimalRoleView:
    role: str
    consumer_id: str
    action: str
    profile_version: int
    capabilities: tuple[str, ...]
    information_fields: tuple[str, ...]
    text: str
    requested_fields: tuple[str, ...]
    covered_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    source_view_count: int
    candidate_unit_count: int
    selected_unit_count: int
    source_chars: int
    selected_chars: int
    target_budget_chars: int
    target_budget_exceeded: bool

    @property
    def reduction_ratio(self) -> float:
        if self.source_chars <= 0:
            return 0.0
        return round(1 - (self.selected_chars / self.source_chars), 6)


@dataclass(frozen=True, slots=True)
class _ViewUnit:
    source_index: int
    source_order: int
    view_id: str
    slot_id: str
    claim_id: str
    text: str
    fields: tuple[str, ...]
    mandatory: bool = False


def infer_collaboration_role(*parts: str) -> str:
    text = " ".join(str(part or "") for part in parts).casefold()
    scores = {
        role: sum(1 for alias in aliases if alias.casefold() in text)
        for role, aliases in ROLE_ALIASES.items()
    }
    best_role = max(scores, key=scores.get)
    return best_role if scores[best_role] > 0 else "general"


def requested_semantic_fields(query: str) -> tuple[str, ...]:
    lowered = str(query or "").casefold()
    return tuple(
        field_name
        for field_name, cues in REQUEST_FIELD_CUES.items()
        if any(cue.casefold() in lowered for cue in cues)
    )


def infer_semantic_action(
    query: str,
    *,
    preferred_actions: Iterable[str] = (),
    method_name: str = "",
    target_kind: str = "",
) -> str:
    text = " ".join((str(query or ""), method_name, target_kind)).casefold()
    scored = {
        action: sum(1 for cue in cues if cue.casefold() in text)
        for action, cues in ACTION_CUES.items()
    }
    normalized_preferences = sorted(
        {
            str(action or "").strip().upper()
            for action in preferred_actions
            if str(action or "").strip()
        }
    )
    preferred_scores = {
        action: scored.get(action, 0) for action in normalized_preferences
    }
    if preferred_scores:
        matched_preferred = max(preferred_scores, key=preferred_scores.get)
        if preferred_scores[matched_preferred] > 0:
            return matched_preferred
    matched = max(scored, key=scored.get)
    if scored[matched] > 0 and not normalized_preferences:
        return matched
    if len(normalized_preferences) == 1:
        return normalized_preferences[0]
    if "on_messages" in method_name or target_kind == "agentchat_agent":
        for candidate in ("WRITE_OUTPUT", "REVIEW_OUTPUT", "RETRIEVE_EVIDENCE"):
            if candidate in normalized_preferences:
                return candidate
    return "HANDLE_TASK"


def consumer_context_from_profile(
    profile: object | None,
    *,
    consumer_id: str,
) -> ConsumerCapabilityContext:
    if profile is None:
        return ConsumerCapabilityContext(consumer_id=consumer_id)
    return ConsumerCapabilityContext(
        consumer_id=consumer_id,
        profile_version=int(getattr(profile, "profile_version", 0) or 0),
        capabilities=tuple(sorted(getattr(profile, "capabilities", ()) or ())),
        preferred_actions=tuple(
            sorted(getattr(profile, "preferred_actions", ()) or ())
        ),
        input_preference=tuple(
            sorted(getattr(profile, "input_preference", ()) or ())
        ),
        output_types=tuple(sorted(getattr(profile, "output_types", ()) or ())),
        accepted_state_types=tuple(
            sorted(getattr(profile, "accepted_state_types", ()) or ())
        ),
    )


def derive_information_fields(
    *,
    query: str,
    consumer: ConsumerCapabilityContext,
    action: str,
) -> tuple[str, ...]:
    ordered: list[str] = []

    def add(fields: Iterable[str]) -> None:
        for field_name in fields:
            if field_name in FIELD_CUES and field_name not in ordered:
                ordered.append(field_name)

    add(requested_semantic_fields(query))
    add(ACTION_FIELDS.get(action, ()))
    focused_capabilities = tuple(
        capability
        for capability in consumer.capabilities
        if capability in ACTION_CAPABILITY_FOCUS.get(action, ())
    )
    active_capabilities = focused_capabilities or consumer.capabilities
    for capability in active_capabilities:
        add(CAPABILITY_FIELDS.get(capability, ()))
    focus_fields = set(ordered)
    for preference in consumer.input_preference:
        preference_fields = INPUT_PREFERENCE_FIELDS.get(preference, ())
        if not focus_fields or set(preference_fields) & focus_fields:
            add(preference_fields)
    add(consumer.field_preferences)
    if not ordered:
        add(("objectives", "constraints", "decisions", "evidence", "deliverables"))
    return tuple(ordered)


def build_minimal_context_view(
    *,
    query: str,
    prompt_views: Iterable[str],
    consumer: ConsumerCapabilityContext,
    action: str = "",
    budget_chars: int | None = None,
) -> MinimalRoleView:
    semantic_action = action or infer_semantic_action(
        query,
        preferred_actions=consumer.preferred_actions,
    )
    information_fields = derive_information_fields(
        query=query,
        consumer=consumer,
        action=semantic_action,
    )
    target_budget = max(
        160,
        int(budget_chars or _dynamic_budget_chars(consumer, semantic_action)),
    )
    source_views = [str(view or "").strip() for view in prompt_views if str(view or "").strip()]
    requested_fields = requested_semantic_fields(query)
    units = _collect_units(source_views)
    query_terms = _semantic_terms(query)
    relevant_fields = set(information_fields)
    required_fields = set(requested_fields)

    scored: list[tuple[float, _ViewUnit]] = []
    for unit in units:
        unit_terms = _semantic_terms(unit.text)
        overlap = len(query_terms & unit_terms)
        unit_fields = set(unit.fields)
        requested_matches = len(required_fields & unit_fields)
        information_matches = len(relevant_fields & unit_fields)
        score = overlap * 5 + requested_matches * 9 + information_matches * 3
        if re.search(r"\d|[\"“”'‘’]", unit.text):
            score += 1
        if unit.source_order == 0:
            score += 0.25
        scored.append((score, unit))

    selected: list[_ViewUnit] = []
    selected_keys: set[tuple[int, int]] = set()
    selected_texts: set[str] = set()

    for unit in units:
        if not unit.mandatory:
            continue
        key = (unit.source_index, unit.source_order)
        normalized_text = _normalize_unit(unit.text)
        if key not in selected_keys and normalized_text not in selected_texts:
            selected.append(unit)
            selected_keys.add(key)
            selected_texts.add(normalized_text)

    for field_name in requested_fields:
        candidates = [item for item in scored if field_name in item[1].fields]
        if not candidates:
            continue
        _, chosen = max(candidates, key=lambda item: (item[0], -item[1].source_order))
        key = (chosen.source_index, chosen.source_order)
        normalized_text = _normalize_unit(chosen.text)
        if key not in selected_keys and normalized_text not in selected_texts:
            selected.append(chosen)
            selected_keys.add(key)
            selected_texts.add(normalized_text)

    ranked = sorted(
        scored,
        key=lambda item: (-item[0], item[1].source_index, item[1].source_order),
    )
    current_chars = sum(len(unit.text) for unit in selected)
    for score, unit in ranked:
        key = (unit.source_index, unit.source_order)
        normalized_text = _normalize_unit(unit.text)
        if key in selected_keys or normalized_text in selected_texts or score <= 0:
            continue
        projected = current_chars + len(unit.text) + 3
        if selected and projected > target_budget and not unit.mandatory:
            continue
        selected.append(unit)
        selected_keys.add(key)
        selected_texts.add(normalized_text)
        current_chars = projected

    if not selected and units:
        selected.append(max(ranked, key=lambda item: item[0])[1])

    selected.sort(key=lambda unit: (unit.source_index, unit.source_order))
    rendered = _render_context_view(consumer, semantic_action, selected)
    covered_fields = tuple(
        field_name
        for field_name in FIELD_CUES
        if any(field_name in unit.fields for unit in selected)
    )
    missing_fields = tuple(
        field_name for field_name in requested_fields if field_name not in covered_fields
    )
    source_chars = len("\n".join(source_views))
    return MinimalRoleView(
        role=consumer.consumer_id,
        consumer_id=consumer.consumer_id,
        action=semantic_action,
        profile_version=consumer.profile_version,
        capabilities=consumer.capabilities,
        information_fields=information_fields,
        text=rendered,
        requested_fields=requested_fields,
        covered_fields=covered_fields,
        missing_fields=missing_fields,
        source_view_count=len(source_views),
        candidate_unit_count=len(units),
        selected_unit_count=len(selected),
        source_chars=source_chars,
        selected_chars=len(rendered),
        target_budget_chars=target_budget,
        target_budget_exceeded=len(rendered) > target_budget,
    )


def build_minimal_role_view(
    *,
    query: str,
    prompt_views: Iterable[str],
    role: str,
    budget_chars: int | None = None,
) -> MinimalRoleView:
    normalized_role = role if role in ROLE_FIELDS else "general"
    return build_minimal_context_view(
        query=query,
        prompt_views=prompt_views,
        consumer=ConsumerCapabilityContext(
            consumer_id=normalized_role,
            field_preferences=ROLE_FIELDS[normalized_role],
        ),
        action="HANDLE_TASK",
        budget_chars=(budget_chars or DEFAULT_ROLE_BUDGETS[normalized_role]),
    )


def field_fetch_query(query: str, missing_fields: Iterable[str]) -> str:
    labels = []
    for field_name in missing_fields:
        cues = FIELD_CUES.get(str(field_name), ())
        labels.extend(cues[:2])
    return " ".join([str(query or "").strip(), *labels]).strip()


def _collect_units(prompt_views: list[str]) -> list[_ViewUnit]:
    units: list[_ViewUnit] = []
    for source_index, view in enumerate(prompt_views):
        view_id = _match_or_default(_VIEW_ID_RE, view, f"view_{source_index + 1}")
        slot_id = _match_or_default(_SLOT_RE, view, "unresolved")
        claim_id = _match_or_default(_CLAIM_RE, view, "unknown")
        body = _memory_view_body(view)
        parts = re.split(r"(?:\r?\n)+|(?<=[。！？!?；;])\s*", body)
        source_order = 0
        for part in parts:
            text = part.strip(" \t\r\n-;；")
            if len(text) < 3:
                continue
            fields = tuple(
                field_name
                for field_name, cues in FIELD_CUES.items()
                if any(cue.casefold() in text.casefold() for cue in cues)
            )
            mandatory = text.startswith("[revision_guard")
            if mandatory and "revisions" not in fields:
                fields = (*fields, "revisions")
            units.append(
                _ViewUnit(
                    source_index=source_index,
                    source_order=source_order,
                    view_id=view_id,
                    slot_id=slot_id,
                    claim_id=claim_id,
                    text=text,
                    fields=fields,
                    mandatory=mandatory,
                )
            )
            source_order += 1
    return units


def _memory_view_body(view: str) -> str:
    body = _VIEW_ID_RE.sub("", view, count=1).strip()
    body = re.sub(r"^slot=[^;]*;\s*", "", body)
    body = re.sub(r"^claim=[^;]*;\s*", "", body)
    body = re.sub(r";\s*tags=\[[^\]]*\](?=\s*(?:\n|$))", "", body)
    return body.strip()


def _dynamic_budget_chars(
    consumer: ConsumerCapabilityContext,
    action: str,
) -> int:
    preferences = set(consumer.input_preference)
    if preferences and preferences <= {"metadata", "readiness", "capability_hints"}:
        return 420
    if preferences & {"execution_trace", "code_artifacts", "structured_data"}:
        return 900
    if preferences & {"evidence_snippets", "deliverable", "deliverable_schema"}:
        return 780
    if action in {"REVIEW_OUTPUT", "DIAGNOSE_FAILURE", "RUN_CODE"}:
        return 820
    return 700


def _render_context_view(
    consumer: ConsumerCapabilityContext,
    action: str,
    units: list[_ViewUnit],
) -> str:
    if not units:
        return ""
    lines = [
        "[minimal_context_view "
        f"consumer={consumer.consumer_id} "
        f"action={action} "
        f"profile_version={consumer.profile_version}]"
    ]
    active_source: tuple[int, str] | None = None
    for unit in units:
        source = (unit.source_index, unit.view_id)
        if source != active_source:
            lines.append(
                f"source_view={unit.view_id}; slot={unit.slot_id}; claim={unit.claim_id}"
            )
            active_source = source
        lines.append(f"- {unit.text}")
    return "\n".join(lines)


def _semantic_terms(text: str) -> set[str]:
    lowered = str(text or "").casefold()
    terms = {match.group(0) for match in _LATIN_OR_ID_RE.finditer(lowered)}
    for run in _CHINESE_RUN_RE.findall(lowered):
        if len(run) == 2:
            terms.add(run)
            continue
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def _match_or_default(pattern: re.Pattern[str], text: str, default: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else default


def _normalize_unit(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()
