from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXAMPLES_DIR = Path(__file__).resolve().parent
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

from agent_runtime.eval.token_counter import TokenCounter  # noqa: E402
from agent_runtime.launcher import (  # noqa: E402
    LaunchRequest,
    ManagedProcessLauncher,
    read_bootstrap_status,
)
from run_autogen_native_smoke import (  # noqa: E402
    _missing_modules,
    _read_trace_events,
    build_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a long-content native AutoGen task under AgentLite and compare "
            "native text, compact SHP shadow wire envelopes, and prompt views."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--skip-dependency-check",
        action="store_true",
        help="Launch anyway even if AutoGen modules are not importable yet.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.skip_dependency_check:
        missing = _missing_modules()
        if missing:
            print(
                "Missing AutoGen modules: " + ", ".join(missing),
                file=sys.stderr,
            )
            print(
                'Install after confirmation with: python -m pip install -e ".[autogen]"',
                file=sys.stderr,
            )
            return 2

    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = PROJECT_ROOT / "runs" / f"v5.12g-autogen-long-shadow-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    app_output = output_dir / "autogen_long_shadow_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            args.python,
            str(PROJECT_ROOT / "examples" / "autogen_long_context_shadow_smoke.py"),
        ],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_LONG_SMOKE_OUTPUT": str(app_output),
    }
    result = ManagedProcessLauncher().launch(request, environ=env)
    status = read_bootstrap_status(result.status_file)
    report = build_report(
        output_dir=output_dir,
        returncode=result.returncode,
        status=status or {},
        app_output=app_output,
    )
    augment_long_context_report(report)
    report_path = output_dir / "autogen_long_shadow_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_long_shadow_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def augment_long_context_report(report: dict[str, Any]) -> None:
    trace_path = Path(str(report.get("trace_path", "")))
    trace_events = _read_trace_events(trace_path)
    token_counter = TokenCounter(allow_estimate=True)
    prompt_views = build_prompt_view_samples(trace_events)
    prompt_view_tokens = sum(
        token_counter.count(view).token_count for view in prompt_views
    )
    shadow_cost = report.get("shp_shadow_cost", {})
    if not isinstance(shadow_cost, dict):
        shadow_cost = {}
    native_tokens = int(shadow_cost.get("native_output_text_tokens", 0) or 0)
    wire_tokens = int(shadow_cost.get("shp_shadow_envelope_tokens", 0) or 0)
    audit_tokens = int(
        shadow_cost.get("shp_shadow_audit_envelope_tokens", 0) or 0
    )
    wire_plus_prompt_view_tokens = wire_tokens + prompt_view_tokens
    cost_comparison = {
        "native_output_text_tokens": native_tokens,
        "shadow_wire_tokens": wire_tokens,
        "state_prompt_view_tokens": prompt_view_tokens,
        "shadow_wire_plus_prompt_view_tokens": wire_plus_prompt_view_tokens,
        "shadow_audit_envelope_tokens": audit_tokens,
        "wire_token_delta_native_minus_wire": native_tokens - wire_tokens,
        "wire_plus_prompt_delta_native_minus_runtime": (
            native_tokens - wire_plus_prompt_view_tokens
        ),
        "wire_reduction_ratio": _ratio(native_tokens - wire_tokens, native_tokens),
        "wire_plus_prompt_view_reduction_ratio": _ratio(
            native_tokens - wire_plus_prompt_view_tokens,
            native_tokens,
        ),
        "prompt_view_count": len(prompt_views),
        "token_counter": token_counter.describe(),
    }
    report["long_context_cost_comparison"] = cost_comparison
    checks = report.get("checks", {})
    if isinstance(checks, dict):
        checks["long_context_native_tokens_exceed_wire"] = native_tokens > wire_tokens
        checks["wire_plus_prompt_view_tokens_exceed_zero"] = (
            wire_plus_prompt_view_tokens > 0
        )
        checks["wire_plus_prompt_view_beats_native_text"] = (
            native_tokens > wire_plus_prompt_view_tokens
        )
        checks["audit_envelope_not_counted_as_wire"] = audit_tokens >= wire_tokens
        report["passed"] = all(bool(value) for value in checks.values())


def build_prompt_view_samples(
    trace_events: list[dict[str, Any]],
) -> list[str]:
    views: list[str] = []
    for event in trace_events:
        if event.get("event_type") != "state_written":
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        state = payload.get("state", {})
        if not isinstance(state, dict):
            continue
        view = render_state_prompt_view_from_trace(state)
        if view:
            views.append(view)
    return views


def render_state_prompt_view_from_trace(state: dict[str, Any]) -> str:
    state_type = str(state.get("state_type", ""))
    state_id = str(state.get("state_id", ""))
    summary = str(state.get("summary", ""))
    tier = str(state.get("tier", ""))
    if not state_id:
        return ""
    if state_type != "artifact_state":
        return f"[{state_type}:{state_id}] {summary}; tier={tier}"
    payload = _load_payload(Path(str(state.get("payload_ref", ""))))
    artifact_id = payload.get("code_artifact_id") or payload.get("artifact_id") or "n/a"
    sha256 = str(payload.get("sha256", "n/a"))[:12]
    return (
        f"[artifact_state:{state_id}] {summary}; "
        f"artifact={artifact_id}; sha256={sha256}; "
        f"tier={tier}; raw_content=cold_audit_only"
    )


def render_markdown(report: dict[str, Any]) -> str:
    checks = report.get("checks", {})
    if not isinstance(checks, dict):
        checks = {}
    lines = [
        "# AutoGen Long Shadow Handoff Report",
        "",
        f"passed: `{str(report.get('passed')).lower()}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        lines.append(f"- `{key}`: `{str(value).lower()}`")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- output_dir: `{report.get('output_dir', '')}`",
            f"- app_output_path: `{report.get('app_output_path', '')}`",
            f"- trace_path: `{report.get('trace_path', '')}`",
            "",
            "## Trace Event Counts",
            "",
        ]
    )
    event_counts = report.get("trace_event_counts", {})
    if isinstance(event_counts, dict):
        for key, value in event_counts.items():
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Long Context Cost", ""])
    cost = report.get("long_context_cost_comparison", {})
    if isinstance(cost, dict):
        for key, value in cost.items():
            lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines) + "\n"


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _ratio(delta: int, baseline: int) -> float:
    if baseline <= 0:
        return 0.0
    return round(delta / baseline, 6)


if __name__ == "__main__":
    raise SystemExit(main())
