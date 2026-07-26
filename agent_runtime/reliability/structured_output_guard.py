from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.reliability.contract_guard import parse_and_repair_json


@dataclass(slots=True)
class StructuredJsonGuardResult:
    parsed: dict[str, Any] | None
    repair_actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.parsed is not None


def guard_structured_json_object(text: str) -> StructuredJsonGuardResult:
    parsed, actions, errors = parse_and_repair_json(text)
    return StructuredJsonGuardResult(
        parsed=parsed,
        repair_actions=list(actions),
        errors=list(errors),
    )


def render_pruned_json_retry_prompt(
    *,
    task_id: str,
    candidate_ids: list[str],
    invalid_response: str,
    validation_error: str,
    evaluation_fields: list[str],
    response_kind: str,
    max_invalid_chars: int = 16000,
) -> str:
    payload = {
        "retry_mode": "format_only",
        "context_mode": "pruned",
        "response_kind": response_kind,
        "task_id": task_id,
        "candidate_ids": list(candidate_ids),
        "required_evaluation_fields": list(evaluation_fields),
        "validation_error": validation_error[:1200],
        "invalid_response": invalid_response[:max_invalid_chars],
    }
    return (
        "你是刚才执行匿名评估的同一个评分器。下面的响应没有通过 JSON "
        "结构或字段校验。只修复格式、缺失字段和截断结构，不改变已有评分、"
        "事实判断、finding 严重级别或候选优劣。必须完整覆盖 candidate_ids，"
        "且只输出一个 JSON 对象，不要输出 Markdown 或解释。若坏响应中根本"
        "没有某个候选的语义评价，不得凭空补写；请返回 "
        '{"repair_status":"full_reaudit_required","missing_candidate_ids":[]}'
        "，由外层使用原证据重新评分。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def concise_reaudit_suffix(
    *,
    validation_error: str,
    candidate_ids: list[str],
    technical: bool,
) -> str:
    finding_rule = (
        "每个候选最多保留 2 个最重要 finding；evidence 不超过 80 字，"
        "issue 不超过 160 字，repair 不超过 120 字；strengths 最多 2 项。"
        if technical
        else "每个候选 strengths 和 risks 各最多 2 项，每项不超过 100 字。"
    )
    return (
        "\n\n前两次输出未通过结构校验，现在重新执行同一评分标准。"
        "为避免响应截断，请压缩说明但不得省略任何候选、分项分数、"
        "交付判定或关键技术缺陷。"
        f"{finding_rule}"
        f"必须覆盖 candidate_ids={candidate_ids!r}。"
        f"上次校验错误：{validation_error[:600]}。"
        "只输出合法 JSON。"
    )


def aggregate_attempt_usage(
    attempts: list[dict[str, Any]],
) -> dict[str, int]:
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    for attempt in attempts:
        usage = attempt.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in totals:
            totals[key] += _int(usage.get(key))
    return totals


def response_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
