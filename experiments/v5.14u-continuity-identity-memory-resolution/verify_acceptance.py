from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.drivers.autogen import (
    _build_chronology_prompt_view,
    _current_task_identity_assessment,
    _decision_preserving_summary,
    _resolve_approved_prior_artifact,
)
from agent_runtime.memory.context_views import (
    ConsumerCapabilityContext,
    build_minimal_context_view,
)
from agent_runtime.reliability.final_delivery_guard import assess_final_delivery


MARKER = "FINAL_ANSWER_READY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify continuity, identity, and memory resolution."
    )
    parser.add_argument("--unittest-output", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(*, unittest_output: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    unittest_text = _read_text(unittest_output)
    checks.append(
        _check(
            "full_unittest_suite_passed",
            "\nOK\n" in unittest_text.replace("\r\n", "\n"),
            _last_nonempty_line(unittest_text),
        )
    )

    identity = _current_task_identity_assessment(
        current_task="Use Q3 evidence to complete the next comparison.",
        output_text="The current task is Q5.",
        task_sequence_index=4,
        user_task_history=(
            "Q1 establish the evidence scope.",
            "Q2 collect the evidence.",
            "Q3 assess the evidence.",
            "Use Q3 evidence to complete the next comparison.",
        ),
    )
    checks.append(
        _check(
            "history_grounded_backward_or_forward_identity_is_blocked",
            (
                identity["blocked"]
                and identity["expected_identity"] == "Q4"
                and identity["unsupported_claims"] == ["Q5"]
            ),
            json.dumps(identity, ensure_ascii=False),
        )
    )

    ordinal = _current_task_identity_assessment(
        current_task="Complete the sixth sequential request.",
        output_text="当前用户指令（交互 #3）要求生成完整交付物。",
        task_sequence_index=6,
    )
    checks.append(
        _check(
            "wrong_explicit_interaction_ordinal_is_blocked",
            ordinal["unsupported_interaction_indices"] == [3],
            json.dumps(ordinal, ensure_ascii=False),
        )
    )

    ordinary_count = _current_task_identity_assessment(
        current_task="Compare three candidates.",
        output_text="当前任务包含3个候选方案，下面逐项比较。",
        task_sequence_index=6,
    )
    checks.append(
        _check(
            "ordinary_numeric_count_is_not_an_identity_claim",
            not ordinary_count["blocked"],
            json.dumps(ordinary_count, ensure_ascii=False),
        )
    )

    request = (
        "Use the previous risk conclusion to prepare the final decision report "
        "without changing that conclusion."
    )
    artifact = (
        "## Complete evidence artifact\n"
        + ("Source lineage and uncertainty details. " * 80)
        + "\nRisk decision: needs_more_evidence\n"
        + ("Bounded follow-up collection details. " * 80)
    )
    approval = (
        "确认前序 specialist 的成果符合要求，批准作为最终交付物。\n"
        f"{MARKER}"
    )
    messages = [
        _message(request, "user"),
        _message(artifact, "specialist"),
        _message(approval, "quality_gate"),
    ]
    chronology = _build_chronology_prompt_view(
        messages,
        current_task=request,
        user_task_history=("Assess the evidence and state the risk conclusion.",),
        final_delivery_marker=MARKER,
        task_sequence_index=2,
    )
    view = build_minimal_context_view(
        query=request,
        prompt_views=[chronology],
        consumer=ConsumerCapabilityContext(
            consumer_id="decision_reporter",
            capabilities=("synthesis", "decision_tracking"),
            input_preference=("decisions", "evidence", "constraints"),
        ),
        action="SYNTHESIZE",
        budget_chars=900,
    )
    checks.append(
        _check(
            "semantic_selection_preserves_middle_prior_decision",
            (
                "needs_more_evidence" in view.text
                and len(view.text) < len(chronology)
            ),
            json.dumps(
                {
                    "source_chars": len(chronology),
                    "selected_chars": len(view.text),
                    "decision_present": "needs_more_evidence" in view.text,
                },
                ensure_ascii=False,
            ),
        )
    )

    resolved = _resolve_approved_prior_artifact(
        messages=messages,
        request=request,
        marker=MARKER,
        grounding_contexts=(request,),
    )
    checks.append(
        _check(
            "compact_approval_resolves_prior_valid_artifact",
            (
                resolved is not None
                and resolved[1] == "specialist"
                and resolved[2].valid
            ),
            (
                json.dumps(resolved[2].to_dict(), ensure_ascii=False)
                if resolved is not None
                else "unresolved"
            ),
        )
    )

    summary = _decision_preserving_summary(artifact, limit=900)
    checks.append(
        _check(
            "promoted_summary_preserves_explicit_decision",
            (
                "needs_more_evidence" in summary
                and len(summary) <= 900
            ),
            json.dumps(
                {
                    "summary_chars": len(summary),
                    "decision_present": "needs_more_evidence" in summary,
                },
                ensure_ascii=False,
            ),
        )
    )

    rejection = assess_final_delivery(
        request=request,
        content=(
            "Review finding: the prior artifact omits required evidence. "
            "Revise it before delivery.\n"
            f"{MARKER}"
        ),
        source="quality_gate",
        marker=MARKER,
        require_marker=True,
    )
    checks.append(
        _check(
            "review_rejection_cannot_trigger_prior_artifact_promotion",
            (
                not rejection.valid
                and not rejection.approved_prior_artifact
            ),
            json.dumps(rejection.to_dict(), ensure_ascii=False),
        )
    )

    passed_count = sum(bool(item["passed"]) for item in checks)
    return {
        "summary": {
            "passed": passed_count == len(checks),
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "identity_guard_count": int(identity["blocked"])
            + int(ordinal["blocked"]),
            "identity_false_positive_count": int(ordinary_count["blocked"]),
            "prior_artifact_resolution_count": int(resolved is not None),
            "decision_preservation_count": int(
                "needs_more_evidence" in summary
            ),
        },
        "checks": checks,
    }


def _message(content: str, source: str) -> SimpleNamespace:
    return SimpleNamespace(
        content_text=content,
        source=source,
        native_type="TextMessage",
        message_kind="text",
    )


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": str(detail)}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if raw and raw.count(b"\x00") > len(raw) // 4:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _last_nonempty_line(text: str) -> str:
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    return rows[-1] if rows else ""


def _markdown(report: dict[str, Any]) -> str:
    summary = dict(report["summary"])
    lines = [
        "# v5.14u Continuity, Identity, and Memory Resolution Acceptance",
        "",
        f"- Passed: `{summary['passed']}`",
        (
            f"- Checks: `{summary['passed_check_count']}/"
            f"{summary['check_count']}`"
        ),
        f"- Identity guards: `{summary['identity_guard_count']}`",
        (
            "- Identity false positives: "
            f"`{summary['identity_false_positive_count']}`"
        ),
        (
            "- Prior artifact resolutions: "
            f"`{summary['prior_artifact_resolution_count']}`"
        ),
        (
            "- Preserved decisions: "
            f"`{summary['decision_preservation_count']}`"
        ),
        "",
        "## Checks",
        "",
    ]
    for item in report["checks"]:
        lines.append(
            f"- [{'x' if item['passed'] else ' '}] `{item['name']}`: "
            f"{item['detail']}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    report = evaluate(unittest_output=args.unittest_output)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.output_markdown.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
