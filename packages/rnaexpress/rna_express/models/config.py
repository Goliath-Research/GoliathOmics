"""Configuration for RNA-Seq DE gene panel selection + tabular classification.

Operator-tunable knobs live in profile/site ``actionConfig.rna_de_select``; defaults
here are permissive so the action runs unconfigured. Composition/covariate stacking is
optional and delegates to ``methyl_validation.covariate_preprocessor``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class RnaDeSelectConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_genes: int = Field(default=200, ge=1)
    min_mean_logcpm: float = Field(
        default=0.0,
        description="Drop genes whose across-cohort mean log2-CPM is below this floor before ranking.",
    )
    ranking: str = Field(
        default="abs_t",
        description="Gene ranking statistic: abs_t (|Welch t|) or signed_log2fc.",
    )
    transform: str = Field(default="logcpm")
    classifier_method: str = Field(default="logistic_regression")
    cv_folds: int = Field(default=5, ge=2)
    random_state: int = Field(default=13)
    covariates_csv: Optional[str] = Field(
        default=None,
        description="Optional covariate sidecar CSV joined by sample_id and stacked into the classifier.",
    )

    @classmethod
    def from_resolved(cls, cfg: Optional[Dict[str, Any]]) -> "RnaDeSelectConfig":
        return cls(**cfg) if isinstance(cfg, dict) else cls()
