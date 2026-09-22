"""Pydantic step config for pipeline.mhb_mhl (resolved via actionConfig)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class MhbMhlStepConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    output_dir: Optional[str] = Field(
        default=None,
        description="Output directory; default {project_root}/mhb_mhl.",
    )
    chromosomes: Optional[List[str]] = Field(
        default=None,
        description="Chromosome allow-list; default from project chromosomes.",
    )
    context: Optional[Literal["CG"]] = Field(
        default=None,
        description="Haplotype context. Only CG is supported.",
    )
    mode: Optional[Literal["discovery", "locked"]] = Field(
        default=None,
        description=(
            "discovery: Guo/mHap-style LD blocks. locked: operator mhb_bed, skip LD. "
            "Operator-set per procedure/profile."
        ),
    )
    mhb_bed: Optional[str] = Field(
        default=None,
        description="Frozen MHB BED (chrom start end [name]). Required for locked mode.",
    )
    r2_min: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum adjacent-CpG Pearson r² for block extension. Operator-set.",
    )
    p_max: Optional[float] = Field(
        default=None,
        gt=0.0,
        le=1.0,
        description="Maximum p-value for adjacent-CpG correlation. Operator-set.",
    )
    core_window: Optional[int] = Field(
        default=None,
        ge=2,
        le=16,
        description="Seed window size in CpGs (Guo/mHap uses 3). Operator-set.",
    )
    min_cpgs: Optional[int] = Field(
        default=None,
        ge=2,
        description="Minimum CpGs per retained block. Operator-set.",
    )
    min_median_reads: Optional[int] = Field(
        default=None,
        ge=1,
        description="Minimum median overlapping reads per block. Operator-set.",
    )
    mhl_max_length: Optional[int] = Field(
        default=None,
        ge=1,
        le=32,
        description="Maximum consecutive-haplotype length for MHL (Wong uses 10). Operator-set.",
    )
    control_bed: Optional[str] = Field(
        default=None,
        description="Optional BED of panel positive/negative controls to drop from discovery.",
    )
    sample_id_column: str = Field(
        default="sample_id",
        description="Column name for sample identifier in mhl_matrix.csv.",
    )
