from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14l-typed-reliability-formal-regression"
)
BASE_TEST_PATH = (
    REPO_ROOT
    / "tests"
    / "test_v514j_semantic_fidelity_formal_regression.py"
)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = _load_module(
    "v514l_verify",
    EXPERIMENT_DIR / "verify_acceptance.py",
)
BASE_TEST = _load_module("v514j_test_fixture", BASE_TEST_PATH)


class TypedReliabilityFormalRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.preregistration = json.loads(
            (EXPERIMENT_DIR / "preregistration.json").read_text(
                encoding="utf-8"
            )
        )
        cls.base_fixture = (
            BASE_TEST.SemanticFidelityFormalRegressionTest()
        )
        cls.base_fixture.preregistration = cls.preregistration
        cls.head_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _write_fixture(self, root: Path) -> dict[str, Any]:
        self.base_fixture._write_run(root)
        system = root / "system"
        system.mkdir(parents=True, exist_ok=True)
        (system / "git-commit.txt").write_text(
            self.head_commit + "\n",
            encoding="utf-8",
        )

        additional_focus = {
            "A": ("A6", "A7"),
            "B": ("B8", "B9"),
        }
        for directory, task_ids in additional_focus.items():
            quality_path = (
                root
                / directory
                / "comparison"
                / "quality_blind_summary.json"
            )
            quality = _read_json(quality_path)
            rows = list(quality.get("tasks") or [])
            for task_id in task_ids:
                rows.append(
                    {
                        "task_id": task_id,
                        "scores": {
                            "native": 9.0,
                            "observed": 8.0,
                            "managed": 9.0,
                        },
                        "delivery_complete": {
                            "native": True,
                            "observed": True,
                            "managed": True,
                        },
                        "technical_findings": {
                            "native": [],
                            "observed": [],
                            "managed": [],
                        },
                    }
                )
            quality["tasks"] = rows
            _write_json(quality_path, quality)

        for directory in ("A", "B"):
            report_path = (
                root / directory / "reports" / "managed-agentlite.json"
            )
            report = _read_json(report_path)
            token_summary = dict(report.get("token_summary") or {})
            token_summary["memory_epistemic_deferred_count"] = (
                1 if directory == "B" else 0
            )
            report["token_summary"] = token_summary
            state_summary = dict(report.get("state_summary") or {})
            state_summary.update(
                {
                    "state_type_counts": (
                        {
                            "artifact_state": 20,
                            "retrieval_state": 1,
                            "embedding_state": 1,
                        }
                        if directory == "B"
                        else {"artifact_state": 20}
                    ),
                    "contains_embedding_refs_count": (
                        1 if directory == "B" else 0
                    ),
                }
            )
            report["state_summary"] = state_summary
            _write_json(report_path, report)
            self._write_runtime_snapshot(root, directory=directory)

        preflight = self.base_fixture._preflight_report()
        preflight["preregistration"] = self.preregistration
        return preflight

    def _write_runtime_snapshot(
        self,
        root: Path,
        *,
        directory: str,
    ) -> None:
        driver = (
            root
            / directory
            / "managed"
            / "agentlite_data"
            / "sessions"
            / "launch_test"
            / "autogen_driver"
        )
        driver.mkdir(parents=True, exist_ok=True)
        states: list[dict[str, Any]] = []
        claims: list[dict[str, Any]] = []
        if directory == "B":
            claims.append(
                {
                    "candidate_id": "claim_expected_latency",
                    "certainty": "uncertain",
                    "admission_status": "pending_confirmation",
                    "value": "42",
                }
            )
            retrieval_text = (
                "retrieval_state: chunk_ids=[chunk_17,chunk_22]; "
                "source_ids=[source_alpha]; "
                "score_map={chunk_17:0.88,chunk_22:0.74}"
            )
            embedding_text = (
                "embedding_state: query_embedding_id=query_vec_17; "
                "vector_dim=384; "
                "candidates=[chunk_vec_17,chunk_vec_22]; "
                "similarity_scores="
                "{chunk_vec_17:0.91,chunk_vec_22:0.72}"
            )
            states.extend(
                [
                    self._write_state(
                        driver,
                        state_id="state_retrieval_fixture",
                        state_type="retrieval_state",
                        content=retrieval_text,
                        payload={
                            "chunk_ids": ["chunk_17", "chunk_22"],
                            "source_ids": ["source_alpha"],
                            "score_map": {
                                "chunk_17": 0.88,
                                "chunk_22": 0.74,
                            },
                        },
                        contains_embedding_refs=False,
                    ),
                    self._write_state(
                        driver,
                        state_id="state_embedding_fixture",
                        state_type="embedding_state",
                        content=embedding_text,
                        payload={
                            "query_embedding_id": "query_vec_17",
                            "chunk_embedding_ids": [
                                "chunk_vec_17",
                                "chunk_vec_22",
                            ],
                            "vector_dim": 384,
                            "score_map": {
                                "chunk_vec_17": 0.91,
                                "chunk_vec_22": 0.72,
                            },
                        },
                        contains_embedding_refs=True,
                    ),
                ]
            )
        _write_json(
            driver / "pool_snapshot_latest.json",
            {
                "state_pool": {"states": states},
                "memory_store": {"claim_candidates": claims},
            },
        )

    @staticmethod
    def _write_state(
        driver: Path,
        *,
        state_id: str,
        state_type: str,
        content: str,
        payload: dict[str, Any],
        contains_embedding_refs: bool,
    ) -> dict[str, Any]:
        state_dir = driver / "state" / "state_hot"
        audit_dir = driver / "state" / "state_cold"
        state_dir.mkdir(parents=True, exist_ok=True)
        audit_dir.mkdir(parents=True, exist_ok=True)
        stored = {
            **payload,
            "payload_kind": "structured_non_text",
            "extraction_method": "framework_output_key_value_v1",
            "raw_content_sha256": hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest(),
        }
        _write_json(state_dir / f"{state_id}.json", stored)
        _write_json(
            audit_dir / f"{state_id}.audit.json",
            {"content": content},
        )
        return {
            "state_id": state_id,
            "state_type": state_type,
            "payload_kind": "structured_non_text",
            "contains_embedding_refs": contains_embedding_refs,
            "tier": "hot",
            "lifecycle": "active",
        }

    def test_frozen_typed_reliability_formal_fixture_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self._write_fixture(root)
            report = VERIFY.evaluate(
                run_root=root,
                preflight_report=preflight,
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(
            report["summary"]["regression_focus_task_count"],
            8,
        )
        self.assertEqual(
            report["summary"]["epistemic_deferred_count"],
            1,
        )
        self.assertEqual(
            report["summary"]["structured_state_audit_failure_count"],
            0,
        )

    def test_uncertain_claim_admission_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self._write_fixture(root)
            snapshot_path = _snapshot_path(root, "B")
            snapshot = _read_json(snapshot_path)
            snapshot["memory_store"]["claim_candidates"][0][
                "admission_status"
            ] = "admitted"
            _write_json(snapshot_path, snapshot)
            report = VERIFY.evaluate(
                run_root=root,
                preflight_report=preflight,
            )

        checks = _checks(report)
        self.assertFalse(
            checks[
                "B-formal:epistemic_candidates_are_not_promoted"
            ]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_corrupt_structured_state_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self._write_fixture(root)
            payload_path = (
                _snapshot_path(root, "B").parent
                / "state"
                / "state_hot"
                / "state_embedding_fixture.json"
            )
            payload = _read_json(payload_path)
            payload["raw_content_sha256"] = "0" * 64
            _write_json(payload_path, payload)
            report = VERIFY.evaluate(
                run_root=root,
                preflight_report=preflight,
            )

        checks = _checks(report)
        self.assertFalse(
            checks[
                "B-formal:structured_state_sources_are_auditable"
            ]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_review_feedback_cannot_be_successful_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self._write_fixture(root)
            sequence_path = root / "A" / "managed" / "sequence_result.json"
            sequence = _read_json(sequence_path)
            task = _find_task(sequence["tasks"], "A7")
            task["final_answer"] = (
                "## Review findings\n"
                "The draft lacks rollback steps. Writer must revise and "
                "resubmit.\n"
                "## Final deliverable\n"
                "This is only a revision checklist, not the corrected "
                "artifact."
            )
            _write_json(sequence_path, sequence)
            report = VERIFY.evaluate(
                run_root=root,
                preflight_report=preflight,
            )

        checks = _checks(report)
        self.assertFalse(
            checks[
                "A-formal:review_feedback_is_not_final_delivery"
            ]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_cross_dimension_quantity_reason_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = self._write_fixture(root)
            sequence_path = root / "A" / "managed" / "sequence_result.json"
            sequence = _read_json(sequence_path)
            task = _find_task(sequence["tasks"], "A6")
            task["delivery_guard_reasons"] = [
                "numeric_constraint_exceeded:"
                "budget_upper_bound=30;observed_total_upper=2800"
            ]
            _write_json(sequence_path, sequence)
            report = VERIFY.evaluate(
                run_root=root,
                preflight_report=preflight,
            )

        checks = _checks(report)
        self.assertFalse(
            checks[
                "A-formal:quantity_dimensions_do_not_cross"
            ]["passed"]
        )
        self.assertFalse(report["summary"]["passed"])

    def test_runner_reuses_frozen_inputs_and_supports_resume(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("v5.14f-formal-scale-acceptance", runner)
        self.assertIn("question_A_formal.json", runner)
        self.assertIn("question_B_formal.json", runner)
        self.assertIn("AGENTLITE_V514L_RESUME", runner)
        self.assertIn("typed_reliability_formal_report.json", runner)

    def test_technical_recovery_reuses_frozen_scoring_checkpoints(
        self,
    ) -> None:
        recovery = (
            EXPERIMENT_DIR / "resume_technical_audit.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("quality_blind_batch.json", recovery)
        self.assertIn("quality_blind_mapping.json", recovery)
        self.assertIn("quality_blind_scores.json", recovery)
        self.assertIn("quality_blind_technical_scores.json", recovery)
        self.assertIn("--resume", recovery)
        self.assertIn("scoring-recovery-history.txt", recovery)
        self.assertNotIn("code_app.py", recovery)


def _snapshot_path(root: Path, directory: str) -> Path:
    return (
        root
        / directory
        / "managed"
        / "agentlite_data"
        / "sessions"
        / "launch_test"
        / "autogen_driver"
        / "pool_snapshot_latest.json"
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _find_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    for task in tasks:
        if task.get("task_id") == task_id:
            return task
    raise AssertionError(f"Task not found: {task_id}")


def _checks(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["name"]: item for item in report["checks"]}


if __name__ == "__main__":
    unittest.main()
