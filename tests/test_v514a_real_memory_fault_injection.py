from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

from agent_runtime.drivers.autogen import _text_fingerprint


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT / "experiments" / "v5.14a-real-memory-fault-injection"
)


def _load_module(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        name,
        EXPERIMENT_DIR / filename,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RealMemoryFaultInjectionExperimentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix = json.loads(
            (EXPERIMENT_DIR / "fault_matrix.json").read_text(encoding="utf-8")
        )
        cls.seed_module = _load_module("v514a_seed", "seed_fault_memory.py")
        cls.verify_module = _load_module(
            "v514a_verify", "verify_fault_injection.py"
        )

    def test_seed_survives_reload_with_structured_and_legacy_guards(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = self.seed_module.seed_memory(
                data_dir=Path(temporary),
                memory_scope="unit-test-scope",
                matrix=self.matrix,
            )
        self.assertEqual(
            payload["collaboration_group_id"],
            "unit-test-scope.team_7a9c5864270a",
        )
        self.assertEqual(payload["search_ref_count"], 2)
        guards = payload["revision_guards"]
        structured = [
            guard
            for guard in guards
            if str(guard["schema_version"]).startswith("ccf.v2")
        ][0]
        legacy = [
            guard
            for guard in guards
            if str(guard["schema_version"]).startswith("ccf.v1")
        ][0]
        self.assertEqual(structured["active_facts"][0]["value"], "50")
        self.assertEqual(structured["historical_facts"][0]["value"], "20")
        self.assertEqual(
            legacy["active_facts"][0]["value"],
            "database busy_timeout=2000 ms",
        )
        self.assertEqual(
            legacy["historical_facts"][0]["value"],
            "database busy_timeout=5000 ms",
        )

    def test_semantic_classifier_distinguishes_negation_and_mixed_values(
        self,
    ) -> None:
        structured = self.matrix["structured_memory"]
        legacy = self.matrix["legacy_memory"]
        classify = self.verify_module.classify_output

        mixed = classify(
            "primary service.capacity=50; fallback service.capacity=20",
            fault_class="active_and_historical",
            structured=structured,
            legacy=legacy,
        )
        negated = classify(
            "service.capacity=50. Do not use service.capacity=20.",
            fault_class="negated_historical",
            structured=structured,
            legacy=legacy,
        )
        unrelated = classify(
            "service.capacity=20; ticket_count=20",
            fault_class="historical_with_unrelated_scalar",
            structured=structured,
            legacy=legacy,
        )

        self.assertEqual(mixed["status"], "mixed")
        self.assertTrue(mixed["induced"])
        self.assertEqual(negated["status"], "useful")
        self.assertTrue(negated["historical_negative"])
        self.assertFalse(negated["historical_positive"])
        self.assertTrue(unrelated["induced"])

    def test_synthetic_complete_evidence_passes_all_acceptance_checks(
        self,
    ) -> None:
        raw = {
            "structured_current": "service.capacity=50",
            "structured_historical": "service.capacity=20",
            "structured_mixed": (
                "primary service.capacity=50; fallback service.capacity=20"
            ),
            "structured_negated_history": (
                "service.capacity=50. Do not use service.capacity=20."
            ),
            "structured_unrelated_same_number": (
                "service.capacity=20; ticket_count=20"
            ),
            "legacy_unstructured_historical": (
                "database busy_timeout=5000 ms"
            ),
        }
        downstream = {
            "structured_current": "service.capacity=50",
            "structured_historical": "service.capacity=50",
            "structured_mixed": (
                "primary service.capacity=50; fallback service.capacity=50"
            ),
            "structured_negated_history": (
                "service.capacity=50. Do not use service.capacity=20."
            ),
            "structured_unrelated_same_number": (
                "service.capacity=50; ticket_count=20"
            ),
            "legacy_unstructured_historical": "AGENTLITE_MEMORY_CONFLICT v1",
        }
        native_rows: list[dict[str, object]] = []
        managed_rows: list[dict[str, object]] = []
        events: list[dict[str, object]] = []
        for scenario in self.matrix["scenarios"]:
            scenario_id = str(scenario["scenario_id"])
            base: dict[str, object] = {
                "scenario_id": scenario_id,
                "repetition": 1,
                "provider_raw_output": raw[scenario_id],
                "provider_raw_fingerprint": _text_fingerprint(raw[scenario_id]),
                "downstream_observed_text": raw[scenario_id],
                "memory_marker_observed_by_emitter": False,
                "emitter_received_messages": [],
            }
            native_rows.append(base)
            managed = dict(base)
            managed.update(
                {
                    "downstream_observed_text": downstream[scenario_id],
                    "memory_marker_observed_by_emitter": True,
                    "emitter_received_messages": [
                        {
                            "content": (
                                "AGENTLITE_SHARED_MEMORY active_value="
                                + (
                                    "2000"
                                    if scenario_id.startswith("legacy_")
                                    else "50"
                                )
                            )
                        }
                    ],
                }
            )
            managed_rows.append(managed)
            action = str(scenario["expected_managed_action"])
            if action != "safe":
                events.append(
                    {
                        "event_type": "autogen_memory_adoption_guard",
                        "payload": {
                            "agent_id": "FaultEmitter",
                            "original_output_fingerprint": _text_fingerprint(
                                raw[scenario_id]
                            ),
                            "status": action,
                            "compensation_event_count": 1,
                        },
                    }
                )

        def payload(rows: list[dict[str, object]]) -> dict[str, object]:
            return {
                "summary": {"repetitions": 1, "llm_total_tokens": 60},
                "rows": rows,
            }

        report = self.verify_module.evaluate(
            matrix=self.matrix,
            native_payload=payload(native_rows),
            managed_payload=payload(managed_rows),
            seed_manifest={
                "memory_scope_id": "unit-test-scope",
                "search_ref_count": 2,
            },
            trace_events=events,
        )
        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["passed_check_count"],
            report["summary"]["check_count"],
        )


if __name__ == "__main__":
    unittest.main()
