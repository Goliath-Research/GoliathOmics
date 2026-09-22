"""Configuration for extraction QC guardrails."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_EXPECTED_CHROMOSOMES: List[str] = [str(i) for i in range(1, 23)] + ["X", "Y"]


class ExtractionQCGuardrailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_cpg_weighted_mean_coverage: float = 10.0
    max_chh_methylation_level: float = 0.02
    max_chg_methylation_level: float = 0.02
    min_autosomal_coverage_uniformity_ratio: float = 0.5
    max_discard_fraction: float = 0.9
    min_on_target_fraction: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum fraction of extracted CG sites inside target_panel_bed. Operator-set.",
    )
    min_on_target_mean_coverage: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Minimum mean coverage of on-target CG sites. Operator-set.",
    )
    min_pos_control_methylation: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum mean methylation in positive-control BED. Operator-set.",
    )
    max_neg_control_methylation: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Maximum mean methylation in negative-control BED. Operator-set.",
    )


class ExtractionQCConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guardrails: ExtractionQCGuardrailConfig = Field(default_factory=ExtractionQCGuardrailConfig)
    expected_chromosomes: List[str] = Field(default_factory=lambda: list(DEFAULT_EXPECTED_CHROMOSOMES))
    sample_paths: List[str] = Field(default_factory=list)
    target_panel_bed: Optional[str] = Field(
        default=None,
        description="Capture panel BED for on-target QC. Operator-set per site/procedure.",
    )
    pos_control_bed: Optional[str] = Field(
        default=None,
        description="Positive-control intervals (expected high methylation).",
    )
    neg_control_bed: Optional[str] = Field(
        default=None,
        description="Negative-control intervals (expected near-zero methylation).",
    )
