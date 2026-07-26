from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
V514H_DIR = (
    REPO_ROOT / "experiments" / "v5.14h-formal-regression-acceptance"
)
AGENT_SOURCES = {
    "agent_config_A.json": (
        REPO_ROOT
        / "experiments"
        / "ordinary-developer-autogen"
        / "agent_config.json"
    ),
    "agent_config_B.json": (
        REPO_ROOT
        / "experiments"
        / "v5.14b-fair-cost-quality-preflight"
        / "agent_config_B.json"
    ),
}
PROTOCOL_SURFACES = (
    "model_visible_input_protocol_marker_count",
    "model_visible_agent_output_protocol_marker_count",
    "model_visible_final_output_protocol_marker_count",
)


def _load_v514h_verifier() -> ModuleType:
    path = V514H_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514h_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14h acceptance verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514H_VERIFY = _load_v514h_verifier()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the frozen v5.14j Provider regression after the "
            "semantic-fidelity and conflict-identity fixes."
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
    report = V514H_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = list(report.get("checks") or [])
    preregistration = dict(preflight_report.get("preregistration") or {})
    thresholds = dict(preregistration.get("thresholds") or {})
    regression_focus = list(
        preregistration.get("regression_focus") or []
    )

    v514h_hash = _sha256(V514H_DIR / "preregistration.json")
    expected_v514h_hash = str(
        dict(preregistration.get("source_lineage") or {}).get(
            "v514h_preregistration_sha256"
        )
        or ""
    ).lower()
    checks.append(
        _check(
            "v514h_preregistration_baseline_is_bound",
            bool(expected_v514h_hash)
            and v514h_hash == expected_v514h_hash,
            (
                f"expected={expected_v514h_hash};"
                f"actual={v514h_hash}"
            ),
        )
    )

    agent_hash_rows: list[dict[str, Any]] = []
    expected_agent_hashes = dict(
        preregistration.get("frozen_agent_sha256") or {}
    )
    for filename, source_path in AGENT_SOURCES.items():
        expected = str(expected_agent_hashes.get(filename) or "").lower()
        copied_path = run_root / "system" / "frozen-inputs" / filename
        source_hash = _sha256(source_path)
        copied_hash = _sha256(copied_path)
        agent_hash_rows.append(
            {
                "filename": filename,
                "expected_sha256": expected,
                "source_sha256": source_hash,
                "copied_sha256": copied_hash,
                "matched": (
                    bool(expected)
                    and expected == source_hash == copied_hash
                ),
            }
        )
    checks.append(
        _check(
            "v514h_agent_profiles_are_reused_exactly",
            bool(agent_hash_rows)
            and all(row["matched"] for row in agent_hash_rows),
            json.dumps(agent_hash_rows, ensure_ascii=False, sort_keys=True),
        )
    )

    scenario_rows: list[dict[str, Any]] = []
    focus_rows: list[dict[str, Any]] = []
    forbidden_reasons = {
        str(value)
        for value in preregistration.get(
            "forbidden_delivery_guard_reasons", []
        )
    }
    forbidden_markers = [
        str(value)
        for value in preregistration.get(
            "forbidden_final_output_markers", []
        )
        if str(value)
    ]
    focus_score_min = _float(
        thresholds.get("regression_focus_score_min")
    )
    focus_score_margin = _float(
        thresholds.get(
            "regression_focus_score_noninferiority_margin"
        )
    )
    focus_blocking_max = _int(
        thresholds.get(
            "regression_focus_blocking_finding_count_max"
        )
    )

    for focus in regression_focus:
        scenario_id = str(focus.get("scenario_id") or "")
        directory = str(focus.get("directory") or "")
        task_ids = [
            str(value) for value in focus.get("task_ids", []) if str(value)
        ]
        managed_report = _read_json(
            run_root / directory / "reports" / "managed-agentlite.json"
        )
        token_summary = dict(managed_report.get("token_summary") or {})
        sequence = _read_json(
            run_root / directory / "managed" / "sequence_result.json"
        )
        quality = _read_json(
            run_root
            / directory
            / "comparison"
            / "quality_blind_summary.json"
        )
        task_rows = list(sequence.get("tasks") or [])
        quality_rows = list(quality.get("tasks") or [])

        surface_counts = {
            key: _int(token_summary.get(key)) for key in PROTOCOL_SURFACES
        }
        protocol_total = _int(
            token_summary.get("model_visible_protocol_marker_count")
        )
        surface_total = sum(surface_counts.values())
        wrong_memory_hits = _int(
            token_summary.get("wrong_memory_hit_count")
        )
        fidelity_failures = _int(
            token_summary.get("current_task_fidelity_failure_count")
        )
        enforcement_failures = _int(
            token_summary.get(
                "memory_adoption_enforcement_failure_count"
            )
        )
        scenario_rows.append(
            {
                "scenario_id": scenario_id,
                "directory": directory,
                **surface_counts,
                "model_visible_protocol_marker_count": protocol_total,
                "protocol_surface_total": surface_total,
                "wrong_memory_hit_count": wrong_memory_hits,
                "current_task_fidelity_failure_count": fidelity_failures,
                "memory_adoption_enforcement_failure_count": (
                    enforcement_failures
                ),
            }
        )

        checks.extend(
            [
                _check(
                    f"{scenario_id}:protocol_surfaces_are_isolated",
                    all(
                        surface_counts[key]
                        <= _int(thresholds.get(f"{key}_max"))
                        for key in PROTOCOL_SURFACES
                    ),
                    json.dumps(surface_counts, sort_keys=True),
                ),
                _check(
                    f"{scenario_id}:protocol_surface_total_is_consistent",
                    protocol_total == surface_total,
                    (
                        f"reported={protocol_total};"
                        f"surface_total={surface_total}"
                    ),
                ),
                _check(
                    f"{scenario_id}:has_no_wrong_memory_hit",
                    wrong_memory_hits
                    <= _int(
                        thresholds.get("wrong_memory_hit_count_max")
                    ),
                    (
                        f"wrong={wrong_memory_hits};maximum="
                        f"{_int(thresholds.get('wrong_memory_hit_count_max'))}"
                    ),
                ),
                _check(
                    f"{scenario_id}:current_task_fidelity_is_preserved",
                    fidelity_failures
                    <= _int(
                        thresholds.get(
                            "current_task_fidelity_failure_count_max"
                        )
                    ),
                    f"failures={fidelity_failures}",
                ),
                _check(
                    f"{scenario_id}:memory_guard_preserves_result_type",
                    enforcement_failures
                    <= _int(
                        thresholds.get(
                            "memory_adoption_enforcement_failure_count_max"
                        )
                    ),
                    f"failures={enforcement_failures}",
                ),
            ]
        )

        for task_id in task_ids:
            task = _find_task(task_rows, task_id)
            quality_task = _find_task(quality_rows, task_id)
            scores = dict(quality_task.get("scores") or {})
            delivery = dict(
                quality_task.get("delivery_complete") or {}
            )
            findings = dict(
                quality_task.get("technical_findings") or {}
            )
            managed_score = _float(scores.get("managed"))
            native_score = _float(scores.get("native"))
            managed_blocking = sum(
                1
                for item in findings.get("managed", [])
                if isinstance(item, dict)
                and str(item.get("severity") or "").lower()
                in {"critical", "high"}
            )
            reasons = {
                str(value)
                for value in task.get("delivery_guard_reasons", [])
            }
            final_answer = str(task.get("final_answer") or "")
            leaked_markers = [
                marker
                for marker in forbidden_markers
                if marker.lower() in final_answer.lower()
            ]
            row = {
                "scenario_id": scenario_id,
                "task_id": task_id,
                "runtime_delivery_valid": bool(
                    task.get("delivery_valid")
                ),
                "blind_delivery_complete": bool(
                    delivery.get("managed")
                ),
                "managed_score": managed_score,
                "native_score": native_score,
                "managed_blocking_finding_count": managed_blocking,
                "delivery_guard_reasons": sorted(reasons),
                "forbidden_guard_reasons": sorted(
                    reasons & forbidden_reasons
                ),
                "leaked_final_output_markers": leaked_markers,
                "final_answer_chars": len(final_answer),
            }
            focus_rows.append(row)
            prefix = f"{scenario_id}:{task_id}"
            checks.extend(
                [
                    _check(
                        f"{prefix}:runtime_delivery_is_valid",
                        bool(task)
                        and bool(task.get("delivery_valid"))
                        and bool(final_answer),
                        (
                            f"valid={task.get('delivery_valid')};"
                            f"chars={len(final_answer)}"
                        ),
                    ),
                    _check(
                        f"{prefix}:blind_delivery_is_complete",
                        bool(delivery.get("managed")),
                        f"complete={delivery.get('managed')}",
                    ),
                    _check(
                        f"{prefix}:quality_is_noninferior",
                        managed_score
                        >= focus_score_min
                        and managed_score
                        >= native_score
                        - focus_score_margin,
                        (
                            f"managed={managed_score:.4f};"
                            f"native={native_score:.4f};minimum="
                            f"{focus_score_min:.4f};"
                            f"margin={focus_score_margin:.4f}"
                        ),
                    ),
                    _check(
                        f"{prefix}:has_no_blocking_finding",
                        managed_blocking
                        <= focus_blocking_max,
                        f"blocking={managed_blocking}",
                    ),
                    _check(
                        f"{prefix}:has_no_fidelity_guard_failure",
                        not (reasons & forbidden_reasons),
                        f"reasons={sorted(reasons)}",
                    ),
                    _check(
                        f"{prefix}:final_output_has_no_internal_marker",
                        not leaked_markers,
                        f"markers={leaked_markers}",
                    ),
                ]
            )

    expected_focus_count = sum(
        len(list(row.get("task_ids") or []))
        for row in regression_focus
        if isinstance(row, dict)
    )
    checks.append(
        _check(
            "all_preregistered_regression_focus_tasks_are_observed",
            expected_focus_count > 0
            and len(focus_rows) == expected_focus_count
            and all(row["final_answer_chars"] > 0 for row in focus_rows),
            (
                f"expected={expected_focus_count};"
                f"observed={len(focus_rows)}"
            ),
        )
    )

    baseline_comparison = _baseline_comparison(
        preregistration=preregistration,
        preflight_report=preflight_report,
        focus_rows=focus_rows,
    )
    passed = all(item["passed"] for item in checks)
    report.update(
        {
            "schema_version": (
                "agentlite.v514j.semantic-fidelity-formal-report.v1"
            ),
            "summary": {
                **dict(report.get("summary") or {}),
                "passed": passed,
                "formal_regression_passed": passed,
                "semantic_fidelity_regression_passed": passed,
                "check_count": len(checks),
                "passed_check_count": sum(
                    item["passed"] for item in checks
                ),
                "regression_focus_task_count": len(focus_rows),
                "protocol_surface_marker_count": sum(
                    row["protocol_surface_total"]
                    for row in scenario_rows
                ),
                "scenario_wrong_memory_hit_count": sum(
                    row["wrong_memory_hit_count"]
                    for row in scenario_rows
                ),
                "memory_adoption_enforcement_failure_count": sum(
                    row["memory_adoption_enforcement_failure_count"]
                    for row in scenario_rows
                ),
            },
            "frozen_agent_evidence": agent_hash_rows,
            "semantic_fidelity": {
                "scenarios": scenario_rows,
                "regression_focus_tasks": focus_rows,
            },
            "v514h_baseline_comparison": baseline_comparison,
            "checks": checks,
        }
    )
    return report


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


def _baseline_comparison(
    *,
    preregistration: dict[str, Any],
    preflight_report: dict[str, Any],
    focus_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    aggregates = dict(preflight_report.get("aggregates") or {})
    provider = dict(aggregates.get("provider") or {})
    transport = dict(aggregates.get("transport") or {})
    quality = dict(aggregates.get("quality") or {})
    managed_provider = dict(provider.get("managed") or {})
    native_provider = dict(provider.get("native") or {})
    managed_quality = dict(quality.get("managed") or {})
    native_quality = dict(quality.get("native") or {})
    return {
        "reference": dict(
            preregistration.get("comparison_baseline_metrics") or {}
        ),
        "current": {
            "managed_provider_llm_total_tokens": _int(
                managed_provider.get("total_tokens")
            ),
            "native_provider_llm_total_tokens": _int(
                native_provider.get("total_tokens")
            ),
            "managed_end_to_end_collaboration_tokens": _int(
                transport.get("end_to_end_collaboration_tokens")
            ),
            "native_collaboration_tokens": _int(
                transport.get("native_baseline_tokens")
            ),
            "managed_quality_mean": _float(
                managed_quality.get("mean_score")
            ),
            "native_quality_mean": _float(
                native_quality.get("mean_score")
            ),
            "managed_delivery_complete_count": _int(
                managed_quality.get("delivery_complete_count")
            ),
            "native_delivery_complete_count": _int(
                native_quality.get("delivery_complete_count")
            ),
            "focus_scores": {
                str(row["task_id"]): _float(row["managed_score"])
                for row in focus_rows
            },
        },
        "is_acceptance_gate": False,
        "note": (
            "The prior run is diagnostic only. The preregistered absolute "
            "cost, quality and delivery gates determine acceptance."
        ),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    fidelity = dict(report.get("semantic_fidelity") or {})
    lines = [
        "# v5.14j 语义保真正式回归验收",
        "",
        f"- 总体通过：`{summary.get('passed')}`",
        (
            f"- 通过项目：`{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        (
            "- 回归焦点任务："
            f"`{summary.get('regression_focus_task_count', 0)}`"
        ),
        (
            "- 模型可见协议标记："
            f"`{summary.get('protocol_surface_marker_count', 0)}`"
        ),
        (
            "- 错误记忆命中："
            f"`{summary.get('scenario_wrong_memory_hit_count', 0)}`"
        ),
        "",
        "## 回归焦点任务",
        "",
        "| 任务 | 运行时交付 | 盲评完整 | Managed | Native | 高危发现 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in fidelity.get("regression_focus_tasks", []):
        lines.append(
            f"| {row['task_id']} | {row['runtime_delivery_valid']} | "
            f"{row['blind_delivery_complete']} | "
            f"{row['managed_score']:.2f} | {row['native_score']:.2f} | "
            f"{row['managed_blocking_finding_count']} |"
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
            "- A/B 任务、Agent 配置、模型、温度、最大轮次、盲评规则及 v5.14h 原阈值均保持不变。",
            "- A2、A4、A10、B10 是预注册的根因回归观察点；运行时不识别这些任务编号或题目内容。",
            "- v5.14h 结果只用于诊断改善幅度，不替代本次预注册的绝对验收门槛。",
            "- 本次仍是一次正式运行，不能据此估计跨重复实验的方差或置信区间。",
            "",
        ]
    )
    return "\n".join(lines)


def _find_task(rows: list[Any], task_id: str) -> dict[str, Any]:
    for row in rows:
        if isinstance(row, dict) and str(row.get("task_id") or "") == task_id:
            return dict(row)
    return {}


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


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


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
