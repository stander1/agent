from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from agent_runtime.memory.claim_extractor import (
    extract_claim_cards,
    normalized_value,
)


REVIEW_ACTIONS = frozenset(
    {
        "REVIEW_OUTPUT",
        "REVIEW_SCHEMA",
        "VERIFY_CLAIM",
        "DIAGNOSE_FAILURE",
    }
)
REVIEW_CAPABILITIES = frozenset(
    {
        "validation",
        "schema_review",
        "failure_review",
        "final_deliverable_review",
    }
)

_CLAUSE_SPLIT_RE = re.compile(
    r"[\r\n]+|(?<=[。！？；!?;])\s*|(?<=\.)\s+"
)
_BLOCKING_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:审查|评审|验收|检查).{0,16}(?:不通过|未通过|失败|拒绝|不合格)",
        r"(?:不通过|未通过|不合格|无法通过|不能通过).{0,16}(?:审查|评审|验收|检查)?",
        r"(?:违反|违背|不满足|未满足|不符合).{0,28}(?:硬约束|强约束|约束|要求|条件)",
        r"(?:关键|阻断|致命|严重).{0,18}(?:问题|缺陷|风险|错误)",
        r"(?:必须|需要).{0,24}(?:修正|修改|重做|替换|更换|纠正)",
        r"(?:review|validation|acceptance).{0,20}(?:failed|rejected|not\s+approved)",
        r"(?:violates?|fails?\s+to\s+meet|does\s+not\s+satisfy).{0,32}(?:hard\s+)?(?:constraint|requirement)",
        r"(?:blocking|critical|fatal|severe).{0,18}(?:issue|defect|risk|error)",
        r"(?:must|requires?\s+).{0,24}(?:revise|revision|correct|replace|rework)",
    )
)
_NEGATED_BLOCKING_RE = re.compile(
    r"(?:未发现|没有|不存在|并无|无)(?:任何)?(?:关键|阻断|致命|严重)?"
    r"(?:问题|缺陷|风险|错误)|"
    r"(?:no|without)\s+(?:blocking|critical|fatal|severe)?\s*"
    r"(?:issue|defect|risk|error)s?",
    re.IGNORECASE,
)
_APPROVAL_RE = re.compile(
    r"(?:审查|评审|验收|检查)(?:结果|结论)?\s*"
    r"(?:为|：|:)?\s*(?:已)?(?:通过|合格|批准)|"
    r"(?:approved|review\s+passed|validation\s+passed|accepted)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ReviewConflictDecision:
    authoritative: bool
    blocking: bool
    authority_reasons: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    blocking_summary: str = ""
    targeted_memory_ids: tuple[str, ...] = ()
    targeted_claim_ids: tuple[str, ...] = ()
    targeted_semantic_keys: tuple[str, ...] = ()
    target_matches: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_review_conflict(
    *,
    output_text: str,
    semantic_action: str,
    capabilities: Iterable[str],
    memory_rows: Iterable[Mapping[str, Any]],
) -> ReviewConflictDecision:
    action = str(semantic_action or "").strip().upper()
    normalized_capabilities = {
        str(capability or "").strip().lower()
        for capability in capabilities
        if str(capability or "").strip()
    }
    authority_reasons: list[str] = []
    if action in REVIEW_ACTIONS:
        authority_reasons.append(f"semantic_action:{action}")
    matched_capabilities = sorted(
        normalized_capabilities & REVIEW_CAPABILITIES
    )
    authority_reasons.extend(
        f"capability:{capability}" for capability in matched_capabilities
    )
    authoritative = bool(authority_reasons)
    if not authoritative:
        return ReviewConflictDecision(
            authoritative=False,
            blocking=False,
        )

    clauses = _clauses(output_text)
    blocking_indexes = [
        index
        for index, clause in enumerate(clauses)
        if _is_blocking_clause(clause)
    ]
    if not blocking_indexes:
        return ReviewConflictDecision(
            authoritative=True,
            blocking=False,
            authority_reasons=tuple(authority_reasons),
        )

    segments = [
        _clause_window(clauses, index)
        for index in blocking_indexes
    ]
    matches: list[dict[str, str]] = []
    memory_ids: list[str] = []
    claim_ids: list[str] = []
    semantic_keys: list[str] = []

    for row in memory_rows:
        memory_id = str(row.get("memory_id") or "")
        revision_guard = row.get("revision_guard")
        if not isinstance(revision_guard, Mapping):
            revision_guard = row
        subject = str(revision_guard.get("subject") or "project:current")
        active_facts = revision_guard.get("active_facts")
        if not isinstance(active_facts, list):
            continue
        for fact in active_facts:
            if not isinstance(fact, Mapping):
                continue
            matched_segment = next(
                (
                    segment
                    for segment in segments
                    if _fact_matches_segment(
                        fact=fact,
                        segment=segment,
                        subject=subject,
                    )
                ),
                "",
            )
            if not matched_segment:
                continue
            claim_id = str(fact.get("claim_id") or "")
            semantic_key = str(fact.get("semantic_key") or "")
            matches.append(
                {
                    "memory_id": memory_id,
                    "claim_id": claim_id,
                    "semantic_key": semantic_key,
                    "slot_id": str(fact.get("slot_id") or ""),
                    "scope": str(fact.get("scope") or ""),
                    "value": str(fact.get("value") or ""),
                    "evidence": matched_segment[:360],
                }
            )
            if memory_id and memory_id not in memory_ids:
                memory_ids.append(memory_id)
            if claim_id and claim_id not in claim_ids:
                claim_ids.append(claim_id)
            if semantic_key and semantic_key not in semantic_keys:
                semantic_keys.append(semantic_key)

    blocking_clauses = [clauses[index] for index in blocking_indexes]
    return ReviewConflictDecision(
        authoritative=True,
        blocking=True,
        authority_reasons=tuple(authority_reasons),
        blocking_reasons=tuple(blocking_clauses),
        blocking_summary=_blocking_summary(blocking_clauses),
        targeted_memory_ids=tuple(memory_ids),
        targeted_claim_ids=tuple(claim_ids),
        targeted_semantic_keys=tuple(semantic_keys),
        target_matches=tuple(matches),
    )


def build_review_blocker_claim(
    decision: ReviewConflictDecision,
    *,
    subject: str,
    source_pointer: str,
) -> dict[str, Any]:
    identity_source = "|".join(decision.targeted_semantic_keys)
    if not identity_source:
        identity_source = decision.blocking_summary
    scope_hash = hashlib.sha256(
        identity_source.encode("utf-8")
    ).hexdigest()[:12]
    summary = decision.blocking_summary or "Authoritative review blocked the prior result."
    return {
        "subject": subject,
        "raw_slot_text": "review_blocking_verdict",
        "slot_id": "slot.system.failure_pattern",
        "scope": f"review.blocking.{scope_hash}",
        "value": summary,
        "value_type": "string",
        "unit": "",
        "raw_text": summary,
        "summary": summary,
        "certainty": "confirmed",
        "modality": "asserted",
        "polarity": "positive",
        "confidence": 0.94,
        "revision_kind": "asserted",
        "temporal_scope": "cross_task",
        "source_pointer": source_pointer,
    }


def _clauses(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", item).strip(" -*#>")
        for item in _CLAUSE_SPLIT_RE.split(str(text or ""))
        if item.strip(" -*#>")
    ]


def _is_blocking_clause(clause: str) -> bool:
    if _NEGATED_BLOCKING_RE.search(clause):
        return False
    if not any(pattern.search(clause) for pattern in _BLOCKING_PATTERNS):
        return False
    if _APPROVAL_RE.search(clause) and not re.search(
        r"(?:但|但是|然而|except|but|however).{0,40}"
        r"(?:不通过|未通过|违反|阻断|必须|failed|rejected|violat|blocking|must)",
        clause,
        re.IGNORECASE,
    ):
        return False
    return True


def _clause_window(clauses: list[str], index: int) -> str:
    start = max(0, index - 1)
    end = min(len(clauses), index + 2)
    return " ".join(clauses[start:end])


def _fact_matches_segment(
    *,
    fact: Mapping[str, Any],
    segment: str,
    subject: str,
) -> bool:
    expected_slot = str(fact.get("slot_id") or "")
    expected_scope = str(fact.get("scope") or "general")
    expected_type = str(fact.get("value_type") or "string")
    expected_unit = str(fact.get("unit") or "")
    expected_value = normalized_value(
        fact.get("value"),
        expected_type,
        expected_unit,
    )
    claims = extract_claim_cards(
        segment,
        subject=subject,
        source_pointer="review_conflict_guard",
        default_confidence=0.9,
    )
    for claim in claims:
        if str(claim.get("slot_id") or "") != expected_slot:
            continue
        if str(claim.get("scope") or "general") != expected_scope:
            continue
        candidate_value = normalized_value(
            claim.get("value"),
            str(claim.get("value_type") or "string"),
            str(claim.get("unit") or ""),
        )
        if candidate_value == expected_value:
            return True

    if expected_type.casefold() in {"number", "integer", "float"}:
        return False
    literal = str(fact.get("value") or "").strip()
    if len(literal) < 2:
        return False
    return literal.casefold() in segment.casefold()


def _blocking_summary(clauses: list[str], limit: int = 420) -> str:
    summary = " ".join(dict.fromkeys(clauses)).strip()
    return summary[:limit]
