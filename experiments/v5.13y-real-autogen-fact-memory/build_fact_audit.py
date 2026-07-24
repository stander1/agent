from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agent_runtime.eval.autogen_session_report import resolve_session_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a bounded fact-level memory audit from an AutoGen run."
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--session-id", default="latest")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-limit", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    session_dir, session_id = resolve_session_dir(
        data_dir=args.data_dir.expanduser().resolve(),
        session_id=args.session_id,
    )
    trace_path = session_dir / "autogen_driver" / "trace.jsonl"
    events = read_jsonl(trace_path)
    payload = build_audit(
        events=events,
        session_id=session_id,
        trace_path=trace_path,
        sample_limit=max(1, args.sample_limit),
    )
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "fact_attribution_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_csv(output_dir / "fact_attribution_sample.csv", payload["sample_rows"])
    (output_dir / "fact_attribution_audit.md").write_text(
        render_markdown(payload),
        encoding="utf-8",
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Trace row is not an object: {path}:{line_number}")
        rows.append(value)
    return rows


def build_audit(
    *,
    events: list[dict[str, Any]],
    session_id: str,
    trace_path: Path,
    sample_limit: int,
) -> dict[str, Any]:
    bridge_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for event in events:
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        event_type = str(event.get("event_type") or "")
        if event_type == "state_memory_bridge":
            bridge_rows.append(
                {
                    "task_id": str(payload.get("task_id") or ""),
                    "agent_id": str(payload.get("agent_id") or ""),
                    "admission_status": str(
                        payload.get("admission_status") or ""
                    ),
                    "raw_claim_count": _int(payload.get("raw_claim_count")),
                    "provisional_claim_count": _int(
                        payload.get("provisional_claim_count")
                    ),
                    "slot_mapping_success_count": _int(
                        payload.get("slot_mapping_success_count")
                    ),
                    "unresolved_scope_count": _int(
                        payload.get("unresolved_scope_count")
                    ),
                    "conflict_detected_count": _int(
                        payload.get("conflict_detected_count")
                    ),
                    "resolved_conflict_count": _int(
                        payload.get("resolved_conflict_count")
                    ),
                    "unresolved_conflict_count": _int(
                        payload.get("unresolved_conflict_count")
                    ),
                    "active_value_selection_count": _int(
                        payload.get("active_value_selection_count")
                    ),
                }
            )
            continue
        if event_type != "autogen_memory_adoption":
            continue
        raw_evidence = payload.get("evidence")
        if not isinstance(raw_evidence, list):
            continue
        for row in raw_evidence:
            if not isinstance(row, dict):
                continue
            memory_ref = (
                row.get("memory_ref")
                if isinstance(row.get("memory_ref"), dict)
                else {}
            )
            evidence_rows.append(
                {
                    "task_id": str(payload.get("task_id") or ""),
                    "agent_id": str(payload.get("agent_id") or ""),
                    "call_id": str(payload.get("call_id") or ""),
                    "memory_id": str(memory_ref.get("memory_id") or ""),
                    "status": str(row.get("status") or "unassessed"),
                    "attribution_mode": str(
                        row.get("attribution_mode")
                        or payload.get("attribution_mode")
                        or ""
                    ),
                    "semantic_key": str(row.get("semantic_key") or ""),
                    "active_value": row.get("active_value"),
                    "historical_values": list(
                        row.get("historical_values") or []
                    ),
                    "matched_fact_count": _int(
                        row.get("matched_fact_count")
                    ),
                    "matched_historical_fact_count": _int(
                        row.get("matched_historical_fact_count")
                    ),
                    "current_task_duplicate_fact_count": _int(
                        row.get("current_task_duplicate_fact_count")
                    ),
                    "classification_reason": str(
                        row.get("classification_reason") or ""
                    ),
                    "matched_output_spans": list(
                        row.get("matched_output_spans") or []
                    ),
                    "manual_label": "",
                    "manual_notes": "",
                }
            )

    status_counts = Counter(row["status"] for row in evidence_rows)
    summary = {
        "session_id": session_id,
        "trace_path": str(trace_path),
        "state_memory_bridge_event_count": len(bridge_rows),
        "raw_claim_count": sum(row["raw_claim_count"] for row in bridge_rows),
        "provisional_claim_count": sum(
            row["provisional_claim_count"] for row in bridge_rows
        ),
        "slot_mapping_success_count": sum(
            row["slot_mapping_success_count"] for row in bridge_rows
        ),
        "unresolved_scope_count": sum(
            row["unresolved_scope_count"] for row in bridge_rows
        ),
        "conflict_detected_count": sum(
            row["conflict_detected_count"] for row in bridge_rows
        ),
        "resolved_conflict_count": sum(
            row["resolved_conflict_count"] for row in bridge_rows
        ),
        "unresolved_conflict_count": sum(
            row["unresolved_conflict_count"] for row in bridge_rows
        ),
        "active_value_selection_count": sum(
            row["active_value_selection_count"] for row in bridge_rows
        ),
        "attribution_evidence_count": len(evidence_rows),
        "attribution_status_counts": dict(sorted(status_counts.items())),
        "sample_row_count": min(len(evidence_rows), sample_limit),
    }
    return {
        "schema_version": "agentlite.fact-attribution-audit.v1",
        "summary": summary,
        "bridge_rows": bridge_rows,
        "sample_rows": evidence_rows[:sample_limit],
        "review_instructions": [
            "CSV 中 manual_label 仅允许填写 correct、false_positive、false_negative 或 uncertain。",
            "判断必须同时核对 active_value、historical_values、matched_output_spans 和对应任务输出。",
            "本文件是人工抽查入口，不把空白人工标签冒充已完成质量结论。",
        ],
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "task_id",
        "agent_id",
        "call_id",
        "memory_id",
        "status",
        "attribution_mode",
        "semantic_key",
        "active_value",
        "historical_values",
        "matched_fact_count",
        "matched_historical_fact_count",
        "current_task_duplicate_fact_count",
        "classification_reason",
        "matched_output_spans",
        "manual_label",
        "manual_notes",
    ]
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "active_value": json.dumps(
                        row.get("active_value"),
                        ensure_ascii=False,
                    ),
                    "historical_values": json.dumps(
                        row.get("historical_values", []),
                        ensure_ascii=False,
                    ),
                    "matched_output_spans": json.dumps(
                        row.get("matched_output_spans", []),
                        ensure_ascii=False,
                    ),
                }
            )


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# v5.13y 事实级记忆采用抽样",
        "",
        f"- AgentLite 会话：`{summary['session_id']}`",
        f"- 状态到记忆桥接事件：{summary['state_memory_bridge_event_count']}",
        f"- 原始事实：{summary['raw_claim_count']}",
        f"- provisional Claim：{summary['provisional_claim_count']}",
        f"- Slot 映射成功：{summary['slot_mapping_success_count']}",
        f"- 当前有效值选择：{summary['active_value_selection_count']}",
        f"- 采用证据：{summary['attribution_evidence_count']}",
        f"- 抽样行数：{summary['sample_row_count']}",
        "",
        "## 分类分布",
        "",
    ]
    counts = summary["attribution_status_counts"]
    lines.extend(
        f"- `{name}`：{count}" for name, count in sorted(counts.items())
    )
    lines.extend(
        [
            "",
            "## 人工复核",
            "",
            "请打开 `fact_attribution_sample.csv`，结合任务输出填写 "
            "`manual_label` 和 `manual_notes`。未填写前只能称为待抽查样本，"
            "不能宣称假阳性或假阴性已经通过人工验收。",
            "",
        ]
    )
    return "\n".join(lines)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
