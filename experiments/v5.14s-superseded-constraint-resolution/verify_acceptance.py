from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.reliability.final_delivery_guard import assess_final_delivery


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify superseded numeric constraint resolution."
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

    request = "Deliver the complete plan with a total budget no more than USD 2600."
    safe_with_history = assess_final_delivery(
        request=request,
        content=(
            "## Final deliverable\n\n"
            "The current budget total is USD 2500.\n"
            "Decision log:\n"
            "- Interaction #1: the initial total budget was USD 3000.\n"
            "- Interaction #8: the total budget was reduced from USD 3000 "
            "to USD 2600.\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        expected_source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    checks.append(
        _check(
            "historical_total_does_not_override_current_total",
            safe_with_history.valid,
            json.dumps(safe_with_history.to_dict(), ensure_ascii=False),
        )
    )

    safe_transition = assess_final_delivery(
        request=request,
        content=(
            "## Final deliverable\n\n"
            "The initial budget total was USD 3000 and is now adjusted to "
            "USD 2500.\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        expected_source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    checks.append(
        _check(
            "explicit_transition_uses_replacement_value",
            safe_transition.valid,
            json.dumps(safe_transition.to_dict(), ensure_ascii=False),
        )
    )

    unsafe_transition = assess_final_delivery(
        request=request,
        content=(
            "## Final deliverable\n\n"
            "The initial budget total was USD 3000 and is now adjusted to "
            "USD 2800.\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        expected_source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    checks.append(
        _check(
            "excessive_replacement_value_is_rejected",
            (
                not unsafe_transition.valid
                and "numeric_upper_bound_violation"
                in unsafe_transition.reasons
            ),
            json.dumps(unsafe_transition.to_dict(), ensure_ascii=False),
        )
    )

    unsafe_current = assess_final_delivery(
        request=request,
        content=(
            "## Final deliverable\n\n"
            "The current total cost is USD 2800.\n"
            "FINAL_ANSWER_READY"
        ),
        source="reviewer",
        expected_source="reviewer",
        marker="FINAL_ANSWER_READY",
        require_marker=True,
    )
    checks.append(
        _check(
            "current_excessive_total_remains_rejected",
            (
                not unsafe_current.valid
                and "numeric_upper_bound_violation"
                in unsafe_current.reasons
            ),
            json.dumps(unsafe_current.to_dict(), ensure_ascii=False),
        )
    )

    passed_count = sum(bool(item["passed"]) for item in checks)
    return {
        "summary": {
            "passed": passed_count == len(checks),
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "historical_false_positive_count": int(not safe_with_history.valid),
            "transition_false_positive_count": int(not safe_transition.valid),
            "current_violation_escape_count": int(unsafe_current.valid),
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
    summary = dict(report["summary"])
    lines = [
        "# v5.14s Superseded Constraint Resolution Acceptance",
        "",
        f"- Passed: `{summary['passed']}`",
        (
            f"- Checks: `{summary['passed_check_count']}/"
            f"{summary['check_count']}`"
        ),
        (
            "- Historical false positives: "
            f"`{summary['historical_false_positive_count']}`"
        ),
        (
            "- Transition false positives: "
            f"`{summary['transition_false_positive_count']}`"
        ),
        (
            "- Current violation escapes: "
            f"`{summary['current_violation_escape_count']}`"
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
