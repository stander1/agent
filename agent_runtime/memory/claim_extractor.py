from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, asdict
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    subject: str
    raw_slot_text: str
    slot_id: str
    scope: str
    value: str
    value_type: str
    unit: str
    raw_text: str
    summary: str
    certainty: str
    modality: str
    polarity: str
    confidence: float
    revision_kind: str
    source_pointer: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SENTENCE_SPLIT_RE = re.compile(
    r"[\r\n]+|(?<=[\u3002\uff01\uff1f\uff1b\uff0c!?;,])\s*|(?<=\.)\s+"
)
_NEGATION_RE = re.compile(
    r"(?:不要|不得|禁止采用|不再使用|不能使用|取消|废弃|避免|do\s+not|must\s+not|no\s+longer)",
    re.IGNORECASE,
)
_REVISION_RE = re.compile(
    r"(?:改为|修改为|修订为|调整为|提高到|降低到|更新为|替代|取代|"
    r"change(?:d)?\s+to|revis(?:e|ed)\s+to|increase(?:d)?\s+to|replace(?:d)?)",
    re.IGNORECASE,
)
_NUMBER_TOKEN = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)"
_CURRENCY_PREFIX = r"(?:人民币|CNY|RMB|USD|美元|¥|￥|\$)"
_CURRENCY_SUFFIX = r"(?:元|人民币|CNY|RMB|USD|美元)"
_NON_MONETARY_COUNT_UNIT = (
    r"(?:人|位|名|天|晚|次|个|组|张|辆|间|份|项|轮|小时|分钟|岁)"
)
_EXPLICIT_DECISION_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<label>"
    r"(?:(?:最终|当前|综合|总体)\s*)?(?:风险\s*)?"
    r"(?:结论|判断|判定|决策|结果|状态)"
    r"|(?:(?:final|current|overall)\s+)?"
    r"(?:decision|verdict|outcome|result|status)"
    r")"
    r"\s*(?:为|是|=|:|：)\s*"
    r"(?P<value>[A-Za-z][A-Za-z0-9_.-]{1,63}|"
    r"[\u3400-\u4dbf\u4e00-\u9fff][^，。；;!?\n]{0,79})",
    re.IGNORECASE,
)


def normalize_claim_cards(
    claim_cards: Iterable[dict[str, Any]],
    *,
    default_subject: str,
    default_slot_hint: str,
    default_confidence: float,
    source_pointer: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in claim_cards:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if value is None:
            value = item.get("object")
        if value is None:
            value = item.get("summary")
        if value is None:
            value = item.get("claim")
        value_text = str(value or "").strip()
        if not value_text:
            continue
        raw_slot_text = str(
            item.get("raw_slot_text")
            or item.get("predicate")
            or item.get("slot_hint")
            or item.get("slot_id")
            or default_slot_hint
        ).strip()
        slot_id = str(item.get("slot_id") or item.get("slot_hint") or "").strip()
        scope = str(item.get("scope") or _scope_from_label(raw_slot_text)).strip()
        raw_text = str(item.get("raw_text") or item.get("summary") or value_text)
        claim = ExtractedClaim(
            subject=str(item.get("subject") or default_subject),
            raw_slot_text=raw_slot_text,
            slot_id=slot_id,
            scope=scope or "general",
            value=value_text,
            value_type=str(item.get("value_type") or _infer_value_type(value_text)),
            unit=str(item.get("unit") or ""),
            raw_text=raw_text,
            summary=str(item.get("summary") or raw_text),
            certainty=str(item.get("certainty") or _infer_certainty(raw_text)),
            modality=str(item.get("modality") or item.get("claim_type") or "asserted"),
            polarity=str(item.get("polarity") or _polarity(raw_text)),
            confidence=_bounded_float(item.get("confidence"), default_confidence),
            revision_kind=str(
                item.get("revision_kind")
                or ("replaces" if _REVISION_RE.search(raw_text) else "asserted")
            ),
            source_pointer=str(
                item.get("source_pointer")
                or item.get("source")
                or source_pointer
            ),
        )
        normalized.append(claim.to_dict())
    return _dedupe_claims(normalized)


def extract_claim_cards(
    text: str,
    *,
    subject: str,
    source_pointer: str = "",
    default_confidence: float = 0.78,
) -> list[dict[str, Any]]:
    claims: list[ExtractedClaim] = _extract_budget_table_claims(
        text,
        subject=subject,
        source_pointer=source_pointer,
        confidence=min(0.98, default_confidence + 0.06),
    )
    for raw_sentence in _sentences(text):
        sentence = _clean_markup(raw_sentence)
        if len(sentence) < 3:
            continue
        polarity = _polarity(sentence)
        revision_kind = "replaces" if _REVISION_RE.search(sentence) else "asserted"
        confidence = min(
            0.98,
            default_confidence
            + (0.08 if revision_kind == "replaces" else 0.0)
            + (0.05 if re.search(r"(?:核心|选定|最终|必须|目标|current|selected)", sentence, re.I) else 0.0),
        )

        claims.extend(
            _extract_explicit_decision_claims(
                sentence,
                subject=subject,
                source_pointer=source_pointer,
                confidence=confidence,
                polarity=polarity,
                revision_kind=revision_kind,
            )
        )
        claims.extend(
            _extract_known_claims(
                sentence,
                subject=subject,
                source_pointer=source_pointer,
                confidence=confidence,
                polarity=polarity,
                revision_kind=revision_kind,
            )
        )
        claims.extend(
            _extract_assignments(
                sentence,
                subject=subject,
                source_pointer=source_pointer,
                confidence=confidence,
                polarity=polarity,
                revision_kind=revision_kind,
            )
        )

    return _dedupe_claims(item.to_dict() for item in claims)


def _extract_explicit_decision_claims(
    sentence: str,
    *,
    subject: str,
    source_pointer: str,
    confidence: float,
    polarity: str,
    revision_kind: str,
) -> list[ExtractedClaim]:
    rows: list[ExtractedClaim] = []
    for match in _EXPLICIT_DECISION_RE.finditer(sentence):
        label = match.group("label").strip()
        value = _clean_decision_value(match.group("value"))
        if not value:
            continue
        normalized_label = _normalize_key(label)
        if re.search(r"(?:风险|risk)", label, re.IGNORECASE):
            scope = "decision.risk"
        elif re.search(r"(?:状态|status)", label, re.IGNORECASE):
            scope = "decision.status"
        elif re.search(r"(?:决策|decision)", label, re.IGNORECASE):
            scope = "decision.selection"
        else:
            scope = "decision.outcome"
        rows.append(
            _claim(
                subject,
                normalized_label,
                "slot.system.design_decision",
                scope,
                value,
                _infer_value_type(value),
                "",
                sentence,
                "decision",
                polarity,
                max(confidence, 0.93),
                revision_kind,
                source_pointer,
            )
        )
    return rows


def claim_identity(claim: dict[str, Any]) -> str:
    subject = _normalize_key(str(claim.get("subject") or ""))
    slot_id = _normalize_key(str(claim.get("slot_id") or ""))
    scope = _normalize_key(str(claim.get("scope") or "general"))
    temporal = _normalize_key(str(claim.get("temporal_scope") or "current_project"))
    return "|".join((subject, slot_id, scope, temporal))


def normalized_value(value: Any, value_type: str = "string", unit: str = "") -> str:
    text = str(value or "").strip()
    kind = str(value_type or "string").lower()
    if kind in {"integer", "number", "float"}:
        match = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
        if match:
            number = match.group(0)
            if kind == "integer" and "." in number:
                number = str(int(float(number)))
            return f"{number}|{_normalize_key(unit)}"
    if kind == "boolean":
        lowered = text.lower()
        if lowered in {"false", "0", "no", "disabled", "forbidden", "禁止", "不允许"}:
            return "false"
        if lowered in {"true", "1", "yes", "enabled", "allowed", "允许"}:
            return "true"
    return _normalize_text(text)


def _extract_known_claims(
    sentence: str,
    *,
    subject: str,
    source_pointer: str,
    confidence: float,
    polarity: str,
    revision_kind: str,
) -> list[ExtractedClaim]:
    rows: list[ExtractedClaim] = []

    architecture = _architecture_value(sentence)
    if architecture and re.search(
        r"(?:核心架构|选定方案|选择(?:了|为)?|采用|保持.*架构|architecture|selected\s+方案)",
        sentence,
        re.IGNORECASE,
    ):
        rows.append(
            _claim(
                subject,
                "architecture",
                "slot.system.design_decision",
                "architecture.storage",
                architecture,
                "string",
                "",
                sentence,
                "project_state",
                polarity,
                confidence,
                revision_kind,
                source_pointer,
            )
        )

    concurrency_values: list[str] = []
    concurrency_pair = re.search(
        r"(?:并发(?:任务|客户端|数)?|concurrenc(?:y|ies)).{0,16}?"
        r"(\d{1,6}).{0,8}?(?:和|与|及|/|and).{0,8}?(\d{1,6})",
        sentence,
        re.IGNORECASE,
    )
    if concurrency_pair:
        concurrency_values.extend(concurrency_pair.groups())
    concurrency_match = re.search(
        r"(?:峰值\s*)?(?:并发(?:任务|客户端|数)?|concurrenc(?:y|ies))"
        r"(?:\s*(?:提高到|调整为|为|=|:|：|达到|支持))?\s*[*`]*(\d{1,6})",
        sentence,
        re.IGNORECASE,
    )
    if not concurrency_match:
        concurrency_match = re.search(
            r"(?:支持|达到|提高到)\s*[*`]*(\d{1,6})[*`]*\s*(?:个)?并发(?:任务|客户端)?",
            sentence,
            re.IGNORECASE,
        )
    if concurrency_match and concurrency_match.group(1) not in concurrency_values:
        concurrency_values.append(concurrency_match.group(1))
    for concurrency_value in concurrency_values:
        rows.append(
            _claim(
                subject,
                "peak_concurrency",
                "slot.project.requirement",
                "constraint.peak_concurrency",
                concurrency_value,
                "integer",
                "task",
                sentence,
                "requirement",
                polarity,
                confidence,
                revision_kind,
                source_pointer,
            )
        )

    retention_match = re.search(
        r"(?:审计(?:记录)?(?:数据)?|audit).{0,12}?(?:保留|retention)"
        r"(?:\s*(?:为|=|:|：))?\s*[*`]*(\d{1,6})[*`]*\s*(?:天|day)",
        sentence,
        re.IGNORECASE,
    )
    if retention_match:
        rows.append(
            _claim(
                subject,
                "audit_retention",
                "slot.project.requirement",
                "constraint.audit_retention_days",
                retention_match.group(1),
                "integer",
                "day",
                sentence,
                "requirement",
                polarity,
                confidence,
                revision_kind,
                source_pointer,
            )
        )

    rto_match = re.search(
        r"(?:RTO|恢复(?:时间|目标|耗时)).{0,16}?([<≤=]?)\s*[*`]*(\d+(?:\.\d+)?)"
        r"[*`]*\s*(?:秒|s(?:ec(?:ond)?)?)",
        sentence,
        re.IGNORECASE,
    )
    if rto_match:
        comparator = rto_match.group(1) or "<="
        rows.append(
            _claim(
                subject,
                "recovery_rto",
                "slot.project.requirement",
                "constraint.recovery_rto_seconds",
                f"{comparator}{rto_match.group(2)}",
                "number",
                "second",
                sentence,
                "requirement",
                polarity,
                confidence,
                revision_kind,
                source_pointer,
            )
        )

    if re.search(r"(?:外部服务|external\s+service)", sentence, re.IGNORECASE):
        if re.search(r"(?:禁止|不得|不能新增|不允许|without|no\s+external)", sentence, re.IGNORECASE):
            value = "false"
        elif re.search(r"(?:允许|可以新增|enable|allowed)", sentence, re.IGNORECASE):
            value = "true"
        else:
            value = ""
        if value:
            rows.append(
                _claim(
                    subject,
                    "external_services_allowed",
                    "slot.project.requirement",
                    "constraint.external_services_allowed",
                    value,
                    "boolean",
                    "",
                    sentence,
                    "constraint",
                    "positive",
                    confidence,
                    revision_kind,
                    source_pointer,
                )
            )

    if re.search(r"(?:单写者|单一写入|single[-\s]?writer)", sentence, re.IGNORECASE):
        rows.append(
            _claim(
                subject,
                "write_model",
                "slot.system.design_decision",
                "runtime.write_model",
                "single_writer_queue",
                "string",
                "",
                sentence,
                "project_state",
                polarity,
                confidence,
                revision_kind,
                source_pointer,
            )
        )

    budget_amount = _extract_budget_amount(sentence)
    if budget_amount is not None:
        budget_value, budget_unit = budget_amount
        budget_scope, budget_label, budget_modality = _budget_claim_shape(
            sentence
        )
        rows.append(
            _claim(
                subject,
                budget_label,
                "slot.project.requirement",
                budget_scope,
                budget_value,
                "number",
                budget_unit,
                sentence,
                budget_modality,
                polarity,
                confidence,
                revision_kind,
                source_pointer,
            )
        )

    destination_match = re.search(
        r"(?:最终目的地|选定目的地|目的地(?:选择)?|destination)"
        r"\s*(?:为|是|=|:|：)\s*([A-Za-z\u3400-\u9fff·\-\s]{2,30})",
        sentence,
        re.IGNORECASE,
    )
    if destination_match:
        value = destination_match.group(1).strip(" ，,。.;；")
        rows.append(
            _claim(
                subject,
                "selected_destination",
                "slot.system.design_decision",
                "plan.selected_destination",
                value,
                "string",
                "",
                sentence,
                "project_state",
                polarity,
                confidence,
                revision_kind,
                source_pointer,
            )
        )

    return rows


def _extract_assignments(
    sentence: str,
    *,
    subject: str,
    source_pointer: str,
    confidence: float,
    polarity: str,
    revision_kind: str,
) -> list[ExtractedClaim]:
    rows: list[ExtractedClaim] = []
    for match in re.finditer(
        r"\b([A-Za-z][A-Za-z0-9_.-]{2,48})\s*(?:=|:)\s*"
        r"([A-Za-z0-9_.+/<>=-]{1,64})",
        sentence,
    ):
        key = match.group(1).lower().replace("-", "_")
        if key in {"http", "https", "evidence_id", "sha256"}:
            continue
        value = match.group(2).rstrip(".,;:!?")
        if not value:
            continue
        rows.append(
            _claim(
                subject,
                key,
                "slot.runtime.config",
                f"config.{key}",
                value,
                _infer_value_type(value),
                "",
                sentence,
                "project_state",
                polarity,
                min(confidence, 0.9),
                revision_kind,
                source_pointer,
            )
        )
    return rows


def _claim(
    subject: str,
    raw_slot_text: str,
    slot_id: str,
    scope: str,
    value: str,
    value_type: str,
    unit: str,
    raw_text: str,
    modality: str,
    polarity: str,
    confidence: float,
    revision_kind: str,
    source_pointer: str,
) -> ExtractedClaim:
    return ExtractedClaim(
        subject=subject,
        raw_slot_text=raw_slot_text,
        slot_id=slot_id,
        scope=scope,
        value=value,
        value_type=value_type,
        unit=unit,
        raw_text=raw_text,
        summary=raw_text,
        certainty=_infer_certainty(raw_text),
        modality=modality,
        polarity=polarity,
        confidence=confidence,
        revision_kind=revision_kind,
        source_pointer=source_pointer,
    )


def _dedupe_claims(claims: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for claim in claims:
        key = (
            "|".join(
                (
                    _normalize_key(str(claim.get("slot_id") or "")),
                    _normalize_key(str(claim.get("scope") or "general")),
                )
            ),
            normalized_value(
                claim.get("value"),
                str(claim.get("value_type") or "string"),
                str(claim.get("unit") or ""),
            ),
        )
        previous = selected.get(key)
        if previous is None or float(claim.get("confidence", 0.0)) >= float(
            previous.get("confidence", 0.0)
        ):
            selected[key] = claim
    return list(selected.values())


def _architecture_value(sentence: str) -> str:
    if re.search(r"SQLite.{0,12}WAL.{0,20}文件载荷", sentence, re.IGNORECASE):
        return "sqlite_wal_file_payload"
    if re.search(r"Redis.{0,12}PostgreSQL", sentence, re.IGNORECASE):
        return "redis_postgresql"
    if re.search(r"(?:纯进程内存|in[-\s]?memory)", sentence, re.IGNORECASE):
        return "in_process_memory"
    return ""


def _scope_from_label(label: str) -> str:
    normalized = _normalize_key(label)
    aliases = {
        "budget": "constraint.budget_upper_bound",
        "peak_concurrency": "constraint.peak_concurrency",
        "concurrency": "constraint.peak_concurrency",
        "audit_retention": "constraint.audit_retention_days",
        "recovery_rto": "constraint.recovery_rto_seconds",
        "architecture": "architecture.storage",
        "write_model": "runtime.write_model",
        "destination": "plan.selected_destination",
        "selected_destination": "plan.selected_destination",
    }
    return aliases.get(normalized, f"field.{normalized}" if normalized else "general")


def _sentences(text: str) -> list[str]:
    return [
        item.strip()
        for item in _SENTENCE_SPLIT_RE.split(str(text or ""))
        if item.strip()
    ]


def _clean_markup(text: str) -> str:
    cleaned = re.sub(r"[#>*`]+", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" |-")


def _clean_decision_value(value: str) -> str:
    cleaned = str(value or "").strip(" \t\r\n`*_\"'，,。.;；:：")
    cleaned = re.split(r"\s*[（(]\s*", cleaned, maxsplit=1)[0].strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,63}", cleaned):
        return cleaned
    return cleaned[:80].rstrip("，,。.;；:：")


def _polarity(text: str) -> str:
    return "negative" if _NEGATION_RE.search(text) else "positive"


def _infer_certainty(text: str) -> str:
    value = str(text or "")
    if re.search(
        r"(?:假设|假定|示例|模拟|预期(?:返回|结果|输出)|可能(?:是|为)?|"
        r"尚未(?:验证|确认)|待确认|无法确认|"
        r"\bhypothetical\b|\bassum(?:e|ed|ption)\b|\bexample\b|"
        r"\bexpected\s+(?:(?:query|tool|model)\s+)?"
        r"(?:return|result|output)\b|\bpossibly\b|\bmaybe\b|"
        r"\bunverified\b|\bto\s+be\s+confirmed\b)",
        value,
        re.IGNORECASE,
    ):
        return "uncertain"
    if re.search(
        r"(?:据此推断|推测|倾向于|表明可能|可推导|"
        r"\binfer(?:red|ence)?\b|\blikely\b|\bsuggests?\s+that\b)",
        value,
        re.IGNORECASE,
    ):
        return "inferred"
    if re.search(
        r"(?:资料快照|观测(?:到|结果)|工具(?:返回|结果)|查询(?:返回|结果)|"
        r"日志(?:显示|记录)|证据(?:显示|表明)|"
        r"\bobserved\b|\btool\s+result\b|\bquery\s+returned\b|"
        r"\bevidence\s+(?:shows?|confirms?)\b)",
        value,
        re.IGNORECASE,
    ):
        return "observed"
    return "asserted"


def _currency_unit(text: str) -> str:
    if "￥" in text or "¥" in text or "元" in text or "人民币" in text:
        return "CNY"
    if "$" in text or "USD" in text.upper():
        return "USD"
    return ""


def _budget_claim_shape(sentence: str) -> tuple[str, str, str]:
    lowered = sentence.casefold()
    if re.search(
        r"(?:上限|红线|不(?:得|可|能)?超过|至多|最多|"
        r"\b(?:cap|maximum|max|at\s+most|no\s+more\s+than)\b|<=|≤)",
        sentence,
        re.IGNORECASE,
    ):
        return "constraint.budget_upper_bound", "budget_upper_bound", "requirement"

    if re.search(
        r"(?:总预算|整体预算|项目预算|\btotal\s+budget\b)",
        sentence,
        re.IGNORECASE,
    ):
        return "estimate.budget_total", "budget_total", "estimate"

    component_match = re.search(
        r"(?P<label>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff]"
        r"[A-Za-z0-9_\-\u3400-\u4dbf\u4e00-\u9fff]{0,23})"
        r"\s*(?:预算|budget)",
        sentence,
        re.IGNORECASE,
    )
    component_label = (
        component_match.group("label").strip() if component_match else ""
    )
    generic_labels = {
        "总",
        "总计",
        "整体",
        "项目",
        "本次",
        "旅行",
        "total",
        "overall",
    }
    if component_label and component_label.casefold() not in generic_labels:
        component_key = _normalize_key(component_label)
        return (
            f"allocation.budget.{component_key}",
            f"{component_label}_budget",
            "allocation",
        )

    if re.search(
        r"(?:预计|估算|测算|总计|合计|总额|总费用|"
        r"\b(?:estimate|estimated|total(?:\s+cost)?)\b)",
        lowered,
        re.IGNORECASE,
    ):
        return "estimate.budget_total", "budget_total", "estimate"
    return "constraint.budget_upper_bound", "budget_upper_bound", "requirement"


def _extract_budget_table_claims(
    text: str,
    *,
    subject: str,
    source_pointer: str,
    confidence: float,
) -> list[ExtractedClaim]:
    lines = str(text or "").splitlines()
    claims: list[ExtractedClaim] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if "|" not in line or not re.search(
            r"(?:预算|金额|费用|budget|amount|cost)",
            line,
            re.IGNORECASE,
        ):
            index += 1
            continue
        headers = _markdown_cells(line)
        amount_index = next(
            (
                position
                for position, header in enumerate(headers)
                if re.search(
                    r"(?:预算|金额|budget|amount|cost)",
                    header,
                    re.IGNORECASE,
                )
            ),
            -1,
        )
        label_index = next(
            (
                position
                for position, header in enumerate(headers)
                if position != amount_index
                and re.search(
                    r"(?:类别|项目|科目|类型|category|item)",
                    header,
                    re.IGNORECASE,
                )
            ),
            0 if amount_index != 0 else 1,
        )
        if amount_index < 0 or label_index >= len(headers):
            index += 1
            continue

        table_unit = _currency_unit(line)
        row_index = index + 1
        while row_index < len(lines) and "|" in lines[row_index]:
            raw_row = lines[row_index].strip()
            cells = _markdown_cells(raw_row)
            row_index += 1
            if not cells or all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
                continue
            if max(amount_index, label_index) >= len(cells):
                continue
            label = cells[label_index].strip()
            amount_cell = cells[amount_index].strip()
            amount_match = re.fullmatch(
                rf"\s*(?:{_CURRENCY_PREFIX}\s*)?"
                rf"(?P<value>{_NUMBER_TOKEN})"
                rf"(?:\s*{_CURRENCY_SUFFIX})?\s*",
                amount_cell,
                re.IGNORECASE,
            )
            if not label or amount_match is None:
                continue
            value = _parse_number_text(amount_match.group("value"))
            unit = _currency_unit(amount_cell) or table_unit
            if re.fullmatch(
                r"(?:总计|合计|总额|总费用|total(?:\s+cost)?)",
                label,
                re.IGNORECASE,
            ):
                scope = "estimate.budget_total"
                raw_slot_text = "budget_total"
                modality = "estimate"
            else:
                scope = f"allocation.budget.{_normalize_key(label)}"
                raw_slot_text = f"{label}_budget"
                modality = "allocation"
            claims.append(
                _claim(
                    subject,
                    raw_slot_text,
                    "slot.project.requirement",
                    scope,
                    value,
                    "number",
                    unit,
                    raw_row,
                    modality,
                    "positive",
                    confidence,
                    "asserted",
                    source_pointer,
                )
            )
        index = max(index + 1, row_index)
    return claims


def _markdown_cells(line: str) -> list[str]:
    stripped = str(line or "").strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")] if stripped else []


def _extract_budget_amount(sentence: str) -> tuple[str, str] | None:
    label = re.search(
        r"(?:预算|总费用|总金额|合计|budget|total\s+cost)",
        sentence,
        re.IGNORECASE,
    )
    if label is None:
        return None
    tail = sentence[label.end() : label.end() + 120]

    currency_patterns = (
        re.compile(
            rf"(?P<prefix>{_CURRENCY_PREFIX})\s*"
            rf"(?P<value>{_NUMBER_TOKEN})(?:\s*{_CURRENCY_SUFFIX})?",
            re.IGNORECASE,
        ),
        re.compile(
            rf"(?P<value>{_NUMBER_TOKEN})\s*(?P<suffix>{_CURRENCY_SUFFIX})",
            re.IGNORECASE,
        ),
    )
    currency_matches = [
        match
        for pattern in currency_patterns
        if (match := pattern.search(tail)) is not None
    ]
    if currency_matches:
        match = min(currency_matches, key=lambda item: item.start())
        matched_text = match.group(0)
        return _parse_number_text(match.group("value")), _currency_unit(
            matched_text
        )

    fallback = re.match(
        rf"\s*(?:(?:为|只有|调整为|上限(?:为)?|不超过|=|:|：|≤|<=)\s*)*"
        rf"(?P<value>{_NUMBER_TOKEN})(?!\s*{_NON_MONETARY_COUNT_UNIT})",
        tail,
        re.IGNORECASE,
    )
    if fallback is None:
        return None
    return _parse_number_text(fallback.group("value")), _currency_unit(
        fallback.group(0)
    )


def _infer_value_type(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"true", "false", "yes", "no", "允许", "禁止", "不允许"}:
        return "boolean"
    if re.fullmatch(r"[<≤>=]*\s*-?\d+", value.strip()):
        return "integer"
    if re.fullmatch(r"[<≤>=]*\s*-?\d+(?:\.\d+)?", value.strip()):
        return "number"
    return "string"


def _parse_number_text(text: str) -> str:
    value = text.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return value
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in digits:
            number = digits[char]
            continue
        unit = units.get(char)
        if unit is None:
            return value
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
            continue
        section += (number or 1) * unit
        number = 0
    return str(total + section + number)


def _bounded_float(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


def _normalize_key(text: str) -> str:
    normalized = re.sub(
        r"[^\w\u3400-\u4dbf\u4e00-\u9fff.]+",
        "_",
        text.casefold(),
    ).strip("_")
    if normalized:
        return normalized
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"field_{digest}"


def _normalize_text(text: str) -> str:
    return re.sub(
        r"[^a-z0-9\u3400-\u4dbf\u4e00-\u9fff<>=]+",
        "",
        text.lower(),
    )
