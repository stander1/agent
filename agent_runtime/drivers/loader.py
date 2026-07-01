from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agent_runtime.bootstrap.startup import BootstrapContext


BUILTIN_DRIVERS = {
    "autogen": "agent_runtime.drivers.autogen",
    "probe": "agent_runtime.drivers.probe",
}


@dataclass(slots=True)
class DriverActivation:
    driver: str
    status: str
    hooks_active: bool
    framework_available: bool | None = None
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def load_and_activate_driver(
    context: BootstrapContext,
) -> DriverActivation:
    module_name = context.driver_override or BUILTIN_DRIVERS.get(
        context.framework
    )
    if not module_name:
        supported = ", ".join(sorted(BUILTIN_DRIVERS))
        raise ValueError(
            f"unknown framework '{context.framework}'; supported: {supported}"
        )

    module = importlib.import_module(module_name)
    activate = getattr(module, "activate", None)
    if not callable(activate):
        raise TypeError(f"driver module '{module_name}' has no activate(context)")
    result = activate(context)
    if not isinstance(result, DriverActivation):
        raise TypeError(
            f"driver '{module_name}' returned {type(result).__name__}, "
            "expected DriverActivation"
        )
    return result
