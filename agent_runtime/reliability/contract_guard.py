from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from agent_runtime.reliability.artifact_digest import build_typed_artifact_digest


CONTROL_OPEN = "<CMJCC_CONTROL>"
CONTROL_CLOSE = "</CMJCC_CONTROL>"
ARTIFACT_OPEN = "<ARTIFACT>"
ARTIFACT_CLOSE = "</ARTIFACT>"

_CONTROL_RE = re.compile(
    rf"{re.escape(CONTROL_OPEN)}(?P<body>.*?){re.escape(CONTROL_CLOSE)}",
    re.S | re.I,
)
_ARTIFACT_RE = re.compile(
    rf"{re.escape(ARTIFACT_OPEN)}(?P<body>.*?){re.escape(ARTIFACT_CLOSE)}",
    re.S | re.I,
)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(?P<body>\{.*?\})\s*```", re.S | re.I)


@dataclass(frozen=True, slots=True)
class ContractContext:
    task_id: str
    agent_id: str
    role: str
    next_action: str
    artifact_ref: str


@dataclass(slots=True)
class ContractGuardResult:
    schema_valid: bool
    contract_status: str
    control: dict[str, Any]
    artifact: str
    artifact_digest: dict[str, Any]
    repair_actions: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    broken_control_header: str = ""
    retry_required: bool = False
    retry_attempted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def guard_agent_output(raw_output: str, context: ContractContext) -> ContractGuardResult:
    """Apply the layered CMJCC output contract guard.

    The guard follows the innovation plan's order: archive/extract, deterministic
    repair, schema validate, default fill, then fallback. The LLM retry itself is
    orchestrated by runtime so this function stays deterministic and testable.
    """

    control_text, artifact, actions = _split_control_and_artifact(raw_output)
    artifact = artifact or raw_output
    digest = build_artifact_digest(artifact)
    errors: list[str] = []

    if not control_text and "{" in raw_output and "}" in raw_output:
        control_text = _extract_json_candidate(raw_output)
        actions.append("extract_json_without_control_tag")

    if not control_text:
        fallback = build_degraded_fallback(
            context,
            artifact_digest=digest,
            errors=["control header missing"],
            broken_control_header="",
        )
        return ContractGuardResult(
            schema_valid=False,
            contract_status="degraded_fallback",
            control=fallback,
            artifact=artifact,
            artifact_digest=digest,
            repair_actions=actions,
            schema_errors=["control header missing"],
            retry_required=True,
        )

    parsed, repair_actions, parse_errors = parse_and_repair_json(control_text)
    actions.extend(repair_actions)
    errors.extend(parse_errors)
    if parsed is None:
        fallback = build_degraded_fallback(
            context,
            artifact_digest=digest,
            errors=errors or ["control header JSON parse failed"],
            broken_control_header=control_text,
        )
        return ContractGuardResult(
            schema_valid=False,
            contract_status="degraded_fallback",
            control=fallback,
            artifact=artifact,
            artifact_digest=digest,
            repair_actions=actions,
            schema_errors=errors or ["control header JSON parse failed"],
            broken_control_header=control_text,
            retry_required=True,
        )

    control, fill_actions = fill_control_defaults(parsed, context, digest)
    actions.extend(fill_actions)
    schema_errors = validate_control(control, context)
    if schema_errors:
        fallback = build_degraded_fallback(
            context,
            artifact_digest=digest,
            errors=schema_errors,
            broken_control_header=control_text,
        )
        return ContractGuardResult(
            schema_valid=False,
            contract_status="degraded_fallback",
            control=fallback,
            artifact=artifact,
            artifact_digest=digest,
            repair_actions=actions,
            schema_errors=schema_errors,
            broken_control_header=control_text,
            retry_required=True,
        )

    return ContractGuardResult(
        schema_valid=True,
        contract_status="repaired" if actions else "valid",
        control=control,
        artifact=artifact,
        artifact_digest=digest,
        repair_actions=actions,
        schema_errors=errors,
        broken_control_header=control_text,
        retry_required=False,
    )


def parse_and_repair_json(text: str) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    actions: list[str] = []
    errors: list[str] = []
    candidate = _extract_json_candidate(text)
    if candidate != text.strip():
        actions.append("extract_json_block")

    attempts = [candidate]
    repaired = _deterministic_json_repair(candidate)
    if repaired != candidate:
        actions.append("deterministic_json_repair")
        attempts.append(repaired)

    for item in attempts:
        try:
            parsed = json.loads(item)
            if isinstance(parsed, dict):
                return parsed, actions, errors
            errors.append(f"control JSON root must be object, got {type(parsed).__name__}")
            return None, actions, errors
        except json.JSONDecodeError as exc:
            last_error = str(exc)

        try:
            parsed = ast.literal_eval(item)
            if isinstance(parsed, dict):
                actions.append("literal_eval_single_quote_json")
                return parsed, actions, errors
        except (SyntaxError, ValueError):
            pass

    errors.append(f"control JSON parse failed: {last_error}")
    return None, actions, errors


def fill_control_defaults(
    control: dict[str, Any], context: ContractContext, artifact_digest: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    actions: list[str] = []
    normalized = dict(control)
    defaults = {
        "msg_type": "agent_output",
        "task_id": context.task_id,
        "from": context.agent_id,
        "action_completed": f"{context.agent_id}_completed",
    }
    for key, value in defaults.items():
        if not normalized.get(key):
            normalized[key] = value
            actions.append(f"default_{key}")

    memory_card = normalized.get("memory_card")
    if not isinstance(memory_card, dict):
        memory_card = {}
        normalized["memory_card"] = memory_card
        actions.append("default_memory_card")
    _fill(memory_card, "summary", artifact_digest["summary"], actions)
    _fill(memory_card, "key_points", [], actions)
    _fill(memory_card, "evidence_snippets", [], actions)
    _fill(memory_card, "tags", [], actions)
    _fill(memory_card, "reuse_scope", [], actions)
    _fill(memory_card, "confidence", 0.5, actions)
    _fill(memory_card, "importance_hint", 0.5, actions)
    _fill(memory_card, "coverage_score", 0.5, actions)
    _fill(memory_card, "compression_loss_risk", "medium", actions)
    _fill(memory_card, "raw_required_hint", False, actions)
    _fill(memory_card, "sufficient_for_actions", [], actions)
    _fill(memory_card, "insufficient_for_actions", [], actions)

    if not isinstance(normalized.get("claim_cards"), list):
        normalized["claim_cards"] = []
        actions.append("default_claim_cards")

    handoff = normalized.get("handoff_suggestion")
    if not isinstance(handoff, dict):
        handoff = {}
        normalized["handoff_suggestion"] = handoff
        actions.append("default_handoff_suggestion")
    _fill(handoff, "next_action", context.next_action, actions)
    _fill(handoff, "required_capabilities", [], actions)
    _fill(handoff, "suggested_memory_refs", [], actions)

    cost = normalized.get("cost_report")
    if not isinstance(cost, dict):
        cost = {}
        normalized["cost_report"] = cost
        actions.append("default_cost_report")
    _fill(cost, "output_tokens", 0, actions)
    _fill(cost, "summary_tokens", 0, actions)
    _fill(cost, "raw_pointer", context.artifact_ref, actions)

    return normalized, actions


def validate_control(control: dict[str, Any], context: ContractContext) -> list[str]:
    errors: list[str] = []
    if control.get("msg_type") != "agent_output":
        errors.append("msg_type must be agent_output")
    if control.get("task_id") != context.task_id:
        errors.append("task_id mismatch")
    if control.get("from") != context.agent_id:
        errors.append("from agent mismatch")
    if not isinstance(control.get("memory_card"), dict):
        errors.append("memory_card must be object")
    else:
        memory = control["memory_card"]
        if not isinstance(memory.get("tags"), list):
            errors.append("memory_card.tags must be array")
        if not isinstance(memory.get("confidence"), (int, float)):
            errors.append("memory_card.confidence must be number")
    if not isinstance(control.get("claim_cards"), list):
        errors.append("claim_cards must be array")
    if not isinstance(control.get("handoff_suggestion"), dict):
        errors.append("handoff_suggestion must be object")
    if not isinstance(control.get("cost_report"), dict):
        errors.append("cost_report must be object")
    return errors


def build_degraded_fallback(
    context: ContractContext,
    *,
    artifact_digest: dict[str, Any],
    errors: list[str],
    broken_control_header: str,
) -> dict[str, Any]:
    del broken_control_header
    return {
        "schema_valid": False,
        "contract_status": "degraded_fallback",
        "action": "unknown_or_untrusted",
        "task_id": context.task_id,
        "from": context.agent_id,
        "artifact_ref": context.artifact_ref,
        "artifact_digest": artifact_digest,
        "schema_errors": errors,
        "allowed_next_step": "review_or_retry_only",
    }


def build_artifact_digest(artifact: str) -> dict[str, Any]:
    return build_typed_artifact_digest(artifact)


def render_contract_retry_prompt(
    *,
    context: ContractContext,
    result: ContractGuardResult,
    target_schema: dict[str, Any] | None = None,
) -> str:
    schema = target_schema or {
        "schema_name": "CMJCC_CONTROL",
        "required": [
            "msg_type",
            "task_id",
            "from",
            "action_completed",
            "memory_card",
            "claim_cards",
            "handoff_suggestion",
            "cost_report",
        ],
    }
    payload = {
        "retry_mode": "format_only",
        "context_mode": "pruned",
        "agent_id": context.agent_id,
        "role_profile": context.role,
        "task_id": context.task_id,
        "action_completed": f"{context.agent_id}_completed",
        "schema_name": "CMJCC_CONTROL",
        "schema_errors": result.schema_errors,
        "broken_control_header": result.broken_control_header[:1200],
        "artifact_pointer": context.artifact_ref,
        "artifact_digest": result.artifact_digest,
        "target_schema": schema,
    }
    return (
        "你上一条 CMJCC_CONTROL 不符合 Schema。\n\n"
        "不要重新执行任务。不要重新输出 ARTIFACT。不要解释。\n"
        "只根据提供的损坏控制头、Schema 错误和任务元信息，重新输出合法的 "
        "<CMJCC_CONTROL> JSON。</CMJCC_CONTROL>\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _split_control_and_artifact(raw_output: str) -> tuple[str, str, list[str]]:
    actions: list[str] = []
    control_match = _CONTROL_RE.search(raw_output)
    artifact_match = _ARTIFACT_RE.search(raw_output)
    control = control_match.group("body").strip() if control_match else ""
    artifact = artifact_match.group("body").strip() if artifact_match else ""
    if control_match:
        actions.append("extract_cmjcc_control")
    if artifact_match:
        actions.append("extract_artifact_body")
    return control, artifact, actions


def _extract_json_candidate(text: str) -> str:
    stripped = text.strip()
    fenced = _FENCED_JSON_RE.search(stripped)
    if fenced:
        return fenced.group("body").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1].strip()
    return stripped


def _deterministic_json_repair(text: str) -> str:
    repaired = text.strip()
    repaired = re.sub(r"^\s*```(?:json)?", "", repaired, flags=re.I).strip()
    repaired = re.sub(r"```\s*$", "", repaired).strip()
    repaired = re.sub(r"//.*?$", "", repaired, flags=re.M)
    repaired = re.sub(r"/\*.*?\*/", "", repaired, flags=re.S)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r"\bTrue\b", "true", repaired)
    repaired = re.sub(r"\bFalse\b", "false", repaired)
    repaired = re.sub(r"\bNone\b", "null", repaired)
    if repaired.count("{") > repaired.count("}"):
        repaired += "}" * (repaired.count("{") - repaired.count("}"))
    if repaired.count("[") > repaired.count("]"):
        repaired += "]" * (repaired.count("[") - repaired.count("]"))
    return repaired


def _fill(target: dict[str, Any], key: str, value: Any, actions: list[str]) -> None:
    if key not in target or target[key] is None:
        target[key] = value
        actions.append(f"default_{key}")


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return False
    try:
        json.loads(stripped)
        return True
    except json.JSONDecodeError:
        return False
