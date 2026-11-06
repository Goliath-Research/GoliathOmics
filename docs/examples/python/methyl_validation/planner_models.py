"""Stand-in for packages/methylvalidation planner models (docs smoke only)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CentroidSeedGroup(BaseModel):
    """Placeholder."""

    model_config = ConfigDict(extra="allow")


class ValidationPlannedIteration(BaseModel):
    """Placeholder."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: str = Field(default="StratifiedCohortDraw", alias="$type")
    runId: str = Field(default="stub-run")
    phase: str = Field(default="stub")
    projectPath: str = Field(default="/work/stub")
    groups: List[object] = Field(default_factory=list)
