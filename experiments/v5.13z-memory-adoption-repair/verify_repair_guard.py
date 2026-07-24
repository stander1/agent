from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_runtime.bootstrap.startup import BootstrapContext
from agent_runtime.core.kernel import AgentDescriptor
from agent_runtime.core.models import TaskSpec
from agent_runtime.drivers.autogen import (
    AutoGenHookManager,
    HookCallContext,
    _InjectedMemoryRecord,
)
from agent_runtime.memory.memory_store import MemoryRef, MemoryStoreLite
from agent_runtime.reliability.memory_adoption_guard import (
    guard_memory_adoption_output,
)
from web_monitor.parser import _autogen_token_summary


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class GenericTextMessage:
    content: str
    source: str
    type: str = "TextMessage"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the generic v5.13z memory-adoption repair guard."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def structured_row(
    *,
    status: str,
    semantic_key: str,
    active_value: str,
    historical_value: str,
    span: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "attribution_mode": "ccf_v2_semantic_key_value_rules",
        "semantic_key": semantic_key,
        "active_value": active_value,
        "historical_values": [historical_value],
        "matched_historical_output_spans": [span],
    }


def integration_evidence(output_dir: Path) -> tuple[list[dict[str, Any]], str]:
    with tempfile.TemporaryDirectory(dir=output_dir) as tmp:
        root = Path(tmp)
        status_file = (
            root / "sessions" / "launch_v513z" / "bootstrap_status.json"
        )
        status_file.parent.mkdir(parents=True, exist_ok=True)
        target_cwd = root / "workspace"
        target_cwd.mkdir()
        bootstrap = BootstrapContext(
            framework="autogen",
            session_id="launch_v513z",
            data_dir=root,
            status_file=status_file,
            target_cwd=target_cwd,
        )
        env = {
            "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
            "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
            "AGENTLITE_MEMORY_SCOPE": "v513z-generic-acceptance",
        }
        with patch.dict(os.environ, env, clear=False):
            manager = AutoGenHookManager(bootstrap)
            context = HookCallContext(
                call_id="call_external_capacity_specialist",
                task=TaskSpec(
                    task_id="generic-capacity-task",
                    group_id="generic-system-chain",
                    title="Publish capacity",
                    prompt="Publish the current service capacity.",
                ),
                agent=AgentDescriptor(
                    "ExternalCapacitySpecialist",
                    "ThirdPartyCustomRole",
                ),
                method_name="on_messages_stream",
                target_kind="agentchat_agent",
            )
            semantic_key = (
                "project:current|slot.runtime.config|config.service.capacity"
            )
            revision_guard = {
                "required": True,
                "schema_version": "ccf.v2",
                "subject": "project:current",
                "semantic_key": semantic_key,
                "active_facts": [
                    {
                        "semantic_key": semantic_key,
                        "slot_id": "slot.runtime.config",
                        "scope": "config.service.capacity",
                        "value": "50",
                        "value_type": "integer",
                        "unit": "",
                        "polarity": "positive",
                    }
                ],
                "historical_facts": [
                    {
                        "semantic_key": semantic_key,
                        "slot_id": "slot.runtime.config",
                        "scope": "config.service.capacity",
                        "value": "20",
                        "value_type": "integer",
                        "unit": "",
                        "polarity": "positive",
                    }
                ],
            }
            manager._memory_injections_by_call[context.call_id] = [
                _InjectedMemoryRecord(
                    ref=MemoryRef(
                        memory_id="mem_capacity_acceptance",
                        version_id=2,
                        status="active",
                        task_topic="generic.system.settings",
                        memory_view_id="view_capacity_acceptance",
                        slot_id="slot.runtime.config",
                    ),
                    prompt_view="service.capacity=50",
                    current_task_text="Publish the current service capacity.",
                    current_task_source="user_task",
                    injected_prompt_view="service.capacity=50",
                    revision_guard=revision_guard,
                )
            ]
            guarded = manager.guard_call_result_if_needed(
                context,
                GenericTextMessage(
                    "Apply service.capacity=20 before launch.",
                    "ExternalCapacitySpecialist",
                ),
            )
            manager.record_call_end(context, guarded)
            events = [
                json.loads(line)
                for line in (manager.output_dir / "trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            return events, guarded.content


def run(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    checks: list[Check] = []

    span = "Apply service.capacity=20 before launch."
    repaired = guard_memory_adoption_output(
        output_text=f"Audit ID 20 remains unchanged. {span}",
        evidence_rows=[
            structured_row(
                status="wrong",
                semantic_key="arbitrary.domain.service_capacity",
                active_value="50",
                historical_value="20",
                span=span,
            )
        ],
    )
    checks.extend(
        [
            Check(
                "exact structured conflict is repaired",
                repaired.status == "rule_repaired"
                and "service.capacity=50" in repaired.output_text,
                repaired.output_text,
            ),
            Check(
                "unrelated same scalar is preserved",
                "Audit ID 20 remains unchanged" in repaired.output_text,
                repaired.output_text,
            ),
        ]
    )

    mixed = guard_memory_adoption_output(
        output_text=(
            "Primary worker.limit=48; fallback worker.limit=16."
        ),
        evidence_rows=[
            structured_row(
                status="mixed",
                semantic_key="another.domain.worker_limit",
                active_value="48",
                historical_value="16",
                span="fallback worker.limit=16.",
            )
        ],
    )
    checks.append(
        Check(
            "mixed active and historical values are normalized",
            mixed.status == "rule_repaired"
            and "worker.limit=16" not in mixed.output_text
            and mixed.output_text.count("worker.limit=48") == 2,
            mixed.output_text,
        )
    )

    blocked = guard_memory_adoption_output(
        output_text="Use an uncertain legacy narrative with private details.",
        evidence_rows=[
            {
                "status": "wrong",
                "attribution_mode": "legacy_fuzzy_rules",
                "semantic_key": "third.domain.release_channel",
                "active_value": "stable",
            }
        ],
    )
    checks.extend(
        [
            Check(
                "uncertain conflict is blocked",
                blocked.status == "blocked"
                and "conflict_alert" in blocked.output_text,
                blocked.output_text,
            ),
            Check(
                "blocked alert does not replay unsafe body",
                "private details" not in blocked.output_text,
                blocked.output_text,
            ),
        ]
    )

    events, guarded_content = integration_evidence(output_dir)
    guard_events = [
        event
        for event in events
        if event.get("event_type") == "autogen_memory_adoption_guard"
    ]
    adoption_events = [
        event
        for event in events
        if event.get("event_type") == "autogen_memory_adoption"
    ]
    output_events = [
        event
        for event in events
        if event.get("event_type") == "autogen_agent_output"
    ]
    state_types = [
        ref.get("state_type")
        for ref in output_events[-1]["payload"].get("state_refs", [])
    ] if output_events else []
    checks.extend(
        [
            Check(
                "arbitrary AutoGen role is guarded before propagation",
                "service.capacity=50" in guarded_content
                and bool(guard_events),
                guarded_content,
            ),
            Check(
                "original wrong adoption remains auditable",
                bool(adoption_events)
                and adoption_events[-1]["payload"].get(
                    "wrong_memory_hit_count"
                ) == 1,
                json.dumps(
                    adoption_events[-1]["payload"]
                    if adoption_events
                    else {},
                    ensure_ascii=False,
                ),
            ),
            Check(
                "repaired output remains a normal artifact",
                state_types == ["artifact_state"],
                repr(state_types),
            ),
        ]
    )

    store = MemoryStoreLite()
    active_ref = store.write_memory(
        task_id="generic-prior",
        source_agent="ExternalSource",
        task_topic="generic.system.settings",
        summary="service.capacity=50",
        tags=["v513z-generic-acceptance"],
        slot_hint="runtime_config",
    )
    compensation = store.record_downstream_compensation(
        active_ref,
        reason="acceptance downstream correction",
        created_by="AcceptanceGuard",
    )
    checks.append(
        Check(
            "compensating event preserves the correct active memory",
            compensation.event_type == "downstream_fact_correction"
            and store.resolve_ref(active_ref.memory_id).status == "active",
            json.dumps(asdict(compensation), ensure_ascii=False),
        )
    )

    summary = _autogen_token_summary(events)
    checks.append(
        Check(
            "guard metrics are reportable",
            summary.get("memory_adoption_guard_event_count") == 1
            and summary.get("memory_adoption_rule_repair_count") == 1
            and summary.get("memory_adoption_repaired_fact_count") == 1,
            json.dumps(summary, ensure_ascii=False),
        )
    )

    passed_count = sum(check.passed for check in checks)
    report = {
        "report_version": "v5.13z",
        "schema_version": "agentlite.v5.13z-memory-adoption-repair.v1",
        "summary": {
            "passed": passed_count == len(checks),
            "passed_check_count": passed_count,
            "check_count": len(checks),
        },
        "checks": [asdict(check) for check in checks],
        "guard_metrics": {
            key: summary.get(key)
            for key in (
                "memory_adoption_guard_event_count",
                "memory_adoption_rule_repair_count",
                "memory_adoption_blocked_count",
                "memory_adoption_enforcement_failure_count",
                "memory_adoption_repaired_fact_count",
            )
        },
    }
    (output_dir / "acceptance_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown = [
        "# v5.13z Memory Adoption Repair Acceptance",
        "",
        (
            f"Result: {passed_count}/{len(checks)} checks passed "
            f"({'PASS' if report['summary']['passed'] else 'FAIL'})"
        ),
        "",
    ]
    for check in checks:
        markdown.append(
            f"- [{'x' if check.passed else ' '}] {check.name}: {check.detail}"
        )
    (output_dir / "acceptance_report.md").write_text(
        "\n".join(markdown) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    args = parse_args()
    report = run(args.output_dir.resolve())
    print(
        "v5.13z memory-adoption repair acceptance: "
        f"{report['summary']['passed_check_count']}/"
        f"{report['summary']['check_count']} passed"
    )
    print(args.output_dir.resolve() / "acceptance_report.json")
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
