from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autogen_agentchat.messages import TextMessage

from agent_runtime.adapters.autogen_termination import resolve_final_artifact
from agent_runtime.reliability.final_delivery_guard import (
    assess_final_delivery,
    is_prior_artifact_approval,
)


MARKER = "FINAL_ANSWER_READY"
APPROVAL_VARIANTS = (
    "## 验收通过\n批准上一份 Writer 成果作为最终交付物。",
    "## 验收通过\n批准上一份Writer成果作为最终交付物",
    "## 验收通过",
    (
        "验收通过。\n批准上一份 Writer 成果"
        "（artifact_state:state_generic）作为最终交付物。"
    ),
    "## Review approved\nAccept the previous output as the final deliverable.",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify compact reviewer approval resolution."
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

    classified = [
        is_prior_artifact_approval(variant)
        for variant in APPROVAL_VARIANTS
    ]
    checks.append(
        _check(
            "compact_approval_variants_are_reference_approvals",
            all(classified),
            json.dumps(classified),
        )
    )

    artifact = (
        "## Complete domain artifact\n\n"
        "This artifact covers the current request, evidence, constraints, "
        "decisions, risks, owners, and acceptance criteria."
    )
    resolution_rows: list[dict[str, Any]] = []
    for variant in APPROVAL_VARIANTS:
        resolved = resolve_final_artifact(
            [
                TextMessage(content=artifact, source="DomainBuilder42"),
                TextMessage(
                    content=f"{variant}\n{MARKER}",
                    source="quality_gate",
                ),
            ],
            marker=MARKER,
            reviewer_source="quality_gate",
        )
        resolution_rows.append(
            {
                "resolved": resolved is not None,
                "origin_source": (
                    resolved.origin_source if resolved is not None else ""
                ),
                "resolution_kind": (
                    resolved.resolution_kind if resolved is not None else ""
                ),
            }
        )
    checks.append(
        _check(
            "compact_approvals_promote_arbitrary_agent_artifact",
            all(
                row["resolved"]
                and row["origin_source"] == "DomainBuilder42"
                and row["resolution_kind"] == "prior_artifact_approved"
                for row in resolution_rows
            ),
            json.dumps(resolution_rows, ensure_ascii=False),
        )
    )

    no_candidate = resolve_final_artifact(
        [
            TextMessage(
                content=f"## 验收通过\n{MARKER}",
                source="quality_gate",
            )
        ],
        marker=MARKER,
        reviewer_source="quality_gate",
    )
    checks.append(
        _check(
            "approval_without_candidate_cannot_create_artifact",
            no_candidate is None,
            f"resolved={no_candidate is not None}",
        )
    )

    revision = assess_final_delivery(
        request="Deliver the complete current artifact.",
        content=(
            "## 验收不通过\n"
            "当前成果缺少证据表，必须补充后重新提交。\n"
            f"{MARKER}"
        ),
        source="quality_gate",
        marker=MARKER,
        require_marker=True,
    )
    checks.append(
        _check(
            "revision_feedback_is_not_approval",
            not revision.approved_prior_artifact
            and revision.review_only
            and not revision.valid,
            json.dumps(revision.to_dict(), ensure_ascii=False),
        )
    )

    complete_artifact = (
        "## 验收通过\n\n"
        "以下是可直接交付的完整报告正文。报告包含范围、方法、证据、"
        "发现、风险等级、修复责任人和验收标准，并逐项回答当前要求。"
    )
    checks.append(
        _check(
            "complete_artifact_is_not_misclassified_as_reference_note",
            not is_prior_artifact_approval(complete_artifact),
            (
                "classified="
                f"{is_prior_artifact_approval(complete_artifact)}"
            ),
        )
    )

    passed_count = sum(bool(item["passed"]) for item in checks)
    return {
        "summary": {
            "passed": passed_count == len(checks),
            "check_count": len(checks),
            "passed_check_count": passed_count,
            "approval_variant_count": len(APPROVAL_VARIANTS),
            "promoted_variant_count": sum(
                bool(row["resolved"]) for row in resolution_rows
            ),
        },
        "checks": checks,
        "resolution_rows": resolution_rows,
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
        "# v5.14o 紧凑审批语义验收",
        "",
        f"- 总体通过：`{summary.get('passed')}`",
        (
            f"- 检查项：`{summary.get('passed_check_count')}/"
            f"{summary.get('check_count')}`"
        ),
        f"- 审批变体：`{summary.get('approval_variant_count')}`",
        f"- 成功晋升：`{summary.get('promoted_variant_count')}`",
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
