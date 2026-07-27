from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
V514P_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14p-compact-approval-formal-regression"
)
V514Q_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14q-candidate-evidence-task-identity"
)


def _load_verifier(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load acceptance verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514P_VERIFY = _load_verifier(
    "v514p_verify",
    V514P_DIR / "verify_acceptance.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the v5.14r Provider regression after current-candidate "
            "evidence and current-task identity protection."
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
    report = V514P_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = [
        item
        for item in report.get("checks", [])
        if str(item.get("name") or "")
        != "formal_run_descends_from_v514o_release"
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
            "formal_run_descends_from_v514q_release",
            bool(mechanism_commit)
            and bool(run_commit)
            and _is_ancestor(mechanism_commit, run_commit),
            f"mechanism={mechanism_commit};run={run_commit}",
        )
    )
    checks.append(
        _hash_check(
            "v514p_preregistration_is_bound",
            str(lineage.get("v514p_preregistration_sha256") or ""),
            V514P_DIR / "preregistration.json",
        )
    )
    checks.append(
        _hash_check(
            "v514q_acceptance_verifier_is_bound",
            str(lineage.get("v514q_acceptance_verifier_sha256") or ""),
            V514Q_DIR / "verify_acceptance.py",
        )
    )

    scenario_rows: list[dict[str, Any]] = []
    totals = {
        "current_candidate_required_count": 0,
        "current_candidate_available_count": 0,
        "current_candidate_complete_count": 0,
        "current_candidate_missing_count": 0,
        "current_candidate_source_tokens": 0,
        "current_candidate_selected_tokens": 0,
        "current_task_identity_anchored_count": 0,
        "current_task_identity_guard_blocked_count": 0,
    }

    for spec in preregistration.get("scenarios", []):
        scenario_id = str(spec.get("scenario_id") or "")
        directory = str(spec.get("directory") or "")
        metrics = _metric_map(
            _read_json(
                run_root
                / directory
                / "reports"
                / "managed-agentlite.json"
            )
        )
        row = {
            "scenario_id": scenario_id,
            "directory": directory,
            **{
                key: _int(metrics.get(key))
                for key in totals
            },
        }
        scenario_rows.append(row)
        for key in totals:
            totals[key] += _int(row[key])
        checks.extend(
            _scenario_checks(
                row=row,
                thresholds=thresholds,
            )
        )

    passed = all(bool(item.get("passed")) for item in checks)
    report.update(
        {
            "schema_version": (
                "agentlite.v514r.candidate-evidence-formal-report.v1"
            ),
            "summary": {
                **dict(report.get("summary") or {}),
                "passed": passed,
                "formal_regression_passed": passed,
                "candidate_evidence_formal_passed": passed,
                "check_count": len(checks),
                "passed_check_count": sum(
                    bool(item.get("passed")) for item in checks
                ),
                **totals,
            },
            "checks": checks,
            "candidate_evidence": {
                "scenarios": scenario_rows,
                **totals,
            },
        }
    )
    return report


def _scenario_checks(
    *,
    row: dict[str, Any],
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    scenario_id = str(row["scenario_id"])
    required = _int(row["current_candidate_required_count"])
    available = _int(row["current_candidate_available_count"])
    complete = _int(row["current_candidate_complete_count"])
    missing = _int(row["current_candidate_missing_count"])
    source_tokens = _int(row["current_candidate_source_tokens"])
    selected_tokens = _int(row["current_candidate_selected_tokens"])
    identity_anchors = _int(
        row["current_task_identity_anchored_count"]
    )
    return [
        _check(
            f"{scenario_id}:validation_candidate_requirement_observed",
            required
            >= _int(
                thresholds.get(
                    "current_candidate_required_count_min_per_scenario"
                )
            ),
            f"required={required}",
        ),
        _check(
            f"{scenario_id}:required_candidate_is_available",
            available == required
            and missing
            <= _int(
                thresholds.get("current_candidate_missing_count_max")
            ),
            (
                f"required={required};available={available};"
                f"missing={missing}"
            ),
        ),
        _check(
            f"{scenario_id}:complete_candidate_rewrite_observed",
            complete
            >= _int(
                thresholds.get(
                    "current_candidate_complete_count_min_per_scenario"
                )
            ),
            f"complete={complete}",
        ),
        _check(
            f"{scenario_id}:candidate_token_accounting_is_consistent",
            selected_tokens
            >= _int(
                thresholds.get(
                    "current_candidate_selected_tokens_min_per_scenario"
                )
            )
            and source_tokens >= selected_tokens,
            f"source={source_tokens};selected={selected_tokens}",
        ),
        _check(
            f"{scenario_id}:current_task_identity_anchor_observed",
            identity_anchors
            >= _int(
                thresholds.get(
                    "current_task_identity_anchored_count_min_per_scenario"
                )
            ),
            f"identity_anchors={identity_anchors}",
        ),
    ]


def _metric_map(report: dict[str, Any]) -> dict[str, Any]:
    return {
        str(row.get("metric") or ""): row.get("value")
        for row in report.get("metric_rows", [])
        if isinstance(row, dict) and str(row.get("metric") or "")
    }


def _hash_check(name: str, expected: str, path: Path) -> dict[str, Any]:
    expected = str(expected or "").lower()
    actual = _sha256(path)
    return _check(
        name,
        bool(expected) and expected == actual,
        f"expected={expected};actual={actual}",
    )


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


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


def _markdown(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    evidence = dict(report.get("candidate_evidence") or {})
    lines = [
        "# v5.14r 候选充分证据正式回归",
        "",
        f"- 总体通过：`{summary.get('passed')}`",
        (
            f"- 通过项目：`{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        (
            "- 候选可用/必需："
            f"`{summary.get('current_candidate_available_count', 0)}/"
            f"{summary.get('current_candidate_required_count', 0)}`"
        ),
        (
            "- 完整候选改写："
            f"`{summary.get('current_candidate_complete_count', 0)}`"
        ),
        (
            "- 候选缺失："
            f"`{summary.get('current_candidate_missing_count', 0)}`"
        ),
        (
            "- 任务身份锚点："
            f"`{summary.get('current_task_identity_anchored_count', 0)}`"
        ),
        (
            "- 身份漂移拦截："
            f"`{summary.get('current_task_identity_guard_blocked_count', 0)}`"
        ),
        "",
        "## 场景证据",
        "",
        "| 场景 | 必需 | 可用 | 完整改写 | 缺失 | 来源 Token | 选中 Token | 身份锚点 | 拦截 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in evidence.get("scenarios", []):
        lines.append(
            f"| {row['scenario_id']} | "
            f"{row['current_candidate_required_count']} | "
            f"{row['current_candidate_available_count']} | "
            f"{row['current_candidate_complete_count']} | "
            f"{row['current_candidate_missing_count']} | "
            f"{row['current_candidate_source_tokens']} | "
            f"{row['current_candidate_selected_tokens']} | "
            f"{row['current_task_identity_anchored_count']} | "
            f"{row['current_task_identity_guard_blocked_count']} |"
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
            "- 三组任务、Agent 配置、模型、温度、轮次和评分器完全一致。",
            "- 完整保留只面向按能力画像识别出的验证类接收者，不恢复全文广播。",
            "- 身份守卫只在标签族和协作序号能唯一推导时生效，歧义输入保持透传。",
            "- 成本、质量、交付和协议门禁未放宽。",
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
    output_markdown.write_text(_markdown(report), encoding="utf-8")


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
