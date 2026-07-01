from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.llm.client import OpenAICompatibleChatClient
from agent_runtime.llm.config import load_llm_config


BASELINE_MODE = "baseline_bounded_nl_framework"
RUNTIME_MODE = "runtime_lite"
DEFAULT_TASKS = ("A10", "B10")
BENCHMARKS = {
    "A": PROJECT_ROOT / "benchmarks" / "travel_task_group_a.json",
    "B": PROJECT_ROOT / "benchmarks" / "security_task_group_b.json",
}


@dataclass(frozen=True)
class FinalAnswer:
    task_id: str
    mode: str
    content: str
    content_chars: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Judge A10/B10 final_answer quality without counting judge cost as runtime cost."
    )
    parser.add_argument(
        "--baseline-run",
        type=Path,
        required=True,
        help="Run directory containing baseline deliverables.json.",
    )
    parser.add_argument(
        "--runtime-run",
        type=Path,
        required=True,
        help="Run directory containing runtime_lite deliverables.json.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output JSON path for judge results.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "llm.mimo.example.json",
    )
    parser.add_argument("--task", action="append", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.8)
    return parser.parse_args()


def load_deliverables(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "deliverables.json"
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    deliverables = payload.get("deliverables", [])
    if not isinstance(deliverables, list):
        raise ValueError(f"Invalid deliverables in {path}")
    return deliverables


def final_answer_for(run_dir: Path, task_id: str, mode: str) -> FinalAnswer:
    for item in load_deliverables(run_dir):
        if item.get("task_id") != task_id:
            continue
        final_answer = item.get("final_answer")
        if not isinstance(final_answer, dict):
            raise ValueError(f"{run_dir} {task_id} has no final_answer payload")
        content = str(final_answer.get("content", ""))
        if not content.strip():
            raise ValueError(f"{run_dir} {task_id} final_answer is empty")
        return FinalAnswer(
            task_id=task_id,
            mode=mode,
            content=content,
            content_chars=len(content),
        )
    raise ValueError(f"{run_dir} has no deliverable for task {task_id}")


def suite_key(task_id: str) -> str:
    key = task_id[:1].upper()
    if key not in BENCHMARKS:
        raise ValueError(f"Unsupported task id for benchmark lookup: {task_id}")
    return key


def load_suite_brief(task_id: str) -> str:
    with BENCHMARKS[suite_key(task_id)].open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    lines: list[str] = []
    for task in payload.get("tasks", []):
        tid = task.get("task_id")
        title = task.get("title", "")
        prompt = task.get("prompt", "")
        documents = task.get("documents", [])
        lines.append(f"[{tid}] {title}\nPrompt: {prompt}")
        if documents:
            doc_lines = "\n".join(f"- {doc}" for doc in documents)
            lines.append(f"Documents:\n{doc_lines}")
    return "\n\n".join(lines)


def json_from_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.S | re.I)
    if fence:
        cleaned = fence.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Judge response JSON root must be an object")
    return parsed


def build_prompt(task_id: str, answer_1: FinalAnswer, answer_2: FinalAnswer) -> str:
    suite_brief = load_suite_brief(task_id)
    return f"""
请作为严格但公平的中文质量裁判，比较两个多 Agent 连续任务的最终交付物。

重要口径：
1. 只评价最终交付正文质量，不评价通信 token、延迟、系统架构或运行方式。
2. 两个答案都应被视为“最终交付物”，不要把 reviewer 审查报告当成更高质量的类型。
3. 不要因为答案更长就自动给高分；也不要因为答案更短就自动扣分。核心看完整性、可执行性、证据链和一致性。
4. 如果答案缺少关键约束、关键结论、预算/风险/证据字段、修订日志或决策日志，应明确扣分。
5. 每个维度使用 1-5 分整数，总分为各维度相加。winner 只能是 answer_1、answer_2 或 tie。
6. 必须只返回合法 JSON，不要输出 Markdown 代码块。

评估维度：
- requirement_coverage: 是否覆盖整个连续任务链的关键要求和最终任务要求。
- deliverable_completeness: 是否像一个完整可交付方案，而不是摘要或中间意见。
- specificity_and_actionability: 是否有具体、可执行、可复核的内容。
- consistency_and_revision_handling: 是否处理前序修订、冲突、废弃线索、预算或风险变化。
- evidence_traceability: 是否能追溯到任务资料、证据表、链路图或决策依据。
- decision_log_quality: 是否保留清晰的取舍理由、修订日志或系统决策日志。

请返回这个 JSON 结构：
{{
  "task_id": "{task_id}",
  "scores": {{
    "answer_1": {{
      "requirement_coverage": 1,
      "deliverable_completeness": 1,
      "specificity_and_actionability": 1,
      "consistency_and_revision_handling": 1,
      "evidence_traceability": 1,
      "decision_log_quality": 1,
      "total": 6
    }},
    "answer_2": {{
      "requirement_coverage": 1,
      "deliverable_completeness": 1,
      "specificity_and_actionability": 1,
      "consistency_and_revision_handling": 1,
      "evidence_traceability": 1,
      "decision_log_quality": 1,
      "total": 6
    }}
  }},
  "winner": "answer_1",
  "quality_drop_if_answer_2_is_runtime": false,
  "summary": "一句话总评",
  "answer_1_strengths": ["..."],
  "answer_1_risks": ["..."],
  "answer_2_strengths": ["..."],
  "answer_2_risks": ["..."],
  "comparison_reasons": ["..."]
}}

连续任务链资料：
{suite_brief}

Answer 1:
{answer_1.content}

Answer 2:
{answer_2.content}
""".strip()


def mapped_result(
    *,
    task_id: str,
    answer_1: FinalAnswer,
    answer_2: FinalAnswer,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    scores = parsed.get("scores", {})
    answer_1_scores = scores.get("answer_1", {}) if isinstance(scores, dict) else {}
    answer_2_scores = scores.get("answer_2", {}) if isinstance(scores, dict) else {}
    by_mode = {
        answer_1.mode: answer_1_scores,
        answer_2.mode: answer_2_scores,
    }
    winner = parsed.get("winner", "tie")
    winner_mode = "tie"
    if winner == "answer_1":
        winner_mode = answer_1.mode
    elif winner == "answer_2":
        winner_mode = answer_2.mode
    baseline_total = int(by_mode.get(BASELINE_MODE, {}).get("total", 0) or 0)
    runtime_total = int(by_mode.get(RUNTIME_MODE, {}).get("total", 0) or 0)
    return {
        "task_id": task_id,
        "answer_mapping": {
            "answer_1": answer_1.mode,
            "answer_2": answer_2.mode,
        },
        "content_chars": {
            answer_1.mode: answer_1.content_chars,
            answer_2.mode: answer_2.content_chars,
        },
        "scores_by_mode": by_mode,
        "winner": winner,
        "winner_mode": winner_mode,
        "runtime_quality_delta": runtime_total - baseline_total,
        "runtime_quality_drop": runtime_total < baseline_total,
        "parsed": parsed,
    }


def main() -> None:
    args = parse_args()
    task_ids = args.task or list(DEFAULT_TASKS)
    config = load_llm_config(
        args.config,
        temperature=args.temperature,
        top_p=args.top_p,
    )
    client = OpenAICompatibleChatClient(config)
    results: list[dict[str, Any]] = []
    judge_total_tokens = 0
    judge_latency_ms = 0.0

    for task_id in task_ids:
        baseline = final_answer_for(args.baseline_run, task_id, BASELINE_MODE)
        runtime = final_answer_for(args.runtime_run, task_id, RUNTIME_MODE)
        if task_id.startswith("A"):
            answer_1, answer_2 = baseline, runtime
        else:
            answer_1, answer_2 = runtime, baseline

        result = client.complete(
            system_prompt=(
                "你是独立质量裁判。你只输出合法 JSON。"
                "不要评价系统名、成本或延迟，只评价最终交付物质量。"
            ),
            user_prompt=build_prompt(task_id, answer_1, answer_2),
        )
        parsed = json_from_response(result.content)
        mapped = mapped_result(
            task_id=task_id,
            answer_1=answer_1,
            answer_2=answer_2,
            parsed=parsed,
        )
        mapped["judge_usage"] = result.usage
        mapped["judge_latency_ms"] = result.latency_ms
        mapped["judge_model"] = result.model
        mapped["raw_response"] = result.content
        judge_total_tokens += int(result.usage.get("total_tokens", 0))
        judge_latency_ms += result.latency_ms
        results.append(mapped)

    output = {
        "judge_model": config.model,
        "judge_config": config.without_secret(),
        "baseline_run": str(args.baseline_run),
        "runtime_run": str(args.runtime_run),
        "judge_total_tokens": judge_total_tokens,
        "judge_latency_ms": judge_latency_ms,
        "results": results,
        "note": "Judge calls are evaluation-only and are not counted as runtime collaboration cost.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
