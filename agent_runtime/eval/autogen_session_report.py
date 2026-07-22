from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from agent_runtime.eval.experiment_archive import verify_bound_experiment
from web_monitor.parser import (
    build_session_snapshot,
    list_framework_runs,
    list_sessions,
)


ReportFormat = Literal["json", "csv", "markdown"]


@dataclass(frozen=True)
class SessionReportRequest:
    data_dir: Path | None = None
    session_id: str | None = None
    output: Path | None = None
    report_format: ReportFormat = "markdown"
    provider_usage: Path | None = None
    experiment_dir: Path | None = None
    exclusive_output: bool = False


@dataclass(frozen=True)
class RunReportRequest:
    data_dir: Path
    session_id: str | None = None
    run_id: str | None = None
    output: Path | None = None
    report_format: ReportFormat = "markdown"
    exclusive_output: bool = False


def build_autogen_session_report(request: SessionReportRequest) -> dict[str, Any]:
    bound: dict[str, Any] | None = None
    provider_usage_path = request.provider_usage
    requested_session_id = request.session_id
    if request.experiment_dir is not None:
        if request.provider_usage is not None:
            raise ValueError(
                "--experiment-dir already binds Provider usage; do not also provide --provider-usage"
            )
        bound = verify_bound_experiment(request.experiment_dir)
        data_dir = bound["data_dir"]
        requested_session_id = bound["session_id"]
        provider_usage_path = bound["provider_usage_path"]
    elif request.data_dir is not None:
        data_dir = request.data_dir.expanduser().resolve()
    else:
        raise ValueError("data_dir or experiment_dir is required")
    session_dir, resolved_session_id = resolve_session_dir(
        data_dir=data_dir,
        session_id=requested_session_id,
    )
    snapshot = build_session_snapshot(session_dir, session_id=resolved_session_id)
    token_summary = dict(snapshot.get("token_summary", {}))
    provider_usage = (
        _load_provider_usage(provider_usage_path)
        if provider_usage_path is not None
        else None
    )
    llm_usage_source = (
        "autogen_model_client_hook"
        if _int(token_summary.get("llm_total_tokens")) > 0
        else "unavailable"
    )
    if provider_usage is not None:
        token_summary["external_provider_usage"] = provider_usage
        if _int(token_summary.get("llm_total_tokens")) <= 0:
            token_summary["llm_call_count"] = provider_usage["llm_call_count"]
            token_summary["llm_prompt_tokens"] = provider_usage["llm_prompt_tokens"]
            token_summary["llm_completion_tokens"] = provider_usage[
                "llm_completion_tokens"
            ]
            token_summary["llm_total_tokens"] = provider_usage["llm_total_tokens"]
            llm_usage_source = "external_provider_usage_file"
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
        "llm_usage_source": llm_usage_source,
        "experiment_dir": str(bound["experiment_dir"]) if bound else "",
        "experiment_run_id": str(bound["run_id"]) if bound else "",
        "experiment_binding_verified": bool(bound),
        "experiment_binding_checks": dict(bound["checks"]) if bound else {},
        "experiment_session_source": str(bound["session_source"]) if bound else "",
        "provider_usage_path": (
            str(provider_usage_path.expanduser().resolve())
            if provider_usage_path is not None
            else ""
        ),
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
            "llm_* 优先来自 AutoGen 模型客户端 usage hook；钩子无数据且提供 --provider-usage 时，改用外部 Provider 用量文件。",
            "记忆命中、注入与有效采用是三个不同阶段；未取得下游引用证据的命中保持 unassessed。",
            "unique_retrieved_memory_tokens 统计检索候选，fanout_retrieved_memory_tokens 只统计实际注入；成本门禁拒绝候选时，后者可以更小。",
            "rewrite_audit_event_count 是全部改写决策；rewrite_applied_event_count 是真正修改消息的次数；rewrite_fallback_event_count 是保留原生消息的次数。",
            "memory_candidate_deduplicated_* 统计规则在候选阶段移除的上下文重复记忆，不等同于已注入或有效记忆。",
        ],
    }


def build_autogen_run_report(request: RunReportRequest) -> dict[str, Any]:
    data_dir = request.data_dir.expanduser().resolve()
    session_dir, resolved_session_id = resolve_session_dir(
        data_dir=data_dir,
        session_id=request.session_id,
    )
    run_id = _resolve_framework_run_id(
        data_dir=data_dir,
        session_id=resolved_session_id,
        run_id=request.run_id,
    )
    snapshot = build_session_snapshot(
        session_dir,
        session_id=resolved_session_id,
        framework_run_id=run_id,
    )
    token_summary = dict(snapshot.get("token_summary", {}))
    native = _int(token_summary.get("native_baseline_tokens"))
    runtime = _int(token_summary.get("end_to_end_collaboration_tokens"))
    savings = native - runtime if native else 0
    ratio = savings / native if native > 0 else 0.0
    token_summary.update(
        {
            "actual_native_transport_tokens": native,
            "actual_agentlite_transport_tokens": runtime,
            "actual_transport_token_savings": savings,
            "actual_transport_token_savings_ratio": round(ratio, 6),
            "native_collaboration_tokens": native,
            "agentlite_runtime_tokens": runtime,
            "agentlite_token_savings": savings,
            "agentlite_token_savings_ratio": round(ratio, 6),
        }
    )
    summary = snapshot.get("summary", {})
    return {
        "session_id": resolved_session_id,
        "framework_run_id": run_id,
        "framework_run_source": snapshot.get("framework_run_source") or "",
        "studio_run_id": snapshot.get("studio_run_id") or "",
        "studio_session_id": snapshot.get("studio_session_id") or "",
        "studio_team_id": snapshot.get("studio_team_id") or "",
        "studio_session_name": snapshot.get("studio_session_name") or "",
        "session_dir": str(session_dir),
        "trace_path": str(summary.get("trace_path") or ""),
        "status": snapshot.get("status") or "unknown",
        "framework": summary.get("framework") or "autogen",
        "driver_status": summary.get("driver_status") or "",
        "hooks_active": bool(summary.get("hooks_active")),
        "event_counts": summary.get("event_counts") or {},
        "llm_usage_source": (
            "autogen_model_client_hook"
            if _int(token_summary.get("llm_total_tokens")) > 0
            else "unavailable"
        ),
        "token_summary": token_summary,
        "metric_rows": _metric_rows(token_summary),
        "notes": [
            "本报告只汇总 framework_run_id 对应的 Trace 事件，不包含同一 Studio 进程中的其他网页 Run。",
            "Studio 原生 run_id 来自其 RunContext；session_id 与 team_id 来自只读数据库关联，未修改 Studio 数据。",
            "若模型客户端没有返回 usage，本 Run 的 LLM Token 保持 unavailable，不用进程级总量填充。",
        ],
    }


def render_autogen_run_report(
    report: dict[str, Any],
    report_format: ReportFormat,
) -> str:
    if report_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    if report_format == "csv":
        buffer = StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=["session_id", "framework_run_id", "metric", "value", "meaning"],
        )
        writer.writeheader()
        for row in report["metric_rows"]:
            writer.writerow(
                {
                    "session_id": report["session_id"],
                    "framework_run_id": report["framework_run_id"],
                    **row,
                }
            )
        return buffer.getvalue().rstrip("\r\n")
    if report_format != "markdown":
        raise ValueError(f"Unsupported report format: {report_format}")
    lines = [
        "# AutoGen Run Token 报告",
        "",
        f"- AgentLite session_id: `{report['session_id']}`",
        f"- framework_run_id: `{report['framework_run_id']}`",
        f"- binding_source: `{report['framework_run_source']}`",
        f"- Studio run_id: `{report['studio_run_id']}`",
        f"- Studio session_id: `{report['studio_session_id']}`",
        f"- Studio team_id: `{report['studio_team_id']}`",
        f"- status: `{report['status']}`",
        f"- llm_usage_source: `{report['llm_usage_source']}`",
        "",
        "## Token 指标",
        "",
        "| 指标 | 数值 | 含义 |",
        "|---|---:|---|",
    ]
    for row in report["metric_rows"]:
        lines.append(f"| `{row['metric']}` | {row['value']} | {row['meaning']} |")
    lines.extend(["", "## Trace 事件计数", "", "| 事件 | 次数 |", "|---|---:|"])
    for event_name, count in sorted((report.get("event_counts") or {}).items()):
        lines.append(f"| `{event_name}` | {count} |")
    lines.extend(["", "## 备注", ""])
    lines.extend(f"- {note}" for note in report.get("notes", []))
    return "\n".join(lines)


def write_autogen_run_report(request: RunReportRequest) -> dict[str, Any]:
    report = build_autogen_run_report(request)
    text = render_autogen_run_report(report, request.report_format)
    if request.output:
        output = request.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        mode = "x" if request.exclusive_output else "w"
        with output.open(mode, encoding="utf-8") as handle:
            handle.write(text + "\n")
        report["output_path"] = str(output)
    else:
        print(text)
    return report


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


def _resolve_framework_run_id(
    *,
    data_dir: Path,
    session_id: str,
    run_id: str | None,
) -> str:
    requested = str(run_id or "latest").strip()
    runs = [
        item
        for item in list_framework_runs(data_dir)
        if str(item.get("session_id") or "") == session_id
    ]
    if not runs:
        raise FileNotFoundError(
            f"No framework Runs found for AgentLite session {session_id!r}"
        )
    if requested in {"", "latest"}:
        return str(runs[0]["framework_run_id"])
    if any(str(item.get("framework_run_id") or "") == requested for item in runs):
        return requested
    raise FileNotFoundError(
        f"Framework run {requested!r} not found in AgentLite session {session_id!r}"
    )


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
        mode = "x" if request.exclusive_output else "w"
        with output.open(mode, encoding="utf-8") as handle:
            handle.write(text + "\n")
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
    rewrite_audit = _int(token_summary.get("rewrite_audit_event_count"))
    rewrite_costed = _int(token_summary.get("rewrite_costed_event_count"))
    rewrite_applied = _int(token_summary.get("rewrite_applied_event_count"))
    rewrite_fallback = _int(token_summary.get("rewrite_fallback_event_count"))
    rewrite_cost_gate_fallback = _int(
        token_summary.get("rewrite_cost_gate_fallback_count")
    )
    rewrite_contract_fallback = _int(
        token_summary.get("rewrite_contract_fallback_count")
    )
    continuity_required = _int(
        token_summary.get("continuity_required_event_count")
    )
    continuity_cost_override = _int(
        token_summary.get("continuity_cost_override_count")
    )
    continuity_memory_injection = _int(
        token_summary.get("continuity_memory_injection_count")
    )
    memory_source_view_tokens = _int(
        token_summary.get("memory_source_view_tokens")
    )
    minimal_role_view_tokens = _int(
        token_summary.get("minimal_role_view_tokens")
    )
    role_view_saved_tokens = _int(token_summary.get("role_view_saved_tokens"))
    role_view_reduction_ratio = float(
        token_summary.get("role_view_reduction_ratio", 0.0) or 0.0
    )
    memory_field_fetch_count = _int(
        token_summary.get("memory_field_fetch_count")
    )
    memory_field_fetch_tokens = _int(
        token_summary.get("memory_field_fetch_tokens")
    )
    receiver_role_view_hydration_count = _int(
        token_summary.get("receiver_role_view_hydration_count")
    )
    current_task_source_tokens = _int(
        token_summary.get("current_task_source_tokens")
    )
    current_task_role_view_tokens = _int(
        token_summary.get("current_task_role_view_tokens")
    )
    current_task_role_view_saved_tokens = _int(
        token_summary.get("current_task_role_view_saved_tokens")
    )
    current_task_role_view_reduction_ratio = float(
        token_summary.get("current_task_role_view_reduction_ratio", 0.0) or 0.0
    )
    final_delivery_assessed_count = _int(
        token_summary.get("final_delivery_assessed_count")
    )
    final_delivery_valid_count = _int(
        token_summary.get("final_delivery_valid_count")
    )
    final_delivery_invalid_count = _int(
        token_summary.get("final_delivery_invalid_count")
    )
    final_delivery_valid_rate = float(
        token_summary.get("final_delivery_valid_rate", 0.0) or 0.0
    )
    capability_profile_update_count = _int(
        token_summary.get("capability_profile_update_count")
    )
    capability_profile_feedback_count = _int(
        token_summary.get("capability_profile_feedback_count")
    )
    registered_capability_profile_count = _int(
        token_summary.get("registered_capability_profile_count")
    )
    capability_context_view_count = _int(
        token_summary.get("capability_context_view_count")
    )
    capability_action_counts = token_summary.get("capability_action_counts", {})
    memory_deduplicated = _int(
        token_summary.get("memory_candidate_deduplicated_count")
    )
    memory_deduplicated_tokens = _int(
        token_summary.get("memory_candidate_deduplicated_tokens")
    )
    shadow_savings = shadow_native - shadow_candidate if shadow_native else 0
    shadow_ratio = shadow_savings / shadow_native if shadow_native > 0 else 0.0
    savings = native - runtime if native else _int(token_summary.get("token_savings"))
    ratio = savings / native if native > 0 else 0.0
    return [
        _row("llm_call_count", llm_call_count, "模型客户端调用次数"),
        _row("llm_prompt_tokens", llm_prompt, "LLM 输入 token；网页端可由 provider usage 补充"),
        _row("llm_completion_tokens", llm_completion, "LLM 输出 token；网页端可由 provider usage 补充"),
        _row("llm_total_tokens", llm_total, "LLM 实际调用总 token"),
        _row(
            "registered_capability_profile_count",
            registered_capability_profile_count,
            "本次运行实际注册的 Agent 能力画像数量",
        ),
        _row(
            "capability_profile_update_count",
            capability_profile_update_count,
            "角色描述或工具变化触发能力画像同步的次数",
        ),
        _row(
            "capability_profile_feedback_count",
            capability_profile_feedback_count,
            "执行结果回写成功率、可靠性、成本与时延的次数",
        ),
        _row(
            "capability_context_view_count",
            capability_context_view_count,
            "按接收者能力与当前动作生成最小上下文视图的次数",
        ),
        _row(
            "capability_action_counts",
            json.dumps(capability_action_counts, ensure_ascii=False, sort_keys=True),
            "运行中识别到的语义动作分布",
        ),
        _row("rewrite_audit_event_count", rewrite_audit, "进入真实改写审计的全部决策次数"),
        _row("rewrite_costed_event_count", rewrite_costed, "具有可比较原生/运行时 Token 的改写决策次数"),
        _row("rewrite_applied_event_count", rewrite_applied, "真正修改了 AutoGen 消息的次数"),
        _row("rewrite_fallback_event_count", rewrite_fallback, "未修改消息并保留原生内容的次数"),
        _row("rewrite_cost_gate_fallback_count", rewrite_cost_gate_fallback, "因候选不比原生更省而回退的次数"),
        _row("rewrite_contract_fallback_count", rewrite_contract_fallback, "因消息结构不受支持而回退的次数"),
        _row(
            "continuity_required_event_count",
            continuity_required,
            "检测到当前任务依赖既有上下文的改写决策次数",
        ),
        _row(
            "continuity_cost_override_count",
            continuity_cost_override,
            "为保证任务连续性而覆盖纯 Token 门禁的次数",
        ),
        _row(
            "continuity_memory_injection_count",
            continuity_memory_injection,
            "连续任务中实际注入已准入记忆的改写次数",
        ),
        _row(
            "memory_source_view_tokens",
            memory_source_view_tokens,
            "角色裁剪前的已准入 MemoryView Token",
        ),
        _row(
            "minimal_role_view_tokens",
            minimal_role_view_tokens,
            "实际为各接收者生成的最小充分角色视图 Token",
        ),
        _row(
            "role_view_saved_tokens",
            role_view_saved_tokens,
            "角色视图相对公共 MemoryView 减少的 Token",
        ),
        _row(
            "role_view_reduction_ratio",
            role_view_reduction_ratio,
            "最小角色视图相对原 MemoryView 的缩减比例",
        ),
        _row(
            "memory_field_fetch_count",
            memory_field_fetch_count,
            "最小视图缺少用户明确要求字段时的片段补取次数",
        ),
        _row(
            "memory_field_fetch_tokens",
            memory_field_fetch_tokens,
            "字段补取读取的来源片段 Token",
        ),
        _row(
            "receiver_role_view_hydration_count",
            receiver_role_view_hydration_count,
            "Team 分区视图在具体 Agent 接收前完成隔离注入的次数",
        ),
        _row(
            "current_task_source_tokens",
            current_task_source_tokens,
            "按接收者广播原始当前任务所需的 Token",
        ),
        _row(
            "current_task_role_view_tokens",
            current_task_role_view_tokens,
            "实际生成的当前任务最小角色视图 Token",
        ),
        _row(
            "current_task_role_view_saved_tokens",
            current_task_role_view_saved_tokens,
            "当前任务角色视图相对完整广播减少的 Token",
        ),
        _row(
            "current_task_role_view_reduction_ratio",
            current_task_role_view_reduction_ratio,
            "当前任务按角色裁剪后的缩减比例",
        ),
        _row(
            "final_delivery_assessed_count",
            final_delivery_assessed_count,
            "经过最终交付规则校验的 Team 输出数",
        ),
        _row(
            "final_delivery_valid_count",
            final_delivery_valid_count,
            "通过最终交付规则校验的 Team 输出数",
        ),
        _row(
            "final_delivery_invalid_count",
            final_delivery_invalid_count,
            "未通过最终交付规则校验的 Team 输出数",
        ),
        _row(
            "final_delivery_valid_rate",
            final_delivery_valid_rate,
            "最终交付规则校验通过率；外部质量评分仍需独立评测",
        ),
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
            "检索阶段生成的记忆视图 Token；每次查询只计一次，包含未通过注入成本门禁的候选",
        ),
        _row(
            "agentlite_fanout_retrieved_memory_tokens",
            fanout_retrieved,
            "实际改写后进入下游 Prompt View 的记忆读取 Token；可能因成本门禁少于检索候选",
        ),
        _row(
            "agentlite_memory_candidate_deduplicated_count",
            memory_deduplicated,
            "候选构建阶段因事实已被当前上下文覆盖而移除的记忆份数",
        ),
        _row(
            "agentlite_memory_candidate_deduplicated_tokens",
            memory_deduplicated_tokens,
            "被内容去重规则从候选 Prompt View 中移除的记忆 Token",
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


def _load_provider_usage(path: Path) -> dict[str, int]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Provider usage file not found: {resolved}")
    text = resolved.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Provider usage file is empty: {resolved}")

    if resolved.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        prompt = sum(
            _int(row.get("prompt_tokens") or row.get("llm_prompt_tokens"))
            for row in rows
        )
        completion = sum(
            _int(
                row.get("completion_tokens")
                or row.get("llm_completion_tokens")
            )
            for row in rows
        )
        total = sum(
            _int(row.get("total_tokens") or row.get("llm_total_tokens"))
            for row in rows
        )
        if total <= 0:
            total = prompt + completion
        return {
            "llm_call_count": len(rows),
            "llm_prompt_tokens": prompt,
            "llm_completion_tokens": completion,
            "llm_total_tokens": total,
        }

    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Provider usage JSON must contain an object")
    prompt = _int(payload.get("llm_prompt_tokens") or payload.get("prompt_tokens"))
    completion = _int(
        payload.get("llm_completion_tokens") or payload.get("completion_tokens")
    )
    total = _int(payload.get("llm_total_tokens") or payload.get("total_tokens"))
    if total <= 0:
        total = prompt + completion
    return {
        "llm_call_count": _int(
            payload.get("calls") or payload.get("llm_call_count")
        ),
        "llm_prompt_tokens": prompt,
        "llm_completion_tokens": completion,
        "llm_total_tokens": total,
    }


def _render_csv(report: dict[str, Any]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["session_id", "metric", "value", "meaning"])
    writer.writeheader()
    for row in report["metric_rows"]:
        writer.writerow({"session_id": report["session_id"], **row})
    return buffer.getvalue().rstrip("\r\n")


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# AutoGen Session Token 报告",
        "",
        f"- session_id: `{report['session_id']}`",
        f"- status: `{report['status']}`",
        f"- framework: `{report['framework']}`",
        f"- hooks_active: `{report['hooks_active']}`",
        f"- llm_usage_source: `{report['llm_usage_source']}`",
        f"- experiment_run_id: `{report['experiment_run_id']}`",
        f"- experiment_binding_verified: `{report['experiment_binding_verified']}`",
        f"- experiment_session_source: `{report['experiment_session_source']}`",
        f"- experiment_dir: `{report['experiment_dir']}`",
        f"- provider_usage_path: `{report['provider_usage_path']}`",
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
