from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from agent_runtime.memory.schema_registry import SUPERSESSION_RELATION_TYPES


BASE_EXPERIMENT_DIR = (
    Path(__file__).resolve().parents[1]
    / "v5.15r-structured-dependency-cost-override"
)


def _load_base_verifier() -> ModuleType:
    path = BASE_EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515r_base_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15r base verifier")
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


def _normalized(value: Any) -> str:
    return str(value or "").strip().casefold()


def _cross_source_binding_evidence(
    *,
    scenario: dict[str, Any],
    memory_snapshot: dict[str, Any],
) -> dict[str, Any]:
    expectation = scenario.get("revision_expectation")
    expectation = expectation if isinstance(expectation, dict) else {}
    active_value = _normalized(expectation.get("active_value"))
    historical_value = _normalized(expectation.get("historical_value"))
    unit = _normalized(expectation.get("unit"))
    claims = [
        claim
        for claim in memory_snapshot.get("claim_cards", [])
        if isinstance(claim, dict)
    ]
    active_matches = [
        claim
        for claim in claims
        if claim.get("status") == "active"
        and _normalized(claim.get("value")) == active_value
        and _normalized(claim.get("unit")) == unit
    ]
    historical_matches = [
        claim
        for claim in claims
        if claim.get("status") == "superseded"
        and _normalized(claim.get("value")) == historical_value
        and _normalized(claim.get("unit")) == unit
    ]
    active = active_matches[0] if len(active_matches) == 1 else {}
    historical = historical_matches[0] if len(historical_matches) == 1 else {}
    source_span = active.get("source_span")
    source_span = source_span if isinstance(source_span, dict) else {}
    source_quote = str(source_span.get("quote") or "")
    relation_rows = [
        relation
        for relation in active.get("relations", [])
        if isinstance(relation, dict)
        and str(relation.get("relation_type") or "").strip()
        in SUPERSESSION_RELATION_TYPES
    ]
    historical_refs = {
        str(historical.get("claim_id") or "").strip(),
        str(historical.get("candidate_id") or "").strip(),
    }
    historical_refs.discard("")
    bound_relations = [
        relation
        for relation in relation_rows
        if str(relation.get("target_candidate_id") or "").strip()
        in historical_refs
    ]
    semantic_identity_closed = bool(
        active
        and historical
        and str(active.get("semantic_key") or "")
        and active.get("semantic_key") == historical.get("semantic_key")
    )
    active_claim_id = str(active.get("claim_id") or "")
    historical_claim_id = str(historical.get("claim_id") or "")
    matching_views = [
        view
        for view in memory_snapshot.get("memory_views", [])
        if isinstance(view, dict)
        and active_claim_id in view.get("active_claim_ids", [])
        and historical_claim_id in view.get("historical_claim_ids", [])
    ]
    historical_literal_omitted = bool(
        historical_value
        and source_quote
        and historical_value not in source_quote.casefold()
    )
    return {
        "expected_active_value": active_value,
        "expected_historical_value": historical_value,
        "expected_unit": unit,
        "active_match_count": len(active_matches),
        "historical_match_count": len(historical_matches),
        "active_claim_id": active_claim_id,
        "historical_claim_id": historical_claim_id,
        "active_candidate_id": str(active.get("candidate_id") or ""),
        "historical_candidate_id": str(historical.get("candidate_id") or ""),
        "semantic_identity_closed": semantic_identity_closed,
        "revision_source_quote": source_quote,
        "historical_literal_omitted": historical_literal_omitted,
        "supersession_relation_count": len(relation_rows),
        "locally_bound_predecessor_count": len(bound_relations),
        "bound_target_ids": [
            str(relation.get("target_candidate_id") or "")
            for relation in bound_relations
        ],
        "closed_memory_view_count": len(matching_views),
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
    evidence = _cross_source_binding_evidence(
        scenario=scenario,
        memory_snapshot=memory_snapshot,
    )
    checks = [
        *report.get("checks", []),
        _check(
            "cross_source_revision_claim_pair_observed",
            evidence["active_match_count"] == 1
            and evidence["historical_match_count"] == 1
            and evidence["semantic_identity_closed"],
            (
                f"active={evidence['active_match_count']};"
                f"historical={evidence['historical_match_count']};"
                f"identity_closed={evidence['semantic_identity_closed']}"
            ),
        ),
        _check(
            "revision_source_omits_predecessor_literal",
            evidence["historical_literal_omitted"],
            (
                f"historical_value={evidence['expected_historical_value']};"
                f"source_quote={evidence['revision_source_quote']!r}"
            ),
        ),
        _check(
            "supersession_target_is_locally_bound_to_historical_claim",
            evidence["supersession_relation_count"] >= 1
            and evidence["locally_bound_predecessor_count"] == 1,
            (
                f"relations={evidence['supersession_relation_count']};"
                f"bound={evidence['locally_bound_predecessor_count']};"
                f"targets={evidence['bound_target_ids']}"
            ),
        ),
        _check(
            "memory_view_closes_bound_revision_pair",
            evidence["closed_memory_view_count"] == 1,
            f"closed_views={evidence['closed_memory_view_count']}",
        ),
    ]
    passed = all(item.get("passed") for item in checks)
    report["schema_version"] = (
        "agentlite.v515s.cross-source-predecessor-binding-acceptance.v1"
    )
    report["summary"] = {
        **report.get("summary", {}),
        "passed": passed,
        "ready_for_unseen_cross_source_predecessor_holdout": passed,
        "check_count": len(checks),
        "passed_check_count": sum(
            int(bool(item.get("passed"))) for item in checks
        ),
        "locally_bound_predecessor_count": evidence[
            "locally_bound_predecessor_count"
        ],
        "revision_source_omits_historical_value": evidence[
            "historical_literal_omitted"
        ],
    }
    report["checks"] = checks
    report["cross_source_predecessor_evidence"] = evidence
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15s Cross-Source Predecessor Binding Acceptance",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- locally bound predecessors: "
            f"`{summary['locally_bound_predecessor_count']}`"
        ),
        (
            "- revision source omits historical value: "
            f"`{summary['revision_source_omits_historical_value']}`"
        ),
        "",
        "## Cross-Source Predecessor Evidence",
        "",
        "```json",
        json.dumps(
            report["cross_source_predecessor_evidence"],
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
