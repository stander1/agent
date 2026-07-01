from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEAM_BENCHMARK_APP = PROJECT_ROOT / "examples" / "autogen_team_benchmark_app.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the AgentLite release gate for the current checkout."
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--skip-autogen-benchmark",
        action="store_true",
        help="Skip the heavier AutoGen benchmark and CLI rewrite smoke.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = PROJECT_ROOT / "runs" / f"release-gate-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    steps: list[dict[str, Any]] = []
    steps.append(
        run_command(
            name="cli_help",
            command=[args.python, "-m", "agent_runtime.cli", "--help"],
            output_dir=output_dir,
        )
    )
    steps.append(
        run_command(
            name="cli_version",
            command=[args.python, "-m", "agent_runtime.cli", "version"],
            output_dir=output_dir,
        )
    )
    steps.append(
        run_command(
            name="doctor_autogen",
            command=[
                args.python,
                "-m",
                "agent_runtime.cli",
                "doctor",
                "--framework",
                "autogen",
                "--json",
            ],
            output_dir=output_dir,
        )
    )
    if not args.skip_autogen_benchmark:
        steps.append(
            run_command(
                name="autogen_team_benchmark",
                command=[
                    args.python,
                    str(PROJECT_ROOT / "examples" / "run_autogen_team_benchmark.py"),
                    "--output-dir",
                    str(output_dir / "autogen_team_benchmark"),
                ],
                output_dir=output_dir,
            )
        )
        steps.append(
            run_cli_rewrite_smoke(
                output_dir=output_dir / "agentlite_cli_rewrite",
                python=args.python,
            )
        )
    steps.append(
        run_command(
            name="unittest",
            command=[args.python, "-m", "unittest", "discover", "-s", "tests"],
            output_dir=output_dir,
        )
    )
    steps.append(
        run_command(
            name="compileall",
            command=[args.python, "-m", "compileall", "agent_runtime", "examples", "tests"],
            output_dir=output_dir,
        )
    )
    steps.append(
        run_command(
            name="git_diff_check",
            command=["git", "diff", "--check"],
            output_dir=output_dir,
        )
    )

    report = {
        "passed": all(bool(step.get("passed")) for step in steps),
        "output_dir": str(output_dir),
        "steps": steps,
    }
    report_path = output_dir / "release_gate_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_path = output_dir / "release_gate_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Report: {report_path}")
    print(f"Markdown: {markdown_path}")
    return 0 if report["passed"] else 1


def run_command(
    *,
    name: str,
    command: list[str],
    output_dir: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    step_dir = output_dir / "steps"
    step_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = step_dir / f"{name}.stdout.txt"
    stderr_path = step_dir / f"{name}.stderr.txt"
    stdout_text = completed.stdout or ""
    stderr_text = completed.stderr or ""
    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    return {
        "name": name,
        "passed": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": command,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_preview": _preview(stdout_text),
        "stderr_preview": _preview(stderr_text),
    }


def run_cli_rewrite_smoke(*, output_dir: Path, python: str) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    app_output = output_dir / "autogen_team_benchmark_cli_output.json"
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_TEAM_BENCHMARK_MODE": "agentlite_cli",
        "AGENTLITE_AUTOGEN_TEAM_BENCHMARK_OUTPUT": str(app_output),
    }
    step = run_command(
        name="agentlite_cli_rewrite",
        command=[
            python,
            "-m",
            "agent_runtime.cli",
            "run",
            "--framework",
            "autogen",
            "--data-dir",
            str(output_dir / "agentlite"),
            "--rewrite",
            "all",
            "--",
            python,
            str(TEAM_BENCHMARK_APP),
        ],
        output_dir=output_dir,
        env=env,
    )
    payload = _load_json(app_output)
    first = _first_stream_item(payload)
    rewrite_ok = (
        bool(payload.get("agentlite_active"))
        and bool(first.get("contains_team_rewrite_marker"))
        and bool(first.get("contains_state_pool_marker"))
        and bool(first.get("contains_broadcast_manifest"))
        and bool(first.get("contains_receiver_prompt_views"))
        and int(first.get("native_marker_count", 0) or 0) == 0
    )
    step.update(
        {
            "passed": bool(step["passed"]) and rewrite_ok,
            "app_output_path": str(app_output),
            "agentlite_active": bool(payload.get("agentlite_active")),
            "first_stream_item": {
                "contains_team_rewrite_marker": bool(
                    first.get("contains_team_rewrite_marker")
                ),
                "contains_state_pool_marker": bool(
                    first.get("contains_state_pool_marker")
                ),
                "contains_broadcast_manifest": bool(
                    first.get("contains_broadcast_manifest")
                ),
                "contains_receiver_prompt_views": bool(
                    first.get("contains_receiver_prompt_views")
                ),
                "native_marker_count": int(first.get("native_marker_count", 0) or 0),
            },
        }
    )
    return step


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AgentLite Release Gate Report",
        "",
        f"passed: `{str(report.get('passed')).lower()}`",
        "",
        "## Steps",
        "",
    ]
    for step in report.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        lines.append(
            f"- `{step.get('name', '')}`: passed=`{str(step.get('passed')).lower()}`, "
            f"returncode=`{step.get('returncode', '')}`"
        )
    lines.extend(["", "## Artifacts", ""])
    lines.append(f"- output_dir: `{report.get('output_dir', '')}`")
    return "\n".join(lines) + "\n"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _first_stream_item(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("stream_items", []) if isinstance(payload, dict) else []
    first = items[0] if isinstance(items, list) and items else {}
    return first if isinstance(first, dict) else {}


def _preview(text: str, limit: int = 600) -> str:
    return " ".join(str(text or "").split())[:limit]


if __name__ == "__main__":
    raise SystemExit(main())
