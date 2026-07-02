from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _missing_dependency(exc: ImportError) -> int:
    print(
        "AutoGen smoke dependencies are not installed. "
        "Install with: python -m pip install -e \".[autogen]\"",
        file=sys.stderr,
    )
    print(f"ImportError: {exc}", file=sys.stderr)
    return 2


try:
    from autogen_agentchat.agents import UserProxyAgent
    from autogen_agentchat.base import TaskResult
    from autogen_agentchat.conditions import TextMentionTermination
    from autogen_agentchat.teams import RoundRobinGroupChat
except ImportError as exc:
    raise SystemExit(_missing_dependency(exc))


NATIVE_MARKER = "WEB_BACKEND_NATIVE_MARKER"


def _make_input(reply: str):
    async def input_func(*_args: Any, **_kwargs: Any) -> str:
        return reply

    return input_func


def _long_task(prefix: str) -> str:
    paragraph = (
        "{prefix} web backend section {idx}: this request body simulates a "
        "browser or service call reaching an AutoGen backend process. It "
        "carries routing notes, review requirements, retrieval hints, state "
        "transfer notes, artifact constraints, and enough neutral filler before "
        "the marker so compact Prompt Views should not contain it. {marker} "
        "The Team entry rewrite should move this whole HTTP-originated task "
        "body into the StatePool before RoundRobinGroupChat distributes it."
    )
    return "\n".join(
        paragraph.format(prefix=prefix, idx=index, marker=NATIVE_MARKER)
        for index in range(1, 22)
    )


def _message_to_dict(message: Any) -> dict[str, Any]:
    content = str(getattr(message, "content", ""))
    return {
        "type": type(message).__name__,
        "source": getattr(message, "source", ""),
        "target": getattr(message, "target", ""),
        "content_chars": len(content),
        "content_preview": " ".join(content.split())[:320],
        "contains_team_rewrite_marker": "AGENTLITE_TEAM_REAL_REWRITE v1" in content,
        "contains_state_pool_marker": (
            "native_team_task_moved_to_state_pool=true" in content
        ),
        "contains_broadcast_manifest": "broadcast_manifest=" in content,
        "contains_receiver_prompt_views": "receiver_prompt_views:" in content,
        "contains_native_marker": NATIVE_MARKER in content,
        "native_marker_count": content.count(NATIVE_MARKER),
    }


def _task_result_to_dict(result: TaskResult) -> dict[str, Any]:
    messages = getattr(result, "messages", []) or []
    return {
        "type": type(result).__name__,
        "stop_reason": getattr(result, "stop_reason", ""),
        "message_count": len(messages),
        "messages": [_message_to_dict(message) for message in messages],
    }


async def _run_team(task: str) -> dict[str, Any]:
    planner = UserProxyAgent(
        "planner",
        input_func=_make_input("planner received compact HTTP-origin task."),
    )
    writer = UserProxyAgent(
        "writer",
        input_func=_make_input("writer continues compact HTTP-origin task."),
    )
    reviewer = UserProxyAgent(
        "reviewer",
        input_func=_make_input("DONE_WEB_BACKEND reviewer accepts compact task."),
    )
    team = RoundRobinGroupChat(
        [planner, writer, reviewer],
        termination_condition=TextMentionTermination("DONE_WEB_BACKEND"),
    )
    stream_items: list[dict[str, Any]] = []
    task_result: dict[str, Any] = {}
    async for item in team.run_stream(task=task):
        if isinstance(item, TaskResult):
            task_result = _task_result_to_dict(item)
        else:
            stream_items.append(_message_to_dict(item))
    return {
        "stream_items": stream_items,
        "task_result": task_result,
    }


class AutoGenBackendHandler(BaseHTTPRequestHandler):
    server_version = "AgentLiteAutoGenWebSmoke/1.0"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length)
        try:
            request_payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            request_payload = {}
        prefix = str(request_payload.get("prefix", "request"))
        task = _long_task(prefix)
        result = asyncio.run(_run_team(task))
        payload = {
            "scenario": "autogen_web_backend_smoke",
            "request_path": self.path,
            "agentlite_active": os.getenv("AGENTLITE_AUTOGEN_DRIVER_ACTIVE") == "1",
            "agentlite_session_id": os.getenv("AGENTLITE_ACTIVE_SESSION_ID", ""),
            "broadcast_mode": os.getenv("AGENTLITE_AUTOGEN_BROADCAST_MODE", ""),
            "team_rewrite_env": os.getenv("AGENTLITE_AUTOGEN_TEAM_REWRITE", ""),
            **result,
        }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), AutoGenBackendHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://{host}:{port}/run",
            data=json.dumps({"prefix": "web-smoke"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
            response_payload = json.loads(response_body)
            response_payload["http_status"] = response.status
            response_payload["server_address"] = f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    output_path = os.getenv("AGENTLITE_AUTOGEN_WEB_BACKEND_OUTPUT")
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(
            json.dumps(response_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        print(json.dumps(response_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
