"""
Strict Pydantic models for Monte Carlo validation planner I/O.

Shared across methylvalidation, workers, and workflow context_json contracts.
Domain-tagged primitives live in ``methyl_domain.types``; planner aggregates here.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from methyl_domain.types import (
    CentroidGroupScope,
    CentroidSeedGroup,
    ComparisonSpecRef,
    McIterationTaskConfig,
    MethylGroup,
)
from pydantic import BaseModel, ConfigDict, Field


class ValidationPlanRequest(BaseModel):
    """Input for validation.plan-iterations (worker or REST)."""

    model_config = ConfigDict(extra="forbid")

    projectPath: str = Field(..., description="Path to base project.json or project directory")
    featureIterations: Optional[int] = Field(None, ge=1)
    qualityIterations: Optional[int] = Field(None, ge=0)
    seed: Optional[int] = None
    trainFraction: Optional[float] = Field(None, gt=0.0, lt=1.0)
    layout: Optional[str] = Field(None, description="binary | multiclass | hierarchical_multiclass")
    overwrite: bool = False
    workerToolMapper: str = "MethylMapper"
    workerToolEnricher: str = "MethylEnricher"
    workerToolProgression: str = "MethylDiseaseProgression"
    orderedComparisonLabels: Optional[List[str]] = None


class ValidationPlannedIteration(BaseModel):
    """One planned Monte Carlo iteration with typed centroid scope and draw metadata."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["StratifiedCohortDraw"] = Field(
        alias="$type",
        default="StratifiedCohortDraw",
    )
    runId: str
    phase: str
    projectPath: str
    seed: Optional[int] = None
    trainFraction: Optional[float] = None
    groups: List[MethylGroup] = Field(default_factory=list)
    comparisons: List[ComparisonSpecRef] = Field(default_factory=list)
    taskConfig: Optional[McIterationTaskConfig] = None
    run_id: Optional[str] = None
    runDir: Optional[str] = None
    centroidGroups: List[CentroidGroupScope] = Field(default_factory=list)
    centroid1Dir: Optional[str] = None
    centroid2Dir: Optional[str] = None
    detectOutDir: Optional[str] = None
    previousRunDir: Optional[str] = None


class ValidationPlanSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseProject: str
    layout: str
    featureIterations: int
    qualityIterations: int
    seed: Optional[int] = None
    trainFraction: float
    monteCarloRunsRoot: str
    parallelMcCentroidSeed: bool = True
    iterations: List[ValidationPlannedIteration] = Field(default_factory=list)


class ValidationPlanContext(BaseModel):
    """ValidationPipeline context_json produced by the planner."""

    model_config = ConfigDict(extra="forbid")

    projectPath: str
    workerToolMapper: str = "MethylMapper"
    workerToolEnricher: str = "MethylEnricher"
    workerToolProgression: str = "MethylDiseaseProgression"
    orderedComparisonLabels: List[str] = Field(default_factory=list)
    centroidSeedGroups: List[CentroidSeedGroup] = Field(default_factory=list)
    iterations: List[ValidationPlannedIteration] = Field(default_factory=list)
    validationPlan: ValidationPlanSummary


__all__ = [
    "CentroidGroupScope",
    "CentroidSeedGroup",
    "McIterationTaskConfig",
    "ValidationPlanContext",
    "ValidationPlanRequest",
    "ValidationPlannedIteration",
    "ValidationPlanSummary",
]
