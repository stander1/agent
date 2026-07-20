from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any


GROUPS = ("native", "observed", "managed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare three stateful AutoGen experiment runs."
    )
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--observed-dir", type=Path, required=True)
    parser.add_argument("--managed-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--blind-seed", type=int, default=20260716)
    args = parser.parse_args()

    run_dirs = {
        "native": args.native_dir.expanduser().resolve(),
        "observed": args.observed_dir.expanduser().resolve(),
        "managed": args.managed_dir.expanduser().resolve(),
    }
    output_dir = args.output_dir.expanduser().resolve()
    result = compare_runs(
        run_dirs=run_dirs,
        output_dir=output_dir,
        blind_seed=args.blind_seed,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


def compare_runs(
    *,
    run_dirs: dict[str, Path],
    output_dir: Path,
    blind_seed: int,
) -> dict[str, Any]:
    missing_groups = [group for group in GROUPS if group not in run_dirs]
    if missing_groups:
        raise ValueError(f"missing experiment groups: {', '.join(missing_groups)}")
    runs = {
        group: _read_json(run_dirs[group] / "sequence_result.json")
        for group in GROUPS
    }
    _validate_runs(runs)

    group_summaries = {
        group: _group_summary(runs[group]) for group in GROUPS
    }
    native_total = group_summaries["native"]["llm_total_tokens"]
    managed_total = group_summaries["managed"]["llm_total_tokens"]
    observed_total = group_summaries["observed"]["llm_total_tokens"]
    summary = {
        "scenario_id": runs["native"]["summary"]["scenario_id"],
        "groups": group_summaries,
        "managed_vs_native": _token_delta(managed_total, native_total),
        "observed_vs_native": _token_delta(observed_total, native_total),
        "quality_evaluation_status": "pending_blind_review",
    }
    task_comparison = _task_comparison(runs)
    blind_batch, blind_mapping = _build_blind_batch(
        run_dirs=run_dirs,
        seed=blind_seed,
    )
    payload = {
        "summary": summary,
        "tasks": task_comparison,
        "warnings": _build_warnings(group_summaries),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "stateful_comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "stateful_comparison.md").write_text(
        _render_markdown(payload), encoding="utf-8"
    )
    (output_dir / "quality_blind_batch.json").write_text(
        json.dumps(blind_batch, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "quality_blind_mapping.json").write_text(
        json.dumps(blind_mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"missing experiment output: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _validate_runs(runs: dict[str, dict[str, Any]]) -> None:
    native_scenario = runs["native"]["summary"].get("scenario_id")
    native_agent_configs = runs["native"].get("agent_configs")
    native_tasks = [
        (task["task_id"], task["question"]) for task in runs["native"]["tasks"]
    ]
    if len(native_tasks) < 2:
        raise ValueError("stateful comparison requires at least two tasks")
    for group in GROUPS:
        summary = runs[group].get("summary", {})
        if summary.get("experiment_mode") != group:
            raise ValueError(
                f"{group} output reports experiment_mode="
                f"{summary.get('experiment_mode')!r}"
            )
        if summary.get("scenario_id") != native_scenario:
            raise ValueError(f"{group} scenario differs from native")
        if not summary.get("same_team_instance"):
            raise ValueError(f"{group} did not report one shared team instance")
        if runs[group].get("agent_configs") != native_agent_configs:
            raise ValueError(f"{group} agent configuration differs from native")
        group_tasks = [
            (task["task_id"], task["question"]) for task in runs[group]["tasks"]
        ]
        if group_tasks != native_tasks:
            raise ValueError(f"{group} task sequence differs from native")


def _group_summary(run: dict[str, Any]) -> dict[str, Any]:
    summary = run["summary"]
    usage = summary["llm_usage"]
    return {
        "task_count": int(summary["task_count"]),
        "valid_delivery_count": int(summary["valid_delivery_count"]),
        "llm_call_count": int(usage["calls"]),
        "llm_prompt_tokens": int(usage["llm_prompt_tokens"]),
        "llm_completion_tokens": int(usage["llm_completion_tokens"]),
        "llm_total_tokens": int(usage["llm_total_tokens"]),
        "retry_count": int(usage["retry_count"]),
        "wall_time_ms": int(summary["wall_time_ms"]),
    }


def _token_delta(value: int, baseline: int) -> dict[str, Any]:
    delta = value - baseline
    ratio = (delta / baseline) if baseline else None
    return {
        "token_delta": delta,
        "token_change_ratio": ratio,
        "actual_llm_tokens_lower": delta < 0,
    }


def _task_comparison(runs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    tasks_by_group = {
        group: {task["task_id"]: task for task in runs[group]["tasks"]}
        for group in GROUPS
    }
    rows: list[dict[str, Any]] = []
    for native_task in runs["native"]["tasks"]:
        task_id = native_task["task_id"]
        row: dict[str, Any] = {
            "task_id": task_id,
            "question": native_task["question"],
            "groups": {},
        }
        for group in GROUPS:
            task = tasks_by_group[group][task_id]
            usage = task["llm_usage"]
            row["groups"][group] = {
                "llm_call_count": int(usage["calls"]),
                "llm_prompt_tokens": int(usage["llm_prompt_tokens"]),
                "llm_completion_tokens": int(usage["llm_completion_tokens"]),
                "llm_total_tokens": int(usage["llm_total_tokens"]),
                "wall_time_ms": int(task["wall_time_ms"]),
                "delivery_valid": bool(task["delivery_valid"]),
                "final_answer_chars": int(task["final_answer_chars"]),
            }
        rows.append(row)
    return rows


def _build_blind_batch(
    *,
    run_dirs: dict[str, Path],
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    randomizer = random.Random(seed)
    candidates_by_task: dict[str, list[dict[str, str]]] = {}
    mapping: list[dict[str, str]] = []
    track_ids = {
        group: "track_"
        + hashlib.sha256(f"{seed}:{group}".encode("utf-8")).hexdigest()[:12]
        for group in GROUPS
    }
    answers_by_group_task: dict[str, dict[str, str]] = {
        group: {} for group in GROUPS
    }
    scenario_id = ""
    for group in GROUPS:
        candidate_payload = _read_json(
            run_dirs[group] / "quality_blind_candidates.json"
        )
        mapping_payload = _read_json(run_dirs[group] / "quality_blind_mapping.json")
        scenario_id = scenario_id or str(candidate_payload.get("scenario_id") or "")
        source_mapping = {
            item["candidate_id"]: item for item in mapping_payload["mapping"]
        }
        for candidate in candidate_payload["candidates"]:
            candidate_id = candidate["candidate_id"]
            task_id = source_mapping[candidate_id]["task_id"]
            candidates_by_task.setdefault(task_id, []).append(
                {
                    "candidate_id": candidate_id,
                    "track_id": track_ids[group],
                    "answer": candidate["answer"],
                    "_group": group,
                }
            )
            answers_by_group_task[group][task_id] = candidate["answer"]
            mapping.append(
                {
                    "candidate_id": candidate_id,
                    "track_id": track_ids[group],
                    "task_id": task_id,
                    "group": group,
                }
            )

    tasks: list[dict[str, Any]] = []
    native_sequence = _read_json(run_dirs["native"] / "sequence_result.json")
    questions = {task["task_id"]: task["question"] for task in native_sequence["tasks"]}
    previous_task_id = ""
    for task_id in questions:
        candidates = []
        for candidate in candidates_by_task[task_id]:
            group = candidate["_group"]
            candidates.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "track_id": candidate["track_id"],
                    "previous_answer": (
                        answers_by_group_task[group].get(previous_task_id, "")
                    ),
                    "answer": candidate["answer"],
                }
            )
        randomizer.shuffle(candidates)
        tasks.append(
            {
                "task_id": task_id,
                "question": questions[task_id],
                "candidates": candidates,
            }
        )
        previous_task_id = task_id
    return (
        {
            "scenario_id": scenario_id,
            "instructions": (
                "Blindly score each candidate before opening the mapping file. "
                "A stable anonymous track_id and its previous answer are included so "
                "state continuity can be judged without revealing experiment groups."
            ),
            "tasks": tasks,
        },
        {
            "scenario_id": scenario_id,
            "blind_seed": seed,
            "mapping": mapping,
        },
    )


def _build_warnings(group_summaries: dict[str, dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for group in GROUPS:
        summary = group_summaries[group]
        if summary["valid_delivery_count"] != summary["task_count"]:
            warnings.append(
                f"{group}: only {summary['valid_delivery_count']}/"
                f"{summary['task_count']} tasks produced strict final deliveries"
            )
    warnings.append(
        "Do not claim end-to-end token savings until blind quality review is complete."
    )
    return warnings


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Stateful AutoGen A1-A10 comparison",
        "",
        "## Provider-reported LLM usage",
        "",
        "| Group | Calls | Prompt | Completion | Total | Valid deliveries | Wall time (s) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group in GROUPS:
        item = summary["groups"][group]
        lines.append(
            f"| {group} | {item['llm_call_count']:,} | "
            f"{item['llm_prompt_tokens']:,} | {item['llm_completion_tokens']:,} | "
            f"{item['llm_total_tokens']:,} | {item['valid_delivery_count']}/"
            f"{item['task_count']} | {item['wall_time_ms'] / 1000:.1f} |"
        )
    delta = summary["managed_vs_native"]
    ratio = delta["token_change_ratio"]
    ratio_text = "n/a" if ratio is None else f"{ratio:+.1%}"
    lines.extend(
        [
            "",
            "## Managed versus native",
            "",
            f"- Actual LLM token delta: {delta['token_delta']:+,} ({ratio_text})",
            f"- Actual LLM tokens lower: {delta['actual_llm_tokens_lower']}",
            "- Quality status: pending blind review",
            "",
            "## Warnings",
            "",
        ]
    )
    lines.extend(f"- {warning}" for warning in payload["warnings"])
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
