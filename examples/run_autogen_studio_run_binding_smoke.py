from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_runtime.drivers.autogen import DRIVER_PHASE  # noqa: E402
from agent_runtime.launcher import LaunchRequest, ManagedProcessLauncher  # noqa: E402
from web_monitor.parser import list_framework_runs  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate per-Run binding using the AutoGen Studio RunContext contract."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "agentlite"
    app_output = output_dir / "target_output.json"
    result = ManagedProcessLauncher().launch(
        LaunchRequest(
            framework="autogen",
            command=[
                args.python,
                str(PROJECT_ROOT / "examples" / "autogen_studio_run_binding_smoke.py"),
            ],
            cwd=PROJECT_ROOT,
            data_dir=data_dir,
        ),
        environ={
            **os.environ,
            "AGENTLITE_AUTOGEN_STUDIO_BINDING_SMOKE_OUTPUT": str(app_output),
        },
    )
    target = json.loads(app_output.read_text(encoding="utf-8")) if app_output.exists() else {}
    runs = list_framework_runs(data_dir)
    run_ids = {str(item.get("framework_run_id") or "") for item in runs}
    session_dir = data_dir / "sessions" / result.session_id
    trace_path = session_dir / "autogen_driver" / "trace.jsonl"
    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ] if trace_path.exists() else []
    starts = [
        event for event in events
        if event.get("event_type") == "autogen_framework_run_started"
    ]
    finishes = [
        event for event in events
        if event.get("event_type") == "autogen_framework_run_finished"
    ]
    unbound_nested = [
        event
        for event in events
        if event.get("event_type") in {"autogen_agent_output", "autogen_stream_item"}
        and not str((event.get("payload") or {}).get("framework_run_id") or "")
    ]
    manifests = list((session_dir / "autogen_driver" / "runs").glob("*/run.json"))
    checks = {
        "target_returncode_zero": result.returncode == 0,
        "agentlite_active": bool(target.get("agentlite_active")),
        "driver_phase_current": any(
            (event.get("payload") or {}).get("phase") == DRIVER_PHASE
            for event in events
            if event.get("event_type") == "autogen_driver_installed"
        ),
        "two_native_studio_run_ids": run_ids == {
            "autogenstudio:101",
            "autogenstudio:102",
        },
        "two_run_start_events": len(starts) == 2,
        "two_run_finish_events": len(finishes) == 2,
        "two_completed_manifests": len(manifests) == 2 and all(
            json.loads(path.read_text(encoding="utf-8")).get("status") == "completed"
            for path in manifests
        ),
        "nested_events_bound": not unbound_nested,
    }
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "driver_phase": DRIVER_PHASE,
        "session_id": result.session_id,
        "framework_runs": runs,
        "trace_path": str(trace_path),
        "manifest_paths": [str(path) for path in manifests],
        "target_output": target,
    }
    report_path = output_dir / "autogen_studio_run_binding_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
