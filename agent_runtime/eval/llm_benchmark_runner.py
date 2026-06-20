from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.core.models import Mode, TaskSpec
from agent_runtime.core.runtime import V0Runtime
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.llm.agents import build_mimo_travel_agents
from agent_runtime.llm.client import OpenAICompatibleChatClient
from agent_runtime.llm.config import LlmConfig
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.state.state_pool import StatePoolLite


def load_task_suite(path: Path) -> list[TaskSpec]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return [TaskSpec(**item) for item in payload["tasks"]]


def run_llm_benchmark(
    *,
    task_suite_paths: list[Path],
    output_dir: Path,
    rounds: int,
    modes: list[Mode],
    llm_config: LlmConfig,
    tokenizer_name: str | None = None,
    model_name: str | None = None,
    allow_estimated_tokens: bool = False,
    max_tasks: int | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = MetricsCollector()
    trace = TraceLogger(output_dir)
    state_pool = StatePoolLite(output_dir)
    memory_store = MemoryStoreLite()
    token_counter = TokenCounter(
        tokenizer_name=tokenizer_name,
        model_name=model_name,
        allow_estimate=allow_estimated_tokens,
    )
    client = OpenAICompatibleChatClient(llm_config)
    runtime = V0Runtime(
        agents=build_mimo_travel_agents(client),
        token_counter=token_counter,
        metrics=metrics,
        trace=trace,
        state_pool=state_pool,
        memory_store=memory_store,
    )

    tasks: list[TaskSpec] = []
    for path in task_suite_paths:
        tasks.extend(load_task_suite(path))
    if max_tasks is not None:
        tasks = tasks[:max_tasks]

    trace.write(
        "llm_benchmark_started",
        {"llm_config": llm_config.without_secret(), "task_count": len(tasks)},
    )
    for round_id in range(1, rounds + 1):
        for mode in modes:
            for task in tasks:
                runtime.run_task(task=task, round_id=round_id, mode=mode)

    runtime.flush_background_tasks()
    metrics.export(output_dir)
    summary = metrics.summary()
    summary["llm_config"] = llm_config.without_secret()
    with (output_dir / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary
