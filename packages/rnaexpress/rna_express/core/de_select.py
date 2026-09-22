"""RNA-Seq DE gene selection + classification (thin adapter over omics_features).

Preserves the RNA public API (``select_de_genes`` returning a ``gene_id`` panel,
``run_rna_de_select`` returning RNA-keyed results) while the generic Welch DE +
tabular-classifier logic lives in :mod:`omics_features.de_select`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import numpy as np
import pandas as pd

from omics_features.de_select import DeSelectConfig, run_de_select, select_de_features

from ..models.config import RnaDeSelectConfig

GENE_PANEL_FILENAME = "rna_de_panel.csv"
RESULTS_FILENAME = "rna_de_results.json"


def _to_generic(cfg: RnaDeSelectConfig) -> DeSelectConfig:
    return DeSelectConfig(
        kind="expression",
        feature_mode="rna_expression",
        max_features=cfg.max_genes,
        min_mean=cfg.min_mean_logcpm,
        ranking="signed_fc" if cfg.ranking == "signed_log2fc" else "abs_t",
        transform=cfg.transform,
        normalize="none",
        impute="none",
        classifier_method=cfg.classifier_method,
        cv_folds=cfg.cv_folds,
        random_state=cfg.random_state,
        covariates_csv=cfg.covariates_csv,
    )


def select_de_genes(
    X: np.ndarray,
    y: np.ndarray,
    gene_ids: Sequence[str],
    *,
    config: RnaDeSelectConfig,
) -> pd.DataFrame:
    """Rank genes by differential expression; returns a panel with a ``gene_id`` column."""
    panel = select_de_features(X, y, gene_ids, config=_to_generic(config))
    return panel.rename(columns={"feature_id": "gene_id", "mean_value": "mean_logcpm"})


def run_rna_de_select(
    *,
    project_path: str,
    output_dir: str | Path,
    comparison: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Select a DE gene panel and evaluate a tabular classifier for one comparison."""
    cfg = _to_generic(RnaDeSelectConfig.from_resolved(config))
    generic = run_de_select(
        project_path=project_path,
        output_dir=output_dir,
        comparison=comparison,
        config=cfg,
        panel_filename=GENE_PANEL_FILENAME,
        results_filename=RESULTS_FILENAME,
    )
    return {
        "comparison": generic["comparison"],
        "control_label": generic["control_label"],
        "disease_label": generic["disease_label"],
        "n_control": generic["n_control"],
        "n_disease": generic["n_disease"],
        "n_genes_selected": generic["n_features_selected"],
        "balanced_accuracy": generic["balanced_accuracy"],
        "gene_panel_csv": generic["panel_csv"],
        "feature_mode": "rna_expression",
    }
