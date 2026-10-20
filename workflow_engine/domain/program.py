"""Re-export the platform DomainProgram IR.

The models live in the methyl-gateway wheel (``domain.program``, GoliathApp
PR #7). This repository keeps ``workflow_engine`` on ``sys.path`` for ``ops``
and ``rest``, so ``import domain`` binds to this fixture tree first. Load the
installed module by file path and re-export it. There is no second model copy.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_EXPORTS = (
    "ActionStep",
    "AssignStep",
    "CollectionInSpec",
    "DomainProgram",
    "EventTriggerSpec",
    "ForeachSpec",
    "ForeachStep",
    "IfStep",
    "ParallelStep",
    "ProgramPhase",
    "ProgramStep",
    "RepeatStep",
    "SwitchStep",
    "VariableDecl",
    "WaitSpec",
    "WaitStep",
    "WhileStep",
)


def _installed_program_path() -> Path:
    here = Path(__file__).resolve()
    for entry in list(sys.path):
        candidate = Path(entry) / "domain" / "program.py"
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and resolved != here:
            return resolved
    raise ImportError(
        "domain.program is not installed. Install the methyl-gateway wheel "
        "from GoliathApp PR #7 (the platform DomainProgram IR)."
    )


def _load_installed_program() -> ModuleType:
    path = _installed_program_path()
    spec = importlib.util.spec_from_file_location("goliathapp_domain_program", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load DomainProgram IR from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_installed = _load_installed_program()
CollectionInSpec = _installed.CollectionInSpec
ForeachSpec = _installed.ForeachSpec
VariableDecl = _installed.VariableDecl
ActionStep = _installed.ActionStep
AssignStep = _installed.AssignStep
IfStep = _installed.IfStep
SwitchStep = _installed.SwitchStep
WhileStep = _installed.WhileStep
RepeatStep = _installed.RepeatStep
ForeachStep = _installed.ForeachStep
ParallelStep = _installed.ParallelStep
EventTriggerSpec = _installed.EventTriggerSpec
WaitSpec = _installed.WaitSpec
WaitStep = _installed.WaitStep
ProgramStep = _installed.ProgramStep
ProgramPhase = _installed.ProgramPhase
DomainProgram = _installed.DomainProgram

__all__ = list(_EXPORTS)
