from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
import zipfile
from pathlib import Path
from typing import Any


REQUIRED_SEMANTIC_MEMBERS = {
    "agent_runtime/bridge/state_memory_bridge.py",
    "agent_runtime/memory/claim_extractor.py",
    "agent_runtime/memory/conflict_resolver.py",
    "agent_runtime/memory/schema_registry.py",
    "agent_runtime/memory/semantic_disambiguator.py",
    "agent_runtime/reliability/final_delivery_guard.py",
    "agent_runtime/reliability/typed_events.py",
}
ACCEPTED_TAKEOVER_PATHS = {
    "applied_rewrite",
    "cost_guarded_fallback",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _step(report: dict[str, Any], name: str) -> dict[str, Any]:
    for step in report.get("steps", []) or []:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    return {}


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_report(
    *,
    implementation_commit: str,
    project_version: str,
    runtime_version: str,
    package_report: dict[str, Any],
    release_report: dict[str, Any],
    wheel_path: Path,
    wheel_members: set[str],
    wheel_sha256: str,
) -> dict[str, Any]:
    package_build = _step(package_report, "build_meta_wheel")
    package_inspect = _step(package_report, "inspect_wheel")
    installed = _step(package_report, "verify_installed_wheel")
    installed_steps = {
        str(step.get("name")): step
        for step in installed.get("steps", []) or []
        if isinstance(step, dict)
    }
    rewrite = installed_steps.get("installed_agentlite_cli_rewrite", {})
    mixed = installed_steps.get("installed_agentlite_cli_mixed_team_core", {})
    metadata_checks = package_inspect.get("metadata_checks", {})
    if not isinstance(metadata_checks, dict):
        metadata_checks = {}
    release_steps = {
        str(step.get("name")): step
        for step in release_report.get("steps", []) or []
        if isinstance(step, dict)
    }
    required_release_steps = {
        "cli_help",
        "cli_version",
        "doctor_autogen",
        "autogen_team_benchmark",
        "agentlite_cli_rewrite",
        "experiment_archive_binding",
        "autogen_studio_run_binding",
        "unittest",
        "compileall",
        "git_diff_check",
    }
    missing_semantic_members = sorted(
        REQUIRED_SEMANTIC_MEMBERS - wheel_members
    )
    missing_release_steps = sorted(
        required_release_steps - set(release_steps)
    )
    failed_release_steps = sorted(
        name
        for name in required_release_steps
        if name in release_steps and not release_steps[name].get("passed")
    )
    rewrite_path = str(rewrite.get("team_takeover_path") or "")
    mixed_path = str(mixed.get("team_takeover_path") or "")
    checks = [
        _check(
            "source_versions_match",
            bool(project_version)
            and project_version == runtime_version,
            f"project={project_version};runtime={runtime_version}",
        ),
        _check(
            "package_gate_passed",
            bool(package_report.get("passed")),
            f"passed={package_report.get('passed')}",
        ),
        _check(
            "wheel_build_passed",
            bool(package_build.get("passed")) and wheel_path.is_file(),
            f"passed={package_build.get('passed')};wheel={wheel_path}",
        ),
        _check(
            "wheel_inspection_passed",
            bool(package_inspect.get("passed"))
            and all(bool(value) for value in metadata_checks.values()),
            (
                f"passed={package_inspect.get('passed')};"
                f"metadata_checks={metadata_checks}"
            ),
        ),
        _check(
            "semantic_bridge_members_packaged",
            not missing_semantic_members,
            f"missing={missing_semantic_members}",
        ),
        _check(
            "installed_wheel_verified_outside_source_tree",
            bool(installed.get("passed"))
            and bool(installed.get("import_path_uses_target_site"))
            and bool(installed.get("doctor_ok")),
            (
                f"passed={installed.get('passed')};"
                f"target_site={installed.get('import_path_uses_target_site')};"
                f"doctor={installed.get('doctor_ok')}"
            ),
        ),
        _check(
            "installed_team_takeover_is_cost_safe",
            bool(rewrite.get("passed"))
            and rewrite_path in ACCEPTED_TAKEOVER_PATHS,
            f"passed={rewrite.get('passed')};path={rewrite_path}",
        ),
        _check(
            "installed_mixed_team_core_takeover_is_cost_safe",
            bool(mixed.get("passed"))
            and mixed_path in ACCEPTED_TAKEOVER_PATHS
            and bool(
                (mixed.get("core_received_first") or {}).get(
                    "agentlite_prompt_view"
                )
            )
            and bool(
                (mixed.get("core_caller_reply_first") or {}).get(
                    "agentlite_prompt_view"
                )
            ),
            f"passed={mixed.get('passed')};path={mixed_path}",
        ),
        _check(
            "complete_release_gate_passed",
            bool(release_report.get("passed"))
            and not missing_release_steps
            and not failed_release_steps,
            (
                f"passed={release_report.get('passed')};"
                f"missing={missing_release_steps};"
                f"failed={failed_release_steps}"
            ),
        ),
        _check(
            "wheel_sha256_recorded",
            len(wheel_sha256) == 64,
            f"sha256={wheel_sha256}",
        ),
    ]
    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "agentlite.v515w.package-release-acceptance.v1",
        "summary": {
            "passed": passed,
            "ready_for_release_artifact_freeze": passed,
            "check_count": len(checks),
            "passed_check_count": sum(
                int(item["passed"]) for item in checks
            ),
            "project_version": project_version,
            "runtime_version": runtime_version,
            "wheel_member_count": len(wheel_members),
            "semantic_member_count": len(REQUIRED_SEMANTIC_MEMBERS),
            "installed_team_takeover_path": rewrite_path,
            "installed_mixed_takeover_path": mixed_path,
        },
        "implementation_commit": implementation_commit,
        "wheel": {
            "path": str(wheel_path),
            "sha256": wheel_sha256,
            "member_count": len(wheel_members),
            "missing_semantic_members": missing_semantic_members,
        },
        "checks": checks,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15w Package Release Acceptance",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        f"- version: `{summary['runtime_version']}`",
        (
            "- installed Team path: "
            f"`{summary['installed_team_takeover_path']}`"
        ),
        (
            "- installed mixed path: "
            f"`{summary['installed_mixed_takeover_path']}`"
        ),
        f"- wheel SHA256: `{report['wheel']['sha256']}`",
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
    parser.add_argument("--package-report", type=Path, required=True)
    parser.add_argument("--release-report", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    package_report = _load(args.package_report)
    release_report = _load(args.release_report)
    wheel_path = Path(str(package_report.get("wheel_path") or ""))
    wheel_members: set[str] = set()
    wheel_sha256 = ""
    if wheel_path.is_file():
        with zipfile.ZipFile(wheel_path) as archive:
            wheel_members = set(archive.namelist())
        wheel_sha256 = _sha256(wheel_path)
    project = tomllib.loads(
        (args.repo_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    project_version = str(project.get("project", {}).get("version", ""))
    from agent_runtime import __version__ as runtime_version

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
        project_version=project_version,
        runtime_version=runtime_version,
        package_report=package_report,
        release_report=release_report,
        wheel_path=wheel_path,
        wheel_members=wheel_members,
        wheel_sha256=wheel_sha256,
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
