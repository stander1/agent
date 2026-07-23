from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from agent_runtime.adapters.autogen_studio import (
    STUDIO_APPDIR_ENV,
    detect_studio_appdir,
)
from agent_runtime.eval.experiment_archive import (
    create_agentlite_session_binding,
    extended_length_path,
    verify_bound_experiment,
    write_agentlite_session_result,
)


FRAMEWORK_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
UNSUPPORTED_PYTHON_FLAGS = {"-E", "-I", "-S"}


@dataclass(slots=True)
class LaunchRequest:
    framework: str
    command: list[str]
    cwd: Path
    data_dir: Path
    runtime_endpoint: str | None = None
    driver_override: str | None = None
    strict_bootstrap: bool = True
    experiment_dir: Path | None = None


@dataclass(slots=True)
class LaunchResult:
    returncode: int
    session_id: str
    status_file: Path
    command: list[str]
    experiment_dir: Path | None = None
    experiment_result_file: Path | None = None
    report_files: tuple[Path, ...] = ()
    binding_verified: bool = False
    binding_error: str = ""


class ManagedProcessLauncher:
    def launch(
        self,
        request: LaunchRequest,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> LaunchResult:
        self.validate_request(request)
        session_id = f"launch_{uuid.uuid4().hex}"
        session_dir = request.data_dir / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        experiment_dir = (
            request.experiment_dir.expanduser().resolve()
            if request.experiment_dir is not None
            else None
        )
        if experiment_dir is not None:
            create_agentlite_session_binding(
                experiment_dir=experiment_dir,
                session_id=session_id,
                data_dir=request.data_dir,
                session_dir=session_dir,
                framework=request.framework,
                cwd=request.cwd,
                command=request.command,
            )
        status_file = session_dir / "bootstrap_status.json"
        launch_file = session_dir / "launch.json"
        studio_appdir = detect_studio_appdir(request.command, cwd=request.cwd)
        launch_file.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "framework": request.framework,
                    "cwd": str(request.cwd),
                    "command": request.command,
                    "runtime_endpoint": request.runtime_endpoint,
                    "experiment_dir": str(experiment_dir or ""),
                    "autogen_studio_appdir": str(studio_appdir or ""),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        child_env = self.build_environment(
            request,
            session_id=session_id,
            status_file=status_file,
            environ=environ,
        )
        completed = subprocess.run(
            request.command,
            cwd=request.cwd,
            env=child_env,
            check=False,
        )
        experiment_result_file: Path | None = None
        report_files: tuple[Path, ...] = ()
        binding_verified = False
        binding_error = ""
        bound_run_id = ""
        binding_checks: dict[str, object] = {}
        archived_session_dir: Path | None = None
        if experiment_dir is not None:
            try:
                archived_session_dir = (
                    experiment_dir / "agentlite_data" / "sessions" / session_id
                )
                archived_session_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    extended_length_path(session_dir),
                    extended_length_path(archived_session_dir),
                )
                bound = verify_bound_experiment(experiment_dir)
                binding_verified = True
                bound_run_id = str(bound["run_id"])
                binding_checks = dict(bound["checks"])
                report_files = self._write_bound_autogen_reports(
                    request=request,
                    experiment_dir=experiment_dir,
                )
            except (OSError, ValueError) as exc:
                binding_error = f"{type(exc).__name__}: {exc}"
            experiment_result_file = write_agentlite_session_result(
                experiment_dir=experiment_dir,
                session_id=session_id,
                returncode=completed.returncode,
                bootstrap_status=read_bootstrap_status(status_file),
                binding_verified=binding_verified,
                binding_error=binding_error,
                run_id=bound_run_id,
                binding_checks=binding_checks,
                archived_session_dir=archived_session_dir,
                report_paths=report_files,
            )
        return LaunchResult(
            returncode=completed.returncode,
            session_id=session_id,
            status_file=status_file,
            command=list(request.command),
            experiment_dir=experiment_dir,
            experiment_result_file=experiment_result_file,
            report_files=report_files,
            binding_verified=binding_verified,
            binding_error=binding_error,
        )

    @staticmethod
    def _write_bound_autogen_reports(
        *,
        request: LaunchRequest,
        experiment_dir: Path,
    ) -> tuple[Path, ...]:
        if request.framework != "autogen":
            return ()
        from agent_runtime.eval.autogen_session_report import (
            SessionReportRequest,
            write_autogen_session_report,
        )

        outputs: list[Path] = []
        for report_format, name in (
            ("json", "agentlite_session_report.json"),
            ("markdown", "agentlite_session_report.md"),
        ):
            output = experiment_dir / name
            write_autogen_session_report(
                SessionReportRequest(
                    experiment_dir=experiment_dir,
                    output=output,
                    report_format=report_format,
                    exclusive_output=True,
                )
            )
            outputs.append(output)
        return tuple(outputs)

    def build_environment(
        self,
        request: LaunchRequest,
        *,
        session_id: str,
        status_file: Path,
        environ: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        source = environ if environ is not None else os.environ
        env = dict(source)
        for stale_key in (
            "AGENTLITE_ACTIVE_SESSION_ID",
            "AGENTLITE_DRIVER",
            "AGENTLITE_PROBE_DRIVER_ACTIVE",
            "AGENTLITE_RUNTIME_ENDPOINT",
            STUDIO_APPDIR_ENV,
        ):
            env.pop(stale_key, None)
        package_root = Path(__file__).resolve().parent.parent
        bootstrap_dir = Path(__file__).resolve().parent / "bootstrap"
        env["PYTHONPATH"] = _prepend_pythonpath(
            [bootstrap_dir, package_root],
            env.get("PYTHONPATH", ""),
        )
        env.update(
            {
                "AGENTLITE_ENABLED": "1",
                "AGENTLITE_FRAMEWORK": request.framework,
                "AGENTLITE_SESSION_ID": session_id,
                "AGENTLITE_DATA_DIR": str(request.data_dir),
                "AGENTLITE_STATUS_FILE": str(status_file),
                "AGENTLITE_TARGET_CWD": str(request.cwd),
                "AGENTLITE_EXPERIMENT_DIR": str(request.experiment_dir or ""),
                "AGENTLITE_BOOTSTRAP_STRICT": (
                    "1" if request.strict_bootstrap else "0"
                ),
                "AGENTLITE_BOOTSTRAP_METADATA": json.dumps(
                    {
                        "launcher_version": "v5.13w",
                        "parent_pid": os.getpid(),
                    },
                    ensure_ascii=False,
                ),
            }
        )
        studio_appdir = detect_studio_appdir(request.command, cwd=request.cwd)
        if studio_appdir is not None:
            env[STUDIO_APPDIR_ENV] = str(studio_appdir)
        if request.runtime_endpoint:
            env["AGENTLITE_RUNTIME_ENDPOINT"] = request.runtime_endpoint
        if request.driver_override:
            env["AGENTLITE_DRIVER"] = request.driver_override
        return env

    @staticmethod
    def validate_request(request: LaunchRequest) -> None:
        if not request.command:
            raise ValueError("managed command is required after '--'")
        if not FRAMEWORK_PATTERN.fullmatch(request.framework):
            raise ValueError(
                "framework must use lowercase letters, digits, '_' or '-'"
            )
        if not request.cwd.is_dir():
            raise ValueError(f"working directory does not exist: {request.cwd}")
        if request.experiment_dir is not None:
            experiment_dir = request.experiment_dir.expanduser().resolve()
            if experiment_dir.exists() and any(experiment_dir.iterdir()):
                raise ValueError(
                    "experiment directory already contains evidence: "
                    f"{experiment_dir}"
                )
        if _looks_like_python(request.command[0]):
            blocked = UNSUPPORTED_PYTHON_FLAGS.intersection(request.command[1:])
            if blocked:
                flags = ", ".join(sorted(blocked))
                raise ValueError(
                    f"Python flags {flags} disable AgentLite startup injection"
                )


def read_bootstrap_status(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def _prepend_pythonpath(paths: Sequence[Path], existing: str) -> str:
    items = [str(path) for path in paths]
    if existing:
        items.append(existing)
    return os.pathsep.join(items)


def _looks_like_python(executable: str) -> bool:
    name = Path(executable).name.lower()
    return name.startswith("python") or Path(executable) == Path(sys.executable)
