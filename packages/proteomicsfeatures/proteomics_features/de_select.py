"""Proteomics differential-abundance selection (thin adapter over omics_features)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from omics_features.de_select import DeSelectConfig, run_de_select

from .models.config import ProteinDeSelectConfig

PANEL_FILENAME = "protein_de_panel.csv"
RESULTS_FILENAME = "protein_de_results.json"


def _to_generic(cfg: ProteinDeSelectConfig) -> DeSelectConfig:
    # null/None in operator config means NaN fill (JSON-safe; RFC 8259 has no NaN).
    fill = float("nan") if cfg.missing_fill is None else float(cfg.missing_fill)
    return DeSelectConfig(
        kind="abundance",
        feature_mode="proteomics_abundance",
        max_features=cfg.max_proteins,
        min_mean=cfg.min_mean,
        ranking=cfg.ranking,
        transform=cfg.transform,
        normalize=cfg.normalize,
        impute=cfg.impute,
        missing_fill=fill,
        classifier_method=cfg.classifier_method,
        cv_folds=cfg.cv_folds,
        random_state=cfg.random_state,
        covariates_csv=cfg.covariates_csv,
    )


def run_protein_de_select(
    *,
    project_path: str,
    output_dir: str | Path,
    comparison: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Select a differential-abundance protein panel and evaluate a tabular classifier."""
    cfg = _to_generic(ProteinDeSelectConfig.from_resolved(config))
    generic = run_de_select(
        project_path=project_path,
        output_dir=output_dir,
        comparison=comparison,
        config=cfg,
        panel_filename=PANEL_FILENAME,
        results_filename=RESULTS_FILENAME,
    )
    return {
        "comparison": generic["comparison"],
        "control_label": generic["control_label"],
        "disease_label": generic["disease_label"],
        "n_control": generic["n_control"],
        "n_disease": generic["n_disease"],
        "n_proteins_selected": generic["n_features_selected"],
        "balanced_accuracy": generic["balanced_accuracy"],
        "protein_panel_csv": generic["panel_csv"],
        "feature_mode": "proteomics_abundance",
    }
