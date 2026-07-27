from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.drivers.autogen import (
    _required_evidence_assessment,
    _required_evidence_contract,
    _required_evidence_prompt_rule,
)
from agent_runtime.eval.autogen_session_report import _metric_rows
from agent_runtime.memory.claim_extractor import extract_claim_cards
from web_monitor.parser import _autogen_token_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify v5.14w evidence fidelity governance."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--unittest-output", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(*, repo_root: Path, unittest_output: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    test_text = unittest_output.read_text(encoding="utf-8")
    checks.append(
        _check(
            "targeted_unittest_passed",
            "\nOK\n" in test_text,
            "see unittest.txt",
        )
    )

    facet_claims = extract_claim_cards(
        (
            "The working budget estimate is 2,300 USD, while the approved "
            "budget cap remains 3,000 USD."
        ),
        subject="project:generic",
    )
    revision_claims = extract_claim_cards(
        "Revise the budget cap from 3,000 USD to 3,200 USD.",
        subject="project:generic",
    )
    by_scope: dict[str, list[str]] = {}
    for claim in facet_claims:
        by_scope.setdefault(str(claim["scope"]), []).append(
            str(claim["value"])
        )
    revision_by_scope: dict[str, list[str]] = {}
    for claim in revision_claims:
        revision_by_scope.setdefault(str(claim["scope"]), []).append(
            str(claim["value"])
        )
    checks.extend(
        [
            _check(
                "budget_estimate_keeps_grouped_value",
                by_scope.get("estimate.budget_total") == ["2300"],
                json.dumps(by_scope, sort_keys=True),
            ),
            _check(
                "budget_upper_bound_uses_revision_target",
                revision_by_scope.get("constraint.budget_upper_bound")
                == ["3200"],
                json.dumps(revision_by_scope, sort_keys=True),
            ),
            _check(
                "historical_revision_source_is_not_active_upper_bound",
                "3000"
                not in revision_by_scope.get(
                    "constraint.budget_upper_bound",
                    [],
                ),
                json.dumps(revision_by_scope, sort_keys=True),
            ),
        ]
    )

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        task = (
            "Classify the release using policy_rules.md. "
            "Evidence missing must output needs_more_evidence."
        )
        contract = _required_evidence_contract(
            current_task=task,
            evidence_context="",
            target_cwd=workspace,
        )
        conflict = _required_evidence_assessment(
            current_task=task,
            evidence_context="",
            output_text=(
                "Final decision: approved_release. "
                "This is based on policy_rules.md."
            ),
            target_cwd=workspace,
        )
        fallback = _required_evidence_assessment(
            current_task=task,
            evidence_context="",
            output_text="Final decision: needs_more_evidence.",
            target_cwd=workspace,
        )
        supplied = _required_evidence_contract(
            current_task=task,
            evidence_context=(
                "BEGIN policy_rules.md\n"
                "release.approval_threshold=0.8\n"
                "END policy_rules.md"
            ),
            target_cwd=workspace,
        )
        prompt_rule = _required_evidence_prompt_rule(
            current_task=task,
            evidence_context="",
            target_cwd=workspace,
        )
        checks.extend(
            [
                _check(
                    "missing_required_artifact_is_detected",
                    contract["required"]
                    and contract["unavailable_artifacts"]
                    == ["policy_rules.md"],
                    json.dumps(contract, sort_keys=True),
                ),
                _check(
                    "unsupported_strong_decision_is_blocked",
                    conflict["blocked"]
                    and conflict["conflicting_decision_values"]
                    == ["approved_release"],
                    json.dumps(conflict, sort_keys=True),
                ),
                _check(
                    "unavailable_artifact_read_claim_is_blocked",
                    conflict["claimed_unavailable_artifacts"]
                    == ["policy_rules.md"],
                    json.dumps(conflict, sort_keys=True),
                ),
                _check(
                    "explicit_fallback_is_preserved",
                    not fallback["blocked"]
                    and fallback["fallback_value"]
                    == "needs_more_evidence",
                    json.dumps(fallback, sort_keys=True),
                ),
                _check(
                    "supplied_artifact_disables_missing_guard",
                    not supplied["required"]
                    and supplied["available_artifacts"]
                    == ["policy_rules.md"],
                    json.dumps(supplied, sort_keys=True),
                ),
                _check(
                    "prompt_preflight_is_minimal_and_explicit",
                    "policy_rules.md" in prompt_rule
                    and "needs_more_evidence" in prompt_rule
                    and "approved_release" not in prompt_rule,
                    prompt_rule,
                ),
            ]
        )

    summary = _autogen_token_summary(
        [
            {
                "event_type": "autogen_required_evidence_guard",
                "payload": {"status": "blocked_and_deferred"},
            }
        ]
    )
    metrics = {
        row["metric"]: row["value"]
        for row in _metric_rows(summary)
    }
    checks.extend(
        [
            _check(
                "required_evidence_guard_is_reported",
                summary["required_evidence_guard_event_count"] == 1
                and summary["required_evidence_guard_blocked_count"] == 1,
                json.dumps(summary, sort_keys=True),
            ),
            _check(
                "required_evidence_guard_metric_is_exported",
                metrics.get("required_evidence_guard_blocked_count") == 1,
                json.dumps(metrics, sort_keys=True),
            ),
        ]
    )

    production_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (repo_root / "agent_runtime").rglob("*.py")
    )
    forbidden_literals = (
        "compliance_rules.md",
        "synthetic_fraud_ring",
        "needs_more_evidence",
    )
    checks.append(
        _check(
            "production_runtime_has_no_fixture_literals",
            not any(item in production_text for item in forbidden_literals),
            ",".join(
                item for item in forbidden_literals if item in production_text
            )
            or "none",
        )
    )

    passed_count = sum(int(item["passed"]) for item in checks)
    summary_row = {
        "passed": passed_count == len(checks),
        "check_count": len(checks),
        "passed_check_count": passed_count,
        "budget_claim_count": len(facet_claims) + len(revision_claims),
        "required_evidence_guard_event_count": summary[
            "required_evidence_guard_event_count"
        ],
        "required_evidence_guard_blocked_count": summary[
            "required_evidence_guard_blocked_count"
        ],
    }
    return {"summary": summary_row, "checks": checks}


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.14w Evidence Fidelity Governance",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- required_evidence_guard_blocked_count: "
            f"`{summary['required_evidence_guard_blocked_count']}`"
        ),
        "",
        "| Check | Passed | Detail |",
        "|---|---:|---|",
    ]
    for item in report["checks"]:
        detail = item["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{item['name']}` | `{item['passed']}` | {detail} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    report = evaluate(
        repo_root=args.repo_root.resolve(),
        unittest_output=args.unittest_output.resolve(),
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
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
