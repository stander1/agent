from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_v514e_verifier() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "v5.14e-current-task-fidelity-acceptance"
        / "verify_acceptance.py"
    )
    spec = importlib.util.spec_from_file_location("v514e_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14e acceptance verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514E_VERIFY = _load_v514e_verifier()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the v5.14f full A1-A10/B1-B10 formal run."
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
    report = V514E_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = list(report.get("checks") or [])
    preregistration = dict(
        preflight_report.get("preregistration") or {}
    )
    thresholds = dict(preregistration.get("thresholds") or {})
    scenario_specs = list(preregistration.get("scenarios") or [])
    expected_tasks = _int(preregistration.get("tasks_per_scenario"))
    expected_group_task_count = expected_tasks * len(scenario_specs)
    expected_task_rows = expected_group_task_count * 3
    preflight_summary = dict(preflight_report.get("summary") or {})
    aggregates = dict(preflight_report.get("aggregates") or {})
    memory = dict(aggregates.get("memory") or {})
    state = dict(aggregates.get("state") or {})
    scenario_evidence = {
        str(item.get("scenario_id") or ""): dict(item)
        for item in preflight_report.get("scenarios", [])
        if isinstance(item, dict)
    }

    checks.extend(
        [
            _check(
                "formal_preregistration_is_frozen",
                bool(preregistration.get("frozen_before_provider_run"))
                and preregistration.get("experiment_kind")
                == "formal_scale_single_run",
                (
                    f"frozen={preregistration.get('frozen_before_provider_run')};"
                    f"kind={preregistration.get('experiment_kind')}"
                ),
            ),
            _check(
                "formal_task_matrix_is_complete",
                expected_tasks == 10
                and _int(preflight_summary.get("task_count_per_group"))
                == expected_group_task_count
                and len(report.get("tasks") or []) == expected_task_rows,
                (
                    f"tasks_per_scenario={expected_tasks};"
                    f"group_tasks={preflight_summary.get('task_count_per_group')};"
                    f"task_rows={len(report.get('tasks') or [])}"
                ),
            ),
        ]
    )

    terminal_rows: list[dict[str, Any]] = []
    for spec in scenario_specs:
        directory = str(spec.get("directory") or "")
        scenario_id = str(spec.get("scenario_id") or directory)
        terminal_task_id = str(spec.get("terminal_task_id") or "")
        managed_sequence = _read_json(
            run_root / directory / "managed" / "sequence_result.json"
        )
        managed_task = _find_task(
            list(managed_sequence.get("tasks") or []),
            terminal_task_id,
        )
        quality = _read_json(
            run_root
            / directory
            / "comparison"
            / "quality_blind_summary.json"
        )
        quality_task = _find_task(
            list(quality.get("tasks") or []),
            terminal_task_id,
        )
        scores = dict(quality_task.get("scores") or {})
        delivery = dict(quality_task.get("delivery_complete") or {})
        findings = dict(quality_task.get("technical_findings") or {})
        scenario_memory = dict(
            scenario_evidence.get(scenario_id, {}).get("memory") or {}
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

        terminal_rows.append(
            {
                "scenario_id": scenario_id,
                "directory": directory,
                "task_id": terminal_task_id,
                "managed_delivery_valid": bool(
                    managed_task.get("delivery_valid")
                ),
                "managed_blind_delivery_complete": bool(
                    delivery.get("managed")
                ),
                "managed_score": managed_score,
                "native_score": native_score,
                "managed_blocking_finding_count": managed_blocking,
                "final_answer_chars": len(
                    str(managed_task.get("final_answer") or "")
                ),
            }
        )
        checks.extend(
            [
                _check(
                    f"{scenario_id}:{terminal_task_id}:"
                    "managed_terminal_delivery_complete",
                    bool(managed_task)
                    and bool(managed_task.get("delivery_valid"))
                    and bool(delivery.get("managed"))
                    and bool(managed_task.get("final_answer")),
                    (
                        f"runtime_valid={managed_task.get('delivery_valid')};"
                        f"blind_complete={delivery.get('managed')};"
                        f"chars={len(str(managed_task.get('final_answer') or ''))}"
                    ),
                ),
                _check(
                    f"{scenario_id}:{terminal_task_id}:"
                    "managed_terminal_quality_is_acceptable",
                    managed_score
                    >= _float(
                        thresholds.get("managed_terminal_score_min")
                    )
                    and managed_score
                    >= native_score
                    - _float(
                        thresholds.get(
                            "managed_terminal_score_noninferiority_margin"
                        )
                    ),
                    (
                        f"managed={managed_score:.4f};native={native_score:.4f};"
                        f"minimum={_float(thresholds.get('managed_terminal_score_min')):.4f};"
                        "margin="
                        f"{_float(thresholds.get('managed_terminal_score_noninferiority_margin')):.4f}"
                    ),
                ),
                _check(
                    f"{scenario_id}:{terminal_task_id}:"
                    "managed_terminal_has_no_blocking_finding",
                    managed_blocking
                    <= _int(
                        thresholds.get(
                            "managed_terminal_blocking_finding_count_max"
                        )
                    ),
                    (
                        f"blocking={managed_blocking};"
                        "maximum="
                        f"{_int(thresholds.get('managed_terminal_blocking_finding_count_max'))}"
                    ),
                ),
                _check(
                    f"{scenario_id}:managed_memory_reuse_is_real",
                    _int(scenario_memory.get("hit_count"))
                    >= _int(thresholds.get("memory_hit_count_min"))
                    and _int(scenario_memory.get("injected_count"))
                    >= _int(
                        thresholds.get("memory_injected_count_min")
                    )
                    and _int(scenario_memory.get("useful_hit_count"))
                    >= _int(
                        thresholds.get("useful_memory_hit_count_min")
                    ),
                    (
                        f"hits={_int(scenario_memory.get('hit_count'))};"
                        f"injected={_int(scenario_memory.get('injected_count'))};"
                        f"useful={_int(scenario_memory.get('useful_hit_count'))}"
                    ),
                ),
            ]
        )

    checks.extend(
        [
            _check(
                "formal_memory_reuse_is_real",
                _int(memory.get("hit_count"))
                >= _int(thresholds.get("memory_hit_count_min"))
                and _int(memory.get("injected_count"))
                >= _int(thresholds.get("memory_injected_count_min"))
                and _int(memory.get("useful_hit_count"))
                >= _int(
                    thresholds.get("useful_memory_hit_count_min")
                ),
                (
                    f"hits={_int(memory.get('hit_count'))};"
                    f"injected={_int(memory.get('injected_count'))};"
                    f"useful={_int(memory.get('useful_hit_count'))}"
                ),
            ),
            _check(
                "formal_memory_has_no_wrong_hit",
                _int(memory.get("wrong_hit_count"))
                <= _int(thresholds.get("wrong_memory_hit_count_max")),
                (
                    f"wrong={_int(memory.get('wrong_hit_count'))};"
                    f"maximum={_int(thresholds.get('wrong_memory_hit_count_max'))}"
                ),
            ),
            _check(
                "formal_current_task_fidelity_is_preserved",
                _int(
                    report.get("summary", {}).get(
                        "current_task_fidelity_failure_count"
                    )
                )
                <= _int(
                    thresholds.get(
                        "current_task_fidelity_failure_count_max"
                    )
                ),
                (
                    "failures="
                    f"{_int(report.get('summary', {}).get('current_task_fidelity_failure_count'))}"
                ),
            ),
            _check(
                "formal_protocol_and_routing_hygiene_hold",
                _int(
                    report.get("summary", {}).get(
                        "model_visible_protocol_marker_count"
                    )
                )
                <= _int(
                    thresholds.get(
                        "model_visible_protocol_marker_count_max"
                    )
                )
                and _int(
                    report.get("summary", {}).get(
                        "routing_metadata_state_count"
                    )
                )
                <= _int(
                    thresholds.get("routing_metadata_state_count_max")
                ),
                (
                    "protocol="
                    f"{_int(report.get('summary', {}).get('model_visible_protocol_marker_count'))};"
                    "routing="
                    f"{_int(report.get('summary', {}).get('routing_metadata_state_count'))}"
                ),
            ),
        ]
    )

    requested_state_types = [
        str(value)
        for value in preregistration.get(
            "diagnostic_requested_state_types", []
        )
    ]
    actual_state_types = dict(state.get("state_type_counts") or {})
    missing_requested_state_types = [
        state_type
        for state_type in requested_state_types
        if _int(actual_state_types.get(state_type)) == 0
    ]
    resume_history_path = (
        run_root / "system" / "resume-history.txt"
    )
    resume_history = (
        resume_history_path.read_text(encoding="utf-8")
        if resume_history_path.is_file()
        else ""
    )
    resume_count = sum(
        line.startswith("resume_at=")
        for line in resume_history.splitlines()
    )

    passed = all(item["passed"] for item in checks)
    report.update(
        {
            "schema_version": "agentlite.v514f.formal-report.v1",
            "summary": {
                **dict(report.get("summary") or {}),
                "passed": passed,
                "formal_scale_passed": passed,
                "check_count": len(checks),
                "passed_check_count": sum(
                    item["passed"] for item in checks
                ),
                "scenario_count": len(scenario_specs),
                "task_count_per_group": expected_group_task_count,
                "terminal_task_count": len(terminal_rows),
                "resume_count": resume_count,
            },
            "terminal_tasks": terminal_rows,
            "resume_evidence": {
                "resume_count": resume_count,
                "history_path": str(resume_history_path),
                "history": resume_history,
            },
            "state_type_diagnostics": {
                "requested_by_benchmark": requested_state_types,
                "observed_in_state_pool": actual_state_types,
                "missing_requested_types": missing_requested_state_types,
                "is_acceptance_gate": False,
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


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.14f A1-A10/B1-B10 正式规模验收",
        "",
        f"- 总体通过：`{summary['passed']}`",
        (
            f"- 通过项目：`{summary['passed_check_count']}/"
            f"{summary['check_count']}`"
        ),
        f"- 每组任务：`{summary['task_count_per_group']}`",
        f"- 终局任务：`{summary['terminal_task_count']}`",
        f"- Provider 中断续跑次数：`{summary['resume_count']}`",
        "",
        "## 终局交付",
        "",
        "| 场景 | 任务 | Managed 分数 | Native 分数 | 完整交付 | 阻断问题 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in report["terminal_tasks"]:
        complete = (
            row["managed_delivery_valid"]
            and row["managed_blind_delivery_complete"]
        )
        lines.append(
            f"| {row['scenario_id']} | {row['task_id']} | "
            f"{row['managed_score']:.3f} | {row['native_score']:.3f} | "
            f"{complete} | {row['managed_blocking_finding_count']} |"
        )

    diagnostics = report["state_type_diagnostics"]
    lines.extend(
        [
            "",
            "## 状态类型诊断",
            "",
            (
                "- StatePool 实际类型："
                f"`{diagnostics['observed_in_state_pool']}`"
            ),
            (
                "- 基准任务要求但未实际生成的类型："
                f"`{diagnostics['missing_requested_types']}`"
            ),
            "- 该诊断不作为本阶段成本-质量正式规模门禁，不据任务文本推断实现状态。",
            "",
            "## 失败项目",
            "",
        ]
    )
    failed = [item for item in report["checks"] if not item["passed"]]
    if not failed:
        lines.append("- 无")
    else:
        for item in failed:
            lines.append(f"- `{item['name']}`：{item['detail']}")
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 本报告覆盖一次完整规模运行，不提供跨重复实验方差或置信区间。",
            "- Provider 与协作 Token、双盲质量、交付完整性仍由基础预注册门禁约束。",
            "- 状态类型只读取真实 StatePool；缺少的类型必须在后续工程阶段实现并单独验收。",
            "",
        ]
    )
    return "\n".join(lines)


def _find_task(
    tasks: list[dict[str, Any]],
    task_id: str,
) -> dict[str, Any]:
    for item in tasks:
        if str(item.get("task_id") or "") == task_id:
            return dict(item)
    return {}


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
