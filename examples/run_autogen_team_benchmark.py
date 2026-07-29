from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
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
from agent_runtime.drivers.autogen import DRIVER_PHASE  # noqa: E402
from agent_runtime.launcher import (  # noqa: E402
    LaunchRequest,
    ManagedProcessLauncher,
    read_bootstrap_status,
)
from run_autogen_broadcast_shadow_smoke import (  # noqa: E402
    summarize_agent_real_rewrite_events,
    summarize_broadcast_shadow_events,
)
from run_autogen_native_smoke import _missing_modules, _read_trace_events  # noqa: E402
from run_autogen_team_rewrite_smoke import (  # noqa: E402
    summarize_team_rewrite_events,
)
from release_gate_evidence import classify_team_takeover_path  # noqa: E402

EXPECTED_PHASE = DRIVER_PHASE
USER_SCRIPT = PROJECT_ROOT / "examples" / "autogen_team_benchmark_app.py"
NATIVE_MARKER = "TEAM_BENCH_NATIVE_MARKER"
TEAM_REWRITE_MARKER = "AGENTLITE_TEAM_REAL_REWRITE v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare native AutoGen Team execution with AgentLite-managed "
            "Team-entry rewriting on the same AutoGen-only user program."
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.13h-autogen-team-benchmark-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report = run_benchmark(output_dir=output_dir, python=args.python)
    report_path = output_dir / "autogen_team_benchmark_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "autogen_team_benchmark_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_benchmark(*, output_dir: Path, python: str) -> dict[str, Any]:
    native = run_native(output_dir=output_dir / "native", python=python)
    managed = run_managed(output_dir=output_dir / "managed", python=python)
    comparison = build_comparison(native=native, managed=managed)
    source_text = USER_SCRIPT.read_text(encoding="utf-8")
    checks = build_checks(
        native=native,
        managed=managed,
        comparison=comparison,
        source_text=source_text,
    )
    return {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "checks": checks,
        "comparison": comparison,
        "native": native,
        "managed": managed,
    }


def run_native(*, output_dir: Path, python: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app_output = output_dir / "autogen_team_benchmark_native_output.json"
    env = _agentlite_free_environment(os.environ)
    env.update(
        {
            "AGENTLITE_AUTOGEN_TEAM_BENCHMARK_MODE": "native",
            "AGENTLITE_AUTOGEN_TEAM_BENCHMARK_OUTPUT": str(app_output),
        }
    )
    completed = subprocess.run(
        [python, str(USER_SCRIPT)],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    app_payload = _load_json(app_output)
    return {
        "mode": "native",
        "returncode": completed.returncode,
        "stdout_preview": _preview(completed.stdout),
        "stderr_preview": _preview(completed.stderr),
        "app_output_path": str(app_output),
        "app_payload": app_payload,
        "first_stream_item": _first_stream_item(app_payload),
        "quality": _quality(app_payload),
    }


def run_managed(*, output_dir: Path, python: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app_output = output_dir / "autogen_team_benchmark_managed_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[python, str(USER_SCRIPT)],
        cwd=PROJECT_ROOT,
        data_dir=output_dir / "agentlite",
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
        "AGENTLITE_AUTOGEN_HANDOFF_REWRITE": "1",
        "AGENTLITE_AUTOGEN_TOOL_SUMMARY_REWRITE": "1",
        "AGENTLITE_AUTOGEN_TEAM_BENCHMARK_MODE": "managed",
        "AGENTLITE_AUTOGEN_TEAM_BENCHMARK_OUTPUT": str(app_output),
    }
    result = ManagedProcessLauncher().launch(request, environ=env)
    status = read_bootstrap_status(result.status_file) or {}
    details = status.get("driver_details", {})
    if not isinstance(details, dict):
        details = {}
    trace_path = Path(str(details.get("trace_path", "")))
    trace_events = _read_trace_events(trace_path)
    event_counts = Counter(str(event.get("event_type", "")) for event in trace_events)
    team_events = _events_by_type(trace_events, "autogen_team_input_real_rewrite")
    broadcast_events = _events_by_type(trace_events, "autogen_broadcast_replacement_shadow")
    agent_rewrite_events = _events_by_type(trace_events, "autogen_agent_input_real_rewrite")
    app_payload = _load_json(app_output)
    return {
        "mode": "managed",
        "returncode": result.returncode,
        "bootstrap_ok": bool(status.get("ok")),
        "hooks_active": bool(status.get("hooks_active")),
        "driver_phase": details.get("phase", ""),
        "broadcast_mode": details.get("broadcast_mode", ""),
        "team_rewrite_enabled": bool(details.get("team_rewrite_enabled")),
        "handoff_rewrite_enabled": bool(details.get("handoff_rewrite_enabled")),
        "tool_summary_rewrite_enabled": bool(
            details.get("tool_summary_rewrite_enabled")
        ),
        "trace_path": str(trace_path) if trace_path else "",
        "app_output_path": str(app_output),
        "trace_event_counts": dict(sorted(event_counts.items())),
        "app_payload": app_payload,
        "first_stream_item": _first_stream_item(app_payload),
        "quality": _quality(app_payload),
        "team_input_real_rewrite": summarize_team_rewrite_events(team_events),
        "broadcast_replacement_shadow": summarize_broadcast_shadow_events(
            broadcast_events
        ),
        "agent_input_real_rewrite": summarize_agent_real_rewrite_events(
            agent_rewrite_events
        ),
    }


def build_comparison(
    *,
    native: dict[str, Any],
    managed: dict[str, Any],
) -> dict[str, Any]:
    counter = TokenCounter(allow_estimate=True)
    native_first = native.get("first_stream_item", {})
    managed_first = managed.get("first_stream_item", {})
    native_first_text = (
        str(native_first.get("content", "")) if isinstance(native_first, dict) else ""
    )
    managed_first_text = (
        str(managed_first.get("content", "")) if isinstance(managed_first, dict) else ""
    )
    native_visible_tokens = counter.count(native_first_text).token_count
    managed_visible_tokens = counter.count(managed_first_text).token_count
    team = managed.get("team_input_real_rewrite", {})
    if not isinstance(team, dict):
        team = {}
    native_quality = native.get("quality", {})
    managed_quality = managed.get("quality", {})
    return {
        "tokenizer": counter.describe(),
        "native_first_stream_tokens": native_visible_tokens,
        "managed_first_stream_tokens": managed_visible_tokens,
        "visible_input_token_delta_native_minus_managed": (
            native_visible_tokens - managed_visible_tokens
        ),
        "visible_input_token_reduction_ratio": _ratio_delta(
            native_visible_tokens,
            managed_visible_tokens,
        ),
        "team_native_task_tokens": team.get("native_task_tokens", 0),
        "team_rewritten_task_tokens": team.get("rewritten_task_tokens", 0),
        "team_token_delta_native_task_minus_rewrite": team.get(
            "token_delta_native_task_minus_rewrite",
            0,
        ),
        "team_native_full_broadcast_tokens": team.get(
            "native_full_broadcast_tokens",
            0,
        ),
        "team_wire_plus_prompt_view_tokens": team.get(
            "wire_plus_prompt_view_tokens",
            0,
        ),
        "team_token_delta_native_broadcast_minus_rewrite": team.get(
            "token_delta_native_broadcast_minus_rewrite",
            0,
        ),
        "native_quality_score": _score(native_quality),
        "managed_quality_score": _score(managed_quality),
        "quality_delta_managed_minus_native": _score(managed_quality)
        - _score(native_quality),
        "native_quality_flags": native_quality.get("flags", {})
        if isinstance(native_quality, dict)
        else {},
        "managed_quality_flags": managed_quality.get("flags", {})
        if isinstance(managed_quality, dict)
        else {},
    }


def build_checks(
    *,
    native: dict[str, Any],
    managed: dict[str, Any],
    comparison: dict[str, Any],
    source_text: str,
) -> dict[str, bool]:
    native_first = native.get("first_stream_item", {})
    managed_first = managed.get("first_stream_item", {})
    team = managed.get("team_input_real_rewrite", {})
    if not isinstance(team, dict):
        team = {}
    takeover_path = _takeover_path_from_team_summary(team)
    managed_caller_native = (
        isinstance(managed_first, dict)
        and TEAM_REWRITE_MARKER not in str(managed_first.get("content", ""))
        and NATIVE_MARKER in str(managed_first.get("content", ""))
    )
    if takeover_path == "applied_rewrite":
        managed_caller_output_safe = (
            managed_caller_native
            and int(
                managed.get("trace_event_counts", {}).get(
                    "autogen_team_display_restored", 0
                )
                or 0
            )
            >= 1
        )
    else:
        managed_caller_output_safe = (
            takeover_path == "cost_guarded_fallback"
            and managed_caller_native
        )
    return {
        "native_returncode_zero": int(native.get("returncode", 1)) == 0,
        "managed_returncode_zero": int(managed.get("returncode", 1)) == 0,
        "managed_bootstrap_ok": bool(managed.get("bootstrap_ok")),
        "managed_hooks_active": bool(managed.get("hooks_active")),
        "driver_phase_current": managed.get("driver_phase") == EXPECTED_PHASE,
        "managed_real_rewrite_mode": managed.get("broadcast_mode") == "real-rewrite",
        "managed_team_rewrite_enabled": bool(managed.get("team_rewrite_enabled")),
        "user_script_does_not_import_agentlite": (
            "from agent_runtime" not in source_text
            and "import agent_runtime" not in source_text
            and "agent_runtime." not in source_text
        ),
        "native_not_agentlite_active": not bool(
            native.get("app_payload", {}).get("agentlite_active")
        ),
        "managed_agentlite_active": bool(
            managed.get("app_payload", {}).get("agentlite_active")
        ),
        "native_sees_native_task": (
            isinstance(native_first, dict)
            and NATIVE_MARKER in str(native_first.get("content", ""))
            and TEAM_REWRITE_MARKER not in str(native_first.get("content", ""))
        ),
        "managed_caller_output_safe": managed_caller_output_safe,
        "team_takeover_event_recorded": int(team.get("event_count", 0) or 0)
        >= 1,
        "team_rewrite_applied_or_cost_guarded": takeover_path
        in {"applied_rewrite", "cost_guarded_fallback"},
        "team_real_message_boundary_safe": (
            takeover_path == "applied_rewrite"
            and int(team.get("real_message_mutation_count", 0) or 0) >= 1
        )
        or (
            takeover_path == "cost_guarded_fallback"
            and int(team.get("real_message_mutation_count", 0) or 0) == 0
        ),
        "team_cost_boundary_safe": (
            takeover_path == "applied_rewrite"
            and int(
                team.get("token_delta_native_task_minus_rewrite", 0) or 0
            )
            > 0
            and int(
                team.get(
                    "token_delta_native_broadcast_minus_rewrite",
                    0,
                )
                or 0
            )
            > 0
        )
        or takeover_path == "cost_guarded_fallback",
        "caller_visible_input_semantics_preserved": int(
            comparison.get("visible_input_token_delta_native_minus_managed", -1)
            or 0
        )
        == 0,
        "quality_not_lower": int(
            comparison.get("quality_delta_managed_minus_native", -999) or 0
        )
        >= 0,
        "both_final_outputs_complete": (
            _score(native.get("quality", {})) == _max_score(native.get("quality", {}))
            and _score(managed.get("quality", {}))
            == _max_score(managed.get("quality", {}))
        ),
    }


def _takeover_path_from_team_summary(team: dict[str, Any]) -> str:
    return classify_team_takeover_path(
        {
            "team_applied_count": int(team.get("applied_count", 0) or 0),
            "team_fallback_count": int(team.get("fallback_count", 0) or 0),
            "real_message_mutation_count": int(
                team.get("real_message_mutation_count", 0) or 0
            ),
            "task_token_savings": int(
                team.get("token_delta_native_task_minus_rewrite", 0) or 0
            ),
            "broadcast_token_savings": int(
                team.get(
                    "token_delta_native_broadcast_minus_rewrite",
                    0,
                )
                or 0
            ),
            "team_fallback_reasons": list(
                team.get("fallback_reasons", []) or []
            ),
        }
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# v5.13h AutoGen Team Benchmark Report",
        "",
        f"passed: `{str(report.get('passed')).lower()}`",
        "",
        "## Checks",
        "",
    ]
    checks = report.get("checks", {})
    if isinstance(checks, dict):
        for key, value in checks.items():
            lines.append(f"- `{key}`: `{str(value).lower()}`")
    comparison = report.get("comparison", {})
    lines.extend(["", "## Comparison", ""])
    if isinstance(comparison, dict):
        for key in (
            "native_first_stream_tokens",
            "managed_first_stream_tokens",
            "visible_input_token_delta_native_minus_managed",
            "visible_input_token_reduction_ratio",
            "team_native_task_tokens",
            "team_rewritten_task_tokens",
            "team_token_delta_native_task_minus_rewrite",
            "team_native_full_broadcast_tokens",
            "team_wire_plus_prompt_view_tokens",
            "team_token_delta_native_broadcast_minus_rewrite",
            "native_quality_score",
            "managed_quality_score",
            "quality_delta_managed_minus_native",
        ):
            lines.append(f"- `{key}`: `{comparison.get(key, 0)}`")
    managed = report.get("managed", {})
    team = managed.get("team_input_real_rewrite", {}) if isinstance(managed, dict) else {}
    agent_rewrite = (
        managed.get("agent_input_real_rewrite", {}) if isinstance(managed, dict) else {}
    )
    lines.extend(["", "## Managed Rewrite", ""])
    if isinstance(team, dict):
        lines.append(
            "- team takeover_path: "
            f"`{_takeover_path_from_team_summary(team)}`"
        )
        lines.append(f"- team fallback_count（安全回退次数）: `{team.get('fallback_count', 0)}`")
        lines.append(f"- team fallback_reasons: `{team.get('fallback_reasons', [])}`")
        lines.append(f"- team applied_count: `{team.get('applied_count', 0)}`")
    if isinstance(agent_rewrite, dict):
        lines.append(
            f"- agent fallback_count（安全回退次数）: "
            f"`{agent_rewrite.get('fallback_count', 0)}`"
        )
        lines.append(
            f"- agent applied_count: `{agent_rewrite.get('applied_count', 0)}`"
        )
    lines.extend(["", "## First Stream Items", ""])
    for label in ("native", "managed"):
        section = report.get(label, {})
        first = (
            section.get("first_stream_item", {}) if isinstance(section, dict) else {}
        )
        if not isinstance(first, dict):
            first = {}
        lines.extend(
            [
                f"### {label}",
                "",
                f"- type: `{first.get('type', '')}`",
                f"- source: `{first.get('source', '')}`",
                f"- content_chars: `{first.get('content_chars', 0)}`",
                f"- contains_team_rewrite_marker: `{first.get('contains_team_rewrite_marker', False)}`",
                f"- native_marker_count: `{first.get('native_marker_count', 0)}`",
                f"- content_preview: `{first.get('content_preview', '')}`",
                "",
            ]
        )
    lines.extend(["## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    managed_section = report.get("managed", {})
    if isinstance(managed_section, dict):
        lines.append(f"- trace_path: `{managed_section.get('trace_path', '')}`")
        lines.append(
            f"- managed_app_output_path: `{managed_section.get('app_output_path', '')}`"
        )
    native_section = report.get("native", {})
    if isinstance(native_section, dict):
        lines.append(
            f"- native_app_output_path: `{native_section.get('app_output_path', '')}`"
        )
    return "\n".join(lines) + "\n"


def _agentlite_free_environment(source: os._Environ[str]) -> dict[str, str]:
    return {key: value for key, value in source.items() if not key.startswith("AGENTLITE_")}


def _events_by_type(
    trace_events: list[dict[str, object]],
    event_type: str,
) -> list[dict[str, Any]]:
    return [
        event
        for event in trace_events
        if event.get("event_type") == event_type and isinstance(event.get("payload"), dict)
    ]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _first_stream_item(app_payload: dict[str, Any]) -> dict[str, Any]:
    items = app_payload.get("stream_items", []) if isinstance(app_payload, dict) else []
    first = items[0] if isinstance(items, list) and items else {}
    return first if isinstance(first, dict) else {}


def _quality(app_payload: dict[str, Any]) -> dict[str, Any]:
    task_result = (
        app_payload.get("task_result", {}) if isinstance(app_payload, dict) else {}
    )
    if not isinstance(task_result, dict):
        return {}
    quality = task_result.get("quality", {})
    return quality if isinstance(quality, dict) else {}


def _score(quality: dict[str, Any]) -> int:
    if isinstance(quality, dict):
        return int(quality.get("score", 0) or 0)
    return 0


def _max_score(quality: dict[str, Any]) -> int:
    if isinstance(quality, dict):
        return int(quality.get("max_score", 0) or 0)
    return 0


def _ratio_delta(native_tokens: int, managed_tokens: int) -> float:
    if native_tokens <= 0:
        return 0.0
    return round((native_tokens - managed_tokens) / native_tokens, 6)


def _preview(text: str, limit: int = 600) -> str:
    return " ".join(str(text or "").split())[:limit]


if __name__ == "__main__":
    raise SystemExit(main())
