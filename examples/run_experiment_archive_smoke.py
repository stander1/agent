from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from agent_runtime.eval.experiment_archive import (
    AGENTLITE_SESSION_RESULT_FILE,
    directory_digest,
    sha256_file,
    verify_bound_experiment,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify immutable experiment and exact session/provider binding."
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    experiment_dir = output_dir / "experiment"
    data_dir = output_dir / "live_agentlite_data"
    app = output_dir / "bound_archive_app.py"
    app.write_text(
        "\n".join(
            [
                "import json, os",
                "from pathlib import Path",
                "from agent_runtime.eval.experiment_archive import initialize_experiment_archive, complete_experiment_archive",
                "out = Path(os.environ['AGENTLITE_EXPERIMENT_DIR'])",
                "identity = initialize_experiment_archive(output_dir=out, scenario_id='release-gate', experiment_mode='managed')",
                "usage = {'calls': 2, 'llm_prompt_tokens': 11, 'llm_completion_tokens': 5, 'llm_total_tokens': 16, 'binding': identity.binding()}",
                "usage_path = out / 'llm_usage_summary.json'",
                "usage_path.write_text(json.dumps(usage), encoding='utf-8')",
                "complete_experiment_archive(identity, summary={'llm_total_tokens': 16}, artifact_paths=[usage_path])",
            ]
        ),
        encoding="utf-8",
    )
    command = [
        args.python,
        "-m",
        "agent_runtime.cli",
        "autogen",
        "--data-dir",
        str(data_dir),
        "--experiment-dir",
        str(experiment_dir),
        "--",
        args.python,
        str(app),
    ]
    first = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    run_manifest = experiment_dir / "experiment_run.json"
    before_hash = sha256_file(run_manifest) if run_manifest.is_file() else ""
    second = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    after_hash = sha256_file(run_manifest) if run_manifest.is_file() else ""

    try:
        verified = verify_bound_experiment(experiment_dir)
        verification_error = ""
    except (OSError, ValueError) as exc:
        verified = {}
        verification_error = f"{type(exc).__name__}: {exc}"
    session_result_path = experiment_dir / AGENTLITE_SESSION_RESULT_FILE
    session_result = (
        json.loads(session_result_path.read_text(encoding="utf-8"))
        if session_result_path.is_file()
        else {}
    )
    checks = {
        "first_launch_succeeded": first.returncode == 0,
        "binding_verified": bool(verified),
        "session_uses_immutable_archive": (
            verified.get("session_source") == "immutable_archive"
        ),
        "provider_total_bound": _provider_total(experiment_dir) == 16,
        "automatic_json_report_exists": (
            experiment_dir / "agentlite_session_report.json"
        ).is_file(),
        "automatic_markdown_report_exists": (
            experiment_dir / "agentlite_session_report.md"
        ).is_file(),
        "session_result_verified": bool(session_result.get("binding_verified")),
        "session_archive_digest_present": bool(
            (session_result.get("session_archive") or {}).get("sha256")
        ),
        "second_launch_rejected": second.returncode != 0,
        "run_manifest_unchanged": bool(before_hash) and before_hash == after_hash,
    }
    report = {
        "passed": all(checks.values()),
        "checks": checks,
        "experiment_dir": str(experiment_dir),
        "session_id": str(verified.get("session_id") or ""),
        "run_id": str(verified.get("run_id") or ""),
        "verification_error": verification_error,
        "first_returncode": first.returncode,
        "second_returncode": second.returncode,
        "first_stdout": first.stdout,
        "first_stderr": first.stderr,
        "second_stderr": second.stderr,
        "archive_digest": (
            directory_digest(experiment_dir) if experiment_dir.is_dir() else {}
        ),
    }
    report_path = output_dir / "experiment_archive_smoke_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Report: {report_path}")
    print(json.dumps({"passed": report["passed"], "checks": checks}, indent=2))
    return 0 if report["passed"] else 1


def _provider_total(experiment_dir: Path) -> int:
    path = experiment_dir / "llm_usage_summary.json"
    if not path.is_file():
        return 0
    value = json.loads(path.read_text(encoding="utf-8"))
    return int(value.get("llm_total_tokens", 0) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
