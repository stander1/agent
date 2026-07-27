from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
V514V_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14v-continuity-memory-formal-regression"
)
V514W_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14w-evidence-fidelity-governance"
)


def _load_verifier(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load acceptance verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514V_VERIFY = _load_verifier(
    "v514v_verify",
    V514V_DIR / "verify_acceptance.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the Provider regression after budget-fact and required-"
            "evidence fidelity governance."
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
    report = V514V_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = list(report.get("checks", []))
    preregistration = dict(preflight_report.get("preregistration") or {})
    lineage = dict(preregistration.get("source_lineage") or {})
    thresholds = dict(preregistration.get("thresholds") or {})

    mechanism_commit = str(
        lineage.get("mechanism_release_commit") or ""
    ).lower()
    run_commit = _read_text(run_root / "system" / "git-commit.txt").lower()
    checks.append(
        _check(
            "formal_run_descends_from_v514w_release",
            bool(mechanism_commit)
            and bool(run_commit)
            and _is_ancestor(mechanism_commit, run_commit),
            f"mechanism={mechanism_commit};run={run_commit}",
        )
    )
    checks.append(
        _hash_check(
            "v514v_preregistration_is_bound",
            str(lineage.get("v514v_preregistration_sha256") or ""),
            V514V_DIR / "preregistration.json",
        )
    )
    checks.append(
        _hash_check(
            "v514w_acceptance_verifier_is_bound",
            str(lineage.get("v514w_acceptance_verifier_sha256") or ""),
            V514W_DIR / "verify_acceptance.py",
        )
    )

    guard_events: list[dict[str, Any]] = []
    invalid_guard_events: list[dict[str, Any]] = []
    for spec in preregistration.get("scenarios", []):
        scenario_id = str(spec.get("scenario_id") or "")
        directory = str(spec.get("directory") or "")
        for event in _scenario_trace_events(run_root, directory):
            if event.get("event_type") != "autogen_required_evidence_guard":
                continue
            payload = dict(event.get("payload") or {})
            record = {
                "scenario_id": scenario_id,
                "task_id": str(payload.get("task_id") or ""),
                "status": str(payload.get("status") or ""),
                "unavailable_artifacts": list(
                    payload.get("unavailable_artifacts") or []
                ),
                "fallback_value": str(
                    payload.get("fallback_value") or ""
                ),
                "claimed_unavailable_artifacts": list(
                    payload.get("claimed_unavailable_artifacts") or []
                ),
                "conflicting_decision_values": list(
                    payload.get("conflicting_decision_values") or []
                ),
            }
            guard_events.append(record)
            if (
                record["status"] != "blocked_and_deferred"
                or not record["unavailable_artifacts"]
                or not record["fallback_value"]
                or not (
                    record["claimed_unavailable_artifacts"]
                    or record["conflicting_decision_values"]
                )
            ):
                invalid_guard_events.append(record)

    maximum_invalid_guards = int(
        thresholds.get(
            "required_evidence_guard_invalid_event_count_max",
            0,
        )
    )
    checks.append(
        _check(
            "required_evidence_guard_events_are_grounded",
            len(invalid_guard_events) <= maximum_invalid_guards,
            json.dumps(
                {
                    "event_count": len(guard_events),
                    "invalid_count": len(invalid_guard_events),
                    "maximum": maximum_invalid_guards,
                    "invalid_events": invalid_guard_events,
                },
                ensure_ascii=False,
            ),
        )
    )

    passed_count = sum(bool(item.get("passed")) for item in checks)
    report["checks"] = checks
    report["evidence_fidelity"] = {
        "required_evidence_guard_event_count": len(guard_events),
        "required_evidence_guard_events": guard_events,
        "required_evidence_guard_invalid_event_count": len(
            invalid_guard_events
        ),
        "required_evidence_guard_invalid_events": invalid_guard_events,
    }
    report["summary"].update(
        {
            "passed": passed_count == len(checks),
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "evidence_fidelity_formal_passed": (
                passed_count == len(checks)
            ),
            "required_evidence_guard_event_count": len(guard_events),
            "required_evidence_guard_invalid_event_count": len(
                invalid_guard_events
            ),
        }
    )
    return report


def _scenario_trace_events(
    run_root: Path,
    directory: str,
) -> Iterable[dict[str, Any]]:
    pattern = (
        f"{directory}/managed/agentlite_data/sessions/"
        "*/autogen_driver/trace.jsonl"
    )
    for path in run_root.glob(pattern):
        for line in path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _hash_check(name: str, expected: str, path: Path) -> dict[str, Any]:
    actual = _sha256(path)
    expected = str(expected or "").lower()
    return _check(
        name,
        bool(expected) and expected == actual,
        f"expected={expected};actual={actual}",
    )


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value) if isinstance(value, dict) else {}


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _markdown(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    evidence = dict(report.get("evidence_fidelity") or {})
    failed = [item for item in report["checks"] if not item["passed"]]
    lines = [
        "# v5.14x Evidence Fidelity Formal Regression",
        "",
        f"- Passed: `{summary.get('passed')}`",
        (
            f"- Checks: `{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        (
            "- Required-evidence guard events: "
            f"`{evidence.get('required_evidence_guard_event_count', 0)}`"
        ),
        (
            "- Invalid required-evidence guard events: "
            f"`{evidence.get('required_evidence_guard_invalid_event_count', 0)}`"
        ),
        "",
        "## Failed checks",
        "",
    ]
    if failed:
        lines.extend(
            f"- `{item['name']}`: {item['detail']}" for item in failed
        )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "- Tasks, Agents, model, temperature, turns, judges, and all "
            "v5.14v thresholds remain frozen.",
            "- Production code contains no scenario, domain, conclusion, or "
            "fixed-role special case.",
            "- A required-evidence guard event is not mandatory; any observed "
            "event must identify a missing artifact, the user's fallback, "
            "and the unsupported output condition.",
            "- Provider, transport, quality, delivery, memory correctness, "
            "review governance, and protocol-hygiene gates remain unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


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
    output_markdown.write_text(_markdown(report), encoding="utf-8")


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
