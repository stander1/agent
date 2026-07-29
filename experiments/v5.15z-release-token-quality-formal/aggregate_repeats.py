from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


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


def _ratio(saved_from: int, used: int) -> float:
    return (saved_from - used) / saved_from if saved_from else 0.0


def _sample_stdev(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


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
        root = runs_root / repeat_id
        preflight = _read_json(root / "preflight_report.json")
        acceptance = _read_json(
            root / "release_token_quality_formal_report.json"
        )
        if not preflight:
            missing.append(repeat_id)
            continue
        aggregates = dict(preflight.get("aggregates") or {})
        provider = dict(aggregates.get("provider") or {})
        native_provider = int(
            dict(provider.get("native") or {}).get("total_tokens") or 0
        )
        managed_provider = int(
            dict(provider.get("managed") or {}).get("total_tokens") or 0
        )
        transport = dict(aggregates.get("transport") or {})
        native_transport = int(
            transport.get("native_baseline_tokens") or 0
        )
        managed_transport = int(
            transport.get("end_to_end_collaboration_tokens") or 0
        )
        quality = dict(aggregates.get("quality") or {})
        native_quality = dict(quality.get("native") or {})
        managed_quality = dict(quality.get("managed") or {})
        memory = dict(aggregates.get("memory") or {})
        repeats.append(
            {
                "repeat_id": repeat_id,
                "preflight_passed": bool(
                    dict(preflight.get("summary") or {}).get("passed")
                ),
                "acceptance_passed": bool(
                    dict(acceptance.get("summary") or {}).get("passed")
                ),
                "native_provider_tokens": native_provider,
                "managed_provider_tokens": managed_provider,
                "provider_reduction_ratio": _ratio(
                    native_provider, managed_provider
                ),
                "native_transport_tokens": native_transport,
                "managed_transport_tokens": managed_transport,
                "transport_reduction_ratio": _ratio(
                    native_transport, managed_transport
                ),
                "native_quality_mean": float(
                    native_quality.get("mean_score") or 0.0
                ),
                "managed_quality_mean": float(
                    managed_quality.get("mean_score") or 0.0
                ),
                "native_delivery_complete_count": int(
                    native_quality.get("delivery_complete_count") or 0
                ),
                "managed_delivery_complete_count": int(
                    managed_quality.get("delivery_complete_count") or 0
                ),
                "wrong_memory_hit_count": int(
                    memory.get("wrong_hit_count") or 0
                ),
            }
        )

    native_provider_total = sum(
        item["native_provider_tokens"] for item in repeats
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
    wrong_hits = sum(item["wrong_memory_hit_count"] for item in repeats)

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
        "wrong_memory_hits_equal_zero": complete and wrong_hits == 0,
        "every_repeat_acceptance_passed": (
            complete and all(item["acceptance_passed"] for item in repeats)
        ),
    }
    return {
        "schema_version": "agentlite.v515z.repeat-aggregate.v1",
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
            "managed_provider_tokens": managed_provider_total,
            "provider_tokens_saved": (
                native_provider_total - managed_provider_total
            ),
            "provider_reduction_ratio": _ratio(
                native_provider_total, managed_provider_total
            ),
            "native_transport_tokens": native_transport_total,
            "managed_transport_tokens": managed_transport_total,
            "transport_tokens_saved": (
                native_transport_total - managed_transport_total
            ),
            "transport_reduction_ratio": _ratio(
                native_transport_total, managed_transport_total
            ),
            "wrong_memory_hit_count": wrong_hits,
        },
        "repeat_statistics": {
            "provider_reduction_min": min(provider_ratios, default=0.0),
            "provider_reduction_max": max(provider_ratios, default=0.0),
            "provider_reduction_sample_stdev": _sample_stdev(
                provider_ratios
            ),
            "transport_reduction_min": min(transport_ratios, default=0.0),
            "transport_reduction_max": max(transport_ratios, default=0.0),
            "transport_reduction_sample_stdev": _sample_stdev(
                transport_ratios
            ),
            "quality_delta_min": min(quality_deltas, default=0.0),
            "quality_delta_max": max(quality_deltas, default=0.0),
            "quality_delta_sample_stdev": _sample_stdev(quality_deltas),
            "delivery_deltas": delivery_deltas,
        },
        "gates": gates,
        "missing_repeats": missing,
        "repeats": repeats,
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    pooled = report["pooled"]
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
            "- Transport reduction: "
            f"`{pooled['transport_reduction_ratio']:.2%}` "
            f"(`{pooled['transport_tokens_saved']}` tokens)"
        ),
        f"- Wrong memory hits: `{pooled['wrong_memory_hit_count']}`",
        "",
        "## Gates",
        "",
    ]
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
            f"`{quality_delta:.2f}`, delivery delta `{delivery_delta}`"
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
