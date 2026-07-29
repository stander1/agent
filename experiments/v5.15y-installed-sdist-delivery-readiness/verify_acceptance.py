from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any


BASE_EXPERIMENT_DIR = (
    Path(__file__).resolve().parents[1]
    / "v5.15x-release-candidate-freeze"
)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_base_verifier() -> ModuleType:
    path = BASE_EXPERIMENT_DIR / "verify_acceptance.py"
    spec = importlib.util.spec_from_file_location("v515x_base_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load v5.15x base verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = _load_base_verifier()


def _load_release_contract() -> ModuleType:
    path = REPO_ROOT / "examples" / "release_package_contract.py"
    spec = importlib.util.spec_from_file_location(
        "v515y_release_package_contract",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load release package contract")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_release_contract()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _step(report: dict[str, Any], name: str) -> dict[str, Any]:
    for step in report.get("steps", []) or []:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    return {}


def build_report(
    *,
    base_report: dict[str, Any],
    artifact_report: dict[str, Any],
    project_metadata: dict[str, Any],
    delivery_guide: str,
) -> dict[str, Any]:
    version = str(project_metadata.get("version") or "")
    installed_sdist = _step(
        artifact_report,
        "verify_installed_sdist",
    )
    urls = project_metadata.get("urls", {})
    if not isinstance(urls, dict):
        urls = {}
    classifiers = {
        str(item) for item in project_metadata.get("classifiers", []) or []
    }
    required_classifiers = set(CONTRACT.REQUIRED_CLASSIFIERS)
    required_urls = dict(CONTRACT.REQUIRED_PROJECT_URLS)
    new_checks = [
        _check(
            "release_sdist_installed_outside_checkout",
            bool(installed_sdist.get("passed"))
            and installed_sdist.get("install_returncode") == 0
            and installed_sdist.get("probe_returncode") == 0
            and installed_sdist.get("cli_returncode") == 0
            and bool(installed_sdist.get("loaded_from_target"))
            and bool(installed_sdist.get("cli_version_ok"))
            and installed_sdist.get("runtime_version") == version
            and installed_sdist.get("distribution_version") == version
            and not installed_sdist.get("missing_members"),
            (
                f"passed={installed_sdist.get('passed')};"
                f"install={installed_sdist.get('install_returncode')};"
                f"probe={installed_sdist.get('probe_returncode')};"
                f"cli={installed_sdist.get('cli_returncode')};"
                f"target={installed_sdist.get('loaded_from_target')};"
                f"runtime={installed_sdist.get('runtime_version')};"
                f"distribution={installed_sdist.get('distribution_version')};"
                f"missing={installed_sdist.get('missing_members', [])}"
            ),
        ),
        _check(
            "release_package_discovery_metadata_complete",
            all(urls.get(name) == value for name, value in required_urls.items())
            and required_classifiers.issubset(classifiers),
            (
                f"urls={urls};"
                f"missing_classifiers="
                f"{sorted(required_classifiers - classifiers)}"
            ),
        ),
        _check(
            "current_competition_delivery_guide_complete",
            version in delivery_guide
            and "openEuler" in delivery_guide
            and "sdist 隔离安装" in delivery_guide
            and "SHA256" in delivery_guide
            and "公开发布阻塞项" in delivery_guide,
            (
                f"version={version};"
                f"size={len(delivery_guide)}"
            ),
        ),
    ]
    checks = [*base_report.get("checks", []), *new_checks]
    passed = all(bool(item.get("passed")) for item in checks)
    publication = base_report.get("publication", {})
    blockers = list(publication.get("blockers", []) or [])
    return {
        "schema_version": (
            "agentlite.v515y.installed-sdist-delivery-readiness.v1"
        ),
        "summary": {
            **base_report.get("summary", {}),
            "passed": passed,
            "technical_release_candidate_ready": passed,
            "open_source_publication_ready": passed and not blockers,
            "check_count": len(checks),
            "passed_check_count": sum(
                int(bool(item.get("passed"))) for item in checks
            ),
            "release_candidate_version": version,
            "installed_sdist_verified": bool(
                installed_sdist.get("passed")
            ),
        },
        "implementation_commit": base_report.get(
            "implementation_commit",
            "",
        ),
        "checks": checks,
        "publication": publication,
        "release_artifacts": base_report.get("release_artifacts", {}),
        "installed_sdist": installed_sdist,
        "package_metadata": {
            "version": version,
            "urls": urls,
            "classifiers": sorted(classifiers),
        },
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15y Installed sdist and Delivery Readiness",
        "",
        f"- technical gate passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        f"- version: `{summary['release_candidate_version']}`",
        (
            "- installed sdist verified: "
            f"`{summary['installed_sdist_verified']}`"
        ),
        (
            "- open-source publication ready: "
            f"`{summary['open_source_publication_ready']}`"
        ),
        (
            "- publication blockers: "
            f"`{report['publication'].get('blockers', [])}`"
        ),
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
    parser.add_argument("--base-report", type=Path, required=True)
    parser.add_argument("--artifact-report", type=Path, required=True)
    parser.add_argument("--pyproject", type=Path, required=True)
    parser.add_argument("--delivery-guide", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()
    base_report = _load(args.base_report)
    artifact_report = _load(args.artifact_report)
    pyproject = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))
    project_metadata = pyproject.get("project", {})
    if not isinstance(project_metadata, dict):
        raise ValueError("pyproject.toml project must be a table")
    report = build_report(
        base_report=base_report,
        artifact_report=artifact_report,
        project_metadata=project_metadata,
        delivery_guide=args.delivery_guide.read_text(encoding="utf-8"),
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
