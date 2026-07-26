from __future__ import annotations

import argparse
import json
import runpy
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.core.kernel import AgentDescriptor, CollaborationKernel
from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.claim_extractor import extract_claim_cards
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.reliability.final_delivery_guard import assess_final_delivery
from agent_runtime.reliability.quantities import parse_quantities
from agent_runtime.state.state_pool import StatePoolLite
from agent_runtime.state.structured_output import structured_state_payloads


TECHNICAL_JUDGE = runpy.run_path(
    str(
        REPO_ROOT
        / "experiments"
        / "ordinary-developer-autogen"
        / "judge_stateful_technical_blind_batch.py"
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify v5.14k typed reliability mechanisms."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--unittest-output", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(*, repo_root: Path, unittest_output: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    test_text = _read_text_output(unittest_output)
    checks.append(
        _check(
            "full_unittest_suite_passed",
            "\nOK\n" in test_text.replace("\r\n", "\n"),
            _last_nonempty_line(test_text),
        )
    )

    quantities = parse_quantities(
        "The total budget is at most CNY 3200; response time is at most "
        "30 minutes; retry count is at most 3 times."
    )
    dimensions = [(item.value, item.dimension) for item in quantities]
    checks.extend(
        [
            _check(
                "money_time_and_count_are_typed_separately",
                dimensions
                == [
                    (3200.0, "money"),
                    (30.0, "time"),
                    (3.0, "count"),
                ],
                repr(dimensions),
            ),
            _check(
                "time_limit_does_not_become_budget_limit",
                assess_final_delivery(
                    request=(
                        "Keep the total budget at most CNY 3200 and the first "
                        "response at most 30 minutes."
                    ),
                    content=(
                        "## Final deliverable\n"
                        "The total budget is CNY 2800 and the first response "
                        "takes 25 minutes.\nFINAL_ANSWER_READY"
                    ),
                    source="reviewer",
                    marker="FINAL_ANSWER_READY",
                    require_marker=True,
                ).valid,
                "CNY 2800 remains below CNY 3200; 25 minutes is a time value",
            ),
            _check(
                "real_budget_overrun_is_rejected",
                not assess_final_delivery(
                    request="Keep the total budget at most CNY 3200.",
                    content=(
                        "## Final deliverable\n"
                        "The total budget is CNY 3500.\nFINAL_ANSWER_READY"
                    ),
                    source="reviewer",
                    marker="FINAL_ANSWER_READY",
                    require_marker=True,
                ).valid,
                "CNY 3500 is above CNY 3200",
            ),
        ]
    )

    review_only = assess_final_delivery(
        request="Deliver a complete executable migration plan.",
        content=(
            "## Review findings\n"
            "The draft lacks rollback steps. Writer must revise and resubmit.\n"
            "## Final deliverable\n"
            "This is only a revision checklist, not the corrected artifact.\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    corrected = assess_final_delivery(
        request="Deliver a complete executable migration plan.",
        content=(
            "## Final deliverable\n"
            "The corrections have been completed and integrated into the "
            "complete final plan. Phase 1 backs up the database, Phase 2 "
            "migrates records, and rollback restores the signed snapshot.\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    checks.extend(
        [
            _check(
                "review_blocker_cannot_masquerade_as_final_artifact",
                not review_only.valid
                and "review_feedback_not_final_artifact"
                in review_only.reasons,
                ",".join(review_only.reasons),
            ),
            _check(
                "integrated_corrected_artifact_can_be_delivered",
                corrected.valid,
                ",".join(corrected.reasons) or "valid",
            ),
        ]
    )

    uncertain_claims = extract_claim_cards(
        "Expected query return: risk_score=0.93.",
        subject="project:generic",
    )
    observed_claims = extract_claim_cards(
        "Observed tool result: risk_score=0.41.",
        subject="project:generic",
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStoreLite(Path(tmp) / "memory")
        uncertain_report = _write_claim(
            store,
            task_id="generic_expected",
            certainty="uncertain",
            value="0.93",
        )
        observed_report = _write_claim(
            store,
            task_id="generic_observed",
            certainty="observed",
            value="0.41",
        )
        memory_snapshot = store.snapshot()
    checks.extend(
        [
            _check(
                "expected_result_is_inferred_as_uncertain",
                len(uncertain_claims) == 1
                and uncertain_claims[0]["certainty"] == "uncertain",
                json.dumps(uncertain_claims, ensure_ascii=False),
            ),
            _check(
                "observed_result_is_inferred_as_observed",
                len(observed_claims) == 1
                and observed_claims[0]["certainty"] == "observed",
                json.dumps(observed_claims, ensure_ascii=False),
            ),
            _check(
                "uncertain_claim_stays_in_candidate_pool",
                uncertain_report.admission_status == "audit_only"
                and uncertain_report.epistemic_deferred_count == 1
                and uncertain_report.memory_write_count == 0
                and any(
                    row.get("admission_status") == "pending_confirmation"
                    for row in memory_snapshot["claim_candidates"]
                ),
                (
                    f"status={uncertain_report.admission_status};"
                    f"deferred={uncertain_report.epistemic_deferred_count};"
                    f"writes={uncertain_report.memory_write_count}"
                ),
            ),
            _check(
                "observed_claim_can_enter_formal_memory",
                observed_report.admission_status == "admitted"
                and observed_report.memory_write_count == 1,
                (
                    f"status={observed_report.admission_status};"
                    f"writes={observed_report.memory_write_count}"
                ),
            ),
        ]
    )

    embedding_payloads = structured_state_payloads(
        (
            "embedding_state: query_embedding_id=query_vec_17; "
            "vector_dim=384; candidates=[chunk_vec_17,chunk_vec_22]; "
            "similarity_scores={chunk_vec_17:0.91,chunk_vec_22:0.72}"
        ),
        semantic_action="BUILD_EMBEDDING",
    )
    retrieval_payloads = structured_state_payloads(
        (
            "retrieval_state: chunk_ids=[chunk_17,chunk_22]; "
            "source_ids=[source_alpha]; "
            "score_map={chunk_17:0.88,chunk_22:0.74}"
        ),
        semantic_action="RETRIEVE_EVIDENCE",
    )
    empty_embedding = structured_state_payloads(
        "Embedding completed successfully.",
        semantic_action="BUILD_EMBEDDING",
    )
    checks.extend(
        [
            _check(
                "real_embedding_references_form_embedding_state",
                len(embedding_payloads) == 1
                and embedding_payloads[0]["state_type"] == "embedding_state"
                and embedding_payloads[0]["payload"]["vector_dim"] == 384,
                json.dumps(embedding_payloads, ensure_ascii=False),
            ),
            _check(
                "real_retrieval_references_form_retrieval_state",
                len(retrieval_payloads) == 1
                and retrieval_payloads[0]["state_type"] == "retrieval_state"
                and retrieval_payloads[0]["payload"]["chunk_ids"]
                == ["chunk_17", "chunk_22"],
                json.dumps(retrieval_payloads, ensure_ascii=False),
            ),
            _check(
                "missing_references_do_not_create_fake_embedding_state",
                empty_embedding == [],
                repr(empty_embedding),
            ),
        ]
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        kernel = CollaborationKernel(
            agents=[],
            token_counter=TokenCounter(allow_estimate=True),
            metrics=MetricsCollector(),
            trace=TraceLogger(root / "trace"),
            state_pool=StatePoolLite(root / "state"),
            memory_store=MemoryStoreLite(root / "memory"),
        )
        state_refs = kernel.write_agent_state(
            task=TaskSpec(
                task_id="generic_retrieval",
                group_id="generic_group",
                title="Rank evidence",
                prompt="Return ranked evidence references.",
            ),
            round_id=1,
            mode="runtime_lite",
            agent=AgentDescriptor(
                "EvidenceNode",
                "Ranks evidence returned by the connected framework.",
            ),
            output=AgentOutput(
                agent_id="EvidenceNode",
                content="ranked evidence",
                metadata={
                    "framework": "autogen",
                    "semantic_action": "RETRIEVE_EVIDENCE",
                    "structured_state_payloads": retrieval_payloads,
                },
            ),
        )
        kernel.close()
    checks.append(
        _check(
            "kernel_persists_real_structured_state_type",
            [ref.state_type for ref in state_refs] == ["retrieval_state"],
            repr([ref.state_type for ref in state_refs]),
        )
    )

    normalize_result = TECHNICAL_JUDGE["normalize_result"]
    stale_evidence_rejected = False
    try:
        normalize_result(
            _judge_payload("old timeout is 30 seconds"),
            task_id="generic_task",
            candidate_ids=["candidate_1"],
            candidate_answers={
                "candidate_1": "The current timeout is 45 seconds."
            },
        )
    except ValueError as exc:
        stale_evidence_rejected = (
            "evidence is not present in the current answer" in str(exc)
        )
    current_result = normalize_result(
        _judge_payload("current timeout is 45 seconds"),
        task_id="generic_task",
        candidate_ids=["candidate_1"],
        candidate_answers={
            "candidate_1": "The current timeout is 45 seconds."
        },
    )
    checks.extend(
        [
            _check(
                "blind_judge_rejects_previous_answer_evidence",
                stale_evidence_rejected,
                f"rejected={stale_evidence_rejected}",
            ),
            _check(
                "blind_judge_accepts_current_answer_evidence",
                current_result["evaluations"][0]["findings"][0]["evidence"]
                == "current timeout is 45 seconds",
                json.dumps(current_result, ensure_ascii=False),
            ),
        ]
    )

    production_sources = [
        repo_root / "agent_runtime/reliability/quantities.py",
        repo_root / "agent_runtime/reliability/final_delivery_guard.py",
        repo_root / "agent_runtime/memory/claim_extractor.py",
        repo_root / "agent_runtime/memory/memory_store.py",
        repo_root / "agent_runtime/state/structured_output.py",
        repo_root / "agent_runtime/drivers/autogen.py",
    ]
    source_text = "\n".join(
        path.read_text(encoding="utf-8") for path in production_sources
    )
    forbidden = (
        "Question A",
        "question_A",
        "Question B",
        "question_B",
        "A1-A10",
        "B1-B10",
        "莫干山",
    )
    leaked = [term for term in forbidden if term in source_text]
    checks.append(
        _check(
            "production_runtime_has_no_benchmark_specific_terms",
            not leaked,
            f"leaked={leaked}",
        )
    )

    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "agentlite.v514k.acceptance-report.v1",
        "summary": {
            "passed": passed,
            "check_count": len(checks),
            "passed_check_count": sum(item["passed"] for item in checks),
            "quantity_dimension_count": len(
                {item.dimension for item in quantities}
            ),
            "epistemic_deferred_count": (
                uncertain_report.epistemic_deferred_count
            ),
            "structured_state_type_count": len(
                {
                    row["state_type"]
                    for row in embedding_payloads + retrieval_payloads
                }
            ),
            "fake_structured_state_count": len(empty_embedding),
            "benchmark_specific_runtime_term_count": len(leaked),
        },
        "checks": checks,
    }


def _write_claim(
    store: MemoryStoreLite,
    *,
    task_id: str,
    certainty: str,
    value: str,
):
    return store.write_memory_candidate_with_report(
        task_id=task_id,
        source_agent="EvidenceNode",
        task_topic="project:generic",
        memory_card={
            "summary": f"risk_score={value}",
            "confidence": 0.9,
            "importance_hint": 0.9,
            "coverage_score": 0.9,
        },
        claim_cards=[
            {
                "subject": "project:generic",
                "raw_slot_text": "risk_score",
                "slot_id": "slot.runtime.config",
                "scope": "config.risk_score",
                "value": value,
                "certainty": certainty,
                "modality": "project_state",
            }
        ],
        tags=["generic"],
        slot_hint="slot.runtime.config",
        source_state_ids=[f"state_{task_id}"],
        evidence_refs=[f"state_{task_id}"],
    )


def _judge_payload(evidence: str) -> dict[str, Any]:
    return {
        "task_id": "generic_task",
        "evaluations": [
            {
                "candidate_id": "candidate_1",
                "constraint_fidelity": 2,
                "technical_correctness": 3,
                "internal_consistency": 2,
                "executability": 2,
                "delivery_usable": True,
                "findings": [
                    {
                        "severity": "low",
                        "evidence": evidence,
                        "issue": "The timeout statement requires review.",
                        "repair": "Use the current verified timeout.",
                    }
                ],
                "strengths": [],
            }
        ],
    }


def _last_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "empty unittest output"


def _read_text_output(path: Path) -> str:
    payload = path.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in payload:
        return payload.decode("utf-16", errors="replace")
    return payload.decode("utf-8", errors="replace")


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.14k 类型化可靠性机制验收",
        "",
        f"- 总体通过：`{summary['passed']}`",
        (
            "- 通过项目："
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        f"- 数量量纲类型：`{summary['quantity_dimension_count']}`",
        f"- 延后确认事实：`{summary['epistemic_deferred_count']}`",
        f"- 真实结构状态类型：`{summary['structured_state_type_count']}`",
        f"- 伪造结构状态：`{summary['fake_structured_state_count']}`",
        (
            "- 运行时基准专用词："
            f"`{summary['benchmark_specific_runtime_term_count']}`"
        ),
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
