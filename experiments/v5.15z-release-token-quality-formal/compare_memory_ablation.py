from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


GROUPS = ("native", "observed", "managed")
VARIANTS = ("memory-on", "memory-off")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare isolated AgentLite shared-memory ablation runs."
    )
    parser.add_argument("--ablation-id", required=True)
    parser.add_argument("--repeat-count", type=int, default=1)
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


def _load_run(root: Path, run_id: str) -> dict[str, Any] | None:
    preflight = _read_json(root / "preflight_report.json")
    if not preflight:
        return None
    aggregates = dict(preflight.get("aggregates") or {})
    provider = dict(aggregates.get("provider") or {})
    quality = dict(aggregates.get("quality") or {})
    memory = dict(aggregates.get("memory") or {})
    return {
        "run_id": run_id,
        "modes": {
            group: {
                "provider_tokens": _int(
                    dict(provider.get(group) or {}).get("total_tokens")
                ),
                "quality_mean": _float(
                    dict(quality.get(group) or {}).get("mean_score")
                ),
                "delivery_complete_count": _int(
                    dict(quality.get(group) or {}).get(
                        "delivery_complete_count"
                    )
                ),
                "blocking_finding_count": _int(
                    dict(quality.get(group) or {}).get(
                        "blocking_finding_count"
                    )
                ),
            }
            for group in GROUPS
        },
        "memory": {
            key: _int(memory.get(key))
            for key in (
                "query_count",
                "hit_count",
                "injected_count",
                "useful_hit_count",
                "wrong_hit_count",
                "unassessed_hit_count",
            )
        },
    }


def _variant_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    modes: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        mode_rows = [row["modes"][group] for row in rows]
        modes[group] = {
            "provider_tokens": sum(
                item["provider_tokens"] for item in mode_rows
            ),
            "quality_mean": (
                statistics.fmean(
                    item["quality_mean"] for item in mode_rows
                )
                if mode_rows
                else 0.0
            ),
            "delivery_complete_count": sum(
                item["delivery_complete_count"] for item in mode_rows
            ),
            "blocking_finding_count": sum(
                item["blocking_finding_count"] for item in mode_rows
            ),
        }
    memory = {
        key: sum(row["memory"][key] for row in rows)
        for key in (
            "query_count",
            "hit_count",
            "injected_count",
            "useful_hit_count",
            "wrong_hit_count",
            "unassessed_hit_count",
        )
    }
    return {
        "run_count": len(rows),
        "modes": modes,
        "memory": memory,
        "managed_minus_native": {
            "provider_tokens": (
                modes["managed"]["provider_tokens"]
                - modes["native"]["provider_tokens"]
            ),
            "quality_mean": (
                modes["managed"]["quality_mean"]
                - modes["native"]["quality_mean"]
            ),
            "delivery_complete_count": (
                modes["managed"]["delivery_complete_count"]
                - modes["native"]["delivery_complete_count"]
            ),
            "blocking_finding_count": (
                modes["managed"]["blocking_finding_count"]
                - modes["native"]["blocking_finding_count"]
            ),
        },
    }


def compare(
    *,
    ablation_id: str,
    repeat_count: int,
    runs_root: Path,
) -> dict[str, Any]:
    missing: list[str] = []
    runs: dict[str, list[dict[str, Any]]] = {
        variant: [] for variant in VARIANTS
    }
    for variant in VARIANTS:
        for number in range(1, repeat_count + 1):
            run_id = f"{ablation_id}-{variant}-r{number}"
            item = _load_run(runs_root / run_id, run_id)
            if item is None:
                missing.append(run_id)
            else:
                runs[variant].append(item)
    variants = {
        variant: _variant_summary(rows)
        for variant, rows in runs.items()
    }
    on_delta = variants["memory-on"]["managed_minus_native"]
    off_delta = variants["memory-off"]["managed_minus_native"]
    difference_in_differences = {
        key: _float(on_delta[key]) - _float(off_delta[key])
        for key in (
            "provider_tokens",
            "quality_mean",
            "delivery_complete_count",
            "blocking_finding_count",
        )
    }
    complete = not missing and all(
        len(rows) == repeat_count for rows in runs.values()
    )
    return {
        "schema_version": "agentlite.shared-memory-ablation.v1",
        "summary": {
            "complete": complete,
            "ablation_id": ablation_id,
            "repeat_count_per_variant": repeat_count,
            "run_count": sum(len(rows) for rows in runs.values()),
        },
        "interpretation": {
            "method": (
                "difference-in-differences using each independent Native run "
                "as the provider-variability control"
            ),
            "positive_quality_value_means": (
                "memory-on improved Managed quality relative to its Native "
                "control more than memory-off did"
            ),
            "negative_provider_token_value_means": (
                "memory-on reduced Managed Provider tokens relative to its "
                "Native control more than memory-off did"
            ),
        },
        "difference_in_differences": difference_in_differences,
        "variants": variants,
        "missing_runs": missing,
        "runs": runs,
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    did = report["difference_in_differences"]
    lines = [
        "# AgentLite Shared-Memory Ablation",
        "",
        f"- Complete: `{summary['complete']}`",
        f"- Runs: `{summary['run_count']}`",
        (
            "- Quality difference-in-differences: "
            f"`{did['quality_mean']:.3f}`"
        ),
        (
            "- Provider-token difference-in-differences: "
            f"`{int(did['provider_tokens'])}`"
        ),
        (
            "- Delivery difference-in-differences: "
            f"`{int(did['delivery_complete_count'])}`"
        ),
        (
            "- Blocking-findings difference-in-differences: "
            f"`{int(did['blocking_finding_count'])}`"
        ),
        "",
        "## Variants",
        "",
        "| Variant | Managed quality | Native quality | Managed tokens | "
        "Native tokens | Memory injected | Memory unassessed |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, item in report["variants"].items():
        lines.append(
            f"| {name} | {item['modes']['managed']['quality_mean']:.3f} | "
            f"{item['modes']['native']['quality_mean']:.3f} | "
            f"{item['modes']['managed']['provider_tokens']} | "
            f"{item['modes']['native']['provider_tokens']} | "
            f"{item['memory']['injected_count']} | "
            f"{item['memory']['unassessed_hit_count']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    report = compare(
        ablation_id=args.ablation_id,
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
    return 0 if report["summary"]["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
