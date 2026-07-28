from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
V514X_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14x-evidence-fidelity-formal-regression"
)
V514Y_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14y-active-fact-terminal-delivery"
)


def _load_verifier(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load acceptance verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514X_VERIFY = _load_verifier(
    "v514x_verify_for_v514z",
    V514X_DIR / "verify_acceptance.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the formal Provider regression after active-fact and "
            "terminal-delivery governance."
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
    report = V514X_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = list(report.get("checks", []))
    preregistration = dict(preflight_report.get("preregistration") or {})
    lineage = dict(preregistration.get("source_lineage") or {})

    mechanism_commit = str(
        lineage.get("mechanism_release_commit") or ""
    ).lower()
    run_commit = _read_text(run_root / "system" / "git-commit.txt").lower()
    checks.extend(
        [
            _check(
                "formal_run_descends_from_v514y_release",
                bool(mechanism_commit)
                and bool(run_commit)
                and _is_ancestor(mechanism_commit, run_commit),
                f"mechanism={mechanism_commit};run={run_commit}",
            ),
            _hash_check(
                "v514x_preregistration_is_bound",
                str(
                    lineage.get("v514x_preregistration_sha256") or ""
                ),
                V514X_DIR / "preregistration.json",
            ),
            _hash_check(
                "v514y_acceptance_verifier_is_bound",
                str(
                    lineage.get("v514y_acceptance_verifier_sha256") or ""
                ),
                V514Y_DIR / "verify_acceptance.py",
            ),
        ]
    )

    passed_count = sum(bool(item.get("passed")) for item in checks)
    report["checks"] = checks
    report["summary"].update(
        {
            "passed": passed_count == len(checks),
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "active_fact_terminal_delivery_formal_passed": (
                passed_count == len(checks)
            ),
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
    failed = [item for item in report["checks"] if not item["passed"]]
    lines = [
        "# v5.14z Active Fact and Terminal Delivery Formal Regression",
        "",
        f"- Passed: `{summary.get('passed')}`",
        (
            f"- Checks: `{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
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
            "- Frozen tasks, capability-based Agents, model, temperature, "
            "turns, judges, and thresholds are unchanged from v5.14x.",
            "- The run must descend from the v5.14y release and bind both "
            "the v5.14x preregistration and v5.14y mechanism verifier.",
            "- Production code contains no scenario, entity, conclusion, or "
            "fixed-role special case.",
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
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
