from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
V514R_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14r-candidate-evidence-formal-regression"
)
V514S_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14s-superseded-constraint-resolution"
)
_HISTORICAL_CONSTRAINT_RE = re.compile(
    r"(?is)(?:"
    r"(?:\u6700\u521d|\u521d\u59cb|\u539f\u5b9a|\u8c03\u6574\u524d|"
    r"initial|original|previous|prior).{0,80}"
    r"(?:\u603b\u9884\u7b97|\u9884\u7b97\u603b\u8ba1|total\s+budget|"
    r"budget\s+total)|"
    r"(?:\u603b\u9884\u7b97|\u9884\u7b97\u603b\u8ba1|total\s+budget|"
    r"budget\s+total).{0,80}"
    r"(?:\u6536\u7d27|\u8c03\u6574|\u964d\u81f3|\u6539\u4e3a|"
    r"reduced|adjusted|changed)"
    r")"
)


def _load_verifier(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load acceptance verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514R_VERIFY = _load_verifier(
    "v514r_verify",
    V514R_DIR / "verify_acceptance.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the Provider regression after superseded numeric "
            "constraint resolution."
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
    report = V514R_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = [
        item
        for item in report.get("checks", [])
        if str(item.get("name") or "")
        != "formal_run_descends_from_v514q_release"
    ]
    preregistration = dict(preflight_report.get("preregistration") or {})
    lineage = dict(preregistration.get("source_lineage") or {})
    thresholds = dict(preregistration.get("thresholds") or {})

    mechanism_commit = str(
        lineage.get("mechanism_release_commit") or ""
    ).lower()
    run_commit = _read_text(run_root / "system" / "git-commit.txt").lower()
    checks.append(
        _check(
            "formal_run_descends_from_v514s_release",
            bool(mechanism_commit)
            and bool(run_commit)
            and _is_ancestor(mechanism_commit, run_commit),
            f"mechanism={mechanism_commit};run={run_commit}",
        )
    )
    checks.append(
        _hash_check(
            "v514r_preregistration_is_bound",
            str(lineage.get("v514r_preregistration_sha256") or ""),
            V514R_DIR / "preregistration.json",
        )
    )
    checks.append(
        _hash_check(
            "v514s_acceptance_verifier_is_bound",
            str(lineage.get("v514s_acceptance_verifier_sha256") or ""),
            V514S_DIR / "verify_acceptance.py",
        )
    )

    observed: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    for spec in preregistration.get("scenarios", []):
        scenario_id = str(spec.get("scenario_id") or "")
        directory = str(spec.get("directory") or "")
        sequence = _read_json(
            run_root / directory / "managed" / "sequence_result.json"
        )
        for task in sequence.get("tasks", []):
            task_id = str(task.get("task_id") or "")
            final_answer = str(task.get("final_answer") or "")
            messages = "\n".join(
                str(message.get("content") or "")
                for message in task.get("messages", [])
                if isinstance(message, dict)
            )
            if _HISTORICAL_CONSTRAINT_RE.search(
                f"{final_answer}\n{messages}"
            ):
                observed.append(
                    {"scenario_id": scenario_id, "task_id": task_id}
                )
            reasons = {
                str(item)
                for item in task.get("delivery_guard_reasons", [])
            }
            if "numeric_upper_bound_violation" in reasons:
                rejected.append(
                    {"scenario_id": scenario_id, "task_id": task_id}
                )

    minimum_observed = int(
        thresholds.get("superseded_constraint_candidate_count_min", 1)
    )
    checks.append(
        _check(
            "superseded_constraint_is_observed_not_assumed",
            len(observed) >= minimum_observed,
            f"observed={len(observed)};minimum={minimum_observed}",
        )
    )
    checks.append(
        _check(
            "managed_has_no_numeric_upper_bound_false_rejection",
            not rejected,
            json.dumps(rejected, ensure_ascii=False),
        )
    )

    passed_count = sum(bool(item.get("passed")) for item in checks)
    report["checks"] = checks
    report["superseded_constraint"] = {
        "observed_count": len(observed),
        "observed_tasks": observed,
        "rejected_count": len(rejected),
        "rejected_tasks": rejected,
    }
    report["summary"].update(
        {
            "passed": passed_count == len(checks),
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "superseded_constraint_formal_passed": (
                passed_count == len(checks)
            ),
            "superseded_constraint_candidate_count": len(observed),
            "numeric_upper_bound_rejection_count": len(rejected),
        }
    )
    return report


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
    evidence = dict(report.get("superseded_constraint") or {})
    failed = [item for item in report["checks"] if not item["passed"]]
    lines = [
        "# v5.14t Superseded Constraint Formal Regression",
        "",
        f"- Passed: `{summary.get('passed')}`",
        (
            f"- Checks: `{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        (
            "- Superseded constraint candidates: "
            f"`{evidence.get('observed_count', 0)}`"
        ),
        (
            "- Numeric upper-bound rejections: "
            f"`{evidence.get('rejected_count', 0)}`"
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
            "- Tasks, Agents, model, temperature, turns, judges, and thresholds "
            "are inherited unchanged from v5.14r.",
            "- The production guard contains no scenario, domain, or fixed-role "
            "special cases.",
            "- Historical values remain auditable; current violations remain "
            "blocking.",
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
