from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.drivers.autogen_codec import AutoGenMessageCodec  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate AutoGen Message Codec against real AutoGen messages."
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = PROJECT_ROOT / "runs" / f"v5.12e-autogen-codec-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        messages = build_real_autogen_messages()
    except ImportError as exc:
        print(f"AutoGen is not installed: {exc}", file=sys.stderr)
        return 2

    codec = AutoGenMessageCodec()
    decoded = [codec.decode(message).to_dict() for message in messages]
    kinds = [item["message_kind"] for item in decoded]
    checks = {
        "text_message_decoded": "text" in kinds,
        "handoff_message_decoded": "handoff" in kinds,
        "tool_call_decoded": "tool_call" in kinds,
        "tool_result_decoded": "tool_result" in kinds,
        "tool_summary_decoded": "tool_summary" in kinds,
        "handoff_target_preserved": any(
            item["message_kind"] == "handoff" and item["target"] == "writer"
            for item in decoded
        ),
        "tool_name_preserved": any(
            call.get("name") == "lookup"
            for item in decoded
            for call in item.get("tool_calls", [])
        ),
        "tool_result_content_preserved": any(
            result.get("content") == "ok"
            for item in decoded
            for result in item.get("tool_results", [])
        ),
    }
    report: dict[str, Any] = {
        "passed": all(checks.values()),
        "checks": checks,
        "decoded_message_count": len(decoded),
        "decoded_message_kinds": kinds,
        "decoded_messages": decoded,
    }
    report_path = output_dir / "autogen_codec_smoke_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_codec_smoke_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def build_real_autogen_messages() -> list[Any]:
    from autogen_agentchat.messages import (  # type: ignore
        HandoffMessage,
        TextMessage,
        ToolCallExecutionEvent,
        ToolCallRequestEvent,
        ToolCallSummaryMessage,
    )
    from autogen_core import FunctionCall  # type: ignore
    from autogen_core.models import FunctionExecutionResult  # type: ignore

    call = FunctionCall(
        id="call_1",
        name="lookup",
        arguments='{"query": "AgentLite"}',
    )
    result = FunctionExecutionResult(
        call_id="call_1",
        name="lookup",
        content="ok",
        is_error=False,
    )
    return [
        TextMessage(source="writer", content="hello"),
        HandoffMessage(source="planner", target="writer", content="please continue"),
        ToolCallRequestEvent(source="assistant", content=[call]),
        ToolCallExecutionEvent(source="assistant", content=[result]),
        ToolCallSummaryMessage(
            source="assistant",
            content="lookup ok",
            tool_calls=[call],
            results=[result],
        ),
    ]


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Codec Smoke Report",
        "",
        f"passed: `{str(report.get('passed')).lower()}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in report.get("checks", {}).items():
        lines.append(f"- `{key}`: `{str(value).lower()}`")
    lines.extend(["", "## Decoded Message Kinds", ""])
    for item in report.get("decoded_message_kinds", []):
        lines.append(f"- `{item}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
