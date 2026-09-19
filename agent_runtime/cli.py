from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from agent_runtime.launcher import (
    LaunchRequest,
    ManagedProcessLauncher,
    read_bootstrap_status,
)


PACKAGE_NAME = "multi-agent-collaboration-runtime"
REWRITE_ENV_BY_PRESET = {
    "off": {
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "shadow-only",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE": "0",
        "AGENTLITE_AUTOGEN_HANDOFF_REWRITE": "0",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE": "0",
        "AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE": "0",
        "AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE": "off",
    },
    "agent": {
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE": "0",
        "AGENTLITE_AUTOGEN_HANDOFF_REWRITE": "0",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE": "0",
        "AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE": "0",
        "AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE": "off",
    },
    "team": {
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
        "AGENTLITE_AUTOGEN_HANDOFF_REWRITE": "0",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE": "0",
        "AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE": "0",
        "AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE": "off",
    },
    "non-text": {
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE": "0",
        "AGENTLITE_AUTOGEN_HANDOFF_REWRITE": "1",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE": "1",
        "AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE": "0",
        "AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE": "off",
    },
    "all": {
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
        "AGENTLITE_AUTOGEN_HANDOFF_REWRITE": "1",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE": "1",
        "AGENTLITE_AUTOGEN_CORE_CONTENT_REWRITE": "1",
        "AGENTLITE_AUTOGEN_CORE_RECEIVER_HYDRATE": "prompt-view",
    },
}
BROADCAST_MODES = ("shadow-only", "dry-run-rewrite", "real-rewrite")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentlite",
        description="AgentLite managed multi-agent runtime launcher.",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    run_parser = subparsers.add_parser(
        "run",
        aliases=["start"],
        help="Run a command under an AgentLite framework driver.",
    )
    run_parser.add_argument("--framework", required=True)
    run_parser.add_argument("--cwd", type=Path, default=Path.cwd())
    run_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".agentlite",
    )
    run_parser.add_argument("--runtime-endpoint")
    run_parser.add_argument(
        "--experiment-dir",
        type=Path,
        help="Bind this launch to a new immutable experiment output directory.",
    )
    run_parser.add_argument(
        "--driver",
        help="Override the built-in driver with a Python module path.",
    )
    run_parser.add_argument(
        "--no-strict-bootstrap",
        action="store_true",
        help="Allow the target process to continue if bootstrap fails.",
    )
    run_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run; place it after '--'.",
    )
    run_parser.add_argument(
        "--rewrite",
        choices=tuple(REWRITE_ENV_BY_PRESET),
        help=(
            "AutoGen rewrite preset. off=observe only; agent=rewrite simple "
            "agent text inputs; team=rewrite Team task entry; non-text=rewrite "
            "safe Handoff/ToolSummary content; all=enables every supported "
            "AutoGen rewrite gate."
        ),
    )
    run_parser.add_argument(
        "--broadcast-mode",
        choices=BROADCAST_MODES,
        help=(
            "Low-level AutoGen broadcast mode override. Usually prefer "
            "--rewrite unless you need an exact diagnostic mode."
        ),
    )

    autogen_parser = subparsers.add_parser(
        "autogen",
        help=(
            "Run a Python command under the AutoGen driver with the supported "
            "rewrite gates enabled by default."
        ),
    )
    autogen_parser.add_argument("--cwd", type=Path, default=Path.cwd())
    autogen_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".agentlite",
    )
    autogen_parser.add_argument("--runtime-endpoint")
    autogen_parser.add_argument(
        "--experiment-dir",
        type=Path,
        help=(
            "Bind this launch, its exact AgentLite session, and Provider usage "
            "to a new immutable experiment output directory."
        ),
    )
    autogen_parser.add_argument(
        "--driver",
        help="Override the built-in AutoGen driver with a Python module path.",
    )
    autogen_parser.add_argument(
        "--no-strict-bootstrap",
        action="store_true",
        help="Allow the target process to continue if bootstrap fails.",
    )
    autogen_parser.add_argument(
        "--rewrite",
        choices=tuple(REWRITE_ENV_BY_PRESET),
        default="all",
        help=(
            "AutoGen rewrite preset. Defaults to all for the shorthand "
            "AutoGen takeover command."
        ),
    )
    autogen_parser.add_argument(
        "--broadcast-mode",
        choices=BROADCAST_MODES,
        help=(
            "Low-level AutoGen broadcast mode override. Usually prefer "
            "--rewrite unless you need an exact diagnostic mode."
        ),
    )
    autogen_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run; place it after '--'.",
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check whether the current environment can run AgentLite.",
    )
    doctor_parser.add_argument(
        "--framework",
        choices=("autogen",),
        help="Also check optional dependencies for a framework driver.",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable diagnostics.",
    )

    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Run the local AgentLite workflow monitor web UI.",
    )
    monitor_parser.add_argument("--host", default="127.0.0.1")
    monitor_parser.add_argument("--port", type=int, default=8765)
    monitor_parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path.cwd() / "runs",
        help="Directory containing benchmark run outputs.",
    )
    monitor_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".agentlite",
        help="AgentLite data directory containing managed launch sessions.",
    )

    report_parser = subparsers.add_parser(
        "report",
        help="Export AgentLite experiment and takeover reports.",
    )
    report_subparsers = report_parser.add_subparsers(dest="report_kind", required=True)
    session_report_parser = report_subparsers.add_parser(
        "autogen-session",
        help="Export token metrics for one AgentLite-managed AutoGen session.",
    )
    session_report_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".agentlite",
        help="AgentLite data directory containing managed launch sessions.",
    )
    session_report_parser.add_argument(
        "--session-id",
        default="latest",
        help="Session id to export. Defaults to latest.",
    )
    session_report_parser.add_argument(
        "--format",
        choices=("markdown", "json", "csv"),
        default="markdown",
        help="Report output format.",
    )
    session_report_parser.add_argument(
        "--output",
        type=Path,
        help="Optional file path. Prints to stdout when omitted.",
    )
    session_report_parser.add_argument(
        "--provider-usage",
        type=Path,
        help=(
            "Optional Provider usage JSON/JSONL. Used when a custom model client "
            "does not expose usage to the AutoGen hook."
        ),
    )
    session_report_parser.add_argument(
        "--experiment-dir",
        type=Path,
        help=(
            "Resolve and verify the exact session and Provider usage from an "
            "immutable experiment archive."
        ),
    )
    run_report_parser = report_subparsers.add_parser(
        "autogen-run",
        help="Export token metrics for one AutoGen Team or Studio Run.",
    )
    run_report_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.home() / ".agentlite",
        help="AgentLite data directory containing managed launch sessions.",
    )
    run_report_parser.add_argument(
        "--session-id",
        default="latest",
        help="AgentLite process session containing the Run. Defaults to latest.",
    )
    run_report_parser.add_argument(
        "--run-id",
        default="latest",
        help=(
            "framework_run_id to export, for example autogenstudio:22. "
            "Defaults to the latest Run in the selected process session."
        ),
    )
    run_report_parser.add_argument(
        "--format",
        choices=("markdown", "json", "csv"),
        default="markdown",
    )
    run_report_parser.add_argument("--output", type=Path)

    subparsers.add_parser("version", help="Print AgentLite package version.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "doctor":
        return _doctor(framework=args.framework, json_output=args.json)
    if args.subcommand == "version":
        print(_package_version())
        return 0
    if args.subcommand == "monitor":
        return _monitor(
            host=args.host,
            port=args.port,
            runs_dir=args.runs_dir,
            data_dir=args.data_dir,
        )
    if args.subcommand == "report":
        return _report(args)
    if args.subcommand not in {"run", "start", "autogen"}:
        return 2

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    framework = _launch_framework(args)
    request = LaunchRequest(
        framework=framework,
        command=command,
        cwd=args.cwd.expanduser().resolve(),
        data_dir=args.data_dir.expanduser().resolve(),
        runtime_endpoint=args.runtime_endpoint,
        driver_override=args.driver,
        strict_bootstrap=not args.no_strict_bootstrap,
        experiment_dir=(
            args.experiment_dir.expanduser().resolve()
            if args.experiment_dir is not None
            else None
        ),
    )
    _print_launch_header(request)
    launch_env = build_managed_environment_overlay(
        framework=request.framework,
        rewrite=args.rewrite,
        broadcast_mode=args.broadcast_mode,
        base=os.environ,
    )
    try:
        result = ManagedProcessLauncher().launch(request, environ=launch_env)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"AgentLite launch failed: {exc}", file=sys.stderr)
        return 2

    status = read_bootstrap_status(result.status_file)
    if status is None:
        print(
            "AgentLite bootstrap status was not produced; "
            "the target may not be a Python process or startup injection was disabled.",
            file=sys.stderr,
        )
        return result.returncode if result.returncode != 0 else 78

    hook_state = "active" if status.get("hooks_active") else "inactive"
    print(f"Bootstrap status: {status.get('driver_status', 'unknown')}")
    print(f"Driver hooks: {hook_state}")
    print(f"Session file: {result.status_file}")
    if result.experiment_dir is not None:
        print(f"Experiment directory: {result.experiment_dir}")
        print(f"Experiment binding verified: {result.binding_verified}")
        if result.report_files:
            print("Bound reports: " + ", ".join(str(path) for path in result.report_files))
        if not result.binding_verified:
            print(
                f"Experiment binding failed: {result.binding_error}",
                file=sys.stderr,
            )
            return result.returncode if result.returncode != 0 else 79
    return result.returncode


def _launch_framework(args: argparse.Namespace) -> str:
    if args.subcommand == "autogen":
        return "autogen"
    return str(args.framework).strip().lower()


def build_managed_environment_overlay(
    *,
    framework: str,
    rewrite: str | None,
    broadcast_mode: str | None,
    base: os._Environ[str] | dict[str, str],
) -> dict[str, str]:
    env = dict(base)
    if framework == "autogen" and rewrite:
        env.update(REWRITE_ENV_BY_PRESET[rewrite])
    if framework == "autogen" and broadcast_mode:
        env["AGENTLITE_AUTOGEN_BROADCAST_MODE"] = broadcast_mode
    return env


def _print_launch_header(request: LaunchRequest) -> None:
    print("AgentLite managed launch", flush=True)
    print(f"Framework: {request.framework}", flush=True)
    print(f"Working directory: {request.cwd}", flush=True)
    print(f"Data directory: {request.data_dir}", flush=True)
    print(
        f"Command: {' '.join(request.command) if request.command else '(missing)'}",
        flush=True,
    )


def _doctor(*, framework: str | None, json_output: bool) -> int:
    checks = _doctor_checks(framework=framework)
    ok = all(check["ok"] for check in checks)
    payload = {
        "ok": ok,
        "package": PACKAGE_NAME,
        "version": _package_version(),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "checks": checks,
    }
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"AgentLite {payload['version']}")
        print(f"Python {payload['python_version']}: {payload['python']}")
        for check in checks:
            status = "ok" if check["ok"] else "fail"
            detail = f" - {check['detail']}" if check.get("detail") else ""
            print(f"[{status}] {check['name']}{detail}")
    return 0 if ok else 1


def _monitor(*, host: str, port: int, runs_dir: Path, data_dir: Path) -> int:
    from web_monitor.server import main as monitor_main

    previous_argv = sys.argv[:]
    sys.argv = [
        "agentlite monitor",
        "--host",
        host,
        "--port",
        str(port),
        "--runs-dir",
        str(runs_dir.expanduser().resolve()),
        "--data-dir",
        str(data_dir.expanduser().resolve()),
    ]
    try:
        return monitor_main()
    finally:
        sys.argv = previous_argv


def _report(args: argparse.Namespace) -> int:
    if args.report_kind == "autogen-run":
        from agent_runtime.eval.autogen_session_report import (
            RunReportRequest,
            write_autogen_run_report,
        )

        try:
            report = write_autogen_run_report(
                RunReportRequest(
                    data_dir=args.data_dir,
                    session_id=args.session_id,
                    run_id=args.run_id,
                    output=args.output,
                    report_format=args.format,
                )
            )
        except (OSError, ValueError) as exc:
            print(f"AgentLite report failed: {exc}", file=sys.stderr)
            return 2
        if args.output:
            print(f"Report written: {report['output_path']}")
        return 0
    if args.report_kind != "autogen-session":
        return 2
    from agent_runtime.eval.autogen_session_report import (
        SessionReportRequest,
        write_autogen_session_report,
    )

    try:
        report = write_autogen_session_report(
            SessionReportRequest(
                data_dir=args.data_dir,
                session_id=args.session_id,
                output=args.output,
                report_format=args.format,
                provider_usage=args.provider_usage,
                experiment_dir=args.experiment_dir,
                exclusive_output=args.experiment_dir is not None,
            )
        )
    except (OSError, ValueError) as exc:
        print(f"AgentLite report failed: {exc}", file=sys.stderr)
        return 2
    if args.output:
        print(f"Report written: {report['output_path']}")
    return 0


def _doctor_checks(*, framework: str | None) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = [
        {
            "name": "python>=3.11",
            "ok": sys.version_info >= (3, 11),
            "detail": platform.python_version(),
        },
        _module_check("tiktoken"),
        _tokenizer_backend_check(),
        _module_check("agent_runtime.launcher"),
        _module_check("agent_runtime.bootstrap.startup"),
    ]
    if framework == "autogen":
        checks.extend(
            [
                _module_check("autogen_agentchat"),
                _module_check("autogen_core"),
                _module_check("agent_runtime.drivers.autogen"),
            ]
        )
    return checks


def _tokenizer_backend_check() -> dict[str, object]:
    """Verify that the installed tokenizer can load its encoding data."""

    try:
        from agent_runtime.eval.token_counter import TokenCounter

        counter = TokenCounter()
        description = counter.describe()
        return {
            "name": "tokenizer:cl100k_base",
            "ok": True,
            "detail": (
                f"{description['tokenizer_name']} "
                f"{description['tokenizer_version']}"
            ),
        }
    except Exception as exc:
        return {
            "name": "tokenizer:cl100k_base",
            "ok": False,
            "detail": f"{type(exc).__name__}: {exc}",
        }


def _module_check(module_name: str) -> dict[str, object]:
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return {
            "name": f"import:{module_name}",
            "ok": False,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    version = getattr(module, "__version__", "")
    return {
        "name": f"import:{module_name}",
        "ok": True,
        "detail": str(version or getattr(module, "__file__", "")),
    }


def _package_version() -> str:
    try:
        from agent_runtime import __version__ as source_version
    except Exception:
        source_version = ""
    try:
        installed_version = importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return source_version or "editable-source"
    if source_version and source_version != installed_version:
        return source_version
    return installed_version


if __name__ == "__main__":
    raise SystemExit(main())
