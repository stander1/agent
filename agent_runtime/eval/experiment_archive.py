from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


EXPERIMENT_RUN_FILE = "experiment_run.json"
EXPERIMENT_RESULT_FILE = "experiment_result.json"
EXPERIMENT_FAILURE_FILE = "experiment_failure.json"
AGENTLITE_SESSION_BINDING_FILE = "agentlite_session_binding.json"
AGENTLITE_SESSION_RESULT_FILE = "agentlite_session_result.json"
PROVIDER_USAGE_FILE = "llm_usage_summary.json"


@dataclass(frozen=True)
class ExperimentRunIdentity:
    run_id: str
    output_dir: Path
    scenario_id: str
    experiment_mode: str
    agentlite_session_id: str
    agentlite_data_dir: str
    created_at: str

    def binding(self) -> dict[str, str]:
        return {
            "schema_version": "agentlite.provider-usage-binding.v1",
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "experiment_mode": self.experiment_mode,
            "agentlite_session_id": self.agentlite_session_id,
        }


def initialize_experiment_archive(
    *,
    output_dir: Path,
    scenario_id: str,
    experiment_mode: str,
    source_paths: Iterable[Path] = (),
    provider_model: str = "",
    provider_base_url: str = "",
    command: Sequence[str] = (),
    environ: Mapping[str, str] | None = None,
) -> ExperimentRunIdentity:
    resolved = output_dir.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    allowed = {AGENTLITE_SESSION_BINDING_FILE}
    unexpected = sorted(path.name for path in resolved.iterdir() if path.name not in allowed)
    if unexpected:
        preview = ", ".join(unexpected[:5])
        raise FileExistsError(
            f"Experiment output directory is not empty: {resolved} ({preview}). "
            "Use a new output directory so previous evidence cannot be overwritten."
        )

    env = environ if environ is not None else os.environ
    session_id = str(env.get("AGENTLITE_SESSION_ID") or "").strip()
    data_dir = str(env.get("AGENTLITE_DATA_DIR") or "").strip()
    environment_experiment_dir = str(env.get("AGENTLITE_EXPERIMENT_DIR") or "").strip()
    if environment_experiment_dir and (
        Path(environment_experiment_dir).expanduser().resolve() != resolved
    ):
        raise ValueError(
            "Application --output-dir must match AgentLite --experiment-dir"
        )
    binding_path = resolved / AGENTLITE_SESSION_BINDING_FILE
    if binding_path.exists():
        binding = read_json_object(binding_path)
        bound_session = str(binding.get("session_id") or "")
        bound_dir = str(binding.get("data_dir") or "")
        if not session_id or session_id != bound_session:
            raise ValueError(
                "AgentLite session binding does not match the child process environment"
            )
        if data_dir and Path(data_dir).expanduser().resolve() != Path(bound_dir).expanduser().resolve():
            raise ValueError("AgentLite data directory binding does not match the child process")

    created_at = utc_now()
    identity = ExperimentRunIdentity(
        run_id=f"run_{uuid.uuid4().hex}",
        output_dir=resolved,
        scenario_id=scenario_id,
        experiment_mode=experiment_mode,
        agentlite_session_id=session_id,
        agentlite_data_dir=data_dir,
        created_at=created_at,
    )
    sources: list[dict[str, str]] = []
    for source in source_paths:
        path = source.expanduser().resolve()
        if path.is_file():
            sources.append({"path": str(path), "sha256": sha256_file(path)})
    command_text = "\0".join(str(item) for item in command)
    manifest = {
        "schema_version": "agentlite.experiment-run.v1",
        **identity.binding(),
        "created_at": created_at,
        "output_dir": str(resolved),
        "agentlite_data_dir": data_dir,
        "agentlite_memory_scope": str(env.get("AGENTLITE_MEMORY_SCOPE") or ""),
        "provider": {
            "model": provider_model,
            "base_url": provider_base_url,
        },
        "source_artifacts": sources,
        "process": {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "python": sys.executable,
            "python_version": platform.python_version(),
            "command_sha256": hashlib.sha256(command_text.encode("utf-8")).hexdigest(),
        },
    }
    write_json_exclusive(resolved / EXPERIMENT_RUN_FILE, manifest)
    return identity


def complete_experiment_archive(
    identity: ExperimentRunIdentity,
    *,
    summary: Mapping[str, Any],
    artifact_paths: Iterable[Path],
) -> Path:
    artifacts: list[dict[str, Any]] = []
    for candidate in artifact_paths:
        path = candidate.expanduser().resolve()
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(identity.output_dir)
        except ValueError:
            relative = path
        artifacts.append(
            {
                "path": str(relative),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    provider_path = identity.output_dir / PROVIDER_USAGE_FILE
    payload = {
        "schema_version": "agentlite.experiment-result.v1",
        **identity.binding(),
        "status": "completed",
        "completed_at": utc_now(),
        "summary": dict(summary),
        "provider_usage": (
            {
                "path": PROVIDER_USAGE_FILE,
                "sha256": sha256_file(provider_path),
            }
            if provider_path.is_file()
            else None
        ),
        "artifacts": sorted(artifacts, key=lambda item: str(item["path"])),
    }
    path = identity.output_dir / EXPERIMENT_RESULT_FILE
    write_json_exclusive(path, payload)
    return path


def fail_experiment_archive(
    identity: ExperimentRunIdentity,
    *,
    error_type: str,
    error_message: str,
) -> Path | None:
    path = identity.output_dir / EXPERIMENT_FAILURE_FILE
    if path.exists() or (identity.output_dir / EXPERIMENT_RESULT_FILE).exists():
        return None
    write_json_exclusive(
        path,
        {
            "schema_version": "agentlite.experiment-failure.v1",
            **identity.binding(),
            "status": "failed",
            "failed_at": utc_now(),
            "error_type": error_type,
            "error_message": error_message,
        },
    )
    return path


def create_agentlite_session_binding(
    *,
    experiment_dir: Path,
    session_id: str,
    data_dir: Path,
    session_dir: Path,
    framework: str,
    cwd: Path,
    command: Sequence[str],
) -> Path:
    resolved = experiment_dir.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    if any(resolved.iterdir()):
        raise FileExistsError(
            f"Experiment directory already contains evidence: {resolved}. "
            "Use a new --experiment-dir for every launch."
        )
    command_text = "\0".join(str(item) for item in command)
    path = resolved / AGENTLITE_SESSION_BINDING_FILE
    write_json_exclusive(
        path,
        {
            "schema_version": "agentlite.session-binding.v1",
            "created_at": utc_now(),
            "session_id": session_id,
            "framework": framework,
            "data_dir": str(data_dir.expanduser().resolve()),
            "session_dir": str(session_dir.expanduser().resolve()),
            "experiment_dir": str(resolved),
            "cwd": str(cwd.expanduser().resolve()),
            "executable": str(command[0]) if command else "",
            "command_sha256": hashlib.sha256(command_text.encode("utf-8")).hexdigest(),
        },
    )
    return path


def verify_experiment_archive(experiment_dir: Path) -> dict[str, Any]:
    resolved = experiment_dir.expanduser().resolve()
    run_path = resolved / EXPERIMENT_RUN_FILE
    result_path = resolved / EXPERIMENT_RESULT_FILE
    usage_path = resolved / PROVIDER_USAGE_FILE
    for path in (run_path, result_path, usage_path):
        if not path.is_file():
            raise FileNotFoundError(f"Experiment artifact not found: {path}")

    run = read_json_object(run_path)
    result = read_json_object(result_path)
    usage = read_json_object(usage_path)
    usage_binding = usage.get("binding")
    if not isinstance(usage_binding, dict):
        raise ValueError("Provider usage file does not contain a binding object")
    run_id = str(run.get("run_id") or "")
    session_id = str(run.get("agentlite_session_id") or "")
    checks = {
        "run_id_present": bool(run_id),
        "result_run_matches": str(result.get("run_id") or "") == run_id,
        "result_session_matches": str(result.get("agentlite_session_id") or "") == session_id,
        "usage_run_matches": str(usage_binding.get("run_id") or "") == run_id,
        "usage_session_matches": str(usage_binding.get("agentlite_session_id") or "") == session_id,
        "provider_usage_hash_matches": _provider_hash_matches(result, usage_path),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise ValueError(
            "Experiment archive validation failed: " + ", ".join(failed)
        )
    return {
        "experiment_dir": resolved,
        "run_id": run_id,
        "session_id": session_id,
        "provider_usage_path": usage_path,
        "checks": checks,
        "run": run,
        "result": result,
    }


def verify_bound_experiment(experiment_dir: Path) -> dict[str, Any]:
    verified = verify_experiment_archive(experiment_dir)
    resolved = verified["experiment_dir"]
    binding_path = resolved / AGENTLITE_SESSION_BINDING_FILE
    if not binding_path.is_file():
        raise FileNotFoundError(f"Bound experiment artifact not found: {binding_path}")
    binding = read_json_object(binding_path)
    run = verified["run"]
    run_id = verified["run_id"]
    session_id = str(binding.get("session_id") or "")
    checks = {
        **verified["checks"],
        "session_id_present": bool(session_id),
        "binding_session_matches_run": (
            str(run.get("agentlite_session_id") or "") == session_id
        ),
    }
    bound_data_dir = Path(str(binding.get("data_dir") or "")).expanduser().resolve()
    bound_session_dir = Path(str(binding.get("session_dir") or "")).expanduser().resolve()
    checks["session_dir_matches"] = (
        bound_session_dir == bound_data_dir / "sessions" / session_id
    )
    archived_data_dir = resolved / "agentlite_data"
    archived_session_dir = archived_data_dir / "sessions" / session_id
    if archived_session_dir.is_dir():
        data_dir = archived_data_dir
        session_dir = archived_session_dir
        session_source = "immutable_archive"
    else:
        data_dir = bound_data_dir
        session_dir = bound_session_dir
        session_source = "live_data_dir"
    checks["session_dir_exists"] = session_dir.is_dir()
    session_result_path = resolved / AGENTLITE_SESSION_RESULT_FILE
    if session_result_path.is_file() and session_dir.is_dir():
        session_result = read_json_object(session_result_path)
        frozen_session = session_result.get("session_archive")
        actual_session = directory_digest(session_dir)
        checks.update(
            {
                "session_result_run_matches": (
                    str(session_result.get("run_id") or "") == run_id
                ),
                "session_result_session_matches": (
                    str(session_result.get("session_id") or "") == session_id
                ),
                "session_result_was_verified": bool(
                    session_result.get("binding_verified")
                ),
                "session_archive_hash_matches": (
                    isinstance(frozen_session, dict)
                    and str(frozen_session.get("sha256") or "")
                    == actual_session["sha256"]
                    and int(frozen_session.get("file_count", -1))
                    == actual_session["file_count"]
                    and int(frozen_session.get("bytes", -1))
                    == actual_session["bytes"]
                ),
            }
        )
        for index, report in enumerate(session_result.get("reports") or []):
            if not isinstance(report, dict):
                checks[f"report_{index}_hash_matches"] = False
                continue
            report_path = Path(str(report.get("path") or ""))
            if not report_path.is_absolute():
                report_path = resolved / report_path
            checks[f"report_{index}_hash_matches"] = (
                report_path.is_file()
                and str(report.get("sha256") or "") == sha256_file(report_path)
            )
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise ValueError(
            "Experiment binding validation failed: " + ", ".join(failed)
        )
    return {
        "experiment_dir": resolved,
        "run_id": run_id,
        "session_id": session_id,
        "data_dir": data_dir,
        "session_dir": session_dir,
        "session_source": session_source,
        "provider_usage_path": verified["provider_usage_path"],
        "checks": checks,
    }


def write_agentlite_session_result(
    *,
    experiment_dir: Path,
    session_id: str,
    returncode: int,
    bootstrap_status: Mapping[str, Any] | None,
    binding_verified: bool,
    binding_error: str,
    run_id: str = "",
    binding_checks: Mapping[str, Any] | None = None,
    archived_session_dir: Path | None = None,
    report_paths: Iterable[Path] = (),
) -> Path:
    resolved = experiment_dir.expanduser().resolve()
    path = resolved / AGENTLITE_SESSION_RESULT_FILE
    session_archive = (
        directory_digest(archived_session_dir)
        if archived_session_dir is not None and archived_session_dir.is_dir()
        else None
    )
    if session_archive is not None and archived_session_dir is not None:
        session_archive["path"] = str(
            archived_session_dir.expanduser().resolve().relative_to(resolved)
        )
    write_json_exclusive(
        path,
        {
            "schema_version": "agentlite.session-result.v1",
            "session_id": session_id,
            "run_id": run_id,
            "returncode": returncode,
            "completed_at": utc_now(),
            "bootstrap_status": dict(bootstrap_status or {}),
            "binding_verified": binding_verified,
            "binding_checks": dict(binding_checks or {}),
            "binding_error": binding_error,
            "session_archive": session_archive,
            "reports": [
                {
                    "path": _portable_archive_path(item, resolved),
                    "bytes": item.stat().st_size,
                    "sha256": sha256_file(item),
                }
                for item in report_paths
                if item.is_file()
            ],
        },
    )
    return path


def read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return value


def write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_digest(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    scan_root = Path(extended_length_path(resolved))
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for file_path in sorted(item for item in scan_root.rglob("*") if item.is_file()):
        relative = file_path.relative_to(scan_root).as_posix()
        file_hash = sha256_file(file_path)
        size = file_path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\0")
        file_count += 1
        total_bytes += size
    return {
        "path": str(resolved),
        "file_count": file_count,
        "bytes": total_bytes,
        "sha256": digest.hexdigest(),
    }


def extended_length_path(path: Path) -> str:
    """Return a Windows long-path form without changing the logical path."""
    resolved = str(path.expanduser().resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved[2:]
    return "\\\\?\\" + resolved


def _portable_archive_path(path: Path, archive_root: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(archive_root))
    except ValueError:
        return str(resolved)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provider_hash_matches(result: Mapping[str, Any], usage_path: Path) -> bool:
    provider = result.get("provider_usage")
    if not isinstance(provider, dict):
        return False
    expected = str(provider.get("sha256") or "")
    return bool(expected) and expected == sha256_file(usage_path)
