from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


AGENT_ORDER = [
    "planner",
    "retriever",
    "writer",
    "reviewer",
    "memory_manager",
    "local_runtime",
]
AGENT_LABELS = {
    "planner": "Planner",
    "retriever": "Retriever",
    "writer": "Writer",
    "reviewer": "Reviewer",
    "memory_manager": "MemoryManager",
    "local_runtime": "Local runtime overhead",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a markdown latency report from agent_metrics.json."
    )
    parser.add_argument("agent_metrics_json", type=Path)
    parser.add_argument("--mode", default="runtime_lite")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def ordered_agents(summary: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    used = set()
    for agent in AGENT_ORDER:
        if agent in summary:
            result.append((agent, summary[agent]))
            used.add(agent)
    for agent in sorted(set(summary) - used):
        result.append((agent, summary[agent]))
    return result


def safe_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_report(payload: dict[str, Any], mode: str) -> str:
    summary = payload.get("summary", {}).get(mode, {})
    if not isinstance(summary, dict):
        raise ValueError(f"agent_metrics.json does not contain mode: {mode}")
    rows = ordered_agents(summary)
    labels = [AGENT_LABELS.get(agent, agent) for agent, _ in rows]
    values = [
        safe_number(row.get("total_wall_time_ms", 0.0))
        for _, row in rows
    ]
    max_value = max(values + [1.0])
    y_max = int(max_value * 1.15) + 1

    lines = [
        "# Agent Latency Breakdown",
        "",
        f"Mode: `{mode}`",
        "",
        "```mermaid",
        "xychart-beta",
        '    title "Agent latency breakdown"',
        f"    x-axis {json.dumps(labels, ensure_ascii=False)}",
        f'    y-axis "ms" 0 --> {y_max}',
        f"    bar {json.dumps([round(v, 3) for v in values])}",
        "```",
        "",
        "| agent | llm_wall_time_ms | local_state_read_ms | local_memory_search_ms | artifact_digest_ms | schema_check_ms | retry_count | raw_access_count | completion_tokens | tokens_per_second |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for agent, row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{AGENT_LABELS.get(agent, agent)}`",
                    f"{safe_number(row.get('llm_wall_time_ms')):.3f}",
                    f"{safe_number(row.get('local_state_read_ms')):.3f}",
                    f"{safe_number(row.get('local_memory_search_ms')):.3f}",
                    f"{safe_number(row.get('artifact_digest_ms')):.3f}",
                    f"{safe_number(row.get('schema_check_ms')):.3f}",
                    str(int(safe_number(row.get("retry_count")))),
                    str(int(safe_number(row.get("raw_access_count")))),
                    str(int(safe_number(row.get("llm_completion_tokens")))),
                    f"{safe_number(row.get('tokens_per_second')):.3f}",
                ]
            )
            + " |"
        )

    local = summary.get("local_runtime", {})
    local_overhead = safe_number(local.get("total_wall_time_ms"))
    llm_total = sum(safe_number(row.get("llm_wall_time_ms")) for _, row in rows)
    raw_access = sum(int(safe_number(row.get("raw_access_count"))) for _, row in rows)
    lines.extend(
        [
            "",
            "## Evidence Notes",
            "",
            f"- Local runtime overhead total: `{local_overhead:.3f} ms`.",
            f"- LLM wall time total: `{llm_total:.3f} ms`.",
            f"- raw_access_count total: `{raw_access}`.",
            "- `ttft_ms` remains 0 until the provider client uses streaming or exposes first-token timing.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    args = parse_args()
    payload = json.loads(args.agent_metrics_json.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(payload, args.mode), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
