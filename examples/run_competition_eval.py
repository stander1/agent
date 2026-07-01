from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.core.models import Mode
from agent_runtime.eval.competition_report import (
    build_competition_report,
    load_suite_run,
    write_report_files,
)
from agent_runtime.eval.llm_benchmark_runner import run_llm_benchmark
from agent_runtime.llm.config import load_llm_config


SUITE_PRESETS = {
    "A": ("旅行规划连续任务", PROJECT_ROOT / "benchmarks" / "travel_task_group_a.json"),
    "B": ("安全应急连续任务", PROJECT_ROOT / "benchmarks" / "security_task_group_b.json"),
}

MODE_PRESETS: dict[str, list[Mode]] = {
    "both": ["baseline_bounded_nl_framework", "runtime_lite"],
    "fair": ["baseline_bounded_nl_framework", "runtime_lite"],
    "stress": ["baseline_stress_full_broadcast", "runtime_lite"],
    "all": [
        "baseline_stress_full_broadcast",
        "baseline_bounded_nl_framework",
        "runtime_lite",
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run v5.0 competition evaluation.")
    parser.add_argument("--provider", default="mimo")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "llm.mimo.example.json")
    parser.add_argument("--suite", action="append", choices=sorted(SUITE_PRESETS), default=None)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument(
        "--mode",
        choices=[
            "baseline_text",
            "baseline_stress_full_broadcast",
            "baseline_bounded_nl_framework",
            "runtime_lite",
            "both",
            "fair",
            "stress",
            "all",
        ],
        default="both",
    )
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--retry-backoff-seconds", type=float, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--tokenizer-name", default=None)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--allow-estimated-tokens", action="store_true")
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Only summarize existing suite output dirs under output-root.",
    )
    parser.add_argument(
        "--existing-run",
        action="append",
        default=[],
        metavar="SUITE=PATH",
        help="Use an existing suite run directory, for example A=runs/v4.2-mimo-full-a.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suite_ids = args.suite or ["A", "B"]
    existing_runs = parse_existing_runs(args.existing_run)
    output_root = args.output_root
    if output_root is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_root = PROJECT_ROOT / "runs" / f"v5.0-competition-{args.provider}-{stamp}"
    output_root.mkdir(parents=True, exist_ok=True)

    modes: list[Mode] = MODE_PRESETS.get(args.mode, [args.mode])
    llm_config = load_llm_config(
        args.config,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        temperature=args.temperature,
        top_p=args.top_p,
    )

    suite_runs = []
    for suite_id in suite_ids:
        suite_name, suite_path = SUITE_PRESETS[suite_id]
        suite_output_dir = existing_runs.get(suite_id, output_root / suite_id.lower())
        if not args.skip_run:
            if suite_id in existing_runs:
                raise SystemExit(
                    f"--existing-run {suite_id}=... cannot be used unless --skip-run is set."
                )
            run_llm_benchmark(
                task_suite_paths=[suite_path],
                output_dir=suite_output_dir,
                rounds=args.rounds,
                modes=modes,
                llm_config=llm_config,
                tokenizer_name=args.tokenizer_name,
                model_name=args.model_name,
                allow_estimated_tokens=args.allow_estimated_tokens,
                max_tasks=args.max_tasks,
            )
        suite_runs.append(
            load_suite_run(
                suite_id=suite_id,
                suite_name=suite_name,
                task_suite_path=suite_path,
                output_dir=suite_output_dir,
            )
        )

    report = build_competition_report(
        runs=suite_runs,
        provider=args.provider,
    )
    json_path, markdown_path = write_report_files(report=report, output_dir=output_root)
    print(
        json.dumps(
            {
                "output_root": str(output_root),
                "report_json": str(json_path),
                "report_markdown": str(markdown_path),
                "aggregate": report["aggregate"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def parse_existing_runs(values: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Invalid --existing-run value: {value}")
        suite_id, path_text = value.split("=", 1)
        suite_id = suite_id.strip().upper()
        if suite_id not in SUITE_PRESETS:
            raise SystemExit(f"Unknown suite id in --existing-run: {suite_id}")
        parsed[suite_id] = Path(path_text)
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
