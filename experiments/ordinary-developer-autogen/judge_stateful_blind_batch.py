from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.llm.client import OpenAICompatibleChatClient
from agent_runtime.llm.config import load_llm_config
from agent_runtime.reliability.structured_output_guard import (
    aggregate_attempt_usage,
    concise_reaudit_suffix,
    guard_structured_json_object,
    render_pruned_json_retry_prompt,
    response_fingerprint,
)


SCORE_LIMITS = {
    "task_completion": 4,
    "context_retention": 3,
    "correctness_consistency": 2,
    "clarity_actionability": 1,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Blindly score stateful experiment candidates before unblinding."
    )
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "llm.mimo.example.json",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.8)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--format-retries", type=int, default=2)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse validated per-task checkpoints already present in --output.",
    )
    return parser.parse_args()


def json_from_response(text: str) -> dict[str, Any]:
    result = guard_structured_json_object(text)
    if result.parsed is None:
        raise ValueError(
            "; ".join(result.errors)
            or "Judge response root must be an object"
        )
    return result.parsed


def build_prompt(
    *,
    task_id: str,
    task_history: list[dict[str, str]],
    candidates: list[dict[str, str]],
) -> str:
    history = "\n\n".join(
        f"[{item['task_id']}]\n{item['question']}" for item in task_history
    )
    answer_sections: list[str] = []
    for item in candidates:
        previous_answer = str(item.get("previous_answer", "")).strip()
        previous_section = previous_answer or "（这是该匿名轨道的第一轮，无上一轮交付物）"
        answer_sections.append(
            f"## {item['candidate_id']}（匿名轨道 {item.get('track_id', '')}）\n"
            f"### 上一轮交付物\n{previous_section}\n\n"
            f"### 当前交付物\n{item['answer']}"
        )
    answers = "\n\n".join(answer_sections)
    candidate_ids = [item["candidate_id"] for item in candidates]
    return f"""
你是独立、严格且公平的中文质量裁判。下面是一个连续任务在 {task_id} 时点的完整用户需求历史，以及三个匿名系统的当前最终交付物。

评分纪律：
1. 只评价最终交付正文，不推测系统身份，不评价 Token、延迟或架构。
2. 不因篇幅长而自动加分，也不因篇幅短而自动扣分。
3. 必须结合从第一问到 {task_id} 的全部约束，检查后续修订是否正确覆盖旧约束。
4. 任务完成度 0-4；上下文保持 0-3；正确性与一致性 0-2；清晰度与可执行性 0-1；总分 0-10。
5. 若只有审查意见、过程说明或拒绝，没有用户所需实际成果，任务完成度不得超过 1，总分不得超过 4。
6. delivery_complete 仅在当前任务要求已形成可直接使用的交付物时为 true。
7. 三个候选必须分别评分；允许并列最佳。
8. 只返回合法 JSON，不要返回 Markdown 代码块或额外文字。

必须使用以下 JSON 结构：
{{
  "task_id": "{task_id}",
  "evaluations": [
    {{
      "candidate_id": "{candidate_ids[0]}",
      "task_completion": 0,
      "context_retention": 0,
      "correctness_consistency": 0,
      "clarity_actionability": 0,
      "total": 0,
      "delivery_complete": false,
      "strengths": ["..."],
      "risks": ["..."]
    }}
  ],
  "best_candidate_ids": ["candidate_id"],
  "summary": "一句话说明主要差异"
}}

连续任务历史：
{history}

匿名候选：
{answers}
""".strip()


def normalize_result(
    parsed: dict[str, Any], *, task_id: str, candidate_ids: list[str]
) -> dict[str, Any]:
    if str(parsed.get("task_id")) != task_id:
        raise ValueError(f"Unexpected task_id: {parsed.get('task_id')!r}")
    rows = parsed.get("evaluations")
    if not isinstance(rows, list):
        raise ValueError("evaluations must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each evaluation must be an object")
        candidate_id = str(row.get("candidate_id", ""))
        if candidate_id not in candidate_ids or candidate_id in by_id:
            raise ValueError(f"Unexpected or duplicate candidate_id: {candidate_id!r}")
        normalized = {"candidate_id": candidate_id}
        total = 0
        for field, maximum in SCORE_LIMITS.items():
            value = int(row.get(field, -1))
            if not 0 <= value <= maximum:
                raise ValueError(f"{candidate_id} {field} out of range: {value}")
            normalized[field] = value
            total += value
        normalized["total"] = total
        normalized["delivery_complete"] = bool(row.get("delivery_complete", False))
        normalized["strengths"] = [str(v) for v in row.get("strengths", [])]
        normalized["risks"] = [str(v) for v in row.get("risks", [])]
        by_id[candidate_id] = normalized
    if set(by_id) != set(candidate_ids):
        raise ValueError("Judge did not score every candidate")
    max_total = max(row["total"] for row in by_id.values())
    best_ids = [cid for cid in candidate_ids if by_id[cid]["total"] == max_total]
    return {
        "task_id": task_id,
        "evaluations": [by_id[cid] for cid in candidate_ids],
        "best_candidate_ids": best_ids,
        "summary": str(parsed.get("summary", "")),
    }


def write_checkpoint(
    output: Path,
    *,
    batch: Path,
    config: dict[str, Any],
    results: list[dict[str, Any]],
) -> None:
    payload = {
        "blind_batch": str(batch),
        "judge_config": config,
        "score_limits": SCORE_LIMITS,
        "results": results,
        "frozen_before_unblinding": True,
        "note": "Judge calls are evaluation-only and excluded from runtime collaboration cost.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint_results(
    output: Path,
    *,
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not output.is_file():
        return []
    payload = json.loads(output.read_text(encoding="utf-8"))
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError(f"Invalid judge checkpoint results: {output}")

    task_candidates = {
        str(task["task_id"]): {
            str(item["candidate_id"])
            for item in task.get("candidates", [])
        }
        for task in tasks
    }
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError(f"Invalid judge checkpoint row: {output}")
        task_id = str(result.get("task_id") or "")
        if task_id not in task_candidates or task_id in seen:
            raise ValueError(
                f"Unexpected or duplicate task in judge checkpoint: {task_id}"
            )
        candidate_ids = {
            str(item.get("candidate_id") or "")
            for item in result.get("evaluations", [])
            if isinstance(item, dict)
        }
        if candidate_ids != task_candidates[task_id]:
            raise ValueError(
                f"Candidate mismatch in judge checkpoint task {task_id}"
            )
        seen.add(task_id)
        validated.append(result)
    return validated


def main() -> int:
    args = parse_args()
    batch = json.loads(args.batch.read_text(encoding="utf-8"))
    tasks = batch.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Blind batch has no tasks")
    config = load_llm_config(
        args.config,
        temperature=args.temperature,
        top_p=args.top_p,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
    )
    client = OpenAICompatibleChatClient(config)
    results = (
        load_checkpoint_results(args.output, tasks=tasks)
        if args.resume
        else []
    )
    completed_task_ids = {
        str(item.get("task_id") or "") for item in results
    }
    history: list[dict[str, str]] = []
    for index, task in enumerate(tasks, start=1):
        task_id = str(task["task_id"])
        history.append({"task_id": task_id, "question": str(task["question"])})
        if task_id in completed_task_ids:
            print(
                f"[{index}/{len(tasks)}] {task_id} checkpoint reused",
                flush=True,
            )
            continue
        candidates = task.get("candidates", [])
        candidate_ids = [str(item["candidate_id"]) for item in candidates]
        prompt = build_prompt(
            task_id=task_id,
            task_history=history,
            candidates=candidates,
        )
        last_error: Exception | None = None
        last_response = ""
        judge_attempts: list[dict[str, Any]] = []
        for format_attempt in range(args.format_retries + 1):
            if format_attempt == 0:
                retry_mode = "initial_audit"
                system_prompt = (
                    "你是匿名质量裁判，只输出可解析的合法 JSON。"
                )
                user_prompt = prompt
            elif format_attempt == 1:
                retry_mode = "pruned_format_repair"
                system_prompt = (
                    "你是同一个匿名质量裁判，只修复上一响应的 JSON 合同。"
                )
                user_prompt = render_pruned_json_retry_prompt(
                    task_id=task_id,
                    candidate_ids=candidate_ids,
                    invalid_response=last_response,
                    validation_error=str(last_error or ""),
                    evaluation_fields=[
                        *SCORE_LIMITS,
                        "delivery_complete",
                        "strengths",
                        "risks",
                    ],
                    response_kind="primary_blind_score",
                )
            else:
                retry_mode = "concise_full_reaudit"
                system_prompt = (
                    "你是匿名质量裁判。沿用原评分标准，精简说明并只输出合法 JSON。"
                )
                user_prompt = prompt + concise_reaudit_suffix(
                    validation_error=str(last_error or ""),
                    candidate_ids=candidate_ids,
                    technical=False,
                )
            response = client.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            guard = guard_structured_json_object(response.content)
            attempt_record = {
                "attempt": format_attempt,
                "mode": retry_mode,
                "usage": dict(response.usage),
                "latency_ms": response.latency_ms,
                "model": response.model,
                "finish_reason": response.raw_finish_reason,
                "provider_guard": response.provider_guard,
                "response_chars": len(response.content),
                "response_sha256": response_fingerprint(response.content),
                "repair_actions": list(guard.repair_actions),
                "parse_errors": list(guard.errors),
            }
            try:
                if guard.parsed is None:
                    raise ValueError(
                        "; ".join(guard.errors)
                        or "Judge JSON parse failed"
                    )
                normalized = normalize_result(
                    guard.parsed,
                    task_id=task_id,
                    candidate_ids=candidate_ids,
                )
                attempt_record["status"] = "valid"
                judge_attempts.append(attempt_record)
                normalized["judge_usage"] = aggregate_attempt_usage(
                    judge_attempts
                )
                normalized["judge_latency_ms"] = sum(
                    float(item.get("latency_ms") or 0.0)
                    for item in judge_attempts
                )
                normalized["judge_model"] = response.model
                normalized["format_retry_count"] = format_attempt
                normalized["judge_attempts"] = judge_attempts
                results.append(normalized)
                write_checkpoint(
                    args.output,
                    batch=args.batch,
                    config=config.without_secret(),
                    results=results,
                )
                print(f"[{index}/{len(tasks)}] {task_id} scored", flush=True)
                break
            except (TypeError, ValueError) as exc:
                attempt_record["status"] = "invalid"
                attempt_record["validation_error"] = str(exc)
                judge_attempts.append(attempt_record)
                last_error = exc
                last_response = response.content
        else:
            raise RuntimeError(f"Unable to score {task_id}: {last_error}") from last_error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
