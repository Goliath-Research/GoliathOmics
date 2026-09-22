"""Configuration for RNA-Seq alignment/quantification QC guardrails.

Thresholds are operator-set (profile/site ``actionConfig.rna_qc``); the defaults here
are permissive fallbacks so QC runs even when a site has not tuned them. Per the
config-not-code rule, production thresholds belong in profile/site config.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class RnaQcGuardrailConfig(BaseModel):
    # Ignore unrelated keys the resolver may fold in (e.g. rna_reference paths).
    model_config = ConfigDict(extra="ignore")

    min_mapping_rate: float = 0.5
    min_uniquely_mapped_rate: float = 0.4
    min_pseudoalignment_rate: float = 0.4
    min_genes_detected: int = 5000
    min_input_reads: int = 1_000_000

    @classmethod
    def from_resolved(cls, cfg: Optional[Dict[str, Any]]) -> "RnaQcGuardrailConfig":
        if not isinstance(cfg, dict):
            return cls()
        # Support a nested "guardrails" block or flat keys.
        nested = cfg.get("guardrails")
        if isinstance(nested, dict):
            return cls(**nested)
        return cls(**cfg)
