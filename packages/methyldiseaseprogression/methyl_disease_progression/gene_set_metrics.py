"""
Backward-compatible re-exports for gene-set overlap metrics.

Prefer :mod:`gene_set_coverage` for new code (plan naming: fractions, nested config).
"""

from __future__ import annotations

from typing import Any, Dict

from .gene_set_coverage import (
    build_gene_set_fractions_json_payload,
    compute_gene_set_fractions,
    gene_set_fractions_summary,
    load_gene_set_profile,
    normalize_progression_gene_set_config,
)


def compute_stage_gene_set_metrics(stage_specs, progression_cfg):
    """Alias for :func:`gene_set_coverage.compute_gene_set_fractions`."""
    return compute_gene_set_fractions(stage_specs, progression_cfg)


def gene_set_metrics_summary(df, raw_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible name; pass progression config as ``raw_cfg`` for full metadata."""
    return gene_set_fractions_summary(df, raw_cfg)


__all__ = [
    "build_gene_set_fractions_json_payload",
    "compute_gene_set_fractions",
    "compute_stage_gene_set_metrics",
    "gene_set_metrics_summary",
    "load_gene_set_profile",
    "normalize_progression_gene_set_config",
]
