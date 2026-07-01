from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agent_runtime.drivers.loader import DriverActivation

if TYPE_CHECKING:
    from agent_runtime.bootstrap.startup import BootstrapContext


def activate(context: BootstrapContext) -> DriverActivation:
    os.environ["AGENTLITE_PROBE_DRIVER_ACTIVE"] = "1"
    os.environ["AGENTLITE_ACTIVE_SESSION_ID"] = context.session_id
    return DriverActivation(
        driver="probe",
        status="active",
        hooks_active=True,
        framework_available=True,
        details={"purpose": "launcher bootstrap verification"},
    )
