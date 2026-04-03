"""
FlowTask Engine — context, template, result, module system, runner.

Lazy imports to avoid circular dependencies.
"""

from .context import Context
from .result import ModuleResult, ModuleError

# Lazy imports for module system (to avoid circular imports with modules.base)
def __getattr__(name):
    if name == "ModuleLoader":
        from .module_loader import ModuleLoader
        return ModuleLoader
    if name == "ModuleNotFoundError":
        from .module_loader import ModuleNotFoundError
        return ModuleNotFoundError
    if name == "BashModuleAdapter":
        from .bash_adapter import BashModuleAdapter
        return BashModuleAdapter
    if name == "Runner":
        from .runner import Runner
        return Runner
    if name == "Playbook":
        from .runner import Playbook
        return Playbook
    if name == "PlaybookResult":
        from .runner import PlaybookResult
        return PlaybookResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "Context",
    "ModuleResult",
    "ModuleError",
    "ModuleLoader",
    "ModuleNotFoundError",
    "BashModuleAdapter",
    "Runner",
    "Playbook",
    "PlaybookResult",
]
