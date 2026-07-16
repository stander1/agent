from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.launcher import (  # noqa: E402
    LaunchRequest,
    ManagedProcessLauncher,
    read_bootstrap_status,
)

EXPECTED_PHASE = "v5.13i"
MEMORY_SCOPE = "autogen-shared-memory-smoke"
MEMORY_FACT = (
    "confirmed preference: three-day trip, budget 3000 CNY, "
    "nature walks and local food, avoid crowded commercial attractions"
)
USER_SCRIPT = PROJECT_ROOT / "examples" / "autogen_shared_memory_smoke.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that AgentLite admits a real AutoGen Team result and "
            "injects its persistent MemoryView into a later process."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args()


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _launch(
    *,
    mode: str,
    python: str,
    output_dir: Path,
    data_dir: Path,
) -> dict[str, Any]:
    app_output = output_dir / f"{mode}_app_output.json"
    request = LaunchRequest(
        framework="autogen",
        command=[
            python,
            str(USER_SCRIPT),
            "--mode",
            mode,
            "--output",
            str(app_output),
        ],
        cwd=PROJECT_ROOT,
        data_dir=data_dir,
    )
    env = {
        **os.environ,
        "AGENTLITE_AUTOGEN_BROADCAST_MODE": "real-rewrite",
        "AGENTLITE_AUTOGEN_TEAM_REWRITE": "1",
        "AGENTLITE_AUTOGEN_SHARED_MEMORY": "1",
        "AGENTLITE_MEMORY_SCOPE": MEMORY_SCOPE,
    }
    result = ManagedProcessLauncher().launch(request, environ=env)
    status = read_bootstrap_status(result.status_file) or {}
    details = status.get("driver_details", {})
    if not isinstance(details, dict):
        details = {}
    trace_path = Path(str(details.get("trace_path", "")))
    app_payload = (
        json.loads(app_output.read_text(encoding="utf-8"))
        if app_output.exists()
        else {}
    )
    return {
        "returncode": result.returncode,
        "session_id": result.session_id,
        "status": status,
        "details": details,
        "trace_path": str(trace_path),
        "events": _read_events(trace_path),
        "app_output_path": str(app_output),
        "app_payload": app_payload,
    }


def _payloads(run: dict[str, Any], event_type: str) -> list[dict[str, Any]]:
    return [
        event["payload"]
        for event in run["events"]
        if event.get("event_type") == event_type
        and isinstance(event.get("payload"), dict)
    ]


def _first_stream_item(run: dict[str, Any]) -> dict[str, Any]:
    items = run.get("app_payload", {}).get("stream_items", [])
    return items[0] if isinstance(items, list) and items else {}


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = PROJECT_ROOT / "runs" / f"autogen-shared-memory-{stamp}"
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "agentlite"

    seed = _launch(
        mode="seed",
        python=args.python,
        output_dir=output_dir,
        data_dir=data_dir,
    )
    recall = _launch(
        mode="recall",
        python=args.python,
        output_dir=output_dir,
        data_dir=data_dir,
    )

    seed_candidates = _payloads(seed, "autogen_memory_candidate")
    recall_retrievals = _payloads(recall, "autogen_memory_retrieval")
    recall_team_rewrites = _payloads(recall, "autogen_team_input_real_rewrite")
    recall_display_restores = _payloads(
        recall,
        "autogen_team_display_restored",
    )
    recall_item = _first_stream_item(recall)
    recall_result_messages = (
        recall.get("app_payload", {})
        .get("task_result", {})
        .get("messages", [])
    )
    if not isinstance(recall_result_messages, list):
        recall_result_messages = []
    persisted_snapshot = (
        data_dir
        / "shared_memory"
        / "autogen"
        / MEMORY_SCOPE
        / "memory_store_snapshot.json"
    )
    checks = {
        "seed_returncode_zero": seed["returncode"] == 0,
        "recall_returncode_zero": recall["returncode"] == 0,
        "seed_hooks_active": bool(seed["status"].get("hooks_active")),
        "recall_hooks_active": bool(recall["status"].get("hooks_active")),
        "driver_phase_matches": (
            seed["details"].get("phase") == EXPECTED_PHASE
            and recall["details"].get("phase") == EXPECTED_PHASE
        ),
        "user_script_has_no_agentlite_import": (
            "from agent_runtime" not in USER_SCRIPT.read_text(encoding="utf-8")
            and "import agent_runtime" not in USER_SCRIPT.read_text(encoding="utf-8")
        ),
        "seed_final_admitted": any(
            item.get("candidate_kind") == "autogen_team_final"
            and item.get("admission_status") == "admitted"
            for item in seed_candidates
        ),
        "persistent_snapshot_written": persisted_snapshot.exists(),
        "recall_memory_hit": any(
            int(item.get("memory_hit_count", 0) or 0) >= 1
            and int(item.get("retrieved_memory_tokens", 0) or 0) > 0
            for item in recall_retrievals
        ),
        "recall_internal_task_was_rewritten": any(
            item.get("rewrite_applied") is True
            and bool(item.get("memory_refs"))
            for item in recall_team_rewrites
        ),
        "recall_internal_prompt_contains_seed_fact": any(
            MEMORY_FACT in str(item.get("prompt_view_preview", ""))
            for item in recall_retrievals
        ),
        "recall_display_keeps_original_task": (
            recall_item.get("content")
            == "Continue the previous travel plan without restating old preferences."
        ),
        "recall_display_hides_internal_marker": not bool(
            recall_item.get("contains_team_rewrite_marker")
        ),
        "recall_result_hides_internal_marker": not any(
            bool(item.get("contains_team_rewrite_marker"))
            for item in recall_result_messages
            if isinstance(item, dict)
        ),
        "recall_display_restore_traced": bool(
            recall_display_restores
        ),
    }
    report = {
        "passed": all(checks.values()),
        "output_dir": str(output_dir),
        "memory_scope": MEMORY_SCOPE,
        "memory_fact": MEMORY_FACT,
        "checks": checks,
        "seed": {
            key: value for key, value in seed.items() if key != "events"
        },
        "recall": {
            key: value for key, value in recall.items() if key != "events"
        },
        "seed_memory_candidates": seed_candidates,
        "recall_memory_retrievals": recall_retrievals,
        "recall_team_rewrites": recall_team_rewrites,
        "recall_display_restores": recall_display_restores,
        "recall_first_stream_item": recall_item,
        "persistent_snapshot": str(persisted_snapshot),
    }
    report_path = output_dir / "autogen_shared_memory_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Report: {report_path}")
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
