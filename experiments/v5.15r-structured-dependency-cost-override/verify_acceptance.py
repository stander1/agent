from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


BASE_EXPERIMENT_DIR = (
    Path(__file__).resolve().parents[1]
    / "v5.15q-explicit-typed-context-retention"
)
STRUCTURED_REASON = "explicit_historical_state_request"


def _load_base_verifier() -> ModuleType:
    path = BASE_EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515q_base_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15q base verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = _load_base_verifier()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _load_trace(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        events.append(value)
    return events


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _final_task_index(scenario: dict[str, Any]) -> int:
    tasks = [
        task for task in scenario.get("tasks", []) if isinstance(task, dict)
    ]
    revision = scenario.get("revision_expectation")
    revision = revision if isinstance(revision, dict) else {}
    final_task_id = str(revision.get("final_task_id") or "")
    indexes = [
        index
        for index, task in enumerate(tasks, start=1)
        if str(task.get("task_id") or "") == final_task_id
    ]
    return indexes[0] if len(indexes) == 1 else 0


def _structured_override_evidence(
    *,
    scenario: dict[str, Any],
    team_config: dict[str, Any],
    trace_events: list[dict[str, Any]],
) -> dict[str, Any]:
    final_index = _final_task_index(scenario)
    agent_names = [
        str(agent.get("name") or "")
        for agent in team_config.get("agents", [])
        if isinstance(agent, dict) and str(agent.get("name") or "")
    ]
    dependency_events: list[dict[str, Any]] = []
    rewrites_by_agent: dict[str, dict[str, Any]] = {}
    for event in trace_events:
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if event.get("event_type") == "autogen_semantic_dependency":
            if _integer(payload.get("task_sequence_index")) == final_index:
                dependency_events.append(payload)
            continue
        if event.get("event_type") != "autogen_agent_input_real_rewrite":
            continue
        safety = payload.get("rewrite_safety")
        safety = safety if isinstance(safety, dict) else {}
        if _integer(safety.get("task_sequence_index")) != final_index:
            continue
        agent_id = str(payload.get("agent_id") or "")
        if agent_id in agent_names:
            rewrites_by_agent[agent_id] = payload

    structured_dependencies = [
        payload
        for payload in dependency_events
        if payload.get("status") == "structured_required"
        and bool(payload.get("required"))
        and STRUCTURED_REASON in payload.get("reasons", [])
    ]
    zero_cost_dependencies = [
        payload
        for payload in structured_dependencies
        if _integer(payload.get("call_count")) == 0
        and _integer(payload.get("total_tokens")) == 0
        and _integer(payload.get("retry_count")) == 0
    ]

    receiver_evidence: dict[str, dict[str, Any]] = {}
    applied_count = 0
    structured_continuity_count = 0
    boundary_count = 0
    expanded_count = 0
    expanded_override_count = 0
    cost_passthrough_count = 0
    for agent_name in agent_names:
        payload = rewrites_by_agent.get(agent_name, {})
        applied = bool(payload.get("rewrite_applied"))
        reasons = payload.get("continuity_context_reasons", [])
        structured_continuity = (
            bool(payload.get("continuity_context_required"))
            and STRUCTURED_REASON in reasons
        )
        boundary = bool(payload.get("model_response_boundary_applied"))
        native_tokens = _integer(payload.get("native_input_tokens"))
        rewritten_tokens = _integer(payload.get("rewritten_input_tokens"))
        expanded = rewritten_tokens >= native_tokens and rewritten_tokens > 0
        override = bool(payload.get("continuity_cost_override"))
        passthrough = (
            payload.get("rewrite_outcome") == "cost_guard_passthrough"
            or "cost_gate_failed" in payload.get("fallback_buckets", [])
        )
        applied_count += int(applied)
        structured_continuity_count += int(structured_continuity)
        boundary_count += int(boundary)
        expanded_count += int(expanded)
        expanded_override_count += int(expanded and override)
        cost_passthrough_count += int(passthrough)
        receiver_evidence[agent_name] = {
            "observed": bool(payload),
            "rewrite_applied": applied,
            "structured_continuity": structured_continuity,
            "response_boundary_applied": boundary,
            "native_input_tokens": native_tokens,
            "rewritten_input_tokens": rewritten_tokens,
            "token_expansion": expanded,
            "continuity_cost_override": override,
            "cost_guard_passthrough": passthrough,
        }

    return {
        "final_task_index": final_index,
        "configured_receiver_count": len(agent_names),
        "observed_receiver_count": len(rewrites_by_agent),
        "dependency_event_count": len(dependency_events),
        "structured_dependency_count": len(structured_dependencies),
        "zero_cost_structured_dependency_count": len(zero_cost_dependencies),
        "applied_receiver_count": applied_count,
        "structured_continuity_receiver_count": structured_continuity_count,
        "response_boundary_receiver_count": boundary_count,
        "expanded_receiver_count": expanded_count,
        "expanded_override_receiver_count": expanded_override_count,
        "cost_guard_passthrough_count": cost_passthrough_count,
        "receiver_evidence": receiver_evidence,
    }


def build_report(
    *,
    implementation_commit: str,
    scenario: dict[str, Any],
    team_config: dict[str, Any],
    workflow: dict[str, Any],
    session_report: dict[str, Any],
    memory_snapshot: dict[str, Any],
    trace_events: list[dict[str, Any]],
) -> dict[str, Any]:
    report = BASE.build_report(
        implementation_commit=implementation_commit,
        scenario=scenario,
        team_config=team_config,
        workflow=workflow,
        session_report=session_report,
        memory_snapshot=memory_snapshot,
        trace_events=trace_events,
    )
    evidence = _structured_override_evidence(
        scenario=scenario,
        team_config=team_config,
        trace_events=trace_events,
    )
    configured = evidence["configured_receiver_count"]
    checks = [
        *report.get("checks", []),
        _check(
            "final_dependency_uses_structured_zero_cost_path",
            evidence["dependency_event_count"] == 1
            and evidence["structured_dependency_count"] == 1
            and evidence["zero_cost_structured_dependency_count"] == 1,
            (
                f"events={evidence['dependency_event_count']};"
                f"structured={evidence['structured_dependency_count']};"
                "zero_cost="
                f"{evidence['zero_cost_structured_dependency_count']}"
            ),
        ),
        _check(
            "final_receiver_rewrites_apply_with_structured_continuity",
            configured > 0
            and evidence["observed_receiver_count"] == configured
            and evidence["applied_receiver_count"] == configured
            and evidence["structured_continuity_receiver_count"] == configured,
            (
                f"observed={evidence['observed_receiver_count']};"
                f"applied={evidence['applied_receiver_count']};"
                "structured="
                f"{evidence['structured_continuity_receiver_count']};"
                f"configured={configured}"
            ),
        ),
        _check(
            "nonreducing_final_rewrites_use_continuity_cost_override",
            evidence["expanded_receiver_count"] > 0
            and evidence["expanded_override_receiver_count"]
            == evidence["expanded_receiver_count"],
            (
                f"expanded={evidence['expanded_receiver_count']};"
                f"overridden={evidence['expanded_override_receiver_count']}"
            ),
        ),
        _check(
            "final_model_inputs_apply_response_boundary",
            configured > 0
            and evidence["response_boundary_receiver_count"] == configured,
            (
                f"bounded={evidence['response_boundary_receiver_count']};"
                f"configured={configured}"
            ),
        ),
        _check(
            "final_receiver_rewrites_have_no_cost_guard_passthrough",
            evidence["cost_guard_passthrough_count"] == 0,
            f"passthrough={evidence['cost_guard_passthrough_count']}",
        ),
    ]
    passed = all(item.get("passed") for item in checks)
    report["schema_version"] = (
        "agentlite.v515r.structured-dependency-cost-override-acceptance.v1"
    )
    report["summary"] = {
        **report.get("summary", {}),
        "passed": passed,
        "ready_for_unseen_structured_dependency_holdout": passed,
        "check_count": len(checks),
        "passed_check_count": sum(int(bool(item.get("passed"))) for item in checks),
        "zero_cost_structured_dependency_count": evidence[
            "zero_cost_structured_dependency_count"
        ],
        "structured_continuity_receiver_count": evidence[
            "structured_continuity_receiver_count"
        ],
        "continuity_cost_override_count": evidence[
            "expanded_override_receiver_count"
        ],
    }
    report["checks"] = checks
    report["structured_dependency_cost_evidence"] = evidence
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15r Structured Dependency Cost Override Acceptance",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- zero-cost structured dependencies: "
            f"`{summary['zero_cost_structured_dependency_count']}`"
        ),
        (
            "- structured continuity receivers: "
            f"`{summary['structured_continuity_receiver_count']}`"
        ),
        (
            "- continuity cost overrides: "
            f"`{summary['continuity_cost_override_count']}`"
        ),
        "",
        "## Structured Dependency and Cost Evidence",
        "",
        "```json",
        json.dumps(
            report["structured_dependency_cost_evidence"],
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "## Checks",
        "",
    ]
    for item in report["checks"]:
        marker = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- [{marker}] `{item['name']}`: {item['detail']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--team-config", type=Path, required=True)
    parser.add_argument("--workflow-result", type=Path, required=True)
    parser.add_argument("--session-report", type=Path, required=True)
    parser.add_argument("--memory-snapshot", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
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
        memory_snapshot=_load(args.memory_snapshot),
        trace_events=_load_trace(args.trace),
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