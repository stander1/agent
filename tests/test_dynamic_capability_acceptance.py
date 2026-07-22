from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "v5.13s-dynamic-capability-acceptance"
EXPERIMENT_V513T = (
    ROOT / "experiments" / "v5.13t-capability-identity-no-expansion"
)
EXPERIMENT_V513U = (
    ROOT / "experiments" / "v5.13u-evidence-attribution-acceptance"
)


def load_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, EXPERIMENT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class DynamicCapabilityAcceptanceTest(unittest.TestCase):
    def test_config_uses_arbitrary_roles_and_one_real_tool(self) -> None:
        configs = json.loads(
            (EXPERIMENT / "agent_config.json").read_text(encoding="utf-8")
        )
        names = {str(item["name"]).casefold() for item in configs}
        self.assertTrue({"planner", "writer", "reviewer"}.isdisjoint(names))
        self.assertEqual(sum(bool(item.get("finalizer")) for item in configs), 1)
        self.assertEqual(
            sum(bool(item.get("use_local_evidence_tool")) for item in configs), 1
        )
        source = (EXPERIMENT / "code_app.py").read_text(encoding="utf-8")
        self.assertNotIn("FINALIZER_SOURCE", source)

    def test_sequence_requires_prior_decisions(self) -> None:
        payload = json.loads(
            (EXPERIMENT / "task_sequence.json").read_text(encoding="utf-8")
        )
        tasks = payload["tasks"]
        self.assertEqual([task["task_id"] for task in tasks], ["D1", "D2", "D3"])
        self.assertIn("基于 D1", tasks[1]["question"])
        self.assertIn("基于 D1 和 D2", tasks[2]["question"])

    def test_runner_ignores_generic_legacy_experiment_paths(self) -> None:
        source = (EXPERIMENT / "run_openeuler.sh").read_text(encoding="utf-8")
        self.assertNotIn('${EXP_ID:-', source)
        self.assertNotIn('${RUN_ROOT:-', source)
        self.assertNotIn('${TRACE_ROOT:-', source)
        self.assertIn("AGENTLITE_V513S_EXP_ID", source)
        self.assertIn("AGENTLITE_V513S_RUN_ROOT", source)
        self.assertIn("AGENTLITE_V513S_TRACE_ROOT", source)

    def test_v513t_runner_uses_dedicated_paths_and_report_version(self) -> None:
        source = (EXPERIMENT_V513T / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("AGENTLITE_V513T_EXP_ID", source)
        self.assertIn("runs/v5.13t-capability-identity", source)
        self.assertIn("exports/v5.13t-capability-identity-", source)
        self.assertIn("--report-version v5.13t", source)

    def test_v513u_runner_uses_dedicated_evidence_paths(self) -> None:
        source = (EXPERIMENT_V513U / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("AGENTLITE_V513U_EXP_ID", source)
        self.assertIn("runs/v5.13u-evidence-attribution", source)
        self.assertIn("exports/v5.13u-evidence-attribution-", source)
        self.assertIn("--report-version v5.13u", source)

    def test_v513u_evidence_checks_separate_passthrough_and_memory_use(self) -> None:
        verifier = load_module("verify_acceptance.py", "v513u_verify")
        checks = []
        token_summary = {
            "rewrite_ineligible_control_passthrough_count": 12,
            "rewrite_error_fallback_count": 0,
            "memory_injected_count": 2,
            "useful_memory_hit_count": 1,
            "wrong_memory_hit_count": 0,
            "unassessed_memory_hit_count": 1,
            "memory_supported_output_count": 1,
        }
        events = [{
            "event_type": "autogen_memory_adoption",
            "payload": {
                "call_id": "call_1",
                "attribution_mode": "distinctive_fact_overlap_rules_v1",
                "useful_memory_hit_count": 1,
                "evidence": [{
                    "adopted": True,
                    "explicit_reference": False,
                    "memory_ref": {"memory_id": "mem_1", "version_id": 1},
                    "matched_fact_fingerprints": ["abc123"],
                }],
            },
        }]

        verifier._append_v513u_evidence_checks(
            checks,
            token_summary=token_summary,
            events=events,
        )

        self.assertEqual(len(checks), 4)
        self.assertTrue(all(check.passed for check in checks))


    def test_local_tool_returns_versioned_evidence(self) -> None:
        app = load_module("code_app.py", "v513s_code_app")
        evidence = json.loads(app.local_evidence_lookup("SQLite WAL 恢复和并发"))
        evidence_ids = {row["evidence_id"] for row in evidence}
        self.assertIn("OE-STATE-02", evidence_ids)
        self.assertIn("OE-STATE-07", evidence_ids)

    def test_verifier_accepts_synthetic_complete_run(self) -> None:
        verifier = load_module("verify_acceptance.py", "v513s_verify")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dirs = {
                group: root / group for group in ("native", "observed", "managed")
            }
            for group, path in run_dirs.items():
                path.mkdir(parents=True)
                sequence = {
                    "summary": {
                        "experiment_mode": group,
                        "task_count": 3,
                        "valid_delivery_count": 3,
                        "same_team_instance": True,
                        "llm_usage": {"llm_total_tokens": 1000},
                    },
                    "tasks": [
                        {"task_id": task_id, "tool_calls": [{"tool_id": "x"}]}
                        for task_id in ("D1", "D2", "D3")
                    ],
                }
                (path / "sequence_result.json").write_text(
                    json.dumps(sequence), encoding="utf-8"
                )

            trace = (
                root
                / "trace"
                / "sessions"
                / "launch_1"
                / "autogen_driver"
                / "trace.jsonl"
            )
            trace.parent.mkdir(parents=True)
            events = []
            for agent_id in verifier.EXPECTED_AGENT_IDS:
                profile = {
                    "agent_id": agent_id,
                    "profile_version": 2,
                    "registry_scope": "business",
                    "role_capabilities": [
                        {"name": "analysis", "source": "role"}
                    ],
                    "tool_capabilities": [],
                    "available_tools": [],
                }
                if agent_id == "EvidenceMiner":
                    profile["tool_capabilities"] = [
                        {
                            "name": "retrieval",
                            "source": "tool:local_evidence_lookup",
                        }
                    ]
                    profile["available_tools"] = ["local_evidence_lookup"]
                events.append(
                    {
                        "event_type": "capability_profile_feedback",
                        "payload": {"agent_id": agent_id, "profile": profile},
                    }
                )
            trace.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            report = {
                "driver_status": "active",
                "hooks_active": True,
                "trace_path": str(trace),
                "event_counts": {},
                "token_summary": {
                    "capability_profile_update_count": 4,
                    "capability_profile_feedback_count": 4,
                    "registered_capability_profile_count": 4,
                    "registered_system_profile_count": 0,
                    "registered_total_profile_count": 4,
                    "memory_source_view_tokens": 100,
                    "minimal_role_view_tokens": 100,
                    "memory_role_view_candidate_tokens": 120,
                    "memory_no_expansion_fallback_count": 1,
                    "current_task_source_tokens": 200,
                    "current_task_role_view_tokens": 180,
                    "current_task_no_expansion_fallback_count": 1,
                    "capability_context_view_count": 2,
                    "capability_action_counts": {
                        "PLAN_TASK": 2,
                        "VERIFY_CLAIM": 2,
                    },
                    "rewrite_applied_event_count": 2,
                    "continuity_required_event_count": 1,
                    "continuity_memory_injection_count": 1,
                },
            }
            (run_dirs["managed"] / "agentlite_session_report.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            quality_path = root / "quality.json"
            quality_path.write_text(
                json.dumps(
                    {
                        "by_group": {
                            "native": {
                                "mean_score": 9.0,
                                "delivery_complete_count": 3,
                            },
                            "observed": {
                                "mean_score": 9.0,
                                "delivery_complete_count": 3,
                            },
                            "managed": {
                                "mean_score": 8.5,
                                "delivery_complete_count": 3,
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            payload = verifier.verify_acceptance(
                native_dir=run_dirs["native"],
                observed_dir=run_dirs["observed"],
                managed_dir=run_dirs["managed"],
                managed_data_dir=root / "trace",
                quality_summary=quality_path,
                report_version="v5.13t",
            )
            self.assertTrue(payload["summary"]["passed"])


            self.assertEqual(payload["report_version"], "v5.13t")
            self.assertEqual(
                payload["schema_version"], "agentlite.v5.13t-acceptance.v1"
            )
            self.assertTrue(
                verifier.render_markdown(payload).startswith(
                    "# v5.13t 动态能力画像 AutoGen 验收报告"
                )
            )


if __name__ == "__main__":
    unittest.main()
