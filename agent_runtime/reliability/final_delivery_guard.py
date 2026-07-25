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
    r"(?=\s*(?:[:：\-—（(】]|$))|"
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
_PRIOR_ARTIFACT_REFERENCE_RE = re.compile(
    r"(?is)(?:"
    r"(?:\u524d\u5e8f|\u6b64\u524d|\u4e0a\u4e00\u7248|\u4e0a\u8f6e)"
    r".{0,48}(?:writer|artifact|draft|\u4ea7\u51fa|\u8349\u7a3f|\u6210\u679c)"
    r"|(?:previous|prior).{0,48}(?:writer|artifact|draft|output)"
    r"|artifact_state\s*:"
    r")"
)
_SUBMITTED_ARTIFACT_REFERENCE_RE = re.compile(
    r"(?is)(?:"
    r"(?:writer|author|specialist|撰写者|执行者).{0,40}"
    r"(?:提交|产出|成果|草稿|报告|artifact|draft|output)"
    r"|(?:团队消息|当前消息|本轮).{0,80}"
    r"(?:writer|author|撰写者).{0,40}(?:提交|产出|成果|草稿|报告)"
    r"|(?:已)?对[^。\n]{1,120}(?:提交的?|产出的?|成果|草稿|报告)"
    r")"
)
_REVIEW_PROCESS_SIGNAL_RE = re.compile(
    r"(?is)(?:"
    r"作为.{0,40}(?:验收|审查|审核|评审)(?:专家|角色|人员)"
    r"|对.{0,100}(?:提交|产出|成果|草稿|报告).{0,50}"
    r"(?:进行|完成).{0,8}(?:审查|验收|审核|评审|检查)"
    r"|检查重点|验收检查|产物验收|所有产出.{0,24}通过验收"
    r")"
)
_INTEGRATED_FINAL_ARTIFACT_RE = re.compile(
    r"(?is)(?:"
    r"以下(?:为|给出).{0,28}(?:整合后|修订后|纠正后|重写后).{0,24}"
    r"(?:完整|最终)?(?:成果|方案|报告|答案|交付物)"
    r"|已完成必要修正.{0,40}(?:完整|最终)(?:成果|方案|报告|答案)"
    r")"
)
_PRIOR_ARTIFACT_APPROVAL_RE = re.compile(
    r"(?is)(?:"
    r"\u9a8c\u6536|\u6279\u51c6|\u901a\u8fc7|\u7b26\u5408\u89c4\u8303|"
    r"\u672a\u53d1\u73b0.{0,32}\u95ee\u9898|"
    r"approved?|accepted?|validated?"
    r")"
)
_REVISION_REQUIRED_RE = re.compile(
    r"(?is)(?:"
    r"(?:\u8bf7|(?<!\u65e0)(?<!\u4e0d)\u9700|\u9700\u8981|\u5fc5\u987b|\u5f85)"
    r".{0,24}(?:\u4fee\u8ba2|\u4fee\u6539|\u91cd\u5199|\u8865\u5145|\u8865\u9f50)"
    r"|(?:\u4fee\u8ba2|\u4fee\u6539|\u91cd\u5199|\u8865\u5145|\u8865\u9f50)"
    r"(?:\s*(?:\u8981\u6c42|\u6e05\u5355|\u6307\u4ee4)|"
    r".{0,6}(?:\u540e\u518d|\u540e\u91cd\u65b0))"
    r"|\u4e0d\u5408\u683c|\u672a\u901a\u8fc7|\u5c1a\u672a|"
    r"revise|revision|required\s+changes?|repair|failed?"
    r")"
)
_BUDGET_CONTEXT_RE = re.compile(
    r"(?i)(?:\u603b?\u9884\u7b97|budget|total\s+cost|cost\s+cap)"
)
_UPPER_BOUND_SIGNAL_RE = re.compile(
    r"(?i)(?:"
    r"\u4ee5\u5185|\u4e0a\u9650|"
    r"\u4e0d(?:\u5f97|\u53ef|\u80fd)?\u8d85\u8fc7|"
    r"\u81f3\u591a|\u6700\u591a|"
    r"<=|under|no\s+more\s+than|at\s+most|maximum|max(?:imum)?|cap"
    r")"
)
_BUDGET_RESULT_RE = re.compile(
    r"(?i)(?:"
    r"\u603b\u8ba1|\u5408\u8ba1|\u603b\u91d1\u989d|\u603b\u8d39\u7528|"
    r"\u9884\u7b97|budget|total(?:\s+cost)?|cost"
    r")"
)
_NUMBER_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")


@dataclass(frozen=True, slots=True)
class FinalDeliveryAssessment:
    valid: bool
    status: str
    reasons: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    review_only: bool
    approved_prior_artifact: bool
    body: str

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "status": self.status,
            "reasons": list(self.reasons),
            "missing_requirements": list(self.missing_requirements),
            "review_only": self.review_only,
            "approved_prior_artifact": self.approved_prior_artifact,
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
    delivery_boundary = has_explicit_delivery_boundary(body)
    approved_prior_artifact = is_prior_artifact_approval(body)
    review_only = approved_prior_artifact or (
        not delivery_boundary
        and (review_heading or delegated_revision or review_only_signal)
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
    numeric_violations = _numeric_upper_bound_violations(
        grounding_text=grounding_text,
        body=body,
    )
    if numeric_violations:
        reasons.append("numeric_upper_bound_violation")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return FinalDeliveryAssessment(
        valid=not unique_reasons,
        status="validated_final_artifact" if not unique_reasons else "degraded_fallback",
        reasons=unique_reasons,
        missing_requirements=numeric_violations,
        review_only=review_only,
        approved_prior_artifact=approved_prior_artifact,
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


def has_explicit_delivery_boundary(content: str) -> bool:
    return bool(_DELIVERY_BOUNDARY_RE.search(str(content or "")))


def is_prior_artifact_approval(content: str) -> bool:
    body = str(content or "").strip()
    if not body or len(body) > 2600:
        return False
    if _REVISION_REQUIRED_RE.search(body):
        return False
    if _INTEGRATED_FINAL_ARTIFACT_RE.search(body):
        return False
    prior_reference = bool(_PRIOR_ARTIFACT_REFERENCE_RE.search(body))
    submitted_artifact_reference = bool(
        _SUBMITTED_ARTIFACT_REFERENCE_RE.search(body)
        and _REVIEW_PROCESS_SIGNAL_RE.search(body)
    )
    return bool(
        (prior_reference or submitted_artifact_reference)
        and _PRIOR_ARTIFACT_APPROVAL_RE.search(body)
    )


def _numeric_upper_bound_violations(
    *,
    grounding_text: str,
    body: str,
) -> tuple[str, ...]:
    budget_bounds: list[float] = []
    for line in str(grounding_text or "").splitlines():
        if not _BUDGET_CONTEXT_RE.search(line):
            continue
        if not _UPPER_BOUND_SIGNAL_RE.search(line):
            continue
        values = _line_numbers(line)
        if values:
            budget_bounds.append(max(values))
    if not budget_bounds:
        return ()

    active_bound = budget_bounds[-1]
    violations: list[str] = []
    for line in str(body or "").splitlines():
        if not _BUDGET_RESULT_RE.search(line):
            continue
        values = [
            value
            for value in _line_numbers(line)
            if not (
                1900 <= value <= 2100
                and re.search(r"(?i)(?:year|date|\u5e74|\u65e5\u671f)", line)
            )
        ]
        observed = max(values, default=0.0)
        if observed > active_bound:
            violations.append(
                "budget_upper_bound="
                f"{_format_number(active_bound)};"
                "observed_total_upper="
                f"{_format_number(observed)}"
            )
    return tuple(dict.fromkeys(violations))


def _line_numbers(text: str) -> list[float]:
    return [float(match.group(1)) for match in _NUMBER_RE.finditer(text)]


def _format_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _normalize_grounding_text(text: str) -> str:
    return re.sub(r"[\s“”\"'`*_]+", "", str(text)).lower()
