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

REPORT_METRICS = [
    "task_runs",
    "success_count",
    "latency_ms",
    "llm_total_tokens",
    "end_to_end_collaboration_tokens",
    "retrieved_memory_tokens",
    "memory_hit_count",
    "useful_memory_hit_count",
    "wrong_memory_hit_count",
    "raw_access_count",
    "fallback_count",
]


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
    report = build_cross_task_report(
        summary=summary,
        metrics_path=output_dir / "metrics.json",
        provider=args.provider,
        suites=suites,
    )
    report_json = output_dir / "cross_task_report.json"
    report_md = output_dir / "cross_task_report.md"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(render_markdown_report(report), encoding="utf-8")
    payload = {
        "provider": args.provider,
        "suites": suites,
        "shared_runtime": True,
        "output_dir": str(output_dir),
        "deliverables_json": str(output_dir / "deliverables.json"),
        "deliverables_jsonl": str(output_dir / "deliverables.jsonl"),
        "cross_task_report_json": str(report_json),
        "cross_task_report_markdown": str(report_md),
        "summary": summary,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_cross_task_report(
    *,
    summary: dict,
    metrics_path: Path,
    provider: str,
    suites: list[str],
) -> dict:
    metrics_payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    rows = metrics_payload.get("rows", [])
    by_suite = {
        suite_id: summarize_rows(
            [row for row in rows if str(row.get("task_id", "")).startswith(suite_id)]
        )
        for suite_id in suites
    }
    return {
        "version": "v5.9-cross-task",
        "provider": provider,
        "suites": suites,
        "shared_runtime": True,
        "by_mode": summarize_by_mode(summary.get("by_mode", {})),
        "by_suite": by_suite,
        "audit_focus": {
            "checks": {
                "shared_runtime_entry": True,
                "runtime_has_memory_hits": (
                    summary.get("by_mode", {})
                    .get("runtime_lite", {})
                    .get("memory_hit_count", 0)
                    > 0
                ),
                "wrong_memory_hit_zero": (
                    summary.get("by_mode", {})
                    .get("runtime_lite", {})
                    .get("wrong_memory_hit_count", 0)
                    == 0
                ),
                "raw_access_low": (
                    summary.get("by_mode", {})
                    .get("runtime_lite", {})
                    .get("raw_access_count", 0)
                    <= 1
                ),
            }
        },
    }


def summarize_by_mode(by_mode: dict) -> dict:
    baseline = dict(by_mode.get("baseline_text", {}))
    runtime = dict(by_mode.get("runtime_lite", {}))
    return {
        metric: {
            "baseline_text": baseline.get(metric, 0),
            "runtime_lite": runtime.get(metric, 0),
            "delta": numeric_delta(runtime.get(metric, 0), baseline.get(metric, 0)),
            "percent_change": percent_change(runtime.get(metric, 0), baseline.get(metric, 0)),
        }
        for metric in REPORT_METRICS
    }


def summarize_rows(rows: list[dict]) -> dict:
    totals: dict[str, dict[str, float]] = {
        "baseline_text": {metric: 0 for metric in REPORT_METRICS},
        "runtime_lite": {metric: 0 for metric in REPORT_METRICS},
    }
    row_counts: dict[str, int] = {"baseline_text": 0, "runtime_lite": 0}
    task_ids: set[str] = set()
    for row in rows:
        mode = str(row.get("mode", ""))
        if mode not in totals:
            continue
        row_counts[mode] += 1
        task_ids.add(str(row.get("task_id", "")))
        for metric in REPORT_METRICS:
            value = row.get(metric, 0)
            if isinstance(value, (int, float)):
                totals[mode][metric] += value
    for mode, count in row_counts.items():
        totals[mode]["task_runs"] = count
        totals[mode]["success_count"] = count
    comparison = {}
    for metric in REPORT_METRICS:
        base = totals["baseline_text"][metric]
        runtime = totals["runtime_lite"][metric]
        comparison[metric] = {
            "baseline_text": base,
            "runtime_lite": runtime,
            "delta": runtime - base,
            "percent_change": percent_change(runtime, base),
        }
    return {
        "task_count": len([item for item in task_ids if item]),
        "metrics": comparison,
    }


def numeric_delta(new_value: object, old_value: object) -> float | int | None:
    if isinstance(new_value, (int, float)) and isinstance(old_value, (int, float)):
        return new_value - old_value
    return None


def percent_change(new_value: object, old_value: object) -> float | None:
    if not isinstance(new_value, (int, float)):
        return None
    if not isinstance(old_value, (int, float)) or old_value == 0:
        return None
    return (new_value - old_value) / old_value * 100


def render_markdown_report(report: dict) -> str:
    lines = [
        "# v5.9 跨任务连续实验报告",
        "",
        f"- Provider: `{report['provider']}`",
        f"- Suites: `{', '.join(report['suites'])}`",
        f"- Shared runtime: `{report['shared_runtime']}`",
        "",
        "## A+B 合计",
        "",
        "| 指标 | baseline_text | runtime_lite | 变化 |",
        "|---|---:|---:|---:|",
    ]
    for metric, values in report["by_mode"].items():
        lines.append(
            "| {metric} | {base} | {runtime} | {change} |".format(
                metric=metric,
                base=format_number(values["baseline_text"]),
                runtime=format_number(values["runtime_lite"]),
                change=format_percent(values["percent_change"]),
            )
        )
    lines.extend(["", "## 分组", ""])
    for suite_id, suite_report in report["by_suite"].items():
        lines.extend(
            [
                f"### Suite {suite_id}",
                "",
                "| 指标 | baseline_text | runtime_lite | 变化 |",
                "|---|---:|---:|---:|",
            ]
        )
        for metric, values in suite_report["metrics"].items():
            lines.append(
                "| {metric} | {base} | {runtime} | {change} |".format(
                    metric=metric,
                    base=format_number(values["baseline_text"]),
                    runtime=format_number(values["runtime_lite"]),
                    change=format_percent(values["percent_change"]),
                )
            )
        lines.append("")
    checks = report["audit_focus"]["checks"]
    lines.extend(
        [
            "## 审计关注点",
            "",
            f"- runtime_has_memory_hits: `{checks['runtime_has_memory_hits']}`",
            f"- wrong_memory_hit_zero: `{checks['wrong_memory_hit_zero']}`",
            f"- raw_access_low: `{checks['raw_access_low']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def format_number(value: object) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.3f}"
    return str(value)


def format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


if __name__ == "__main__":
    raise SystemExit(main())
