from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.core.communication import CapabilityProfileManagerLite
from agent_runtime.drivers.autogen import _memory_adoption_evidence
from agent_runtime.memory.context_views import (
    build_minimal_context_view,
    consumer_context_from_profile,
)
from agent_runtime.memory.memory_store import MemoryStoreLite


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify generic conflict-aware memory behavior for v5.13w."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = verify()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "conflict_guard_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "conflict_guard_report.md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


def verify() -> dict[str, Any]:
    checks: list[Check] = []
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStoreLite(storage_dir=Path(tmp))
        old = store.write_memory_with_report(
            task_id="R1",
            source_agent="InitialConfigurator",
            task_topic="runtime database configuration",
            summary="database busy_timeout=5000 ms",
            tags=["runtime", "database"],
            slot_hint="reuse_strategy",
            confidence=0.72,
        )
        active = store.write_memory_with_report(
            task_id="R2",
            source_agent="ConfigurationVerifier",
            task_topic="runtime database configuration",
            summary="database busy_timeout=2000 ms",
            tags=["runtime", "database"],
            slot_hint="reuse_strategy",
            confidence=0.93,
        )
        prompt_view = store.render_prompt_view(active.memory_ref, budget_chars=1200)
        guard = store.revision_guard(active.memory_ref)

        _add(
            checks,
            "superseded_claim_removed_from_active_prompt",
            "busy_timeout=2000" in prompt_view
            and "busy_timeout=5000" not in prompt_view,
            prompt_view,
        )
        _add(
            checks,
            "revision_guard_generated",
            "[revision_guard" in prompt_view
            and guard.get("required") is True
            and len(guard.get("historical_claims") or []) == 1,
            (
                f"required={guard.get('required')}, "
                f"historical={len(guard.get('historical_claims') or [])}"
            ),
        )

        profiles = CapabilityProfileManagerLite([])
        profile = profiles.register_or_update(
            agent_id="ReleaseSafetyInspector",
            role="Configuration safety specialist",
            role_description="Validate current runtime settings before release.",
        )
        consumer = consumer_context_from_profile(
            profile,
            consumer_id="ReleaseSafetyInspector",
        )
        role_view = build_minimal_context_view(
            query="List release readiness evidence only.",
            prompt_views=[prompt_view],
            consumer=consumer,
            action="REVIEW_OUTPUT",
            budget_chars=160,
        )
        _add(
            checks,
            "dynamic_consumer_retains_revision_guard",
            role_view.consumer_id == "ReleaseSafetyInspector"
            and "[revision_guard" in role_view.text,
            role_view.text,
        )

        common = {
            "memory_prompt_view": prompt_view,
            "injected_prompt_view": role_view.text,
            "current_task_text": "Provide the final database configuration.",
            "memory_id": active.memory_ref.memory_id,
            "memory_view_id": active.memory_ref.memory_view_id,
            "revision_guard": guard,
        }
        evidence = {
            "useful": _memory_adoption_evidence(
                output_text="Use database busy_timeout=2000 ms.",
                **common,
            ),
            "wrong": _memory_adoption_evidence(
                output_text="Use database busy_timeout=5000 ms.",
                **common,
            ),
            "mixed": _memory_adoption_evidence(
                output_text=(
                    "Use database busy_timeout=2000 ms, while a fallback keeps "
                    "busy_timeout=5000 ms."
                ),
                **common,
            ),
            "unassessed": _memory_adoption_evidence(
                output_text="The release checklist is ready for review.",
                **common,
            ),
            "negated_old": _memory_adoption_evidence(
                output_text=(
                    "Use database busy_timeout=2000 ms, not the old 5000 ms value."
                ),
                **common,
            ),
        }
        statuses = {name: row["status"] for name, row in evidence.items()}
        _add(
            checks,
            "four_way_classifier_detects_clean_wrong_and_mixed",
            statuses["useful"] == "useful"
            and statuses["wrong"] == "wrong"
            and statuses["mixed"] == "mixed"
            and statuses["unassessed"] == "unassessed",
            json.dumps(statuses, ensure_ascii=False, sort_keys=True),
        )
        _add(
            checks,
            "negated_historical_value_not_misclassified",
            statuses["negated_old"] == "useful"
            and evidence["negated_old"]["matched_historical_fact_count"] == 0,
            json.dumps(evidence["negated_old"], ensure_ascii=False, sort_keys=True),
        )

        store.record_useful_hits([active.memory_ref.memory_id])
        store.record_wrong_hits([active.memory_ref.memory_id])
        store.record_mixed_hits([active.memory_ref.memory_id])
        memory = next(
            item
            for item in store.snapshot()["memories"]
            if item["memory_id"] == active.memory_ref.memory_id
        )
        _add(
            checks,
            "feedback_counters_persist",
            memory["useful_hit_count"] == 1
            and memory["wrong_hit_count"] == 1
            and memory["mixed_hit_count"] == 1,
            json.dumps(memory, ensure_ascii=False, sort_keys=True),
        )

        runtime_files = (
            Path(__file__).resolve().parents[2]
            / "agent_runtime"
        )
        runtime_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in runtime_files.rglob("*.py")
        ).casefold()
        forbidden = (
            "question_a.md",
            "莫干山",
            "皖南",
            "首日十点后出发",
            "三个候选项",
        )
        found = [term for term in forbidden if term.casefold() in runtime_text]
        _add(
            checks,
            "runtime_has_no_experiment_specific_rules",
            not found,
            f"found={found}",
        )

        old_memory = store._memories[old.memory_ref.memory_id]
        _add(
            checks,
            "old_memory_remains_audit_only",
            old_memory.status == "superseded",
            f"status={old_memory.status}",
        )

    passed_count = sum(1 for check in checks if check.passed)
    return {
        "report_version": "v5.13w",
        "summary": {
            "passed": passed_count == len(checks),
            "passed_check_count": passed_count,
            "check_count": len(checks),
        },
        "checks": [asdict(check) for check in checks],
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.13w Conflict-Aware Memory Acceptance",
        "",
        f"- Passed: `{summary['passed']}`",
        (
            f"- Checks: `{summary['passed_check_count']}/"
            f"{summary['check_count']}`"
        ),
        "",
        "| Check | Passed | Detail |",
        "|---|---:|---|",
    ]
    for check in report["checks"]:
        detail = str(check["detail"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{check['name']}` | `{check['passed']}` | {detail[:500]} |"
        )
    return "\n".join(lines) + "\n"


def _add(checks: list[Check], name: str, passed: bool, detail: str) -> None:
    checks.append(Check(name=name, passed=bool(passed), detail=detail))


if __name__ == "__main__":
    raise SystemExit(main())
