from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from web_monitor.parser import build_session_snapshot, list_sessions


ReportFormat = Literal["json", "csv", "markdown"]


@dataclass(frozen=True)
class SessionReportRequest:
    data_dir: Path
    session_id: str | None = None
    output: Path | None = None
    report_format: ReportFormat = "markdown"


def build_autogen_session_report(request: SessionReportRequest) -> dict[str, Any]:
    data_dir = request.data_dir.expanduser().resolve()
    session_dir, resolved_session_id = resolve_session_dir(
        data_dir=data_dir,
        session_id=request.session_id,
    )
    snapshot = build_session_snapshot(session_dir, session_id=resolved_session_id)
    token_summary = snapshot.get("token_summary", {})
    summary = snapshot.get("summary", {})
    rows = _metric_rows(token_summary)
    native = _int(token_summary.get("native_baseline_tokens"))
    runtime = _int(token_summary.get("end_to_end_collaboration_tokens"))
    savings = native - runtime if native else 0
    ratio = savings / native if native > 0 else 0.0
    return {
        "session_id": snapshot.get("session_id") or resolved_session_id,
        "session_dir": str(session_dir),
        "trace_path": str(summary.get("trace_path") or ""),
        "status": snapshot.get("status") or "unknown",
        "framework": summary.get("framework") or "autogen",
        "driver_status": summary.get("driver_status") or "",
        "hooks_active": bool(summary.get("hooks_active")),
        "event_counts": summary.get("event_counts") or {},
        "token_summary": {
            **token_summary,
            "actual_native_transport_tokens": native,
            "actual_agentlite_transport_tokens": runtime,
            "actual_transport_token_savings": savings,
            "actual_transport_token_savings_ratio": round(ratio, 6),
            "native_collaboration_tokens": native,
            "agentlite_runtime_tokens": runtime,
            "agentlite_token_savings": savings,
            "agentlite_token_savings_ratio": round(ratio, 6),
        },
        "metric_rows": rows,
        "notes": [
            "actual_* 仅汇总真实改写审计事件；改写回退时按原生传输成本计入，不能记作节省。",
            "shadow_* 是未实际替换消息的理论候选成本，只表示潜力，不是已实现节省。",
            "llm_* 字段来自 AutoGen 模型客户端 usage hook；如果目标框架没有暴露 usage，则仍应保存 provider 后台记录作为旁证。",
            "记忆命中、注入与有效采用是三个不同阶段；未取得下游引用证据的命中保持 unassessed。",
        ],
    }


def resolve_session_dir(*, data_dir: Path, session_id: str | None) -> tuple[Path, str]:
    sessions = list_sessions(data_dir)
    if not sessions:
        raise FileNotFoundError(f"No AgentLite sessions found under {data_dir / 'sessions'}")
    requested = (session_id or "latest").strip()
    if requested in {"", "latest"}:
        selected = sessions[0]
    else:
        matches = [item for item in sessions if item.get("session_id") == requested]
        if not matches:
            raise FileNotFoundError(f"Session {requested!r} not found under {data_dir / 'sessions'}")
        selected = matches[0]
    resolved_id = str(selected.get("session_id") or requested)
    session_dir = data_dir / "sessions" / resolved_id
    if not session_dir.exists():
        # Status files may report a different session_id from the folder name in tests
        # or copied artifacts; fall back to the listed path when available.
        path_value = selected.get("path")
        if path_value:
            session_dir = Path(str(path_value))
    return session_dir.resolve(), resolved_id


def render_autogen_session_report(report: dict[str, Any], report_format: ReportFormat) -> str:
    if report_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    if report_format == "csv":
        return _render_csv(report)
    if report_format == "markdown":
        return _render_markdown(report)
    raise ValueError(f"Unsupported report format: {report_format}")


def write_autogen_session_report(request: SessionReportRequest) -> dict[str, Any]:
    report = build_autogen_session_report(request)
    text = render_autogen_session_report(report, request.report_format)
    if request.output:
        output = request.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        report["output_path"] = str(output)
    else:
        print(text)
    return report


def _metric_rows(token_summary: dict[str, Any]) -> list[dict[str, Any]]:
    native = _int(token_summary.get("native_baseline_tokens"))
    runtime = _int(token_summary.get("end_to_end_collaboration_tokens"))
    direct = _int(token_summary.get("direct_message_tokens"))
    prompt_view = _int(token_summary.get("prompt_view_tokens"))
    retrieved = _int(token_summary.get("retrieved_memory_tokens"))
    control = _int(token_summary.get("control_llm_tokens"))
    retry = _int(token_summary.get("retry_tokens"))
    llm_prompt = _int(token_summary.get("llm_prompt_tokens"))
    llm_completion = _int(token_summary.get("llm_completion_tokens"))
    llm_total = _int(token_summary.get("llm_total_tokens"))
    llm_call_count = _int(token_summary.get("llm_call_count"))
    memory_query_count = _int(token_summary.get("memory_query_count"))
    memory_hit_count = _int(token_summary.get("memory_hit_count"))
    memory_injected_count = _int(token_summary.get("memory_injected_count"))
    useful_memory_hit_count = _int(
        token_summary.get("useful_memory_hit_count")
    )
    wrong_memory_hit_count = _int(token_summary.get("wrong_memory_hit_count"))
    unassessed_memory_hit_count = _int(
        token_summary.get("unassessed_memory_hit_count")
    )
    unique_retrieved = _int(token_summary.get("unique_retrieved_memory_tokens"))
    fanout_retrieved = _int(token_summary.get("fanout_retrieved_memory_tokens"))
    shadow_native = _int(token_summary.get("shadow_native_tokens"))
    shadow_candidate = _int(token_summary.get("shadow_candidate_tokens"))
    shadow_savings = shadow_native - shadow_candidate if shadow_native else 0
    shadow_ratio = shadow_savings / shadow_native if shadow_native > 0 else 0.0
    savings = native - runtime if native else _int(token_summary.get("token_savings"))
    ratio = savings / native if native > 0 else 0.0
    return [
        _row("llm_call_count", llm_call_count, "模型客户端调用次数"),
        _row("llm_prompt_tokens", llm_prompt, "LLM 输入 token；网页端可由 provider usage 补充"),
        _row("llm_completion_tokens", llm_completion, "LLM 输出 token；网页端可由 provider usage 补充"),
        _row("llm_total_tokens", llm_total, "LLM 实际调用总 token"),
        _row("actual_native_transport_tokens", native, "真实改写审计覆盖范围内的原生传输成本"),
        _row("agentlite_direct_message_tokens", direct, "AgentLite 在线传输的短消息成本"),
        _row("agentlite_prompt_view_tokens", prompt_view, "下游 Agent 读取 Prompt View 的成本"),
        _row("agentlite_retrieved_memory_tokens", retrieved, "记忆检索读取成本"),
        _row("agentlite_memory_query_count", memory_query_count, "共享记忆查询次数"),
        _row("agentlite_memory_hit_count", memory_hit_count, "共享记忆命中条数"),
        _row(
            "agentlite_memory_injected_count",
            memory_injected_count,
            "实际进入 Prompt View 的记忆条数",
        ),
        _row(
            "agentlite_useful_memory_hit_count",
            useful_memory_hit_count,
            "有下游输出采用证据的有效记忆条数",
        ),
        _row(
            "agentlite_wrong_memory_hit_count",
            wrong_memory_hit_count,
            "被判定为错误或过期的记忆命中条数",
        ),
        _row(
            "agentlite_unassessed_memory_hit_count",
            unassessed_memory_hit_count,
            "已注入但尚无采用或错误判定证据的记忆条数",
        ),
        _row(
            "agentlite_unique_retrieved_memory_tokens",
            unique_retrieved,
            "每次检索视图只计一次的 Token",
        ),
        _row(
            "agentlite_fanout_retrieved_memory_tokens",
            fanout_retrieved,
            "记忆视图向多个接收者展开后的总读取 Token",
        ),
        _row("agentlite_control_llm_tokens", control, "控制模块 LLM 成本"),
        _row("agentlite_retry_tokens", retry, "重试带来的额外成本"),
        _row("actual_agentlite_transport_tokens", runtime, "真实应用或回退后的 AgentLite 传输成本"),
        _row("actual_transport_token_savings", savings, "真实审计范围内的传输成本差额"),
        _row("actual_transport_token_savings_ratio", round(ratio, 6), "真实审计范围内的传输节省比例"),
        _row("shadow_native_transport_tokens", shadow_native, "影子推演对应的原生候选成本"),
        _row("shadow_candidate_transport_tokens", shadow_candidate, "影子推演的压缩候选成本，未实际传输"),
        _row("shadow_potential_token_savings", shadow_savings, "影子方案的理论节省量"),
        _row("shadow_potential_token_savings_ratio", round(shadow_ratio, 6), "影子方案理论节省比例，不代表真实收益"),
    ]


def _row(metric: str, value: int | float, meaning: str) -> dict[str, Any]:
    return {"metric": metric, "value": value, "meaning": meaning}


def _render_csv(report: dict[str, Any]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["session_id", "metric", "value", "meaning"])
    writer.writeheader()
    for row in report["metric_rows"]:
        writer.writerow({"session_id": report["session_id"], **row})
    return buffer.getvalue().rstrip("\r\n")


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# AutoGen 网页端 Session Token 报告",
        "",
        f"- session_id: `{report['session_id']}`",
        f"- status: `{report['status']}`",
        f"- framework: `{report['framework']}`",
        f"- hooks_active: `{report['hooks_active']}`",
        f"- session_dir: `{report['session_dir']}`",
        f"- trace_path: `{report['trace_path']}`",
        "",
        "## Token 指标",
        "",
        "| 指标 | 数值 | 含义 |",
        "|---|---:|---|",
    ]
    for row in report["metric_rows"]:
        lines.append(f"| `{row['metric']}` | {row['value']} | {row['meaning']} |")
    lines.extend(["", "## Trace 事件计数", ""])
    event_counts = report.get("event_counts") or {}
    if event_counts:
        lines.extend(["| 事件 | 次数 |", "|---|---:|"])
        for event_name, count in sorted(event_counts.items()):
            lines.append(f"| `{event_name}` | {count} |")
    else:
        lines.append("暂无 trace 事件计数。")
    lines.extend(["", "## 备注", ""])
    lines.extend(f"- {note}" for note in report.get("notes", []))
    return "\n".join(lines)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
