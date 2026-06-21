from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


BASELINE_MODE = "baseline_text"
RUNTIME_MODE = "runtime_lite"

SUMMARY_METRICS = [
    "task_runs",
    "success_count",
    "latency_ms",
    "avg_latency_ms",
    "llm_prompt_tokens",
    "llm_completion_tokens",
    "llm_total_tokens",
    "llm_call_count",
    "direct_text_tokens",
    "prompt_view_tokens",
    "retrieved_memory_tokens",
    "end_to_end_collaboration_tokens",
    "fallback_count",
    "provider_response_retry_count",
    "contract_retry_count",
    "deliverable_schema_hit_count",
    "deliverable_schema_required_count",
    "deliverable_schema_complete_count",
    "raw_access_count",
    "wrong_memory_hit_count",
    "background_memory_wait_ms",
]


@dataclass(slots=True)
class SuiteRun:
    suite_id: str
    suite_name: str
    task_suite_path: str
    output_dir: Path
    summary: dict[str, Any]


def load_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_suite_run(
    *, suite_id: str, suite_name: str, task_suite_path: Path, output_dir: Path
) -> SuiteRun:
    return SuiteRun(
        suite_id=suite_id,
        suite_name=suite_name,
        task_suite_path=str(task_suite_path),
        output_dir=output_dir,
        summary=load_summary(output_dir / "summary.json"),
    )


def percent_change(new_value: Any, old_value: Any) -> float | None:
    if not isinstance(new_value, (int, float)):
        return None
    if not isinstance(old_value, (int, float)) or old_value == 0:
        return None
    return (new_value - old_value) / old_value * 100


def mode_summary(run: SuiteRun, mode: str) -> dict[str, Any]:
    return dict(run.summary.get("by_mode", {}).get(mode, {}))


def compare_modes(run: SuiteRun) -> dict[str, Any]:
    baseline = mode_summary(run, BASELINE_MODE)
    runtime = mode_summary(run, RUNTIME_MODE)
    metrics: dict[str, Any] = {}
    for key in SUMMARY_METRICS:
        old_value = baseline.get(key, 0)
        new_value = runtime.get(key, 0)
        delta = (
            new_value - old_value
            if isinstance(new_value, (int, float))
            and isinstance(old_value, (int, float))
            else None
        )
        metrics[key] = {
            "baseline": old_value,
            "runtime_lite": new_value,
            "delta": delta,
            "percent_change": percent_change(new_value, old_value),
        }
    return {
        "suite_id": run.suite_id,
        "suite_name": run.suite_name,
        "task_suite_path": run.task_suite_path,
        "output_dir": str(run.output_dir),
        "metrics": metrics,
        "quality": {
            BASELINE_MODE: quality_gate(baseline, require_schema=False),
            RUNTIME_MODE: quality_gate(runtime, require_schema=True),
        },
    }


def aggregate_runs(runs: list[SuiteRun]) -> dict[str, Any]:
    totals: dict[str, dict[str, Any]] = {
        BASELINE_MODE: {key: 0 for key in SUMMARY_METRICS},
        RUNTIME_MODE: {key: 0 for key in SUMMARY_METRICS},
    }
    for run in runs:
        for mode in [BASELINE_MODE, RUNTIME_MODE]:
            summary = mode_summary(run, mode)
            for key in SUMMARY_METRICS:
                value = summary.get(key, 0)
                if isinstance(value, (int, float)):
                    totals[mode][key] += value

    metrics: dict[str, Any] = {}
    for key in SUMMARY_METRICS:
        old_value = totals[BASELINE_MODE][key]
        new_value = totals[RUNTIME_MODE][key]
        metrics[key] = {
            "baseline": old_value,
            "runtime_lite": new_value,
            "delta": new_value - old_value,
            "percent_change": percent_change(new_value, old_value),
        }
    return {"metrics": metrics}


def quality_gate(summary: dict[str, Any], *, require_schema: bool) -> dict[str, Any]:
    task_runs = int(summary.get("task_runs", 0) or 0)
    success_count = int(summary.get("success_count", 0) or 0)
    success_rate = success_count / task_runs if task_runs else 0.0
    schema_required = int(summary.get("deliverable_schema_required_count", 0) or 0)
    schema_hit = int(summary.get("deliverable_schema_hit_count", 0) or 0)
    schema_complete_count = int(summary.get("deliverable_schema_complete_count", 0) or 0)
    schema_complete = (
        schema_complete_count > 0
        and schema_required > 0
        and schema_hit >= schema_required
    )
    checks = {
        "success_rate_100_percent": success_rate == 1.0,
        "fallback_count_zero": int(summary.get("fallback_count", 0) or 0) == 0,
        "contract_retry_count_zero": int(summary.get("contract_retry_count", 0) or 0)
        == 0,
        "wrong_memory_hit_count_zero": int(summary.get("wrong_memory_hit_count", 0) or 0)
        == 0,
        "raw_access_count_zero": int(summary.get("raw_access_count", 0) or 0) == 0,
    }
    if require_schema:
        checks["final_schema_complete"] = schema_complete
    passed = all(checks.values())
    return {
        "passed": passed,
        "checks": checks,
        "success_rate": success_rate,
        "schema_hit_count": schema_hit,
        "schema_required_count": schema_required,
        "schema_complete_count": schema_complete_count,
    }


def build_competition_report(
    *,
    runs: list[SuiteRun],
    provider: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    suite_comparisons = [compare_modes(run) for run in runs]
    aggregate = aggregate_runs(runs)
    report = {
        "version": "v5.0",
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "modes": [BASELINE_MODE, RUNTIME_MODE],
        "suites": suite_comparisons,
        "aggregate": aggregate,
        "metric_definitions": metric_definitions(),
    }
    return report


def metric_definitions() -> dict[str, str]:
    return {
        "llm_total_tokens": "模型服务返回的真实 usage 总 token，更接近后台账单口径。",
        "end_to_end_collaboration_tokens": "系统内部端到端协作成本，包含直接消息、Prompt View、记忆读取、控制和重试 token。",
        "fallback_count": "降级兜底次数，表示协议无法正常修复时系统用安全降级消息维持流程的次数。",
        "schema_valid_rate": "结构化协议输出通过 schema 校验的比例。",
        "latency_ms": "任务从开始到最终交付物选定的端到端耗时。",
        "provider_response_retry_count": "Provider 响应异常后由 Provider Response Guard 触发的重试次数。",
        "contract_retry_count": "Agent 输出协议不合格后由 Contract Guard 触发的短上下文格式重试次数。",
    }


def format_int(value: Any) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.3f}"
    return str(value)


def format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def render_markdown_report(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# v5.0 比赛评测报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- Provider：{report['provider']}",
        f"- 对照模式：`{BASELINE_MODE}` vs `{RUNTIME_MODE}`",
        "",
        "## 指标解释",
        "",
    ]
    for key, definition in report["metric_definitions"].items():
        lines.append(f"- `{key}`：{definition}")
    lines.extend(["", "## A/B 汇总", ""])
    lines.extend(render_metric_table(report["aggregate"]["metrics"]))
    lines.extend(["", "## 分组结果", ""])
    for suite in report["suites"]:
        lines.extend(
            [
                f"### {suite['suite_id']}：{suite['suite_name']}",
                "",
                f"- 任务文件：`{suite['task_suite_path']}`",
                f"- 输出目录：`{suite['output_dir']}`",
                "",
            ]
        )
        lines.extend(render_metric_table(suite["metrics"]))
        lines.extend(["", "质量门禁：", ""])
        for mode, quality in suite["quality"].items():
            status = "通过" if quality["passed"] else "未通过"
            lines.append(f"- `{mode}`：{status}")
            for check_name, passed in quality["checks"].items():
                mark = "OK" if passed else "FAIL"
                lines.append(f"  - {check_name}: {mark}")
        lines.append("")
    lines.extend(
        [
            "## 结论模板",
            "",
            "请在正式提交前根据最新一次完整评测补充：",
            "",
            "- token 降幅是否同时体现在 provider usage 和系统内部协作口径。",
            "- 延迟降幅是否稳定，是否仍受串行 LLM 调用数量限制。",
            "- runtime_lite 是否保持 100% 成功率、0 fallback、最终字段完整。",
            "- 记忆读取 token 是否远小于 baseline 完整历史 token，避免 cost shifting 质疑。",
        ]
    )
    return "\n".join(lines) + "\n"


def render_metric_table(metrics: dict[str, Any]) -> list[str]:
    selected = [
        "task_runs",
        "success_count",
        "latency_ms",
        "llm_prompt_tokens",
        "llm_completion_tokens",
        "llm_total_tokens",
        "direct_text_tokens",
        "prompt_view_tokens",
        "retrieved_memory_tokens",
        "end_to_end_collaboration_tokens",
        "fallback_count",
        "provider_response_retry_count",
        "contract_retry_count",
        "background_memory_wait_ms",
    ]
    lines = [
        "| 指标 | baseline_text | runtime_lite | 变化 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for key in selected:
        row = metrics[key]
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{key}`",
                    format_int(row["baseline"]),
                    format_int(row["runtime_lite"]),
                    format_percent(row["percent_change"]),
                ]
            )
            + " |"
        )
    return lines


def write_report_files(
    *,
    report: dict[str, Any],
    output_dir: Path,
    markdown_name: str = "competition_report.md",
    json_name: str = "competition_report.json",
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / json_name
    markdown_path = output_dir / markdown_name
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    return json_path, markdown_path
