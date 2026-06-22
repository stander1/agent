from __future__ import annotations

import json
from pathlib import Path

from agent_runtime.core.agents import build_default_agents
from agent_runtime.core.models import Mode, TaskSpec
from agent_runtime.core.runtime import V0Runtime
from agent_runtime.eval.metrics import MetricsCollector
from agent_runtime.eval.token_counter import TokenCounter
from agent_runtime.eval.trace_logger import TraceLogger
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.state.state_pool import StatePoolLite


def load_task_suite(path: Path) -> list[TaskSpec]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return [TaskSpec(**item) for item in payload["tasks"]]


def run_v0_benchmark(
    task_suite_paths: list[Path],
    output_dir: Path,
    rounds: int,
    modes: list[Mode],
    tokenizer_name: str | None = None,
    model_name: str | None = None,
    allow_estimated_tokens: bool = False,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = MetricsCollector()
    trace = TraceLogger(output_dir)
    state_pool = StatePoolLite(output_dir)
    memory_store = MemoryStoreLite(output_dir / "memory")
    token_counter = TokenCounter(
        tokenizer_name=tokenizer_name,
        model_name=model_name,
        allow_estimate=allow_estimated_tokens,
    )
    runtime = V0Runtime(
        agents=build_default_agents(),
        token_counter=token_counter,
        metrics=metrics,
        trace=trace,
        state_pool=state_pool,
        memory_store=memory_store,
    )

    tasks: list[TaskSpec] = []
    for path in task_suite_paths:
        tasks.extend(load_task_suite(path))

    try:
        for round_id in range(1, rounds + 1):
            for mode in modes:
                for task in tasks:
                    runtime.run_task(task=task, round_id=round_id, mode=mode)
    finally:
        runtime.close()
        metrics.export(output_dir)
        summary = metrics.summary()
        with (output_dir / "summary.json").open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    return summary
