from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import setuptools.build_meta as build_meta
from agent_runtime import __version__ as RUNTIME_VERSION
from release_package_contract import (
    DIST_INFO_PREFIX,
    FORBIDDEN_DISTRIBUTION_PREFIXES,
    PACKAGE_NAME,
    REQUIRED_CLASSIFIERS,
    REQUIRED_LICENSE_EXPRESSION,
    REQUIRED_LICENSE_FILE,
    REQUIRED_PROJECT_URLS,
    REQUIRED_SDIST_MEMBERS,
    REQUIRED_WHEEL_MEMBERS,
    apache_license_text_valid,
    normalize_license_text,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and inspect AgentLite final release artifacts."
    )
    parser.add_argument("--dist-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument(
        "--keep-existing-dist",
        action="store_true",
        help="Do not remove the existing dist directory before building.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dist_dir = args.dist_dir.expanduser().resolve()
    report_dir = (
        args.report_dir.expanduser().resolve()
        if args.report_dir
        else PROJECT_ROOT
        / "runs"
        / f"release-artifacts-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    _assert_under_project(dist_dir)
    _mkdir(report_dir)
    if not args.keep_existing_dist:
        _rmtree(dist_dir)
    _mkdir(dist_dir)

    project = _load_project_metadata()
    artifacts = build_artifacts(dist_dir=dist_dir)
    cleanup_transient_build_outputs()
    wheel_path = dist_dir / artifacts["wheel"]
    sdist_path = dist_dir / artifacts["sdist"]
    steps = [
        inspect_wheel(wheel_path=wheel_path, expected_version=project["version"]),
        inspect_sdist(sdist_path=sdist_path, expected_version=project["version"]),
        verify_installed_sdist(
            sdist_path=sdist_path,
            expected_version=project["version"],
            output_dir=report_dir / "installed_sdist",
        ),
    ]
    report = {
        "passed": (
            project["version"] == RUNTIME_VERSION
            and all(bool(step.get("passed")) for step in steps)
        ),
        "package": project["name"],
        "version": project["version"],
        "runtime_version": RUNTIME_VERSION,
        "source_versions_match": project["version"] == RUNTIME_VERSION,
        "dist_dir": str(dist_dir),
        "wheel_path": str(wheel_path),
        "sdist_path": str(sdist_path),
        "artifacts": {
            "wheel": artifact_metadata(wheel_path),
            "sdist": artifact_metadata(sdist_path),
        },
        "steps": steps,
    }
    _write_text(report_dir / "release_artifacts_report.json", json.dumps(report, ensure_ascii=False, indent=2))
    _write_text(report_dir / "release_artifacts_report.md", render_markdown(report))
    print(f"Report: {report_dir / 'release_artifacts_report.json'}")
    print(f"Markdown: {report_dir / 'release_artifacts_report.md'}")
    return 0 if report["passed"] else 1


def build_artifacts(*, dist_dir: Path) -> dict[str, str]:
    cwd = Path.cwd()
    os.chdir(PROJECT_ROOT)
    try:
        sdist_name = build_meta.build_sdist(str(dist_dir))
        wheel_name = build_meta.build_wheel(str(dist_dir))
    finally:
        os.chdir(cwd)
    return {"sdist": sdist_name, "wheel": wheel_name}


def cleanup_transient_build_outputs() -> None:
    for path in (
        PROJECT_ROOT / "build",
        PROJECT_ROOT / "multi_agent_collaboration_runtime.egg-info",
    ):
        _rmtree(path)


def inspect_wheel(*, wheel_path: Path, expected_version: str) -> dict[str, Any]:
    if not _exists(wheel_path):
        return {"name": "inspect_wheel", "passed": False, "error": "missing wheel"}
    source_license = (
        _read_text(PROJECT_ROOT / REQUIRED_LICENSE_FILE)
        if _exists(PROJECT_ROOT / REQUIRED_LICENSE_FILE)
        else ""
    )
    with zipfile.ZipFile(_io_path(wheel_path)) as archive:
        names = set(archive.namelist())
        metadata_name = _single_dist_info_member(names, "METADATA")
        entry_points_name = _single_dist_info_member(names, "entry_points.txt")
        license_members = sorted(
            name
            for name in names
            if name.endswith(f".dist-info/licenses/{REQUIRED_LICENSE_FILE}")
        )
        metadata = archive.read(metadata_name).decode("utf-8", errors="replace")
        entry_points = archive.read(entry_points_name).decode("utf-8", errors="replace")
        packaged_license = (
            archive.read(license_members[0]).decode("utf-8", errors="replace")
            if len(license_members) == 1
            else ""
        )
    metadata_checks = {
        "name": f"Name: {PACKAGE_NAME}" in metadata,
        "version": f"Version: {expected_version}" in metadata,
        "requires_tiktoken": "Requires-Dist: tiktoken" in metadata,
        "requires_python": "Requires-Python: >=3.11" in metadata,
        "license_expression": (
            f"License-Expression: {REQUIRED_LICENSE_EXPRESSION}" in metadata
        ),
        "license_file": f"License-File: {REQUIRED_LICENSE_FILE}" in metadata,
        "project_urls": all(
            f"Project-URL: {name}, {url}" in metadata
            for name, url in REQUIRED_PROJECT_URLS.items()
        ),
        "classifiers": all(
            f"Classifier: {classifier}" in metadata
            for classifier in REQUIRED_CLASSIFIERS
        ),
    }
    license_checks = {
        "single_packaged_license": len(license_members) == 1,
        "source_is_apache_2_0": apache_license_text_valid(source_license),
        "packaged_is_apache_2_0": apache_license_text_valid(packaged_license),
        "packaged_matches_source": (
            bool(source_license)
            and normalize_license_text(packaged_license)
            == normalize_license_text(source_license)
        ),
    }
    missing_members = sorted(REQUIRED_WHEEL_MEMBERS - names)
    forbidden_members = _forbidden_members(names)
    entry_point_ok = "agentlite = agent_runtime.cli:main" in entry_points
    return {
        "name": "inspect_wheel",
        "passed": (
            not missing_members
            and not forbidden_members
            and all(metadata_checks.values())
            and all(license_checks.values())
            and entry_point_ok
        ),
        "missing_members": missing_members,
        "forbidden_members": forbidden_members,
        "metadata_checks": metadata_checks,
        "license_checks": license_checks,
        "license_members": license_members,
        "entry_point_ok": entry_point_ok,
    }


def inspect_sdist(*, sdist_path: Path, expected_version: str) -> dict[str, Any]:
    if not _exists(sdist_path):
        return {"name": "inspect_sdist", "passed": False, "error": "missing sdist"}
    source_license = (
        _read_text(PROJECT_ROOT / REQUIRED_LICENSE_FILE)
        if _exists(PROJECT_ROOT / REQUIRED_LICENSE_FILE)
        else ""
    )
    with tarfile.open(_io_path(sdist_path), mode="r:gz") as archive:
        raw_names = {member.name.replace("\\", "/") for member in archive.getmembers()}
        names = {_strip_sdist_root(name) for name in raw_names}
        pkg_info_name = next(
            (name for name in raw_names if name.endswith("/PKG-INFO")),
            "",
        )
        pkg_info = (
            archive.extractfile(pkg_info_name).read().decode("utf-8", errors="replace")
            if pkg_info_name
            else ""
        )
        license_name = next(
            (
                name
                for name in raw_names
                if _strip_sdist_root(name) == REQUIRED_LICENSE_FILE
            ),
            "",
        )
        license_member = archive.extractfile(license_name) if license_name else None
        packaged_license = (
            license_member.read().decode("utf-8", errors="replace")
            if license_member is not None
            else ""
        )
    metadata_checks = {
        "name": f"Name: {PACKAGE_NAME}" in pkg_info,
        "version": f"Version: {expected_version}" in pkg_info,
        "requires_python": "Requires-Python: >=3.11" in pkg_info,
        "license_expression": (
            f"License-Expression: {REQUIRED_LICENSE_EXPRESSION}" in pkg_info
        ),
        "license_file": f"License-File: {REQUIRED_LICENSE_FILE}" in pkg_info,
        "project_urls": all(
            f"Project-URL: {name}, {url}" in pkg_info
            for name, url in REQUIRED_PROJECT_URLS.items()
        ),
        "classifiers": all(
            f"Classifier: {classifier}" in pkg_info
            for classifier in REQUIRED_CLASSIFIERS
        ),
    }
    license_checks = {
        "source_is_apache_2_0": apache_license_text_valid(source_license),
        "packaged_is_apache_2_0": apache_license_text_valid(packaged_license),
        "packaged_matches_source": (
            bool(source_license)
            and normalize_license_text(packaged_license)
            == normalize_license_text(source_license)
        ),
    }
    missing_members = sorted(REQUIRED_SDIST_MEMBERS - names)
    forbidden_members = _forbidden_members(names)
    return {
        "name": "inspect_sdist",
        "passed": (
            not missing_members
            and not forbidden_members
            and all(metadata_checks.values())
            and all(license_checks.values())
        ),
        "missing_members": missing_members,
        "forbidden_members": forbidden_members,
        "metadata_checks": metadata_checks,
        "license_checks": license_checks,
    }


def verify_installed_sdist(
    *,
    sdist_path: Path,
    expected_version: str,
    output_dir: Path,
) -> dict[str, Any]:
    if not _exists(sdist_path):
        return {
            "name": "verify_installed_sdist",
            "passed": False,
            "error": "missing sdist",
        }
    _rmtree(output_dir)
    target_dir = output_dir / "target"
    work_dir = output_dir / "work"
    _mkdir(target_dir)
    _mkdir(work_dir)
    install_command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-deps",
        "--no-build-isolation",
        "--target",
        str(target_dir),
        str(sdist_path),
    ]
    install = subprocess.run(
        install_command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _write_text(output_dir / "pip-install.stdout.txt", install.stdout or "")
    _write_text(output_dir / "pip-install.stderr.txt", install.stderr or "")

    probe_payload: dict[str, Any] = {}
    probe_returncode: int | None = None
    probe_stdout = ""
    probe_stderr = ""
    cli_returncode: int | None = None
    cli_stdout = ""
    cli_stderr = ""
    if install.returncode == 0:
        probe_source = "\n".join(
            [
                "import json, sys",
                "from importlib.metadata import version",
                "from pathlib import Path",
                f"target = Path({json.dumps(str(target_dir))}).resolve()",
                "sys.path.insert(0, str(target))",
                "import agent_runtime",
                "module_file = Path(agent_runtime.__file__).resolve()",
                "print(json.dumps({",
                '    "runtime_version": agent_runtime.__version__,',
                f'    "distribution_version": version({json.dumps(PACKAGE_NAME)}),',
                '    "module_file": str(module_file),',
                '    "loaded_from_target": module_file.is_relative_to(target),',
                "}, sort_keys=True))",
            ]
        )
        probe = subprocess.run(
            [sys.executable, "-c", probe_source],
            cwd=work_dir,
            env=_isolated_probe_env(target_dir),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        probe_returncode = probe.returncode
        probe_stdout = probe.stdout or ""
        probe_stderr = probe.stderr or ""
        probe_payload = _last_json_object(probe_stdout)

        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_runtime.cli",
                "version",
            ],
            cwd=work_dir,
            env=_isolated_probe_env(target_dir),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        cli_returncode = cli.returncode
        cli_stdout = cli.stdout or ""
        cli_stderr = cli.stderr or ""

    _write_text(output_dir / "import-probe.stdout.txt", probe_stdout)
    _write_text(output_dir / "import-probe.stderr.txt", probe_stderr)
    _write_text(output_dir / "cli-version.stdout.txt", cli_stdout)
    _write_text(output_dir / "cli-version.stderr.txt", cli_stderr)
    target_root = _io_path(target_dir)
    installed_members = {
        path.relative_to(target_root).as_posix()
        for path in target_root.rglob("*")
        if path.is_file()
    }
    missing_members = sorted(REQUIRED_WHEEL_MEMBERS - installed_members)
    version_ok = (
        probe_payload.get("runtime_version") == expected_version
        and probe_payload.get("distribution_version") == expected_version
    )
    cli_version_ok = (
        cli_returncode == 0
        and cli_stdout.strip() == expected_version
    )
    return {
        "name": "verify_installed_sdist",
        "passed": (
            install.returncode == 0
            and probe_returncode == 0
            and bool(probe_payload.get("loaded_from_target"))
            and version_ok
            and cli_version_ok
            and not missing_members
        ),
        "install_returncode": install.returncode,
        "probe_returncode": probe_returncode,
        "cli_returncode": cli_returncode,
        "runtime_version": probe_payload.get("runtime_version", ""),
        "distribution_version": probe_payload.get(
            "distribution_version",
            "",
        ),
        "module_file": probe_payload.get("module_file", ""),
        "loaded_from_target": bool(
            probe_payload.get("loaded_from_target")
        ),
        "cli_version_ok": cli_version_ok,
        "missing_members": missing_members,
    }


def artifact_metadata(path: Path) -> dict[str, Any]:
    data = _io_path(path).read_bytes()
    return {
        "name": path.name,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AgentLite Final Release Artifacts",
        "",
        f"passed: `{str(report.get('passed')).lower()}`",
        f"package: `{report.get('package')}`",
        f"version: `{report.get('version')}`",
        f"runtime version: `{report.get('runtime_version')}`",
        (
            "source versions match: "
            f"`{str(report.get('source_versions_match')).lower()}`"
        ),
        "",
        "## Artifacts",
        "",
    ]
    for key in ("wheel", "sdist"):
        item = report.get("artifacts", {}).get(key, {})
        lines.append(
            f"- `{item.get('name')}`: size=`{item.get('size_bytes')}`, "
            f"sha256=`{item.get('sha256')}`"
        )
    lines.extend(["", "## Checks", ""])
    for step in report.get("steps", []):
        lines.append(f"- `{step.get('name')}`: passed=`{str(step.get('passed')).lower()}`")
        missing = step.get("missing_members") or []
        forbidden = step.get("forbidden_members") or []
        if missing:
            lines.append(f"  - missing: `{missing}`")
        if forbidden:
            lines.append(f"  - forbidden: `{forbidden}`")
    return "\n".join(lines) + "\n"


def _load_project_metadata() -> dict[str, str]:
    payload = tomllib.loads(_read_text(PROJECT_ROOT / "pyproject.toml"))
    project = payload.get("project", {})
    return {
        "name": str(project.get("name", "")),
        "version": str(project.get("version", "")),
    }


def _single_dist_info_member(names: set[str], basename: str) -> str:
    matches = sorted(
        name
        for name in names
        if name.endswith(f".dist-info/{basename}")
        and name.startswith(DIST_INFO_PREFIX)
    )
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {basename}, got {matches}")
    return matches[0]


def _strip_sdist_root(name: str) -> str:
    parts = name.split("/", 1)
    return parts[1] if len(parts) == 2 else name


def _forbidden_members(names: set[str]) -> list[str]:
    return sorted(
        name
        for name in names
        if name.startswith(FORBIDDEN_DISTRIBUTION_PREFIXES)
        or "/__pycache__/" in name
        or name.endswith((".pyc", ".pyo"))
    )


def _isolated_probe_env(target_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = str(target_dir)
    return env


def _last_json_object(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _assert_under_project(path: Path) -> None:
    try:
        path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"path must be under project root: {path}") from exc


def _io_path(path: Path | str) -> Path:
    candidate = Path(path)
    if os.name != "nt":
        return candidate
    raw = str(candidate)
    if raw.startswith("\\\\?\\"):
        return candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        resolved = candidate.absolute()
    resolved_raw = str(resolved)
    if resolved_raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + resolved_raw.lstrip("\\"))
    return Path("\\\\?\\" + resolved_raw)


def _mkdir(path: Path | str) -> None:
    _io_path(path).mkdir(parents=True, exist_ok=True)


def _write_text(path: Path | str, text: str) -> None:
    target = Path(path)
    _mkdir(target.parent)
    _io_path(target).write_text(text, encoding="utf-8")


def _read_text(path: Path | str) -> str:
    return _io_path(path).read_text(encoding="utf-8")


def _exists(path: Path | str) -> bool:
    return _io_path(path).exists()


def _rmtree(path: Path | str) -> None:
    if _exists(path):
        shutil.rmtree(_io_path(path))


if __name__ == "__main__":
    raise SystemExit(main())
