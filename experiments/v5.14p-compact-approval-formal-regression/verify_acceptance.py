from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
V514L_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14l-typed-reliability-formal-regression"
)
V514M_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14m-reviewer-artifact-continuity"
)
V514O_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14o-compact-review-approval"
)


def _load_v514l_verifier() -> ModuleType:
    path = V514L_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514l_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14l acceptance verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514L_VERIFY = _load_v514l_verifier()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the v5.14p Provider regression after the compact "
            "review-approval fix."
        )
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--preflight-report", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(
    *,
    run_root: Path,
    preflight_report: dict[str, Any],
) -> dict[str, Any]:
    report = V514L_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    superseded_check_names = {
        "v514h_agent_profiles_are_reused_exactly",
    }
    checks = [
        item
        for item in report.get("checks", [])
        if str(item.get("name") or "") not in superseded_check_names
    ]
    preregistration = dict(preflight_report.get("preregistration") or {})
    thresholds = dict(preregistration.get("thresholds") or {})
    lineage = dict(preregistration.get("source_lineage") or {})

    mechanism_commit = str(
        lineage.get("mechanism_release_commit") or ""
    ).lower()
    run_commit = _read_text(run_root / "system" / "git-commit.txt").lower()
    checks.append(
        _check(
            "formal_run_descends_from_v514o_release",
            bool(mechanism_commit)
            and bool(run_commit)
            and _is_ancestor(mechanism_commit, run_commit),
            f"mechanism={mechanism_commit};run={run_commit}",
        )
    )

    expected_verifier_hash = str(
        lineage.get("v514o_acceptance_verifier_sha256") or ""
    ).lower()
    actual_verifier_hash = _sha256(V514O_DIR / "verify_acceptance.py")
    checks.append(
        _check(
            "v514o_acceptance_verifier_is_bound",
            bool(expected_verifier_hash)
            and expected_verifier_hash == actual_verifier_hash,
            (
                f"expected={expected_verifier_hash};"
                f"actual={actual_verifier_hash}"
            ),
        )
    )

    frozen_agent_hashes = dict(
        preregistration.get("frozen_agent_sha256") or {}
    )
    for name in ("agent_config_A.json", "agent_config_B.json"):
        expected = str(frozen_agent_hashes.get(name) or "").lower()
        source_actual = _sha256(V514M_DIR / name)
        frozen_actual = _sha256(
            run_root / "system" / "frozen-inputs" / name
        )
        checks.extend(
            [
                _check(
                    f"{name}:v514m_agent_profile_is_bound",
                    bool(expected) and expected == source_actual,
                    f"expected={expected};source={source_actual}",
                ),
                _check(
                    f"{name}:formal_copy_matches_preregistered_profile",
                    bool(expected) and expected == frozen_actual,
                    f"expected={expected};frozen={frozen_actual}",
                ),
            ]
        )

    scenario_rows: list[dict[str, Any]] = []
    total_prior_approved = 0
    total_direct_reviewer_artifacts = 0
    total_origin_mismatches = 0
    total_approval_body_leaks = 0
    total_explicit_decision_memories = 0

    for spec in preregistration.get("scenarios", []):
        scenario_id = str(spec.get("scenario_id") or "")
        directory = str(spec.get("directory") or "")
        sequence = _read_json(
            run_root / directory / "managed" / "sequence_result.json"
        )
        artifact_audit = _audit_managed_artifacts(
            list(sequence.get("tasks") or [])
        )
        decision_count = _count_explicit_decision_memories(
            run_root / directory / "managed" / "agentlite_data"
        )
        row = {
            "scenario_id": scenario_id,
            "directory": directory,
            **artifact_audit,
            "explicit_decision_memory_count": decision_count,
        }
        scenario_rows.append(row)
        total_prior_approved += artifact_audit[
            "prior_artifact_approved_count"
        ]
        total_direct_reviewer_artifacts += artifact_audit[
            "direct_reviewer_artifact_count"
        ]
        total_origin_mismatches += artifact_audit[
            "reviewer_origin_mismatch_count"
        ]
        total_approval_body_leaks += artifact_audit[
            "approval_body_leak_count"
        ]
        total_explicit_decision_memories += decision_count

        minimum = _int(
            thresholds.get(
                "prior_artifact_approved_count_min_per_scenario"
            )
        )
        checks.append(
            _check(
                f"{scenario_id}:reviewer_reference_promotion_is_observed",
                artifact_audit["prior_artifact_approved_count"] >= minimum,
                (
                    f"approved={artifact_audit['prior_artifact_approved_count']};"
                    f"minimum={minimum}"
                ),
            )
        )

    checks.extend(
        [
            _check(
                "managed_reviewer_does_not_replace_business_artifacts",
                total_direct_reviewer_artifacts
                <= _int(
                    thresholds.get(
                        "managed_direct_reviewer_artifact_count_max"
                    )
                ),
                f"direct_reviewer_artifacts={total_direct_reviewer_artifacts}",
            ),
            _check(
                "approved_artifacts_retain_original_owner",
                total_origin_mismatches
                <= _int(
                    thresholds.get(
                        "reviewer_artifact_origin_mismatch_count_max"
                    )
                ),
                f"origin_mismatches={total_origin_mismatches}",
            ),
            _check(
                "reviewer_approval_note_is_never_final_body",
                total_approval_body_leaks
                <= _int(
                    thresholds.get("approval_body_leak_count_max")
                ),
                f"approval_body_leaks={total_approval_body_leaks}",
            ),
            _check(
                "explicit_decisions_enter_typed_shared_memory",
                total_explicit_decision_memories
                >= _int(
                    thresholds.get(
                        "explicit_decision_memory_count_min_total"
                    )
                ),
                (
                    "explicit_decision_memories="
                    f"{total_explicit_decision_memories}"
                ),
            ),
        ]
    )

    passed = all(item["passed"] for item in checks)
    report.update(
        {
            "schema_version": (
                "agentlite.v514p.compact-approval-formal-report.v1"
            ),
            "summary": {
                **dict(report.get("summary") or {}),
                "passed": passed,
                "formal_regression_passed": passed,
                "compact_approval_formal_passed": passed,
                "check_count": len(checks),
                "passed_check_count": sum(
                    bool(item["passed"]) for item in checks
                ),
                "prior_artifact_approved_count": total_prior_approved,
                "direct_reviewer_artifact_count": (
                    total_direct_reviewer_artifacts
                ),
                "reviewer_artifact_origin_mismatch_count": (
                    total_origin_mismatches
                ),
                "approval_body_leak_count": total_approval_body_leaks,
                "explicit_decision_memory_count": (
                    total_explicit_decision_memories
                ),
            },
            "checks": checks,
            "reviewer_artifact_continuity": {
                "scenarios": scenario_rows,
                "prior_artifact_approved_count": total_prior_approved,
                "direct_reviewer_artifact_count": (
                    total_direct_reviewer_artifacts
                ),
                "reviewer_artifact_origin_mismatch_count": (
                    total_origin_mismatches
                ),
                "approval_body_leak_count": total_approval_body_leaks,
                "explicit_decision_memory_count": (
                    total_explicit_decision_memories
                ),
            },
            "superseded_checks": [
                {
                    "name": "v514h_agent_profiles_are_reused_exactly",
                    "reason": (
                        "v5.14p intentionally preregisters the v5.14m "
                        "profiles for all three groups; the replacement "
                        "source and frozen-copy hash checks are mandatory"
                    ),
                }
            ],
        }
    )
    return report


def _audit_managed_artifacts(
    tasks: list[dict[str, Any]],
) -> dict[str, int]:
    prior_approved = 0
    direct_reviewer = 0
    origin_mismatches = 0
    approval_body_leaks = 0
    for task in tasks:
        resolution = str(task.get("final_resolution_kind") or "")
        if resolution == "reviewer_artifact":
            direct_reviewer += 1
        if resolution != "prior_artifact_approved":
            continue
        prior_approved += 1
        origin = str(task.get("final_artifact_origin_source") or "")
        if not origin or origin.lower() == "reviewer":
            origin_mismatches += 1
        final_body = _normalized_body(str(task.get("final_answer") or ""))
        reviewer_messages = [
            _normalized_body(str(message.get("content") or ""))
            for message in task.get("messages", [])
            if isinstance(message, dict)
            and str(message.get("source") or "").lower() == "reviewer"
        ]
        if final_body and final_body in reviewer_messages:
            approval_body_leaks += 1
    return {
        "task_count": len(tasks),
        "prior_artifact_approved_count": prior_approved,
        "direct_reviewer_artifact_count": direct_reviewer,
        "reviewer_origin_mismatch_count": origin_mismatches,
        "approval_body_leak_count": approval_body_leaks,
    }


def _count_explicit_decision_memories(data_root: Path) -> int:
    claim_ids: set[str] = set()
    if not data_root.is_dir():
        return 0
    for path in data_root.rglob("pool_snapshot_latest.json"):
        snapshot = _read_json(path)
        memory_store = dict(snapshot.get("memory_store") or {})
        for claim in memory_store.get("claim_cards", []):
            if not isinstance(claim, dict):
                continue
            scope = str(claim.get("scope") or "")
            slot_id = str(claim.get("slot_id") or "")
            if (
                slot_id == "slot.system.design_decision"
                and scope.startswith("decision.")
            ):
                claim_ids.add(
                    str(claim.get("claim_id") or claim.get("semantic_key"))
                )
    return len(claim_ids)


def _normalized_body(value: str) -> str:
    lines = [
        line.strip()
        for line in value.replace("\r\n", "\n").splitlines()
        if line.strip() and line.strip() != "FINAL_ANSWER_READY"
    ]
    return "\n".join(lines)


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _render_v514p_markdown(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    continuity = dict(
        report.get("reviewer_artifact_continuity") or {}
    )
    lines = [
        "# v5.14p 紧凑审批准入正式回归",
        "",
        f"- 总体通过：`{summary.get('passed')}`",
        (
            f"- 通过项目：`{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        (
            "- 引用晋升成果："
            f"`{summary.get('prior_artifact_approved_count', 0)}`"
        ),
        (
            "- Reviewer 正文替代成果："
            f"`{summary.get('direct_reviewer_artifact_count', 0)}`"
        ),
        (
            "- 审批意见误作正文："
            f"`{summary.get('approval_body_leak_count', 0)}`"
        ),
        (
            "- 明确决策记忆："
            f"`{summary.get('explicit_decision_memory_count', 0)}`"
        ),
        "",
        "## 场景证据",
        "",
        (
            "| 场景 | 任务 | 引用晋升 | Reviewer 替代 | "
            "所有权错误 | 审批正文泄漏 | 决策记忆 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in continuity.get("scenarios", []):
        lines.append(
            f"| {row['scenario_id']} | {row['task_count']} | "
            f"{row['prior_artifact_approved_count']} | "
            f"{row['direct_reviewer_artifact_count']} | "
            f"{row['reviewer_origin_mismatch_count']} | "
            f"{row['approval_body_leak_count']} | "
            f"{row['explicit_decision_memory_count']} |"
        )
    lines.extend(["", "## 失败项目", ""])
    failed = [item for item in report["checks"] if not item["passed"]]
    if failed:
        lines.extend(
            f"- `{item['name']}`：{item['detail']}" for item in failed
        )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 三组使用相同的 v5.14m Agent 配置、任务、模型、温度和轮次。",
            (
                "- v5.14o 仅修正紧凑 Reviewer 审批的协议解释，"
                "不识别 Question A/B、领域实体或固定业务 Agent 名称。"
            ),
            (
                "- Reviewer 只负责否决或按引用批准，最终正文仍属于"
                "最近一份通过当前任务守卫的业务成果。"
            ),
            (
                "- Provider 与端到端协作成本、双盲质量和完整交付"
                "继续沿用未放宽的正式门禁。"
            ),
            "- 本次是单次正式回归，不能替代多次重复实验的方差分析。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    report: dict[str, Any],
    *,
    output_json: Path,
    output_markdown: Path,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_markdown.write_text(
        _render_v514p_markdown(report),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value) if isinstance(value, dict) else {}


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    args = parse_args()
    report = evaluate(
        run_root=args.run_root,
        preflight_report=_read_json(args.preflight_report),
    )
    write_outputs(
        report,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
    )
    print(f"Report: {args.output_json.resolve()}")
    print(f"Markdown: {args.output_markdown.resolve()}")
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
