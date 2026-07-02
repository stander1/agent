from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.core.models import Mode
from agent_runtime.eval.benchmark_runner import run_v0_benchmark
from web_monitor.parser import (
    build_run_snapshot,
    build_session_snapshot,
    list_runs,
    list_sessions,
)


RUNS_DIR = PROJECT_ROOT / "runs"
AGENTLITE_DATA_DIR = Path.home() / ".agentlite"
DEMO_DIR = PROJECT_ROOT / "web_monitor" / "demo"


class ExperimentRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id = f"monitor-{stamp}"
        output_dir = RUNS_DIR / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        task_suite_path = output_dir / "task_suite.json"
        task_suite = {
            "tasks": [
                {
                    "task_id": "MON1",
                    "group_id": "monitor",
                    "title": str(payload.get("title") or "用户输入任务"),
                    "prompt": str(payload.get("prompt") or ""),
                    "documents": _string_list(payload.get("documents")),
                    "expected_agents": [],
                }
            ]
        }
        task_suite_path.write_text(
            json.dumps(task_suite, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        item = {
            "experiment_id": run_id,
            "run_id": run_id,
            "status": "running",
            "output_dir": str(output_dir),
            "error": "",
            "created_at": datetime.now().isoformat(),
        }
        with self._lock:
            self._items[run_id] = item

        thread = threading.Thread(
            target=self._run_experiment,
            args=(run_id, output_dir, task_suite_path, int(payload.get("rounds") or 1)),
            daemon=True,
        )
        thread.start()
        item["thread"] = thread
        return self.status(run_id)

    def status(self, experiment_id: str) -> dict[str, Any]:
        with self._lock:
            item = dict(self._items.get(experiment_id, {}))
        if not item:
            return {}
        item.pop("thread", None)
        return item

    def snapshot(self, experiment_id: str) -> dict[str, Any] | None:
        item = self.status(experiment_id)
        if not item:
            return None
        errors = [item["error"]] if item.get("error") else []
        return build_run_snapshot(
            Path(item["output_dir"]),
            run_id=item["run_id"],
            status=item["status"],
            errors=errors,
        )

    def _run_experiment(
        self, experiment_id: str, output_dir: Path, task_suite_path: Path, rounds: int
    ) -> None:
        try:
            modes: list[Mode] = ["baseline_text", "runtime_lite"]
            run_v0_benchmark(
                task_suite_paths=[task_suite_path],
                output_dir=output_dir,
                rounds=max(1, rounds),
                modes=modes,
                allow_estimated_tokens=True,
            )
        except Exception:
            with self._lock:
                self._items[experiment_id]["status"] = "failed"
                self._items[experiment_id]["error"] = traceback.format_exc()
            return
        with self._lock:
            self._items[experiment_id]["status"] = "succeeded"


REGISTRY = ExperimentRegistry()


class MonitorHandler(SimpleHTTPRequestHandler):
    server_version = "WorkflowMonitor/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/runs":
            self._json({"runs": list_runs(RUNS_DIR)})
            return
        if path == "/api/sessions":
            self._json({"sessions": list_sessions(AGENTLITE_DATA_DIR)})
            return
        if path.startswith("/api/runs/") and path.endswith("/snapshot"):
            run_id = unquote(path[len("/api/runs/") : -len("/snapshot")].strip("/"))
            run_dir = _safe_run_dir(run_id)
            if run_dir is None:
                self._json({"error": "run not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(build_run_snapshot(run_dir, run_id=run_id))
            return
        if path.startswith("/api/sessions/") and path.endswith("/snapshot"):
            session_id = unquote(
                path[len("/api/sessions/") : -len("/snapshot")].strip("/")
            )
            session_dir = _safe_session_dir(session_id)
            if session_dir is None:
                self._json({"error": "session not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(build_session_snapshot(session_dir, session_id=session_id))
            return
        if path.startswith("/api/experiments/") and path.endswith("/snapshot"):
            experiment_id = unquote(
                path[len("/api/experiments/") : -len("/snapshot")].strip("/")
            )
            snapshot = REGISTRY.snapshot(experiment_id)
            if snapshot is None:
                self._json({"error": "experiment not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(snapshot)
            return
        if path.startswith("/api/experiments/") and path.endswith("/status"):
            experiment_id = unquote(
                path[len("/api/experiments/") : -len("/status")].strip("/")
            )
            status = REGISTRY.status(experiment_id)
            if not status:
                self._json({"error": "experiment not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json(status)
            return
        return self._static_get(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") == "/api/experiments":
            payload = self._read_json_body()
            if not str(payload.get("prompt") or "").strip():
                self._json({"error": "prompt is required"}, HTTPStatus.BAD_REQUEST)
                return
            engine = str(payload.get("engine") or "deterministic")
            if engine != "deterministic":
                self._json(
                    {"error": "only deterministic engine is available in v1"},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self._json(REGISTRY.create(payload), HTTPStatus.ACCEPTED)
            return
        self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _static_get(self, path: str) -> None:
        if path in {"/", "/index.html"}:
            target = DEMO_DIR / "index.html"
        elif path.startswith("/static/"):
            target = DEMO_DIR / path[len("/static/") :]
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            resolved = target.resolve()
            resolved.relative_to(DEMO_DIR.resolve())
        except ValueError:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        if not resolved.exists() or not resolved.is_file():
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = _content_type(resolved)
        data = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main() -> int:
    global RUNS_DIR, AGENTLITE_DATA_DIR
    parser = argparse.ArgumentParser(description="Run the multi-agent workflow monitor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--data-dir", type=Path, default=AGENTLITE_DATA_DIR)
    args = parser.parse_args()
    RUNS_DIR = args.runs_dir.expanduser().resolve()
    AGENTLITE_DATA_DIR = args.data_dir.expanduser().resolve()
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    AGENTLITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), MonitorHandler)
    print(f"Workflow monitor running at http://{args.host}:{args.port}")
    print(f"Runs directory: {RUNS_DIR}")
    print(f"AgentLite data directory: {AGENTLITE_DATA_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _safe_run_dir(run_id: str) -> Path | None:
    if not run_id or "/" in run_id or "\\" in run_id:
        return None
    run_dir = (RUNS_DIR / run_id).resolve()
    try:
        run_dir.relative_to(RUNS_DIR.resolve())
    except ValueError:
        return None
    return run_dir if run_dir.exists() and run_dir.is_dir() else None


def _safe_session_dir(session_id: str) -> Path | None:
    if not session_id or "/" in session_id or "\\" in session_id:
        return None
    session_dir = (AGENTLITE_DATA_DIR / "sessions" / session_id).resolve()
    try:
        session_dir.relative_to((AGENTLITE_DATA_DIR / "sessions").resolve())
    except ValueError:
        return None
    return session_dir if session_dir.exists() and session_dir.is_dir() else None


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml"
    return "application/octet-stream"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


if __name__ == "__main__":
    raise SystemExit(main())
