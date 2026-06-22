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


SUITE_PRESETS = {
    "A": PROJECT_ROOT / "benchmarks" / "travel_task_group_a.json",
    "B": PROJECT_ROOT / "benchmarks" / "security_task_group_b.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run cross-task A->B evaluation in one runtime so StatePool and "
            "MemoryStore are shared across suites."
        )
    )
    parser.add_argument("--provider", default="mimo")
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "llm.mimo.example.json",
    )
    parser.add_argument("--suite", action="append", choices=sorted(SUITE_PRESETS), default=None)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--mode",
        choices=["baseline_text", "runtime_lite", "both"],
        default="both",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--retry-backoff-seconds", type=float, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--tokenizer-name", default=None)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--allow-estimated-tokens", action="store_true")
    parser.add_argument("--max-tasks", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suites = args.suite or ["A", "B"]
    suite_paths = [SUITE_PRESETS[item] for item in suites]
    modes: list[Mode] = (
        ["baseline_text", "runtime_lite"] if args.mode == "both" else [args.mode]
    )
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = PROJECT_ROOT / "runs" / f"cross-task-{args.provider}-{stamp}"
    llm_config = load_llm_config(
        args.config,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        temperature=args.temperature,
        top_p=args.top_p,
    )
    summary = run_llm_benchmark(
        task_suite_paths=suite_paths,
        output_dir=output_dir,
        rounds=args.rounds,
        modes=modes,
        llm_config=llm_config,
        tokenizer_name=args.tokenizer_name,
        model_name=args.model_name,
        allow_estimated_tokens=args.allow_estimated_tokens,
        max_tasks=args.max_tasks,
    )
    payload = {
        "provider": args.provider,
        "suites": suites,
        "shared_runtime": True,
        "output_dir": str(output_dir),
        "summary": summary,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
