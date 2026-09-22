"""Generic differential-feature selection + tabular classification.

Analyte-agnostic core shared by the RNA-Seq and proteomics packs. Replaces the
methylation centroid/detector/ECDF science with:

1. Build a ``samples x features`` matrix for two groups (transform/normalize/impute).
2. Rank features by Welch differential statistic and keep a discriminatory panel.
3. Train a tabular sklearn classifier (optionally stacking covariates) and report
   cross-validated balanced accuracy.

The panel CSV keeps a recurrence-friendly schema (one row per selected feature keyed by
``feature_id``) so ``validation.stability`` counts feature recurrence across MC runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from .matrix import load_feature_matrix

PANEL_FILENAME = "de_panel.csv"
RESULTS_FILENAME = "de_results.json"


class DeSelectConfig(BaseModel):
    """Config for generic differential-feature selection + classification."""

    model_config = ConfigDict(extra="ignore")

    kind: str = "expression"
    feature_mode: str = "rna_expression"
    max_features: int = Field(default=200, ge=1)
    min_mean: float = Field(
        default=0.0,
        description="Drop features whose across-cohort mean (post-transform) is below this floor.",
    )
    ranking: str = Field(default="abs_t", description="abs_t (|Welch t|) or signed_fc.")
    transform: str = Field(default="logcpm")
    normalize: str = Field(default="none")
    impute: str = Field(default="none")
    missing_fill: float = 0.0
    classifier_method: str = Field(default="logistic_regression")
    cv_folds: int = Field(default=5, ge=2)
    random_state: int = Field(default=13)
    covariates_csv: Optional[str] = None

    @classmethod
    def from_resolved(cls, cfg: Optional[Dict[str, Any]]) -> "DeSelectConfig":
        return cls(**cfg) if isinstance(cfg, dict) else cls()


def _welch_stats(a: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean_a = a.mean(axis=0)
    mean_b = b.mean(axis=0)
    log2fc = mean_b - mean_a
    mean_all = np.vstack([a, b]).mean(axis=0)
    try:
        from scipy import stats  # type: ignore

        t_stat, _p = stats.ttest_ind(b, a, axis=0, equal_var=False, nan_policy="omit")
        t_stat = np.nan_to_num(np.asarray(t_stat, dtype=np.float64), nan=0.0)
    except Exception:
        var_a = a.var(axis=0, ddof=1) if a.shape[0] > 1 else np.zeros(a.shape[1])
        var_b = b.var(axis=0, ddof=1) if b.shape[0] > 1 else np.zeros(b.shape[1])
        denom = np.sqrt(var_a / max(a.shape[0], 1) + var_b / max(b.shape[0], 1))
        denom[denom == 0] = np.inf
        t_stat = np.nan_to_num((mean_b - mean_a) / denom, nan=0.0)
    return t_stat, np.asarray(log2fc, dtype=np.float64), mean_all


def select_de_features(
    X: np.ndarray,
    y: np.ndarray,
    feature_ids: Sequence[str],
    *,
    config: DeSelectConfig,
) -> pd.DataFrame:
    """Rank features by differential statistic and return the top-``max_features`` panel."""
    y = np.asarray(y)
    control = X[y == 0]
    disease = X[y == 1]
    if control.shape[0] == 0 or disease.shape[0] == 0:
        raise ValueError("select_de_features requires samples in both classes")

    t_stat, log2fc, mean_all = _welch_stats(control, disease)
    df = pd.DataFrame(
        {
            "feature_id": list(feature_ids),
            "t_stat": t_stat,
            "log2fc": log2fc,
            "mean_value": mean_all,
        }
    )
    if config.min_mean > 0:
        df = df[df["mean_value"] >= config.min_mean]
    df["score"] = df["log2fc"].abs() if config.ranking == "signed_fc" else df["t_stat"].abs()
    df = df.sort_values("score", ascending=False).head(int(config.max_features)).reset_index(drop=True)
    df.insert(0, "rank", np.arange(1, len(df) + 1))
    return df


def _build_estimator(config: DeSelectConfig):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression

    method = str(config.classifier_method).strip().lower()
    if method in ("random_forest", "rf"):
        return RandomForestClassifier(random_state=config.random_state)
    return LogisticRegression(max_iter=1000, random_state=config.random_state)


def _cross_validated_balanced_accuracy(X: np.ndarray, y: np.ndarray, config: DeSelectConfig) -> float:
    from sklearn.metrics import balanced_accuracy_score
    from sklearn.model_selection import StratifiedKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y = np.asarray(y)
    n_min = int(np.min(np.bincount(y))) if len(y) else 0
    folds = max(2, min(int(config.cv_folds), n_min)) if n_min >= 2 else 0
    if folds < 2:
        return float("nan")
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=config.random_state)
    scores: List[float] = []
    for train_idx, test_idx in skf.split(X, y):
        model = make_pipeline(StandardScaler(), _build_estimator(config))
        model.fit(X[train_idx], y[train_idx])
        scores.append(balanced_accuracy_score(y[test_idx], model.predict(X[test_idx])))
    return float(np.mean(scores)) if scores else float("nan")


def _stack_covariates(X: np.ndarray, sample_ids: Sequence[str], covariates_csv: Optional[str]) -> np.ndarray:
    if not covariates_csv:
        return X
    try:
        from methyl_validation.covariate_preprocessor import fit_covariates
    except Exception:
        return X
    if not Path(covariates_csv).is_file():
        return X
    try:
        cov_array, _pre, _meta = fit_covariates(
            str(covariates_csv), list(sample_ids), covariate_id_column="sample_id", strict_join=False
        )
        if cov_array is None:
            return X
        cov = np.asarray(cov_array, dtype=np.float64)
        if cov.ndim == 2 and cov.shape[0] == X.shape[0] and cov.shape[1] > 0:
            return np.concatenate([X, cov], axis=1)
    except Exception:
        return X
    return X


def resolve_two_groups(project_path: str, comparison: Optional[str]) -> Tuple[str, List[str], str, List[str]]:
    """Resolve (control_label, control_dirs, disease_label, disease_dirs) from a project."""
    from methyl_utils import load_project

    project = load_project(project_path)
    groups = project.get_resolved_groups()
    if not groups:
        raise RuntimeError(f"project {project_path} resolved no groups")
    label_to_paths = {str(label): [str(p) for p in paths] for label, paths in groups}
    labels = list(label_to_paths.keys())
    control_tokens = ("healthy", "control", "normal")

    control_label: Optional[str] = None
    disease_label: Optional[str] = None
    if comparison and "_vs_" in comparison:
        left, _, right = comparison.partition("_vs_")
        if left in label_to_paths and right in label_to_paths:
            control_label, disease_label = left, right
    if control_label is None:
        control_label = next(
            (lab for lab in labels if any(tok in lab.lower() for tok in control_tokens)),
            labels[0],
        )
    if disease_label is None:
        remaining = [lab for lab in labels if lab != control_label]
        if comparison and comparison in label_to_paths and comparison != control_label:
            disease_label = comparison
        elif remaining:
            disease_label = remaining[0]
        else:
            raise RuntimeError("could not resolve a disease group distinct from control")
    return control_label, label_to_paths[control_label], disease_label, label_to_paths[disease_label]


def run_de_select(
    *,
    project_path: str,
    output_dir: str | Path,
    comparison: Optional[str] = None,
    config: Optional[Dict[str, Any] | DeSelectConfig] = None,
    panel_filename: str = PANEL_FILENAME,
    results_filename: str = RESULTS_FILENAME,
) -> Dict[str, Any]:
    """Select a differential-feature panel and evaluate a tabular classifier."""
    cfg = config if isinstance(config, DeSelectConfig) else DeSelectConfig.from_resolved(config)
    control_label, control_dirs, disease_label, disease_dirs = resolve_two_groups(project_path, comparison)

    all_dirs = list(control_dirs) + list(disease_dirs)
    X, feature_ids, sample_ids = load_feature_matrix(
        all_dirs,
        kind=cfg.kind,
        transform=cfg.transform,
        normalize=cfg.normalize,
        impute=cfg.impute,
        missing_fill=cfg.missing_fill,
    )
    y = np.asarray([0] * len(control_dirs) + [1] * len(disease_dirs))

    panel = select_de_features(X, y, feature_ids, config=cfg)
    panel_features = panel["feature_id"].tolist()
    col_index = {f: j for j, f in enumerate(feature_ids)}
    cols = [col_index[f] for f in panel_features if f in col_index]
    X_panel = X[:, cols] if cols else X[:, :0]

    X_model = _stack_covariates(X_panel, sample_ids, cfg.covariates_csv)
    balanced_accuracy = _cross_validated_balanced_accuracy(X_model, y, cfg)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    panel_path = out_dir / panel_filename
    panel.to_csv(panel_path, index=False)

    results = {
        "comparison": comparison or f"{control_label}_vs_{disease_label}",
        "control_label": control_label,
        "disease_label": disease_label,
        "n_control": len(control_dirs),
        "n_disease": len(disease_dirs),
        "n_features_selected": int(len(panel)),
        "balanced_accuracy": balanced_accuracy,
        "panel_csv": str(panel_path),
        "feature_mode": cfg.feature_mode,
    }
    (out_dir / results_filename).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return results
