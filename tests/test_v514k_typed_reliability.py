from __future__ import annotations

import runpy
import tempfile
import unittest
from pathlib import Path

from agent_runtime.core.kernel import AgentDescriptor, CollaborationKernel
from agent_runtime.core.models import AgentOutput, TaskSpec
from agent_runtime.eval.autogen_session_report import _metric_rows
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.claim_extractor import extract_claim_cards
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.reliability.final_delivery_guard import assess_final_delivery
from agent_runtime.reliability.quantities import parse_quantities
from agent_runtime.state.state_pool import StatePoolLite
from agent_runtime.state.structured_output import structured_state_payloads
from web_monitor.parser import _autogen_token_summary


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TECHNICAL_JUDGE = runpy.run_path(
    str(
        PROJECT_ROOT
        / "experiments"
        / "ordinary-developer-autogen"
        / "judge_stateful_technical_blind_batch.py"
    )
)


class V514KTypedReliabilityTests(unittest.TestCase):
    def test_quantity_parser_keeps_money_and_time_separate(self) -> None:
        quantities = parse_quantities(
            "总预算不超过 3200 元；首次响应时间不得超过 30 分钟。"
        )

        self.assertEqual(
            [(item.value, item.dimension) for item in quantities],
            [(3200.0, "money"), (30.0, "time")],
        )
        english_count = parse_quantities("Retry at most 3 times.")
        self.assertEqual(
            [(item.value, item.dimension) for item in english_count],
            [(3.0, "count")],
        )

    def test_delivery_guard_does_not_treat_time_limit_as_budget(self) -> None:
        assessment = assess_final_delivery(
            request=(
                "总预算保持在 3200 元以内；首次响应时间不得超过 30 分钟。"
            ),
            content=(
                "## 最终可交付方案\n"
                "方案总预算为 2800 元，首次响应时间为 25 分钟。\n"
                "FINAL_ANSWER_READY"
            ),
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertTrue(assessment.valid)

    def test_delivery_guard_rejects_revision_list_with_final_heading(self) -> None:
        assessment = assess_final_delivery(
            request="提交可直接执行的完整迁移方案。",
            content=(
                "## 审查意见\n"
                "当前草案缺少回滚步骤，请 Writer 补充后重新提交。\n"
                "## 最终可交付方案\n"
                "下面仅给出一个示例结构，尚未完成修订。\n"
                "FINAL_ANSWER_READY"
            ),
            source="reviewer",
            marker="FINAL_ANSWER_READY",
            require_marker=True,
        )

        self.assertFalse(assessment.valid)
        self.assertIn("review_feedback_not_final_artifact", assessment.reasons)

    def test_hypothetical_claim_is_marked_uncertain(self) -> None:
        claims = extract_claim_cards(
            "Expected query return: risk_score=0.93.",
            subject="project:generic",
        )

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["certainty"], "uncertain")

    def test_uncertain_claim_stays_in_candidate_pool(self) -> None:
        store = MemoryStoreLite()
        report = store.write_memory_candidate_with_report(
            task_id="T1",
            source_agent="analyst",
            task_topic="project:generic",
            memory_card={
                "summary": "Hypothetical result: risk_score=0.93.",
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
                    "value": "0.93",
                    "certainty": "uncertain",
                    "modality": "project_state",
                }
            ],
            tags=["generic"],
            slot_hint="slot.runtime.config",
            source_state_ids=["state_1"],
            evidence_refs=["state_1"],
        )

        self.assertEqual(report.admission_status, "audit_only")
        self.assertEqual(report.epistemic_deferred_count, 1)
        self.assertEqual(report.memory_write_count, 0)
        snapshot = store.snapshot()
        self.assertEqual(
            snapshot["claim_candidates"][0]["admission_status"],
            "pending_confirmation",
        )

    def test_framework_output_builds_real_embedding_and_retrieval_payloads(
        self,
    ) -> None:
        embedding = structured_state_payloads(
            (
                "embedding_state: query_embedding_id=emb_query_17; "
                "vector_dim=384; "
                "candidates=[profile_17,profile_22]; "
                "similarity_scores={profile_17:0.91,profile_22:0.72}"
            ),
            semantic_action="BUILD_EMBEDDING",
        )
        retrieval = structured_state_payloads(
            (
                "retrieval_state: evidence_rows=[row_17,row_22]; "
                "source_ids=[source_alpha]; "
                "score_map={row_17:0.88,row_22:0.74}"
            ),
            semantic_action="RETRIEVE_EVIDENCE",
        )

        self.assertEqual(embedding[0]["state_type"], "embedding_state")
        self.assertEqual(embedding[0]["payload"]["vector_dim"], 384)
        self.assertEqual(
            embedding[0]["payload"]["score_map"]["profile_17"],
            0.91,
        )
        self.assertEqual(retrieval[0]["state_type"], "retrieval_state")
        self.assertEqual(
            retrieval[0]["payload"]["chunk_ids"],
            ["row_17", "row_22"],
        )

    def test_kernel_commits_structured_state_instead_of_text_artifact(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kernel = CollaborationKernel(
                agents=[],
                token_counter=TokenCounter(allow_estimate=True),
                metrics=MetricsCollector(),
                trace=TraceLogger(root),
                state_pool=StatePoolLite(root / "state"),
                memory_store=MemoryStoreLite(root / "memory"),
            )
            refs = kernel.write_agent_state(
                task=TaskSpec(
                    task_id="T2",
                    group_id="generic",
                    title="structured output",
                    prompt="Return ranked evidence.",
                ),
                round_id=1,
                mode="runtime_lite",
                agent=AgentDescriptor("EvidenceNode", "Evidence specialist"),
                output=AgentOutput(
                    agent_id="EvidenceNode",
                    content="ranked evidence",
                    metadata={
                        "framework": "autogen",
                        "semantic_action": "RETRIEVE_EVIDENCE",
                        "structured_state_payloads": [
                            {
                                "state_type": "retrieval_state",
                                "payload": {
                                    "chunk_ids": ["row_1"],
                                    "source_ids": ["source_1"],
                                    "score_map": {"row_1": 0.9},
                                    "evidence_rank": ["row_1"],
                                    "payload_kind": "structured_non_text",
                                },
                                "summary": "one ranked evidence row",
                                "usage_hint": "summary_context_selection",
                            }
                        ],
                    },
                ),
            )
            kernel.close()

        self.assertEqual([ref.state_type for ref in refs], ["retrieval_state"])

    def test_technical_judge_rejects_previous_answer_evidence(self) -> None:
        normalize_result = TECHNICAL_JUDGE["normalize_result"]
        parsed = {
            "task_id": "T3",
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
                            "evidence": "old timeout is 30 seconds",
                            "issue": "The timeout is stale.",
                            "repair": "Use the current timeout.",
                        }
                    ],
                    "strengths": [],
                }
            ],
        }

        with self.assertRaisesRegex(
            ValueError,
            "evidence is not present in the current answer",
        ):
            normalize_result(
                parsed,
                task_id="T3",
                candidate_ids=["candidate_1"],
                candidate_answers={
                    "candidate_1": "The current timeout is 45 seconds."
                },
            )

    def test_epistemic_deferral_is_visible_in_reports(self) -> None:
        summary = _autogen_token_summary(
            [
                {
                    "event_type": "state_memory_bridge",
                    "payload": {"epistemic_deferred_count": 2},
                }
            ]
        )
        rows = {
            row["metric"]: row["value"]
            for row in _metric_rows(summary)
        }

        self.assertEqual(summary["memory_epistemic_deferred_count"], 2)
        self.assertEqual(
            rows["agentlite_memory_epistemic_deferred_count"],
            2,
        )


if __name__ == "__main__":
    unittest.main()
