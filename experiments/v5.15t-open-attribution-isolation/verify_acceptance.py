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
    / "v5.15s-cross-source-predecessor-binding"
)


def _load_base_verifier() -> ModuleType:
    path = BASE_EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515s_base_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15s base verifier")
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


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _open_attribution_evidence(
    trace_events: list[dict[str, Any]],
) -> dict[str, Any]:
    adoption_payloads = [
        event.get("payload", {})
        for event in trace_events
        if event.get("event_type") == "autogen_memory_adoption"
        and isinstance(event.get("payload"), dict)
    ]
    rows = [
        row
        for payload in adoption_payloads
        for row in payload.get("evidence", [])
        if isinstance(row, dict)
    ]
    v3_mode = "ccf_v3_open_candidate_evidence"
    exact_match_count = sum(
        int(mode == "open_candidate_exact")
        for row in rows
        for mode in (
            *row.get("matched_active_match_modes", []),
            *row.get("matched_historical_match_modes", []),
        )
    )
    return {
        "adoption_event_count": len(adoption_payloads),
        "evidence_row_count": len(rows),
        "v3_event_count": sum(
            int(payload.get("attribution_mode") == v3_mode)
            for payload in adoption_payloads
        ),
        "v3_evidence_row_count": sum(
            int(row.get("attribution_mode") == v3_mode) for row in rows
        ),
        "legacy_domain_candidate_count": sum(
            _as_int(row.get("legacy_domain_candidate_count")) for row in rows
        ),
        "open_output_candidate_count": sum(
            _as_int(row.get("open_output_candidate_count")) for row in rows
        ),
        "open_current_task_candidate_count": sum(
            _as_int(row.get("open_current_task_candidate_count"))
            for row in rows
        ),
        "open_candidate_exact_match_count": exact_match_count,
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
    evidence = _open_attribution_evidence(trace_events)
    event_count = evidence["adoption_event_count"]
    row_count = evidence["evidence_row_count"]
    checks = [
        *report.get("checks", []),
        _check(
            "open_attribution_events_observed",
            event_count >= 1 and row_count >= 1,
            f"events={event_count};rows={row_count}",
        ),
        _check(
            "memory_attribution_uses_open_candidates_only",
            evidence["v3_event_count"] == event_count
            and evidence["v3_evidence_row_count"] == row_count,
            (
                f"v3_events={evidence['v3_event_count']}/{event_count};"
                f"v3_rows={evidence['v3_evidence_row_count']}/{row_count}"
            ),
        ),
        _check(
            "legacy_domain_candidates_absent_from_attribution",
            evidence["legacy_domain_candidate_count"] == 0,
            f"legacy_candidates={evidence['legacy_domain_candidate_count']}",
        ),
        _check(
            "open_predicate_exact_match_observed",
            evidence["open_output_candidate_count"] >= 1
            and evidence["open_candidate_exact_match_count"] >= 1,
            (
                f"output_candidates={evidence['open_output_candidate_count']};"
                f"exact_matches={evidence['open_candidate_exact_match_count']}"
            ),
        ),
    ]
    passed = all(item.get("passed") for item in checks)
    report["schema_version"] = (
        "agentlite.v515t.open-attribution-isolation-acceptance.v1"
    )
    report["summary"] = {
        **report.get("summary", {}),
        "passed": passed,
        "ready_for_unseen_open_attribution_holdout": passed,
        "check_count": len(checks),
        "passed_check_count": sum(
            int(bool(item.get("passed"))) for item in checks
        ),
        "open_attribution_evidence_row_count": row_count,
        "open_candidate_exact_match_count": evidence[
            "open_candidate_exact_match_count"
        ],
        "legacy_domain_candidate_count": evidence[
            "legacy_domain_candidate_count"
        ],
    }
    report["checks"] = checks
    report["open_attribution_evidence"] = evidence
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15t Open Attribution Isolation Acceptance",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- open attribution rows: "
            f"`{summary['open_attribution_evidence_row_count']}`"
        ),
        (
            "- exact open matches: "
            f"`{summary['open_candidate_exact_match_count']}`"
        ),
        (
            "- legacy domain candidates: "
            f"`{summary['legacy_domain_candidate_count']}`"
        ),
        "",
        "## Open Attribution Evidence",
        "",
        "```json",
        json.dumps(
            report["open_attribution_evidence"],
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