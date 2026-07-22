from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from agent_runtime.eval.experiment_archive import (
    verify_bound_experiment,
    verify_experiment_archive,
)

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
    parser.add_argument(
        "--allow-legacy-unbound",
        action="store_true",
        help="Allow comparison of older outputs without immutable run/session bindings.",
    )
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
        require_immutable=not args.allow_legacy_unbound,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


def compare_runs(
    *,
    run_dirs: dict[str, Path],
    output_dir: Path,
    blind_seed: int,
    require_immutable: bool = True,
) -> dict[str, Any]:
    missing_groups = [group for group in GROUPS if group not in run_dirs]
    if missing_groups:
        raise ValueError(f"missing experiment groups: {', '.join(missing_groups)}")
    runs = {
        group: _read_json(run_dirs[group] / "sequence_result.json")
        for group in GROUPS
    }
    _validate_runs(runs)
    archive_evidence = (
        _validate_archive_evidence(run_dirs=run_dirs, runs=runs)
        if require_immutable
        else {group: {"status": "legacy_unverified"} for group in GROUPS}
    )

    group_summaries = {
        group: _group_summary(runs[group]) for group in GROUPS
    }
    native_total = group_summaries["native"]["llm_total_tokens"]
    managed_total = group_summaries["managed"]["llm_total_tokens"]
    observed_total = group_summaries["observed"]["llm_total_tokens"]
    normalized_common_calls = _normalized_common_call_summary(run_dirs)
    summary = {
        "scenario_id": runs["native"]["summary"]["scenario_id"],
        "groups": group_summaries,
        "managed_vs_native": _token_delta(managed_total, native_total),
        "observed_vs_native": _token_delta(observed_total, native_total),
        "normalized_common_calls": normalized_common_calls,
        "quality_evaluation_status": "pending_blind_review",
        "archive_binding_required": require_immutable,
        "archive_evidence": archive_evidence,
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
    if any(output_dir.iterdir()):
        raise FileExistsError(
            f"Comparison output directory is not empty: {output_dir}. "
            "Use a new directory to preserve previous comparison evidence."
        )
    _write_text_exclusive(
        output_dir / "stateful_comparison.json",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    _write_text_exclusive(
        output_dir / "stateful_comparison.md",
        _render_markdown(payload),
    )
    _write_text_exclusive(
        output_dir / "quality_blind_batch.json",
        json.dumps(blind_batch, ensure_ascii=False, indent=2),
    )
    _write_text_exclusive(
        output_dir / "quality_blind_mapping.json",
        json.dumps(blind_mapping, ensure_ascii=False, indent=2),
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


def _validate_archive_evidence(
    *,
    run_dirs: dict[str, Path],
    runs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    run_ids: set[str] = set()
    for group in GROUPS:
        verified = (
            verify_experiment_archive(run_dirs[group])
            if group == "native"
            else verify_bound_experiment(run_dirs[group])
        )
        manifest = _read_json(run_dirs[group] / "experiment_run.json")
        summary_binding = runs[group].get("summary", {}).get("experiment_binding")
        if not isinstance(summary_binding, dict):
            raise ValueError(f"{group} sequence result has no experiment binding")
        run_id = str(verified["run_id"])
        if run_id in run_ids:
            raise ValueError(f"duplicate experiment run_id across groups: {run_id}")
        run_ids.add(run_id)
        if str(manifest.get("experiment_mode") or "") != group:
            raise ValueError(f"{group} archive manifest reports a different mode")
        if str(manifest.get("scenario_id") or "") != str(
            runs[group]["summary"].get("scenario_id") or ""
        ):
            raise ValueError(f"{group} archive scenario differs from sequence result")
        if str(summary_binding.get("run_id") or "") != run_id:
            raise ValueError(f"{group} sequence result belongs to another run_id")
        if str(summary_binding.get("agentlite_session_id") or "") != str(
            verified["session_id"]
        ):
            raise ValueError(f"{group} sequence result belongs to another session")
        if group == "native" and verified["session_id"]:
            raise ValueError("native archive unexpectedly belongs to an AgentLite session")
        evidence[group] = {
            "status": "verified",
            "run_id": run_id,
            "session_id": str(verified["session_id"]),
            "checks": dict(verified["checks"]),
            "session_source": str(verified.get("session_source") or "none"),
        }
    return evidence


def _write_text_exclusive(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


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


def _normalized_common_call_summary(
    run_dirs: dict[str, Path],
) -> dict[str, Any]:
    rows_by_group: dict[str, dict[tuple[str, str, int], dict[str, Any]]] = {}
    for group in GROUPS:
        usage_path = run_dirs[group] / "llm_usage.jsonl"
        if not usage_path.is_file():
            return {
                "available": False,
                "reason": f"missing {group}/llm_usage.jsonl",
                "common_call_count": 0,
                "groups": {},
            }
        ordinal_by_agent_task: defaultdict[tuple[str, str], int] = defaultdict(int)
        keyed: dict[tuple[str, str, int], dict[str, Any]] = {}
        for line_number, line in enumerate(
            usage_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Invalid usage row: {usage_path}:{line_number}")
            task_id = str(row.get("task_id") or "")
            agent = str(row.get("agent") or "")
            if not task_id or not agent:
                raise ValueError(
                    f"Usage row lacks task_id/agent: {usage_path}:{line_number}"
                )
            ordinal_key = (task_id, agent)
            ordinal_by_agent_task[ordinal_key] += 1
            key = (task_id, agent, ordinal_by_agent_task[ordinal_key])
            keyed[key] = row
        rows_by_group[group] = keyed

    common_keys = set.intersection(
        *(set(rows_by_group[group]) for group in GROUPS)
    )
    ordered_keys = [
        key for key in rows_by_group["native"] if key in common_keys
    ]
    group_summaries: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        rows = [rows_by_group[group][key] for key in ordered_keys]
        prompt = sum(_usage_int(row, "llm_prompt_tokens") for row in rows)
        completion = sum(
            _usage_int(row, "llm_completion_tokens") for row in rows
        )
        total = sum(_usage_int(row, "llm_total_tokens") for row in rows)
        group_summaries[group] = {
            "matched_call_count": len(rows),
            "unmatched_call_count": len(rows_by_group[group]) - len(rows),
            "llm_prompt_tokens": prompt,
            "llm_completion_tokens": completion,
            "llm_total_tokens": total,
            "average_prompt_tokens_per_call": (
                prompt / len(rows) if rows else 0.0
            ),
        }
    native = group_summaries["native"]
    managed = group_summaries["managed"]
    return {
        "available": bool(ordered_keys),
        "matching_key": "task_id+agent+ordinal_within_task_agent",
        "common_call_count": len(ordered_keys),
        "groups": group_summaries,
        "managed_vs_native": {
            "prompt": _token_delta(
                managed["llm_prompt_tokens"], native["llm_prompt_tokens"]
            ),
            "completion": _token_delta(
                managed["llm_completion_tokens"],
                native["llm_completion_tokens"],
            ),
            "total": _token_delta(
                managed["llm_total_tokens"], native["llm_total_tokens"]
            ),
        },
    }


def _usage_int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


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
        ]
    )
    normalized = summary.get("normalized_common_calls") or {}
    if normalized.get("available"):
        common = int(normalized.get("common_call_count") or 0)
        lines.extend(
            [
                "## Normalized common logical calls",
                "",
                (
                    "Calls are matched by task, agent, and ordinal within that "
                    "task-agent pair. Extra turns are excluded from this table."
                ),
                "",
                "| Group | Matched calls | Unmatched calls | Prompt | Completion | Total |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for group in GROUPS:
            item = normalized["groups"][group]
            lines.append(
                f"| {group} | {common} | {item['unmatched_call_count']} | "
                f"{item['llm_prompt_tokens']:,} | "
                f"{item['llm_completion_tokens']:,} | "
                f"{item['llm_total_tokens']:,} |"
            )
        normalized_delta = normalized["managed_vs_native"]["total"]
        normalized_ratio = normalized_delta["token_change_ratio"]
        normalized_ratio_text = (
            "n/a" if normalized_ratio is None else f"{normalized_ratio:+.1%}"
        )
        lines.extend(
            [
                "",
                (
                    "- Managed versus native normalized total delta: "
                    f"{normalized_delta['token_delta']:+,} "
                    f"({normalized_ratio_text})"
                ),
                "",
            ]
        )
    lines.extend(["## Warnings", ""])
    lines.extend(f"- {warning}" for warning in payload["warnings"])
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
