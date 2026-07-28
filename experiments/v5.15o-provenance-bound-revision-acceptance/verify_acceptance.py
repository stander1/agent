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
    / "v5.15f-integrated-autogen-semantic-preflight"
)


def _load_base_verifier() -> ModuleType:
    path = BASE_EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515f_base_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15f base verifier")
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


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _typed_value_matches(
    claim: dict[str, Any],
    *,
    value: str,
    value_type: str,
    unit: str,
) -> bool:
    actual_type = str(claim.get("value_type") or "string").casefold()
    if actual_type != value_type.casefold():
        return False
    actual_unit = str(claim.get("unit") or "").strip().casefold()
    if actual_unit != unit.strip().casefold():
        return False
    actual_value = str(claim.get("value") or "").strip()
    if value_type.casefold() == "number":
        return _decimal(actual_value) == _decimal(value)
    return actual_value.casefold() == value.strip().casefold()


def _revision_pair(
    snapshot: dict[str, Any],
    expectation: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    cards = [
        card
        for card in snapshot.get("claim_cards", [])
        if isinstance(card, dict)
    ]
    value_type = str(expectation.get("value_type") or "")
    unit = str(expectation.get("unit") or "")
    active_value = str(expectation.get("active_value") or "")
    historical_value = str(expectation.get("historical_value") or "")
    active = [
        card
        for card in cards
        if card.get("status") == "active"
        and _typed_value_matches(
            card,
            value=active_value,
            value_type=value_type,
            unit=unit,
        )
    ]
    historical = [
        card
        for card in cards
        if card.get("status") == "superseded"
        and _typed_value_matches(
            card,
            value=historical_value,
            value_type=value_type,
            unit=unit,
        )
    ]
    pairs = [
        (current, former)
        for current in active
        for former in historical
        if current.get("semantic_key")
        and current.get("semantic_key") == former.get("semantic_key")
    ]
    detail = (
        f"active_matches={len(active)};historical_matches={len(historical)};"
        f"same_identity_pairs={len(pairs)}"
    )
    if len(pairs) != 1:
        return None, None, detail
    return pairs[0][0], pairs[0][1], detail


def build_report(
    *,
    implementation_commit: str,
    scenario: dict[str, Any],
    team_config: dict[str, Any],
    workflow: dict[str, Any],
    session_report: dict[str, Any],
    memory_snapshot: dict[str, Any],
) -> dict[str, Any]:
    base = BASE.build_report(
        implementation_commit=implementation_commit,
        scenario=scenario,
        team_config=team_config,
        workflow=workflow,
        session_report=session_report,
    )
    expectation = scenario.get("revision_expectation")
    expectation = expectation if isinstance(expectation, dict) else {}
    active, historical, pair_detail = _revision_pair(
        memory_snapshot,
        expectation,
    )

    final_task_id = str(expectation.get("final_task_id") or "")
    final_task = next(
        (
            task
            for task in scenario.get("tasks", [])
            if isinstance(task, dict)
            and str(task.get("task_id") or "") == final_task_id
        ),
        {},
    )
    final_question = str(final_task.get("question") or "").casefold()
    active_value = str(expectation.get("active_value") or "")
    historical_value = str(expectation.get("historical_value") or "")
    values_absent_from_final_task = (
        bool(final_task_id)
        and bool(active_value)
        and bool(historical_value)
        and active_value.casefold() not in final_question
        and historical_value.casefold() not in final_question
    )

    historical_targets = set()
    if historical is not None:
        historical_targets = {
            str(historical.get("claim_id") or ""),
            str(historical.get("candidate_id") or ""),
        } - {""}
    relation_targets = {
        str(relation.get("target_candidate_id") or "")
        for relation in (active or {}).get("relations", [])
        if isinstance(relation, dict)
        and relation.get("relation_type") == "supersedes_value"
    }
    relation_bound = bool(historical_targets & relation_targets)

    matching_views = [
        view
        for view in memory_snapshot.get("memory_views", [])
        if isinstance(view, dict)
        and active is not None
        and historical is not None
        and view.get("semantic_key") == active.get("semantic_key")
        and active.get("claim_id") in view.get("active_claim_ids", [])
        and historical.get("claim_id")
        in view.get("historical_claim_ids", [])
    ]

    metrics = _metrics(session_report)
    fetch_count = _integer(metrics.get("agentlite_memory_field_fetch_count"))
    fetch_tokens = _integer(metrics.get("agentlite_memory_field_fetch_tokens"))
    minimum_fetch_count = max(
        1,
        _integer(expectation.get("minimum_field_fetch_count")),
    )

    revision_checks = [
        _check(
            "base_integrated_preflight_passed",
            bool(base.get("summary", {}).get("passed")),
            (
                f"base={base.get('summary', {}).get('passed_check_count')}"
                f"/{base.get('summary', {}).get('check_count')}"
            ),
        ),
        _check(
            "final_history_request_does_not_restate_values",
            values_absent_from_final_task,
            f"final_task_id={final_task_id}",
        ),
        _check(
            "typed_revision_pair_is_admitted_under_one_identity",
            active is not None and historical is not None,
            pair_detail,
        ),
        _check(
            "active_revision_targets_historical_predecessor",
            relation_bound,
            (
                f"relation_targets={sorted(relation_targets)};"
                f"historical_targets={sorted(historical_targets)}"
            ),
        ),
        _check(
            "memory_view_exposes_active_and_historical_claims",
            len(matching_views) == 1,
            f"matching_views={len(matching_views)}",
        ),
        _check(
            "unstated_history_is_charged_as_field_fetch",
            fetch_count >= minimum_fetch_count and fetch_tokens > 0,
            (
                f"count={fetch_count};minimum={minimum_fetch_count};"
                f"tokens={fetch_tokens}"
            ),
        ),
    ]
    checks = [*base.get("checks", []), *revision_checks]
    passed = all(item.get("passed") for item in checks)
    base_summary = base.get("summary", {})
    base["schema_version"] = (
        "agentlite.v515o.provenance-bound-revision-acceptance.v1"
    )
    base["summary"] = {
        **base_summary,
        "passed": passed,
        "ready_for_unseen_revision_holdout": passed,
        "check_count": len(checks),
        "passed_check_count": sum(
            int(bool(item.get("passed"))) for item in checks
        ),
        "memory_field_fetch_count": fetch_count,
        "memory_field_fetch_tokens": fetch_tokens,
    }
    base["checks"] = checks
    base["revision_evidence"] = {
        "active_claim_id": str((active or {}).get("claim_id") or ""),
        "historical_claim_id": str(
            (historical or {}).get("claim_id") or ""
        ),
        "semantic_key": str((active or {}).get("semantic_key") or ""),
        "matching_memory_view_count": len(matching_views),
        "memory_field_fetch_count": fetch_count,
        "memory_field_fetch_tokens": fetch_tokens,
    }
    return base


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15o Provenance-Bound Revision Acceptance",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- memory field fetch: "
            f"`{summary['memory_field_fetch_count']}` calls, "
            f"`{summary['memory_field_fetch_tokens']}` tokens"
        ),
        "",
        "## Revision Evidence",
        "",
        "```json",
        json.dumps(report["revision_evidence"], ensure_ascii=False, indent=2),
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