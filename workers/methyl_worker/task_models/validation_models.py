"""Strict Pydantic models for validation workflow actions."""

from __future__ import annotations

from typing import List, Literal, Optional

from methyl_validation.planner_models import (
    CentroidSeedGroup,
    ValidationPlannedIteration,
)
from pydantic import BaseModel, ConfigDict, Field

from .base import ActionOutputBase


class ValidationPlanTaskOutput(ActionOutputBase):
    projectPath: Optional[str] = None
    n_iterations: int = 0
    centroidSeedGroups: List[CentroidSeedGroup] = Field(default_factory=list)
    iterations: List[ValidationPlannedIteration] = Field(default_factory=list)


class StabilityTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    monteCarloRunsRoot: Optional[str] = None
    outputDir: Optional[str] = None


class PrepareFreezeTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    monteCarloRunsRoot: Optional[str] = None
    stableDmpCsv: Optional[str] = None
    productionOutputDir: Optional[str] = None
    sourceRunDir: Optional[str] = None
    targetRunDir: Optional[str] = None


class FinalizeFreezeModelBundleTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    productionOutputDir: Optional[str] = None
    monteCarloRunsRoot: Optional[str] = None


class FreezeReadinessTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    outputDir: Optional[str] = None


class LinkArtifactsTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    sourceRunDir: Optional[str] = None
    targetRunDir: Optional[str] = None
    runDir: Optional[str] = None


class ModelBundleTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    bundleDir: Optional[str] = None
    bundleH5: Optional[str] = None


class ModelTrainTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    backend: Optional[str] = None
    bundleDir: Optional[str] = None


class ModelPredictTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    backend: Optional[str] = None
    bundleDir: Optional[str] = None


class ModelMcTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    monteCarloRunsRoot: Optional[str] = None
    modelMcRoot: Optional[str] = None
    backends: Optional[List[str]] = None
    productionOutputDir: Optional[str] = None
    resume: Optional[int] = Field(default=None, ge=0)
    requireArtifactReuse: bool = Field(
        default=False,
        description=(
            "Require every model-MC split to reuse existing primary MC centroids and "
            "detections; fail instead of recomputing when artifacts are missing or incompatible."
        ),
    )
    featureIterations: Optional[int] = Field(default=None, ge=0)
    qualityIterations: Optional[int] = Field(default=None, ge=0)


class SelectBestModelTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    monteCarloRunsRoot: Optional[str] = None
    modelMcRoot: Optional[str] = None
    selectionMetric: Optional[str] = None


class PostModelValidationTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: Optional[str] = None
    projectPath: Optional[str] = None
    project: Optional[str] = None
    monteCarloRunsRoot: Optional[str] = None
    outputDir: Optional[str] = None
    runDir: Optional[str] = Field(
        default=None,
        description="Deprecated alias for outputDir; retained for existing compiled workflows.",
    )
    productionOutputDir: Optional[str] = None
    testControlCsv: Optional[str] = None
    testDiseaseCsv: Optional[str] = None
    valControlCsv: Optional[str] = Field(
        default=None,
        description="Deprecated alias for testControlCsv.",
    )
    valDiseaseCsv: Optional[str] = Field(
        default=None,
        description="Deprecated alias for testDiseaseCsv.",
    )
    testGroupsJson: Optional[str] = None
    valGroupsJson: Optional[str] = Field(
        default=None,
        description="Deprecated alias for testGroupsJson.",
    )


class StabilitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_iterations: Optional[int] = None
    n_stable_dmps: Optional[int] = None
    n_stable_genes: Optional[int] = None
    dmp_min_frequency: Optional[float] = None
    gene_min_frequency: Optional[float] = None
    summary_json_path: Optional[str] = None


class ValidationStabilityOutput(ActionOutputBase):
    outputDir: str
    summary: StabilitySummary


class ValidationPrepareFreezeOutput(ActionOutputBase):
    productionOutputDir: Optional[str] = None
    sourceRunDir: Optional[str] = None
    targetRunDir: Optional[str] = None
    projectPath: Optional[str] = None
    fixedDmpPanel: Optional[str] = None
    # Bound into workflow scope so freeze detector/mapper use production centroids,
    # not study-root paths left from initial context enrichment.
    centroid1Dir: Optional[str] = None
    centroid2Dir: Optional[str] = None
    detectOutDir: Optional[str] = None


class ValidationFinalizeFreezeModelBundleOutput(ActionOutputBase):
    projectPath: Optional[str] = None
    modelBundleDir: Optional[str] = None
    n_mapper_rows: int = 0
    n_genes: int = 0


class ValidationFreezeReadinessOutput(ActionOutputBase):
    ready: bool = False
    outputDir: Optional[str] = None
    missing_artifacts: List[str] = Field(default_factory=list)


class ValidationLinkArtifactsOutput(ActionOutputBase):
    bundleDir: Optional[str] = None
    linked_files: List[str] = Field(default_factory=list)


class ValidationModelBundleOutput(ActionOutputBase):
    bundleDir: Optional[str] = None
    bundleH5: Optional[str] = None


class ValidationModelTrainOutput(ActionOutputBase):
    model_path: Optional[str] = None
    backend: Optional[str] = None


class ValidationModelPredictOutput(ActionOutputBase):
    predictions_path: Optional[str] = None
    n_samples: Optional[int] = None


class ValidationModelMcOutput(ActionOutputBase):
    modelMcRoot: Optional[str] = None
    n_iterations: Optional[int] = None


class ValidationSelectBestModelOutput(ActionOutputBase):
    selectedBackend: Optional[str] = None
    selectionMetric: Optional[str] = None
    selectionStat: Optional[float] = None


class ValidationPostModelValidationOutput(ActionOutputBase):
    outputDir: Optional[str] = None
    report_path: Optional[str] = None
    passed: Optional[bool] = None


class TaskErrorOutput(BaseModel):
    """Worker failure payload on submit with result_code < 0."""

    model_config = ConfigDict(extra="forbid")

    error: str
    capability: Optional[str] = None
