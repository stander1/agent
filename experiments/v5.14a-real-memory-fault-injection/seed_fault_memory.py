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
    _resolve_collaboration_group_id,
    _safe_identifier,
)
from agent_runtime.memory.memory_store import MemoryStoreLite


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed isolated active and historical facts for v5.14a."
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--memory-scope", required=True)
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path(__file__).with_name("fault_matrix.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def seed_memory(
    *,
    data_dir: Path,
    memory_scope: str,
    matrix: dict[str, Any],
) -> dict[str, Any]:
    participants = tuple(str(item) for item in matrix["participants"])
    safe_scope = _safe_identifier(memory_scope)[:80]
    group_id = _resolve_collaboration_group_id(
        memory_scope_id=safe_scope,
        target_kind="agentchat_team",
        agent_id="",
        team_participants=participants,
    )
    storage_dir = (
        data_dir.expanduser().resolve()
        / "shared_memory"
        / "autogen"
        / safe_scope
    )
    store = MemoryStoreLite(storage_dir=storage_dir)

    structured = dict(matrix["structured_memory"])
    structured_old = _write_structured_claim(
        store,
        task_id="SEED_STRUCTURED_OLD",
        group_id=group_id,
        spec=structured,
        value=str(structured["historical_value"]),
        revision_kind="asserted",
    )
    structured_current = _write_structured_claim(
        store,
        task_id="SEED_STRUCTURED_CURRENT",
        group_id=group_id,
        spec=structured,
        value=str(structured["active_value"]),
        revision_kind="replaces",
    )

    legacy = dict(matrix["legacy_memory"])
    legacy_old = _write_legacy_claim(
        store,
        task_id="SEED_LEGACY_OLD",
        group_id=group_id,
        spec=legacy,
        value=str(legacy["historical_value"]),
    )
    legacy_current = _write_legacy_claim(
        store,
        task_id="SEED_LEGACY_CURRENT",
        group_id=group_id,
        spec=legacy,
        value=str(legacy["active_value"]),
    )

    reloaded = MemoryStoreLite(storage_dir=storage_dir)
    refs = reloaded.search_memory(
        "current service capacity and database busy timeout",
        tags=[group_id],
        required_tags=[group_id],
        top_k=10,
    )
    guards = [reloaded.revision_guard(ref) for ref in refs]
    structured_guards = [
        guard
        for guard in guards
        if str(guard.get("schema_version") or "").startswith("ccf.v2")
    ]
    legacy_guards = [
        guard
        for guard in guards
        if str(guard.get("schema_version") or "").startswith("ccf.v1")
    ]
    if not structured_guards or not legacy_guards:
        raise RuntimeError("Seeded memory did not survive persistence and scoped search.")
    if not any(guard.get("historical_facts") for guard in structured_guards):
        raise RuntimeError("Structured seed has no historical fact.")
    if not any(guard.get("historical_claims") for guard in legacy_guards):
        raise RuntimeError("Legacy seed has no historical claim.")

    return {
        "schema_version": "agentlite.real_memory_fault_seed.v1",
        "memory_scope_id": safe_scope,
        "collaboration_group_id": group_id,
        "participants": list(participants),
        "storage_dir": str(storage_dir),
        "writes": {
            "structured_old": _write_summary(structured_old),
            "structured_current": _write_summary(structured_current),
            "legacy_old": _write_summary(legacy_old),
            "legacy_current": _write_summary(legacy_current),
        },
        "search_ref_count": len(refs),
        "revision_guards": guards,
    }


def _write_structured_claim(
    store: MemoryStoreLite,
    *,
    task_id: str,
    group_id: str,
    spec: dict[str, Any],
    value: str,
    revision_kind: str,
) -> Any:
    assignment = f"{spec['assignment_key']}={value}"
    return store.write_memory_candidate_with_report(
        task_id=task_id,
        source_agent="AcceptanceMemorySeeder",
        task_topic=str(spec["subject"]),
        memory_card={
            "summary": assignment,
            "confidence": 0.99,
            "importance_hint": 0.95,
            "coverage_score": 0.95,
            "reuse_scope": [group_id],
            "slot_hint": str(spec["slot_id"]),
        },
        claim_cards=[
            {
                "subject": str(spec["subject"]),
                "raw_slot_text": str(spec["assignment_key"]),
                "slot_id": str(spec["slot_id"]),
                "scope": str(spec["scope"]),
                "value": value,
                "value_type": str(spec.get("value_type") or "string"),
                "unit": "",
                "raw_text": assignment,
                "summary": assignment,
                "certainty": "confirmed",
                "modality": "asserted",
                "polarity": "positive",
                "confidence": 0.99,
                "revision_kind": revision_kind,
                "source_pointer": f"state:{task_id}",
            }
        ],
        tags=[group_id, "v5.14a-fault-injection", "runtime-config"],
        slot_hint=str(spec["slot_id"]),
        source_state_ids=[f"state_{task_id.lower()}"],
        evidence_refs=[f"evidence_{task_id.lower()}"],
        reuse_intent="reuse only inside the isolated acceptance collaboration group",
        fallback_summary=assignment,
    )


def _write_legacy_claim(
    store: MemoryStoreLite,
    *,
    task_id: str,
    group_id: str,
    spec: dict[str, Any],
    value: str,
) -> Any:
    summary = f"{spec['assignment_key']}={value} {spec.get('unit', '')}".strip()
    return store.write_memory_with_report(
        task_id=task_id,
        source_agent="AcceptanceLegacyMemorySeeder",
        task_topic=str(spec["subject"]),
        summary=summary,
        tags=[group_id, "v5.14a-fault-injection", "legacy-runtime-config"],
        slot_hint=str(spec["slot_id"]),
        source_state_ids=[f"state_{task_id.lower()}"],
        evidence_refs=[f"evidence_{task_id.lower()}"],
        reuse_intent="exercise the conservative unstructured-conflict path",
        confidence=0.99,
        schema_version="ccf.v1-lite",
    )


def _write_summary(report: Any) -> dict[str, Any]:
    ref = getattr(report, "memory_ref", None)
    return {
        "memory_id": str(getattr(ref, "memory_id", "") or ""),
        "memory_view_id": str(getattr(ref, "memory_view_id", "") or ""),
        "status": str(getattr(ref, "status", "") or ""),
        "admission_status": str(getattr(report, "admission_status", "") or ""),
        "conflict_detected_count": int(
            getattr(report, "conflict_detected_count", 0) or 0
        ),
        "resolved_conflict_count": int(
            getattr(report, "resolved_conflict_count", 0) or 0
        ),
    }


def main() -> int:
    args = parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    payload = seed_memory(
        data_dir=args.data_dir,
        memory_scope=args.memory_scope,
        matrix=matrix,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
