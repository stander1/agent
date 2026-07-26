from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
V514F_DIR = REPO_ROOT / "experiments" / "v5.14f-formal-scale-acceptance"
LEGACY_THRESHOLD_KEYS = (
    "managed_provider_token_reduction_min",
    "managed_transport_token_reduction_min",
    "managed_quality_noninferiority_margin",
    "managed_delivery_complete_delta_min",
    "rewrite_error_fallback_count_max",
    "managed_terminal_score_min",
    "managed_terminal_score_noninferiority_margin",
    "managed_terminal_blocking_finding_count_max",
    "memory_hit_count_min",
    "memory_injected_count_min",
    "useful_memory_hit_count_min",
    "wrong_memory_hit_count_max",
    "current_task_fidelity_failure_count_max",
    "model_visible_protocol_marker_count_max",
    "routing_metadata_state_count_max",
)


def _load_v514f_verifier() -> ModuleType:
    path = V514F_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v514f_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14f acceptance verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514F_VERIFY = _load_v514f_verifier()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the v5.14h unchanged formal run and dynamic review "
            "governance evidence."
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
    report = V514F_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = list(report.get("checks") or [])
    preregistration = dict(preflight_report.get("preregistration") or {})
    thresholds = dict(preregistration.get("thresholds") or {})
    scenario_specs = list(preregistration.get("scenarios") or [])
    frozen_hashes = dict(preregistration.get("frozen_input_sha256") or {})

    v514f_preregistration = _read_json(
        V514F_DIR / "preregistration.json"
    )
    v514f_thresholds = dict(
        v514f_preregistration.get("thresholds") or {}
    )
    drifted_thresholds = {
        key: {
            "v514f": v514f_thresholds.get(key),
            "v514h": thresholds.get(key),
        }
        for key in LEGACY_THRESHOLD_KEYS
        if thresholds.get(key) != v514f_thresholds.get(key)
    }
    checks.append(
        _check(
            "v514f_cost_quality_thresholds_are_unchanged",
            not drifted_thresholds,
            f"drifted={drifted_thresholds}",
        )
    )

    input_hash_rows: list[dict[str, Any]] = []
    for filename in ("question_A_formal.json", "question_B_formal.json"):
        expected = str(frozen_hashes.get(filename) or "").lower()
        source_path = V514F_DIR / filename
        copied_path = run_root / "system" / "frozen-inputs" / filename
        source_hash = _sha256(source_path)
        copied_hash = _sha256(copied_path)
        matched = bool(expected) and source_hash == expected == copied_hash
        input_hash_rows.append(
            {
                "filename": filename,
                "expected_sha256": expected,
                "source_sha256": source_hash,
                "copied_sha256": copied_hash,
                "matched": matched,
            }
        )
    checks.append(
        _check(
            "v514f_frozen_tasks_are_reused_exactly",
            all(row["matched"] for row in input_hash_rows),
            json.dumps(input_hash_rows, ensure_ascii=False, sort_keys=True),
        )
    )

    scenario_rows: list[dict[str, Any]] = []
    for spec in scenario_specs:
        scenario_id = str(spec.get("scenario_id") or "")
        directory = str(spec.get("directory") or "")
        managed_report = _read_json(
            run_root
            / directory
            / "reports"
            / "managed-agentlite.json"
        )
        governance = _governance_summary(managed_report)
        scenario_rows.append(
            {
                "scenario_id": scenario_id,
                "directory": directory,
                **governance,
            }
        )
        checks.append(
            _check(
                f"{scenario_id}:dynamic_review_authority_is_observed",
                governance["event_count"]
                >= _int(
                    thresholds.get(
                        "review_governance_event_count_min_per_scenario"
                    )
                )
                and governance["authoritative_count"]
                == governance["event_count"],
                (
                    f"events={governance['event_count']};"
                    f"authoritative={governance['authoritative_count']}"
                ),
            )
        )

    aggregate = _sum_governance(scenario_rows)
    checks.extend(
        [
            _check(
                "review_governance_has_no_failed_event",
                aggregate["failure_event_count"]
                <= _int(
                    thresholds.get(
                        "review_governance_failure_event_count_max"
                    )
                ),
                (
                    f"failures={aggregate['failure_event_count']};"
                    "maximum="
                    f"{_int(thresholds.get('review_governance_failure_event_count_max'))}"
                ),
            ),
            _check(
                "blocking_review_deprecates_every_targeted_memory",
                aggregate["targeted_without_deprecation_count"]
                <= _int(
                    thresholds.get(
                        "review_governance_targeted_without_deprecation_count_max"
                    )
                ),
                (
                    "missing="
                    f"{aggregate['targeted_without_deprecation_count']}"
                ),
            ),
            _check(
                "review_governance_deprecates_no_unrelated_memory",
                aggregate["unexpected_deprecation_count"]
                <= _int(
                    thresholds.get(
                        "review_governance_unexpected_deprecation_count_max"
                    )
                ),
                (
                    "unexpected="
                    f"{aggregate['unexpected_deprecation_count']}"
                ),
            ),
            _check(
                "blocking_review_conclusion_uses_candidate_admission",
                aggregate[
                    "blocking_without_admitted_blocker_count"
                ]
                <= _int(
                    thresholds.get(
                        "review_governance_blocking_without_admitted_blocker_count_max"
                    )
                ),
                (
                    "missing_admission="
                    f"{aggregate['blocking_without_admitted_blocker_count']}"
                ),
            ),
            _check(
                "positive_review_has_no_lifecycle_side_effect",
                aggregate["nonblocking_side_effect_count"]
                <= _int(
                    thresholds.get(
                        "review_governance_nonblocking_side_effect_count_max"
                    )
                ),
                (
                    "side_effects="
                    f"{aggregate['nonblocking_side_effect_count']}"
                ),
            ),
        ]
    )

    passed = all(item["passed"] for item in checks)
    report.update(
        {
            "schema_version": "agentlite.v514h.formal-regression-report.v1",
            "summary": {
                **dict(report.get("summary") or {}),
                "passed": passed,
                "formal_regression_passed": passed,
                "check_count": len(checks),
                "passed_check_count": sum(
                    item["passed"] for item in checks
                ),
                "review_governance_event_count": aggregate["event_count"],
                "review_governance_blocking_count": aggregate[
                    "blocking_count"
                ],
                "review_governance_failure_event_count": aggregate[
                    "failure_event_count"
                ],
            },
            "frozen_input_evidence": input_hash_rows,
            "legacy_threshold_drift": drifted_thresholds,
            "review_governance": {
                "aggregate": aggregate,
                "scenarios": scenario_rows,
                "blocking_event_is_mandatory": False,
            },
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


def _governance_summary(report: dict[str, Any]) -> dict[str, int]:
    source = report.get("review_governance_summary")
    if not isinstance(source, dict):
        token_summary = report.get("token_summary")
        source = token_summary if isinstance(token_summary, dict) else {}
        prefix = "review_governance_"
        return {
            key: _int(source.get(f"{prefix}{key}"))
            for key in _governance_keys()
        }
    return {
        key: _int(source.get(key))
        for key in _governance_keys()
    }


def _governance_keys() -> tuple[str, ...]:
    return (
        "event_count",
        "authoritative_count",
        "blocking_count",
        "positive_count",
        "targeted_memory_count",
        "deprecated_memory_count",
        "blocker_admitted_count",
        "blocker_memory_count",
        "targeted_without_deprecation_count",
        "unexpected_deprecation_count",
        "blocking_without_admitted_blocker_count",
        "nonblocking_side_effect_count",
        "failure_event_count",
        "safe_event_count",
    )


def _sum_governance(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        key: sum(_int(row.get(key)) for row in rows)
        for key in _governance_keys()
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    governance = dict(report.get("review_governance") or {})
    aggregate = dict(governance.get("aggregate") or {})
    lines = [
        "# v5.14h 正式规模回归验收",
        "",
        f"- 总体通过：`{summary.get('passed')}`",
        (
            f"- 通过项目：`{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        (
            "- 审查治理事件："
            f"`{aggregate.get('event_count', 0)}`，"
            f"阻断事件：`{aggregate.get('blocking_count', 0)}`，"
            f"治理失败：`{aggregate.get('failure_event_count', 0)}`"
        ),
        "",
        "## 场景证据",
        "",
        "| 场景 | 治理事件 | 阻断 | 定向记忆 | 软废弃 | 阻断准入 | 失败 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in governance.get("scenarios", []):
        lines.append(
            f"| {row['scenario_id']} | {row['event_count']} | "
            f"{row['blocking_count']} | {row['targeted_memory_count']} | "
            f"{row['deprecated_memory_count']} | "
            f"{row['blocker_admitted_count']} | "
            f"{row['failure_event_count']} |"
        )
    lines.extend(["", "## 失败项目", ""])
    failed = [item for item in report["checks"] if not item["passed"]]
    if failed:
        lines.extend(
            f"- `{item['name']}`：{item['detail']}"
            for item in failed
        )
    else:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- A/B 任务、Agent 配置、模型、温度、轮次和 v5.14f 原有阈值保持不变。",
            "- 阻断事件不是强制数量门禁：若 Provider 本次直接生成合格结果，可以没有阻断。",
            "- 一旦发生阻断，定向软废弃、候选准入和无旁路副作用均为强制门禁。",
            "- 本次仍是单次正式回归，不用于估计跨重复实验方差或置信区间。",
            "",
        ]
    )
    return "\n".join(lines)


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
    return {"name": name, "passed": bool(passed), "detail": detail}


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
