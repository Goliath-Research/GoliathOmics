"""Strict Pydantic I/O models for pipeline workflow actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import ActionOutputBase
from .step_override_models import (
    CentroidStepOverride,
    ClassifierStepOverride,
    DmpSelectStepOverride,
    DetectorStepOverride,
    EnricherStepOverride,
    MapperStepOverride,
    PredictorStepOverride,
    ProgressionStepOverride,
)


class CentroidTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    group: Optional[str] = None
    chromosome: Optional[str] = None
    context: Optional[str] = None
    comparison: Optional[str] = None
    outputDir: Optional[str] = None
    centroid1Dir: Optional[str] = None
    centroid2Dir: Optional[str] = None
    centroidSeedDir: Optional[str] = None
    addSamples: Optional[List[str]] = None
    removeSamples: Optional[List[str]] = None
    residualizeCoefDir: Optional[str] = Field(
        default=None,
        description="Optional residualize_fit coefficient directory. Absent = raw betas.",
    )
    stepOverride: Optional[CentroidStepOverride] = None


class CentroidTaskOutput(ActionOutputBase):
    group: Optional[str] = None
    chromosome: Optional[str] = None
    context: Optional[str] = None
    output_dir: Optional[str] = None
    centroid_h5_path: Optional[str] = None
    n_samples: Optional[int] = None
    n_positions: Optional[int] = None


class DetectorTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    group: Optional[str] = None
    chromosome: Optional[str] = None
    context: Optional[str] = None
    comparison: Optional[str] = None
    outputDir: Optional[str] = None
    centroid1Dir: Optional[str] = None
    centroid2Dir: Optional[str] = None
    stepOverride: Optional[DetectorStepOverride] = None
    fixedDmpPanel: Optional[str] = None


class DetectorTaskOutput(ActionOutputBase):
    group: Optional[str] = None
    chromosome: Optional[str] = None
    context: Optional[str] = None
    output_dir: Optional[str] = None
    n_statistical_dmps: Optional[int] = None
    n_biological_dmps: Optional[int] = None
    discovery_csv: Optional[str] = None
    result_json_path: Optional[str] = None


class MapperTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    group: Optional[str] = None
    stepOverride: Optional[MapperStepOverride] = None


class MapperTaskOutput(ActionOutputBase):
    group: Optional[str] = None
    chromosome: Optional[str] = None
    context: Optional[str] = None
    output_dir: Optional[str] = None
    n_input_dmps: Optional[int] = None
    n_output_genes: Optional[int] = None
    output_csv: Optional[str] = None
    output_json: Optional[str] = None


class DerivedMeasuresTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[Dict[str, Any]] = None


class DerivedMeasuresTaskOutput(ActionOutputBase):
    output_dir: Optional[str] = None
    output_csv: Optional[str] = None
    n_samples: Optional[int] = None
    n_columns: Optional[int] = None


class CellDeconvolutionTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[Dict[str, Any]] = None


class CellDeconvolutionTaskOutput(ActionOutputBase):
    output_dir: Optional[str] = None
    output_csv: Optional[str] = None
    n_samples: Optional[int] = None
    n_columns: Optional[int] = None
    n_ok: Optional[int] = None


class ResidualizeFitTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[Dict[str, Any]] = None


class ResidualizeFitTaskOutput(ActionOutputBase):
    output_dir: Optional[str] = None
    n_files: Optional[int] = None
    n_train_samples: Optional[int] = None
    manifest_path: Optional[str] = None


class ConfounderScoresTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[Dict[str, Any]] = None


class ConfounderScoresTaskOutput(ActionOutputBase):
    output_dir: Optional[str] = None
    output_csv: Optional[str] = None
    n_samples: Optional[int] = None
    n_columns: Optional[int] = None


class InfoMeasuresTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[Dict[str, Any]] = None


class InfoMeasuresTaskOutput(ActionOutputBase):
    output_dir: Optional[str] = None
    output_csv: Optional[str] = None
    confirmation_report: Optional[str] = None
    n_samples: Optional[int] = None
    n_columns: Optional[int] = None
    n_jsd_windows: Optional[int] = None
    status: Optional[str] = None


class MhbMhlTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[Dict[str, Any]] = None


class MhbMhlTaskOutput(ActionOutputBase):
    output_dir: Optional[str] = None
    mhl_matrix: Optional[str] = None
    mhb_bed: Optional[str] = None
    mhb_qc: Optional[str] = None
    n_samples: Optional[int] = None
    n_blocks: Optional[int] = None
    status: Optional[str] = None


class EnricherTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    comparison: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[EnricherStepOverride] = None


class EnricherTaskOutput(ActionOutputBase):
    comparison: Optional[str] = None
    output_dir: Optional[str] = None
    all_complete: Optional[bool] = None
    completeness_manifest_path: Optional[str] = None
    n_comparisons: Optional[int] = None


class ProgressionTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    orderedComparisonLabels: Optional[List[str]] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[ProgressionStepOverride] = None


class ProgressionTaskOutput(ActionOutputBase):
    output_dir: Optional[str] = None
    n_comparisons: Optional[int] = None
    summary_path: Optional[str] = None


class ClassifierTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    group: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[ClassifierStepOverride] = None
    fixedDmpPanel: Optional[str] = None
    residualizeCoefDir: Optional[str] = Field(
        default=None,
        description=(
            "Optional residualize_fit coefficient directory. Null/absent = raw betas "
            "(shipped path). Bind when scoring residualized centroids."
        ),
    )


class ClassifierTaskOutput(ActionOutputBase):
    output_dir: Optional[str] = None
    model_path: Optional[str] = None
    n_features: Optional[int] = None


class PredictorTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    project: Optional[str] = None
    projectPath: Optional[str] = None
    group: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[PredictorStepOverride] = None
    residualizeCoefDir: Optional[str] = Field(
        default=None,
        description=(
            "Optional residualize_fit coefficient directory. Null/absent = raw betas "
            "(shipped path). Bind when scoring residualized centroids."
        ),
    )


class PredictorTaskOutput(ActionOutputBase):
    output_dir: Optional[str] = None
    validation_metrics_path: Optional[str] = None
    balanced_accuracy: Optional[float] = None


class DmpSelectTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "MethylDmpSelect"
    projectPath: Optional[str] = None
    project: Optional[str] = None
    group: Optional[str] = None
    chromosome: str
    context: Optional[Union[str, List[str]]] = None
    comparison: Optional[str] = None
    discoveryCsv: Optional[str] = None
    outputDir: Optional[str] = None
    stepOverride: Optional[DmpSelectStepOverride] = None

    @model_validator(mode="after")
    def _project_required(self) -> "DmpSelectTaskInput":
        if not (self.projectPath or self.project):
            raise ValueError("projectPath or project is required")
        return self


class DmpSelectTaskOutput(ActionOutputBase):
    chromosome: Optional[str] = None
    n_dmps_discovery: Optional[int] = None
    n_dmps_classifier: Optional[int] = None
    n_dmps_extended: Optional[int] = None
    discovery_csv: Optional[str] = None
    classifier_csv: Optional[str] = None
    extended_csv: Optional[str] = None
    audit_path: Optional[str] = None


class GeneSelectTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "MethylGeneSelect"
    projectPath: Optional[str] = None
    project: Optional[str] = None
    runDir: Optional[str] = None
    comparison: Optional[str] = None
    biomarkerFilter: bool = False

    @model_validator(mode="after")
    def _project_and_run_dir(self) -> "GeneSelectTaskInput":
        if not (self.projectPath or self.project):
            raise ValueError("projectPath or project is required")
        if not self.runDir:
            project = self.projectPath or self.project
            project_path = Path(str(project))
            self.runDir = str(project_path.parent if project_path.suffix else project_path)
        return self


class GeneSelectTaskOutput(ActionOutputBase):
    run_dir: Optional[str] = None
    selected_k: Optional[int] = None
    balanced_accuracy: Optional[float] = None
    genes_classifier_csv: Optional[str] = None
    gene_dmp_loci_csv: Optional[str] = None
    metrics_json: Optional[str] = None


class GeneFeatureSelectTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "MethylGeneFeatureSelect"
    mapperDir: str
    outputDir: str


class GeneFeatureSelectTaskOutput(ActionOutputBase):
    n_features: Optional[int] = None
    output_csv: Optional[str] = None
    audit_path: Optional[str] = None


class BiomarkerFilterSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    mode: Optional[str] = None
    region_hits: List[str] = Field(default_factory=list)
    biomarker_pool_size: Optional[int] = None
    n_genes_in_mapper_after_intersect: Optional[int] = None
    n_mapper_genes_input: Optional[int] = None
    n_after_csv_filters: Optional[int] = None
    n_after_region_hits: Optional[int] = None
    n_after_top_genes_cap: Optional[int] = None
    ppi_fetch_attempted: Optional[bool] = None
    ppi_cache_path: Optional[str] = None
    ppi_score_threshold: Optional[float] = None
    n_string_edges: Optional[int] = None
    n_ppi_hubs: Optional[int] = None
    ppi_skip_reason: Optional[str] = None
    empty_reason: Optional[str] = None


class BiomarkerFilterTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ValidationBiomarkerFilter"
    projectPath: Optional[str] = None
    project: Optional[str] = None
    runDir: Optional[str] = None

    @model_validator(mode="after")
    def _project_required(self) -> "BiomarkerFilterTaskInput":
        if not (self.projectPath or self.project):
            raise ValueError("projectPath or project is required")
        return self


class BiomarkerFilterTaskOutput(ActionOutputBase):
    n_genes: int
    outputCsv: str
    biomarker_filter: BiomarkerFilterSummary
