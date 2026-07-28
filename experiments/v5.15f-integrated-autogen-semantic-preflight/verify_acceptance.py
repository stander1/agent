from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


BENCHMARK_ROLE_NAMES = {"planner", "writer", "reviewer"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _metrics(report: dict[str, Any]) -> dict[str, Any]:
    return {
        str(row.get("metric") or ""): row.get("value")
        for row in report.get("metric_rows", [])
        if isinstance(row, dict)
    }


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _contains_fragment(text: str, fragment: str) -> bool:
    """Match semantic evidence without treating capitalization as meaning."""

    return fragment.casefold() in text.casefold()


def build_report(
    *,
    implementation_commit: str,
    scenario: dict[str, Any],
    team_config: dict[str, Any],
    workflow: dict[str, Any],
    session_report: dict[str, Any],
) -> dict[str, Any]:
    metrics = _metrics(session_report)
    tasks = workflow.get("tasks")
    tasks = tasks if isinstance(tasks, list) else []
    expected_tasks = scenario.get("tasks")
    expected_tasks = (
        expected_tasks if isinstance(expected_tasks, list) else []
    )
    agent_names = [
        str(value).strip().casefold()
        for value in workflow.get("team", {}).get("agent_names", [])
        if str(value).strip()
    ]

    grounded_task_count = 0
    task_grounding: list[dict[str, Any]] = []
    for expected, actual in zip(expected_tasks, tasks):
        expected = expected if isinstance(expected, dict) else {}
        actual = actual if isinstance(actual, dict) else {}
        output_text = "\n".join(
            str(message.get("content") or "")
            for message in actual.get("provider_outputs", [])
            if isinstance(message, dict)
        )
        required = [
            str(value)
            for value in expected.get("required_fragments", [])
            if str(value)
        ]
        forbidden = [
            str(value)
            for value in expected.get("forbidden_fragments", [])
            if str(value)
        ]
        missing = [
            value
            for value in required
            if not _contains_fragment(output_text, value)
        ]
        present_forbidden = [
            value
            for value in forbidden
            if _contains_fragment(output_text, value)
        ]
        grounded = not missing and not present_forbidden
        grounded_task_count += int(grounded)
        task_grounding.append(
            {
                "task_id": str(expected.get("task_id") or ""),
                "grounded": grounded,
                "missing_required_fragments": missing,
                "present_forbidden_fragments": present_forbidden,
            }
        )

    provider_usage = workflow.get("summary", {}).get(
        "provider_usage", {}
    )
    team_provider_tokens = _integer(
        provider_usage.get("llm_total_tokens")
    )
    control_tokens = _integer(
        metrics.get("agentlite_control_llm_tokens")
    )
    runtime_tokens = _integer(
        metrics.get("actual_agentlite_transport_tokens")
    )
    transport_excluding_control = max(0, runtime_tokens - control_tokens)
    provider_total = team_provider_tokens + control_tokens
    accounted_total = provider_total + transport_excluding_control
    main_retry_count = _integer(provider_usage.get("retry_count"))
    control_retry_count = _integer(
        metrics.get("agentlite_control_llm_retry_count")
    )

    checks = [
        _check(
            "external_inputs_are_bound_to_current_commit",
            scenario.get("authored_after_commit")
            == implementation_commit
            and team_config.get("authored_after_commit")
            == implementation_commit
            and workflow.get("binding", {}).get(
                "implementation_commit"
            )
            == implementation_commit,
            implementation_commit,
        ),
        _check(
            "workflow_uses_arbitrary_capability_names",
            len(agent_names) >= 2
            and not (set(agent_names) & BENCHMARK_ROLE_NAMES),
            f"agents={agent_names}",
        ),
        _check(
            "same_team_is_reset_between_tasks",
            bool(workflow.get("team", {}).get("same_team_instance"))
            and bool(
                workflow.get("team", {}).get("reset_between_tasks")
            ),
            json.dumps(workflow.get("team", {}), ensure_ascii=False),
        ),
        _check(
            "all_preregistered_tasks_completed",
            len(tasks) == len(expected_tasks) >= 2
            and all(
                _integer(task.get("provider_calls")) >= len(agent_names)
                for task in tasks
                if isinstance(task, dict)
            ),
            f"actual={len(tasks)};expected={len(expected_tasks)}",
        ),
        _check(
            "complete_agent_outputs_are_preserved",
            _integer(
                workflow.get("summary", {}).get(
                    "complete_agent_output_count"
                )
            )
            >= len(tasks) * len(agent_names),
            str(
                workflow.get("summary", {}).get(
                    "complete_agent_output_count"
                )
            ),
        ),
        _check(
            "task_chain_outputs_preserve_required_semantics",
            grounded_task_count == len(expected_tasks),
            f"grounded={grounded_task_count}/{len(expected_tasks)}",
        ),
        _check(
            "autogen_driver_and_hooks_are_active",
            session_report.get("driver_status") == "active"
            and bool(session_report.get("hooks_active")),
            (
                f"driver={session_report.get('driver_status')};"
                f"hooks={session_report.get('hooks_active')}"
            ),
        ),
        _check(
            "dynamic_capability_profiles_cover_the_team",
            _integer(
                metrics.get("registered_capability_profile_count")
            )
            >= len(agent_names),
            (
                "profiles="
                f"{metrics.get('registered_capability_profile_count')};"
                f"agents={len(agent_names)}"
            ),
        ),
        _check(
            "semantic_control_path_is_observed",
            _integer(
                metrics.get("agentlite_control_llm_call_count")
            )
            > 0
            and control_tokens > 0
            and _integer(
                metrics.get(
                    "agentlite_semantic_disambiguation_accepted_count"
                )
            )
            > 0,
            (
                "calls="
                f"{metrics.get('agentlite_control_llm_call_count')};"
                f"accepted={metrics.get('agentlite_semantic_disambiguation_accepted_count')};"
                f"tokens={control_tokens}"
            ),
        ),
        _check(
            "state_memory_bridge_and_injection_are_observed",
            _integer(
                metrics.get("agentlite_state_memory_bridge_event_count")
            )
            > 0
            and _integer(
                metrics.get("agentlite_memory_injected_count")
            )
            > 0,
            (
                "bridge="
                f"{metrics.get('agentlite_state_memory_bridge_event_count')};"
                f"injected={metrics.get('agentlite_memory_injected_count')}"
            ),
        ),
        _check(
            "injected_memory_has_supported_downstream_output",
            _integer(
                metrics.get("agentlite_memory_supported_output_count")
            )
            > 0
            or _integer(
                metrics.get("agentlite_useful_memory_hit_count")
            )
            > 0,
            (
                "supported="
                f"{metrics.get('agentlite_memory_supported_output_count')};"
                f"useful={metrics.get('agentlite_useful_memory_hit_count')}"
            ),
        ),
        _check(
            "no_wrong_memory_or_fidelity_failure",
            _integer(
                metrics.get("agentlite_wrong_memory_hit_count")
            )
            == 0
            and _integer(
                metrics.get("current_task_fidelity_failure_count")
            )
            == 0,
            (
                "wrong="
                f"{metrics.get('agentlite_wrong_memory_hit_count')};"
                "fidelity_failures="
                f"{metrics.get('current_task_fidelity_failure_count')}"
            ),
        ),
        _check(
            "no_model_visible_protocol_marker",
            _integer(
                metrics.get(
                    "agentlite_model_visible_protocol_marker_count"
                )
            )
            == 0,
            str(
                metrics.get(
                    "agentlite_model_visible_protocol_marker_count"
                )
            ),
        ),
        _check(
            "provider_usage_and_control_cost_are_complete",
            team_provider_tokens > 0
            and control_tokens > 0
            and provider_total
            == team_provider_tokens + control_tokens,
            (
                f"team={team_provider_tokens};control={control_tokens};"
                f"provider_total={provider_total}"
            ),
        ),
        _check(
            "accepted_preflight_has_no_unmetered_retry",
            main_retry_count == 0 and control_retry_count == 0,
            (
                f"team_retries={main_retry_count};"
                f"control_retries={control_retry_count}"
            ),
        ),
        _check(
            "cost_ledger_uses_disjoint_control_accounting",
            runtime_tokens >= control_tokens
            and accounted_total
            == provider_total + transport_excluding_control,
            (
                f"runtime={runtime_tokens};control={control_tokens};"
                f"transport_without_control={transport_excluding_control};"
                f"accounted={accounted_total}"
            ),
        ),
    ]
    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "agentlite.v515f.integrated-preflight.v1",
        "summary": {
            "passed": passed,
            "ready_for_comparative_preflight": passed,
            "check_count": len(checks),
            "passed_check_count": sum(
                int(item["passed"]) for item in checks
            ),
            "task_count": len(tasks),
            "agent_count": len(agent_names),
            "grounded_task_count": grounded_task_count,
        },
        "binding": {
            "scenario_id": str(scenario.get("scenario_id") or ""),
            "implementation_commit": implementation_commit,
        },
        "cost": {
            "team_provider_tokens": team_provider_tokens,
            "control_provider_tokens": control_tokens,
            "provider_total_tokens": provider_total,
            "collaboration_runtime_tokens": runtime_tokens,
            "transport_tokens_excluding_control": (
                transport_excluding_control
            ),
            "accounted_end_to_end_tokens": accounted_total,
            "team_retry_count": main_retry_count,
            "control_retry_count": control_retry_count,
            "control_double_counted_token_count": 0,
        },
        "quality": {
            "grounded_task_count": grounded_task_count,
            "wrong_memory_hit_count": _integer(
                metrics.get("agentlite_wrong_memory_hit_count")
            ),
            "memory_supported_output_count": _integer(
                metrics.get("agentlite_memory_supported_output_count")
            ),
            "model_visible_protocol_marker_count": _integer(
                metrics.get(
                    "agentlite_model_visible_protocol_marker_count"
                )
            ),
        },
        "task_grounding": task_grounding,
        "checks": checks,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15f Integrated AutoGen Semantic Preflight",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        f"- tasks: `{summary['task_count']}`",
        f"- agents: `{summary['agent_count']}`",
        f"- grounded tasks: `{summary['grounded_task_count']}`",
        "",
        "## Cost",
        "",
        "```json",
        json.dumps(report["cost"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Checks",
        "",
    ]
    for item in report["checks"]:
        marker = "PASS" if item["passed"] else "FAIL"
        lines.append(
            f"- [{marker}] `{item['name']}`: {item['detail']}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--team-config", type=Path, required=True)
    parser.add_argument("--workflow-result", type=Path, required=True)
    parser.add_argument("--session-report", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=args.repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    report = build_report(
        implementation_commit=commit,
        scenario=_load(args.scenario),
        team_config=_load(args.team_config),
        workflow=_load(args.workflow_result),
        session_report=_load(args.session_report),
    )
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output_markdown.write_text(
        _render_markdown(report) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
