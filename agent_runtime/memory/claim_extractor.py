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
_NUMBER_TOKEN = (
    r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|"
    r"\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)"
)
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
_CLAIM_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s+|[一二三四五六七八九十百]+[、.．]\s*)"
)
_CLAIM_HISTORY_HEADING_RE = re.compile(
    r"(?i)(?:决策日志|变更日志|修订记录|版本记录|历史记录|"
    r"decision\s+log|change\s+log|revision\s+(?:log|history)|history)"
)
_CLAIM_PLAIN_HISTORY_HEADING_RE = re.compile(
    r"(?i)^\s*[*_`]*\s*(?:决策日志|变更日志|修订记录|版本记录|历史记录|"
    r"decision\s+log|change\s+log|revision\s+(?:log|history)|history)"
    r"\s*[*_`]*\s*[:：]?\s*$"
)
_CLAIM_STAGE_TRANSITION_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"[A-Za-z][A-Za-z0-9_.-]*\d+\s*(?:->|=>|→)\s*"
    r"[A-Za-z][A-Za-z0-9_.-]*\d+(?![A-Za-z0-9_])"
)
_CLAIM_CURRENT_RESULT_RE = re.compile(
    r"(?i)^\s*(?:(?:当前|最终|现行|最新)|"
    r"(?:current|final|active|latest)\b)"
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
    active_text = _active_claim_text(text)
    claims: list[ExtractedClaim] = _extract_budget_table_claims(
        active_text,
        subject=subject,
        source_pointer=source_pointer,
        confidence=min(0.98, default_confidence + 0.06),
    )
    sentence_source = re.sub(
        r"(?<=\d),(?=\d{3}(?:\D|$))",
        "",
        active_text,
    )
    for raw_sentence in _sentences(sentence_source):
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


def _active_claim_text(text: str) -> str:
    active_lines: list[str] = []
    historical_section = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if _CLAIM_SECTION_HEADING_RE.search(stripped):
            historical_section = bool(
                _CLAIM_HISTORY_HEADING_RE.search(stripped)
            )
            if historical_section:
                continue
        elif _CLAIM_PLAIN_HISTORY_HEADING_RE.search(stripped):
            historical_section = True
            continue
        if (
            historical_section
            and _CLAIM_CURRENT_RESULT_RE.search(stripped)
            and not _CLAIM_STAGE_TRANSITION_RE.search(stripped)
        ):
            historical_section = False
        if historical_section:
            continue
        if _CLAIM_STAGE_TRANSITION_RE.search(stripped):
            continue
        active_lines.append(line)
    return "\n".join(active_lines)


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

    for (
        budget_value,
        budget_unit,
        budget_scope,
        budget_label,
        budget_modality,
    ) in _extract_budget_claim_specs(sentence):
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
        r"\s*(?:为|是|=|:|：)\s*([A-Za-z\u3400-\u9fff·\-\s]{2,80})",
        sentence,
        re.IGNORECASE,
    )
    if destination_match:
        value = _clean_destination_value(destination_match.group(1))
        if not value:
            return rows
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
        if key in {
            "http",
            "https",
            "evidence_id",
            "sha256",
            "budget",
            "total_budget",
            "budget_cap",
            "destination",
            "selected_destination",
        }:
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


def _clean_destination_value(value: str) -> str:
    cleaned = str(value or "").strip(" \t\r\n`*_\"'，,。.;；:：")
    cleaned = re.split(
        r"\s*(?:选择理由|推荐理由|入选理由|理由|原因|"
        r"selection\s+(?:reason|rationale)|reasons?)\s*[:：]?",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
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


def _budget_claim_shape(
    sentence: str,
) -> tuple[str, str, str] | None:
    lowered = sentence.casefold()
    if re.search(
        r"(?:上限|红线|不(?:得|可|能)?超过|至多|最多|"
        r"\b(?:cap|maximum|max|at\s+most|no\s+more\s+than)\b|<=|≤)",
        sentence,
        re.IGNORECASE,
    ):
        return "constraint.budget_upper_bound", "budget_upper_bound", "requirement"

    if re.search(
        r"(?:基础预算|初步预算|预估预算|总预算|整体预算|项目预算|"
        r"\b(?:base|initial|estimated|total|overall|project)\s+budget\b)",
        sentence,
        re.IGNORECASE,
    ):
        return "estimate.budget_total", "budget_total", "estimate"

    if re.search(
        r"(?:预算余量|预算余额|预算余款|预算缓冲|预算弹性空间|"
        r"应急(?:金|预算)|预留(?:金额|预算)?|"
        r"\b(?:budget\s+)?(?:reserve|buffer|headroom|contingency)\b)",
        sentence,
        re.IGNORECASE,
    ):
        return (
            "allocation.budget.reserve",
            "budget_reserve",
            "allocation",
        )

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

    if re.search(
        r"(?:^|[，,。；;:：])(?:当前|本次|已确认)?\s*预算"
        r"\s*(?:为|是|=|:|：)",
        sentence,
        re.IGNORECASE,
    ):
        return "constraint.budget_upper_bound", "budget_upper_bound", "requirement"
    return None


def _extract_budget_claim_specs(
    sentence: str,
) -> list[tuple[str, str, str, str, str]]:
    """Extract distinct budget facets from one sentence.

    A sentence may state an estimate and a cap together, or revise a cap from
    an old value to a new value. Classifying the first number using cues from
    the whole sentence conflates those facts and can promote an estimate as
    the active constraint.
    """

    amount_pattern = re.compile(
        rf"(?:(?P<prefix>{_CURRENCY_PREFIX})\s*)?"
        rf"(?P<value>{_NUMBER_TOKEN})"
        rf"(?:\s*(?P<suffix>{_CURRENCY_SUFFIX}))?",
        re.IGNORECASE,
    )
    budget_cue = re.compile(
        r"(?:预算|总费用|总金额|合计|budget|total\s+cost)",
        re.IGNORECASE,
    )
    upper_cue = re.compile(
        r"(?:上限|红线|不超过|不得超过|至多|最多|只有|≤|<=|"
        r"\b(?:cap|limit|maximum|max|at\s+most|no\s+more\s+than)\b)",
        re.IGNORECASE,
    )
    estimate_cue = re.compile(
        r"(?:预计|估算|测算|原方案|当前方案|基础预算|初步预算|"
        r"预估预算|预算总和|总预算|"
        r"总费用|总金额|合计|\b(?:estimate|estimated|total\s+budget|"
        r"total\s+cost)\b)",
        re.IGNORECASE,
    )
    reserve_cue = re.compile(
        r"(?:预算余量|预算余额|预算余款|预算缓冲|预算弹性空间|"
        r"应急(?:金|预算)|预留(?:金额|预算)?|"
        r"\b(?:budget\s+)?(?:reserve|buffer|headroom|contingency)\b)",
        re.IGNORECASE,
    )
    revision_transition = re.search(
        r"(?:从|由|\bfrom\b).{0,64}?"
        r"(?:改为|修改为|修订为|调整为|提高到|降低到|更新为|到|至|\bto\b)"
        r"\s*",
        sentence,
        re.IGNORECASE,
    )
    revision_match = revision_transition or _REVISION_RE.search(sentence)
    amount_matches = []
    for match in amount_pattern.finditer(sentence):
        if match.group("prefix") or match.group("suffix"):
            amount_matches.append(match)
            continue
        following = sentence[match.end() : match.end() + 12]
        if re.match(
            rf"\s*{_NON_MONETARY_COUNT_UNIT}",
            following,
            re.IGNORECASE,
        ):
            continue
        preceding = sentence[max(0, match.start() - 48) : match.start()]
        direct_budget_value = re.search(
            r"(?:预算|总费用|总金额|合计|budget|total\s+cost|"
            r"上限|cap|limit|estimate|estimated)\s*$",
            preceding,
            re.IGNORECASE,
        )
        related_budget_value = re.search(
            r"(?:预算|总费用|总金额|合计|budget|total\s+cost|"
            r"上限|cap|limit|estimate|estimated)"
            r"[^0-9]{0,32}?"
            r"(?:为|是|只有|调整为|更新为|不超过|至多|最多|"
            r"=|:|：|≤|<=|\bis\b|\bat\b|\bremains?\b)"
            r"\s*$",
            preceding,
            re.IGNORECASE,
        )
        if direct_budget_value or related_budget_value:
            amount_matches.append(match)
    if not amount_matches:
        fallback = _extract_budget_amount(sentence)
        if fallback is None:
            return []
        value, unit = fallback
        shape = _budget_claim_shape(sentence)
        if shape is None:
            return []
        scope, label, modality = shape
        return [(value, unit, scope, label, modality)]

    revision_target_start = -1
    if revision_match is not None:
        post_revision = [
            match
            for match in amount_matches
            if match.start() >= revision_match.end()
        ]
        if post_revision:
            revision_target_start = post_revision[0].start()

    extracted: list[tuple[str, str, str, str, str]] = []
    for match in amount_matches:
        clause_start = max(
            sentence.rfind(mark, 0, match.start()) + 1
            for mark in ("\n", "。", "；", ";", "!", "！", "?", "？")
        )
        clause_end_candidates = [
            position
            for mark in ("\n", "。", "；", ";", "!", "！", "?", "？")
            if (position := sentence.find(mark, match.end())) >= 0
        ]
        clause_end = min(clause_end_candidates) if clause_end_candidates else len(sentence)
        local_clause = sentence[clause_start:clause_end]
        local_before = sentence[max(clause_start, match.start() - 48) : match.start()]
        local_after = sentence[match.end() : min(clause_end, match.end() + 16)]
        local_window = f"{local_before}{match.group(0)}{local_after}"
        if not budget_cue.search(local_clause) and not upper_cue.search(local_clause):
            continue

        # In "cap from OLD updated to NEW", OLD is lineage, not the new fact.
        if (
            revision_target_start >= 0
            and match.start() < revision_match.end()
            and upper_cue.search(local_clause)
        ):
            continue

        amount_start = len(local_before)
        amount_end = amount_start + len(match.group(0))
        cue_candidates = [
            (
                _cue_distance(
                    pattern,
                    local_window,
                    amount_start=amount_start,
                    amount_end=amount_end,
                ),
                priority,
                kind,
            )
            for priority, (kind, pattern) in enumerate(
                (
                    ("upper", upper_cue),
                    ("reserve", reserve_cue),
                    ("estimate", estimate_cue),
                )
            )
        ]
        nearest_kind = min(
            (
                item
                for item in cue_candidates
                if item[0] is not None
            ),
            default=(None, 99, ""),
        )[2]

        if nearest_kind == "upper":
            scope, label, modality = (
                "constraint.budget_upper_bound",
                "budget_upper_bound",
                "requirement",
            )
        elif nearest_kind == "reserve":
            scope, label, modality = (
                "allocation.budget.reserve",
                "budget_reserve",
                "allocation",
            )
        elif nearest_kind == "estimate":
            scope, label, modality = (
                "estimate.budget_total",
                "budget_total",
                "estimate",
            )
        else:
            component = re.search(
                r"(?P<label>[A-Za-z\u3400-\u4dbf\u4e00-\u9fff]"
                r"[A-Za-z0-9_\-\u3400-\u4dbf\u4e00-\u9fff]{0,23})"
                r"\s*(?:预算|budget)\s*(?:为|=|:|：)?\s*$",
                local_before,
                re.IGNORECASE,
            )
            component_label = component.group("label").strip() if component else ""
            if not component_label:
                parenthetical_component = re.search(
                    r"(?:^|[，,、；;:：])"
                    r"(?P<label>[^，,、；;:：()（）]{1,24})"
                    r"\s*[（(]?\s*$",
                    local_before,
                    re.IGNORECASE,
                )
                if parenthetical_component:
                    component_label = _clean_budget_component_label(
                        parenthetical_component.group("label")
                    )
            if component_label and component_label.casefold() not in {
                "总",
                "总计",
                "整体",
                "项目",
                "本次",
                "旅行",
                "total",
                "overall",
            }:
                normalized = _normalize_key(component_label)
                scope, label, modality = (
                    f"allocation.budget.{normalized}",
                    f"{component_label}_budget",
                    "allocation",
                )
            else:
                bare_budget = re.search(
                    r"(?:^|[，,。；;:：])(?:当前|本次|已确认)?\s*"
                    r"预算\s*(?:为|是|=|:|：)\s*$",
                    local_before,
                    re.IGNORECASE,
                )
                if not bare_budget:
                    continue
                scope, label, modality = (
                    "constraint.budget_upper_bound",
                    "budget_upper_bound",
                    "requirement",
                )

        value = _parse_number_text(match.group("value"))
        unit = _currency_unit(match.group(0))
        extracted.append((value, unit, scope, label, modality))

    unique: dict[tuple[str, str], tuple[str, str, str, str, str]] = {}
    for row in extracted:
        unique[(row[2], normalized_value(row[0], "number", row[1]))] = row
    return list(unique.values())


def _cue_distance(
    pattern: re.Pattern[str],
    text: str,
    *,
    amount_start: int,
    amount_end: int,
) -> int | None:
    distances: list[int] = []
    for cue in pattern.finditer(text):
        if cue.end() <= amount_start:
            distances.append(amount_start - cue.end())
        elif cue.start() >= amount_end:
            distances.append(cue.start() - amount_end)
        else:
            distances.append(0)
    return min(distances) if distances else None


def _clean_budget_component_label(value: str) -> str:
    cleaned = str(value or "").strip(" \t\r\n`*_\"'，,。.;；:：")
    cleaned = re.sub(
        r"^(?:保留项|保留|其中|包括|核心(?:的)?|主要(?:的)?)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned[:24].strip()


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
    value = text.strip().replace(",", "")
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
