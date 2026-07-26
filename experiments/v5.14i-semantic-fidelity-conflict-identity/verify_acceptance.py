from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.drivers.autogen import _structured_memory_adoption_evidence
from agent_runtime.memory.claim_extractor import claim_identity, extract_claim_cards
from agent_runtime.memory.schema_registry import SchemaRegistryLite
from agent_runtime.reliability.final_delivery_guard import assess_final_delivery
from agent_runtime.reliability.memory_adoption_guard import (
    guard_memory_adoption_output,
)
from web_monitor.parser import _autogen_token_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify v5.14i semantic fidelity and conflict identity."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--unittest-output", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(*, repo_root: Path, unittest_output: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    test_text = _read_test_output(unittest_output)
    checks.append(
        _check(
            "targeted_unittest_passed",
            "\nOK\n" in test_text,
            "see unittest.txt",
        )
    )

    budget_claims = extract_claim_cards(
        (
            "总预算上限不超过3000元。"
            "当前方案预计总费用为2320元。"
            "伴手礼预算为200元。"
        ),
        subject="project:generic",
    )
    budget_by_scope = {
        str(claim["scope"]): claim
        for claim in budget_claims
        if "budget" in str(claim["scope"])
    }
    expected_budget = {
        "constraint.budget_upper_bound": "3000",
        "estimate.budget_total": "2320",
    }
    checks.extend(
        [
            _check(
                "budget_upper_bound_and_total_are_distinct",
                all(
                    str(budget_by_scope.get(scope, {}).get("value")) == value
                    for scope, value in expected_budget.items()
                ),
                json.dumps(budget_by_scope, ensure_ascii=False, sort_keys=True),
            ),
            _check(
                "budget_component_has_independent_scope",
                any(
                    scope.startswith("allocation.budget.")
                    and str(claim["value"]) == "200"
                    for scope, claim in budget_by_scope.items()
                ),
                f"scopes={sorted(budget_by_scope)}",
            ),
            _check(
                "budget_claim_identities_do_not_collapse",
                len(
                    {
                        claim_identity(claim)
                        for claim in budget_by_scope.values()
                    }
                )
                == len(budget_by_scope)
                == 3,
                f"identities={len({claim_identity(c) for c in budget_by_scope.values()})}",
            ),
            _check(
                "unicode_component_scope_is_preserved",
                SchemaRegistryLite.normalize_scope(
                    "allocation.budget.住宿"
                )
                == "allocation.budget.住宿",
                SchemaRegistryLite.normalize_scope("allocation.budget.住宿"),
            ),
        ]
    )

    table_claims = extract_claim_cards(
        """
| 费用类别 | 预算（元） | 说明 |
| --- | ---: | --- |
| 住宿 | 760 | 两晚 |
| 核心活动 | 180 | 门票 |
| 总计 | 2300 | 当前估算 |
""",
        subject="project:generic",
    )
    table_budget = [
        claim for claim in table_claims if "budget" in str(claim["scope"])
    ]
    checks.append(
        _check(
            "structured_budget_table_rows_are_preserved",
            {str(claim["value"]) for claim in table_budget}
            == {"760", "180", "2300"},
            f"values={sorted(str(claim['value']) for claim in table_budget)}",
        )
    )

    revision_guard = {
        "subject": "project:generic",
        "semantic_key": "project:generic|slot.runtime.config|config.alert_id",
        "active_facts": [
            _fact("config.alert_id", "alert_071", value_type="string")
        ],
        "historical_facts": [
            _fact("config.alert_id", "alert_071", value_type="string")
        ],
    }
    duplicate_evidence = _structured_memory_adoption_evidence(
        revision_guard=revision_guard,
        current_task_text="Publish the current alert configuration.",
        output_text="Use alert_id=alert_071 in the final configuration.",
        explicit_reference=False,
    )
    duplicate_decision = guard_memory_adoption_output(
        output_text="Use alert_id=alert_071.",
        evidence_rows=[
            {
                "status": "mixed",
                "attribution_mode": "ccf_v2_semantic_key_value_rules",
                "semantic_key": revision_guard["semantic_key"],
                "active_value": "alert_071",
                "historical_values": ["alert_071"],
                "matched_historical_output_spans": [
                    "Use alert_id=alert_071."
                ],
            }
        ],
    )
    checks.extend(
        [
            _check(
                "identical_active_historical_fact_is_not_conflict",
                duplicate_evidence["status"] == "useful"
                and duplicate_evidence["historical_fact_count"] == 0,
                json.dumps(
                    {
                        "status": duplicate_evidence["status"],
                        "historical_fact_count": duplicate_evidence[
                            "historical_fact_count"
                        ],
                    },
                    sort_keys=True,
                ),
            ),
            _check(
                "duplicate_only_guard_row_is_safe",
                duplicate_decision.status == "safe",
                duplicate_decision.status,
            ),
        ]
    )

    partial_decision = guard_memory_adoption_output(
        output_text=(
            "Apply service.capacity=20. "
            "Keep the legacy backlog at thirty."
        ),
        evidence_rows=[
            {
                "status": "mixed",
                "attribution_mode": "ccf_v2_semantic_key_value_rules",
                "semantic_key": "project:generic|config.capacity",
                "active_value": "50",
                "historical_values": ["20", "30"],
                "matched_historical_output_spans": [
                    "Apply service.capacity=20.",
                    "Keep the legacy backlog at thirty.",
                ],
            }
        ],
    )
    blocked_decision = guard_memory_adoption_output(
        output_text="Expose the rejected confidential draft.",
        evidence_rows=[
            {
                "status": "wrong",
                "attribution_mode": "legacy_fuzzy_rules",
                "semantic_key": "project:generic|release.channel",
                "active_value": "stable",
            }
        ],
    )
    checks.extend(
        [
            _check(
                "partial_rule_repair_is_blocked",
                partial_decision.status == "blocked"
                and "incomplete_historical_span_repair"
                in partial_decision.reasons,
                ",".join(partial_decision.reasons),
            ),
            _check(
                "blocked_output_hides_internal_protocol_and_original_body",
                blocked_decision.status == "blocked"
                and "AGENTLITE_" not in blocked_decision.output_text
                and "confidential" not in blocked_decision.output_text,
                blocked_decision.output_text,
            ),
        ]
    )

    machine_request = (
        "必须输出 evidence_chain_state，并包含 hop_path、target "
        "和 evidence_refs 字段。"
    )
    machine_assessment = assess_final_delivery(
        request=machine_request,
        content="## 最终可交付报告\n证据已经复核。\nFINAL_ANSWER_READY",
        source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    candidate_request = (
        "请比较下面全部三个候选并选出推荐方案：\n"
        "1. Alpha Ridge\n2. Beta Harbor\n3. Gamma Valley"
    )
    stale_assessment = assess_final_delivery(
        request=candidate_request,
        content=(
            "## 最终可交付方案\n"
            "需求已经整理，后续再收集候选项。\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    corrected_assessment = assess_final_delivery(
        request=candidate_request,
        content=(
            "## 最终可交付方案\n"
            "Alpha Ridge 距离最远，Beta Harbor 成本最高，"
            "Gamma Valley 综合最优，因此推荐 Gamma Valley。\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    checks.extend(
        [
            _check(
                "machine_deliverable_anchors_are_enforced",
                not machine_assessment.valid
                and "evidence_chain_state"
                in machine_assessment.missing_requirements,
                ",".join(machine_assessment.missing_requirements),
            ),
            _check(
                "stale_candidate_output_is_rejected",
                not stale_assessment.valid
                and "current_task_requirements_missing"
                in stale_assessment.reasons,
                ",".join(stale_assessment.missing_requirements),
            ),
            _check(
                "corrected_candidate_output_is_accepted",
                corrected_assessment.valid,
                ",".join(corrected_assessment.reasons),
            ),
        ]
    )

    token_summary = _autogen_token_summary(
        [
            {
                "event_type": "autogen_agent_input_real_rewrite",
                "payload": {
                    "rewrite_applied": True,
                    "model_visible_protocol_marker_count": 0,
                },
            },
            {
                "event_type": "autogen_agent_output",
                "payload": {
                    "model_visible_protocol_marker_count": 1,
                    "model_visible_surface": "agent_output",
                },
            },
        ]
    )
    checks.append(
        _check(
            "protocol_hygiene_covers_agent_outputs",
            token_summary["model_visible_protocol_marker_count"] == 1
            and token_summary[
                "model_visible_agent_output_protocol_marker_count"
            ]
            == 1,
            json.dumps(
                {
                    key: token_summary[key]
                    for key in (
                        "model_visible_protocol_marker_count",
                        "model_visible_input_protocol_marker_count",
                        "model_visible_agent_output_protocol_marker_count",
                        "model_visible_final_output_protocol_marker_count",
                    )
                },
                sort_keys=True,
            ),
        )
    )

    production_sources = [
        repo_root / "agent_runtime/memory/claim_extractor.py",
        repo_root / "agent_runtime/reliability/memory_adoption_guard.py",
        repo_root / "agent_runtime/reliability/final_delivery_guard.py",
        repo_root / "agent_runtime/adapters/autogen_termination.py",
        repo_root / "agent_runtime/drivers/autogen.py",
    ]
    source_text = "\n".join(
        path.read_text(encoding="utf-8") for path in production_sources
    )
    forbidden = (
        "Question A",
        "question_A",
        "Question B",
        "question_B",
        "莫干山",
        "皖南",
        "浙西",
    )
    leaked = [term for term in forbidden if term in source_text]
    checks.append(
        _check(
            "production_runtime_has_no_experiment_specific_terms",
            not leaked,
            f"leaked={leaked}",
        )
    )

    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "agentlite.v514i.acceptance-report.v1",
        "summary": {
            "passed": passed,
            "check_count": len(checks),
            "passed_check_count": sum(item["passed"] for item in checks),
            "budget_fact_count": len(budget_by_scope),
            "budget_table_fact_count": len(table_budget),
            "duplicate_conflict_count": int(
                duplicate_evidence["status"] in {"wrong", "mixed"}
            ),
            "partial_repair_blocked_count": int(partial_decision.blocked),
            "current_task_fidelity_rejection_count": int(
                not machine_assessment.valid
            )
            + int(not stale_assessment.valid),
        },
        "budget_facts": budget_by_scope,
        "duplicate_evidence": duplicate_evidence,
        "partial_repair": partial_decision.to_dict(),
        "blocked_output": blocked_decision.to_dict(),
        "protocol_summary": {
            key: token_summary[key]
            for key in (
                "model_visible_protocol_marker_count",
                "model_visible_input_protocol_marker_count",
                "model_visible_agent_output_protocol_marker_count",
                "model_visible_final_output_protocol_marker_count",
            )
        },
        "checks": checks,
    }


def _fact(
    scope: str,
    value: str,
    *,
    value_type: str,
) -> dict[str, Any]:
    return {
        "slot_id": "slot.runtime.config",
        "scope": scope,
        "value": value,
        "value_type": value_type,
        "unit": "",
        "polarity": "positive",
    }


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _read_test_output(path: Path) -> str:
    raw = path.read_bytes()
    return (
        raw.decode("utf-16", errors="replace")
        if b"\x00" in raw[:200]
        else raw.decode("utf-8", errors="replace")
    ).replace("\r\n", "\n")


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.14i 语义保真与冲突身份验收",
        "",
        f"- 总体通过：`{summary['passed']}`",
        f"- 通过检查：`{summary['passed_check_count']}/{summary['check_count']}`",
        f"- 预算事实：`{summary['budget_fact_count']}`",
        f"- 表格预算事实：`{summary['budget_table_fact_count']}`",
        f"- 重复事实误冲突：`{summary['duplicate_conflict_count']}`",
        "",
        "## 检查项",
        "",
    ]
    for item in report["checks"]:
        lines.append(
            f"- [{'x' if item['passed'] else ' '}] `{item['name']}`："
            f"{item['detail']}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    report = evaluate(
        repo_root=args.repo_root.resolve(),
        unittest_output=args.unittest_output.resolve(),
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.output_markdown.write_text(
        _render_markdown(report),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
