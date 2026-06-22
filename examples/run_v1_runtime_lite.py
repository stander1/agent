from __future__ import annotations

"""Compatibility launcher for the shared v0/v1 benchmark CLI.

The v1 behavior is selected by ``--mode runtime_lite``; keeping one parser and
runner avoids two launchers drifting into different experiment protocols.
"""

from run_v0_baseline import main


if __name__ == "__main__":
    raise SystemExit(main())
