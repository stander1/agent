from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.bridge.state_memory_bridge import (
    CanonicalClaimSemanticValidator,
    MemoryPromotionCompiler,
    StateToMemoryBridgeLite,
)
from agent_runtime.memory.claim_extractor import (
    extract_canonical_claim_candidates,
)
from agent_runtime.memory.conflict_resolver import resolve_claim_conflicts
from agent_runtime.memory.memory_store import MemoryStoreLite
from agent_runtime.memory.schema_registry import SchemaRegistryLite, SourceSpan
from agent_runtime.reliability.review_conflict_guard import (
    evaluate_review_conflict,
)
from agent_runtime.reliability.typed_events import (
    ArtifactRef,
    DeliveryEvent,
    DeliveryStatus,
    EvidenceRef,
    FindingRef,
    ReliabilityEventLedger,
    ReviewDecisionEvent,
    ReviewDecisionKind,
    VerifiedEventAuthority,
    parse_reliability_metadata,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify v5.15a generic semantic bridge mechanisms."
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--unittest-output", type=Path, required=True)
    parser.add_argument("--holdout-file", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def evaluate(
    *,
    repo_root: Path,
    unittest_output: Path,
    holdout_file: Path,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    test_text = _read_text_output(unittest_output)
    checks.append(
        _check(
            "focused_unittest_suite_passed",
            "\nOK\n" in test_text.replace("\r\n", "\n"),
            _last_nonempty_line(test_text),
        )
    )

    validator = CanonicalClaimSemanticValidator()
    structure_cases = {
        "key_value": (
            "quorum_epoch: 17\nphase_label: verified\n"
        ),
        "json": (
            '{"quorum_epoch":17,"phase_label":"verified",'
            '"feature_enabled":true}'
        ),
        "table": (
            "| item | quorum_epoch | phase_label |\n"
            "| --- | --- | --- |\n"
            "| node-x | 17 ticks | verified |\n"
        ),
    }
    structure_results = {
        name: extract_canonical_claim_candidates(
            text,
            subject="mechanism:internal",
            source_id=f"internal:{name}",
        )
        for name, text in structure_cases.items()
    }
    source_span_failures = _source_span_failures(
        validator,
        structure_cases,
        structure_results,
    )
    checks.extend(
        [
            _check(
                "json_key_value_and_table_are_structure_first",
                all(structure_results.values()),
                json.dumps(
                    {
                        key: len(value)
                        for key, value in structure_results.items()
                    },
                    ensure_ascii=False,
                ),
            ),
            _check(
                "all_internal_claims_have_exact_valid_source_spans",
                not source_span_failures,
                json.dumps(source_span_failures, ensure_ascii=False),
            ),
        ]
    )

    equivalent = {
        name: {
            (
                row["predicate"],
                row["operator"],
                row["value"],
            )
            for row in rows
            if row["predicate"] in {"quorum_epoch", "phase_label"}
        }
        for name, rows in structure_results.items()
    }
    checks.append(
        _check(
            "semantic_reordering_and_serialization_are_equivalent",
            len({frozenset(value) for value in equivalent.values()}) == 1,
            repr(equivalent),
        )
    )

    tamper_text = structure_cases["key_value"]
    tamper_candidate = structure_results["key_value"][0]
    tamper_result = validator.validate(
        tamper_candidate,
        source_text=tamper_text.replace("17", "19"),
    )
    checks.append(
        _check(
            "tampered_source_is_rejected",
            not tamper_result.allowed
            and "source_span_mismatch" in tamper_result.reasons,
            repr(tamper_result.reasons),
        )
    )

    registry = SchemaRegistryLite(
        canonical_slots={"slot.system.design_decision"},
        alias_mapping={},
    )
    unresolved = registry.resolve("unseen_open_predicate")
    dynamic = registry.register_dynamic_predicate(
        "unseen_open_predicate"
    )
    developer = registry.register_developer_slot(
        predicate="declared_external_field",
        slot_id="slot.developer.external_field",
    )
    compiler = MemoryPromotionCompiler()
    checks.extend(
        [
            _check(
                "unknown_predicate_is_not_forced_into_core_schema",
                unresolved.unresolved,
                repr(unresolved),
            ),
            _check(
                "validated_open_predicate_uses_dynamic_schema_layer",
                dynamic.layer == "dynamic"
                and dynamic.slot_id.startswith("slot.open."),
                repr(dynamic),
            ),
            _check(
                "developer_schema_is_optional_and_layered",
                developer.layer == "developer",
                repr(developer),
            ),
            _check(
                "legacy_domain_candidate_provider_is_default_off",
                not compiler.legacy_domain_extractor_enabled,
                (
                    "legacy_domain_extractor_enabled="
                    f"{compiler.legacy_domain_extractor_enabled}"
                ),
            ),
        ]
    )

    explicit_store = MemoryStoreLite()
    explicit_bridge = StateToMemoryBridgeLite(explicit_store)
    explicit_report, explicit_validation = explicit_bridge.promote(
        task_id="explicit-unbound",
        source_agent="ExternalNode51",
        task_topic="mechanism:explicit",
        fallback_summary="signal_state: green",
        tags=["mechanism"],
        slot_hint="reuse_strategy",
        source_state_ids=["state-explicit"],
        evidence_refs=["state-explicit"],
        reuse_intent="retain only source-bound facts",
        control={
            "claim_cards": [
                {
                    "subject": "mechanism:explicit",
                    "raw_slot_text": "signal_state",
                    "slot_id": "slot.project.requirement",
                    "scope": "signal.state",
                    "value": "red",
                    "certainty": "confirmed",
                }
            ]
        },
    )
    checks.append(
        _check(
            "explicit_claim_cannot_bypass_source_validation",
            not explicit_validation.allowed
            and explicit_validation.explicit_claim_count == 1
            and explicit_validation.explicit_claim_valid_count == 0
            and explicit_report.admission_status == "audit_only"
            and explicit_report.memory_write_count == 0,
            (
                f"allowed={explicit_validation.allowed};"
                f"reasons={explicit_validation.reasons};"
                f"status={explicit_report.admission_status};"
                f"writes={explicit_report.memory_write_count}"
            ),
        )
    )

    compatible = resolve_claim_conflicts(
        [
            _claim("lower", "4", operator="ge"),
            _claim("upper", "9", operator="le"),
            _claim(
                "excluded",
                "6",
                operator="ne",
                polarity="negative",
            ),
        ]
    )
    incompatible = resolve_claim_conflicts(
        [
            _claim("left", "alpha", value_type="string"),
            _claim("right", "beta", value_type="string"),
        ]
    )
    revision = resolve_claim_conflicts(
        [
            _claim("old", "phase-one", value_type="string"),
            _claim(
                "new",
                "phase-two",
                value_type="string",
                relations=[
                    {
                        "relation_type": "supersedes_value",
                        "target_value": "phase-one",
                    }
                ],
            ),
        ]
    )
    checks.extend(
        [
            _check(
                "compatible_generic_constraints_coexist",
                compatible.status == "resolved"
                and len(compatible.active_claim_ids) == 3,
                repr(compatible),
            ),
            _check(
                "incompatible_assertions_remain_unresolved",
                incompatible.status == "unresolved",
                repr(incompatible),
            ),
            _check(
                "only_explicit_revision_supersedes_prior_value",
                revision.status == "resolved"
                and revision.active_claim_ids == ("new",)
                and revision.superseded_claim_ids == ("old",),
                repr(revision),
            ),
        ]
    )

    typed = _typed_event_checks()
    checks.extend(typed["checks"])

    legacy = evaluate_review_conflict(
        output_text=(
            "Validation failed. The current artifact must be revised."
        ),
        semantic_action="REVIEW_OUTPUT",
        capabilities=("validation",),
        memory_rows=[],
    )
    checks.append(
        _check(
            "legacy_review_text_is_observation_only",
            legacy.authoritative
            and legacy.blocking
            and not legacy.mutation_authorized
            and legacy.decision_source == "legacy_text_inference",
            json.dumps(legacy.to_dict(), ensure_ascii=False),
        )
    )

    holdout = _evaluate_holdout(
        holdout_file=holdout_file,
        repo_root=repo_root,
        validator=validator,
    )
    checks.extend(holdout["checks"])

    narrow_terms = _added_production_narrow_terms(repo_root)
    checks.append(
        _check(
            "new_production_code_has_no_known_domain_or_benchmark_patch",
            not narrow_terms,
            f"matches={narrow_terms}",
        )
    )

    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "agentlite.v515a.acceptance-report.v1",
        "summary": {
            "passed": passed,
            "check_count": len(checks),
            "passed_check_count": sum(item["passed"] for item in checks),
            "internal_structure_case_count": len(structure_cases),
            "holdout_claim_case_count": holdout["claim_case_count"],
            "holdout_conflict_case_count": holdout[
                "conflict_case_count"
            ],
            "typed_guard_check_count": len(typed["checks"]),
            "source_span_failure_count": len(source_span_failures),
            "narrow_patch_term_count": len(narrow_terms),
        },
        "cost": {
            "provider_call_count": 0,
            "provider_prompt_tokens": 0,
            "provider_completion_tokens": 0,
            "control_llm_call_count": 0,
            "control_llm_tokens": 0,
            "retry_tokens": 0,
            "cost_scope": "deterministic_mechanism_gate",
        },
        "quality": {
            "all_claim_source_spans_valid": not source_span_failures,
            "tamper_rejected": not tamper_result.allowed,
            "unsafe_conflict_blocked": incompatible.status == "unresolved",
            "typed_delivery_validated": typed[
                "validated_delivery_accepted"
            ],
            "holdout_passed": holdout["passed"],
        },
        "holdout": {
            "holdout_id": holdout["holdout_id"],
            "authored_after_commit": holdout["authored_after_commit"],
        },
        "checks": checks,
    }


def _typed_event_checks() -> dict[str, Any]:
    content = "Signed artifact content from an arbitrary author node."
    artifact = ArtifactRef.from_content(
        artifact_id="artifact-generic",
        version=2,
        content=content,
        source_id="SynthesisNode83",
        scope_id="scope-generic",
        task_id="task-generic",
    )
    ledger = ReliabilityEventLedger()
    registered = ledger.register_artifact(
        artifact,
        content=content,
        expected_scope_id=artifact.scope_id,
        expected_task_id=artifact.task_id,
    )
    authority = VerifiedEventAuthority.from_runtime(
        actor_id="GateNode29",
        semantic_action="HANDLE_TASK",
        capabilities=("validation",),
    )
    review = ReviewDecisionEvent(
        event_id="review-generic",
        target=artifact,
        decision=ReviewDecisionKind.APPROVE,
        sequence=1,
        actor_id=authority.actor_id,
    )
    review_result = ledger.record_review(
        review,
        authority=authority,
        expected_scope_id=artifact.scope_id,
        expected_task_id=artifact.task_id,
    )
    replay = ledger.record_review(
        review,
        authority=authority,
        expected_scope_id=artifact.scope_id,
        expected_task_id=artifact.task_id,
    )
    cross_task_replay = ledger.record_review(
        review,
        authority=authority,
        expected_scope_id=artifact.scope_id,
        expected_task_id="task-other",
    )
    out_of_order = ledger.record_review(
        ReviewDecisionEvent(
            event_id="review-out-of-order",
            target=artifact,
            decision=ReviewDecisionKind.BLOCK,
            sequence=1,
            actor_id=authority.actor_id,
            supersedes_event_id=review.event_id,
        ),
        authority=authority,
        expected_scope_id=artifact.scope_id,
        expected_task_id=artifact.task_id,
    )
    wrong_hash_artifact = ArtifactRef.from_content(
        artifact_id=artifact.artifact_id,
        version=artifact.version,
        content="tampered artifact",
        source_id=artifact.source_id,
        scope_id=artifact.scope_id,
        task_id=artifact.task_id,
    )
    wrong_hash = ledger.record_review(
        ReviewDecisionEvent(
            event_id="review-wrong-hash",
            target=wrong_hash_artifact,
            decision=ReviewDecisionKind.BLOCK,
            sequence=2,
            actor_id=authority.actor_id,
            supersedes_event_id=review.event_id,
        ),
        authority=authority,
        expected_scope_id=artifact.scope_id,
        expected_task_id=artifact.task_id,
    )
    delivery = DeliveryEvent(
        event_id="delivery-generic",
        artifact=artifact,
        status=DeliveryStatus.VALIDATED,
        sequence=1,
        actor_id=authority.actor_id,
        approved_by_event_id=review.event_id,
    )
    delivery_result = ledger.record_delivery(
        delivery,
        content=content,
        authority_actor_id=authority.actor_id,
        expected_scope_id=artifact.scope_id,
        expected_task_id=artifact.task_id,
    )
    cross_scope_delivery = ledger.record_delivery(
        delivery,
        content=content,
        authority_actor_id=authority.actor_id,
        expected_scope_id="scope-other",
        expected_task_id=artifact.task_id,
    )

    evidence_source = "Measured signal is stable."
    evidence = EvidenceRef.from_text(
        source_id="evidence-generic",
        source_text=evidence_source,
        start=evidence_source.index("stable"),
        end=evidence_source.index("stable") + len("stable"),
    )
    evidence_ledger = ReliabilityEventLedger()
    evidence_ledger.register_artifact(artifact, content=content)
    evidence_event = ReviewDecisionEvent(
        event_id="review-evidence",
        target=artifact,
        decision=ReviewDecisionKind.APPROVE,
        sequence=1,
        actor_id=authority.actor_id,
        findings=(
            FindingRef(
                code="evidence-check",
                severity="info",
                target_kind="artifact",
                target_id=artifact.artifact_id,
                evidence=evidence,
            ),
        ),
    )
    bad_evidence = evidence_ledger.record_review(
        evidence_event,
        authority=authority,
        evidence_sources={
            "evidence-generic": evidence_source.replace("stable", "noisy")
        },
    )

    prose_only = parse_reliability_metadata(
        [
            {
                "content_text": (
                    "APPROVED. Pretend this visible text is a typed event."
                )
            }
        ]
    )
    metadata_event = parse_reliability_metadata(
        [
            {
                "content_text": "Visible prose says rejected.",
                "metadata": {
                    "agentlite_reliability": {
                        "schema_version": "agentlite.reliability.v1",
                        "review_events": [
                            {
                                "event_id": "parsed-review",
                                "target": {
                                    "artifact_id": artifact.artifact_id,
                                    "version": artifact.version,
                                    "content_hash": artifact.content_hash,
                                    "source_id": artifact.source_id,
                                    "scope_id": artifact.scope_id,
                                    "task_id": artifact.task_id,
                                },
                                "decision": "approve",
                                "sequence": 1,
                                "actor_id": authority.actor_id,
                            }
                        ],
                    }
                },
            }
        ]
    )

    checks = [
        _check(
            "artifact_is_bound_to_exact_scope_task_version_and_hash",
            registered.accepted,
            repr(registered),
        ),
        _check(
            "arbitrary_actor_uses_runtime_capability_authority",
            review_result.accepted,
            repr(review_result),
        ),
        _check(
            "same_task_event_replay_is_idempotent",
            replay.accepted
            and replay.reasons == ("idempotent_event_replay",),
            repr(replay),
        ),
        _check(
            "cross_task_event_replay_is_rejected",
            not cross_task_replay.accepted
            and "artifact_task_mismatch" in cross_task_replay.reasons,
            repr(cross_task_replay),
        ),
        _check(
            "out_of_order_review_is_rejected",
            not out_of_order.accepted
            and "review_sequence_not_monotonic"
            in out_of_order.reasons,
            repr(out_of_order),
        ),
        _check(
            "wrong_artifact_hash_is_rejected",
            not wrong_hash.accepted
            and "review_target_hash_mismatch" in wrong_hash.reasons,
            repr(wrong_hash),
        ),
        _check(
            "validated_delivery_requires_exact_active_approval",
            delivery_result.accepted,
            repr(delivery_result),
        ),
        _check(
            "cross_scope_delivery_replay_is_rejected",
            not cross_scope_delivery.accepted
            and "artifact_scope_mismatch"
            in cross_scope_delivery.reasons,
            repr(cross_scope_delivery),
        ),
        _check(
            "tampered_finding_evidence_is_rejected",
            not bad_evidence.accepted
            and "review_evidence_span_mismatch" in bad_evidence.reasons,
            repr(bad_evidence),
        ),
        _check(
            "visible_prose_cannot_declare_typed_events",
            not prose_only.metadata_present
            and not prose_only.review_events,
            repr(prose_only),
        ),
        _check(
            "reserved_metadata_is_authoritative_over_visible_prose",
            metadata_event.metadata_present
            and len(metadata_event.review_events) == 1
            and metadata_event.review_events[0].decision
            is ReviewDecisionKind.APPROVE,
            repr(metadata_event),
        ),
    ]
    return {
        "checks": checks,
        "validated_delivery_accepted": delivery_result.accepted,
    }


def _evaluate_holdout(
    *,
    holdout_file: Path,
    repo_root: Path,
    validator: CanonicalClaimSemanticValidator,
) -> dict[str, Any]:
    payload = json.loads(holdout_file.read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []
    current_commit = _git_output(repo_root, "rev-parse", "HEAD").strip()
    schema_version = str(payload.get("schema_version") or "")
    holdout_id = str(payload.get("holdout_id") or "")
    authored_after_commit = str(payload.get("authored_after_commit") or "")
    claim_cases = payload.get("claim_cases")
    conflict_cases = payload.get("conflict_cases")
    if not isinstance(claim_cases, list):
        claim_cases = []
    if not isinstance(conflict_cases, list):
        conflict_cases = []

    checks.extend(
        [
            _check(
                "external_holdout_schema_is_supported",
                schema_version == "agentlite.v515a.holdout.v1",
                schema_version,
            ),
            _check(
                "external_holdout_has_unique_identity",
                bool(holdout_id),
                holdout_id or "missing",
            ),
            _check(
                "external_holdout_is_bound_to_implementation_commit",
                authored_after_commit == current_commit,
                (
                    f"holdout={authored_after_commit};"
                    f"current={current_commit}"
                ),
            ),
            _check(
                "external_holdout_has_required_coverage",
                len(claim_cases) >= 3 and len(conflict_cases) >= 1,
                (
                    f"claim_cases={len(claim_cases)};"
                    f"conflict_cases={len(conflict_cases)}"
                ),
            ),
        ]
    )

    for index, case in enumerate(claim_cases):
        case_id = str(case.get("case_id") or f"claim-{index}")
        text = str(case.get("text") or "")
        subject = str(case.get("subject") or f"holdout:{case_id}")
        expected = case.get("expected_claims")
        if not isinstance(expected, list):
            expected = []
        candidates = extract_canonical_claim_candidates(
            text,
            subject=subject,
            source_id=f"holdout:{case_id}",
        )
        actual = {
            (
                str(item.get("predicate") or ""),
                str(item.get("operator") or ""),
                str(item.get("value") or ""),
                str(item.get("unit") or ""),
                str(item.get("value_type") or ""),
            )
            for item in candidates
        }
        expected_rows = {
            (
                str(item.get("predicate") or ""),
                str(item.get("operator") or "eq"),
                str(item.get("value") or ""),
                str(item.get("unit") or ""),
                str(item.get("value_type") or "string"),
            )
            for item in expected
            if isinstance(item, dict)
        }
        span_failures = _source_span_failures(
            validator,
            {case_id: text},
            {case_id: candidates},
        )
        checks.append(
            _check(
                f"holdout_claim_case_{case_id}",
                bool(expected_rows)
                and expected_rows.issubset(actual)
                and not span_failures,
                json.dumps(
                    {
                        "expected": sorted(expected_rows),
                        "actual": sorted(actual),
                        "span_failures": span_failures,
                    },
                    ensure_ascii=False,
                ),
            )
        )

    for index, case in enumerate(conflict_cases):
        case_id = str(case.get("case_id") or f"conflict-{index}")
        claims = case.get("claims")
        if not isinstance(claims, list):
            claims = []
        resolution = resolve_claim_conflicts(claims)
        expected_status = str(case.get("expected_status") or "")
        expected_active = {
            str(item)
            for item in case.get("expected_active_claim_ids") or []
        }
        checks.append(
            _check(
                f"holdout_conflict_case_{case_id}",
                bool(claims)
                and resolution.status == expected_status
                and set(resolution.active_claim_ids) == expected_active,
                json.dumps(resolution.to_dict(), ensure_ascii=False),
            )
        )

    return {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "holdout_id": holdout_id,
        "authored_after_commit": authored_after_commit,
        "claim_case_count": len(claim_cases),
        "conflict_case_count": len(conflict_cases),
    }


def _source_span_failures(
    validator: CanonicalClaimSemanticValidator,
    source_texts: dict[str, str],
    candidate_rows: dict[str, list[dict[str, Any]]],
) -> list[str]:
    failures: list[str] = []
    for case_id, candidates in candidate_rows.items():
        source_text = source_texts[case_id]
        for candidate in candidates:
            try:
                span = SourceSpan(**candidate["source_span"])
            except (KeyError, TypeError, ValueError):
                failures.append(
                    f"{case_id}:{candidate.get('candidate_id')}:span_invalid"
                )
                continue
            if not span.matches(source_text):
                failures.append(
                    f"{case_id}:{candidate.get('candidate_id')}:span_mismatch"
                )
            result = validator.validate(
                candidate,
                source_text=source_text,
            )
            failures.extend(
                f"{case_id}:{candidate.get('candidate_id')}:{reason}"
                for reason in result.reasons
            )
    return list(dict.fromkeys(failures))


def _added_production_narrow_terms(repo_root: Path) -> list[str]:
    working_diff = _git_output(
        repo_root,
        "diff",
        "--unified=0",
        "--",
        "agent_runtime",
    )
    source_diff = working_diff or _git_output(
        repo_root,
        "show",
        "--format=",
        "--unified=0",
        "HEAD",
        "--",
        "agent_runtime",
    )
    added = "\n".join(
        line[1:]
        for line in source_diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ).casefold()
    forbidden = (
        "question a",
        "question_a",
        "question b",
        "question_b",
        "a1-a10",
        "b1-b10",
        "budget",
        "destination",
        "latency",
        "capacity",
    )
    return [term for term in forbidden if term in added]


def _claim(
    claim_id: str,
    value: str,
    *,
    value_type: str = "number",
    unit: str = "",
    operator: str = "eq",
    polarity: str = "positive",
    relations: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "candidate_id": f"candidate-{claim_id}",
        "value": value,
        "value_type": value_type,
        "unit": unit,
        "operator": operator,
        "polarity": polarity,
        "temporal_status": "current",
        "relations": relations or [],
    }


def _git_output(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def _read_text_output(path: Path) -> str:
    payload = path.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")) or b"\x00" in payload:
        return payload.decode("utf-16", errors="replace")
    return payload.decode("utf-8", errors="replace")


def _last_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else "empty unittest output"


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "detail": str(detail),
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v5.15a Generic Semantic Bridge Acceptance",
        "",
        f"- Overall passed: `{summary['passed']}`",
        (
            "- Passed checks: "
            f"`{summary['passed_check_count']}/{summary['check_count']}`"
        ),
        (
            "- External holdout: "
            f"`{report['holdout']['holdout_id']}`"
        ),
        (
            "- Provider/control LLM calls: "
            f"`{report['cost']['provider_call_count']}/"
            f"{report['cost']['control_llm_call_count']}`"
        ),
        "",
        "## Checks",
        "",
    ]
    for item in report["checks"]:
        marker = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- [{marker}] `{item['name']}`: {item['detail']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    report = evaluate(
        repo_root=args.repo_root.resolve(),
        unittest_output=args.unittest_output.resolve(),
        holdout_file=args.holdout_file.resolve(),
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
    print(
        json.dumps(
            report["summary"],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
