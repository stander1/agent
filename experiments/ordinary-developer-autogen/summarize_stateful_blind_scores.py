from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unblind and summarize frozen scores.")
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--technical-scores", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scores = json.loads(args.scores.read_text(encoding="utf-8"))
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    technical_scores = (
        json.loads(args.technical_scores.read_text(encoding="utf-8"))
        if args.technical_scores
        else None
    )
    payload = summarize_scores(
        scores=scores,
        mapping=mapping,
        technical_scores=technical_scores,
    )
    payload["source_scores"] = str(args.scores)
    payload["source_mapping"] = str(args.mapping)
    payload["source_technical_scores"] = (
        str(args.technical_scores) if args.technical_scores else None
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def summarize_scores(
    *,
    scores: dict[str, Any],
    mapping: dict[str, Any],
    technical_scores: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not scores.get("frozen_before_unblinding"):
        raise ValueError("Scores are not marked as frozen before unblinding")
    technical_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    if technical_scores is not None:
        if not technical_scores.get("frozen_before_unblinding"):
            raise ValueError(
                "Technical scores are not marked as frozen before unblinding"
            )
        for result in technical_scores.get("results", []):
            task_id = str(result["task_id"])
            for row in result.get("evaluations", []):
                key = (task_id, str(row["candidate_id"]))
                if key in technical_lookup:
                    raise ValueError(f"Duplicate technical score: {key}")
                technical_lookup[key] = row
    identity = {
        (str(item["task_id"]), str(item["candidate_id"])): str(item["group"])
        for item in mapping.get("mapping", [])
    }
    primary_keys = {
        (str(result["task_id"]), str(row["candidate_id"]))
        for result in scores.get("results", [])
        for row in result.get("evaluations", [])
    }
    if technical_scores is not None and set(technical_lookup) != primary_keys:
        missing = sorted(primary_keys - set(technical_lookup))
        extra = sorted(set(technical_lookup) - primary_keys)
        raise ValueError(
            "Technical scores do not align with primary scores: "
            f"missing={missing}, extra={extra}"
        )
    group_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_rows: list[dict[str, Any]] = []
    for result in scores.get("results", []):
        task_id = str(result["task_id"])
        revealed: list[dict[str, Any]] = []
        for row in result.get("evaluations", []):
            candidate_id = str(row["candidate_id"])
            group = identity[(task_id, candidate_id)]
            item = {**row, "group": group}
            item["primary_total"] = int(row["total"])
            item["primary_delivery_complete"] = bool(row["delivery_complete"])
            if technical_scores is not None:
                technical = technical_lookup[(task_id, candidate_id)]
                item["technical_total"] = int(technical["total"])
                item["technical_delivery_usable"] = bool(
                    technical["delivery_usable"]
                )
                item["technical_findings"] = list(
                    technical.get("findings") or []
                )
                item["technical_finding_count"] = int(
                    technical.get("finding_count") or 0
                )
                item["blocking_finding_count"] = int(
                    technical.get("blocking_finding_count") or 0
                )
                item["total"] = min(
                    item["primary_total"], item["technical_total"]
                )
                item["delivery_complete"] = (
                    item["primary_delivery_complete"]
                    and item["technical_delivery_usable"]
                )
            else:
                item["technical_total"] = None
                item["technical_delivery_usable"] = None
                item["technical_findings"] = []
                item["technical_finding_count"] = 0
                item["blocking_finding_count"] = 0
            revealed.append(item)
            group_rows[group].append(item)
        max_total = max(int(row["total"]) for row in revealed)
        winners = sorted(
            row["group"] for row in revealed if int(row["total"]) == max_total
        )
        task_rows.append(
            {
                "task_id": task_id,
                "scores": {row["group"]: row["total"] for row in revealed},
                "primary_scores": {
                    row["group"]: row["primary_total"] for row in revealed
                },
                "technical_scores": {
                    row["group"]: row["technical_total"] for row in revealed
                },
                "delivery_complete": {
                    row["group"]: row["delivery_complete"] for row in revealed
                },
                "technical_findings": {
                    row["group"]: row["technical_findings"] for row in revealed
                },
                "winners": winners,
                "summary": result.get("summary", ""),
            }
        )
    by_group: dict[str, dict[str, Any]] = {}
    for group, rows in sorted(group_rows.items()):
        totals = [int(row["total"]) for row in rows]
        by_group[group] = {
            "task_count": len(rows),
            "mean_score": sum(totals) / len(totals),
            "primary_mean_score": sum(
                int(row["primary_total"]) for row in rows
            )
            / len(rows),
            "technical_mean_score": (
                sum(int(row["technical_total"]) for row in rows) / len(rows)
                if technical_scores is not None
                else None
            ),
            "min_score": min(totals),
            "max_score": max(totals),
            "delivery_complete_count": sum(
                bool(row["delivery_complete"]) for row in rows
            ),
            "technical_finding_count": sum(
                int(row["technical_finding_count"]) for row in rows
            ),
            "blocking_finding_count": sum(
                int(row["blocking_finding_count"]) for row in rows
            ),
            "technical_review_applied": technical_scores is not None,
            "dimension_means": {
                field: sum(int(row[field]) for row in rows) / len(rows)
                for field in (
                    "task_completion",
                    "context_retention",
                    "correctness_consistency",
                    "clarity_actionability",
                )
            },
            "outright_win_count": sum(
                task["winners"] == [group] for task in task_rows
            ),
            "tie_for_best_count": sum(
                group in task["winners"] and len(task["winners"]) > 1
                for task in task_rows
            ),
        }
    payload = {
        "scenario_id": mapping.get("scenario_id"),
        "technical_review_applied": technical_scores is not None,
        "evaluation_judge_usage": {
            "primary": _aggregate_judge_usage(scores),
            "technical": (
                _aggregate_judge_usage(technical_scores)
                if technical_scores is not None
                else None
            ),
            "included_in_runtime_collaboration_cost": False,
        },
        "by_group": by_group,
        "tasks": task_rows,
    }
    return payload


def _aggregate_judge_usage(scores: dict[str, Any]) -> dict[str, int]:
    summary = {
        "call_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0,
        "format_retry_count": 0,
    }
    for result in scores.get("results", []):
        usage = result.get("judge_usage")
        usage = usage if isinstance(usage, dict) else {}
        summary["call_count"] += int(bool(usage))
        summary["prompt_tokens"] += _usage_value(
            usage, "prompt_tokens", "llm_prompt_tokens"
        )
        summary["completion_tokens"] += _usage_value(
            usage, "completion_tokens", "llm_completion_tokens"
        )
        summary["total_tokens"] += _usage_value(
            usage, "total_tokens", "llm_total_tokens"
        )
        summary["latency_ms"] += int(result.get("judge_latency_ms") or 0)
        summary["format_retry_count"] += int(
            result.get("format_retry_count") or 0
        )
    return summary


def _usage_value(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in usage:
            try:
                return int(usage.get(key) or 0)
            except (TypeError, ValueError):
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
