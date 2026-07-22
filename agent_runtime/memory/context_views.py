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
class MinimalRoleView:
    role: str
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


def build_minimal_role_view(
    *,
    query: str,
    prompt_views: Iterable[str],
    role: str,
    budget_chars: int | None = None,
) -> MinimalRoleView:
    normalized_role = role if role in ROLE_FIELDS else "general"
    target_budget = max(
        160,
        int(budget_chars or DEFAULT_ROLE_BUDGETS[normalized_role]),
    )
    source_views = [str(view or "").strip() for view in prompt_views if str(view or "").strip()]
    requested_fields = requested_semantic_fields(query)
    units = _collect_units(source_views)
    query_terms = _semantic_terms(query)
    role_fields = set(ROLE_FIELDS[normalized_role])
    required_fields = set(requested_fields)

    scored: list[tuple[float, _ViewUnit]] = []
    for unit in units:
        unit_terms = _semantic_terms(unit.text)
        overlap = len(query_terms & unit_terms)
        unit_fields = set(unit.fields)
        requested_matches = len(required_fields & unit_fields)
        role_matches = len(role_fields & unit_fields)
        score = overlap * 5 + requested_matches * 9 + role_matches * 2
        if re.search(r"\d|[\"“”'‘’]", unit.text):
            score += 1
        if unit.source_order == 0:
            score += 0.25
        scored.append((score, unit))

    selected: list[_ViewUnit] = []
    selected_keys: set[tuple[int, int]] = set()
    selected_texts: set[str] = set()
    covered_required: set[str] = set()

    for field_name in requested_fields:
        candidates = [
            item for item in scored if field_name in item[1].fields
        ]
        if not candidates:
            continue
        _, chosen = max(candidates, key=lambda item: (item[0], -item[1].source_order))
        key = (chosen.source_index, chosen.source_order)
        normalized_text = _normalize_unit(chosen.text)
        if key not in selected_keys and normalized_text not in selected_texts:
            selected.append(chosen)
            selected_keys.add(key)
            selected_texts.add(normalized_text)
        covered_required.add(field_name)

    ranked = sorted(
        scored,
        key=lambda item: (-item[0], item[1].source_index, item[1].source_order),
    )
    current_chars = sum(len(unit.text) for unit in selected)
    for score, unit in ranked:
        key = (unit.source_index, unit.source_order)
        normalized_text = _normalize_unit(unit.text)
        if (
            key in selected_keys
            or normalized_text in selected_texts
            or score <= 0
        ):
            continue
        projected = current_chars + len(unit.text) + 3
        if selected and projected > target_budget:
            continue
        selected.append(unit)
        selected_keys.add(key)
        selected_texts.add(normalized_text)
        current_chars = projected

    if not selected and units:
        selected.append(max(ranked, key=lambda item: item[0])[1])

    selected.sort(key=lambda unit: (unit.source_index, unit.source_order))
    rendered = _render_role_view(normalized_role, selected)
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
        role=normalized_role,
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
            units.append(
                _ViewUnit(
                    source_index=source_index,
                    source_order=source_order,
                    view_id=view_id,
                    slot_id=slot_id,
                    claim_id=claim_id,
                    text=text,
                    fields=fields,
                )
            )
            source_order += 1
    return units


def _memory_view_body(view: str) -> str:
    body = _VIEW_ID_RE.sub("", view, count=1).strip()
    body = re.sub(r"^slot=[^;]*;\s*", "", body)
    body = re.sub(r"^claim=[^;]*;\s*", "", body)
    body = re.sub(r";\s*tags=\[[^\]]*\]\s*$", "", body)
    return body.strip()


def _render_role_view(role: str, units: list[_ViewUnit]) -> str:
    if not units:
        return ""
    lines = [f"[minimal_memory_view role={role}]"]
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
