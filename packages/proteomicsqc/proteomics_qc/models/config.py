"""Configuration for proteomics QC guardrails (operator-set via actionConfig.proteomics_qc)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class ProteomicsQcGuardrailConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    min_proteins_identified: int = 300
    max_missing_fraction: float = 0.9
    expected_proteins: Optional[int] = None  # panel size for missingness (else missingness skipped)

    @classmethod
    def from_resolved(cls, cfg: Optional[Dict[str, Any]]) -> "ProteomicsQcGuardrailConfig":
        if not isinstance(cfg, dict):
            return cls()
        nested = cfg.get("guardrails")
        if isinstance(nested, dict):
            return cls(**nested)
        return cls(**cfg)
