"""Strict Pydantic models for proteomics sample-prep + modeling actions."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

from .base import ActionOutputBase
from .sample_prep_models import GuardrailsOutput


class DiannTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ProteomicsDiann"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = None
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = None


class SageTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ProteomicsSage"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = None
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = None


class IngestPanelTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ProteomicsIngestPanel"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = None
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = None
    panelPath: Optional[str] = None
    panelFormat: str = "open"


class RegisterAbundanceTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ProteomicsRegisterAbundance"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = None
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = None
    source: str = "diann"


class ProteomicsQcTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ProteomicsQc"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = None
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = None


class DlRescoreTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ProteomicsProsit"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = None
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = None


class CasanovoTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ProteomicsCasanovo"
    sampleId: str
    sampleDir: str
    projectPath: Optional[str] = None
    project: Optional[str] = None
    executionScopeId: Optional[str] = None
    resolvedConfig: Optional[dict] = None


class ProteinDeSelectTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = "ProteinDeSelect"
    project: Optional[str] = None
    projectPath: Optional[str] = None
    comparison: Optional[str] = None
    outputDir: Optional[str] = None
    resolvedConfigPath: Optional[str] = None
    stepOverride: Optional[dict] = None


class DiannTaskOutput(ActionOutputBase):
    sampleId: Optional[str] = None
    reportTsv: Optional[str] = None


class SageTaskOutput(ActionOutputBase):
    sampleId: Optional[str] = None
    reportTsv: Optional[str] = None
    lfqTsv: Optional[str] = None


class AbundanceTaskOutput(ActionOutputBase):
    sampleId: Optional[str] = None
    abundanceH5: Optional[str] = None
    n_proteins: int = 0
    source: Optional[str] = None


class DlRescoreTaskOutput(ActionOutputBase):
    sampleId: Optional[str] = None
    reportTsv: Optional[str] = None
    n_ids_before: Optional[int] = None
    n_ids_after: Optional[int] = None


class CasanovoTaskOutput(ActionOutputBase):
    sampleId: Optional[str] = None
    peptidesCsv: Optional[str] = None
    n_peptides: int = 0


class ProteomicsQcTaskOutput(ActionOutputBase):
    sampleId: str
    qcPath: str
    guardrails: GuardrailsOutput
    proteomicsQcPass: Optional[bool] = None


class ProteinDeSelectTaskOutput(ActionOutputBase):
    comparison: Optional[str] = None
    control_label: Optional[str] = None
    disease_label: Optional[str] = None
    n_control: int = 0
    n_disease: int = 0
    n_proteins_selected: int = 0
    balanced_accuracy: Optional[float] = None
    protein_panel_csv: Optional[str] = None
    feature_mode: str = "proteomics_abundance"
