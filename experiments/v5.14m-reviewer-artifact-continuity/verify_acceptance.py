from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autogen_agentchat.messages import TextMessage

from agent_runtime.adapters.autogen_termination import resolve_final_artifact
from agent_runtime.memory.claim_extractor import extract_claim_cards
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.reliability.final_delivery_guard import assess_final_delivery


MARKER = "FINAL_ANSWER_READY"
EXPERIMENT_DIR = REPO_ROOT / "experiments" / "v5.14m-reviewer-artifact-continuity"
V514L_DIR = REPO_ROOT / "experiments" / "v5.14l-typed-reliability-formal-regression"
FROZEN_AGENT_PATHS = {
    "agent_config_A.json": (
        REPO_ROOT / "experiments" / "ordinary-developer-autogen" / "agent_config.json"
    ),
    "agent_config_B.json": (
        REPO_ROOT
        / "experiments"
        / "v5.14b-fair-cost-quality-preflight"
        / "agent_config_B.json"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify reviewer artifact ownership and decision continuity."
    )
    parser.add_argument("--unittest-output", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(*, unittest_output: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    unittest_text = _read_text(unittest_output)
    checks.append(
        _check(
            "full_unittest_suite_passed",
            "\nOK\n" in unittest_text.replace("\r\n", "\n"),
            _last_nonempty_line(unittest_text),
        )
    )

    revision = assess_final_delivery(
        request="Deliver the complete current artifact.",
        content=(
            "## Final deliverable\n"
            "The current output is only a revision checklist and is missing "
            "the required complete artifact. Writer must revise and resubmit.\n"
            f"{MARKER}"
        ),
        source="quality_gate",
        marker=MARKER,
        require_marker=True,
    )
    checks.append(
        _check(
            "revision_checklist_cannot_cross_delivery_boundary",
            not revision.valid
            and revision.review_only
            and "review_feedback_not_final_artifact" in revision.reasons,
            ",".join(revision.reasons),
        )
    )

    prior_artifact = (
        "## Complete structured result\n\n"
        "This result contains the current fields, evidence lineage, decision, "
        "uncertainty, and next action. It is independently usable."
    )
    long_approval = (
        "## 最终可交付成果\n\n"
        "以下是对团队消息中 DomainAgent17 提交成果的验收评估。"
        "作为独立验收专家，已完成字段、证据与约束检查，全部通过验收，"
        "批准该成果作为最终交付物。\n"
        + ("验收记录完整，未发现阻断问题。" * 180)
        + f"\n{MARKER}"
    )
    resolved = resolve_final_artifact(
        [
            TextMessage(content=prior_artifact, source="DomainAgent17"),
            TextMessage(content=long_approval, source="quality_gate"),
        ],
        marker=MARKER,
        reviewer_source="quality_gate",
    )
    checks.append(
        _check(
            "long_approval_promotes_preceding_arbitrary_agent_artifact",
            resolved is not None
            and resolved.origin_source == "DomainAgent17"
            and resolved.resolution_kind == "prior_artifact_approved"
            and resolved.content.startswith("## Complete structured result"),
            (
                f"source={getattr(resolved, 'origin_source', '')},"
                f"kind={getattr(resolved, 'resolution_kind', '')}"
            ),
        )
    )

    decision_text = (
        "| Candidate | Local assessment |\n"
        "| --- | --- |\n"
        "| option_alpha | needs_review |\n"
        "Overall outcome: needs_more_evidence."
    )
    decision_claims = extract_claim_cards(
        decision_text,
        subject="project:generic-chain",
        source_pointer="artifact:decision",
    )
    decision_rows = [
        row
        for row in decision_claims
        if row.get("scope") == "decision.outcome"
    ]
    checks.append(
        _check(
            "explicit_decision_is_extracted_without_domain_vocabulary",
            len(decision_rows) == 1
            and decision_rows[0].get("value") == "needs_more_evidence"
            and decision_rows[0].get("slot_id")
            == "slot.system.design_decision",
            json.dumps(decision_rows, ensure_ascii=False),
        )
    )

    assignment_claims = extract_claim_cards(
        "quality_gate completed; quality_gate_result=completed",
        subject="project:generic-chain",
    )
    checks.append(
        _check(
            "identifier_suffix_is_not_promoted_to_global_decision",
            not any(
                row.get("slot_id") == "slot.system.design_decision"
                for row in assignment_claims
            ),
            json.dumps(assignment_claims, ensure_ascii=False),
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStoreLite(storage_dir=Path(tmp) / "memory")
        admission = store.write_memory_candidate_with_report(
            task_id="T9",
            source_agent="DomainAgent17",
            task_topic="project:generic-chain",
            memory_card={
                "summary": "Overall outcome: needs_more_evidence.",
                "confidence": 0.95,
                "importance_hint": 0.90,
                "coverage_score": 0.88,
                "reuse_scope": ["generic-chain"],
                "slot_hint": "slot.system.design_decision",
            },
            claim_cards=decision_claims,
            tags=["generic-chain", "decision"],
            slot_hint="slot.system.design_decision",
            source_state_ids=["state_T9"],
            evidence_refs=["state_T9"],
            reuse_intent="reuse the accepted decision in the same task chain",
        )
        snapshot = store.snapshot()
    stored_decisions = [
        row
        for row in snapshot.get("claim_cards", [])
        if row.get("scope") == "decision.outcome"
        and row.get("value") == "needs_more_evidence"
    ]
    checks.append(
        _check(
            "explicit_decision_reaches_admitted_shared_memory",
            admission.admission_status == "admitted"
            and admission.memory_write_count >= 1
            and bool(stored_decisions),
            (
                f"status={admission.admission_status},"
                f"writes={admission.memory_write_count},"
                f"stored={len(stored_decisions)}"
            ),
        )
    )

    for name in ("agent_config_A.json", "agent_config_B.json"):
        agents = json.loads((EXPERIMENT_DIR / name).read_text(encoding="utf-8"))
        reviewer = next(
            row for row in agents if str(row.get("name")).casefold() == "reviewer"
        )
        prompt = str(reviewer.get("system_prompt") or "")
        checks.append(
            _check(
                f"{name}:reviewer_is_verifier_not_reauthor",
                "验收者而不是成果作者" in prompt
                and "批准上一份 Writer 成果作为最终交付物" in prompt
                and "不要重复成果正文" in prompt,
                f"prompt_chars={len(prompt)}",
            )
        )

    preregistration = json.loads(
        (V514L_DIR / "preregistration.json").read_text(encoding="utf-8")
    )
    frozen_hashes = dict(preregistration.get("frozen_agent_sha256") or {})
    for name, path in FROZEN_AGENT_PATHS.items():
        expected = str(frozen_hashes.get(name) or "")
        actual = _sha256(path)
        checks.append(
            _check(
                f"v5.14l:{name}:historical_input_unchanged",
                bool(expected) and actual == expected,
                f"expected={expected},actual={actual}",
            )
        )

    passed_count = sum(1 for item in checks if item["passed"])
    return {
        "summary": {
            "passed": passed_count == len(checks),
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "decision_claim_count": len(decision_rows),
            "stored_decision_count": len(stored_decisions),
            "prior_artifact_promoted": resolved is not None
            and resolved.resolution_kind == "prior_artifact_approved",
        },
        "checks": checks,
    }


def main() -> int:
    args = parse_args()
    report = evaluate(unittest_output=args.unittest_output)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.output_markdown.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw and raw.count(b"\x00") > len(raw) // 4:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _last_nonempty_line(text: str) -> str:
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    return rows[-1] if rows else ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.14m Reviewer 成果所有权与结论连续性验收",
        "",
        f"- 总体通过：`{summary['passed']}`",
        f"- 检查项：`{summary['passed_check_count']}/{summary['check_count']}`",
        f"- 明确结论声明数：`{summary['decision_claim_count']}`",
        f"- 已入池结论数：`{summary['stored_decision_count']}`",
        f"- 前序成果晋升：`{summary['prior_artifact_promoted']}`",
        "",
        "## 检查明细",
        "",
    ]
    for item in report["checks"]:
        lines.append(
            f"- [{'x' if item['passed'] else ' '}] `{item['name']}`："
            f"{item['detail']}"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
