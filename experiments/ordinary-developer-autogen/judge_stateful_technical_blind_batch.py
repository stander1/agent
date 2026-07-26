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
    "constraint_fidelity": 2,
    "technical_correctness": 4,
    "internal_consistency": 2,
    "executability": 2,
}
SEVERITY_CAPS = {
    "critical": 5,
    "high": 6,
    "medium": 8,
    "low": 9,
}
ALLOWED_SEVERITIES = frozenset(SEVERITY_CAPS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Blindly audit technical correctness of stateful candidates."
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
            or "Technical judge response root must be an object"
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
        answer_sections.append(
            f"## {item['candidate_id']}（匿名轨道 {item.get('track_id', '')}）\n"
            f"### 上一轮交付物\n{previous_answer or '（第一轮，无上一轮交付物）'}\n\n"
            f"### 当前交付物\n{item['answer']}"
        )
    answers = "\n\n".join(answer_sections)
    candidate_ids = [str(item["candidate_id"]) for item in candidates]
    return f"""
你是独立、保守的技术审查员。下面是连续任务在 {task_id} 时点的需求历史和三个匿名候选。你的职责不是评价文风，而是寻找会影响真实实施的技术缺陷。

审查纪律：
1. 逐项检查命令、SQL、代码、配置参数、单位换算、并发模型、生命周期、恢复流程和验收标准是否可执行且相互一致。
2. 主动检查技术方言混用、参数单位误解、先后顺序错误、数据破坏风险、没有证据的保证、前后轮结论冲突和遗漏硬约束。
3. 不因回答很长、术语很多或结构漂亮而加技术分；也不能为了拉开差距而臆造错误。
4. finding 的 evidence 必须引用候选中的短文本，issue 必须解释具体错误，repair 给出通用修正方向。
5. severity 只能是 critical、high、medium、low。critical/high 表示会导致核心方案不可执行、数据错误或违反硬约束；medium 表示局部命令、配置或论证需要修正；low 表示不阻断使用的小问题。
6. 有 critical/high finding 时 delivery_usable 必须为 false；存在任何明确 finding 时不得给 10 分。
7. 分项为：约束忠实度 0-2、技术正确性 0-4、内部一致性 0-2、可执行性 0-2。
8. 三个候选分别审查，允许并列，只返回合法 JSON。

JSON 结构：
{{
  "task_id": "{task_id}",
  "evaluations": [
    {{
      "candidate_id": "{candidate_ids[0]}",
      "constraint_fidelity": 0,
      "technical_correctness": 0,
      "internal_consistency": 0,
      "executability": 0,
      "total": 0,
      "delivery_usable": false,
      "findings": [
        {{
          "severity": "medium",
          "evidence": "候选中的短文本",
          "issue": "具体问题",
          "repair": "修正方向"
        }}
      ],
      "strengths": ["经过技术核验的优点"]
    }}
  ],
  "best_candidate_ids": ["candidate_id"],
  "summary": "一句话说明技术质量差异"
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
            raise ValueError("Each technical evaluation must be an object")
        candidate_id = str(row.get("candidate_id", ""))
        if candidate_id not in candidate_ids or candidate_id in by_id:
            raise ValueError(f"Unexpected or duplicate candidate_id: {candidate_id!r}")
        normalized: dict[str, Any] = {"candidate_id": candidate_id}
        raw_total = 0
        for field, maximum in SCORE_LIMITS.items():
            value = int(row.get(field, -1))
            if not 0 <= value <= maximum:
                raise ValueError(f"{candidate_id} {field} out of range: {value}")
            normalized[field] = value
            raw_total += value
        findings = _normalize_findings(row.get("findings"), candidate_id)
        score_cap = min(
            (SEVERITY_CAPS[item["severity"]] for item in findings),
            default=10,
        )
        total = min(raw_total, score_cap)
        blocking = any(
            item["severity"] in {"critical", "high"} for item in findings
        )
        normalized.update(
            {
                "raw_total": raw_total,
                "score_cap": score_cap,
                "total": total,
                "delivery_usable": bool(row.get("delivery_usable", False))
                and not blocking,
                "blocking_finding_count": sum(
                    item["severity"] in {"critical", "high"}
                    for item in findings
                ),
                "finding_count": len(findings),
                "findings": findings,
                "strengths": [str(value) for value in row.get("strengths", [])],
            }
        )
        by_id[candidate_id] = normalized
    if set(by_id) != set(candidate_ids):
        raise ValueError("Technical judge did not score every candidate")
    max_total = max(row["total"] for row in by_id.values())
    best_ids = [cid for cid in candidate_ids if by_id[cid]["total"] == max_total]
    return {
        "task_id": task_id,
        "evaluations": [by_id[cid] for cid in candidate_ids],
        "best_candidate_ids": best_ids,
        "summary": str(parsed.get("summary", "")),
    }


def _normalize_findings(value: Any, candidate_id: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{candidate_id} findings must be a list")
    findings: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{candidate_id} finding must be an object")
        severity = str(item.get("severity") or "").casefold()
        if severity not in ALLOWED_SEVERITIES:
            raise ValueError(f"{candidate_id} invalid finding severity: {severity!r}")
        evidence = str(item.get("evidence") or "").strip()
        issue = str(item.get("issue") or "").strip()
        repair = str(item.get("repair") or "").strip()
        if not evidence or not issue or not repair:
            raise ValueError(f"{candidate_id} finding lacks evidence/issue/repair")
        findings.append(
            {
                "severity": severity,
                "evidence": evidence[:240],
                "issue": issue,
                "repair": repair,
            }
        )
    return findings


def write_checkpoint(
    output: Path,
    *,
    batch: Path,
    config: dict[str, Any],
    results: list[dict[str, Any]],
) -> None:
    payload = {
        "schema_version": "agentlite.technical-blind-quality.v1",
        "blind_batch": str(batch),
        "judge_config": config,
        "score_limits": SCORE_LIMITS,
        "severity_caps": SEVERITY_CAPS,
        "results": results,
        "frozen_before_unblinding": True,
        "note": (
            "Technical judge calls are evaluation-only and excluded from runtime "
            "collaboration cost."
        ),
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
        raise ValueError(f"Invalid technical checkpoint results: {output}")

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
            raise ValueError(f"Invalid technical checkpoint row: {output}")
        task_id = str(result.get("task_id") or "")
        if task_id not in task_candidates or task_id in seen:
            raise ValueError(
                "Unexpected or duplicate task in technical checkpoint: "
                f"{task_id}"
            )
        candidate_ids = {
            str(item.get("candidate_id") or "")
            for item in result.get("evaluations", [])
            if isinstance(item, dict)
        }
        if candidate_ids != task_candidates[task_id]:
            raise ValueError(
                f"Candidate mismatch in technical checkpoint task {task_id}"
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
        candidates = list(task.get("candidates") or [])
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
                    "你是匿名技术审查员。逐项核验可执行性，只输出合法 JSON。"
                )
                user_prompt = prompt
            elif format_attempt == 1:
                retry_mode = "pruned_format_repair"
                system_prompt = (
                    "你是同一个匿名技术审查员，只修复上一响应的 JSON 合同。"
                )
                user_prompt = render_pruned_json_retry_prompt(
                    task_id=task_id,
                    candidate_ids=candidate_ids,
                    invalid_response=last_response,
                    validation_error=str(last_error or ""),
                    evaluation_fields=[
                        *SCORE_LIMITS,
                        "delivery_usable",
                        "findings",
                        "strengths",
                    ],
                    response_kind="technical_blind_audit",
                )
            else:
                retry_mode = "concise_full_reaudit"
                system_prompt = (
                    "你是匿名技术审查员。沿用原评分标准，精简说明并只输出合法 JSON。"
                )
                user_prompt = prompt + concise_reaudit_suffix(
                    validation_error=str(last_error or ""),
                    candidate_ids=candidate_ids,
                    technical=True,
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
                        or "Technical judge JSON parse failed"
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
                print(
                    f"[{index}/{len(tasks)}] "
                    f"{task_id} technical audit complete",
                    flush=True,
                )
                break
            except (TypeError, ValueError) as exc:
                attempt_record["status"] = "invalid"
                attempt_record["validation_error"] = str(exc)
                judge_attempts.append(attempt_record)
                last_error = exc
                last_response = response.content
        else:
            raise RuntimeError(
                f"Unable to technically audit {task_id}: {last_error}"
            ) from last_error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
