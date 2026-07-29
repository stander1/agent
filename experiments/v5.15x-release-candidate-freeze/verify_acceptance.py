from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


BASE_EXPERIMENT_DIR = (
    Path(__file__).resolve().parents[1]
    / "v5.15w-package-release-hardening"
)


def _load_base_verifier() -> ModuleType:
    path = BASE_EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515w_base_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15w base verifier")
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _step(report: dict[str, Any], name: str) -> dict[str, Any]:
    for step in report.get("steps", []) or []:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    return {}


def build_report(
    *,
    base_report: dict[str, Any],
    artifact_report: dict[str, Any],
    actual_artifact_sha256: dict[str, str],
    license_present: bool,
) -> dict[str, Any]:
    wheel_step = _step(artifact_report, "inspect_wheel")
    sdist_step = _step(artifact_report, "inspect_sdist")
    artifacts = artifact_report.get("artifacts", {})
    if not isinstance(artifacts, dict):
        artifacts = {}
    reported_hashes = {
        name: str((artifacts.get(name) or {}).get("sha256") or "")
        for name in ("wheel", "sdist")
    }
    version = str(artifact_report.get("version") or "")
    runtime_version = str(artifact_report.get("runtime_version") or "")
    artifact_checks = [
        _check(
            "release_candidate_artifact_builder_passed",
            bool(artifact_report.get("passed")),
            f"passed={artifact_report.get('passed')}",
        ),
        _check(
            "release_candidate_version_identity",
            bool(re.fullmatch(r"\d+\.\d+\.\d+rc\d+", version))
            and version == runtime_version
            and bool(artifact_report.get("source_versions_match")),
            (
                f"project={version};runtime={runtime_version};"
                f"match={artifact_report.get('source_versions_match')}"
            ),
        ),
        _check(
            "release_wheel_contract_passed",
            bool(wheel_step.get("passed"))
            and not wheel_step.get("missing_members")
            and not wheel_step.get("forbidden_members"),
            (
                f"passed={wheel_step.get('passed')};"
                f"missing={wheel_step.get('missing_members', [])};"
                f"forbidden={wheel_step.get('forbidden_members', [])}"
            ),
        ),
        _check(
            "release_sdist_contract_passed",
            bool(sdist_step.get("passed"))
            and not sdist_step.get("missing_members")
            and not sdist_step.get("forbidden_members"),
            (
                f"passed={sdist_step.get('passed')};"
                f"missing={sdist_step.get('missing_members', [])};"
                f"forbidden={sdist_step.get('forbidden_members', [])}"
            ),
        ),
        _check(
            "release_artifact_hashes_verified",
            all(
                len(actual_artifact_sha256.get(name, "")) == 64
                and actual_artifact_sha256.get(name)
                == reported_hashes.get(name)
                for name in ("wheel", "sdist")
            ),
            (
                f"reported={reported_hashes};"
                f"actual={actual_artifact_sha256}"
            ),
        ),
    ]
    checks = [*base_report.get("checks", []), *artifact_checks]
    passed = all(item.get("passed") for item in checks)
    publication_blockers = (
        [] if license_present else ["explicit_repository_license_missing"]
    )
    return {
        "schema_version": "agentlite.v515x.release-candidate-freeze.v1",
        "summary": {
            **base_report.get("summary", {}),
            "passed": passed,
            "technical_release_candidate_ready": passed,
            "open_source_publication_ready": passed
            and not publication_blockers,
            "check_count": len(checks),
            "passed_check_count": sum(
                int(bool(item.get("passed"))) for item in checks
            ),
            "release_candidate_version": version,
            "publication_blocker_count": len(publication_blockers),
        },
        "implementation_commit": base_report.get(
            "implementation_commit", ""
        ),
        "checks": checks,
        "release_artifacts": {
            "report": artifacts,
            "actual_sha256": actual_artifact_sha256,
        },
        "publication": {
            "license_present": license_present,
            "blockers": publication_blockers,
        },
        "package_release": {
            "wheel": base_report.get("wheel", {}),
        },
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15x Release Candidate Freeze",
        "",
        f"- technical gate passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        f"- version: `{summary['release_candidate_version']}`",
        (
            "- open-source publication ready: "
            f"`{summary['open_source_publication_ready']}`"
        ),
        (
            "- publication blockers: "
            f"`{report['publication']['blockers']}`"
        ),
        "",
        "## Artifacts",
        "",
        "```json",
        json.dumps(
            report["release_artifacts"],
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


def _license_present(repo_root: Path) -> bool:
    names = (
        "LICENSE",
        "LICENSE.txt",
        "LICENSE.md",
        "COPYING",
        "COPYING.txt",
    )
    return any((repo_root / name).is_file() for name in names)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--package-report", type=Path, required=True)
    parser.add_argument("--release-report", type=Path, required=True)
    parser.add_argument("--artifact-report", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    package_report = _load(args.package_report)
    release_report = _load(args.release_report)
    artifact_report = _load(args.artifact_report)
    package_wheel = Path(str(package_report.get("wheel_path") or ""))
    package_members: set[str] = set()
    if package_wheel.is_file():
        import zipfile

        with zipfile.ZipFile(package_wheel) as archive:
            package_members = set(archive.namelist())
    project_version = str(
        (artifact_report.get("version") or "")
    )
    runtime_version = str(
        (artifact_report.get("runtime_version") or "")
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=args.repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    base_report = BASE.build_report(
        implementation_commit=commit,
        project_version=project_version,
        runtime_version=runtime_version,
        package_report=package_report,
        release_report=release_report,
        wheel_path=package_wheel,
        wheel_members=package_members,
        wheel_sha256=_sha256(package_wheel) if package_wheel.is_file() else "",
    )
    actual_hashes: dict[str, str] = {}
    for name, key in (("wheel", "wheel_path"), ("sdist", "sdist_path")):
        path = Path(str(artifact_report.get(key) or ""))
        actual_hashes[name] = _sha256(path) if path.is_file() else ""
    report = build_report(
        base_report=base_report,
        artifact_report=artifact_report,
        actual_artifact_sha256=actual_hashes,
        license_present=_license_present(args.repo_root),
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
