from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import ModuleType
from typing import Any


BASE_EXPERIMENT_DIR = (
    Path(__file__).resolve().parents[1]
    / "v5.15p-closed-revision-measurement-acceptance"
)

def _load_base_verifier() -> ModuleType:
    path = BASE_EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515p_base_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15p base verifier")
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


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _fact_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    if str(actual.get("kind") or "") != str(expected.get("kind") or ""):
        return False
    value_type = str(expected.get("value_type") or "string").casefold()
    if str(actual.get("value_type") or "string").casefold() != value_type:
        return False
    if str(actual.get("unit") or "").strip().casefold() != str(
        expected.get("unit") or ""
    ).strip().casefold():
        return False
    expected_status = str(expected.get("status") or "").strip().casefold()
    if expected_status and str(actual.get("status") or "").casefold() != expected_status:
        return False
    actual_value = str(actual.get("value") or "").strip()
    expected_value = str(expected.get("value") or "").strip()
    if value_type == "number":
        return _decimal(actual_value) == _decimal(expected_value)
    return actual_value.casefold() == expected_value.casefold()


def _expected_typed_facts(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    revision = scenario.get("revision_expectation")
    revision = revision if isinstance(revision, dict) else {}
    value_type = str(revision.get("value_type") or "")
    unit = str(revision.get("unit") or "")
    expected: list[dict[str, Any]] = []
    if revision.get("active_value") is not None:
        expected.append(
            {
                "kind": "active_fact",
                "value": str(revision.get("active_value") or ""),
                "value_type": value_type,
                "unit": unit,
                "status": "active",
            }
        )
    if revision.get("historical_value") is not None:
        expected.append(
            {
                "kind": "historical_fact",
                "value": str(revision.get("historical_value") or ""),
                "value_type": value_type,
                "unit": unit,
                "status": "superseded",
            }
        )
    context = scenario.get("model_visible_context_expectation")
    context = context if isinstance(context, dict) else {}
    for fact in context.get("required_typed_facts", []):
        if isinstance(fact, dict):
            expected.append(dict(fact))
    return expected


def _model_visible_evidence(
    *,
    scenario: dict[str, Any],
    team_config: dict[str, Any],
    trace_events: list[dict[str, Any]],
) -> dict[str, Any]:
    tasks = [task for task in scenario.get("tasks", []) if isinstance(task, dict)]
    revision = scenario.get("revision_expectation")
    revision = revision if isinstance(revision, dict) else {}
    final_task_id = str(revision.get("final_task_id") or "")
    final_indexes = [
        index
        for index, task in enumerate(tasks, start=1)
        if str(task.get("task_id") or "") == final_task_id
    ]
    final_task_index = final_indexes[0] if len(final_indexes) == 1 else 0
    agent_names = [
        str(agent.get("name") or "")
        for agent in team_config.get("agents", [])
        if isinstance(agent, dict) and str(agent.get("name") or "")
    ]
    expected = _expected_typed_facts(scenario)
    facts_by_agent: dict[str, list[dict[str, Any]]] = {}
    for event in trace_events:
        if event.get("event_type") != "autogen_agent_input_real_rewrite":
            continue
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        rewrite_safety = payload.get("rewrite_safety")
        rewrite_safety = (
            rewrite_safety if isinstance(rewrite_safety, dict) else {}
        )
        if (
            int(rewrite_safety.get("task_sequence_index") or 0)
            != final_task_index
        ):
            continue
        agent_id = str(payload.get("agent_id") or "")
        if agent_id not in agent_names:
            continue
        facts = facts_by_agent.setdefault(agent_id, [])
        if not bool(payload.get("rewrite_applied")):
            continue
        for fact in payload.get("model_visible_memory_facts", []):
            if not isinstance(fact, dict):
                continue
            facts.append(dict(fact))

    receiver_evidence: dict[str, dict[str, int]] = {}
    complete_receivers: list[str] = []
    for agent_name in agent_names:
        actual = facts_by_agent.get(agent_name, [])
        matched = sum(
            int(any(_fact_matches(fact, item) for fact in actual))
            for item in expected
        )
        receiver_evidence[agent_name] = {
            "typed_fact_count": len(actual),
            "matched_expected_count": matched,
            "missing_expected_count": max(0, len(expected) - matched),
        }
        if expected and matched == len(expected):
            complete_receivers.append(agent_name)

    return {
        "final_task_id": final_task_id,
        "final_task_index": final_task_index,
        "configured_receiver_count": len(agent_names),
        "observed_receiver_count": len(facts_by_agent),
        "complete_receiver_count": len(complete_receivers),
        "expected_typed_fact_count": len(expected),
        "declared_additional_fact_count": max(0, len(expected) - 2),
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
    )
    evidence = _model_visible_evidence(
        scenario=scenario,
        team_config=team_config,
        trace_events=trace_events,
    )
    configured = evidence["configured_receiver_count"]
    expected = evidence["expected_typed_fact_count"]
    context_checks = [
        _check(
            "external_typed_context_expectation_is_declared",
            evidence["declared_additional_fact_count"] > 0,
            (
                "additional="
                f"{evidence['declared_additional_fact_count']};expected={expected}"
            ),
        ),
        _check(
            "final_receiver_inputs_preserve_explicit_typed_context",
            (
                configured > 0
                and expected >= 3
                and evidence["complete_receiver_count"] == configured
            ),
            (
                f"complete={evidence['complete_receiver_count']};"
                f"configured={configured};expected={expected}"
            ),
        ),
        _check(
            "typed_context_is_observed_for_every_configured_receiver",
            evidence["observed_receiver_count"] == configured and configured > 0,
            (
                f"observed={evidence['observed_receiver_count']};"
                f"configured={configured}"
            ),
        ),
    ]
    checks = [*report.get("checks", []), *context_checks]
    passed = all(item.get("passed") for item in checks)
    report["schema_version"] = (
        "agentlite.v515q.explicit-typed-context-retention-acceptance.v1"
    )
    report["summary"] = {
        **report.get("summary", {}),
        "passed": passed,
        "ready_for_unseen_typed_context_holdout": passed,
        "check_count": len(checks),
        "passed_check_count": sum(int(bool(item.get("passed"))) for item in checks),
        "model_visible_context_receiver_count": evidence[
            "complete_receiver_count"
        ],
        "expected_typed_fact_count": expected,
    }
    report["checks"] = checks
    report["model_visible_context_evidence"] = evidence
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15q Explicit Typed Context Retention Acceptance",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- model-visible context receivers: "
            f"`{summary['model_visible_context_receiver_count']}`"
        ),
        (
            "- expected typed facts: "
            f"`{summary['expected_typed_fact_count']}`"
        ),
        "",
        "## Model-Visible Context Evidence",
        "",
        "```json",
        json.dumps(
            report["model_visible_context_evidence"],
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
