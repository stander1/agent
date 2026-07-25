from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14d-final-artifact-resolution-acceptance"
)


def _load_verifier() -> ModuleType:
    path = EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514d_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14d verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _task(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "question": "请给出完整执行方案，总预算不得超过 3000 元。",
        "final_answer": (
            "## 最终可交付方案\n\n"
            "方案包含目标、步骤、责任人、风险和验收方式。"
            "总预算 2900 元，可直接执行。"
        ),
        "delivery_valid": True,
        "final_resolution_kind": "reviewer_artifact",
        "final_artifact_origin_source": "reviewer",
    }


class FinalArtifactResolutionAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.verify = _load_verifier()

    def _write_run(self, root: Path) -> None:
        for scenario in ("A", "B"):
            for group in ("native", "observed", "managed"):
                group_dir = root / scenario / group
                group_dir.mkdir(parents=True)
                (group_dir / "sequence_result.json").write_text(
                    json.dumps(
                        {
                            "tasks": [
                                _task(f"{scenario}{index}")
                                for index in range(1, 4)
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            reports = root / scenario / "reports"
            reports.mkdir(parents=True)
            (reports / "managed-agentlite.json").write_text(
                json.dumps(
                    {
                        "state_summary": {
                            "routing_metadata_state_count": 0,
                            "state_dedup_reuse_count": 2,
                        },
                        "token_summary": {
                            "model_visible_protocol_marker_count": 0,
                        },
                        "metric_rows": [],
                    }
                ),
                encoding="utf-8",
            )

    def test_complete_synthetic_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            report = self.verify.evaluate(
                run_root=root,
                preflight_report={"summary": {"passed": True}},
            )

        self.assertTrue(report["summary"]["passed"])
        self.assertEqual(report["summary"]["task_count"], 18)
        self.assertEqual(report["summary"]["routing_metadata_state_count"], 0)
        self.assertEqual(report["summary"]["state_dedup_reuse_count"], 4)

    def test_approval_note_cannot_be_final_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            path = root / "B" / "managed" / "sequence_result.json"
            sequence = json.loads(path.read_text(encoding="utf-8"))
            sequence["tasks"][0].update(
                {
                    "final_answer": (
                        "基于对前序 writer 产出的验收，确认符合规范，"
                        "现批准作为最终交付物。"
                    ),
                    "final_resolution_kind": "prior_artifact_approved",
                    "final_artifact_origin_source": "writer",
                }
            )
            path.write_text(
                json.dumps(sequence, ensure_ascii=False),
                encoding="utf-8",
            )
            report = self.verify.evaluate(
                run_root=root,
                preflight_report={"summary": {"passed": True}},
            )

        self.assertFalse(report["summary"]["passed"])
        failed = {
            item["name"]
            for item in report["checks"]
            if not item["passed"]
        }
        self.assertIn(
            "B:managed:B1:approval_note_is_not_final_body",
            failed,
        )

    def test_numeric_upper_bound_violation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_run(root)
            path = root / "A" / "managed" / "sequence_result.json"
            sequence = json.loads(path.read_text(encoding="utf-8"))
            sequence["tasks"][0]["final_answer"] = (
                "## 最终可交付方案\n\n"
                "方案完整且可以执行，总预算 3800 元。"
            )
            path.write_text(
                json.dumps(sequence, ensure_ascii=False),
                encoding="utf-8",
            )
            report = self.verify.evaluate(
                run_root=root,
                preflight_report={"summary": {"passed": True}},
            )

        failed = {
            item["name"]
            for item in report["checks"]
            if not item["passed"]
        }
        self.assertIn(
            "A:managed:A1:numeric_upper_bound_is_respected",
            failed,
        )

    def test_runner_reuses_frozen_v514b_preflight_with_stage_hook(self) -> None:
        runner = (EXPERIMENT_DIR / "run_openeuler.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("v5.14b-fair-cost-quality-preflight/run_openeuler.sh", runner)
        self.assertIn("AGENTLITE_V514B_POST_VERIFY", runner)
        self.assertIn("AGENTLITE_V514B_EXPERIMENT_LABEL=\"v5.14d\"", runner)
        self.assertIn("AGENTLITE_V514B_MEMORY_SCOPE_PREFIX=\"v514d\"", runner)


if __name__ == "__main__":
    unittest.main()
