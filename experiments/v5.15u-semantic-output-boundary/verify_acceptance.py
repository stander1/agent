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
    / "v5.15t-open-attribution-isolation"
)


def _load_base_verifier() -> ModuleType:
    path = BASE_EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515t_base_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15t base verifier")
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
    adoption_events = [
        event
        for event in trace_events
        if event.get("event_type") == "autogen_memory_adoption"
    ]
    surface_counts: dict[str, int] = {}
    for event in adoption_events:
        surface = str(
            event.get("payload", {}).get("attribution_surface") or ""
        )
        surface_counts[surface] = surface_counts.get(surface, 0) + 1
    decoded_count = surface_counts.get("decoded_model_content_v1", 0)
    boundary_check = _check(
        "memory_attribution_uses_decoded_model_content_surface",
        bool(adoption_events) and decoded_count == len(adoption_events),
        (
            f"decoded_surface_events={decoded_count}/"
            f"{len(adoption_events)};surfaces={surface_counts}"
        ),
    )
    checks = [*report.get("checks", []), boundary_check]
    passed = all(item.get("passed") for item in checks)
    report["schema_version"] = (
        "agentlite.v515u.semantic-output-boundary-acceptance.v1"
    )
    report["summary"] = {
        **report.get("summary", {}),
        "passed": passed,
        "ready_for_unseen_semantic_output_holdout": passed,
        "check_count": len(checks),
        "passed_check_count": sum(
            int(bool(item.get("passed"))) for item in checks
        ),
        "decoded_model_content_event_count": decoded_count,
        "memory_adoption_event_count": len(adoption_events),
    }
    report["checks"] = checks
    report["semantic_output_boundary"] = {
        "surface_counts": surface_counts,
        "decoded_model_content_event_count": decoded_count,
        "memory_adoption_event_count": len(adoption_events),
    }
    return report


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15u Semantic Output Boundary Acceptance",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- decoded model-content events: "
            f"`{summary['decoded_model_content_event_count']}/"
            f"{summary['memory_adoption_event_count']}`"
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
        "## Semantic Output Boundary",
        "",
        "```json",
        json.dumps(
            report["semantic_output_boundary"],
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
