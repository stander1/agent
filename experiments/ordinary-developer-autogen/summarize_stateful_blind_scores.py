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
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scores = json.loads(args.scores.read_text(encoding="utf-8"))
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    if not scores.get("frozen_before_unblinding"):
        raise ValueError("Scores are not marked as frozen before unblinding")
    identity = {
        (str(item["task_id"]), str(item["candidate_id"])): str(item["group"])
        for item in mapping.get("mapping", [])
    }
    group_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    task_rows: list[dict[str, Any]] = []
    for result in scores.get("results", []):
        task_id = str(result["task_id"])
        revealed: list[dict[str, Any]] = []
        for row in result.get("evaluations", []):
            candidate_id = str(row["candidate_id"])
            group = identity[(task_id, candidate_id)]
            item = {**row, "group": group}
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
                "delivery_complete": {
                    row["group"]: row["delivery_complete"] for row in revealed
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
            "min_score": min(totals),
            "max_score": max(totals),
            "delivery_complete_count": sum(bool(row["delivery_complete"]) for row in rows),
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
        "by_group": by_group,
        "tasks": task_rows,
        "source_scores": str(args.scores),
        "source_mapping": str(args.mapping),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
