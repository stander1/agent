from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


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


@dataclass(slots=True)
class LaunchResult:
    returncode: int
    session_id: str
    status_file: Path
    command: list[str]


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
        status_file = session_dir / "bootstrap_status.json"
        launch_file = session_dir / "launch.json"
        launch_file.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "framework": request.framework,
                    "cwd": str(request.cwd),
                    "command": request.command,
                    "runtime_endpoint": request.runtime_endpoint,
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
        return LaunchResult(
            returncode=completed.returncode,
            session_id=session_id,
            status_file=status_file,
            command=list(request.command),
        )

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
                "AGENTLITE_BOOTSTRAP_STRICT": (
                    "1" if request.strict_bootstrap else "0"
                ),
                "AGENTLITE_BOOTSTRAP_METADATA": json.dumps(
                    {
                        "launcher_version": "v5.12c",
                        "parent_pid": os.getpid(),
                    },
                    ensure_ascii=False,
                ),
            }
        )
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
