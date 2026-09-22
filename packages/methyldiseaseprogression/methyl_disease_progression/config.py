"""
Pydantic models for ``step_config.progression`` in pipeline project JSON.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GeneSetMetricsStepConfig(BaseModel):
    """Nested ``gene_set_metrics`` block (plan-style progression config)."""

    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = Field(
        default=None,
        description="Enable per-stage gene-set overlap metrics.",
    )
    gene_universe: Optional[
        Literal["mapper_all", "mapper_top_n", "mapper_min_weight_quantile"]
    ] = Field(
        default=None,
        description="Gene universe for overlap denominators.",
    )
    top_n: Optional[int] = Field(default=None, ge=1, description="Top-N genes when gene_universe=mapper_top_n.")
    weight_quantile: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Weight quantile when gene_universe=mapper_min_weight_quantile.",
    )
    output_basename: Optional[str] = Field(
        default=None,
        description="Output basename for stage_gene_set_fractions artifacts (without extension).",
    )


class ProgressionStepConfig(BaseModel):
    """
    Settings under ``step_config.progression`` in project JSON.

    Consumed by ``methyl-disease-progression`` and ``methyl-validation --freeze``.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = Field(
        default=False,
        description="Run methyl-disease-progression after methyl-enricher in freeze.",
    )
    ordered_comparison_labels: Optional[List[str]] = Field(
        default=None,
        description="Explicit stage order (comparison_label tokens).",
    )
    ordered_disease_groups: Optional[List[str]] = Field(
        default=None,
        description="Deprecated alias for ordered_comparison_labels (disease_group tokens).",
    )
    strict_missing: bool = Field(
        default=False,
        description="Fail when required mapper/enricher stage inputs are missing.",
    )
    report_md: bool = Field(
        default=False,
        description="Write progression/report.md in addition to CSV/JSON outputs.",
    )
    gene_score_mode: Literal["gene_importance", "effect_x_support"] = Field(
        default="effect_x_support",
        description="Gene scoring mode for progression long tables.",
    )
    ordering_mode: Optional[str] = Field(
        default=None,
        description="When set to auto_gleason, order stages by Gleason-like tokens in comparison labels.",
    )
    disease_context: Optional[str] = Field(
        default=None,
        description="Bundled disease profile key (e.g. prostate_cancer) for gene_set_metrics defaults.",
    )
    gene_set_profile: Optional[Union[str, Dict[str, Any]]] = Field(
        default=None,
        description="External or inline gene-set profile JSON (path string or object).",
    )
    gene_set_metrics: Optional[GeneSetMetricsStepConfig] = Field(
        default=None,
        description="Nested gene-set overlap metrics configuration.",
    )
    gene_set_metrics_enabled: Optional[bool] = Field(
        default=None,
        description="Legacy flat flag to enable gene-set metrics.",
    )
    gene_sets_path: Optional[str] = Field(
        default=None,
        description="Legacy path to gene-set profile JSON.",
    )
    disease_profile: Optional[str] = Field(
        default=None,
        description="Legacy bundled profile key (alias for disease_context resolution).",
    )
    gene_set_denominator: Optional[str] = Field(
        default=None,
        description="Legacy denominator mode: all_genes | top_n | min_weight_quantile.",
    )
    gene_set_top_n: Optional[int] = Field(default=None, ge=1)
    gene_set_weight_quantile: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("gene_score_mode")
    @classmethod
    def _normalize_gene_score_mode(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        allowed = {"gene_importance", "effect_x_support"}
        if normalized not in allowed:
            raise ValueError(f"gene_score_mode must be one of {sorted(allowed)}")
        return normalized

    @field_validator("ordering_mode")
    @classmethod
    def _normalize_ordering_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        return normalized or None
