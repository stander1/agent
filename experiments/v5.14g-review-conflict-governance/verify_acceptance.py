from __future__ import annotations

import argparse
import inspect
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.core.kernel import CollaborationKernel
from agent_runtime.memory.claim_extractor import extract_claim_cards
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.reliability.review_conflict_guard import (
    build_review_blocker_claim,
    evaluate_review_conflict,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify v5.14g generic review conflict governance."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--unittest-output", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(*, repo_root: Path, unittest_output: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    test_bytes = unittest_output.read_bytes()
    test_text = (
        test_bytes.decode("utf-16", errors="replace")
        if b"\x00" in test_bytes[:200]
        else test_bytes.decode("utf-8", errors="replace")
    ).replace("\r\n", "\n")
    checks.append(_check("targeted_unittest_passed", "\nOK\n" in test_text, "see unittest.txt"))

    budget_cases = (
        ("预算：两人所有费用总计不超过3000元。", "3000", "CNY"),
        ("总预算：两人合计2600元。", "2600", "CNY"),
        ("budget for 2 people is USD 500.", "500", "USD"),
        ("预算为3000。", "3000", ""),
    )
    budget_results = []
    for text, expected_value, expected_unit in budget_cases:
        claims = _budget_claims(text)
        passed = (
            len(claims) == 1
            and claims[0]["value"] == expected_value
            and claims[0]["unit"] == expected_unit
        )
        budget_results.append(
            {
                "text": text,
                "expected_value": expected_value,
                "expected_unit": expected_unit,
                "claims": claims,
                "passed": passed,
            }
        )
    checks.append(
        _check(
            "budget_currency_amount_beats_party_count",
            all(row["passed"] for row in budget_results),
            f"cases={len(budget_results)}",
        )
    )
    checks.append(
        _check(
            "budget_without_amount_is_not_claim",
            not _budget_claims("预算需要两人共同确认。"),
            "party count must not become a monetary value",
        )
    )

    choice_row = _memory_row(
        memory_id="mem_choice",
        claim_id="claim_choice",
        semantic_key="project:demo|plan.selected_option",
        slot_id="slot.system.design_decision",
        scope="plan.selected_option",
        value="Option Alpha",
    )
    budget_row = _memory_row(
        memory_id="mem_budget",
        claim_id="claim_budget",
        semantic_key="project:demo|constraint.budget",
        slot_id="slot.project.requirement",
        scope="constraint.budget",
        value="3000",
        value_type="number",
        unit="CNY",
    )
    review_text = (
        "Validation failed. Option Alpha violates the hard latency "
        "constraint and must be replaced."
    )
    decision = evaluate_review_conflict(
        output_text=review_text,
        semantic_action="HANDLE_TASK",
        capabilities=("final_deliverable_review",),
        memory_rows=(choice_row, budget_row),
    )
    checks.extend(
        [
            _check(
                "dynamic_capability_grants_review_authority",
                decision.authoritative and decision.blocking,
                ",".join(decision.authority_reasons),
            ),
            _check(
                "only_referenced_fact_is_targeted",
                decision.targeted_memory_ids == ("mem_choice",),
                f"targets={list(decision.targeted_memory_ids)}",
            ),
        ]
    )

    non_reviewer = evaluate_review_conflict(
        output_text=review_text,
        semantic_action="WRITE_OUTPUT",
        capabilities=("writing",),
        memory_rows=(choice_row,),
    )
    approved = evaluate_review_conflict(
        output_text="Review passed. No blocking issues were found for Option Alpha.",
        semantic_action="REVIEW_OUTPUT",
        capabilities=("validation",),
        memory_rows=(choice_row,),
    )
    checks.extend(
        [
            _check(
                "ordinary_writer_cannot_govern_memory",
                not non_reviewer.authoritative and not non_reviewer.blocking,
                "authority is capability/action based",
            ),
            _check(
                "positive_review_does_not_deprecate",
                approved.authoritative and not approved.blocking,
                f"blocking={approved.blocking}",
            ),
        ]
    )

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStoreLite(storage_dir=Path(tmp) / "memory")
        choice = _write_fact(
            store,
            task_id="T1",
            slot_id="slot.system.design_decision",
            scope="plan.selected_option",
            value="Option Alpha",
        )
        budget = _write_fact(
            store,
            task_id="T1",
            slot_id="slot.project.requirement",
            scope="constraint.budget",
            value="3000",
            value_type="number",
            unit="CNY",
        )
        event = store.soft_deprecate(
            choice.memory_ref,
            reason="authoritative_review_blocked",
            created_by="QualityGate17",
        )
        choice_guard = store.revision_guard(choice.memory_ref)
        budget_ref = store.resolve_ref(budget.memory_ref.memory_id)
        budget_guard = store.revision_guard(budget_ref)
        blocker_claim = build_review_blocker_claim(
            decision,
            subject="collaboration:generic-chain",
            source_pointer="state_review",
        )
        blocker = store.write_memory_candidate_with_report(
            task_id="T2",
            source_agent="QualityGate17",
            task_topic="autogen.generic-chain.slot.system.failure_pattern",
            memory_card={
                "summary": decision.blocking_summary,
                "confidence": 0.94,
                "importance_hint": 0.90,
                "coverage_score": 0.76,
                "reuse_scope": ["generic-chain"],
                "slot_hint": "slot.system.failure_pattern",
            },
            claim_cards=[blocker_claim],
            tags=["generic-chain", "autogen_review_blocker"],
            slot_hint="slot.system.failure_pattern",
            source_state_ids=["state_review"],
            evidence_refs=["state_review"],
            reuse_intent="reuse authoritative review in the same task chain",
            fallback_summary=decision.blocking_summary,
        )
        blocker_prompt = store.render_prompt_view(
            blocker.memory_ref,
            budget_chars=1600,
        )
        checks.extend(
            [
                _check(
                    "target_memory_is_soft_deprecated",
                    store.resolve_ref(choice.memory_ref.memory_id).status
                    == "deprecated",
                    f"event={event.event_id}",
                ),
                _check(
                    "deprecated_claim_leaves_active_view",
                    not choice_guard["active_facts"]
                    and choice_guard["historical_facts"][0]["value"]
                    == "Option Alpha",
                    f"active={len(choice_guard['active_facts'])}",
                ),
                _check(
                    "unrelated_memory_remains_active",
                    budget_ref.status == "active"
                    and budget_guard["active_facts"][0]["value"] == "3000",
                    f"status={budget_ref.status}",
                ),
                _check(
                    "soft_deprecation_has_compensation_event",
                    event.event_type == "soft_deprecation",
                    f"event_type={event.event_type}",
                ),
                _check(
                    "review_blocker_passes_candidate_admission",
                    blocker.admission_status == "admitted",
                    f"status={blocker.admission_status}",
                ),
                _check(
                    "review_blocker_is_visible_to_next_task",
                    "Option Alpha" in blocker_prompt,
                    f"chars={len(blocker_prompt)}",
                ),
            ]
        )

    signature = inspect.signature(CollaborationKernel.promote_memory_candidate)
    checks.append(
        _check(
            "kernel_accepts_explicit_claim_cards",
            "claim_cards" in signature.parameters,
            str(signature),
        )
    )
    production_sources = [
        repo_root / "agent_runtime/reliability/review_conflict_guard.py",
        repo_root / "agent_runtime/memory/claim_extractor.py",
        repo_root / "agent_runtime/memory/memory_store.py",
        repo_root / "agent_runtime/drivers/autogen.py",
    ]
    source_text = "\n".join(
        path.read_text(encoding="utf-8") for path in production_sources
    )
    forbidden = (
        "Question A",
        "question_A",
        "莫干山",
        "皖南",
        "浙西",
        "ReviewerAgent",
    )
    leaked = [term for term in forbidden if term in source_text]
    checks.append(
        _check(
            "production_runtime_has_no_experiment_or_fixed_role_terms",
            not leaked,
            f"leaked={leaked}",
        )
    )

    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "agentlite.v514g.acceptance-report.v1",
        "summary": {
            "passed": passed,
            "check_count": len(checks),
            "passed_check_count": sum(item["passed"] for item in checks),
            "budget_case_count": len(budget_results),
            "targeted_memory_count": len(decision.targeted_memory_ids),
            "wrong_numeric_attribution_count": 0
            if all(row["passed"] for row in budget_results)
            else 1,
        },
        "review_decision": decision.to_dict(),
        "budget_results": budget_results,
        "checks": checks,
    }


def _budget_claims(text: str) -> list[dict[str, Any]]:
    return [
        claim
        for claim in extract_claim_cards(text, subject="project:demo")
        if "budget" in str(claim["scope"])
    ]


def _memory_row(
    *,
    memory_id: str,
    claim_id: str,
    semantic_key: str,
    slot_id: str,
    scope: str,
    value: str,
    value_type: str = "string",
    unit: str = "",
) -> dict[str, Any]:
    return {
        "memory_id": memory_id,
        "revision_guard": {
            "subject": "project:demo",
            "active_facts": [
                {
                    "claim_id": claim_id,
                    "semantic_key": semantic_key,
                    "slot_id": slot_id,
                    "scope": scope,
                    "value": value,
                    "value_type": value_type,
                    "unit": unit,
                    "polarity": "positive",
                }
            ],
        },
    }


def _write_fact(
    store: MemoryStoreLite,
    *,
    task_id: str,
    slot_id: str,
    scope: str,
    value: str,
    value_type: str = "string",
    unit: str = "",
):
    return store.write_memory_candidate_with_report(
        task_id=task_id,
        source_agent="FactAgent",
        task_topic="project:demo",
        memory_card={
            "summary": f"{scope}={value}",
            "confidence": 0.92,
            "importance_hint": 0.84,
            "coverage_score": 0.80,
            "reuse_scope": ["generic-chain"],
            "slot_hint": slot_id,
        },
        claim_cards=[
            {
                "subject": "project:demo",
                "raw_slot_text": scope,
                "slot_id": slot_id,
                "scope": scope,
                "value": value,
                "value_type": value_type,
                "unit": unit,
                "raw_text": f"{scope}={value}",
                "summary": f"{scope}={value}",
                "certainty": "confirmed",
                "modality": "asserted",
                "polarity": "positive",
                "confidence": 0.92,
                "revision_kind": "asserted",
                "temporal_scope": "cross_task",
                "source_pointer": f"state:{task_id}",
            }
        ],
        tags=["generic-chain", task_id],
        slot_hint=slot_id,
        source_state_ids=[f"state_{task_id}"],
        evidence_refs=[f"state_{task_id}"],
        reuse_intent="reuse in the same generic task chain",
        fallback_summary=f"{scope}={value}",
    )


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.14g 动态审查冲突治理验收",
        "",
        f"- 总体通过：`{summary['passed']}`",
        f"- 通过项目：`{summary['passed_check_count']}/{summary['check_count']}`",
        f"- 数值样例：`{summary['budget_case_count']}`",
        f"- 被定向废弃的记忆：`{summary['targeted_memory_count']}`",
        f"- 数值归因误报：`{summary['wrong_numeric_attribution_count']}`",
        "",
        "## 检查项",
        "",
    ]
    for item in report["checks"]:
        marker = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- `{marker}` {item['name']}: {item['detail']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    report = evaluate(
        repo_root=args.repo_root.resolve(),
        unittest_output=args.unittest_output.resolve(),
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.output_markdown.write_text(
        _render_markdown(report),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
