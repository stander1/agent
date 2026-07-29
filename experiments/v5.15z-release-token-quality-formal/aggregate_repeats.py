from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable


GROUPS = ("native", "observed", "managed")
_T_CRITICAL_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate v5.15z formal benchmark repeats."
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--repeat-count", type=int, default=3)
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("runs/v5.15z-release-token-quality-formal"),
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ratio(saved_from: int, used: int) -> float:
    return (saved_from - used) / saved_from if saved_from else 0.0


def _sum_dicts(
    rows: Iterable[dict[str, Any]],
    keys: Iterable[str],
) -> dict[str, int]:
    materialized = list(rows)
    return {
        key: sum(_int(row.get(key)) for row in materialized)
        for key in keys
    }


def _distribution(values: Iterable[float]) -> dict[str, float | int]:
    samples = [float(value) for value in values]
    if not samples:
        return {
            "count": 0,
            "mean": 0.0,
            "sample_stdev": 0.0,
            "min": 0.0,
            "max": 0.0,
            "ci95_low": 0.0,
            "ci95_high": 0.0,
        }
    mean = statistics.fmean(samples)
    stdev = statistics.stdev(samples) if len(samples) >= 2 else 0.0
    if len(samples) >= 2:
        critical = _T_CRITICAL_95.get(len(samples) - 1, 1.96)
        margin = critical * stdev / math.sqrt(len(samples))
    else:
        margin = 0.0
    return {
        "count": len(samples),
        "mean": mean,
        "sample_stdev": stdev,
        "min": min(samples),
        "max": max(samples),
        "ci95_low": mean - margin,
        "ci95_high": mean + margin,
    }


def _judge_usage(quality: dict[str, Any]) -> dict[str, dict[str, int]]:
    usage = dict(quality.get("evaluation_judge_usage") or {})
    result: dict[str, dict[str, int]] = {}
    for judge in ("primary", "technical"):
        row = dict(usage.get(judge) or {})
        result[judge] = {
            key: _int(row.get(key))
            for key in (
                "call_count",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "latency_ms",
                "format_retry_count",
            )
        }
    return result


def _scenario_directories(preflight: dict[str, Any]) -> dict[str, str]:
    preregistration = dict(preflight.get("preregistration") or {})
    directories: dict[str, str] = {}
    for item in preregistration.get("scenarios") or []:
        if not isinstance(item, dict):
            continue
        scenario_id = str(item.get("scenario_id") or "")
        directory = str(item.get("directory") or scenario_id)
        if scenario_id:
            directories[scenario_id] = directory
    return directories


def _scenario_detail(
    *,
    root: Path,
    row: dict[str, Any],
    directory: str,
) -> dict[str, Any]:
    scenario_id = str(row.get("scenario_id") or directory)
    scenario_root = root / directory
    quality = _read_json(
        scenario_root / "comparison" / "quality_blind_summary.json"
    )
    quality_groups = dict(quality.get("by_group") or {})
    row_provider = dict(row.get("provider") or {})
    row_quality = dict(row.get("quality") or {})
    modes: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        sequence = _read_json(
            scenario_root / group / "sequence_result.json"
        )
        sequence_summary = dict(sequence.get("summary") or {})
        llm_usage = dict(sequence_summary.get("llm_usage") or {})
        provider = dict(row_provider.get(group) or {})
        quality_row = dict(
            quality_groups.get(group) or row_quality.get(group) or {}
        )
        modes[group] = {
            "task_count": _int(
                quality_row.get("task_count")
                or dict(row.get("task_counts") or {}).get(group)
            ),
            "provider_calls": _int(
                provider.get("calls") or llm_usage.get("calls")
            ),
            "provider_prompt_tokens": _int(
                provider.get("prompt_tokens")
                or llm_usage.get("llm_prompt_tokens")
            ),
            "provider_completion_tokens": _int(
                provider.get("completion_tokens")
                or llm_usage.get("llm_completion_tokens")
            ),
            "provider_total_tokens": _int(
                provider.get("total_tokens")
                or llm_usage.get("llm_total_tokens")
            ),
            "provider_retry_count": _int(llm_usage.get("retry_count")),
            "strict_delivery_count": _int(
                provider.get("strict_delivery_count")
            ),
            "mean_score": _float(quality_row.get("mean_score")),
            "primary_mean_score": _float(
                quality_row.get("primary_mean_score")
            ),
            "technical_mean_score": _float(
                quality_row.get("technical_mean_score")
            ),
            "delivery_complete_count": _int(
                quality_row.get("delivery_complete_count")
            ),
            "technical_finding_count": _int(
                quality_row.get("technical_finding_count")
            ),
            "blocking_finding_count": _int(
                quality_row.get("blocking_finding_count")
            ),
        }

    managed_report = _read_json(
        scenario_root / "reports" / "managed-agentlite.json"
    )
    tokens = dict(managed_report.get("token_summary") or {})
    review = dict(managed_report.get("review_governance_summary") or {})
    transport = {
        key: _int(value)
        for key, value in dict(row.get("transport") or {}).items()
    }
    memory = {
        key: _int(value)
        for key, value in dict(row.get("memory") or {}).items()
    }
    reliability = {
        "rewrite_error_fallback_count": _int(
            tokens.get("rewrite_error_fallback_count")
        ),
        "memory_conflict_detected_count": _int(
            tokens.get("memory_conflict_detected_count")
        ),
        "memory_conflict_resolved_count": _int(
            tokens.get("memory_conflict_resolved_count")
        ),
        "memory_unresolved_conflict_count": _int(
            tokens.get("memory_unresolved_conflict_count")
        ),
        "review_governance_event_count": _int(
            review.get("event_count")
            or tokens.get("review_governance_event_count")
        ),
        "review_governance_failure_event_count": _int(
            review.get("failure_event_count")
            or tokens.get("review_governance_failure_event_count")
        ),
    }
    return {
        "scenario_id": scenario_id,
        "directory": directory,
        "modes": modes,
        "transport": transport,
        "memory": memory,
        "reliability": reliability,
        "judge_usage": _judge_usage(quality),
    }


def _load_repeat(
    *,
    root: Path,
    repeat_id: str,
) -> dict[str, Any] | None:
    preflight = _read_json(root / "preflight_report.json")
    if not preflight:
        return None
    acceptance = _read_json(
        root / "release_token_quality_formal_report.json"
    )
    directories = _scenario_directories(preflight)
    scenario_rows = [
        dict(item)
        for item in preflight.get("scenarios") or []
        if isinstance(item, dict)
    ]
    scenarios = {
        str(row.get("scenario_id") or ""): _scenario_detail(
            root=root,
            row=row,
            directory=directories.get(
                str(row.get("scenario_id") or ""),
                str(row.get("scenario_id") or ""),
            ),
        )
        for row in scenario_rows
        if str(row.get("scenario_id") or "")
    }

    aggregates = dict(preflight.get("aggregates") or {})
    provider = dict(aggregates.get("provider") or {})
    quality = dict(aggregates.get("quality") or {})
    transport = dict(aggregates.get("transport") or {})
    memory = {
        key: _int(value)
        for key, value in dict(aggregates.get("memory") or {}).items()
    }
    provider_totals = {
        group: _int(dict(provider.get(group) or {}).get("total_tokens"))
        for group in GROUPS
    }
    quality_means = {
        group: _float(dict(quality.get(group) or {}).get("mean_score"))
        for group in GROUPS
    }
    delivery_counts = {
        group: _int(
            dict(quality.get(group) or {}).get("delivery_complete_count")
        )
        for group in GROUPS
    }
    provider_calls = {
        group: sum(
            _int(detail["modes"][group]["provider_calls"])
            for detail in scenarios.values()
        )
        for group in GROUPS
    }
    provider_retries = {
        group: sum(
            _int(detail["modes"][group]["provider_retry_count"])
            for detail in scenarios.values()
        )
        for group in GROUPS
    }
    judge_usage = {
        judge: _sum_dicts(
            (
                detail["judge_usage"][judge]
                for detail in scenarios.values()
            ),
            (
                "call_count",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "latency_ms",
                "format_retry_count",
            ),
        )
        for judge in ("primary", "technical")
    }
    native_transport = _int(transport.get("native_baseline_tokens"))
    managed_transport = _int(
        transport.get("end_to_end_collaboration_tokens")
    )
    return {
        "repeat_id": repeat_id,
        "preflight_passed": bool(
            dict(preflight.get("summary") or {}).get("passed")
        ),
        "acceptance_passed": bool(
            dict(acceptance.get("summary") or {}).get("passed")
        ),
        "native_provider_tokens": provider_totals["native"],
        "observed_provider_tokens": provider_totals["observed"],
        "managed_provider_tokens": provider_totals["managed"],
        "provider_reduction_ratio": _ratio(
            provider_totals["native"],
            provider_totals["managed"],
        ),
        "native_transport_tokens": native_transport,
        "managed_transport_tokens": managed_transport,
        "transport_reduction_ratio": _ratio(
            native_transport,
            managed_transport,
        ),
        "native_quality_mean": quality_means["native"],
        "observed_quality_mean": quality_means["observed"],
        "managed_quality_mean": quality_means["managed"],
        "native_delivery_complete_count": delivery_counts["native"],
        "observed_delivery_complete_count": delivery_counts["observed"],
        "managed_delivery_complete_count": delivery_counts["managed"],
        "wrong_memory_hit_count": memory.get("wrong_hit_count", 0),
        "provider_calls": provider_calls,
        "provider_retries": provider_retries,
        "memory": memory,
        "judge_usage": judge_usage,
        "scenarios": scenarios,
    }


def _scenario_aggregate(
    scenario_id: str,
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    modes: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        rows = [detail["modes"][group] for detail in details]
        task_count = sum(_int(row.get("task_count")) for row in rows)
        weighted_score = sum(
            _float(row.get("mean_score")) * _int(row.get("task_count"))
            for row in rows
        )
        modes[group] = {
            "task_count": task_count,
            "provider_calls": sum(
                _int(row.get("provider_calls")) for row in rows
            ),
            "provider_total_tokens": sum(
                _int(row.get("provider_total_tokens")) for row in rows
            ),
            "provider_retry_count": sum(
                _int(row.get("provider_retry_count")) for row in rows
            ),
            "mean_score": (
                weighted_score / task_count if task_count else 0.0
            ),
            "delivery_complete_count": sum(
                _int(row.get("delivery_complete_count")) for row in rows
            ),
            "blocking_finding_count": sum(
                _int(row.get("blocking_finding_count")) for row in rows
            ),
        }
    memory = _sum_dicts(
        (detail["memory"] for detail in details),
        (
            "query_count",
            "hit_count",
            "injected_count",
            "useful_hit_count",
            "wrong_hit_count",
            "unassessed_hit_count",
        ),
    )
    reliability = _sum_dicts(
        (detail["reliability"] for detail in details),
        (
            "rewrite_error_fallback_count",
            "memory_conflict_detected_count",
            "memory_conflict_resolved_count",
            "memory_unresolved_conflict_count",
            "review_governance_event_count",
            "review_governance_failure_event_count",
        ),
    )
    provider_reductions = [
        _ratio(
            detail["modes"]["native"]["provider_total_tokens"],
            detail["modes"]["managed"]["provider_total_tokens"],
        )
        for detail in details
    ]
    quality_deltas = [
        detail["modes"]["managed"]["mean_score"]
        - detail["modes"]["native"]["mean_score"]
        for detail in details
    ]
    delivery_deltas = [
        detail["modes"]["managed"]["delivery_complete_count"]
        - detail["modes"]["native"]["delivery_complete_count"]
        for detail in details
    ]
    return {
        "scenario_id": scenario_id,
        "repeat_count": len(details),
        "modes": modes,
        "managed_vs_native": {
            "provider_reduction_ratio": _ratio(
                modes["native"]["provider_total_tokens"],
                modes["managed"]["provider_total_tokens"],
            ),
            "quality_delta": (
                modes["managed"]["mean_score"]
                - modes["native"]["mean_score"]
            ),
            "delivery_delta": (
                modes["managed"]["delivery_complete_count"]
                - modes["native"]["delivery_complete_count"]
            ),
        },
        "repeat_statistics": {
            "provider_reduction_ratio": _distribution(
                provider_reductions
            ),
            "quality_delta": _distribution(quality_deltas),
            "delivery_delta": _distribution(delivery_deltas),
        },
        "memory": memory,
        "reliability": reliability,
    }


def aggregate(
    *,
    batch_id: str,
    repeat_count: int,
    runs_root: Path,
) -> dict[str, Any]:
    repeats: list[dict[str, Any]] = []
    missing: list[str] = []
    for number in range(1, repeat_count + 1):
        repeat_id = f"{batch_id}-r{number}"
        item = _load_repeat(
            root=runs_root / repeat_id,
            repeat_id=repeat_id,
        )
        if item is None:
            missing.append(repeat_id)
            continue
        repeats.append(item)

    native_provider_total = sum(
        item["native_provider_tokens"] for item in repeats
    )
    observed_provider_total = sum(
        item["observed_provider_tokens"] for item in repeats
    )
    managed_provider_total = sum(
        item["managed_provider_tokens"] for item in repeats
    )
    native_transport_total = sum(
        item["native_transport_tokens"] for item in repeats
    )
    managed_transport_total = sum(
        item["managed_transport_tokens"] for item in repeats
    )
    provider_ratios = [
        item["provider_reduction_ratio"] for item in repeats
    ]
    transport_ratios = [
        item["transport_reduction_ratio"] for item in repeats
    ]
    quality_deltas = [
        item["managed_quality_mean"] - item["native_quality_mean"]
        for item in repeats
    ]
    delivery_deltas = [
        item["managed_delivery_complete_count"]
        - item["native_delivery_complete_count"]
        for item in repeats
    ]
    memory = _sum_dicts(
        (item["memory"] for item in repeats),
        (
            "query_count",
            "hit_count",
            "injected_count",
            "useful_hit_count",
            "wrong_hit_count",
            "unassessed_hit_count",
        ),
    )
    provider_calls = {
        group: sum(item["provider_calls"][group] for item in repeats)
        for group in GROUPS
    }
    provider_retries = {
        group: sum(item["provider_retries"][group] for item in repeats)
        for group in GROUPS
    }
    judge_usage = {
        judge: _sum_dicts(
            (item["judge_usage"][judge] for item in repeats),
            (
                "call_count",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "latency_ms",
                "format_retry_count",
            ),
        )
        for judge in ("primary", "technical")
    }
    scenario_ids = sorted(
        {
            scenario_id
            for item in repeats
            for scenario_id in item["scenarios"]
        }
    )
    by_scenario = {
        scenario_id: _scenario_aggregate(
            scenario_id,
            [
                item["scenarios"][scenario_id]
                for item in repeats
                if scenario_id in item["scenarios"]
            ],
        )
        for scenario_id in scenario_ids
    }

    complete = len(repeats) == repeat_count and not missing
    gates = {
        "all_repeats_complete": complete,
        "pooled_provider_reduction_at_least_15_percent": (
            complete
            and _ratio(native_provider_total, managed_provider_total) >= 0.15
        ),
        "pooled_transport_reduction_at_least_15_percent": (
            complete
            and _ratio(native_transport_total, managed_transport_total)
            >= 0.15
        ),
        "every_repeat_quality_noninferiority_within_0_3": (
            complete and all(delta >= -0.3 for delta in quality_deltas)
        ),
        "every_repeat_delivery_noninferior": (
            complete and all(delta >= 0 for delta in delivery_deltas)
        ),
        "wrong_memory_hits_equal_zero": (
            complete and memory["wrong_hit_count"] == 0
        ),
        "every_repeat_acceptance_passed": (
            complete and all(item["acceptance_passed"] for item in repeats)
        ),
    }
    return {
        "schema_version": "agentlite.v515z.repeat-aggregate.v2",
        "summary": {
            "passed": all(gates.values()),
            "batch_id": batch_id,
            "planned_repeat_count": repeat_count,
            "completed_repeat_count": len(repeats),
            "planned_task_executions": repeat_count * 60,
            "completed_task_executions": len(repeats) * 60,
        },
        "pooled": {
            "native_provider_tokens": native_provider_total,
            "observed_provider_tokens": observed_provider_total,
            "managed_provider_tokens": managed_provider_total,
            "provider_tokens_saved": (
                native_provider_total - managed_provider_total
            ),
            "provider_reduction_ratio": _ratio(
                native_provider_total, managed_provider_total
            ),
            "observed_provider_change_ratio": (
                (observed_provider_total - native_provider_total)
                / native_provider_total
                if native_provider_total
                else 0.0
            ),
            "native_transport_tokens": native_transport_total,
            "managed_transport_tokens": managed_transport_total,
            "transport_tokens_saved": (
                native_transport_total - managed_transport_total
            ),
            "transport_reduction_ratio": _ratio(
                native_transport_total, managed_transport_total
            ),
            "provider_calls": provider_calls,
            "provider_retries": provider_retries,
            "memory": memory,
            "wrong_memory_hit_count": memory["wrong_hit_count"],
            "judge_usage": judge_usage,
        },
        "repeat_statistics": {
            "provider_reduction_ratio": _distribution(provider_ratios),
            "transport_reduction_ratio": _distribution(transport_ratios),
            "quality_delta": _distribution(quality_deltas),
            "delivery_delta": _distribution(delivery_deltas),
            "provider_reduction_min": min(provider_ratios, default=0.0),
            "provider_reduction_max": max(provider_ratios, default=0.0),
            "provider_reduction_sample_stdev": _distribution(
                provider_ratios
            )["sample_stdev"],
            "transport_reduction_min": min(transport_ratios, default=0.0),
            "transport_reduction_max": max(transport_ratios, default=0.0),
            "transport_reduction_sample_stdev": _distribution(
                transport_ratios
            )["sample_stdev"],
            "quality_delta_min": min(quality_deltas, default=0.0),
            "quality_delta_max": max(quality_deltas, default=0.0),
            "quality_delta_sample_stdev": _distribution(
                quality_deltas
            )["sample_stdev"],
            "delivery_deltas": delivery_deltas,
        },
        "by_scenario": by_scenario,
        "gates": gates,
        "missing_repeats": missing,
        "repeats": repeats,
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    pooled = report["pooled"]
    statistics_row = report["repeat_statistics"]
    provider_stats = statistics_row["provider_reduction_ratio"]
    quality_stats = statistics_row["quality_delta"]
    lines = [
        "# v5.15z Repeated Release Token and Quality Benchmark",
        "",
        f"- Passed: `{summary['passed']}`",
        (
            f"- Repeats: `{summary['completed_repeat_count']}/"
            f"{summary['planned_repeat_count']}`"
        ),
        (
            "- Provider reduction: "
            f"`{pooled['provider_reduction_ratio']:.2%}` "
            f"(`{pooled['provider_tokens_saved']}` tokens)"
        ),
        (
            "- Provider reduction 95% CI across repeats: "
            f"`[{provider_stats['ci95_low']:.2%}, "
            f"{provider_stats['ci95_high']:.2%}]`"
        ),
        (
            "- Transport reduction: "
            f"`{pooled['transport_reduction_ratio']:.2%}` "
            f"(`{pooled['transport_tokens_saved']}` tokens)"
        ),
        (
            "- Managed quality delta 95% CI across repeats: "
            f"`[{quality_stats['ci95_low']:.3f}, "
            f"{quality_stats['ci95_high']:.3f}]`"
        ),
        f"- Wrong memory hits: `{pooled['wrong_memory_hit_count']}`",
        (
            "- Unassessed memory hits: "
            f"`{pooled['memory']['unassessed_hit_count']}`"
        ),
        "",
        "## Scenario Breakdown",
        "",
        "| Scenario | Provider reduction | Quality delta | Delivery delta | "
        "Managed retries | Unassessed memory |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for scenario_id, item in report["by_scenario"].items():
        delta = item["managed_vs_native"]
        lines.append(
            f"| {scenario_id} | {delta['provider_reduction_ratio']:.2%} | "
            f"{delta['quality_delta']:.3f} | {delta['delivery_delta']} | "
            f"{item['modes']['managed']['provider_retry_count']} | "
            f"{item['memory']['unassessed_hit_count']} |"
        )
    lines.extend(["", "## Gates", ""])
    lines.extend(
        f"- `{name}`: `{passed}`"
        for name, passed in report["gates"].items()
    )
    lines.extend(["", "## Repeats", ""])
    for item in report["repeats"]:
        quality_delta = (
            item["managed_quality_mean"] - item["native_quality_mean"]
        )
        delivery_delta = (
            item["managed_delivery_complete_count"]
            - item["native_delivery_complete_count"]
        )
        lines.append(
            f"- `{item['repeat_id']}`: provider "
            f"`{item['provider_reduction_ratio']:.2%}`, transport "
            f"`{item['transport_reduction_ratio']:.2%}`, quality delta "
            f"`{quality_delta:.2f}`, delivery delta `{delivery_delta}`, "
            f"managed retries `{item['provider_retries']['managed']}`"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    report = aggregate(
        batch_id=args.batch_id,
        repeat_count=args.repeat_count,
        runs_root=args.runs_root,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.output_markdown.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
