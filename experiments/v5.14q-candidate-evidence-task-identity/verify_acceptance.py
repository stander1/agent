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
    _current_candidate_artifact_view,
    _current_task_identity_assessment,
)
from agent_runtime.memory.context_views import (
    ConsumerCapabilityContext,
    build_minimal_context_view,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify candidate evidence and task identity guards."
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

    candidate = "\n".join(
        (
            "CANDIDATE_BEGIN",
            *[f"evidence-{index}: amount={index * 17}" for index in range(80)],
            "CANDIDATE_MIDDLE",
            *[f"constraint-{index}: value={index * 19}" for index in range(80, 160)],
            "CANDIDATE_END",
        )
    )
    messages = [
        SimpleNamespace(
            source="LegacyWorker",
            content_text="obsolete " * 4000,
            message_kind="text",
            native_type="TextMessage",
        ),
        SimpleNamespace(
            source="DomainBuilder42",
            content_text=candidate,
            message_kind="text",
            native_type="TextMessage",
        ),
    ]
    validation_view = _current_candidate_artifact_view(
        messages,
        semantic_action="REVIEW_OUTPUT",
        capabilities=("validation",),
    )
    checks.append(
        _check(
            "validation_capability_requires_current_candidate",
            validation_view["required"] and validation_view["available"],
            json.dumps(
                {
                    "required": validation_view["required"],
                    "available": validation_view["available"],
                    "source": validation_view["source"],
                }
            ),
        )
    )
    checks.append(
        _check(
            "current_candidate_is_complete_not_head_tail_digest",
            validation_view["complete"]
            and candidate in validation_view["text"]
            and all(
                marker in validation_view["text"]
                for marker in (
                    "CANDIDATE_BEGIN",
                    "CANDIDATE_MIDDLE",
                    "CANDIDATE_END",
                )
            ),
            (
                f"source_chars={len(candidate)}, "
                f"selected_chars={len(validation_view['content'])}"
            ),
        )
    )
    generic_view = _current_candidate_artifact_view(
        messages,
        semantic_action="HANDLE_TASK",
        capabilities=("writing",),
    )
    checks.append(
        _check(
            "non_validation_capability_does_not_force_full_candidate",
            not generic_view["required"] and not generic_view["text"],
            json.dumps(generic_view, ensure_ascii=False),
        )
    )

    identity_source = (
        "Use R1, R6, and R7 evidence to assess the current risk. "
        "Do not advance to a later task."
    )
    blocked = _current_task_identity_assessment(
        current_task=identity_source,
        output_text="The current task is R10 and should prepare the next report.",
        task_sequence_index=9,
    )
    checks.append(
        _check(
            "future_task_identity_drift_is_blocked",
            blocked["blocked"]
            and blocked["expected_identity"] == "R9"
            and blocked["unsupported_claims"] == ["R10"],
            json.dumps(blocked, ensure_ascii=False),
        )
    )
    valid = _current_task_identity_assessment(
        current_task=identity_source,
        output_text="The current task is R9; assess only the supplied evidence.",
        task_sequence_index=9,
    )
    checks.append(
        _check(
            "correct_current_task_identity_is_not_blocked",
            not valid["blocked"] and valid["expected_identity"] == "R9",
            json.dumps(valid, ensure_ascii=False),
        )
    )
    ambiguous = _current_task_identity_assessment(
        current_task="Review the current deployment without a task label.",
        output_text="The current task is R8.",
        task_sequence_index=8,
    )
    checks.append(
        _check(
            "ambiguous_identity_remains_passthrough",
            not ambiguous["blocked"]
            and ambiguous["inference_basis"] == "task_label_family_ambiguous",
            json.dumps(ambiguous, ensure_ascii=False),
        )
    )

    identity_anchor = (
        "CURRENT_USER_TASK (highest priority):\n"
        "Review the current deployment.\n"
        "CURRENT_TASK_IDENTITY_RULE:\n"
        "[current_task_identity] The exact CURRENT_USER_TASK above is "
        "interaction #4 and is the only current task."
    )
    minimized = build_minimal_context_view(
        query="Review the current deployment.",
        prompt_views=[identity_anchor, "obsolete history " * 1000],
        consumer=ConsumerCapabilityContext(
            consumer_id="QualitySentinel",
            capabilities=("validation",),
        ),
        action="REVIEW_OUTPUT",
        budget_chars=220,
    )
    checks.append(
        _check(
            "identity_anchor_survives_context_minimization",
            "[current_task_identity]" in minimized.text
            and minimized.current_task_units_preserved,
            minimized.text,
        )
    )

    passed_count = sum(bool(item["passed"]) for item in checks)
    return {
        "summary": {
            "passed": passed_count == len(checks),
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "candidate_source_chars": len(candidate),
            "candidate_selected_chars": len(validation_view["content"]),
            "identity_guard_block_count": int(blocked["blocked"]),
            "identity_guard_false_positive_count": int(valid["blocked"])
            + int(ambiguous["blocked"]),
        },
        "checks": checks,
    }


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
    summary = dict(report.get("summary") or {})
    lines = [
        "# v5.14q 当前候选充分证据与任务身份验收",
        "",
        f"- 总体通过：`{summary.get('passed')}`",
        (
            f"- 检查项：`{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        (
            f"- 候选正文字符：`{summary.get('candidate_selected_chars')}/"
            f"{summary.get('candidate_source_chars')}`"
        ),
        f"- 身份漂移拦截：`{summary.get('identity_guard_block_count')}`",
        (
            "- 身份守卫误报："
            f"`{summary.get('identity_guard_false_positive_count')}`"
        ),
        "",
        "## 检查明细",
        "",
    ]
    for item in report["checks"]:
        lines.append(
            f"- [{'x' if item['passed'] else ' '}] `{item['name']}`："
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
