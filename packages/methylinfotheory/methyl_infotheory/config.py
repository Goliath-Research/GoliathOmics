"""Pydantic step config for pipeline.info_measures (resolved via actionConfig)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class InfoTheoryStepConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    output_dir: Optional[str] = Field(
        default=None,
        description="Output directory; default {project_root}/info_measures.",
    )
    contexts: Optional[List[Literal["CG", "CHG", "CHH"]]] = Field(
        default=None,
        description="Methylation contexts to scan; default CG only.",
    )
    chromosomes: Optional[List[str]] = Field(
        default=None,
        description="Chromosome allow-list; default from project chromosomes.",
    )
    tile_size: Optional[int] = Field(
        default=None,
        ge=2,
        le=16,
        description="Expected tile width k in sidecar files. Operator-set per profile/site.",
    )
    min_tile_reads: Optional[int] = Field(
        default=None,
        ge=1,
        description="Minimum reads per tile for inclusion in aggregates.",
    )
    jsd_top_windows: Optional[int] = Field(
        default=None,
        ge=1,
        description="Number of top JSD windows to retain in confirmation report.",
    )
    jsd_min_cohort_reads: Optional[int] = Field(
        default=None,
        ge=1,
        description="Minimum summed reads per tile per cohort for JSD computation.",
    )
    sample_id_column: str = Field(
        default="sample_id",
        description="Column name for sample identifier in readlevel_measures.csv.",
    )
    mapper_gene_csv: Optional[str] = Field(
        default=None,
        description="Optional path to mapper all-gene_name-combined.csv for gene concordance.",
    )
    dmp_panel_csv: Optional[str] = Field(
        default=None,
        description="Optional path to DMP discovery/selected CSV for locus overlap concordance.",
    )
    ising_enabled: Optional[bool] = Field(
        default=None,
        description="Enable v2 equilibrium Ising/max-entropy model per tile. Operator-set per profile/site.",
    )
    ising_max_iter: Optional[int] = Field(
        default=None,
        ge=1,
        description="Maximum Newton iterations for batched Ising fit.",
    )
    ising_tol: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="Convergence tolerance on moment-matching residual.",
    )
    ising_l2: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="L2 ridge on Ising parameters for sparse tiles.",
    )
    ising_coupling: Optional[Literal["nearest", "all"]] = Field(
        default=None,
        description="Pairwise coupling structure: nearest-neighbor or all-pairs within tile.",
    )
    ising_min_tile_reads: Optional[int] = Field(
        default=None,
        ge=1,
        description="Minimum reads per tile for Ising model fitting (may exceed min_tile_reads).",
    )
    prefer_gpu: Optional[bool] = Field(
        default=None,
        description="Prefer GPU via methyl_utils array backend; None = auto.",
    )
    ising_batch_tiles: Optional[int] = Field(
        default=None,
        ge=1,
        description="Max tiles per GPU batch during Ising fit.",
    )
    dynamics_enabled: Optional[bool] = Field(
        default=None,
        description="Enable dynamic measures (channel capacity, RDE, turnover); deferred in v2 phase 1.",
    )
