from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "v5.13y-real-autogen-fact-memory"
BASE_EXPERIMENT = (
    ROOT / "experiments" / "v5.13s-dynamic-capability-acceptance"
)


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class RealAutoGenFactMemoryExperimentTest(unittest.TestCase):
    def test_runner_freezes_fair_variables_and_dual_quality_review(self) -> None:
        source = (EXPERIMENT / "run_openeuler.sh").read_text(encoding="utf-8")
        self.assertIn("AGENTLITE_V513Y_EXP_ID", source)
        self.assertIn("runs/v5.13y-real-autogen-fact-memory", source)
        self.assertEqual(source.count('--task-sequence "$TASKS"'), 3)
        self.assertEqual(source.count('--agent-config "$AGENTS"'), 3)
        self.assertEqual(source.count('--temperature "$V513Y_TEMPERATURE"'), 3)
        self.assertEqual(source.count('--max-turns "$V513Y_MAX_TURNS"'), 3)
        self.assertIn("judge_stateful_blind_batch.py", source)
        self.assertIn("judge_stateful_technical_blind_batch.py", source)
        self.assertIn("--technical-scores", source)
        self.assertIn("--report-version v5.13y", source)

    def test_fact_audit_exports_structured_evidence_without_fake_manual_label(
        self,
    ) -> None:
        audit = load_module(
            EXPERIMENT / "build_fact_audit.py",
            "v513y_build_fact_audit",
        )
        events = [
            {
                "event_type": "state_memory_bridge",
                "payload": {
                    "task_id": "D2",
                    "agent_id": "DesignSynthesizer",
                    "admission_status": "admitted",
                    "raw_claim_count": 2,
                    "provisional_claim_count": 2,
                    "slot_mapping_success_count": 2,
                    "conflict_detected_count": 1,
                    "resolved_conflict_count": 1,
                    "active_value_selection_count": 2,
                },
            },
            {
                "event_type": "autogen_memory_adoption",
                "payload": {
                    "task_id": "D3",
                    "agent_id": "IntegritySentinel",
                    "call_id": "call_1",
                    "attribution_mode": "ccf_v2_semantic_key_value_rules",
                    "evidence": [
                        {
                            "memory_ref": {"memory_id": "mem_1"},
                            "status": "useful",
                            "attribution_mode": (
                                "ccf_v2_semantic_key_value_rules"
                            ),
                            "semantic_key": (
                                "project|slot.project.requirement|"
                                "constraint.peak_concurrency|current_project"
                            ),
                            "active_value": "50",
                            "historical_values": ["20"],
                            "matched_fact_count": 1,
                            "matched_output_spans": ["峰值并发 50"],
                        }
                    ],
                },
            },
        ]
        payload = audit.build_audit(
            events=events,
            session_id="launch_test",
            trace_path=Path("trace.jsonl"),
            sample_limit=20,
        )

        self.assertEqual(payload["summary"]["raw_claim_count"], 2)
        self.assertEqual(payload["summary"]["resolved_conflict_count"], 1)
        self.assertEqual(payload["summary"]["attribution_evidence_count"], 1)
        row = payload["sample_rows"][0]
        self.assertEqual(row["status"], "useful")
        self.assertEqual(row["active_value"], "50")
        self.assertEqual(row["manual_label"], "")
        self.assertEqual(row["manual_notes"], "")

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "sample.csv"
            audit.write_csv(csv_path, payload["sample_rows"])
            text = csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("manual_label", text)
            self.assertIn("constraint.peak_concurrency", text)

    def test_v513y_verifier_accepts_fact_level_metrics(self) -> None:
        verifier = load_module(
            BASE_EXPERIMENT / "verify_acceptance.py",
            "v513y_verify_acceptance",
        )
        checks = []
        token_summary = {
            "raw_claim_count": 4,
            "provisional_claim_count": 4,
            "slot_mapping_success_count": 4,
            "active_memory_value_selection_count": 3,
            "unresolved_scope_count": 0,
            "memory_unresolved_conflict_count": 0,
        }
        events = [
            {
                "event_type": "state_memory_bridge",
                "payload": {"raw_claim_count": 4},
            },
            {
                "event_type": "autogen_memory_adoption",
                "payload": {
                    "evidence": [
                        {
                            "attribution_mode": (
                                "ccf_v2_semantic_key_value_rules"
                            ),
                            "semantic_key": "project|slot|scope|current",
                        }
                    ]
                },
            },
        ]

        verifier._append_v513x_fact_memory_checks(
            checks,
            token_summary=token_summary,
            events=events,
        )

        self.assertEqual(len(checks), 3)
        self.assertTrue(all(check.passed for check in checks))

    def test_v513y_quality_gate_accepts_exact_structured_attribution(self) -> None:
        verifier = load_module(
            BASE_EXPERIMENT / "verify_acceptance.py",
            "v513y_structured_quality_verify",
        )
        checks = []
        events = [
            {
                "event_type": "autogen_memory_adoption",
                "payload": {
                    "call_id": "call_1",
                    "current_task_source": "team_task_by_group",
                    "current_task_fingerprint": "task123",
                    "evidence": [
                        {
                            "adopted": True,
                            "explicit_reference": False,
                            "attribution_threshold": 1.0,
                            "current_task_overlap_threshold": 1.0,
                            "current_task_duplicate_fact_count": 1,
                            "matched_fact_fingerprints": ["fact123"],
                            "attribution_margins": [1.0],
                        }
                    ],
                },
            }
        ]
        quality = {
            "technical_review_applied": True,
            "evaluation_judge_usage": {
                "primary": {"total_tokens": 100},
                "technical": {"total_tokens": 100},
                "included_in_runtime_collaboration_cost": False,
            },
            "by_group": {
                group: {
                    "technical_review_applied": True,
                    "technical_mean_score": 8.0,
                }
                for group in ("native", "observed", "managed")
            },
        }
        comparison = {
            "summary": {
                "normalized_common_calls": {
                    "available": True,
                    "common_call_count": 4,
                    "groups": {
                        group: {"matched_call_count": 4}
                        for group in ("native", "observed", "managed")
                    },
                    "managed_vs_native": {
                        "total": {"token_delta": -10}
                    },
                }
            }
        }

        verifier._append_v513v_evidence_checks(
            checks,
            events=events,
            quality=quality,
            comparison=comparison,
            structured_attribution=True,
        )

        self.assertEqual(len(checks), 4)
        self.assertTrue(all(check.passed for check in checks))

    def test_runtime_source_does_not_contain_experiment_task_ids(self) -> None:
        runtime_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "agent_runtime").rglob("*.py")
        )
        self.assertNotIn("v5.13y-real-autogen-fact-memory", runtime_sources)
        self.assertNotIn("task_id == \"D1\"", runtime_sources)
        self.assertNotIn("task_id == \"D2\"", runtime_sources)
        self.assertNotIn("task_id == \"D3\"", runtime_sources)


if __name__ == "__main__":
    unittest.main()
