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

from agent_runtime.drivers.autogen import _memory_adoption_evidence
from agent_runtime.memory.memory_store import MemoryStoreLite


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify generic fact-level TLC-Memory behavior for v5.13x."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def write_claim(
    store: MemoryStoreLite,
    *,
    task_id: str,
    agent_id: str,
    subject: str,
    slot_id: str,
    scope: str,
    value: str,
    value_type: str = "string",
    unit: str = "",
    confidence: float = 0.9,
    revision_kind: str = "asserted",
):
    return store.write_memory_candidate_with_report(
        task_id=task_id,
        source_agent=agent_id,
        task_topic=subject,
        memory_card={
            "summary": f"{scope}={value}",
            "confidence": 0.9,
            "importance_hint": 0.8,
            "coverage_score": 0.8,
            "reuse_scope": ["acceptance-chain"],
            "slot_hint": slot_id,
        },
        claim_cards=[
            {
                "subject": subject,
                "raw_slot_text": scope,
                "slot_id": slot_id,
                "scope": scope,
                "value": value,
                "value_type": value_type,
                "unit": unit,
                "raw_text": f"{scope}={value}",
                "summary": f"{scope}={value}",
                "certainty": "confirmed",
                "modality": "asserted",
                "polarity": "positive",
                "confidence": confidence,
                "revision_kind": revision_kind,
                "source_pointer": f"state:{task_id}",
            }
        ],
        tags=["acceptance-chain", subject, task_id],
        slot_hint=slot_id,
        source_state_ids=[f"state_{task_id}"],
        evidence_refs=[f"evidence_{task_id}"],
        reuse_intent="reuse in the same task chain",
        fallback_summary=f"{scope}={value}",
    )


def verify() -> dict[str, Any]:
    checks: list[Check] = []
    with tempfile.TemporaryDirectory() as tmp:
        storage_dir = Path(tmp)
        store = MemoryStoreLite(storage_dir=storage_dir)

        config_old = write_claim(
            store,
            task_id="C1",
            agent_id="ConfigurationAuthor",
            subject="project:runtime-demo",
            slot_id="slot.runtime.config",
            scope="config.busy_timeout",
            value="5000",
            value_type="integer",
            unit="ms",
        )
        config_new = write_claim(
            store,
            task_id="C2",
            agent_id="ReleaseSafetyInspector",
            subject="project:runtime-demo",
            slot_id="slot.runtime.config",
            scope="config.busy_timeout",
            value="2000",
            value_type="integer",
            unit="ms",
            revision_kind="replaces",
        )
        config_guard = store.revision_guard(config_new.memory_ref)
        config_prompt = store.render_prompt_view(
            config_new.memory_ref,
            budget_chars=1600,
        )
        add(
            checks,
            "configuration_revision_forms_active_and_history",
            config_new.conflict_detected_count == 1
            and config_new.resolved_conflict_count == 1
            and config_guard["active_facts"][0]["value"] == "2000"
            and config_guard["historical_facts"][0]["value"] == "5000",
            json.dumps(config_guard, ensure_ascii=False, sort_keys=True),
        )
        add(
            checks,
            "prompt_contains_only_active_configuration_value",
            "active_value=2000" in config_prompt
            and "active_value=5000" not in config_prompt,
            config_prompt,
        )

        plan_old = write_claim(
            store,
            task_id="P1",
            agent_id="ConstraintCollector",
            subject="plan:generic-demo",
            slot_id="slot.project.requirement",
            scope="constraint.daily_activity_limit",
            value="5",
            value_type="integer",
            unit="activity",
        )
        plan_new = write_claim(
            store,
            task_id="P2",
            agent_id="ScheduleComposer",
            subject="plan:generic-demo",
            slot_id="slot.project.requirement",
            scope="constraint.daily_activity_limit",
            value="3",
            value_type="integer",
            unit="activity",
            revision_kind="replaces",
        )
        plan_guard = store.revision_guard(plan_new.memory_ref)
        add(
            checks,
            "second_domain_uses_the_same_generic_revision_path",
            plan_new.resolved_conflict_count == 1
            and plan_guard["active_facts"][0]["value"] == "3"
            and plan_guard["historical_facts"][0]["value"] == "5",
            json.dumps(plan_guard, ensure_ascii=False, sort_keys=True),
        )
        add(
            checks,
            "arbitrary_agent_names_are_preserved",
            store._claims[
                store._memories[plan_old.memory_ref.memory_id].claim_id
            ].source_agent
            == "ConstraintCollector"
            and store._claims[
                store._memories[plan_new.memory_ref.memory_id].claim_id
            ].source_agent
            == "ScheduleComposer",
            "ConstraintCollector -> ScheduleComposer",
        )

        repeated = write_claim(
            store,
            task_id="C3",
            agent_id="IndependentVerifier",
            subject="project:runtime-demo",
            slot_id="slot.runtime.config",
            scope="config.busy_timeout",
            value="2000",
            value_type="integer",
            unit="ms",
        )
        repeated_view = store._views[repeated.memory_ref.memory_view_id]
        add(
            checks,
            "same_value_merges_evidence_without_new_history",
            repeated.conflict_detected_count == 0
            and len(repeated_view.active_claim_ids) == 2
            and len(repeated_view.historical_claim_ids) == 1,
            json.dumps(asdict(repeated_view), ensure_ascii=False, sort_keys=True),
        )

        other_scope = write_claim(
            store,
            task_id="C4",
            agent_id="ConnectionTuner",
            subject="project:runtime-demo",
            slot_id="slot.runtime.config",
            scope="config.connection_timeout",
            value="2000",
            value_type="integer",
            unit="ms",
        )
        add(
            checks,
            "different_scope_creates_a_separate_memory_view",
            other_scope.memory_view_ids[0] != config_new.memory_view_ids[0]
            and other_scope.conflict_detected_count == 0,
            (
                f"config={config_new.memory_view_ids[0]}, "
                f"connection={other_scope.memory_view_ids[0]}"
            ),
        )

        unresolved_scope = write_claim(
            store,
            task_id="U1",
            agent_id="AmbiguousCollector",
            subject="project:runtime-demo",
            slot_id="slot.project.requirement",
            scope="general",
            value="unknown",
        )
        add(
            checks,
            "required_scope_is_blocked",
            unresolved_scope.admission_status == "unresolved_scope"
            and unresolved_scope.memory_write_count == 0,
            json.dumps(asdict(unresolved_scope), ensure_ascii=False, sort_keys=True),
        )

        conflict_a = write_claim(
            store,
            task_id="E1",
            agent_id="EvidenceReaderEast",
            subject="paper:generic-demo",
            slot_id="slot.paper.evidence",
            scope="evidence.primary_result",
            value="supported",
            confidence=0.8,
        )
        conflict_b = write_claim(
            store,
            task_id="E2",
            agent_id="EvidenceReaderWest",
            subject="paper:generic-demo",
            slot_id="slot.paper.evidence",
            scope="evidence.primary_result",
            value="rejected",
            confidence=0.8,
        )
        conflict_view = store._views[conflict_a.memory_view_ids[0]]
        add(
            checks,
            "unresolved_conflict_is_not_retrievable",
            conflict_b.unresolved_conflict_count == 1
            and conflict_view.active_value is None
            and not store.search_memory(
                "primary result",
                tags=["acceptance-chain"],
                required_tags=["paper:generic-demo"],
                top_k=10,
            ),
            json.dumps(asdict(conflict_view), ensure_ascii=False, sort_keys=True),
        )

        common = {
            "memory_prompt_view": config_prompt,
            "injected_prompt_view": config_prompt,
            "current_task_text": "Return the final runtime configuration.",
            "memory_id": config_new.memory_ref.memory_id,
            "memory_view_id": config_new.memory_ref.memory_view_id,
            "revision_guard": config_guard,
        }
        adoption = {
            "useful": _memory_adoption_evidence(
                output_text="Use busy_timeout=2000.",
                **common,
            ),
            "wrong": _memory_adoption_evidence(
                output_text="Use busy_timeout=5000.",
                **common,
            ),
            "mixed": _memory_adoption_evidence(
                output_text="Use busy_timeout=2000 and busy_timeout=5000.",
                **common,
            ),
            "negated_old": _memory_adoption_evidence(
                output_text=(
                    "Use busy_timeout=2000. Do not use busy_timeout=5000."
                ),
                **common,
            ),
        }
        statuses = {name: row["status"] for name, row in adoption.items()}
        add(
            checks,
            "structured_adoption_classifier_is_value_and_polarity_aware",
            statuses
            == {
                "useful": "useful",
                "wrong": "wrong",
                "mixed": "mixed",
                "negated_old": "useful",
            },
            json.dumps(statuses, ensure_ascii=False, sort_keys=True),
        )

        prompt_views = store.get_prompt_view(
            config_guard["semantic_key"],
            budget_chars=1600,
        )
        audit_view = store.get_audit_view(
            config_new.memory_ref.memory_view_id,
            budget_chars=12000,
        )
        old_claim_id = store._memories[
            config_old.memory_ref.memory_id
        ].claim_id
        evidence_view = store.expand_evidence(
            config_new.memory_ref.memory_view_id,
            old_claim_id,
            budget_chars=12000,
        )
        add(
            checks,
            "prompt_audit_and_evidence_views_are_separated",
            len(prompt_views) == 1
            and "active_value=2000" in prompt_views[0]
            and '"value": "5000"' in audit_view
            and '"view_type": "evidence_expansion"' in evidence_view,
            (
                f"prompt_chars={len(prompt_views[0]) if prompt_views else 0}, "
                f"audit_chars={len(audit_view)}, evidence_chars={len(evidence_view)}"
            ),
        )

        runtime_dir = REPO_ROOT / "agent_runtime"
        runtime_text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in runtime_dir.rglob("*.py")
        ).casefold()
        forbidden = (
            "question_a.md",
            "question_d.md",
            "莫干山",
            "皖南",
            "首日十点后出发",
            "三个候选项",
        )
        found = [term for term in forbidden if term.casefold() in runtime_text]
        add(
            checks,
            "runtime_has_no_experiment_specific_rules",
            not found,
            f"found={found}",
        )

    passed_count = sum(1 for check in checks if check.passed)
    return {
        "report_version": "v5.13x",
        "schema_version": "agentlite.v5.13x-fact-memory.v1",
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
        "# v5.13x Fact-Level Memory Acceptance",
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


def add(checks: list[Check], name: str, passed: bool, detail: str) -> None:
    checks.append(Check(name=name, passed=bool(passed), detail=detail))


def main() -> int:
    args = parse_args()
    report = verify()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "fact_memory_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "fact_memory_report.md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
