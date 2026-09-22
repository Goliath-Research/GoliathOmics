"""
Tagged domain value types stored in wf.scope_variable as JSON objects.

Every model includes a literal ``$type`` field for discrimination.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from .fastq_storage import FastqSourceLocation

DomainTypeName = Literal[
    "MethylIngestRef",
    "MethylSampleRef",
    "AlignmentQcRef",
    "ExtractionQcRef",
    "FragmentomicsRef",
    "MethylationMatrixRef",
    "MethylGroup",
    "MethylCentroidRef",
    "MethylDetectionRef",
    "StratifiedCohortDraw",
    "CentroidSeedGroup",
    "ComparisonSpec",
    "ResolvedComparison",
    "ResolvedProject",
]

DOMAIN_TYPE_NAMES: tuple[str, ...] = (
    "MethylIngestRef",
    "MethylSampleRef",
    "AlignmentQcRef",
    "ExtractionQcRef",
    "FragmentomicsRef",
    "MethylationMatrixRef",
    "MethylGroup",
    "MethylCentroidRef",
    "MethylDetectionRef",
    "StratifiedCohortDraw",
    "CentroidSeedGroup",
    "ComparisonSpec",
    "ResolvedComparison",
    "ResolvedProject",
)


def domain_type_names() -> List[str]:
    return list(DOMAIN_TYPE_NAMES)


class DomainTaggedModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class MethylIngestRef(DomainTaggedModel):
    """Pre-download sample ingest descriptor (structured source known, files not yet local)."""

    type: Literal["MethylIngestRef"] = Field(alias="$type", default="MethylIngestRef")
    sampleId: str
    sampleDir: Optional[str] = None
    fastqSource: Optional[FastqSourceLocation] = None
    fastqUris: Optional[List[str]] = None
    fileCount: Optional[int] = None


class AlignmentQcRef(DomainTaggedModel):
    """Alignment QC JSON artifact and pass/fail gate."""

    type: Literal["AlignmentQcRef"] = Field(alias="$type", default="AlignmentQcRef")
    qcPath: str
    overallPass: bool
    guardrails: Optional[Dict[str, Any]] = None


class ExtractionQcRef(DomainTaggedModel):
    """Post-extraction QC JSON artifact and pass/fail gate."""

    type: Literal["ExtractionQcRef"] = Field(alias="$type", default="ExtractionQcRef")
    qcPath: str
    overallPass: bool
    guardrails: Optional[Dict[str, Any]] = None


class FragmentomicsRef(DomainTaggedModel):
    """cfDNA fragmentomics run artifacts."""

    type: Literal["FragmentomicsRef"] = Field(alias="$type", default="FragmentomicsRef")
    outputDir: str
    summaryPath: Optional[str] = None


class MethylationMatrixRef(DomainTaggedModel):
    """Post-extract per-chromosome HDF5 methylation matrices on disk."""

    type: Literal["MethylationMatrixRef"] = Field(
        alias="$type", default="MethylationMatrixRef"
    )
    sampleDir: str
    chromosomes: List[str] = Field(default_factory=list)
    contexts: List[str] = Field(default_factory=lambda: ["CG"])
    h5Pattern: str = "{chr}-{ctx}.h5"
    h5Files: Optional[List[str]] = None


class MethylSampleRef(DomainTaggedModel):
    """Sample lifecycle handle; fields accumulate through sample prep."""

    type: Literal["MethylSampleRef"] = Field(alias="$type", default="MethylSampleRef")
    sampleId: str
    sampleDir: str
    fastqFiles: Optional[List[str]] = None
    bamPath: Optional[str] = None
    metricsJson: Optional[str] = None
    alignmentQc: Optional[AlignmentQcRef] = None
    extractionQc: Optional[ExtractionQcRef] = None
    fragmentomics: Optional[FragmentomicsRef] = None
    methylation: Optional[MethylationMatrixRef] = None
    h5Archive: Optional[Dict[str, Any]] = None
    status: Optional[str] = None


class MethylGroup(DomainTaggedModel):
    """Cohort of samples (static project group or MC train/val draw)."""

    type: Literal["MethylGroup"] = Field(alias="$type", default="MethylGroup")
    label: str
    role: Optional[Literal["control", "disease", "stage", "other"]] = None
    sampleRefs: Optional[List[MethylSampleRef]] = None
    sampleCsv: Optional[str] = None
    trainCsv: Optional[str] = None
    testCsv: Optional[str] = None
    valCsv: Optional[str] = None
    count: Optional[int] = None
    projectPath: Optional[str] = None


class MethylCentroidRef(DomainTaggedModel):
    """Centroid HDF5 artifact for one group × chromosome × context."""

    type: Literal["MethylCentroidRef"] = Field(alias="$type", default="MethylCentroidRef")
    groupLabel: str
    outputDir: str
    chromosome: str
    context: str = "CG"
    h5Path: Optional[str] = None


class ComparisonSpecRef(DomainTaggedModel):
    """Pairwise control vs disease comparison."""

    type: Literal["ComparisonSpec"] = Field(alias="$type", default="ComparisonSpec")
    controlGroup: str
    diseaseGroup: str
    comparisonLabel: Optional[str] = None


class McIterationTaskConfig(BaseModel):
    """Structured MC iteration taskConfig embedded in StratifiedCohortDraw / planner output."""

    model_config = ConfigDict(extra="forbid")

    runId: str
    phase: str
    iteration: int = Field(..., ge=0)
    layout: str
    trainFraction: float = Field(..., gt=0.0, lt=1.0)
    seed: Optional[int] = None
    projectJson: str
    runDir: str
    monteCarloRunsRoot: str
    detectorStepOverride: Optional[str] = None
    testControlCsv: Optional[str] = None
    testDiseaseCsv: Optional[str] = None
    testGroupsJson: Optional[str] = None
    valControlCsv: Optional[str] = None
    valDiseaseCsv: Optional[str] = None
    valGroupsJson: Optional[str] = None
    centroidGroup1Override: Optional[str] = None
    centroidGroup2Override: Optional[str] = None
    centroidOverridesByLabel: Optional[Dict[str, str]] = None


class CentroidGroupScope(BaseModel):
    """Per-group centroid wire payload for one MC iteration (workflow FOREACH item)."""

    model_config = ConfigDict(extra="forbid")

    label: str
    addSamples: List[str] = Field(default_factory=list)
    removeSamples: List[str] = Field(default_factory=list)
    centroidDir: str
    centroidSeedDir: Optional[str] = None


class CentroidSeedGroup(DomainTaggedModel):
    """Shared per-cohort centroid seed built before parallel MC iterations."""

    type: Literal["CentroidSeedGroup"] = Field(alias="$type", default="CentroidSeedGroup")
    label: str
    addSamples: List[str] = Field(default_factory=list)
    removeSamples: List[str] = Field(default_factory=list)
    centroidDir: str


class MethylDetectionRef(DomainTaggedModel):
    """Scope-facing detector run summary (paths, not full DMP table)."""

    type: Literal["MethylDetectionRef"] = Field(alias="$type", default="MethylDetectionRef")
    comparisonLabel: str
    controlGroup: str
    diseaseGroup: str
    nDmps: Optional[int] = None
    dmpCsvPath: Optional[str] = None
    outputDir: Optional[str] = None
    chromosome: Optional[str] = None
    context: Optional[str] = None


class StratifiedCohortDraw(DomainTaggedModel):
    """One Monte Carlo stratified subsample draw with train/val groups."""

    type: Literal["StratifiedCohortDraw"] = Field(
        alias="$type", default="StratifiedCohortDraw"
    )
    runId: str
    phase: str
    seed: Optional[int] = None
    trainFraction: Optional[float] = None
    projectPath: str
    groups: List[MethylGroup] = Field(default_factory=list)
    comparisons: List[ComparisonSpecRef] = Field(default_factory=list)
    taskConfig: Optional[McIterationTaskConfig] = None


class ResolvedComparison(DomainTaggedModel):
    """Fully materialized comparison with artifact dirs and per-side sample paths."""

    type: Literal["ResolvedComparison"] = Field(alias="$type", default="ResolvedComparison")
    label: str = Field(description="Comparison label (usually disease group name).")
    controlGroup: str
    diseaseGroup: str
    comparisonLabel: Optional[str] = None
    centroid1Dir: str = Field(description="Control-side centroid output directory.")
    centroid2Dir: str = Field(description="Disease-side centroid output directory.")
    detectOutDir: str = Field(description="Detection output directory for this comparison.")
    mapperOutDir: str = Field(description="Mapper output directory for this comparison.")
    enricherOutDir: str = Field(description="Enricher output directory for this comparison.")
    classifierOutDir: Optional[str] = Field(
        default=None,
        description="Classifier output directory when configured for this comparison.",
    )
    controlSamplePaths: List[str] = Field(
        default_factory=list,
        description="Absolute HDF5/sample paths for the control side.",
    )
    diseaseSamplePaths: List[str] = Field(
        default_factory=list,
        description="Absolute HDF5/sample paths for the disease side.",
    )


class ResolvedProject(DomainTaggedModel):
    """Study manifest materialized for worker actions (paths + cohorts, no re-read of /work)."""

    type: Literal["ResolvedProject"] = Field(alias="$type", default="ResolvedProject")
    projectPath: str = Field(description="Provenance path to study manifest (logging only on worker path).")
    groups: List[MethylGroup] = Field(default_factory=list)
    comparisons: List[ResolvedComparison] = Field(default_factory=list)
    chromosomes: List[str] = Field(default_factory=list)
    contexts: List[str] = Field(default_factory=lambda: ["CG"])
    centroid1Dir: Optional[str] = Field(
        default=None,
        description="Shared control centroid dir for the primary comparison when applicable.",
    )
    centroidSeedGroups: Optional[List[CentroidSeedGroup]] = Field(
        default=None,
        description="Optional MC seed centroid groups when monteCarloRunsRoot is resolved.",
    )


DOMAIN_MODEL_BY_TYPE: Dict[str, type[DomainTaggedModel]] = {
    "MethylIngestRef": MethylIngestRef,
    "MethylSampleRef": MethylSampleRef,
    "AlignmentQcRef": AlignmentQcRef,
    "ExtractionQcRef": ExtractionQcRef,
    "FragmentomicsRef": FragmentomicsRef,
    "MethylationMatrixRef": MethylationMatrixRef,
    "MethylGroup": MethylGroup,
    "MethylCentroidRef": MethylCentroidRef,
    "MethylDetectionRef": MethylDetectionRef,
    "StratifiedCohortDraw": StratifiedCohortDraw,
    "CentroidSeedGroup": CentroidSeedGroup,
    "ComparisonSpec": ComparisonSpecRef,
    "ResolvedComparison": ResolvedComparison,
    "ResolvedProject": ResolvedProject,
}


def parse_domain_value(data: Dict[str, Any]) -> DomainTaggedModel:
    """Validate a tagged JSON object against its domain schema."""
    type_name = data.get("$type") or data.get("type")
    if not type_name or type_name not in DOMAIN_MODEL_BY_TYPE:
        raise ValueError(f"unknown domain $type: {type_name!r}")
    return DOMAIN_MODEL_BY_TYPE[type_name].model_validate(data)


def to_tagged_json(model: DomainTaggedModel) -> Dict[str, Any]:
    """Serialize with ``$type`` key for engine scope storage."""
    payload = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    if "$type" not in payload and "type" in payload:
        payload["$type"] = payload.pop("type")
    return payload
