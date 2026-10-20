"""DomainProgram JSON IR.

The model definitions live in the platform package ``domain.program``
(methyl-gateway wheel, GoliathApp PR #7). This module re-exports them so
product code has one IR.
"""

from __future__ import annotations

from domain.program import (
    ActionStep,
    AssignStep,
    CollectionInSpec,
    DomainProgram,
    EventTriggerSpec,
    ForeachSpec,
    ForeachStep,
    IfStep,
    ParallelStep,
    ProgramPhase,
    ProgramStep,
    RepeatStep,
    SwitchStep,
    VariableDecl,
    WaitSpec,
    WaitStep,
    WhileStep,
)

__all__ = [
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
]
