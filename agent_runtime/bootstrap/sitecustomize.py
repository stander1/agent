from __future__ import annotations

import os
import sys
from pathlib import Path

from agent_runtime.bootstrap.startup import (
    bootstrap_from_environment,
    run_chained_sitecustomize,
)


result = bootstrap_from_environment()
if result is not None and not result.ok:
    message = f"[AgentLite] bootstrap failed: {result.error}\n"
    sys.stderr.write(message)
    sys.stderr.flush()
    if os.environ.get("AGENTLITE_BOOTSTRAP_STRICT", "1") != "0":
        os._exit(78)

run_chained_sitecustomize(Path(__file__))
