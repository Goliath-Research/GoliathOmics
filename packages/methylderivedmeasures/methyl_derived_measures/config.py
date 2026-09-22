"""Pydantic step config for pipeline.derived_measures (resolved via actionConfig)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class DerivedMeasuresStepConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    output_dir: Optional[str] = Field(
        default=None,
        description="Output directory; default {project_root}/derived_measures.",
    )
    contexts: Optional[List[Literal["CG", "CHG", "CHH"]]] = Field(
        default=None,
        description="Methylation contexts to scan; default CG only.",
    )
    chromosomes: Optional[List[str]] = Field(
        default=None,
        description="Chromosome allow-list; default from project chromosomes.",
    )
    hypo_beta_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Beta cutoff for global/chromosome hypomethylation burden. Operator-set per profile/site.",
    )
    intermediate_beta_lo: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Lower bound for intermediate-methylation fraction.",
    )
    intermediate_beta_hi: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Upper bound for intermediate-methylation fraction.",
    )
    pmd_beta_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Beta cutoff for PMD / hypomethylation-block load (sliding windows).",
    )
    pmd_window_bp: Optional[int] = Field(
        default=None,
        ge=1000,
        description="Sliding-window size (bp) for PMD load estimation.",
    )
    pmd_step_bp: Optional[int] = Field(
        default=None,
        ge=500,
        description="Sliding-window step (bp) for PMD load estimation.",
    )
    pdr_disagreement_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Adjacent-CpG beta delta threshold for PDR/epipolymorphism proxy.",
    )
    min_coverage: Optional[int] = Field(
        default=None,
        ge=1,
        description="Minimum read depth per CpG for inclusion in aggregates.",
    )
    sample_id_column: str = Field(
        default="sample_id",
        description="Column name for sample identifier in derived_measures.csv.",
    )
