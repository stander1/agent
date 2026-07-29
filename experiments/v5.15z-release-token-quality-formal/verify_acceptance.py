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
V514Z_DIR = (
    REPO_ROOT
    / "experiments"
    / "v5.14z-active-fact-terminal-delivery-formal"
)


def _load_verifier(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load acceptance verifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V514Z_VERIFY = _load_verifier(
    "v514z_verify_for_v515z",
    V514Z_DIR / "verify_acceptance.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify a v0.5.15 release token-quality repeat."
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
    report = V514Z_VERIFY.evaluate(
        run_root=run_root,
        preflight_report=preflight_report,
    )
    checks = list(report.get("checks", []))
    preregistration = dict(preflight_report.get("preregistration") or {})
    lineage = dict(preregistration.get("source_lineage") or {})

    release_commit = str(lineage.get("mechanism_release_commit") or "").lower()
    release_tag = str(lineage.get("release_tag") or "")
    run_commit = _read_text(run_root / "system" / "git-commit.txt").lower()
    allowed_prefixes = tuple(
        str(item)
        for item in lineage.get("allowed_post_release_paths") or []
        if str(item)
    )
    changed_paths = _changed_paths(release_commit, run_commit)
    disallowed_paths = [
        path
        for path in changed_paths
        if not any(path.startswith(prefix) for prefix in allowed_prefixes)
    ]
    resolved_tag = _rev_parse(f"{release_tag}^{{commit}}")

    checks.extend(
        [
            _check(
                "release_tag_resolves_to_preregistered_commit",
                bool(release_tag)
                and bool(release_commit)
                and resolved_tag == release_commit,
                (
                    f"tag={release_tag};expected={release_commit};"
                    f"actual={resolved_tag}"
                ),
            ),
            _check(
                "formal_run_descends_from_release_commit",
                bool(release_commit)
                and bool(run_commit)
                and _is_ancestor(release_commit, run_commit),
                f"release={release_commit};run={run_commit}",
            ),
            _check(
                "post_release_diff_is_experiment_only",
                bool(allowed_prefixes) and not disallowed_paths,
                (
                    f"allowed={list(allowed_prefixes)};"
                    f"changed={changed_paths};disallowed={disallowed_paths}"
                ),
            ),
            _hash_check(
                "v514z_preregistration_is_bound",
                str(lineage.get("v514z_preregistration_sha256") or ""),
                V514Z_DIR / "preregistration.json",
            ),
            _hash_check(
                "v514z_acceptance_verifier_is_bound",
                str(lineage.get("v514z_acceptance_verifier_sha256") or ""),
                V514Z_DIR / "verify_acceptance.py",
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
            "release_token_quality_formal_passed": (
                passed_count == len(checks)
            ),
            "release_commit": release_commit,
            "run_commit": run_commit,
        }
    )
    return report


def _changed_paths(base: str, head: str) -> list[str]:
    if not base or not head:
        return []
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}..{head}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ["<git-diff-failed>"]
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _rev_parse(ref: str) -> str:
    if not ref:
        return ""
    result = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip().lower() if result.returncode == 0 else ""


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


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
        "# v5.15z Release Token and Quality Formal Benchmark",
        "",
        f"- Passed: `{summary.get('passed')}`",
        (
            f"- Checks: `{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        f"- Release commit: `{summary.get('release_commit')}`",
        f"- Harness commit: `{summary.get('run_commit')}`",
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
            "- Production source is the exact v0.5.15 release tree.",
            "- Post-release changes are limited to this experiment harness.",
            "- Frozen tasks, Agents, Provider parameters, judges, and "
            "thresholds are unchanged from v5.14z.",
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
