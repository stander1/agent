from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.core.models import Mode
from agent_runtime.eval.benchmark_runner import run_v0_benchmark
from examples.run_cross_task_eval import build_cross_task_report, render_markdown_report


SUITE_PRESETS = {
    "A": PROJECT_ROOT / "benchmarks" / "travel_task_group_a.json",
    "B": PROJECT_ROOT / "benchmarks" / "security_task_group_b.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run local deterministic cross-task A->B evaluation without an LLM API key. "
            "This is an audit-chain smoke experiment, not a provider-cost substitute."
        )
    )
    parser.add_argument("--suite", action="append", choices=sorted(SUITE_PRESETS), default=None)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument(
        "--mode",
        choices=["baseline_text", "runtime_lite", "both"],
        default="both",
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--tokenizer-name", default=None)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--allow-estimated-tokens", action="store_true")
    parser.add_argument(
        "--max-tasks-per-suite",
        type=int,
        default=1,
        help=(
            "Limit local deterministic smoke size. Full baseline_text can grow "
            "very large because it intentionally carries complete history."
        ),
    )
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
        output_dir = PROJECT_ROOT / "runs" / f"v5.9-cross-task-local-{stamp}"

    with tempfile.TemporaryDirectory() as tmp:
        bounded_suite_paths = [
            build_bounded_suite(
                source_path=path,
                output_dir=Path(tmp),
                max_tasks=max(1, args.max_tasks_per_suite),
            )
            for path in suite_paths
        ]
        summary = run_v0_benchmark(
            task_suite_paths=bounded_suite_paths,
            output_dir=output_dir,
            rounds=args.rounds,
            modes=modes,
            tokenizer_name=args.tokenizer_name,
            model_name=args.model_name,
            allow_estimated_tokens=args.allow_estimated_tokens,
        )
    report = build_cross_task_report(
        summary=summary,
        metrics_path=output_dir / "metrics.json",
        provider="local_deterministic",
        suites=suites,
    )
    report["audit_focus"]["provider_scope"] = (
        "local deterministic agents; use MiMo run_cross_task_eval.py for real provider tokens"
    )
    report_json = output_dir / "cross_task_report.json"
    report_md = output_dir / "cross_task_report.md"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(render_markdown_report(report), encoding="utf-8")

    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "cross_task_report_json": str(report_json),
                "cross_task_report_markdown": str(report_md),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_bounded_suite(*, source_path: Path, output_dir: Path, max_tasks: int) -> Path:
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks", [])[:max_tasks]
    bounded = {"tasks": tasks}
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / source_path.name
    target.write_text(json.dumps(bounded, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


if __name__ == "__main__":
    raise SystemExit(main())
