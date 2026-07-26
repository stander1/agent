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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.reliability.final_delivery_guard import assess_final_delivery


V514J_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14j-semantic-fidelity-formal-regression"
)
V514K_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14k-typed-reliability-acceptance"
)
REUSABLE_CERTAINTIES = {
    "observed",
    "asserted",
    "confirmed",
    "verified",
}
STRUCTURED_STATE_TYPES = {"retrieval_state", "embedding_state"}


def _load_v514j_verifier() -> ModuleType:
    path = V514J_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514j_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14j acceptance verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514J_VERIFY = _load_v514j_verifier()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the frozen v5.14l Provider regression after the "
            "typed-reliability and epistemic-admission fixes."
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
    report = V514J_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = list(report.get("checks") or [])
    preregistration = dict(preflight_report.get("preregistration") or {})
    thresholds = dict(preregistration.get("thresholds") or {})
    lineage = dict(preregistration.get("source_lineage") or {})

    v514j_preregistration = _read_json(
        V514J_DIR / "preregistration.json"
    )
    expected_v514j_hash = str(
        lineage.get("v514j_preregistration_sha256") or ""
    ).lower()
    actual_v514j_hash = _sha256(V514J_DIR / "preregistration.json")
    checks.append(
        _check(
            "v514j_preregistration_baseline_is_bound",
            bool(expected_v514j_hash)
            and expected_v514j_hash == actual_v514j_hash,
            (
                f"expected={expected_v514j_hash};"
                f"actual={actual_v514j_hash}"
            ),
        )
    )

    old_thresholds = dict(v514j_preregistration.get("thresholds") or {})
    drifted_thresholds = {
        key: {
            "v514j": value,
            "v514l": thresholds.get(key),
        }
        for key, value in old_thresholds.items()
        if thresholds.get(key) != value
    }
    checks.append(
        _check(
            "v514j_thresholds_are_unchanged",
            not drifted_thresholds,
            f"drifted={drifted_thresholds}",
        )
    )

    expected_v514k_hash = str(
        lineage.get("v514k_acceptance_verifier_sha256") or ""
    ).lower()
    actual_v514k_hash = _sha256(V514K_DIR / "verify_acceptance.py")
    mechanism_commit = str(
        lineage.get("mechanism_release_commit") or ""
    ).lower()
    run_commit = _read_text(run_root / "system" / "git-commit.txt").lower()
    checks.extend(
        [
            _check(
                "v514k_acceptance_verifier_is_bound",
                bool(expected_v514k_hash)
                and expected_v514k_hash == actual_v514k_hash,
                (
                    f"expected={expected_v514k_hash};"
                    f"actual={actual_v514k_hash}"
                ),
            ),
            _check(
                "formal_run_descends_from_v514k_release",
                bool(mechanism_commit)
                and bool(run_commit)
                and _is_ancestor(mechanism_commit, run_commit),
                (
                    f"mechanism={mechanism_commit};"
                    f"run={run_commit}"
                ),
            ),
        ]
    )

    required_states = dict(
        preregistration.get("required_formal_state_types_by_scenario")
        or {}
    )
    scenario_rows: list[dict[str, Any]] = []
    total_metric_deferred = 0
    total_snapshot_deferred = 0
    total_epistemic_violations = 0
    total_state_audit_failures = 0
    total_review_feedback_deliveries = 0
    total_quantity_dimension_failures = 0

    for spec in preregistration.get("scenarios", []):
        scenario_id = str(spec.get("scenario_id") or "")
        directory = str(spec.get("directory") or "")
        managed_report = _read_json(
            run_root
            / directory
            / "reports"
            / "managed-agentlite.json"
        )
        token_summary = dict(managed_report.get("token_summary") or {})
        state_summary = dict(managed_report.get("state_summary") or {})
        state_type_counts = {
            str(key): _int(value)
            for key, value in dict(
                state_summary.get("state_type_counts") or {}
            ).items()
        }
        metric_present = "memory_epistemic_deferred_count" in token_summary
        metric_deferred = _int(
            token_summary.get("memory_epistemic_deferred_count")
        )
        snapshot_audit = _audit_runtime_snapshots(
            run_root=run_root,
            directory=directory,
        )
        sequence = _read_json(
            run_root / directory / "managed" / "sequence_result.json"
        )
        delivery_audit = _audit_final_deliveries(
            list(sequence.get("tasks") or [])
        )
        required = {
            str(key): _int(value)
            for key, value in dict(
                required_states.get(scenario_id) or {}
            ).items()
        }
        report_state_match = all(
            state_type_counts.get(state_type, 0) >= minimum
            for state_type, minimum in required.items()
        )
        snapshot_state_match = all(
            snapshot_audit["state_type_counts"].get(state_type, 0)
            >= minimum
            for state_type, minimum in required.items()
        )
        embedding_refs = _int(
            state_summary.get("contains_embedding_refs_count")
        )

        row = {
            "scenario_id": scenario_id,
            "directory": directory,
            "epistemic_metric_present": metric_present,
            "epistemic_deferred_count": metric_deferred,
            "snapshot_deferred_claim_count": snapshot_audit[
                "deferred_claim_count"
            ],
            "epistemic_admission_violation_count": snapshot_audit[
                "epistemic_admission_violation_count"
            ],
            "state_type_counts": state_type_counts,
            "snapshot_state_type_counts": snapshot_audit[
                "state_type_counts"
            ],
            "structured_state_audit_failure_count": snapshot_audit[
                "structured_state_audit_failure_count"
            ],
            "structured_state_audit_rows": snapshot_audit[
                "structured_state_rows"
            ],
            "contains_embedding_refs_count": embedding_refs,
            "review_feedback_final_delivery_count": delivery_audit[
                "review_feedback_final_delivery_count"
            ],
            "quantity_cross_dimension_guard_failure_count": delivery_audit[
                "quantity_cross_dimension_guard_failure_count"
            ],
        }
        scenario_rows.append(row)
        total_metric_deferred += metric_deferred
        total_snapshot_deferred += snapshot_audit[
            "deferred_claim_count"
        ]
        total_epistemic_violations += snapshot_audit[
            "epistemic_admission_violation_count"
        ]
        total_state_audit_failures += snapshot_audit[
            "structured_state_audit_failure_count"
        ]
        total_review_feedback_deliveries += delivery_audit[
            "review_feedback_final_delivery_count"
        ]
        total_quantity_dimension_failures += delivery_audit[
            "quantity_cross_dimension_guard_failure_count"
        ]

        checks.extend(
            [
                _check(
                    f"{scenario_id}:epistemic_metric_is_present",
                    metric_present,
                    f"present={metric_present};deferred={metric_deferred}",
                ),
                _check(
                    f"{scenario_id}:epistemic_candidates_are_not_promoted",
                    snapshot_audit[
                        "epistemic_admission_violation_count"
                    ]
                    <= _int(
                        thresholds.get(
                            "epistemic_admission_violation_count_max"
                        )
                    ),
                    (
                        "violations="
                        f"{snapshot_audit['epistemic_admission_violation_count']}"
                    ),
                ),
                _check(
                    f"{scenario_id}:review_feedback_is_not_final_delivery",
                    delivery_audit[
                        "review_feedback_final_delivery_count"
                    ]
                    <= _int(
                        thresholds.get(
                            "review_feedback_final_delivery_count_max"
                        )
                    ),
                    (
                        "violations="
                        f"{delivery_audit['review_feedback_final_delivery_count']}"
                    ),
                ),
                _check(
                    f"{scenario_id}:quantity_dimensions_do_not_cross",
                    delivery_audit[
                        "quantity_cross_dimension_guard_failure_count"
                    ]
                    <= _int(
                        thresholds.get(
                            "quantity_cross_dimension_guard_failure_count_max"
                        )
                    ),
                    (
                        "violations="
                        f"{delivery_audit['quantity_cross_dimension_guard_failure_count']}"
                    ),
                ),
            ]
        )
        if required:
            checks.extend(
                [
                    _check(
                        f"{scenario_id}:required_structured_states_reported",
                        report_state_match,
                        (
                            f"required={required};"
                            f"reported={state_type_counts}"
                        ),
                    ),
                    _check(
                        f"{scenario_id}:required_structured_states_persisted",
                        snapshot_state_match,
                        (
                            f"required={required};snapshot="
                            f"{snapshot_audit['state_type_counts']}"
                        ),
                    ),
                    _check(
                        f"{scenario_id}:structured_state_sources_are_auditable",
                        snapshot_audit[
                            "structured_state_audit_failure_count"
                        ]
                        <= _int(
                            thresholds.get(
                                "structured_state_audit_failure_count_max"
                            )
                        ),
                        (
                            "failures="
                            f"{snapshot_audit['structured_state_audit_failure_count']}"
                        ),
                    ),
                    _check(
                        f"{scenario_id}:embedding_references_are_present",
                        embedding_refs
                        >= _int(
                            thresholds.get(
                                "contains_embedding_refs_count_min_b"
                            )
                        ),
                        f"contains_embedding_refs={embedding_refs}",
                    ),
                ]
            )

    deferred_minimum = _int(
        thresholds.get("epistemic_deferred_count_min_total")
    )
    checks.extend(
        [
            _check(
                "formal_run_exercises_epistemic_deferral",
                total_metric_deferred >= deferred_minimum
                and total_snapshot_deferred >= deferred_minimum,
                (
                    f"metric={total_metric_deferred};"
                    f"snapshot={total_snapshot_deferred};"
                    f"minimum={deferred_minimum}"
                ),
            ),
            _check(
                "formal_run_has_no_epistemic_admission_violation",
                total_epistemic_violations
                <= _int(
                    thresholds.get(
                        "epistemic_admission_violation_count_max"
                    )
                ),
                f"violations={total_epistemic_violations}",
            ),
            _check(
                "formal_run_has_no_structured_state_audit_failure",
                total_state_audit_failures
                <= _int(
                    thresholds.get(
                        "structured_state_audit_failure_count_max"
                    )
                ),
                f"failures={total_state_audit_failures}",
            ),
            _check(
                "formal_run_has_no_review_feedback_final_delivery",
                total_review_feedback_deliveries
                <= _int(
                    thresholds.get(
                        "review_feedback_final_delivery_count_max"
                    )
                ),
                f"violations={total_review_feedback_deliveries}",
            ),
            _check(
                "formal_run_has_no_quantity_cross_dimension_failure",
                total_quantity_dimension_failures
                <= _int(
                    thresholds.get(
                        "quantity_cross_dimension_guard_failure_count_max"
                    )
                ),
                f"violations={total_quantity_dimension_failures}",
            ),
        ]
    )

    passed = all(item["passed"] for item in checks)
    report.update(
        {
            "schema_version": (
                "agentlite.v514l.typed-reliability-formal-report.v1"
            ),
            "summary": {
                **dict(report.get("summary") or {}),
                "passed": passed,
                "formal_regression_passed": passed,
                "typed_reliability_formal_passed": passed,
                "check_count": len(checks),
                "passed_check_count": sum(
                    item["passed"] for item in checks
                ),
                "epistemic_deferred_count": total_metric_deferred,
                "snapshot_deferred_claim_count": total_snapshot_deferred,
                "epistemic_admission_violation_count": (
                    total_epistemic_violations
                ),
                "structured_state_audit_failure_count": (
                    total_state_audit_failures
                ),
                "review_feedback_final_delivery_count": (
                    total_review_feedback_deliveries
                ),
                "quantity_cross_dimension_guard_failure_count": (
                    total_quantity_dimension_failures
                ),
            },
            "legacy_threshold_drift_from_v514j": drifted_thresholds,
            "typed_reliability": {
                "scenarios": scenario_rows,
            },
            "checks": checks,
        }
    )
    return report


def _audit_runtime_snapshots(
    *,
    run_root: Path,
    directory: str,
) -> dict[str, Any]:
    snapshots = sorted(
        (
            run_root
            / directory
            / "managed"
            / "agentlite_data"
            / "sessions"
        ).glob("*/autogen_driver/pool_snapshot_latest.json")
    )
    states_by_id: dict[str, tuple[dict[str, Any], Path]] = {}
    claims_by_id: dict[str, dict[str, Any]] = {}
    for snapshot_path in snapshots:
        snapshot = _read_json(snapshot_path)
        state_pool = dict(snapshot.get("state_pool") or {})
        memory_store = dict(snapshot.get("memory_store") or {})
        for state in state_pool.get("states", []):
            if not isinstance(state, dict):
                continue
            state_id = str(state.get("state_id") or "")
            if state_id:
                states_by_id[state_id] = (state, snapshot_path.parent)
        for claim in memory_store.get("claim_candidates", []):
            if not isinstance(claim, dict):
                continue
            claim_id = str(claim.get("candidate_id") or "")
            if claim_id:
                claims_by_id[claim_id] = claim

    deferred_claims = [
        claim
        for claim in claims_by_id.values()
        if str(claim.get("certainty") or "").casefold()
        not in REUSABLE_CERTAINTIES
    ]
    epistemic_violations = [
        claim
        for claim in deferred_claims
        if str(claim.get("admission_status") or "")
        != "pending_confirmation"
    ]

    state_type_counts: dict[str, int] = {}
    structured_rows: list[dict[str, Any]] = []
    structured_failures = 0
    for state_id, (state, driver_root) in states_by_id.items():
        state_type = str(state.get("state_type") or "")
        state_type_counts[state_type] = state_type_counts.get(state_type, 0) + 1
        if state_type not in STRUCTURED_STATE_TYPES:
            continue
        row = _audit_structured_state(
            state_id=state_id,
            state_type=state_type,
            state=state,
            driver_root=driver_root,
        )
        structured_rows.append(row)
        structured_failures += int(not row["valid"])

    return {
        "snapshot_count": len(snapshots),
        "claim_candidate_count": len(claims_by_id),
        "deferred_claim_count": len(deferred_claims),
        "epistemic_admission_violation_count": len(
            epistemic_violations
        ),
        "state_type_counts": state_type_counts,
        "structured_state_audit_failure_count": structured_failures,
        "structured_state_rows": structured_rows,
    }


def _audit_structured_state(
    *,
    state_id: str,
    state_type: str,
    state: dict[str, Any],
    driver_root: Path,
) -> dict[str, Any]:
    state_files = [
        path
        for path in (driver_root / "state").glob(f"state_*/{state_id}.json")
        if not path.name.endswith(".audit.json")
    ]
    audit_files = list(
        (driver_root / "state").glob(
            f"state_*/{state_id}.audit.json"
        )
    )
    payload = _read_json(state_files[0]) if len(state_files) == 1 else {}
    audit = _read_json(audit_files[0]) if len(audit_files) == 1 else {}
    audit_content = str(
        audit.get("content")
        or audit.get("guarded_content")
        or audit.get("original_content")
        or ""
    )
    source_hash = (
        hashlib.sha256(audit_content.encode("utf-8")).hexdigest()
        if audit_content
        else ""
    )
    payload_hash = str(payload.get("raw_content_sha256") or "")
    base_valid = (
        len(state_files) == 1
        and len(audit_files) == 1
        and payload.get("payload_kind") == "structured_non_text"
        and payload.get("extraction_method")
        == "framework_output_key_value_v1"
        and bool(payload_hash)
        and payload_hash == source_hash
    )
    if state_type == "embedding_state":
        type_valid = (
            bool(payload.get("query_embedding_id"))
            and _int(payload.get("vector_dim")) > 0
            and bool(payload.get("score_map"))
            and bool(state.get("contains_embedding_refs"))
        )
    else:
        type_valid = bool(
            payload.get("chunk_ids") or payload.get("source_ids")
        )
    return {
        "state_id": state_id,
        "state_type": state_type,
        "valid": bool(base_valid and type_valid),
        "state_file_count": len(state_files),
        "audit_file_count": len(audit_files),
        "payload_hash": payload_hash,
        "source_hash": source_hash,
        "contains_embedding_refs": bool(
            state.get("contains_embedding_refs")
        ),
    }


def _audit_final_deliveries(tasks: list[Any]) -> dict[str, int]:
    review_feedback_deliveries = 0
    quantity_dimension_failures = 0
    for value in tasks:
        if not isinstance(value, dict):
            continue
        reasons = [
            str(reason)
            for reason in value.get("delivery_guard_reasons", [])
        ]
        quantity_dimension_failures += int(
            any("budget_upper_bound=30" in reason for reason in reasons)
        )
        final_answer = str(value.get("final_answer") or "")
        request = str(
            value.get("question")
            or value.get("prompt")
            or value.get("task_prompt")
            or ""
        )
        if not final_answer or not bool(value.get("delivery_valid")):
            continue
        assessment = assess_final_delivery(
            request=request,
            content=final_answer,
            source=str(
                value.get("final_artifact_origin_source")
                or value.get("final_source")
                or "reviewer"
            ),
            require_marker=False,
        )
        review_feedback_deliveries += int(
            "review_feedback_not_final_artifact" in assessment.reasons
        )
    return {
        "review_feedback_final_delivery_count": (
            review_feedback_deliveries
        ),
        "quantity_cross_dimension_guard_failure_count": (
            quantity_dimension_failures
        ),
    }


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _render_markdown(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    typed = dict(report.get("typed_reliability") or {})
    lines = [
        "# v5.14l 类型化可靠性正式回归验收",
        "",
        f"- 总体通过：`{summary.get('passed')}`",
        (
            f"- 通过项目：`{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        (
            "- 延后确认事实："
            f"`{summary.get('epistemic_deferred_count', 0)}`"
        ),
        (
            "- 错误记忆准入："
            f"`{summary.get('epistemic_admission_violation_count', 0)}`"
        ),
        (
            "- 结构状态审计失败："
            f"`{summary.get('structured_state_audit_failure_count', 0)}`"
        ),
        (
            "- 审查意见误交付："
            f"`{summary.get('review_feedback_final_delivery_count', 0)}`"
        ),
        (
            "- 跨量纲守卫错误："
            f"`{summary.get('quantity_cross_dimension_guard_failure_count', 0)}`"
        ),
        "",
        "## 场景证据",
        "",
        "| 场景 | 延后事实 | 准入违规 | retrieval | embedding | 向量引用 | 状态审计失败 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in typed.get("scenarios", []):
        state_counts = dict(row.get("state_type_counts") or {})
        lines.append(
            f"| {row['scenario_id']} | "
            f"{row['epistemic_deferred_count']} | "
            f"{row['epistemic_admission_violation_count']} | "
            f"{state_counts.get('retrieval_state', 0)} | "
            f"{state_counts.get('embedding_state', 0)} | "
            f"{row['contains_embedding_refs_count']} | "
            f"{row['structured_state_audit_failure_count']} |"
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
            "- A/B 任务、Agent、模型、温度、最大轮次和 v5.14j 既有阈值保持不变。",
            "- 不确定事实数量不是越多越好；该门槛只确认 B8 的暂缓结论实际经过候选池。",
            "- 结构状态必须来自真实输出字段，摘要哈希必须与审计原文一致。",
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
        _render_markdown(report),
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
