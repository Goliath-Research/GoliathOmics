"""Strict Pydantic models for RNA-Seq sample-prep workflow actions.

Parallel to :mod:`methyl_worker.task_models.sample_prep_models` (WGBS path). RNA-Seq
quantification produces a per-sample expression contract (gene counts / transcript
abundances) instead of methylation HDF5.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .base import ActionOutputBase
from .sample_prep_models import GuardrailsOutput


class ParabricksRnaFq2bamTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ParabricksRnaFq2Bam"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = Field(
        default=None,
        description="Legacy alias of projectPath from older input templates (provenance only).",
    )
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = Field(
        default=None,
        description="Merged actionConfig.rna_align baked at instance configuration.",
    )


class KallistoTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ParabricksKallisto"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = Field(
        default=None,
        description="Legacy alias of projectPath from older input templates (provenance only).",
    )
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = Field(
        default=None,
        description="Merged actionConfig.rna_align baked at instance configuration.",
    )


class RnaQuantTaskOutput(ActionOutputBase):
    sampleId: Optional[str] = None
    bamPath: Optional[str] = None
    geneCountsPath: Optional[str] = None
    abundancePath: Optional[str] = None
    metricsJson: Optional[str] = None


class RnaQcTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "RnaAlignmentQc"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = Field(
        default=None,
        description="Legacy alias of projectPath from older input templates (provenance only).",
    )
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = Field(
        default=None,
        description="Merged actionConfig.rna_qc baked at instance configuration.",
    )


class RnaQcTaskOutput(ActionOutputBase):
    sampleId: str
    qcPath: str
    guardrails: GuardrailsOutput
    rnaQcPass: Optional[bool] = None


class RegisterExpressionTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "RnaRegisterExpression"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = Field(
        default=None,
        description="Legacy alias of projectPath from older input templates (provenance only).",
    )
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = Field(
        default=None,
        description="Merged actionConfig.rna_align baked at instance configuration.",
    )


class RegisterExpressionTaskOutput(ActionOutputBase):
    sampleId: Optional[str] = None
    expressionH5: Optional[str] = None
    n_genes: int = 0
    quantMode: Optional[str] = None


class RnaDeSelectTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "RnaDeSelect"
    project: Optional[str] = None
    projectPath: Optional[str] = None
    comparison: Optional[str] = None
    outputDir: Optional[str] = None
    resolvedConfigPath: Optional[str] = None
    stepOverride: Optional[dict] = None


class RnaDeSelectTaskOutput(ActionOutputBase):
    comparison: Optional[str] = None
    control_label: Optional[str] = None
    disease_label: Optional[str] = None
    n_control: int = 0
    n_disease: int = 0
    n_genes_selected: int = 0
    balanced_accuracy: Optional[float] = None
    gene_panel_csv: Optional[str] = None
    feature_mode: str = "rna_expression"
