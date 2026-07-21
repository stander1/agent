from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


STUDIO_APPDIR_ENV = "AGENTLITE_AUTOGEN_STUDIO_APPDIR"
STUDIO_RUN_CONTEXT_MODULE = "autogenstudio.web.managers.run_context"
_ACTIVE_RUN_BINDING: ContextVar[FrameworkRunBinding | None] = ContextVar(
    "agentlite_active_framework_run_binding",
    default=None,
)


@dataclass(frozen=True, slots=True)
class FrameworkRunBinding:
    framework_run_id: str
    source: str
    studio_run_id: str = ""
    studio_session_id: str = ""
    studio_team_id: str = ""
    studio_session_name: str = ""
    studio_status: str = ""
    studio_database_path: str = ""

    def trace_fields(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "framework_run_id": self.framework_run_id,
                "framework_run_source": self.source,
                "studio_run_id": self.studio_run_id,
                "studio_session_id": self.studio_session_id,
                "studio_team_id": self.studio_team_id,
                "studio_session_name": self.studio_session_name,
                "studio_status": self.studio_status,
                "studio_database_path": self.studio_database_path,
            }.items()
            if value
        }


def current_run_binding() -> FrameworkRunBinding | None:
    active = _ACTIVE_RUN_BINDING.get()
    if active is not None:
        return active
    return current_studio_run_binding()


def current_studio_run_binding() -> FrameworkRunBinding | None:
    module = sys.modules.get(STUDIO_RUN_CONTEXT_MODULE)
    run_context = getattr(module, "RunContext", None) if module is not None else None
    current_run_id = getattr(run_context, "current_run_id", None)
    if not callable(current_run_id):
        return None
    try:
        raw_run_id = current_run_id()
    except (LookupError, RuntimeError):
        return None
    if raw_run_id is None or not str(raw_run_id).strip():
        return None
    studio_run_id = str(raw_run_id).strip()
    return FrameworkRunBinding(
        framework_run_id=f"autogenstudio:{studio_run_id}",
        source="autogenstudio_run_context",
        studio_run_id=studio_run_id,
    )


def activate_run_binding(binding: FrameworkRunBinding | None) -> Token:
    return _ACTIVE_RUN_BINDING.set(binding)


def reset_run_binding(token: Token) -> None:
    _ACTIVE_RUN_BINDING.reset(token)


def enrich_studio_binding(
    binding: FrameworkRunBinding,
    *,
    appdir: Path | None,
) -> FrameworkRunBinding:
    if not binding.studio_run_id or appdir is None:
        return binding
    metadata = read_studio_run_metadata(appdir=appdir, run_id=binding.studio_run_id)
    if not metadata:
        return binding
    return FrameworkRunBinding(
        framework_run_id=binding.framework_run_id,
        source=binding.source,
        studio_run_id=binding.studio_run_id,
        studio_session_id=str(metadata.get("session_id") or ""),
        studio_team_id=str(metadata.get("team_id") or ""),
        studio_session_name=str(metadata.get("session_name") or ""),
        studio_status=str(metadata.get("status") or ""),
        studio_database_path=str(metadata.get("database_path") or ""),
    )


def detect_studio_appdir(command: Sequence[str], *, cwd: Path) -> Path | None:
    if not _looks_like_studio_command(command):
        return None
    raw_appdir = _option_value(command, "--appdir")
    if not raw_appdir:
        return None
    path = Path(raw_appdir).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def configured_studio_appdir(
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    env = environ if environ is not None else os.environ
    raw = str(env.get(STUDIO_APPDIR_ENV) or "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def discover_studio_database(appdir: Path) -> Path | None:
    resolved = appdir.expanduser().resolve()
    if not resolved.is_dir():
        return None
    preferred = (
        resolved / "autogen04202.db",
        resolved / "database.sqlite",
        resolved / "database.db",
    )
    for candidate in preferred:
        if candidate.is_file():
            return candidate
    candidates = [
        path
        for pattern in ("*.db", "*.sqlite", "*.sqlite3")
        for path in resolved.glob(pattern)
        if path.is_file()
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def read_studio_run_metadata(*, appdir: Path, run_id: str) -> dict[str, Any]:
    database = discover_studio_database(appdir)
    if database is None:
        return {}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(database), timeout=1.0)
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            """
            SELECT
                r.id,
                r.session_id,
                r.status,
                r.created_at,
                r.updated_at,
                r.task,
                s.team_id,
                s.name
            FROM run AS r
            LEFT JOIN session AS s ON s.id = r.session_id
            WHERE CAST(r.id AS TEXT) = ?
            LIMIT 1
            """,
            (str(run_id),),
        ).fetchone()
    except (OSError, sqlite3.Error):
        return {}
    finally:
        if connection is not None:
            connection.close()
    if row is None:
        return {}
    return {
        "run_id": str(row[0] or ""),
        "session_id": str(row[1] or ""),
        "status": str(row[2] or ""),
        "created_at": str(row[3] or ""),
        "updated_at": str(row[4] or ""),
        "task_preview": _task_preview(row[5]),
        "team_id": str(row[6] or ""),
        "session_name": str(row[7] or ""),
        "database_path": str(database),
    }


def _looks_like_studio_command(command: Sequence[str]) -> bool:
    normalized = [Path(str(item)).name.lower() for item in command]
    if any(name in {"autogenstudio", "autogenstudio.exe"} for name in normalized):
        return True
    for index, item in enumerate(command[:-1]):
        if item == "-m" and str(command[index + 1]).strip() == "autogenstudio":
            return True
    return False


def _option_value(command: Sequence[str], option: str) -> str:
    for index, item in enumerate(command):
        text = str(item)
        if text == option and index + 1 < len(command):
            return str(command[index + 1]).strip()
        prefix = f"{option}="
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return ""


def _task_preview(value: Any, limit: int = 240) -> str:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
    text = _find_content(parsed)
    return " ".join(text.split())[:limit]


def _find_content(value: Any, *, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            text
            for item in value
            if (text := _find_content(item, depth=depth + 1))
        )
    if isinstance(value, dict):
        for key in ("content", "task", "query", "text"):
            if key in value:
                text = _find_content(value[key], depth=depth + 1)
                if text:
                    return text
        return "\n".join(
            text
            for item in value.values()
            if (text := _find_content(item, depth=depth + 1))
        )
    return ""
