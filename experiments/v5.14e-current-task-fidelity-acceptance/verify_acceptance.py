from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any


SCENARIOS = ("A", "B")


def _load_v514d_verifier() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "v5.14d-final-artifact-resolution-acceptance"
        / "verify_acceptance.py"
    )
    spec = importlib.util.spec_from_file_location("v514d_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.14d acceptance verifier")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514D_VERIFY = _load_v514d_verifier()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify current-task fidelity, final-artifact promotion, and "
            "memory no-hit semantics for v5.14e."
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
    report = V514D_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = list(report.get("checks") or [])
    task_rows = list(report.get("tasks") or [])

    team_rewrite_event_count = 0
    receiver_plan_count = 0
    current_task_fidelity_failure_count = 0
    report_fidelity_failure_count = 0
    agent_fidelity_audit_count = 0

    for scenario in SCENARIOS:
        managed_root = run_root / scenario / "managed"
        events = _read_trace_events(managed_root)
        team_events = [
            event
            for event in events
            if event.get("event_type") == "autogen_team_input_real_rewrite"
        ]
        team_rewrite_event_count += len(team_events)

        scenario_plans: list[dict[str, Any]] = []
        trace_schema_complete = bool(team_events)
        scenario_trace_failures = 0
        for event in team_events:
            payload = dict(event.get("payload") or {})
            if "current_task_fidelity_failure_count" not in payload:
                trace_schema_complete = False
            scenario_trace_failures += _int(
                payload.get("current_task_fidelity_failure_count")
            )
            plans = [
                dict(item)
                for item in payload.get("receiver_plans", [])
                if isinstance(item, dict)
            ]
            if not plans:
                trace_schema_complete = False
            scenario_plans.extend(plans)

        scenario_plan_failures = sum(
            int(not bool(plan.get("current_task_units_preserved")))
            for plan in scenario_plans
        )
        receiver_plan_count += len(scenario_plans)
        current_task_fidelity_failure_count += (
            scenario_trace_failures + scenario_plan_failures
        )

        audited_agent_events = []
        for event in events:
            if event.get("event_type") != "autogen_agent_input_real_rewrite":
                continue
            payload = dict(event.get("payload") or {})
            if not bool(payload.get("rewrite_applied")):
                continue
            safety = dict(payload.get("rewrite_safety") or {})
            if "current_task_units_preserved" in safety:
                audited_agent_events.append(safety)
        agent_fidelity_audit_count += len(audited_agent_events)
        scenario_agent_failures = sum(
            int(not bool(safety.get("current_task_units_preserved")))
            for safety in audited_agent_events
        )
        current_task_fidelity_failure_count += scenario_agent_failures

        managed_report = _read_json(
            run_root / scenario / "reports" / "managed-agentlite.json"
        )
        token_summary = dict(managed_report.get("token_summary") or {})
        metric_rows = {
            str(item.get("metric") or ""): item.get("value")
            for item in managed_report.get("metric_rows", [])
            if isinstance(item, dict)
        }
        report_metric_present = (
            "current_task_fidelity_failure_count" in token_summary
            or "current_task_fidelity_failure_count" in metric_rows
        )
        scenario_report_failures = _int(
            token_summary.get(
                "current_task_fidelity_failure_count",
                metric_rows.get("current_task_fidelity_failure_count", 0),
            )
        )
        report_fidelity_failure_count += scenario_report_failures

        memory_query_count = _int(token_summary.get("memory_query_count"))
        memory_hit_count = _int(token_summary.get("memory_hit_count"))
        memory_injected_count = _int(
            token_summary.get("memory_injected_count")
        )
        memory_path_valid = (
            memory_query_count > 0
            and (
                (
                    memory_hit_count == 0
                    and memory_injected_count == 0
                )
                or (
                    memory_hit_count > 0
                    and memory_injected_count > 0
                )
            )
        )

        checks.extend(
            [
                _check(
                    f"{scenario}:managed:current_task_fidelity_trace_present",
                    trace_schema_complete,
                    (
                        f"team_rewrites={len(team_events)};"
                        f"receiver_plans={len(scenario_plans)}"
                    ),
                ),
                _check(
                    f"{scenario}:managed:current_task_units_preserved",
                    scenario_trace_failures == 0
                    and scenario_plan_failures == 0
                    and scenario_agent_failures == 0,
                    (
                        f"team_failures={scenario_trace_failures};"
                        f"receiver_failures={scenario_plan_failures};"
                        f"agent_failures={scenario_agent_failures}"
                    ),
                ),
                _check(
                    f"{scenario}:managed:fidelity_metric_reported",
                    report_metric_present
                    and scenario_report_failures == 0,
                    (
                        f"present={report_metric_present};"
                        f"failures={scenario_report_failures}"
                    ),
                ),
                _check(
                    f"{scenario}:managed:memory_no_hit_semantics_valid",
                    memory_path_valid,
                    (
                        f"queries={memory_query_count};"
                        f"hits={memory_hit_count};"
                        f"injected={memory_injected_count}"
                    ),
                ),
            ]
        )

    passed = all(item["passed"] for item in checks)
    report.update(
        {
            "schema_version": "agentlite.v514e.acceptance-report.v1",
            "summary": {
                **dict(report.get("summary") or {}),
                "passed": passed,
                "check_count": len(checks),
                "passed_check_count": sum(
                    item["passed"] for item in checks
                ),
                "team_rewrite_event_count": team_rewrite_event_count,
                "receiver_plan_count": receiver_plan_count,
                "current_task_fidelity_failure_count": (
                    current_task_fidelity_failure_count
                ),
                "reported_fidelity_failure_count": (
                    report_fidelity_failure_count
                ),
                "agent_fidelity_audit_count": agent_fidelity_audit_count,
            },
            "tasks": task_rows,
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
        "# v5.14e 当前任务保真与最终成果验收",
        "",
        f"- 总体通过：`{summary['passed']}`",
        (
            f"- 通过项目：`{summary['passed_check_count']}/"
            f"{summary['check_count']}`"
        ),
        f"- Team 改写事件：`{summary['team_rewrite_event_count']}`",
        f"- 接收者视图：`{summary['receiver_plan_count']}`",
        (
            "- 当前任务保真失败："
            f"`{summary['current_task_fidelity_failure_count']}`"
        ),
        (
            "- 报告中的保真失败："
            f"`{summary['reported_fidelity_failure_count']}`"
        ),
        "",
        "## 失败项目",
        "",
    ]
    failed = [item for item in report["checks"] if not item["passed"]]
    if not failed:
        lines.append("- 无")
    else:
        for item in failed:
            lines.append(f"- `{item['name']}`：{item['detail']}")
    return "\n".join(lines) + "\n"


def _read_trace_events(managed_root: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in managed_root.glob(
        "agentlite_data/sessions/*/autogen_driver/trace.jsonl"
    ):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                events.append(item)
    return events


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "detail": detail,
    }


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
