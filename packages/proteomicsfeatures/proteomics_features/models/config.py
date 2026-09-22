"""Configuration for proteomics differential-abundance selection + classification.

Operator-tunable knobs live in profile/site ``actionConfig.protein_de_select``. Defaults
differ from RNA in transform (log2), normalization (median), and imputation (per-protein
minimum, the classic left-censored proteomics strategy for high, non-random missingness).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProteinDeSelectConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_proteins: int = Field(default=200, ge=1)
    min_mean: float = Field(default=0.0)
    ranking: str = Field(default="abs_t", description="abs_t or signed_fc")
    transform: str = Field(default="log2")
    normalize: str = Field(default="median", description="median | quantile | none")
    impute: str = Field(default="min", description="min | mean | zero | none (left-censored default)")
    missing_fill: Optional[float] = Field(
        default=None,
        description=(
            "Value for features absent from a sample before impute. "
            "null/None means NaN (left-censored; pair with impute=min|mean). "
            "Operator-set in profile/site actionConfig.protein_de_select. "
            "Must be JSON-serializable (do not use Python NaN in schemas)."
        ),
    )
    classifier_method: str = Field(default="logistic_regression")
    cv_folds: int = Field(default=5, ge=2)
    random_state: int = Field(default=13)
    covariates_csv: Optional[str] = None

    @classmethod
    def from_resolved(cls, cfg: Optional[Dict[str, Any]]) -> "ProteinDeSelectConfig":
        return cls(**cfg) if isinstance(cfg, dict) else cls()
