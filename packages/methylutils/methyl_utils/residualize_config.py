"""Pydantic step configs for residualize and methylation confounder scores."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ResidualizeStepConfig(BaseModel):
    """actionConfig.residualize — tunable knobs default to None (config-not-code)."""

    model_config = ConfigDict(extra="ignore")

    output_dir: Optional[str] = Field(
        default=None,
        description="Coefficient directory. Operator-set; freeze typically {output_base}/production/residualize.",
    )
    covariates_path: Optional[List[str]] = Field(
        default=None,
        description=(
            "CSV paths joined on sample_id (cell_fractions plus confounder_scores). "
            "Operator-set per procedure/profile."
        ),
    )
    numeric_columns: Optional[List[str]] = Field(
        default=None,
        description="Non-composition covariate columns (smoking_score, age_score, bmi_score, crp_score, …).",
    )
    composition_columns: Optional[List[str]] = Field(
        default=None,
        description="Leukocyte Ω columns to encode as Neu-referenced ALR. Omit to skip Ω residualization.",
    )
    composition_reference: Optional[str] = Field(
        default=None,
        description="ALR reference leaf (typically Neu). Operator-set per procedure.",
    )
    composition_pseudocount: Optional[float] = Field(
        default=None,
        gt=0.0,
        description="ALR zero-replacement pseudocount. Operator-set per procedure/profile.",
    )
    variance_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Drop covariates with sample std below this (constant never-smokers, etc.).",
    )
    m_value_eps: Optional[float] = Field(
        default=None,
        gt=0.0,
        lt=0.5,
        description="Clip beta to [eps, 1-eps] before logit / after inverse logit.",
    )
    contexts: Optional[List[str]] = Field(
        default=None,
        description="Methylation contexts to residualize. Operator-set; typically [CG].",
    )
    chromosomes: Optional[List[str]] = Field(
        default=None,
        description="Chromosome allow-list; default from the study project chromosomes.",
    )
    min_coverage: Optional[int] = Field(
        default=None,
        ge=1,
        description="Minimum mC+uC for a CpG to enter the M-value matrix.",
    )
    sample_id_column: Optional[str] = Field(
        default=None,
        description="Covariate CSV sample-id column name. Operator-set; typically sample_id.",
    )


class ConfounderScoresStepConfig(BaseModel):
    """actionConfig.methylation_confounder_scores — panel paths, no science defaults."""

    model_config = ConfigDict(extra="ignore")

    output_dir: Optional[str] = Field(
        default=None,
        description="Output directory; typically {output_base}/confounder_scores.",
    )
    smoking_panel_path: Optional[str] = Field(
        default=None,
        description="JSON panel (hg38 sites + weights) for the smoking score. Operator-set path.",
    )
    age_clock_path: Optional[str] = Field(
        default=None,
        description="JSON clock coefficients. Procedure default is Hannum 2013 (hannum2013_v1.json); operators may overlay Horvath/PhenoAge.",
    )
    bmi_panel_path: Optional[str] = Field(
        default=None,
        description="JSON panel for the BMI/adiposity methylation score. Operator-set path.",
    )
    inflammation_panel_path: Optional[str] = Field(
        default=None,
        description="JSON panel for the CRP/inflammation methylation score. Operator-set path.",
    )
    contexts: Optional[List[str]] = Field(
        default=None,
        description="Contexts to read for score CpGs. Operator-set; typically [CG].",
    )
    min_coverage: Optional[int] = Field(
        default=None,
        ge=1,
        description="Minimum coverage at a panel CpG to include it in the weighted score.",
    )
    min_sites_fraction: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum fraction of panel sites observed or the score is marked insufficient.",
    )
