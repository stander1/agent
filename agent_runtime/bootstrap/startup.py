from __future__ import annotations

import json
import importlib.machinery
import importlib.util
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from agent_runtime.drivers.loader import DriverActivation, load_and_activate_driver


@dataclass(slots=True, frozen=True)
class BootstrapContext:
    framework: str
    session_id: str
    data_dir: Path
    status_file: Path
    target_cwd: Path
    runtime_endpoint: str | None = None
    driver_override: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BootstrapResult:
    ok: bool
    framework: str
    session_id: str
    process_id: int
    driver: str = ""
    driver_status: str = ""
    hooks_active: bool = False
    framework_available: bool | None = None
    runtime_endpoint: str | None = None
    warnings: list[str] = field(default_factory=list)
    driver_details: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    activated_at: float = field(default_factory=time.time)


def bootstrap_from_environment(
    environ: Mapping[str, str] | None = None,
) -> BootstrapResult | None:
    env = environ or os.environ
    if env.get("AGENTLITE_ENABLED") != "1":
        return None

    context: BootstrapContext | None = None
    try:
        context = context_from_environment(env)
        activation = load_and_activate_driver(context)
        result = _success_result(context, activation)
    except Exception as exc:
        framework = context.framework if context else env.get(
            "AGENTLITE_FRAMEWORK", ""
        )
        session_id = context.session_id if context else env.get(
            "AGENTLITE_SESSION_ID", ""
        )
        result = BootstrapResult(
            ok=False,
            framework=framework,
            session_id=session_id,
            process_id=os.getpid(),
            runtime_endpoint=env.get("AGENTLITE_RUNTIME_ENDPOINT") or None,
            error=f"{type(exc).__name__}: {exc}",
        )

    status_file = (
        context.status_file
        if context is not None
        else _optional_path(env.get("AGENTLITE_STATUS_FILE"))
    )
    if status_file is not None:
        write_bootstrap_status(status_file, result)
    _emit_bootstrap_message(result)
    return result


def context_from_environment(env: Mapping[str, str]) -> BootstrapContext:
    framework = env.get("AGENTLITE_FRAMEWORK", "").strip().lower()
    session_id = env.get("AGENTLITE_SESSION_ID", "").strip()
    data_dir_text = env.get("AGENTLITE_DATA_DIR", "").strip()
    status_file_text = env.get("AGENTLITE_STATUS_FILE", "").strip()
    target_cwd_text = env.get("AGENTLITE_TARGET_CWD", "").strip()
    missing = [
        name
        for name, value in (
            ("AGENTLITE_FRAMEWORK", framework),
            ("AGENTLITE_SESSION_ID", session_id),
            ("AGENTLITE_DATA_DIR", data_dir_text),
            ("AGENTLITE_STATUS_FILE", status_file_text),
            ("AGENTLITE_TARGET_CWD", target_cwd_text),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "missing managed-process environment: " + ", ".join(missing)
        )

    metadata_text = env.get("AGENTLITE_BOOTSTRAP_METADATA", "").strip()
    metadata = json.loads(metadata_text) if metadata_text else {}
    if not isinstance(metadata, dict):
        raise ValueError("AGENTLITE_BOOTSTRAP_METADATA must be a JSON object")

    return BootstrapContext(
        framework=framework,
        session_id=session_id,
        data_dir=Path(data_dir_text).expanduser().resolve(),
        status_file=Path(status_file_text).expanduser().resolve(),
        target_cwd=Path(target_cwd_text).expanduser().resolve(),
        runtime_endpoint=env.get("AGENTLITE_RUNTIME_ENDPOINT") or None,
        driver_override=env.get("AGENTLITE_DRIVER") or None,
        metadata=metadata,
    )


def write_bootstrap_status(path: Path, result: BootstrapResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **asdict(result),
        "python": sys.executable,
        "python_version": platform.python_version(),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def run_chained_sitecustomize(current_file: Path) -> bool:
    """Run an existing environment sitecustomize after AgentLite bootstrap."""

    current_file = current_file.resolve()
    bootstrap_dir = current_file.parent
    search_paths: list[str] = []
    for entry in sys.path:
        candidate = Path(entry or os.getcwd())
        try:
            if candidate.resolve() == bootstrap_dir:
                continue
        except OSError:
            pass
        search_paths.append(entry)

    spec = importlib.machinery.PathFinder.find_spec(
        "sitecustomize", search_paths
    )
    if spec is None or not spec.origin:
        return False
    origin = Path(spec.origin)
    try:
        if origin.resolve() == current_file:
            return False
    except OSError:
        return False

    chained_spec = importlib.util.spec_from_file_location(
        "_agentlite_chained_sitecustomize",
        origin,
    )
    if chained_spec is None or chained_spec.loader is None:
        return False
    module = importlib.util.module_from_spec(chained_spec)
    chained_spec.loader.exec_module(module)
    return True


def _success_result(
    context: BootstrapContext,
    activation: DriverActivation,
) -> BootstrapResult:
    return BootstrapResult(
        ok=True,
        framework=context.framework,
        session_id=context.session_id,
        process_id=os.getpid(),
        driver=activation.driver,
        driver_status=activation.status,
        hooks_active=activation.hooks_active,
        framework_available=activation.framework_available,
        runtime_endpoint=context.runtime_endpoint,
        warnings=list(activation.warnings),
        driver_details=dict(activation.details),
    )


def _emit_bootstrap_message(result: BootstrapResult) -> None:
    if not result.ok:
        return
    hook_state = "active" if result.hooks_active else "inactive"
    message = (
        f"[AgentLite] driver={result.driver} status={result.driver_status} "
        f"hooks={hook_state} session={result.session_id}"
    )
    print(message, file=sys.stderr, flush=True)
    for warning in result.warnings:
        print(f"[AgentLite] warning: {warning}", file=sys.stderr, flush=True)


def _optional_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser().resolve()
