"""Rules-first validation for reviewer-produced final delivery candidates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


_REVIEW_HEADING_RE = re.compile(
    r"(?im)^\s*(?:[#>*-]+\s*)?(?:reviewer(?:\s*agent)?\s*)?(?:最终)?"
    r"(?:审查意见|审查结论|审查报告|审查发现|评审意见|审核意见|review findings?)\s*[:：]?"
)
_DELEGATED_REVISION_RE = re.compile(
    r"(?is)(?:请|建议|需|需要|必须).{0,24}"
    r"(?:planner|writer|规划者|撰写者|写作者|团队|协作组|agent).{0,32}"
    r"(?:修订|补充|重写|继续|完善|据此修改)"
)
_REVIEW_ONLY_SIGNAL_RE = re.compile(
    r"(?is)(?:重大问题(?:清单|发现)|可执行修订清单|修订清单|"
    r"当前产出(?:不合格|未通过)|审查不通过|"
    r"尚未达到.{0,40}(?:标准|要求)|修订指令|审查才会通过|"
    r"请(?:在)?下一轮(?:产出|修订|提交)|供\s*(?:planner|writer).{0,12}(?:遵循|修订))"
)
_DELIVERY_BOUNDARY_RE = re.compile(
    r"(?im)(?:"
    r"^\s*(?:#{1,4}\s*)?(?:\*{1,2})?(?:【)?最终(?:可交付|可执行)?"
    r"(?:交付物|成果|答案|规划方案|方案|文档|报告|结果|实现|手册|版本)"
    r"(?=\s*[:：\-—（(】])|"
    r"^\s*#{1,4}\s*(?!.*审查).{0,80}(?:最终|完整)"
    r"(?:成果|答案|方案|文档|报告|结果|实现|手册)|"
    r"^\s*(?:#{1,4}\s*)?完整(?:成果|答案|方案|文档|报告|结果|实现|手册)\s*[:：]?"
    r")"
)
_USER_CONFIRMED_QUOTE_RE = re.compile(
    r"(?is)(?:根据\s*)?(?:您|用户)(?:的)?"
    r"(?:最新|明确|已经|已)?(?:确认|指出|说明|提出|要求)"
    r"\s*(?:(?:为|是|内容为|事项为)\s*)?[:：,，(（]?\s*"
    r"[“\"]([^”\"\n]{3,180})[”\"]"
)


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
    grounding_contexts: Sequence[str] = (),
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
    review_only_signal = bool(_REVIEW_ONLY_SIGNAL_RE.search(body))
    delivery_boundary = bool(_DELIVERY_BOUNDARY_RE.search(body))
    review_only = not delivery_boundary and (
        review_heading or delegated_revision or review_only_signal
    )
    if review_only:
        reasons.append("review_feedback_not_final_artifact")

    grounding_text = "\n".join(
        text for text in (request, *grounding_contexts) if str(text).strip()
    )
    if grounding_text:
        normalized_grounding = _normalize_grounding_text(grounding_text)
        if any(
            _normalize_grounding_text(quote) not in normalized_grounding
            for quote in _USER_CONFIRMED_QUOTE_RE.findall(body)
        ):
            reasons.append("ungrounded_user_confirmation_claim")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return FinalDeliveryAssessment(
        valid=not unique_reasons,
        status="validated_final_artifact" if not unique_reasons else "degraded_fallback",
        reasons=unique_reasons,
        missing_requirements=(),
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


def _normalize_grounding_text(text: str) -> str:
    return re.sub(r"[\s“”\"'`*_]+", "", str(text)).lower()
