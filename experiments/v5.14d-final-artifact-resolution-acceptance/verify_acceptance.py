from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agent_runtime.reliability.final_delivery_guard import (
    assess_final_delivery,
    is_prior_artifact_approval,
)


GROUPS = ("native", "observed", "managed")
SCENARIOS = ("A", "B")
RESOLUTION_KINDS = {
    "reviewer_artifact",
    "reviewer_marker_repaired",
    "prior_artifact_approved",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify v5.14d final artifact and routing hygiene."
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
    checks: list[dict[str, Any]] = []
    resolution_counts: Counter[str] = Counter()
    task_rows: list[dict[str, Any]] = []
    routing_state_count = 0
    state_reuse_count = 0
    protocol_marker_count = 0

    checks.append(
        _check(
            "base_preflight_passed",
            bool(preflight_report.get("summary", {}).get("passed")),
            (
                "base_passed="
                f"{preflight_report.get('summary', {}).get('passed')}"
            ),
        )
    )

    for scenario in SCENARIOS:
        scenario_dir = run_root / scenario
        for group in GROUPS:
            sequence_path = scenario_dir / group / "sequence_result.json"
            sequence = _read_json(sequence_path)
            tasks = list(sequence.get("tasks") or [])
            checks.append(
                _check(
                    f"{scenario}:{group}:sequence_result_present",
                    bool(tasks),
                    f"path={sequence_path};tasks={len(tasks)}",
                )
            )
            grounding_contexts: list[str] = []
            for task in tasks:
                task_id = str(task.get("task_id") or "")
                question = str(task.get("question") or "")
                answer = str(task.get("final_answer") or "")
                valid = bool(task.get("delivery_valid"))
                resolution_kind = str(
                    task.get("final_resolution_kind") or ""
                )
                origin_source = str(
                    task.get("final_artifact_origin_source") or ""
                )
                resolution_counts[resolution_kind] += 1

                assessment = assess_final_delivery(
                    request=question,
                    content=answer,
                    grounding_contexts=tuple(
                        [*grounding_contexts, question]
                    ),
                )
                numeric_valid = (
                    "numeric_upper_bound_violation"
                    not in assessment.reasons
                )
                approval_only = is_prior_artifact_approval(answer)
                resolution_valid = (
                    not valid
                    or resolution_kind in RESOLUTION_KINDS
                )
                lineage_valid = (
                    resolution_kind != "prior_artifact_approved"
                    or (
                        bool(origin_source)
                        and origin_source != "reviewer"
                    )
                )

                task_rows.append(
                    {
                        "scenario": scenario,
                        "group": group,
                        "task_id": task_id,
                        "delivery_valid": valid,
                        "resolution_kind": resolution_kind,
                        "origin_source": origin_source,
                        "approval_only_final": approval_only,
                        "numeric_upper_bound_valid": numeric_valid,
                        "final_answer_chars": len(answer),
                    }
                )
                checks.extend(
                    [
                        _check(
                            f"{scenario}:{group}:{task_id}:"
                            "resolved_delivery_has_lineage",
                            resolution_valid and lineage_valid,
                            (
                                f"delivery_valid={valid};"
                                f"resolution={resolution_kind};"
                                f"origin={origin_source}"
                            ),
                        ),
                        _check(
                            f"{scenario}:{group}:{task_id}:"
                            "approval_note_is_not_final_body",
                            not valid or not approval_only,
                            (
                                f"delivery_valid={valid};"
                                f"approval_only={approval_only};"
                                f"chars={len(answer)}"
                            ),
                        ),
                        _check(
                            f"{scenario}:{group}:{task_id}:"
                            "numeric_upper_bound_is_respected",
                            not valid or numeric_valid,
                            (
                                f"delivery_valid={valid};"
                                f"reasons={list(assessment.reasons)}"
                            ),
                        ),
                    ]
                )
                grounding_contexts.append(question)

        report_path = scenario_dir / "reports" / "managed-agentlite.json"
        managed_report = _read_json(report_path)
        state_summary = dict(managed_report.get("state_summary") or {})
        token_summary = dict(managed_report.get("token_summary") or {})
        metric_rows = {
            str(item.get("metric") or ""): item.get("value")
            for item in managed_report.get("metric_rows", [])
            if isinstance(item, dict)
        }
        scenario_routing = _int(
            state_summary.get("routing_metadata_state_count")
        )
        scenario_reuse = _int(
            state_summary.get("state_dedup_reuse_count")
        )
        scenario_protocol = _int(
            token_summary.get(
                "model_visible_protocol_marker_count",
                metric_rows.get(
                    "agentlite_model_visible_protocol_marker_count",
                    0,
                ),
            )
        )
        routing_state_count += scenario_routing
        state_reuse_count += scenario_reuse
        protocol_marker_count += scenario_protocol
        checks.extend(
            [
                _check(
                    f"{scenario}:managed:routing_metadata_not_in_state_pool",
                    scenario_routing == 0,
                    f"routing_metadata_state_count={scenario_routing}",
                ),
                _check(
                    f"{scenario}:managed:protocol_not_visible_to_model",
                    scenario_protocol == 0,
                    f"model_visible_protocol_marker_count={scenario_protocol}",
                ),
            ]
        )

    checks.append(
        _check(
            "state_reuse_is_reported",
            state_reuse_count > 0,
            f"state_dedup_reuse_count={state_reuse_count}",
        )
    )
    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "agentlite.v514d.acceptance-report.v1",
        "summary": {
            "passed": passed,
            "check_count": len(checks),
            "passed_check_count": sum(item["passed"] for item in checks),
            "task_count": len(task_rows),
            "routing_metadata_state_count": routing_state_count,
            "state_dedup_reuse_count": state_reuse_count,
            "model_visible_protocol_marker_count": protocol_marker_count,
        },
        "resolution_kind_counts": dict(sorted(resolution_counts.items())),
        "tasks": task_rows,
        "checks": checks,
    }


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
        "# v5.14d 最终成果与路由卫生验收",
        "",
        f"- 总体通过：`{summary['passed']}`",
        (
            f"- 通过项目：`{summary['passed_check_count']}/"
            f"{summary['check_count']}`"
        ),
        f"- 任务记录：`{summary['task_count']}`",
        (
            "- 路由元数据状态："
            f"`{summary['routing_metadata_state_count']}`"
        ),
        f"- 状态复用次数：`{summary['state_dedup_reuse_count']}`",
        (
            "- 模型可见协议标记："
            f"`{summary['model_visible_protocol_marker_count']}`"
        ),
        "",
        "## 最终成果解析方式",
        "",
    ]
    for key, value in report["resolution_kind_counts"].items():
        lines.append(f"- `{key or 'missing'}`：`{value}`")
    lines.extend(["", "## 失败项目", ""])
    failed = [item for item in report["checks"] if not item["passed"]]
    if not failed:
        lines.append("- 无")
    else:
        for item in failed:
            lines.append(f"- `{item['name']}`：{item['detail']}")
    return "\n".join(lines) + "\n"


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "detail": detail,
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    args = parse_args()
    preflight_report = _read_json(args.preflight_report)
    report = evaluate(
        run_root=args.run_root,
        preflight_report=preflight_report,
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
