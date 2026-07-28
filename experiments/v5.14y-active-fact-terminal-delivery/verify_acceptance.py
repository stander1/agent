from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.drivers.autogen import (
    _required_evidence_fallback_artifact,
)
from agent_runtime.memory.claim_extractor import extract_claim_cards
from agent_runtime.reliability.final_delivery_guard import (
    assess_final_delivery,
    is_prior_artifact_approval,
)
from web_monitor.parser import _autogen_token_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify v5.14y active-fact and terminal-delivery governance."
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

    ambiguous_text = (
        "The plan reserves 700 USD as budget headroom. "
        "Transport (380 USD), lodging (700 USD), local transfer (180 USD), "
        "and the core activity (180 USD) budgets are protected. "
        "Budget note: 1."
    )
    ambiguous_claims = extract_claim_cards(
        ambiguous_text,
        subject="project:generic",
    )
    ambiguous_caps = [
        str(claim["value"])
        for claim in ambiguous_claims
        if claim["scope"] == "constraint.budget_upper_bound"
    ]
    checks.extend(
        [
            _check(
                "component_and_reserve_values_are_not_global_caps",
                not ambiguous_caps,
                json.dumps(ambiguous_claims, ensure_ascii=False),
            ),
            _check(
                "reserve_is_preserved_as_allocation",
                any(
                    str(claim["scope"]).startswith("allocation.budget.")
                    for claim in ambiguous_claims
                ),
                json.dumps(ambiguous_claims, ensure_ascii=False),
            ),
        ]
    )

    history_claims = extract_claim_cards(
        """## Current plan
The budget cap is 2600 USD and the estimated total is 2400 USD.

## Decision log
R3 -> R4: the historical budget cap was 3000 USD.
""",
        subject="project:generic",
    )
    history_caps = [
        str(claim["value"])
        for claim in history_claims
        if claim["scope"] == "constraint.budget_upper_bound"
    ]
    checks.append(
        _check(
            "historical_cap_is_not_admitted_as_current",
            history_caps == ["2600"],
            json.dumps(history_claims, ensure_ascii=False),
        )
    )

    destination_claims = extract_claim_cards(
        "Selected destination: Cedar Harbor Selection rationale: best access.",
        subject="project:generic",
    )
    destination_values = [
        str(claim["value"])
        for claim in destination_claims
        if claim["scope"] == "plan.selected_destination"
    ]
    checks.append(
        _check(
            "selected_value_drops_trailing_rationale_heading",
            destination_values == ["Cedar Harbor"],
            json.dumps(destination_claims, ensure_ascii=False),
        )
    )

    compliant_with_history = assess_final_delivery(
        request="Deliver the plan with a budget cap of 2600 USD.",
        content=(
            "## Final deliverable\n"
            "The current budget total is 2400 USD.\n"
            "## Decision log\n"
            "R3 -> R4: the historical budget cap was 3000 USD.\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        expected_source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    active_violation = assess_final_delivery(
        request="Deliver the plan with a budget cap of 2600 USD.",
        content=(
            "## Final deliverable\n"
            "Decision log:\n"
            "- Interaction #1: the initial budget was 3000 USD.\n"
            "Current total budget is 2800 USD.\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        expected_source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    checks.extend(
        [
            _check(
                "historical_numeric_value_does_not_reject_current_artifact",
                compliant_with_history.valid,
                json.dumps(
                    compliant_with_history.missing_requirements,
                    ensure_ascii=False,
                ),
            ),
            _check(
                "active_numeric_violation_remains_blocking",
                not active_violation.valid
                and "numeric_upper_bound_violation"
                in active_violation.reasons,
                json.dumps(
                    active_violation.missing_requirements,
                    ensure_ascii=False,
                ),
            ),
        ]
    )

    terminal_approval = (
        "The first submitted draft failed and required revision. "
        "The second submitted draft also needed repair. "
        "The latest submitted artifact is validated and approved as the "
        "final delivery."
    )
    approval_then_revision = (
        "The submitted artifact is approved in principle, but it still "
        "requires revision before final delivery."
    )
    checks.extend(
        [
            _check(
                "terminal_approval_can_follow_historical_rejections",
                is_prior_artifact_approval(terminal_approval),
                terminal_approval,
            ),
            _check(
                "revision_after_approval_remains_blocking",
                not is_prior_artifact_approval(approval_then_revision),
                approval_then_revision,
            ),
        ]
    )

    fallback = _required_evidence_fallback_artifact(
        fallback_value="needs_more_evidence",
        unavailable_artifacts=["policy_rules.md"],
    )
    checks.extend(
        [
            _check(
                "required_evidence_fallback_is_a_complete_degraded_artifact",
                all(
                    marker in fallback
                    for marker in (
                        "needs_more_evidence",
                        "degraded_fallback",
                        "policy_rules.md",
                        "Uncertainty",
                        "Allowed next step",
                    )
                ),
                fallback,
            ),
            _check(
                "required_evidence_fallback_is_not_deferred_instruction",
                "Continue from the current task" not in fallback,
                fallback,
            ),
        ]
    )

    summary = _autogen_token_summary(
        [
            {
                "event_type": "autogen_required_evidence_guard",
                "payload": {
                    "status": "blocked_with_explicit_fallback",
                },
            }
        ]
    )
    checks.append(
        _check(
            "explicit_fallback_guard_is_observable",
            summary["required_evidence_guard_event_count"] == 1
            and summary["required_evidence_guard_blocked_count"] == 1,
            json.dumps(summary, sort_keys=True),
        )
    )

    production_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (repo_root / "agent_runtime").rglob("*.py")
    )
    forbidden_literals = (
        "Cedar Harbor",
        "Question A",
        "Question B",
        "policy_rules.md",
    )
    checks.append(
        _check(
            "production_runtime_has_no_acceptance_fixture_literals",
            not any(item in production_text for item in forbidden_literals),
            ",".join(
                item for item in forbidden_literals if item in production_text
            )
            or "none",
        )
    )

    passed_count = sum(int(item["passed"]) for item in checks)
    return {
        "summary": {
            "passed": passed_count == len(checks),
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "ambiguous_budget_cap_count": len(ambiguous_caps),
            "active_history_cap_count": len(history_caps),
            "required_evidence_guard_blocked_count": summary[
                "required_evidence_guard_blocked_count"
            ],
        },
        "checks": checks,
    }


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.14y Active Fact and Terminal Delivery Acceptance",
        "",
        f"- passed: `{summary['passed']}`",
        (
            "- checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- ambiguous_budget_cap_count: "
            f"`{summary['ambiguous_budget_cap_count']}`"
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
