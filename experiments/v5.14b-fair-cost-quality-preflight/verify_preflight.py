from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


GROUPS = ("native", "observed", "managed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the preregistered v5.14b cost-quality preflight."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(
    *,
    preregistration: dict[str, Any],
    evidence_by_scenario: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scenario_specs = list(preregistration.get("scenarios") or [])
    expected_ids = [str(item["scenario_id"]) for item in scenario_specs]
    expected_tasks = int(preregistration["tasks_per_scenario"])
    thresholds = dict(preregistration["thresholds"])

    rows: list[dict[str, Any]] = []
    evidence_checks: list[dict[str, Any]] = [
        _check(
            "preregistration_frozen_before_provider_run",
            bool(preregistration.get("frozen_before_provider_run")),
            f"schema={preregistration.get('schema_version', '')}",
            category="evidence",
        ),
        _check(
            "all_preregistered_scenarios_present",
            set(evidence_by_scenario) == set(expected_ids),
            (
                f"expected={sorted(expected_ids)}, "
                f"actual={sorted(evidence_by_scenario)}"
            ),
            category="evidence",
        ),
    ]

    for scenario_id in expected_ids:
        evidence = evidence_by_scenario.get(scenario_id, {})
        comparison = dict(evidence.get("comparison") or {})
        comparison_summary = dict(comparison.get("summary") or {})
        run_parameters = dict(
            comparison_summary.get("run_parameters") or {}
        )
        quality = dict(evidence.get("quality") or {})
        quality_groups = dict(quality.get("by_group") or {})
        archive_evidence = dict(
            comparison_summary.get("archive_evidence") or {}
        )
        comparison_groups = dict(comparison_summary.get("groups") or {})
        observed_report = dict(evidence.get("observed_report") or {})
        managed_report = dict(evidence.get("managed_report") or {})
        observed_tokens = dict(observed_report.get("token_summary") or {})
        managed_tokens = dict(managed_report.get("token_summary") or {})
        managed_state = dict(managed_report.get("state_summary") or {})
        managed_state_types = dict(
            managed_state.get("state_type_counts") or {}
        )
        managed_payload_kinds = dict(
            managed_state.get("payload_kind_counts") or {}
        )

        task_counts = {
            group: _int(comparison_groups.get(group, {}).get("task_count"))
            for group in GROUPS
        }
        provider = {
            group: {
                "calls": _int(
                    comparison_groups.get(group, {}).get("llm_call_count")
                ),
                "prompt_tokens": _int(
                    comparison_groups.get(group, {}).get("llm_prompt_tokens")
                ),
                "completion_tokens": _int(
                    comparison_groups.get(group, {}).get(
                        "llm_completion_tokens"
                    )
                ),
                "total_tokens": _int(
                    comparison_groups.get(group, {}).get("llm_total_tokens")
                ),
                "wall_time_ms": _int(
                    comparison_groups.get(group, {}).get("wall_time_ms")
                ),
                "strict_delivery_count": _int(
                    comparison_groups.get(group, {}).get(
                        "valid_delivery_count"
                    )
                ),
            }
            for group in GROUPS
        }
        quality_summary = {
            group: {
                "task_count": _int(
                    quality_groups.get(group, {}).get("task_count")
                ),
                "mean_score": _float(
                    quality_groups.get(group, {}).get("mean_score")
                ),
                "delivery_complete_count": _int(
                    quality_groups.get(group, {}).get(
                        "delivery_complete_count"
                    )
                ),
                "blocking_finding_count": _int(
                    quality_groups.get(group, {}).get(
                        "blocking_finding_count"
                    )
                ),
            }
            for group in GROUPS
        }
        rows.append(
            {
                "scenario_id": scenario_id,
                "run_parameters": run_parameters,
                "task_counts": task_counts,
                "provider": provider,
                "quality": quality_summary,
                "transport": {
                    "native_baseline_tokens": _int(
                        managed_tokens.get("native_baseline_tokens")
                    ),
                    "end_to_end_collaboration_tokens": _int(
                        managed_tokens.get(
                            "end_to_end_collaboration_tokens"
                        )
                    ),
                    "direct_message_tokens": _int(
                        managed_tokens.get("direct_message_tokens")
                    ),
                    "prompt_view_tokens": _int(
                        managed_tokens.get("prompt_view_tokens")
                    ),
                    "retrieved_memory_tokens": _int(
                        managed_tokens.get("retrieved_memory_tokens")
                    ),
                    "control_llm_tokens": _int(
                        managed_tokens.get("control_llm_tokens")
                    ),
                    "retry_tokens": _int(
                        managed_tokens.get("retry_tokens")
                    ),
                },
                "memory": {
                    "query_count": _int(
                        managed_tokens.get("memory_query_count")
                    ),
                    "hit_count": _int(
                        managed_tokens.get("memory_hit_count")
                    ),
                    "injected_count": _int(
                        managed_tokens.get("memory_injected_count")
                    ),
                    "useful_hit_count": _int(
                        managed_tokens.get("useful_memory_hit_count")
                    ),
                    "wrong_hit_count": _int(
                        managed_tokens.get("wrong_memory_hit_count")
                    ),
                    "unassessed_hit_count": _int(
                        managed_tokens.get(
                            "unassessed_memory_hit_count"
                        )
                    ),
                },
                "state": {
                    "state_count": _int(
                        managed_state.get("state_count")
                    ),
                    "state_type_counts": {
                        str(key): _int(value)
                        for key, value in managed_state_types.items()
                    },
                    "payload_kind_counts": {
                        str(key): _int(value)
                        for key, value in managed_payload_kinds.items()
                    },
                    "structured_non_text_count": _int(
                        managed_state.get("structured_non_text_count")
                    ),
                    "contains_embedding_refs_count": _int(
                        managed_state.get(
                            "contains_embedding_refs_count"
                        )
                    ),
                    "total_size_bytes": _int(
                        managed_state.get("total_size_bytes")
                    ),
                },
                "rewrite": {
                    "observed_applied_count": _first_int(
                        observed_tokens,
                        "rewrite_applied_event_count",
                        "actual_rewrite_event_count",
                    ),
                    "managed_applied_count": _first_int(
                        managed_tokens,
                        "rewrite_applied_event_count",
                        "actual_rewrite_event_count",
                    ),
                    "managed_error_fallback_count": _int(
                        managed_tokens.get("rewrite_error_fallback_count")
                    ),
                },
            }
        )

        evidence_checks.extend(
            [
                _check(
                    f"{scenario_id}:paired_task_matrix_complete",
                    all(value == expected_tasks for value in task_counts.values()),
                    f"tasks={task_counts}",
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:preregistered_run_parameters_match",
                    _float(run_parameters.get("temperature"))
                    == _float(preregistration.get("temperature"))
                    and _int(run_parameters.get("max_turns"))
                    == _int(preregistration.get("max_turns"))
                    and str(run_parameters.get("provider_model") or "")
                    == str(
                        preregistration.get("provider", {}).get("model")
                        or ""
                    )
                    and str(run_parameters.get("provider_base_url") or "")
                    == str(
                        preregistration.get("provider", {}).get("base_url")
                        or ""
                    ),
                    f"actual={run_parameters}",
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:immutable_group_bindings_verified",
                    all(
                        archive_evidence.get(group, {}).get("status")
                        == "verified"
                        for group in GROUPS
                    ),
                    (
                        "statuses="
                        + str(
                            {
                                group: archive_evidence.get(group, {}).get(
                                    "status"
                                )
                                for group in GROUPS
                            }
                        )
                    ),
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:provider_usage_complete",
                    all(
                        provider[group]["calls"] > 0
                        and provider[group]["total_tokens"] > 0
                        for group in GROUPS
                    ),
                    (
                        "calls="
                        + str(
                            {
                                group: provider[group]["calls"]
                                for group in GROUPS
                            }
                        )
                    ),
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:double_blind_quality_complete",
                    bool(quality.get("technical_review_applied"))
                    and all(
                        quality_summary[group]["task_count"] == expected_tasks
                        for group in GROUPS
                    ),
                    (
                        f"technical={quality.get('technical_review_applied')}, "
                        f"tasks="
                        f"{ {group: quality_summary[group]['task_count'] for group in GROUPS} }"
                    ),
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:agentlite_reports_bound_and_active",
                    bool(observed_report.get("experiment_binding_verified"))
                    and bool(managed_report.get("experiment_binding_verified"))
                    and bool(observed_report.get("hooks_active"))
                    and bool(managed_report.get("hooks_active")),
                    (
                        f"observed_bound="
                        f"{observed_report.get('experiment_binding_verified')}, "
                        f"managed_bound="
                        f"{managed_report.get('experiment_binding_verified')}, "
                        f"observed_hooks={observed_report.get('hooks_active')}, "
                        f"managed_hooks={managed_report.get('hooks_active')}"
                    ),
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:observed_mode_is_non_modifying",
                    rows[-1]["rewrite"]["observed_applied_count"] == 0,
                    (
                        f"applied="
                        f"{rows[-1]['rewrite']['observed_applied_count']}"
                    ),
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:managed_rewrite_and_memory_observed",
                    rows[-1]["rewrite"]["managed_applied_count"] > 0
                    and rows[-1]["memory"]["query_count"] > 0
                    and rows[-1]["memory"]["injected_count"] > 0,
                    (
                        f"rewrite={rows[-1]['rewrite']['managed_applied_count']}, "
                        f"queries={rows[-1]['memory']['query_count']}, "
                        f"injected={rows[-1]['memory']['injected_count']}"
                    ),
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:managed_reliability_metrics_present",
                    all(
                        key in managed_tokens
                        for key in (
                            "rewrite_error_fallback_count",
                            "memory_query_count",
                            "memory_hit_count",
                            "memory_injected_count",
                        )
                    ),
                    (
                        "present="
                        + str(
                            {
                                key: key in managed_tokens
                                for key in (
                                    "rewrite_error_fallback_count",
                                    "memory_query_count",
                                    "memory_hit_count",
                                    "memory_injected_count",
                                )
                            }
                        )
                    ),
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:structured_non_text_state_observed",
                    all(
                        key in managed_state
                        for key in (
                            "state_count",
                            "state_type_counts",
                            "payload_kind_counts",
                            "structured_non_text_count",
                            "contains_embedding_refs_count",
                            "total_size_bytes",
                        )
                    )
                    and rows[-1]["state"]["state_count"] > 0
                    and rows[-1]["state"][
                        "structured_non_text_count"
                    ]
                    > 0
                    and rows[-1]["state"]["state_type_counts"].get(
                        "artifact_state", 0
                    )
                    > 0,
                    (
                        f"state_count={rows[-1]['state']['state_count']}, "
                        f"structured_non_text="
                        f"{rows[-1]['state']['structured_non_text_count']}, "
                        f"types={rows[-1]['state']['state_type_counts']}"
                    ),
                    category="evidence",
                ),
                _check(
                    f"{scenario_id}:managed_transport_cost_is_measurable",
                    rows[-1]["transport"]["native_baseline_tokens"] > 0
                    and rows[-1]["transport"][
                        "end_to_end_collaboration_tokens"
                    ]
                    > 0,
                    (
                        f"native_transport="
                        f"{rows[-1]['transport']['native_baseline_tokens']}, "
                        f"managed_transport="
                        f"{rows[-1]['transport']['end_to_end_collaboration_tokens']}"
                    ),
                    category="evidence",
                ),
            ]
        )

    aggregates = _aggregate_rows(rows)
    quality_margin = _float(
        thresholds["managed_quality_noninferiority_margin"]
    )
    delivery_delta_min = _int(
        thresholds["managed_delivery_complete_delta_min"]
    )
    provider_reduction_min = _float(
        thresholds["managed_provider_token_reduction_min"]
    )
    transport_reduction_min = _float(
        thresholds["managed_transport_token_reduction_min"]
    )
    fallback_max = _int(
        thresholds["rewrite_error_fallback_count_max"]
    )
    target_checks = [
        _check(
            "managed_delivery_is_noninferior",
            aggregates["quality"]["managed"]["delivery_complete_count"]
            - aggregates["quality"]["native"]["delivery_complete_count"]
            >= delivery_delta_min
            and aggregates["provider"]["managed"]["strict_delivery_count"]
            >= aggregates["provider"]["native"]["strict_delivery_count"],
            (
                f"blind_delta="
                f"{aggregates['quality']['managed']['delivery_complete_count'] - aggregates['quality']['native']['delivery_complete_count']}, "
                f"strict_managed="
                f"{aggregates['provider']['managed']['strict_delivery_count']}, "
                f"strict_native="
                f"{aggregates['provider']['native']['strict_delivery_count']}"
            ),
            category="target",
        ),
        _check(
            "managed_quality_is_noninferior",
            aggregates["quality"]["managed"]["mean_score"]
            >= aggregates["quality"]["native"]["mean_score"] - quality_margin,
            (
                f"managed={aggregates['quality']['managed']['mean_score']:.4f}, "
                f"native={aggregates['quality']['native']['mean_score']:.4f}, "
                f"margin={quality_margin:.4f}"
            ),
            category="target",
        ),
        _check(
            "managed_provider_tokens_meet_reduction_target",
            aggregates["provider_reduction_ratio"]
            >= provider_reduction_min,
            (
                f"reduction={aggregates['provider_reduction_ratio']:.4f}, "
                f"target={provider_reduction_min:.4f}"
            ),
            category="target",
        ),
        _check(
            "managed_transport_tokens_meet_reduction_target",
            aggregates["transport_reduction_ratio"]
            >= transport_reduction_min,
            (
                f"reduction={aggregates['transport_reduction_ratio']:.4f}, "
                f"target={transport_reduction_min:.4f}"
            ),
            category="target",
        ),
        _check(
            "managed_has_no_rewrite_error_fallback",
            aggregates["managed_rewrite_error_fallback_count"]
            <= fallback_max,
            (
                f"actual="
                f"{aggregates['managed_rewrite_error_fallback_count']}, "
                f"max={fallback_max}"
            ),
            category="target",
        ),
    ]
    evidence_pipeline_passed = all(
        item["passed"] for item in evidence_checks
    )
    preliminary_targets_met = all(
        item["passed"] for item in target_checks
    )
    passed = evidence_pipeline_passed and preliminary_targets_met
    return {
        "schema_version": "agentlite.v514b.preflight-report.v1",
        "summary": {
            "passed": passed,
            "evidence_pipeline_passed": evidence_pipeline_passed,
            "preliminary_targets_met": preliminary_targets_met,
            "ready_for_formal_run": passed,
            "check_count": len(evidence_checks) + len(target_checks),
            "passed_check_count": sum(
                item["passed"] for item in evidence_checks + target_checks
            ),
            "scenario_count": len(rows),
            "task_count_per_group": sum(
                row["task_counts"].get("native", 0) for row in rows
            ),
        },
        "preregistration": preregistration,
        "aggregates": aggregates,
        "checks": evidence_checks + target_checks,
        "scenarios": rows,
    }


def load_evidence(
    *, run_root: Path, preregistration: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for item in preregistration.get("scenarios") or []:
        scenario_id = str(item["scenario_id"])
        scenario_dir = run_root / str(item["directory"])
        evidence[scenario_id] = {
            "comparison": _read_json(
                scenario_dir / "comparison" / "stateful_comparison.json"
            ),
            "quality": _read_json(
                scenario_dir / "comparison" / "quality_blind_summary.json"
            ),
            "observed_report": _read_json(
                scenario_dir / "reports" / "observed-agentlite.json"
            ),
            "managed_report": _read_json(
                scenario_dir / "reports" / "managed-agentlite.json"
            ),
        }
    return evidence


def write_outputs(
    report: dict[str, Any],
    *,
    output_json: Path,
    output_markdown: Path,
) -> None:
    for path in (output_json, output_markdown):
        path.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output_markdown.write_text(
        _render_markdown(report),
        encoding="utf-8",
    )


def _aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    provider = {
        group: {
            "calls": sum(row["provider"][group]["calls"] for row in rows),
            "prompt_tokens": sum(
                row["provider"][group]["prompt_tokens"] for row in rows
            ),
            "completion_tokens": sum(
                row["provider"][group]["completion_tokens"] for row in rows
            ),
            "total_tokens": sum(
                row["provider"][group]["total_tokens"] for row in rows
            ),
            "wall_time_ms": sum(
                row["provider"][group]["wall_time_ms"] for row in rows
            ),
            "strict_delivery_count": sum(
                row["provider"][group]["strict_delivery_count"]
                for row in rows
            ),
        }
        for group in GROUPS
    }
    quality: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        task_count = sum(
            row["quality"][group]["task_count"] for row in rows
        )
        weighted_total = sum(
            row["quality"][group]["mean_score"]
            * row["quality"][group]["task_count"]
            for row in rows
        )
        quality[group] = {
            "task_count": task_count,
            "mean_score": (
                weighted_total / task_count if task_count else 0.0
            ),
            "delivery_complete_count": sum(
                row["quality"][group]["delivery_complete_count"]
                for row in rows
            ),
            "blocking_finding_count": sum(
                row["quality"][group]["blocking_finding_count"]
                for row in rows
            ),
        }
    transport = {
        key: sum(row["transport"][key] for row in rows)
        for key in (
            "native_baseline_tokens",
            "end_to_end_collaboration_tokens",
            "direct_message_tokens",
            "prompt_view_tokens",
            "retrieved_memory_tokens",
            "control_llm_tokens",
            "retry_tokens",
        )
    }
    memory = {
        key: sum(row["memory"][key] for row in rows)
        for key in (
            "query_count",
            "hit_count",
            "injected_count",
            "useful_hit_count",
            "wrong_hit_count",
            "unassessed_hit_count",
        )
    }
    state_type_counts: dict[str, int] = {}
    payload_kind_counts: dict[str, int] = {}
    for row in rows:
        for key, value in row["state"]["state_type_counts"].items():
            state_type_counts[key] = state_type_counts.get(key, 0) + _int(
                value
            )
        for key, value in row["state"]["payload_kind_counts"].items():
            payload_kind_counts[key] = (
                payload_kind_counts.get(key, 0) + _int(value)
            )
    state = {
        "state_count": sum(row["state"]["state_count"] for row in rows),
        "state_type_counts": dict(sorted(state_type_counts.items())),
        "payload_kind_counts": dict(sorted(payload_kind_counts.items())),
        "structured_non_text_count": sum(
            row["state"]["structured_non_text_count"] for row in rows
        ),
        "contains_embedding_refs_count": sum(
            row["state"]["contains_embedding_refs_count"] for row in rows
        ),
        "total_size_bytes": sum(
            row["state"]["total_size_bytes"] for row in rows
        ),
    }
    return {
        "provider": provider,
        "quality": quality,
        "transport": transport,
        "memory": memory,
        "state": state,
        "provider_reduction_ratio": _reduction_ratio(
            provider["native"]["total_tokens"],
            provider["managed"]["total_tokens"],
        ),
        "observed_provider_change_ratio": _change_ratio(
            provider["observed"]["total_tokens"],
            provider["native"]["total_tokens"],
        ),
        "transport_reduction_ratio": _reduction_ratio(
            transport["native_baseline_tokens"],
            transport["end_to_end_collaboration_tokens"],
        ),
        "managed_rewrite_error_fallback_count": sum(
            row["rewrite"]["managed_error_fallback_count"] for row in rows
        ),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    aggregates = report["aggregates"]
    lines = [
        "# v5.14b 公平成本-质量预检报告",
        "",
        f"- 总体通过：`{summary['passed']}`",
        f"- 证据管线通过：`{summary['evidence_pipeline_passed']}`",
        f"- 初步目标通过：`{summary['preliminary_targets_met']}`",
        f"- 可进入正式 A1-A10/B1-B10：`{summary['ready_for_formal_run']}`",
        (
            f"- 检查项：`{summary['passed_check_count']}/"
            f"{summary['check_count']}`"
        ),
        "",
        "## Provider 实际用量",
        "",
        "| 组别 | 调用 | Prompt Token | Completion Token | 总 Token | 严格交付 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for group in GROUPS:
        value = aggregates["provider"][group]
        lines.append(
            f"| {group} | {value['calls']} | {value['prompt_tokens']} | "
            f"{value['completion_tokens']} | {value['total_tokens']} | "
            f"{value['strict_delivery_count']} |"
        )
    lines.extend(
        [
            "",
            (
                f"- Managed 相对 Native Provider Token 降低："
                f"`{aggregates['provider_reduction_ratio']:.2%}`"
            ),
            (
                f"- Observed 相对 Native Provider Token 变化："
                f"`{aggregates['observed_provider_change_ratio']:.2%}`"
            ),
            "",
            "## 端到端协作成本",
            "",
            "| 指标 | Token |",
            "|---|---:|",
        ]
    )
    for key, label in (
        ("native_baseline_tokens", "Managed 同输出原生传输基线"),
        ("end_to_end_collaboration_tokens", "AgentLite 端到端协作"),
        ("direct_message_tokens", "直接消息"),
        ("prompt_view_tokens", "Prompt View"),
        ("retrieved_memory_tokens", "记忆读取"),
        ("control_llm_tokens", "控制模型"),
        ("retry_tokens", "重试"),
    ):
        lines.append(f"| {label} | {aggregates['transport'][key]} |")
    lines.extend(
        [
            "",
            (
                f"- 实际传输成本降低："
                f"`{aggregates['transport_reduction_ratio']:.2%}`"
            ),
            "",
            "## 非文本状态证据",
            "",
            f"- 状态总数：`{aggregates['state']['state_count']}`",
            (
                "- 结构化非文本状态："
                f"`{aggregates['state']['structured_non_text_count']}`"
            ),
            (
                "- 含 embedding 引用的状态："
                f"`{aggregates['state']['contains_embedding_refs_count']}`"
            ),
            f"- 状态总字节：`{aggregates['state']['total_size_bytes']}`",
            "",
            "| 状态类型 | 数量 |",
            "|---|---:|",
        ]
    )
    state_types = aggregates["state"]["state_type_counts"]
    if state_types:
        for state_type, count in state_types.items():
            lines.append(f"| `{state_type}` | {count} |")
    else:
        lines.append("| `unavailable` | 0 |")
    lines.extend(
        [
            "",
            "## 匿名质量",
            "",
            "| 组别 | 任务数 | 综合均分 | 完整交付 | 阻断级问题 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for group in GROUPS:
        value = aggregates["quality"][group]
        lines.append(
            f"| {group} | {value['task_count']} | "
            f"{value['mean_score']:.3f} | "
            f"{value['delivery_complete_count']} | "
            f"{value['blocking_finding_count']} |"
        )
    lines.extend(["", "## 检查项", ""])
    for check in report["checks"]:
        marker = "PASS" if check["passed"] else "FAIL"
        lines.append(
            f"- [{marker}] `{check['name']}` ({check['category']}): "
            f"{check['detail']}"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- Provider Token 来自模型响应 usage；评审模型 Token 单独统计，不进入运行时协作成本。",
            "- 端到端协作成本来自 Managed trace，并包含直接消息、Prompt View、记忆读取、控制和重试。",
            "- 状态类型来自真实 StatePool 快照；没有产生的 retrieval_state 或 embedding_state 保持为 0，不由题目文本推断。",
            "- 预检只覆盖 A1-A3/B1-B3；只有全部目标通过后，才能进入正式十轮实验。",
            "- 预检失败时保留完整证据并定位原因，不允许看结果后修改预注册阈值。",
            "",
        ]
    )
    return "\n".join(lines)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing v5.14b evidence: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _check(
    name: str,
    passed: bool,
    detail: str,
    *,
    category: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "detail": detail,
        "category": category,
    }


def _first_int(value: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in value and value.get(key) is not None:
            return _int(value.get(key))
    return 0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _reduction_ratio(baseline: int, value: int) -> float:
    return (baseline - value) / baseline if baseline else 0.0


def _change_ratio(value: int, baseline: int) -> float:
    return (value - baseline) / baseline if baseline else 0.0


def main() -> int:
    args = parse_args()
    preregistration = _read_json(
        args.preregistration.expanduser().resolve()
    )
    run_root = args.run_root.expanduser().resolve()
    report = evaluate(
        preregistration=preregistration,
        evidence_by_scenario=load_evidence(
            run_root=run_root,
            preregistration=preregistration,
        ),
    )
    write_outputs(
        report,
        output_json=args.output_json.expanduser().resolve(),
        output_markdown=args.output_markdown.expanduser().resolve(),
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
