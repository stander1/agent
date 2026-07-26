from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.drivers.autogen import _text_fingerprint
from agent_runtime.memory.claim_extractor import extract_claim_cards


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify v5.14a real AutoGen memory-fault propagation evidence."
    )
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--managed-dir", type=Path, required=True)
    parser.add_argument("--managed-data-dir", type=Path, required=True)
    parser.add_argument("--seed-manifest", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    return parser.parse_args()


def evaluate(
    *,
    matrix: dict[str, Any],
    native_payload: dict[str, Any],
    managed_payload: dict[str, Any],
    seed_manifest: dict[str, Any],
    trace_events: list[dict[str, Any]],
) -> dict[str, Any]:
    scenarios = {
        str(item["scenario_id"]): dict(item) for item in matrix["scenarios"]
    }
    structured = dict(matrix["structured_memory"])
    legacy = dict(matrix["legacy_memory"])
    native_rows = _index_rows(native_payload.get("rows", []))
    managed_rows = _index_rows(managed_payload.get("rows", []))
    all_keys = sorted(set(native_rows) | set(managed_rows))

    guard_queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    guard_event_count = 0
    compensation_event_count = 0
    for event in trace_events:
        if str(event.get("event_type") or "") != "autogen_memory_adoption_guard":
            continue
        payload = dict(event.get("payload") or {})
        if str(payload.get("agent_id") or "").casefold() != str(
            matrix["participants"][0]
        ).casefold():
            continue
        fingerprint = str(payload.get("original_output_fingerprint") or "")
        if fingerprint:
            guard_queues[fingerprint].append(payload)
        guard_event_count += 1
        compensation_event_count += int(
            payload.get("compensation_event_count", 0) or 0
        )

    audit_rows: list[dict[str, Any]] = []
    for key in all_keys:
        scenario_id, repetition = key
        scenario = scenarios.get(scenario_id, {})
        native = native_rows.get(key, {})
        managed = managed_rows.get(key, {})
        raw_native = str(native.get("provider_raw_output") or "")
        raw_managed = str(managed.get("provider_raw_output") or "")
        downstream_native = str(native.get("downstream_observed_text") or "")
        downstream_managed = str(managed.get("downstream_observed_text") or "")
        native_semantics = classify_output(
            raw_native,
            fault_class=str(scenario.get("fault_class") or ""),
            structured=structured,
            legacy=legacy,
        )
        managed_semantics = classify_output(
            raw_managed,
            fault_class=str(scenario.get("fault_class") or ""),
            structured=structured,
            legacy=legacy,
        )
        downstream_native_semantics = classify_output(
            downstream_native,
            fault_class=str(scenario.get("fault_class") or ""),
            structured=structured,
            legacy=legacy,
        )
        downstream_managed_semantics = classify_output(
            downstream_managed,
            fault_class=str(scenario.get("fault_class") or ""),
            structured=structured,
            legacy=legacy,
        )
        fingerprint = str(
            managed.get("provider_raw_fingerprint")
            or _text_fingerprint(raw_managed)
        )
        guard_payload = (
            guard_queues[fingerprint].popleft()
            if guard_queues.get(fingerprint)
            else {}
        )
        expected_action = str(scenario.get("expected_managed_action") or "")
        actual_action = str(guard_payload.get("status") or "safe")
        managed_received_text = "\n".join(
            str(item.get("content") or "")
            for item in managed.get("emitter_received_messages", [])
            if isinstance(item, dict)
        )
        memory_current_visible = (
            str(structured["active_value"]) in managed_received_text
            if not scenario_id.startswith("legacy_")
            else str(legacy["active_value"]) in managed_received_text
        )
        managed_success = _managed_success(
            fault_class=str(scenario.get("fault_class") or ""),
            expected_action=expected_action,
            actual_action=actual_action,
            raw_semantics=managed_semantics,
            downstream_semantics=downstream_managed_semantics,
            downstream_text=downstream_managed,
        )
        managed_safe_fallback = bool(
            str(scenario.get("fault_class") or "") == "negated_historical"
            and not managed_semantics["induced"]
            and actual_action == "safe"
            and managed_semantics["status"] == "useful"
            and downstream_managed_semantics["status"] == "useful"
            and not managed_semantics["historical_positive"]
            and not downstream_managed_semantics["historical_positive"]
        )
        native_fault_leaked = _native_fault_leaked(
            fault_class=str(scenario.get("fault_class") or ""),
            semantics=downstream_native_semantics,
        )
        audit_rows.append(
            {
                "scenario_id": scenario_id,
                "repetition": repetition,
                "fault_class": str(scenario.get("fault_class") or ""),
                "expected_managed_action": expected_action,
                "actual_managed_action": actual_action,
                "native_fault_induced": native_semantics["induced"],
                "managed_fault_induced": managed_semantics["induced"],
                "native_fault_leaked_downstream": native_fault_leaked,
                "managed_fault_escaped_downstream": _fault_escaped(
                    str(scenario.get("fault_class") or ""),
                    downstream_managed_semantics,
                ),
                "managed_success": managed_success,
                "managed_safe_fallback": managed_safe_fallback,
                "managed_memory_marker_observed": bool(
                    managed.get("memory_marker_observed_by_emitter")
                ),
                "managed_current_value_visible": memory_current_visible,
                "native_memory_marker_observed": bool(
                    native.get("memory_marker_observed_by_emitter")
                ),
                "unrelated_scalar_preserved": (
                    "ticket_count=20"
                    in downstream_managed.replace(" ", "").casefold()
                    if scenario_id == "structured_unrelated_same_number"
                    else True
                ),
                "conflict_alert_observed": (
                    _runtime_conflict_hold_observed(downstream_managed)
                ),
                "compensation_event_count": int(
                    guard_payload.get("compensation_event_count", 0) or 0
                ),
                "native_raw_status": native_semantics["status"],
                "managed_raw_status": managed_semantics["status"],
                "native_downstream_status": downstream_native_semantics["status"],
                "managed_downstream_status": downstream_managed_semantics[
                    "status"
                ],
                "native_raw_output": raw_native,
                "managed_raw_output": raw_managed,
                "native_downstream_output": downstream_native,
                "managed_downstream_output": downstream_managed,
            }
        )

    expected_rows = len(scenarios) * int(
        managed_payload.get("summary", {}).get("repetitions", 0) or 0
    )
    fault_rows = [
        row
        for row in audit_rows
        if row["fault_class"]
        in {
            "historical_only",
            "active_and_historical",
            "historical_with_unrelated_scalar",
            "unstructured_historical",
        }
    ]
    safe_rows = [
        row
        for row in audit_rows
        if row["fault_class"] in {"safe_current", "negated_historical"}
    ]
    negated_rows = [
        row
        for row in safe_rows
        if row["fault_class"] == "negated_historical"
    ]
    checks = [
        _check(
            "real_provider_usage_captured",
            int(native_payload.get("summary", {}).get("llm_total_tokens", 0)) > 0
            and int(managed_payload.get("summary", {}).get("llm_total_tokens", 0))
            > 0,
            (
                f"native={native_payload.get('summary', {}).get('llm_total_tokens', 0)}, "
                f"managed={managed_payload.get('summary', {}).get('llm_total_tokens', 0)}"
            ),
        ),
        _check(
            "complete_paired_matrix",
            len(audit_rows) == expected_rows
            and len(native_rows) == expected_rows
            and len(managed_rows) == expected_rows,
            (
                f"expected={expected_rows}, audit={len(audit_rows)}, "
                f"native={len(native_rows)}, managed={len(managed_rows)}"
            ),
        ),
        _check(
            "isolated_seed_scope_matches_runtime",
            bool(seed_manifest.get("memory_scope_id"))
            and int(seed_manifest.get("search_ref_count", 0)) >= 2,
            (
                f"scope={seed_manifest.get('memory_scope_id', '')}, "
                f"refs={seed_manifest.get('search_ref_count', 0)}"
            ),
        ),
        _check(
            "native_has_no_agentlite_memory_injection",
            bool(audit_rows)
            and all(not row["native_memory_marker_observed"] for row in audit_rows),
            f"unexpected={sum(row['native_memory_marker_observed'] for row in audit_rows)}",
        ),
        _check(
            "managed_memory_reaches_emitter",
            bool(audit_rows)
            and all(
                row["managed_memory_marker_observed"]
                and row["managed_current_value_visible"]
                for row in audit_rows
            ),
            (
                f"marker={sum(row['managed_memory_marker_observed'] for row in audit_rows)}/"
                f"{len(audit_rows)}, current={sum(row['managed_current_value_visible'] for row in audit_rows)}/"
                f"{len(audit_rows)}"
            ),
        ),
        _check(
            "unsafe_fault_injection_is_observed_not_assumed",
            bool(fault_rows)
            and all(
                row["native_fault_induced"] and row["managed_fault_induced"]
                for row in fault_rows
            ),
            (
                f"native={sum(row['native_fault_induced'] for row in fault_rows)}/"
                f"{len(fault_rows)}, managed={sum(row['managed_fault_induced'] for row in fault_rows)}/"
                f"{len(fault_rows)}"
            ),
        ),
        _check(
            "negated_history_false_positive_case_observed",
            bool(negated_rows)
            and any(row["managed_fault_induced"] for row in negated_rows),
            (
                f"managed_negated_samples="
                f"{sum(row['managed_fault_induced'] for row in negated_rows)}/"
                f"{len(negated_rows)}"
            ),
        ),
        _check(
            "native_control_propagates_induced_faults",
            bool(fault_rows)
            and all(row["native_fault_leaked_downstream"] for row in fault_rows),
            (
                f"leaked={sum(row['native_fault_leaked_downstream'] for row in fault_rows)}/"
                f"{len(fault_rows)}"
            ),
        ),
        _check(
            "managed_structured_faults_are_repaired",
            all(
                row["actual_managed_action"] == "rule_repaired"
                for row in fault_rows
                if row["fault_class"] != "unstructured_historical"
            ),
            "structured fault rows require rule_repaired",
        ),
        _check(
            "managed_unstructured_faults_are_blocked",
            all(
                row["actual_managed_action"] == "blocked"
                and row["conflict_alert_observed"]
                for row in fault_rows
                if row["fault_class"] == "unstructured_historical"
            ),
            "legacy fault rows require conflict_alert",
        ),
        _check(
            "managed_faults_do_not_escape_downstream",
            bool(fault_rows)
            and all(
                row["managed_success"]
                and not row["managed_fault_escaped_downstream"]
                for row in fault_rows
            ),
            (
                f"prevented={sum(row['managed_success'] and not row['managed_fault_escaped_downstream'] for row in fault_rows)}/"
                f"{len(fault_rows)}"
            ),
        ),
        _check(
            "safe_and_negated_outputs_have_no_false_positive",
            bool(safe_rows)
            and all(
                row["actual_managed_action"] == "safe"
                and (row["managed_success"] or row["managed_safe_fallback"])
                for row in safe_rows
            ),
            (
                f"safe={sum(row['actual_managed_action'] == 'safe' and (row['managed_success'] or row['managed_safe_fallback']) for row in safe_rows)}/"
                f"{len(safe_rows)}"
            ),
        ),
        _check(
            "unrelated_equal_scalar_is_preserved",
            all(row["unrelated_scalar_preserved"] for row in audit_rows),
            "ticket_count=20 must survive capacity repair",
        ),
        _check(
            "guard_actions_emit_compensation_audit",
            bool(fault_rows)
            and all(row["compensation_event_count"] > 0 for row in fault_rows),
            (
                f"rows_with_compensation={sum(row['compensation_event_count'] > 0 for row in fault_rows)}/"
                f"{len(fault_rows)}"
            ),
        ),
    ]
    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "agentlite.real_memory_fault_acceptance.v1",
        "summary": {
            "passed": passed,
            "check_count": len(checks),
            "passed_check_count": sum(item["passed"] for item in checks),
            "row_count": len(audit_rows),
            "fault_row_count": len(fault_rows),
            "safe_row_count": len(safe_rows),
            "managed_negated_sample_count": sum(
                row["managed_fault_induced"] for row in negated_rows
            ),
            "managed_safe_fallback_count": sum(
                row["managed_safe_fallback"] for row in safe_rows
            ),
            "guard_event_count": guard_event_count,
            "compensation_event_count": compensation_event_count,
            "native_llm_total_tokens": int(
                native_payload.get("summary", {}).get("llm_total_tokens", 0)
            ),
            "managed_llm_total_tokens": int(
                managed_payload.get("summary", {}).get("llm_total_tokens", 0)
            ),
            "native_fault_escape_rate": _ratio(
                sum(row["native_fault_leaked_downstream"] for row in fault_rows),
                len(fault_rows),
            ),
            "managed_fault_escape_rate": _ratio(
                sum(
                    row["managed_fault_escaped_downstream"] for row in fault_rows
                ),
                len(fault_rows),
            ),
            "managed_false_positive_rate": _ratio(
                sum(row["actual_managed_action"] != "safe" for row in safe_rows),
                len(safe_rows),
            ),
        },
        "checks": checks,
        "rows": audit_rows,
    }


def classify_output(
    text: str,
    *,
    fault_class: str,
    structured: dict[str, Any],
    legacy: dict[str, Any],
) -> dict[str, Any]:
    if fault_class == "unstructured_historical":
        normalized = " ".join(text.casefold().split())
        old = (
            f"{legacy['assignment_key']}={legacy['historical_value']}"
        ).casefold()
        current = (
            f"{legacy['assignment_key']}={legacy['active_value']}"
        ).casefold()
        historical_positive = old in normalized
        active_positive = current in normalized
        status = (
            "mixed"
            if historical_positive and active_positive
            else "wrong"
            if historical_positive
            else "useful"
            if active_positive
            else "blocked"
            if "agentlite_memory_conflict" in normalized
            else "unassessed"
        )
        return {
            "status": status,
            "active_positive": active_positive,
            "historical_positive": historical_positive,
            "historical_negative": False,
            "induced": historical_positive,
        }

    claims = extract_claim_cards(
        text,
        subject=str(structured["subject"]),
        source_pointer="v5.14a-evaluation",
        default_confidence=0.8,
    )
    relevant = [
        claim
        for claim in claims
        if str(claim.get("slot_id") or "") == str(structured["slot_id"])
        and str(claim.get("scope") or "") == str(structured["scope"])
    ]
    active = str(structured["active_value"])
    historical = str(structured["historical_value"])
    active_positive = any(
        str(claim.get("value") or "") == active
        and str(claim.get("polarity") or "positive") != "negative"
        for claim in relevant
    )
    historical_positive = any(
        str(claim.get("value") or "") == historical
        and str(claim.get("polarity") or "positive") != "negative"
        for claim in relevant
    )
    historical_negative = any(
        str(claim.get("value") or "") == historical
        and str(claim.get("polarity") or "") == "negative"
        for claim in relevant
    )
    status = (
        "mixed"
        if active_positive and historical_positive
        else "wrong"
        if historical_positive
        else "useful"
        if active_positive
        else "unassessed"
    )
    if fault_class == "safe_current":
        induced = active_positive and not historical_positive
    elif fault_class == "historical_only":
        induced = historical_positive and not active_positive
    elif fault_class == "active_and_historical":
        induced = active_positive and historical_positive
    elif fault_class == "negated_historical":
        induced = (
            active_positive and historical_negative and not historical_positive
        )
    elif fault_class == "historical_with_unrelated_scalar":
        induced = (
            historical_positive
            and "ticket_count=20" in text.replace(" ", "").casefold()
        )
    else:
        induced = False
    return {
        "status": status,
        "active_positive": active_positive,
        "historical_positive": historical_positive,
        "historical_negative": historical_negative,
        "induced": induced,
    }


def _managed_success(
    *,
    fault_class: str,
    expected_action: str,
    actual_action: str,
    raw_semantics: dict[str, Any],
    downstream_semantics: dict[str, Any],
    downstream_text: str,
) -> bool:
    if not raw_semantics["induced"] or actual_action != expected_action:
        return False
    if expected_action == "blocked":
        return (
            _runtime_conflict_hold_observed(downstream_text)
            and not downstream_semantics["historical_positive"]
        )
    if expected_action == "rule_repaired":
        return (
            downstream_semantics["active_positive"]
            and not downstream_semantics["historical_positive"]
        )
    if fault_class == "negated_historical":
        return (
            downstream_semantics["active_positive"]
            and downstream_semantics["historical_negative"]
            and not downstream_semantics["historical_positive"]
        )
    return (
        downstream_semantics["active_positive"]
        and not downstream_semantics["historical_positive"]
    )


def _runtime_conflict_hold_observed(text: str) -> bool:
    normalized = str(text or "").casefold()
    return (
        "agentlite_memory_conflict" in normalized
        or "runtime safety hold" in normalized
    )


def _native_fault_leaked(
    *,
    fault_class: str,
    semantics: dict[str, Any],
) -> bool:
    if fault_class in {
        "historical_only",
        "active_and_historical",
        "historical_with_unrelated_scalar",
        "unstructured_historical",
    }:
        return bool(semantics["historical_positive"])
    return False


def _fault_escaped(
    fault_class: str,
    semantics: dict[str, Any],
) -> bool:
    return _native_fault_leaked(fault_class=fault_class, semantics=semantics)


def _index_rows(value: Iterable[Any]) -> dict[tuple[str, int], dict[str, Any]]:
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("scenario_id") or ""),
            int(item.get("repetition", 0) or 0),
        )
        rows[key] = dict(item)
    return rows


def load_trace_events(data_dir: Path) -> list[dict[str, Any]]:
    traces = sorted(
        data_dir.expanduser().resolve().glob(
            "sessions/*/autogen_driver/trace.jsonl"
        ),
        key=lambda path: path.stat().st_mtime,
    )
    if not traces:
        raise FileNotFoundError(
            f"No AgentLite AutoGen trace found under {data_dir}"
        )
    events: list[dict[str, Any]] = []
    for trace in traces:
        for line in trace.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def write_outputs(
    report: dict[str, Any],
    *,
    output_json: Path,
    output_markdown: Path,
    output_csv: Path,
) -> None:
    for path in (output_json, output_markdown, output_csv):
        path.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        fieldnames = [
            key
            for key in report["rows"][0].keys()
            if not key.endswith("_output")
        ] if report["rows"] else []
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            for row in report["rows"]:
                writer.writerow({key: row.get(key) for key in fieldnames})

    summary = report["summary"]
    lines = [
        "# v5.14a Real AutoGen Memory Fault Injection Acceptance",
        "",
        f"- Overall passed: `{summary['passed']}`",
        (
            f"- Checks: `{summary['passed_check_count']}/"
            f"{summary['check_count']}`"
        ),
        f"- Evidence rows: `{summary['row_count']}`",
        f"- Fault rows: `{summary['fault_row_count']}`",
        f"- Safe rows: `{summary['safe_row_count']}`",
        f"- Native fault escape rate: `{summary['native_fault_escape_rate']:.4f}`",
        (
            f"- Managed fault escape rate: "
            f"`{summary['managed_fault_escape_rate']:.4f}`"
        ),
        (
            f"- Managed false-positive rate: "
            f"`{summary['managed_false_positive_rate']:.4f}`"
        ),
        (
            f"- Managed real negated samples: "
            f"`{summary['managed_negated_sample_count']}`"
        ),
        (
            f"- Managed safe fallback samples: "
            f"`{summary['managed_safe_fallback_count']}`"
        ),
        f"- Native provider tokens: `{summary['native_llm_total_tokens']}`",
        f"- Managed provider tokens: `{summary['managed_llm_total_tokens']}`",
        "",
        "## Checks",
        "",
    ]
    for check in report["checks"]:
        marker = "PASS" if check["passed"] else "FAIL"
        lines.append(f"- [{marker}] `{check['name']}`: {check['detail']}")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            (
                "This is a propagation-safety experiment. Token totals are "
                "reported for audit, but no token-reduction claim is made from "
                "these deliberately short fault outputs."
            ),
            (
                "Every dangerous-fault row passes only when the real provider "
                "actually produced the obsolete positive value, native AutoGen "
                "propagated it, and AgentLite repaired or blocked it before the "
                "downstream probe."
            ),
            (
                "The negated-history false-positive case requires at least one "
                "real managed negation sample. A repeated sample that emits only "
                "the active value is recorded separately as a safe fallback, "
                "never as a repaired fault."
            ),
            "",
        ]
    )
    output_markdown.write_text("\n".join(lines), encoding="utf-8")


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def main() -> int:
    args = parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    native_payload = json.loads(
        (args.native_dir / "fault_results.json").read_text(encoding="utf-8")
    )
    managed_payload = json.loads(
        (args.managed_dir / "fault_results.json").read_text(encoding="utf-8")
    )
    seed_manifest = json.loads(
        args.seed_manifest.read_text(encoding="utf-8")
    )
    report = evaluate(
        matrix=matrix,
        native_payload=native_payload,
        managed_payload=managed_payload,
        seed_manifest=seed_manifest,
        trace_events=load_trace_events(args.managed_data_dir),
    )
    write_outputs(
        report,
        output_json=args.output_json,
        output_markdown=args.output_markdown,
        output_csv=args.output_csv,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
