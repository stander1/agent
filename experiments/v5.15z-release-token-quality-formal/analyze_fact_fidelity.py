from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from agent_runtime.memory.claim_extractor import (
    extract_canonical_claim_candidates,
)


CHECKPOINT_EVENT_TYPES = {
    "received_input": {"autogen_agent_receive"},
    "retrieved_memory": {"autogen_memory_retrieval"},
    "rewritten_input": {
        "autogen_agent_input_real_rewrite",
        "autogen_broadcast_replacement_applied",
    },
    "agent_outputs": {"autogen_agent_output"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trace open canonical claims from task requests through AgentLite "
            "runtime checkpoints and final delivery."
        )
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument(
        "--mode",
        choices=("native", "observed", "managed"),
        default="managed",
    )
    parser.add_argument("--trace-path", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def _decoded_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in payload.get("decoded_messages") or []:
        if not isinstance(item, dict):
            continue
        text = str(
            item.get("content_text")
            or item.get("content_preview")
            or ""
        ).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def _event_text(row: dict[str, Any]) -> str:
    payload = dict(row.get("payload") or {})
    decoded = _decoded_text(payload)
    if decoded:
        return decoded
    for key in (
        "prompt_view",
        "prompt_view_preview",
        "input_text",
        "input_preview",
        "output_text",
        "output_preview",
        "candidate_text",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _event_matches_task(row: dict[str, Any], task_index: int) -> bool:
    payload = dict(row.get("payload") or {})
    try:
        sequence_index = int(payload.get("task_sequence_index") or 0)
    except (TypeError, ValueError):
        sequence_index = 0
    return sequence_index == task_index


def _claim_key(candidate: dict[str, Any]) -> tuple[str, str]:
    return (
        _normalize(candidate.get("predicate")),
        _normalize(candidate.get("unit")),
    )


def _claim_visible(candidate: dict[str, Any], text: str) -> bool:
    haystack = _normalize(text)
    value = _normalize(candidate.get("value"))
    unit = _normalize(candidate.get("unit"))
    if not value or len(value) < 2:
        return False
    needle = value + unit
    return bool(
        re.search(
            rf"(?<![\w.]){re.escape(needle)}(?![\w.])",
            haystack,
        )
    )


def _finding_text(findings: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        for key in ("evidence", "summary", "description", "impact"):
            value = str(finding.get(key) or "").strip()
            if value:
                parts.append(value)
    return "\n".join(parts)


def _technical_findings(
    quality: dict[str, Any],
    *,
    task_id: str,
    mode: str,
) -> list[dict[str, Any]]:
    for task in quality.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        if str(task.get("task_id") or "") != task_id:
            continue
        findings_by_group = dict(task.get("technical_findings") or {})
        return [
            dict(item)
            for item in findings_by_group.get(mode) or []
            if isinstance(item, dict)
        ]
    return []


def _trace_from_report(
    *,
    run_root: Path,
    scenario: str,
    mode: str,
) -> Path | None:
    report = _read_json(
        run_root / scenario / "reports" / f"{mode}-agentlite.json"
    )
    recorded = Path(str(report.get("trace_path") or ""))
    if recorded.is_file():
        return recorded

    search_roots = [
        run_root / scenario / mode,
        *[
            ancestor.parent / ".agentlite-exp"
            for ancestor in run_root.parents
            if ancestor.name == "runs"
        ],
    ]
    candidates: list[Path] = []
    for ancestor in search_roots:
        if not ancestor.is_dir():
            continue
        try:
            for path in ancestor.rglob("trace.jsonl"):
                normalized = _normalize(path.as_posix())
                if (
                    _normalize(scenario) in normalized
                    and _normalize(mode) in normalized
                ):
                    candidates.append(path)
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(
        key=lambda path: (
            "autogen_driver" not in path.parts,
            -path.stat().st_mtime,
            len(path.parts),
        )
    )
    return candidates[0]


def _stage_texts(
    events: list[dict[str, Any]],
    *,
    task_index: int,
    question: str,
    final_answer: str,
) -> dict[str, str]:
    stages: dict[str, list[str]] = {
        "request": [question],
        "received_input": [],
        "retrieved_memory": [],
        "rewritten_input": [],
        "agent_outputs": [],
        "final_answer": [final_answer],
    }
    for row in events:
        if not _event_matches_task(row, task_index):
            continue
        event_type = str(row.get("event_type") or "")
        for stage, accepted in CHECKPOINT_EVENT_TYPES.items():
            if event_type not in accepted:
                continue
            text = _event_text(row)
            if text:
                stages[stage].append(text)
    return {
        stage: "\n".join(parts)
        for stage, parts in stages.items()
    }


def _claim_journey(
    candidate: dict[str, Any],
    *,
    stages: dict[str, str],
    finding_text: str,
    source_texts: dict[str, str],
) -> dict[str, Any]:
    availability = {
        stage: bool(text.strip()) for stage, text in stages.items()
    }
    visibility = {
        stage: (
            _claim_visible(candidate, text)
            if availability[stage]
            else None
        )
        for stage, text in stages.items()
    }
    first_loss = ""
    seen = False
    for stage in stages:
        if visibility[stage] is True:
            seen = True
        elif seen and visibility[stage] is False:
            first_loss = stage
            break
    source_span = dict(candidate.get("source_span") or {})
    source_quote = str(source_span.get("quote") or "")
    source_hash = str(source_span.get("text_hash") or "")
    source_id = str(source_span.get("source_id") or "")
    source_text = source_texts.get(source_id, "")
    source_start = int(source_span.get("start") or 0)
    source_end = int(source_span.get("end") or 0)
    exact_source_span = (
        bool(source_quote and source_hash and source_text)
        and 0 <= source_start <= source_end <= len(source_text)
        and source_text[source_start:source_end] == source_quote
        and hashlib.sha256(
            source_quote.encode("utf-8")
        ).hexdigest()
        == source_hash
    )
    value = str(candidate.get("value") or "")
    unit = str(candidate.get("unit") or "")
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "subject": str(candidate.get("subject") or ""),
        "predicate": str(candidate.get("predicate") or ""),
        "operator": str(candidate.get("operator") or ""),
        "value": value,
        "value_type": str(candidate.get("value_type") or ""),
        "unit": unit,
        "polarity": str(candidate.get("polarity") or ""),
        "modality": str(candidate.get("modality") or ""),
        "temporal_status": str(candidate.get("temporal_status") or ""),
        "origin_task_id": str(candidate.get("origin_task_id") or ""),
        "origin_task_index": int(
            candidate.get("origin_task_index") or 0
        ),
        "source_span": {
            "source_id": source_id,
            "start": source_start,
            "end": source_end,
            "quote": source_quote,
            "text_hash": source_hash,
            "exact": exact_source_span,
        },
        "checkpoint_available": availability,
        "visibility": visibility,
        "first_visibility_loss": first_loss,
        "final_visible": visibility["final_answer"] is True,
        "correlated_with_audited_finding": (
            bool(finding_text)
            and _normalize(value) in _normalize(finding_text)
            and (not unit or _normalize(unit) in _normalize(finding_text))
        ),
    }


def analyze(
    *,
    run_root: Path,
    scenario: str,
    mode: str,
    trace_path: Path | None = None,
) -> dict[str, Any]:
    sequence = _read_json(
        run_root / scenario / mode / "sequence_result.json"
    )
    quality = _read_json(
        run_root
        / scenario
        / "comparison"
        / "quality_blind_summary.json"
    )
    resolved_trace = trace_path or _trace_from_report(
        run_root=run_root,
        scenario=scenario,
        mode=mode,
    )
    events = _read_jsonl(resolved_trace) if resolved_trace else []

    active_claims: dict[tuple[str, str], dict[str, Any]] = {}
    source_texts: dict[str, str] = {}
    tasks: list[dict[str, Any]] = []
    for fallback_index, task in enumerate(sequence.get("tasks") or [], start=1):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or f"task_{fallback_index:03d}")
        task_index = int(task.get("task_index") or fallback_index)
        question = str(task.get("question") or "")
        final_answer = str(task.get("final_answer") or "")
        source_id = f"{scenario}/{task_id}/request"
        source_texts[source_id] = question
        extracted = extract_canonical_claim_candidates(
            question,
            subject=f"{scenario}:active-context",
            source_id=source_id,
        )
        for candidate in extracted:
            stored = dict(candidate)
            stored["origin_task_id"] = task_id
            stored["origin_task_index"] = task_index
            active_claims[_claim_key(stored)] = stored

        findings = _technical_findings(
            quality,
            task_id=task_id,
            mode=mode,
        )
        findings_text = _finding_text(findings)
        stages = _stage_texts(
            events,
            task_index=task_index,
            question=question,
            final_answer=final_answer,
        )
        journeys = [
            _claim_journey(
                candidate,
                stages=stages,
                finding_text=findings_text,
                source_texts=source_texts,
            )
            for candidate in active_claims.values()
        ]
        tasks.append(
            {
                "task_id": task_id,
                "task_index": task_index,
                "delivery_valid": bool(task.get("delivery_valid")),
                "technical_findings": findings,
                "audited_failure": bool(findings),
                "new_claim_count": len(extracted),
                "active_claim_count": len(active_claims),
                "trace_checkpoint_event_count": sum(
                    1
                    for row in events
                    if _event_matches_task(row, task_index)
                    and str(row.get("event_type") or "")
                    in {
                        event_type
                        for accepted in CHECKPOINT_EVENT_TYPES.values()
                        for event_type in accepted
                    }
                ),
                "stage_chars": {
                    stage: len(text) for stage, text in stages.items()
                },
                "claim_journeys": journeys,
            }
        )

    journeys = [
        journey
        for task in tasks
        for journey in task["claim_journeys"]
    ]
    return {
        "schema_version": "agentlite.fact-fidelity-analysis.v1",
        "summary": {
            "scenario": scenario,
            "mode": mode,
            "task_count": len(tasks),
            "tasks_with_runtime_trace": sum(
                task["trace_checkpoint_event_count"] > 0 for task in tasks
            ),
            "audited_failure_task_count": sum(
                task["audited_failure"] for task in tasks
            ),
            "new_claim_count": sum(task["new_claim_count"] for task in tasks),
            "claim_journey_count": len(journeys),
            "final_visible_count": sum(
                journey["final_visible"] for journey in journeys
            ),
            "final_not_visible_count": sum(
                not journey["final_visible"] for journey in journeys
            ),
            "finding_correlated_claim_count": sum(
                journey["correlated_with_audited_finding"]
                for journey in journeys
            ),
        },
        "inputs": {
            "run_root": str(run_root),
            "sequence_result": str(
                run_root / scenario / mode / "sequence_result.json"
            ),
            "quality_summary": str(
                run_root
                / scenario
                / "comparison"
                / "quality_blind_summary.json"
            ),
            "trace_path": str(resolved_trace or ""),
            "trace_available": bool(resolved_trace and resolved_trace.is_file()),
        },
        "limitations": [
            (
                "Visibility loss is a diagnostic checkpoint, not proof that "
                "an omitted claim was required by the task."
            ),
            (
                "The analyzer uses open structured claims and exact value/unit "
                "evidence; it does not use scenario or domain vocabularies."
            ),
            (
                "Audited findings remain the authority for quality failure; "
                "the trace analysis only localizes where evidence stopped "
                "being visible."
            ),
        ],
        "tasks": tasks,
    }


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# AgentLite Fact Fidelity Trace",
        "",
        f"- Scenario: `{summary['scenario']}`",
        f"- Mode: `{summary['mode']}`",
        f"- Tasks: `{summary['task_count']}`",
        (
            "- Tasks with runtime trace: "
            f"`{summary['tasks_with_runtime_trace']}`"
        ),
        (
            "- Audited failure tasks: "
            f"`{summary['audited_failure_task_count']}`"
        ),
        f"- Claim journeys: `{summary['claim_journey_count']}`",
        (
            "- Final visible / not visible: "
            f"`{summary['final_visible_count']} / "
            f"{summary['final_not_visible_count']}`"
        ),
        "",
        "## Tasks",
        "",
        "| Task | New claims | Active claims | Audited findings | "
        "First-loss claims | Final-visible claims |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for task in report["tasks"]:
        journeys = task["claim_journeys"]
        lines.append(
            f"| {task['task_id']} | {task['new_claim_count']} | "
            f"{task['active_claim_count']} | "
            f"{len(task['technical_findings'])} | "
            f"{sum(bool(item['first_visibility_loss']) for item in journeys)} | "
            f"{sum(item['final_visible'] for item in journeys)} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            (
                "A visibility loss is a localization signal only. It becomes "
                "a quality failure only when the frozen technical audit also "
                "identifies the omitted or contradicted evidence."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    report = analyze(
        run_root=args.run_root,
        scenario=args.scenario,
        mode=args.mode,
        trace_path=args.trace_path,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.output_markdown.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
