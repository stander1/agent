from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_runtime import __version__ as RUNTIME_VERSION
from agent_runtime.drivers.autogen import DRIVER_PHASE

from release_gate_evidence import (
    assess_team_takeover,
    classify_team_takeover_path,
    collect_team_takeover_evidence,
)
from release_package_contract import (
    DIST_INFO_PREFIX,
    FORBIDDEN_DISTRIBUTION_PREFIXES,
    PACKAGE_NAME,
    REQUIRED_WHEEL_MEMBERS,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEAM_BENCHMARK_APP = PROJECT_ROOT / "examples" / "autogen_team_benchmark_app.py"
MIXED_TEAM_CORE_APP = PROJECT_ROOT / "examples" / "autogen_mixed_team_core_smoke.py"
PACKAGE_VERSION = RUNTIME_VERSION
WHEEL_PREFIX = "multi_agent_collaboration_runtime-"


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the AgentLite wheel and verify the installed package in an "
            "isolated virtual environment."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--skip-installed-autogen-smoke",
        action="store_true",
        help="Only build and inspect the wheel; skip installed AutoGen rewrite smoke.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = PROJECT_ROOT / "runs" / f"package-release-gate-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    _mkdir(output_dir)

    steps: list[dict[str, Any]] = []
    wheelhouse = output_dir / "wheelhouse"
    _mkdir(wheelhouse)
    steps.append(build_wheel(output_dir=output_dir, wheelhouse=wheelhouse, python=args.python))
    wheel_path = _find_built_wheel(wheelhouse)
    inspect_step = inspect_wheel(wheel_path)
    steps.append(inspect_step)
    if wheel_path and not args.skip_installed_autogen_smoke:
        steps.append(
            verify_installed_wheel(
                wheel_path=wheel_path,
                output_dir=output_dir / "installed_wheel",
                python=args.python,
            )
        )

    report = {
        "passed": all(bool(step.get("passed")) for step in steps),
        "output_dir": str(output_dir),
        "wheel_path": str(wheel_path) if wheel_path else "",
        "steps": steps,
    }
    report_path = output_dir / "package_release_gate_report.json"
    _write_text(
        report_path,
        json.dumps(report, ensure_ascii=False, indent=2),
    )
    markdown_path = output_dir / "package_release_gate_report.md"
    _write_text(markdown_path, render_markdown(report))
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def build_wheel(*, output_dir: Path, wheelhouse: Path, python: str) -> dict[str, Any]:
    wheelhouse_literal = json.dumps(str(wheelhouse))
    build_script = "\n".join(
        [
            "from pathlib import Path",
            "import setuptools.build_meta as build_meta",
            f"wheelhouse = Path({wheelhouse_literal})",
            "wheelhouse.mkdir(parents=True, exist_ok=True)",
            "print(build_meta.build_wheel(str(wheelhouse)))",
        ]
    )
    return run_command(
        name="build_meta_wheel",
        command=[python, "-c", build_script],
        output_dir=output_dir,
    )


def inspect_wheel(wheel_path: Path | None) -> dict[str, Any]:
    if wheel_path is None:
        return {
            "name": "inspect_wheel",
            "passed": False,
            "error": "no wheel was built",
        }
    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())
        metadata_name = _single_dist_info_member(names, "METADATA")
        entry_points_name = _single_dist_info_member(names, "entry_points.txt")
        metadata = archive.read(metadata_name).decode("utf-8", errors="replace")
        entry_points = archive.read(entry_points_name).decode(
            "utf-8",
            errors="replace",
        )
    missing_members = sorted(REQUIRED_WHEEL_MEMBERS - names)
    forbidden_members = sorted(
        name
        for name in names
        if name.startswith(FORBIDDEN_DISTRIBUTION_PREFIXES)
    )
    project_metadata = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    project_version = str(project_metadata.get("project", {}).get("version", ""))
    metadata_checks = {
        "name": f"Name: {PACKAGE_NAME}" in metadata,
        "version_matches_runtime": f"Version: {PACKAGE_VERSION}" in metadata,
        "source_versions_match": project_version == PACKAGE_VERSION,
        "requires_tiktoken": "Requires-Dist: tiktoken" in metadata,
        "requires_python": "Requires-Python: >=3.11" in metadata,
    }
    entry_point_ok = "agentlite = agent_runtime.cli:main" in entry_points
    passed = (
        not missing_members
        and not forbidden_members
        and all(metadata_checks.values())
        and entry_point_ok
    )
    return {
        "name": "inspect_wheel",
        "passed": passed,
        "wheel_path": str(wheel_path),
        "required_member_count": len(REQUIRED_WHEEL_MEMBERS),
        "missing_members": missing_members,
        "forbidden_members": forbidden_members,
        "metadata_checks": metadata_checks,
        "project_version": project_version,
        "runtime_version": PACKAGE_VERSION,
        "entry_point_ok": entry_point_ok,
    }


def verify_installed_wheel(
    *,
    wheel_path: Path,
    output_dir: Path,
    python: str,
) -> dict[str, Any]:
    _rmtree(output_dir)
    _mkdir(output_dir)
    target_site = output_dir / "target_site"
    install_env = _target_install_env(target_site)
    steps = [
        run_command(
            name="pip_install_wheel_target_no_deps",
            command=[
                python,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--target",
                str(target_site),
                str(wheel_path),
            ],
            output_dir=output_dir,
        ),
        run_command(
            name="installed_version",
            command=[python, "-m", "agent_runtime.cli", "version"],
            output_dir=output_dir,
            cwd=output_dir,
            env=install_env,
        ),
        run_command(
            name="installed_import_path",
            command=[
                python,
                "-c",
                (
                    "import json, agent_runtime; "
                    "print(json.dumps({'version': agent_runtime.__version__, "
                    "'file': agent_runtime.__file__}, ensure_ascii=True))"
                ),
            ],
            output_dir=output_dir,
            cwd=output_dir,
            env=install_env,
        ),
        run_command(
            name="installed_doctor_autogen",
            command=[
                python,
                "-m",
                "agent_runtime.cli",
                "doctor",
                "--framework",
                "autogen",
                "--json",
            ],
            output_dir=output_dir,
            cwd=output_dir,
            env=install_env,
        ),
        run_installed_cli_rewrite_smoke(
            output_dir=output_dir / "installed_cli_rewrite",
            python=python,
            env=install_env,
        ),
        run_installed_cli_mixed_team_core_smoke(
            output_dir=output_dir / "installed_cli_mixed_team_core",
            python=python,
            env=install_env,
        ),
    ]
    import_payload = _json_from_step_stdout(steps[2])
    doctor_payload = _json_from_step_stdout(steps[3])
    path_ok = _path_is_under(
        Path(str(import_payload.get("file", ""))),
        target_site,
    )
    doctor_ok = bool(doctor_payload.get("ok"))
    return {
        "name": "verify_installed_wheel",
        "passed": (
            all(bool(step.get("passed")) for step in steps)
            and path_ok
            and doctor_ok
        ),
        "target_site": str(target_site),
        "installed_import_path": import_payload,
        "doctor_ok": doctor_ok,
        "import_path_uses_target_site": path_ok,
        "steps": steps,
    }


def run_installed_cli_rewrite_smoke(
    *,
    output_dir: Path,
    python: str,
    env: dict[str, str],
) -> dict[str, Any]:
    _mkdir(output_dir)
    app_output = output_dir / "autogen_team_benchmark_installed_cli_output.json"
    smoke_env = {
        **env,
        "AGENTLITE_AUTOGEN_TEAM_BENCHMARK_MODE": "installed_wheel_cli",
        "AGENTLITE_AUTOGEN_TEAM_BENCHMARK_OUTPUT": str(app_output),
    }
    step = run_command(
        name="installed_agentlite_cli_rewrite",
        command=[
            python,
            "-m",
            "agent_runtime.cli",
            "run",
            "--framework",
            "autogen",
            "--cwd",
            str(output_dir),
            "--data-dir",
            str(output_dir / "agentlite"),
            "--rewrite",
            "all",
            "--",
            python,
            str(TEAM_BENCHMARK_APP),
        ],
        output_dir=output_dir,
        cwd=output_dir,
        env=smoke_env,
    )
    payload = _load_json(app_output)
    first = _first_stream_item(payload)
    evidence = collect_team_takeover_evidence(output_dir / "agentlite")
    takeover_path = classify_team_takeover_path(evidence)
    takeover_checks = assess_team_takeover(
        evidence=evidence,
        app_payload=payload,
        first_stream_item=first,
        expected_phase=DRIVER_PHASE,
    )
    rewrite_ok = all(takeover_checks.values())
    step.update(
        {
            "passed": bool(step.get("passed")) and rewrite_ok,
            "app_output_path": str(app_output),
            "agentlite_active": bool(payload.get("agentlite_active")),
            "team_takeover_path": takeover_path,
            "takeover_checks": takeover_checks,
            "takeover_evidence": evidence,
            "first_stream_item": {
                "contains_team_rewrite_marker": bool(
                    first.get("contains_team_rewrite_marker")
                ),
                "contains_state_pool_marker": bool(
                    first.get("contains_state_pool_marker")
                ),
                "contains_broadcast_manifest": bool(
                    first.get("contains_broadcast_manifest")
                ),
                "contains_receiver_prompt_views": bool(
                    first.get("contains_receiver_prompt_views")
                ),
                "native_marker_count": int(first.get("native_marker_count", 0) or 0),
            },
        }
    )
    return step


def run_installed_cli_mixed_team_core_smoke(
    *,
    output_dir: Path,
    python: str,
    env: dict[str, str],
) -> dict[str, Any]:
    _mkdir(output_dir)
    app_output = output_dir / "autogen_mixed_team_core_installed_cli_output.json"
    smoke_env = {
        **env,
        "AGENTLITE_AUTOGEN_MIXED_TEAM_CORE_OUTPUT": str(app_output),
    }
    step = run_command(
        name="installed_agentlite_cli_mixed_team_core",
        command=[
            python,
            "-m",
            "agent_runtime.cli",
            "autogen",
            "--cwd",
            str(output_dir),
            "--data-dir",
            str(output_dir / "agentlite"),
            "--",
            python,
            str(MIXED_TEAM_CORE_APP),
        ],
        output_dir=output_dir,
        cwd=output_dir,
        env=smoke_env,
    )
    payload = _load_json(app_output)
    bridge_seen = _first_list_dict(payload.get("bridge_seen_messages", []))
    core_received = _first_list_dict(payload.get("core_received", []))
    core_reply = _first_list_dict(payload.get("core_caller_replies", []))
    final_message = _last_task_message(payload.get("task_result", {}))
    takeover_evidence = collect_team_takeover_evidence(output_dir / "agentlite")
    takeover_path = classify_team_takeover_path(takeover_evidence)
    rewritten_team_message = (
        bool(bridge_seen.get("contains_team_rewrite_marker"))
        and bool(bridge_seen.get("contains_state_pool_marker"))
        and bool(bridge_seen.get("contains_broadcast_manifest"))
        and bool(bridge_seen.get("contains_receiver_prompt_views"))
        and not bool(bridge_seen.get("contains_team_native_marker"))
    )
    cost_guarded_native_message = (
        takeover_path == "cost_guarded_fallback"
        and not bool(bridge_seen.get("contains_team_rewrite_marker"))
        and not bool(bridge_seen.get("contains_state_pool_marker"))
        and not bool(bridge_seen.get("contains_broadcast_manifest"))
        and not bool(bridge_seen.get("contains_receiver_prompt_views"))
        and bool(bridge_seen.get("contains_team_native_marker"))
    )
    mixed_ok = (
        bool(payload.get("agentlite_active"))
        and takeover_path in {"applied_rewrite", "cost_guarded_fallback"}
        and (rewritten_team_message or cost_guarded_native_message)
        and bool(core_received.get("agentlite_prompt_view"))
        and not bool(core_received.get("contains_core_request_native_marker"))
        and not bool(core_received.get("contains_core_rewrite_marker"))
        and bool(core_reply.get("agentlite_prompt_view"))
        and not bool(core_reply.get("contains_core_reply_native_marker"))
        and not bool(core_reply.get("contains_core_rewrite_marker"))
        and bool(final_message.get("contains_done_token"))
        and not bool(final_message.get("contains_team_rewrite_marker"))
        and not bool(final_message.get("contains_state_pool_marker"))
        and not bool(final_message.get("contains_broadcast_manifest"))
    )
    step.update(
        {
            "passed": bool(step.get("passed")) and mixed_ok,
            "app_output_path": str(app_output),
            "agentlite_active": bool(payload.get("agentlite_active")),
            "team_takeover_path": takeover_path,
            "takeover_evidence": takeover_evidence,
            "bridge_seen_first_message": {
                "contains_team_rewrite_marker": bool(
                    bridge_seen.get("contains_team_rewrite_marker")
                ),
                "contains_state_pool_marker": bool(
                    bridge_seen.get("contains_state_pool_marker")
                ),
                "contains_broadcast_manifest": bool(
                    bridge_seen.get("contains_broadcast_manifest")
                ),
                "contains_receiver_prompt_views": bool(
                    bridge_seen.get("contains_receiver_prompt_views")
                ),
                "contains_team_native_marker": bool(
                    bridge_seen.get("contains_team_native_marker")
                ),
            },
            "core_received_first": {
                "message_type": core_received.get("message_type", ""),
                "agentlite_prompt_view": bool(
                    core_received.get("agentlite_prompt_view")
                ),
                "contains_core_request_native_marker": bool(
                    core_received.get("contains_core_request_native_marker")
                ),
                "contains_core_rewrite_marker": bool(
                    core_received.get("contains_core_rewrite_marker")
                ),
            },
            "core_caller_reply_first": {
                "message_type": core_reply.get("message_type", ""),
                "agentlite_prompt_view": bool(core_reply.get("agentlite_prompt_view")),
                "contains_core_reply_native_marker": bool(
                    core_reply.get("contains_core_reply_native_marker")
                ),
                "contains_core_rewrite_marker": bool(
                    core_reply.get("contains_core_rewrite_marker")
                ),
            },
            "final_message": {
                "contains_done_token": bool(final_message.get("contains_done_token")),
                "contains_team_rewrite_marker": bool(
                    final_message.get("contains_team_rewrite_marker")
                ),
                "contains_state_pool_marker": bool(
                    final_message.get("contains_state_pool_marker")
                ),
                "contains_broadcast_manifest": bool(
                    final_message.get("contains_broadcast_manifest")
                ),
            },
        }
    )
    return step


def run_command(
    *,
    name: str,
    command: list[str],
    output_dir: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd or PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    step_dir = output_dir / "steps"
    _mkdir(step_dir)
    stdout_path = step_dir / f"{name}.stdout.txt"
    stderr_path = step_dir / f"{name}.stderr.txt"
    stdout_text = completed.stdout or ""
    stderr_text = completed.stderr or ""
    _write_text(stdout_path, stdout_text)
    _write_text(stderr_path, stderr_text)
    return {
        "name": name,
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_preview": _preview(stdout_text),
        "stderr_preview": _preview(stderr_text),
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AgentLite Package Release Gate Report",
        "",
        f"passed: `{str(report.get('passed')).lower()}`",
        "",
        "## Steps",
        "",
    ]
    for step in report.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        lines.append(
            f"- `{step.get('name', '')}`: passed=`{str(step.get('passed')).lower()}`"
        )
        nested = step.get("steps", [])
        if isinstance(nested, list):
            for child in nested:
                if isinstance(child, dict):
                    lines.append(
                        f"  - `{child.get('name', '')}`: "
                        f"passed=`{str(child.get('passed')).lower()}`, "
                        f"returncode=`{child.get('returncode', '')}`"
                    )
    lines.extend(["", "## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    lines.append(f"- wheel_path: `{report.get('wheel_path', '')}`")
    return "\n".join(lines) + "\n"


def _find_built_wheel(wheelhouse: Path) -> Path | None:
    wheels = sorted(wheelhouse.glob(f"{WHEEL_PREFIX}*.whl"))
    return wheels[-1] if wheels else None


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

def _target_install_env(target_site: Path) -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    pythonpath = str(target_site)
    if existing:
        pythonpath = os.pathsep.join([pythonpath, existing])
    env["PYTHONPATH"] = pythonpath
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _path_is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _json_from_step_stdout(step: dict[str, Any]) -> dict[str, Any]:
    path_text = str(step.get("stdout_path", ""))
    if not path_text:
        return {}
    text = _read_text(path_text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_json(path: Path) -> dict[str, Any]:
    if not _exists(path):
        return {}
    payload = json.loads(_read_text(path))
    return payload if isinstance(payload, dict) else {}


def _first_stream_item(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("stream_items", []) if isinstance(payload, dict) else []
    first = items[0] if isinstance(items, list) and items else {}
    return first if isinstance(first, dict) else {}


def _first_list_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _last_task_message(task_result: Any) -> dict[str, Any]:
    if not isinstance(task_result, dict):
        return {}
    messages = task_result.get("messages", [])
    if isinstance(messages, list) and messages and isinstance(messages[-1], dict):
        return messages[-1]
    return {}


def _preview(text: str, limit: int = 600) -> str:
    return " ".join(str(text or "").split())[:limit]


if __name__ == "__main__":
    raise SystemExit(main())
