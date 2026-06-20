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
from agent_runtime.eval.llm_benchmark_runner import run_llm_benchmark
from agent_runtime.llm.config import load_llm_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run v2 LLM evaluation benchmark.")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--mode",
        choices=["baseline_text", "runtime_lite", "both"],
        default="both",
    )
    parser.add_argument(
        "--task-suite",
        action="append",
        type=Path,
        help="Path to a task suite JSON file. Can be passed multiple times.",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--retry-backoff-seconds", type=float, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--tokenizer-name", default=None)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--allow-estimated-tokens", action="store_true")
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Limit the number of tasks loaded from the suite for low-cost smoke tests.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suites = args.task_suite or [
        PROJECT_ROOT / "benchmarks" / "travel_task_group_a.json"
    ]
    modes: list[Mode] = (
        ["baseline_text", "runtime_lite"] if args.mode == "both" else [args.mode]
    )
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = PROJECT_ROOT / "runs" / f"v2-llm-{stamp}"

    llm_config = load_llm_config(
        args.config,
        base_url=args.base_url,
        model=args.model,
        api_key_env=args.api_key_env,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        temperature=args.temperature,
        top_p=args.top_p,
    )
    summary = run_llm_benchmark(
        task_suite_paths=suites,
        output_dir=output_dir,
        rounds=args.rounds,
        modes=modes,
        llm_config=llm_config,
        tokenizer_name=args.tokenizer_name,
        model_name=args.model_name,
        allow_estimated_tokens=args.allow_estimated_tokens,
        max_tasks=args.max_tasks,
    )
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "llm_config": llm_config.without_secret(),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
