"""Rules-first validation for reviewer-produced final delivery candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass


_REVIEW_HEADING_RE = re.compile(
    r"(?im)^\s*(?:[#>*-]+\s*)?(?:reviewer\s*)?(?:最终)?"
    r"(?:审查意见|审查结论|审查报告|评审意见|审核意见|review findings?)\s*[:：]?"
)
_DELEGATED_REVISION_RE = re.compile(
    r"(?is)(?:请|建议|需要|必须).{0,24}"
    r"(?:planner|writer|规划者|撰写者|写作者).{0,32}"
    r"(?:修订|补充|重写|继续|完善|据此修改)"
)
_DELIVERY_BOUNDARY_RE = re.compile(
    r"(?im)(?:"
    r"^\s*(?:#{1,4}\s*)?(?:\*{1,2})?(?:【)?最终(?:可交付|可执行)?"
    r"(?:交付物|答案|规划方案|方案|手册|版本)(?=\s*[:：\-—（(】])|"
    r"^\s*#{1,4}\s*(?!.*审查).{0,80}(?:最终旅行手册|完整旅行手册)|"
    r"^\s*(?:#{1,4}\s*)?(?:完整(?:方案|手册|行程|答案)|"
    r"需求(?:与约束)?清单|每日安排|预算表|决策日志)\s*[:：]?"
    r")"
)
_TABLE_RE = re.compile(r"(?m)^\s*\|.*\|\s*$")
_MONEY_RE = re.compile(r"(?:¥|￥)?\s*\d+(?:\.\d+)?\s*(?:元|块)")


@dataclass(frozen=True, slots=True)
class FinalDeliveryAssessment:
    valid: bool
    status: str
    reasons: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    review_only: bool
    body: str

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "status": self.status,
            "reasons": list(self.reasons),
            "missing_requirements": list(self.missing_requirements),
            "review_only": self.review_only,
            "body_chars": len(self.body),
        }


def assess_final_delivery(
    *,
    request: str,
    content: str,
    source: str = "",
    expected_source: str = "",
    marker: str = "",
    require_marker: bool = False,
    minimum_body_chars: int = 0,
) -> FinalDeliveryAssessment:
    """Validate a final candidate without invoking another model."""

    reasons: list[str] = []
    if expected_source and source != expected_source:
        reasons.append("unexpected_final_source")
    marker_present = has_exact_last_line_marker(content, marker) if marker else False
    if require_marker and not marker_present:
        reasons.append("exact_final_marker_missing")
    body = strip_exact_last_line_marker(content, marker) if marker_present else content.strip()
    if not body:
        reasons.append("empty_final_body")
    if len(body) < max(0, minimum_body_chars):
        reasons.append("final_body_too_short")

    review_heading = bool(_REVIEW_HEADING_RE.search(body))
    delegated_revision = bool(_DELEGATED_REVISION_RE.search(body))
    delivery_boundary = bool(_DELIVERY_BOUNDARY_RE.search(body))
    review_only = delegated_revision or (review_heading and not delivery_boundary)
    if review_only:
        reasons.append("review_feedback_not_final_artifact")

    missing = _missing_task_requirements(request, body)
    if missing:
        reasons.append("task_delivery_requirements_missing")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return FinalDeliveryAssessment(
        valid=not unique_reasons,
        status="validated_final_artifact" if not unique_reasons else "degraded_fallback",
        reasons=unique_reasons,
        missing_requirements=tuple(missing),
        review_only=review_only,
        body=body,
    )


def has_exact_last_line_marker(content: str, marker: str) -> bool:
    if not marker.strip():
        return False
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return bool(lines and lines[-1] == marker.strip())


def strip_exact_last_line_marker(content: str, marker: str) -> str:
    lines = content.rstrip().splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and marker.strip() and lines[-1].strip() == marker.strip():
        lines.pop()
    return "\n".join(lines).rstrip()


def _missing_task_requirements(request: str, body: str) -> list[str]:
    request_text = " ".join(request.split()).lower()
    body_text = " ".join(body.split()).lower()
    missing: list[str] = []

    if "需求和约束" in request_text or "需求与约束" in request_text:
        _require_terms(body_text, missing, "需求与约束清单", ("需求",), ("约束",))

    if re.search(r"(?:筛选|列出|推荐)\s*3\s*个", request_text):
        numbered = all(
            re.search(
                rf"(?m)^\s*(?:#{{1,6}}\s*)?(?:{number}|{'一二三'[number - 1]})\s*[.、)]",
                body,
            )
            for number in (1, 2, 3)
        )
        if not numbered:
            missing.append("三个候选项")

    asks_itinerary = "行程" in request_text and bool(
        re.search(r"3\s*天\s*2\s*晚", request_text)
    )
    asks_final_manual = "最终旅行手册" in request_text
    if asks_itinerary or asks_final_manual:
        if not all(_contains_day(body_text, day) for day in (1, 2, 3)):
            missing.append("三天逐日安排")

    if "预算" in request_text and any(
        term in request_text for term in ("优化", "预算表", "控制", "检查", "手册")
    ):
        budget_terms = sum(
            term in body_text for term in ("交通", "住宿", "餐饮", "活动", "合计", "总计")
        )
        if len(_MONEY_RE.findall(body)) < 3 or (not _TABLE_RE.search(body) and budget_terms < 3):
            missing.append("可核算预算明细")

    if asks_final_manual:
        _require_terms(body_text, missing, "注意事项", ("注意事项", "提醒"))
        _require_terms(body_text, missing, "备选方案", ("备选", "替代方案"))

    if "决策日志" in request_text:
        _require_terms(body_text, missing, "决策日志", ("决策日志",))
    if "晕车" in request_text:
        _require_terms(body_text, missing, "晕车约束", ("晕车", "盘山路"))
    if re.search(r"10\s*点以后", request_text):
        _require_terms(body_text, missing, "首日十点后出发", ("10:00", "10 点", "十点"))
    if "下雨" in request_text or "雨天" in request_text:
        _require_terms(body_text, missing, "雨天低风险备选", ("室内", "低风险", "雨天备选"))
    if "2600" in request_text:
        _require_terms(body_text, missing, "2600元预算上限", ("2600",))
    if "不吃辣" in request_text:
        _require_terms(body_text, missing, "不吃辣餐饮约束", ("不吃辣", "不辣", "免辣"))
    if "伴手礼" in request_text:
        _require_terms(body_text, missing, "伴手礼预算", ("伴手礼",), ("200",))

    return list(dict.fromkeys(missing))


def _contains_day(text: str, day: int) -> bool:
    chinese = {1: "一", 2: "二", 3: "三"}[day]
    return bool(
        re.search(
            rf"(?:第\s*(?:{day}|{chinese})\s*天|day\s*{day}\b|d{day}\b)",
            text,
        )
    )


def _require_terms(
    text: str,
    missing: list[str],
    label: str,
    *term_groups: tuple[str, ...],
) -> None:
    if any(not any(term.lower() in text for term in group) for group in term_groups):
        missing.append(label)
