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
from agent_runtime.eval.benchmark_runner import run_v0_benchmark


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run v0 baseline benchmark.")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument(
        "--mode",
        choices=[
            "baseline_text",
            "baseline_stress_full_broadcast",
            "baseline_bounded_nl_framework",
            "runtime_stub",
            "runtime_lite",
            "both",
            "fair",
            "stress",
            "all",
        ],
        default="both",
    )
    parser.add_argument(
        "--task-suite",
        action="append",
        type=Path,
        help="Path to a task suite JSON file. Can be passed multiple times.",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--tokenizer-name", default=None)
    parser.add_argument("--model-name", default=None)
    parser.add_argument(
        "--allow-estimated-tokens",
        action="store_true",
        help="Allow CJK-aware estimated token counts if no tokenizer backend is available.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suites = args.task_suite or [
        PROJECT_ROOT / "benchmarks" / "task_suite_a.json",
        PROJECT_ROOT / "benchmarks" / "task_suite_b.json",
    ]
    modes: list[Mode]
    if args.mode == "both":
        modes = ["baseline_bounded_nl_framework", "runtime_lite"]
    elif args.mode == "fair":
        modes = ["baseline_bounded_nl_framework", "runtime_lite"]
    elif args.mode == "stress":
        modes = ["baseline_stress_full_broadcast", "runtime_lite"]
    elif args.mode == "all":
        modes = [
            "baseline_stress_full_broadcast",
            "baseline_bounded_nl_framework",
            "runtime_stub",
            "runtime_lite",
        ]
    else:
        modes = [args.mode]

    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = PROJECT_ROOT / "runs" / f"v0-{stamp}"

    summary = run_v0_benchmark(
        task_suite_paths=suites,
        output_dir=output_dir,
        rounds=args.rounds,
        modes=modes,
        tokenizer_name=args.tokenizer_name,
        model_name=args.model_name,
        allow_estimated_tokens=args.allow_estimated_tokens,
    )

    print(json.dumps({"output_dir": str(output_dir), "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
