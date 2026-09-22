"""Task I/O models for context.* workflow actions."""

from __future__ import annotations

from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .base import ActionOutputBase


class ResolveProjectTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    projectPath: str
    monteCarloRunsRoot: Optional[str] = Field(
        default=None,
        description="When set with cohortPathsList, materialize centroidSeedGroups.",
    )
    cohortPathsList: Optional[List[Tuple[str, List[str]]]] = Field(
        default=None,
        description="Optional list of (group_label, sample_paths) for MC seed centroids.",
    )


class ResolveProjectTaskOutput(ActionOutputBase):
    resolvedProject: dict = Field(description="Tagged ResolvedProject domain object.")
