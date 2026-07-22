from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


EXPECTED_AGENT_IDS = (
    "ScopeCartographer",
    "EvidenceMiner",
    "DesignSynthesizer",
    "IntegritySentinel",
)
FORBIDDEN_FIXED_ROLE_IDS = {"planner", "writer", "reviewer"}


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the v5.13s dynamic-capability AutoGen acceptance run."
    )
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--observed-dir", type=Path, required=True)
    parser.add_argument("--managed-dir", type=Path, required=True)
    parser.add_argument("--managed-data-dir", type=Path, required=True)
    parser.add_argument("--quality-summary", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--report-version", default="v5.13s")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = verify_acceptance(
        native_dir=args.native_dir,
        observed_dir=args.observed_dir,
        managed_dir=args.managed_dir,
        managed_data_dir=args.managed_data_dir,
        quality_summary=args.quality_summary,
        report_version=args.report_version,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0 if payload["summary"]["passed"] else 1


def verify_acceptance(
    *,
    native_dir: Path,
    observed_dir: Path,
    managed_dir: Path,
    managed_data_dir: Path,
    quality_summary: Path,
    report_version: str = "v5.13s",
) -> dict[str, Any]:
    run_dirs = {
        "native": native_dir.expanduser().resolve(),
        "observed": observed_dir.expanduser().resolve(),
        "managed": managed_dir.expanduser().resolve(),
    }
    runs = {
        group: _read_json(path / "sequence_result.json")
        for group, path in run_dirs.items()
    }
    report = _read_json(run_dirs["managed"] / "agentlite_session_report.json")
    quality = _read_json(quality_summary.expanduser().resolve())
    trace_path = _resolve_trace_path(
        managed_dir=run_dirs["managed"],
        managed_data_dir=managed_data_dir.expanduser().resolve(),
        report=report,
    )
    events = _read_jsonl(trace_path)
    profiles = _latest_profiles(events)
    token_summary = dict(report.get("token_summary") or {})
    event_counts = dict(report.get("event_counts") or {})

    checks: list[Check] = []
    _check(
        checks,
        "agentlite_hooks_active",
        bool(report.get("hooks_active")) and report.get("driver_status") == "active",
        f"driver_status={report.get('driver_status')!r}, hooks_active={report.get('hooks_active')!r}",
    )
    expected = set(EXPECTED_AGENT_IDS)
    registered = set(profiles)
    _check(
        checks,
        "arbitrary_agents_profiled",
        expected <= registered,
        f"expected={sorted(expected)}, registered={sorted(registered)}",
    )
    business_profiles = {
        agent_id
        for agent_id, profile in profiles.items()
        if str(profile.get("registry_scope") or "business") == "business"
    }
    _check(
        checks,
        "logical_business_agents_only",
        business_profiles == expected,
        f"expected={sorted(expected)}, business_profiles={sorted(business_profiles)}",
    )
    forbidden = {
        agent_id
        for agent_id in registered
        if agent_id.casefold() in FORBIDDEN_FIXED_ROLE_IDS
    }
    _check(
        checks,
        "no_fixed_business_role_dependency",
        not forbidden,
        f"forbidden_registered={sorted(forbidden)}",
    )
    missing_role_capabilities = [
        agent_id
        for agent_id in EXPECTED_AGENT_IDS
        if not list((profiles.get(agent_id) or {}).get("role_capabilities") or [])
    ]
    _check(
        checks,
        "role_capabilities_inferred",
        not missing_role_capabilities,
        f"missing={missing_role_capabilities}",
    )

    evidence_profile = profiles.get("EvidenceMiner") or {}
    tool_rows = list(evidence_profile.get("tool_capabilities") or [])
    tool_sources = {
        str(row.get("source") or "")
        for row in tool_rows
        if isinstance(row, dict)
    }
    available_tools = {
        str(item) for item in evidence_profile.get("available_tools") or []
    }
    _check(
        checks,
        "real_tool_capability_discovered",
        "local_evidence_lookup" in available_tools
        and "tool:local_evidence_lookup" in tool_sources,
        f"available_tools={sorted(available_tools)}, sources={sorted(tool_sources)}",
    )

    stale_profiles = {
        agent_id: int((profiles.get(agent_id) or {}).get("profile_version") or 0)
        for agent_id in EXPECTED_AGENT_IDS
        if int((profiles.get(agent_id) or {}).get("profile_version") or 0) < 2
    }
    _check(
        checks,
        "runtime_feedback_updated_profiles",
        not stale_profiles,
        f"profile_versions={_profile_versions(profiles)}",
    )
    _check(
        checks,
        "capability_profile_events_recorded",
        _int(token_summary.get("capability_profile_update_count")) >= len(expected)
        and _int(token_summary.get("capability_profile_feedback_count"))
        >= len(expected),
        (
            "updates="
            f"{_int(token_summary.get('capability_profile_update_count'))}, "
            "feedback="
            f"{_int(token_summary.get('capability_profile_feedback_count'))}"
        ),
    )
    profile_updates = _int(token_summary.get("capability_profile_update_count"))
    registered_total = _int(token_summary.get("registered_total_profile_count"))
    expected_update_ceiling = (registered_total or len(registered)) + len(expected)
    _check(
        checks,
        "capability_profile_events_deduplicated",
        profile_updates <= expected_update_ceiling,
        (
            f"updates={profile_updates}, registered_total="
            f"{registered_total or len(registered)}, ceiling={expected_update_ceiling}"
        ),
    )
    _check(
        checks,
        "dynamic_capability_context_views_used",
        _int(token_summary.get("capability_context_view_count")) > 0,
        f"count={_int(token_summary.get('capability_context_view_count'))}",
    )
    memory_source_tokens = _int(token_summary.get("memory_source_view_tokens"))
    memory_selected_tokens = _int(token_summary.get("minimal_role_view_tokens"))
    _check(
        checks,
        "memory_role_views_do_not_expand",
        memory_source_tokens > 0
        and 0 <= memory_selected_tokens <= memory_source_tokens,
        (
            f"source={memory_source_tokens}, selected={memory_selected_tokens}, "
            "fallbacks="
            f"{_int(token_summary.get('memory_no_expansion_fallback_count'))}"
        ),
    )
    task_source_tokens = _int(token_summary.get("current_task_source_tokens"))
    task_selected_tokens = _int(token_summary.get("current_task_role_view_tokens"))
    _check(
        checks,
        "current_task_role_views_do_not_expand",
        task_source_tokens > 0
        and 0 <= task_selected_tokens <= task_source_tokens,
        (
            f"source={task_source_tokens}, selected={task_selected_tokens}, "
            "fallbacks="
            f"{_int(token_summary.get('current_task_no_expansion_fallback_count'))}"
        ),
    )
    action_counts = token_summary.get("capability_action_counts")
    action_counts = action_counts if isinstance(action_counts, dict) else {}
    _check(
        checks,
        "semantic_actions_observed",
        len([key for key, value in action_counts.items() if _int(value) > 0]) >= 2,
        f"actions={json.dumps(action_counts, ensure_ascii=False, sort_keys=True)}",
    )
    actual_rewrites = _int(token_summary.get("rewrite_applied_event_count"))
    _check(
        checks,
        "managed_messages_actually_rewritten",
        actual_rewrites > 0,
        f"rewrite_applied_event_count={actual_rewrites}",
    )
    continuity_required = _int(token_summary.get("continuity_required_event_count"))
    continuity_injected = _int(
        token_summary.get("continuity_memory_injection_count")
    )
    _check(
        checks,
        "continuous_task_memory_injected",
        continuity_required > 0 and continuity_injected > 0,
        (
            f"continuity_required={continuity_required}, "
            f"continuity_memory_injection={continuity_injected}"
        ),
    )

    for group, run in runs.items():
        summary = dict(run.get("summary") or {})
        tasks = list(run.get("tasks") or [])
        tool_calls = sum(len(task.get("tool_calls") or []) for task in tasks)
        _check(
            checks,
            f"{group}_sequence_complete",
            _int(summary.get("task_count")) == 3
            and _int(summary.get("valid_delivery_count")) == 3
            and bool(summary.get("same_team_instance")),
            (
                f"tasks={summary.get('task_count')}, "
                f"valid={summary.get('valid_delivery_count')}, "
                f"same_team_instance={summary.get('same_team_instance')}"
            ),
        )
        _check(
            checks,
            f"{group}_real_tool_executed",
            tool_calls >= 3,
            f"tool_calls={tool_calls}",
        )

    by_group = dict(quality.get("by_group") or {})
    native_quality = dict(by_group.get("native") or {})
    managed_quality = dict(by_group.get("managed") or {})
    native_mean = float(native_quality.get("mean_score") or 0.0)
    managed_mean = float(managed_quality.get("mean_score") or 0.0)
    native_complete = _int(native_quality.get("delivery_complete_count"))
    managed_complete = _int(managed_quality.get("delivery_complete_count"))
    _check(
        checks,
        "managed_quality_not_meaningfully_lower",
        bool(native_quality)
        and bool(managed_quality)
        and managed_mean + 0.5 >= native_mean
        and managed_complete >= native_complete,
        (
            f"native_mean={native_mean:.3f}, managed_mean={managed_mean:.3f}, "
            f"native_complete={native_complete}, managed_complete={managed_complete}"
        ),
    )

    provider_tokens = {
        group: _int(
            ((run.get("summary") or {}).get("llm_usage") or {}).get(
                "llm_total_tokens"
            )
        )
        for group, run in runs.items()
    }
    native_tokens = provider_tokens["native"]
    managed_tokens = provider_tokens["managed"]
    token_delta = managed_tokens - native_tokens
    token_change_ratio = token_delta / native_tokens if native_tokens else None
    passed = all(item.passed for item in checks)
    return {
        "schema_version": f"agentlite.{report_version}-acceptance.v1",
        "report_version": report_version,
        "summary": {
            "passed": passed,
            "check_count": len(checks),
            "passed_check_count": sum(item.passed for item in checks),
            "failed_check_count": sum(not item.passed for item in checks),
            "trace_path": str(trace_path),
        },
        "checks": [item.to_dict() for item in checks],
        "profiles": {
            agent_id: profiles[agent_id]
            for agent_id in EXPECTED_AGENT_IDS
            if agent_id in profiles
        },
        "runtime_metrics": {
            "event_counts": event_counts,
            "token_summary": token_summary,
            "provider_llm_total_tokens": provider_tokens,
            "managed_vs_native_provider_token_delta": token_delta,
            "managed_vs_native_provider_token_change_ratio": token_change_ratio,
            "note": (
                "Provider Token 变化用于结果分析，不作为接管成功的单一门禁；"
                "必须结合质量、连续性和端到端协作成本解释。"
            ),
        },
        "quality": quality,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    metrics = payload["runtime_metrics"]
    token_summary = metrics["token_summary"]
    quality = payload.get("quality", {}).get("by_group", {})
    lines = [
        f"# {payload.get('report_version', 'v5.13s')} 动态能力画像 AutoGen 验收报告",
        "",
        f"- 总体结果：{'通过' if summary['passed'] else '未通过'}",
        f"- 检查项：{summary['passed_check_count']}/{summary['check_count']} 通过",
        f"- Trace：`{summary['trace_path']}`",
        "",
        "## 硬性检查",
        "",
        "| 检查项 | 结果 | 证据 |",
        "|---|---:|---|",
    ]
    for item in payload["checks"]:
        detail = str(item["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{item['name']}` | {'通过' if item['passed'] else '失败'} | {detail} |"
        )
    lines.extend(
        [
            "",
            "## 关键运行指标",
            "",
            f"- 能力画像更新：{_int(token_summary.get('capability_profile_update_count'))}",
            f"- 执行反馈回写：{_int(token_summary.get('capability_profile_feedback_count'))}",
            f"- 动态能力上下文视图：{_int(token_summary.get('capability_context_view_count'))}",
            f"- 真实消息改写：{_int(token_summary.get('rewrite_applied_event_count'))}",
            f"- 连续任务必要记忆注入：{_int(token_summary.get('continuity_memory_injection_count'))}",
            "",
            "## Provider Token 与质量",
            "",
            "| 组别 | LLM 总 Token | 盲评均分 | 完整交付数 |",
            "|---|---:|---:|---:|",
        ]
    )
    for group in ("native", "observed", "managed"):
        group_quality = quality.get(group, {})
        lines.append(
            f"| {group} | {metrics['provider_llm_total_tokens'].get(group, 0)} | "
            f"{float(group_quality.get('mean_score') or 0.0):.3f} | "
            f"{_int(group_quality.get('delivery_complete_count'))} |"
        )
    ratio = metrics.get("managed_vs_native_provider_token_change_ratio")
    ratio_text = "不可计算" if ratio is None else f"{float(ratio):.2%}"
    lines.extend(
        [
            "",
            f"接管组相对原生组 Provider Token 变化：{ratio_text}。",
            "",
            "> Token 变化不是单独的通过条件。最终结论必须同时考虑真实通信改写、记忆读取、控制与重试成本，以及匿名质量评分。",
            "",
        ]
    )
    return "\n".join(lines)


def _resolve_trace_path(
    *, managed_dir: Path, managed_data_dir: Path, report: dict[str, Any]
) -> Path:
    reported_text = str(report.get("trace_path") or "").strip()
    if reported_text:
        reported = Path(reported_text)
        if reported.is_file():
            return reported.resolve()
    candidates = [
        *managed_dir.glob("agentlite_data/sessions/*/autogen_driver/trace.jsonl"),
        *managed_data_dir.glob("sessions/*/autogen_driver/trace.jsonl"),
    ]
    files = sorted({path.resolve() for path in candidates if path.is_file()})
    if len(files) != 1:
        raise ValueError(
            f"Expected exactly one managed trace, found {len(files)}: {files}"
        )
    return files[0]


def _latest_profiles(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event_type") not in {
            "capability_profile_updated",
            "capability_profile_feedback",
        }:
            continue
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        agent_id = str(payload.get("agent_id") or "")
        profile = payload.get("profile")
        if agent_id and isinstance(profile, dict):
            profiles[agent_id] = profile
    return profiles


def _profile_versions(profiles: dict[str, dict[str, Any]]) -> dict[str, int]:
    return {
        agent_id: _int((profiles.get(agent_id) or {}).get("profile_version"))
        for agent_id in EXPECTED_AGENT_IDS
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _check(checks: list[Check], name: str, passed: bool, detail: str) -> None:
    checks.append(Check(name=name, passed=bool(passed), detail=detail))


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
