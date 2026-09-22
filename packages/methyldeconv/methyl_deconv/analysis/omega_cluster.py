"""Ω-cluster cancer detection: leakage-safe stratum analysis on CellDeconv proportions.

Research-first helper (not a DomainProgram node). Fit clusters on train-healthy Ω
only (all six cell types), assign every sample to a stratum, then compare:

- baseline healthy vs disease (single two-group model)
- matched-stratum models (healthy_c_i vs disease assigned to c_i)
- all-pairs models (healthy_c_i vs disease_c_j) when disease is clustered separately

See docs/research/omega-cluster-detection.md.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    balanced_accuracy_score,
    roc_auc_score,
    silhouette_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

OMEGA_COLS: Tuple[str, ...] = ("CD8T", "CD4T", "NK", "Bcell", "Mono", "Neu")


@dataclass
class OmegaClusterConfig:
    """Operator-facing knobs for the research analysis (no invented science defaults in callers)."""

    healthy_group: str = "all"
    disease_group: str = "PCa"
    test_size: float = 0.3
    random_state: int = 13
    k_min: int = 2
    k_max: int = 6
    min_train_per_class: int = 8
    min_test_per_class: int = 3
    qp_status_ok: str = "ok"
    use_clr: bool = True
    lda_residual_sensitivity: bool = True


@dataclass
class ClusterFit:
    k: int
    centroids: np.ndarray  # (k, d) in model space (CLR+optional scale)
    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    silhouette: float
    method: str = "kmeans"


@dataclass
class FoldResult:
    strategy: str
    balanced_accuracy: Optional[float]
    roc_auc: Optional[float]
    n_train: int
    n_test: int
    detail: Dict[str, Any] = field(default_factory=dict)


def clr_transform(X: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Centered log-ratio on composition rows (non-negative, sum≈1)."""
    X = np.asarray(X, dtype=np.float64)
    X = np.clip(X, eps, None)
    X = X / X.sum(axis=1, keepdims=True)
    log_x = np.log(X)
    return log_x - log_x.mean(axis=1, keepdims=True)


def load_cell_fractions(
    path: str | Path,
    *,
    cfg: OmegaClusterConfig,
) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"sample_id", "group", *OMEGA_COLS}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"cell_fractions.csv missing columns: {missing}")
    out = df.copy()
    out["sample_id"] = out["sample_id"].astype(str)
    out["group"] = out["group"].astype(str)
    if "qp_status" in out.columns and cfg.qp_status_ok:
        out = out[out["qp_status"].astype(str) == cfg.qp_status_ok].copy()
    for col in OMEGA_COLS:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=list(OMEGA_COLS))
    if out.empty:
        raise ValueError("No usable rows after qp_status / NaN filter")
    labels = []
    for g in out["group"]:
        if g == cfg.healthy_group:
            labels.append(0)
        elif g == cfg.disease_group:
            labels.append(1)
        else:
            labels.append(-1)
    out["y"] = labels
    out = out[out["y"] >= 0].copy()
    if out["y"].nunique() < 2:
        raise ValueError(
            f"Need both healthy_group={cfg.healthy_group!r} and "
            f"disease_group={cfg.disease_group!r} in the group column"
        )
    return out.reset_index(drop=True)


def _fit_space(X_raw: np.ndarray, *, use_clr: bool) -> np.ndarray:
    return clr_transform(X_raw) if use_clr else np.asarray(X_raw, dtype=np.float64)


def choose_k_kmeans(
    X: np.ndarray,
    *,
    k_min: int,
    k_max: int,
    random_state: int,
) -> Tuple[int, float, KMeans]:
    n = int(X.shape[0])
    k_hi = min(k_max, max(k_min, n - 1))
    if n < k_min + 1:
        raise ValueError(f"Need at least {k_min + 1} samples to cluster; got {n}")
    best_k = k_min
    best_score = -1.0
    best_model: Optional[KMeans] = None
    for k in range(k_min, k_hi + 1):
        if k >= n:
            break
        km = KMeans(n_clusters=k, n_init=10, random_state=random_state)
        labels = km.fit_predict(X)
        if len(set(labels.tolist())) < 2:
            continue
        score = float(silhouette_score(X, labels))
        if score > best_score:
            best_score = score
            best_k = k
            best_model = km
    if best_model is None:
        raise ValueError("Failed to fit any k-means model")
    return best_k, best_score, best_model


def fit_healthy_clusters(
    X_raw: np.ndarray,
    *,
    cfg: OmegaClusterConfig,
) -> ClusterFit:
    X = _fit_space(X_raw, use_clr=cfg.use_clr)
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    k, sil, km = choose_k_kmeans(
        Xs,
        k_min=cfg.k_min,
        k_max=cfg.k_max,
        random_state=cfg.random_state,
    )
    return ClusterFit(
        k=k,
        centroids=np.asarray(km.cluster_centers_, dtype=np.float64),
        scaler_mean=np.asarray(scaler.mean_, dtype=np.float64),
        scaler_scale=np.asarray(scaler.scale_, dtype=np.float64),
        silhouette=sil,
    )


def transform_omega(X_raw: np.ndarray, fit: ClusterFit, *, use_clr: bool) -> np.ndarray:
    X = _fit_space(X_raw, use_clr=use_clr)
    scale = np.where(fit.scaler_scale == 0, 1.0, fit.scaler_scale)
    return (X - fit.scaler_mean) / scale


def assign_strata(X_raw: np.ndarray, fit: ClusterFit, *, use_clr: bool) -> np.ndarray:
    Xs = transform_omega(X_raw, fit, use_clr=use_clr)
    # nearest centroid
    d2 = ((Xs[:, None, :] - fit.centroids[None, :, :]) ** 2).sum(axis=2)
    return np.argmin(d2, axis=1).astype(np.int32)


def _lda_direction(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Simple two-class LDA direction (not full Fisher with shrinkage)."""
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.int32)
    x0 = X[y == 0]
    x1 = X[y == 1]
    if len(x0) < 2 or len(x1) < 2:
        raise ValueError("LDA residual needs ≥2 samples per class")
    mu0 = x0.mean(axis=0)
    mu1 = x1.mean(axis=0)
    sw = np.cov(x0, rowvar=False) + np.cov(x1, rowvar=False)
    sw = sw + 1e-6 * np.eye(sw.shape[0])
    direction = np.linalg.solve(sw, mu1 - mu0)
    nrm = np.linalg.norm(direction)
    if nrm < 1e-12:
        raise ValueError("LDA direction is degenerate")
    return direction / nrm


def project_out_direction(X: np.ndarray, direction: np.ndarray) -> np.ndarray:
    d = direction.reshape(-1)
    return X - np.outer(X @ d, d)


def fit_logistic(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_test: np.ndarray,
    y_test: np.ndarray,
    *,
    random_state: int,
) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    if len(np.unique(y_train)) < 2 or len(np.unique(y_test)) < 2:
        return None, None, {"skipped": "need both classes in train and test"}
    if int((y_train == 0).sum()) < 2 or int((y_train == 1).sum()) < 2:
        return None, None, {"skipped": "too few train samples per class"}
    clf = LogisticRegression(
        max_iter=2000,
        class_weight="balanced",
        random_state=random_state,
    )
    clf.fit(X_train, y_train)
    proba = clf.predict_proba(X_test)[:, 1]
    y_hat = (proba >= 0.5).astype(np.int32)
    ba = float(balanced_accuracy_score(y_test, y_hat))
    try:
        auc = float(roc_auc_score(y_test, proba))
    except ValueError:
        auc = None
    return ba, auc, {"coef_l2": float(np.linalg.norm(clf.coef_))}


def _omega_matrix(df: pd.DataFrame) -> np.ndarray:
    return df.loc[:, list(OMEGA_COLS)].to_numpy(dtype=np.float64)


def evaluate_baseline(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    cfg: OmegaClusterConfig,
) -> FoldResult:
    Xtr = _fit_space(_omega_matrix(train), use_clr=cfg.use_clr)
    Xte = _fit_space(_omega_matrix(test), use_clr=cfg.use_clr)
    scaler = StandardScaler().fit(Xtr)
    ba, auc, detail = fit_logistic(
        scaler.transform(Xtr),
        train["y"].to_numpy(),
        scaler.transform(Xte),
        test["y"].to_numpy(),
        random_state=cfg.random_state,
    )
    return FoldResult(
        strategy="baseline_healthy_vs_disease",
        balanced_accuracy=ba,
        roc_auc=auc,
        n_train=int(len(train)),
        n_test=int(len(test)),
        detail=detail,
    )


def evaluate_matched_stratum(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    stratum_col: str,
    cfg: OmegaClusterConfig,
) -> FoldResult:
    """Route each test sample to its stratum model; skip tiny strata."""
    y_true: List[int] = []
    y_score: List[float] = []
    used = 0
    skipped: List[Dict[str, Any]] = []
    for s in sorted(train[stratum_col].unique()):
        tr = train[train[stratum_col] == s]
        te = test[test[stratum_col] == s]
        n0 = int((tr["y"] == 0).sum())
        n1 = int((tr["y"] == 1).sum())
        if n0 < cfg.min_train_per_class or n1 < cfg.min_train_per_class:
            skipped.append({"stratum": int(s), "reason": "min_train", "n0": n0, "n1": n1})
            continue
        if len(te) < 1 or te["y"].nunique() < 1:
            skipped.append({"stratum": int(s), "reason": "empty_test"})
            continue
        if int((te["y"] == 0).sum()) < 1 or int((te["y"] == 1).sum()) < 1:
            # still score if both classes present in train; for BA need both in test
            if te["y"].nunique() < 2:
                skipped.append({"stratum": int(s), "reason": "test_single_class", "n_test": int(len(te))})
                continue
        Xtr = _fit_space(_omega_matrix(tr), use_clr=cfg.use_clr)
        Xte = _fit_space(_omega_matrix(te), use_clr=cfg.use_clr)
        scaler = StandardScaler().fit(Xtr)
        clf = LogisticRegression(
            max_iter=2000,
            class_weight="balanced",
            random_state=cfg.random_state,
        )
        clf.fit(scaler.transform(Xtr), tr["y"].to_numpy())
        proba = clf.predict_proba(scaler.transform(Xte))[:, 1]
        y_true.extend(te["y"].astype(int).tolist())
        y_score.extend(proba.tolist())
        used += 1
    if len(y_true) < 4 or len(set(y_true)) < 2:
        return FoldResult(
            strategy="matched_stratum_routed",
            balanced_accuracy=None,
            roc_auc=None,
            n_train=int(len(train)),
            n_test=int(len(test)),
            detail={"skipped": skipped, "strata_used": used},
        )
    y_true_a = np.asarray(y_true, dtype=np.int32)
    y_score_a = np.asarray(y_score, dtype=np.float64)
    y_hat = (y_score_a >= 0.5).astype(np.int32)
    ba = float(balanced_accuracy_score(y_true_a, y_hat))
    try:
        auc = float(roc_auc_score(y_true_a, y_score_a))
    except ValueError:
        auc = None
    return FoldResult(
        strategy="matched_stratum_routed",
        balanced_accuracy=ba,
        roc_auc=auc,
        n_train=int(len(train)),
        n_test=int(len(y_true)),
        detail={"skipped": skipped, "strata_used": used},
    )


def evaluate_all_pairs(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    healthy_stratum_col: str,
    disease_stratum_col: str,
    cfg: OmegaClusterConfig,
) -> FoldResult:
    """
    Train healthy_c_i vs disease_c_j models; for each test disease sample use
    the healthy stratum it was assigned to under healthy centroids, paired with
    its disease stratum. Healthy test samples scored against their own healthy
    stratum vs each disease cluster (mean score). Simplified routing:

    - disease test: use model (healthy_stratum[sample], disease_stratum[sample])
    - healthy test: score as 1 - mean P(disease) across disease clusters for that healthy stratum
    """
    pair_models: Dict[Tuple[int, int], Tuple[StandardScaler, LogisticRegression]] = {}
    skipped_pairs: List[Dict[str, Any]] = []
    h_ids = sorted(train.loc[train["y"] == 0, healthy_stratum_col].unique())
    d_ids = sorted(train.loc[train["y"] == 1, disease_stratum_col].unique())
    for hi in h_ids:
        for dj in d_ids:
            tr_h = train[(train["y"] == 0) & (train[healthy_stratum_col] == hi)]
            tr_d = train[(train["y"] == 1) & (train[disease_stratum_col] == dj)]
            if len(tr_h) < cfg.min_train_per_class or len(tr_d) < cfg.min_train_per_class:
                skipped_pairs.append(
                    {
                        "healthy_c": int(hi),
                        "disease_c": int(dj),
                        "n_h": int(len(tr_h)),
                        "n_d": int(len(tr_d)),
                    }
                )
                continue
            tr = pd.concat([tr_h, tr_d], axis=0)
            Xtr = _fit_space(_omega_matrix(tr), use_clr=cfg.use_clr)
            scaler = StandardScaler().fit(Xtr)
            clf = LogisticRegression(
                max_iter=2000,
                class_weight="balanced",
                random_state=cfg.random_state,
            )
            clf.fit(scaler.transform(Xtr), tr["y"].to_numpy())
            pair_models[(int(hi), int(dj))] = (scaler, clf)

    if not pair_models:
        return FoldResult(
            strategy="all_pairs_routed",
            balanced_accuracy=None,
            roc_auc=None,
            n_train=int(len(train)),
            n_test=int(len(test)),
            detail={"skipped_pairs": skipped_pairs, "n_models": 0},
        )

    y_true: List[int] = []
    y_score: List[float] = []
    for row in test.itertuples(index=False):
        y = int(getattr(row, "y"))
        hi = int(getattr(row, healthy_stratum_col))
        omega = np.asarray([[getattr(row, c) for c in OMEGA_COLS]], dtype=np.float64)
        X = _fit_space(omega, use_clr=cfg.use_clr)
        if y == 1:
            dj = int(getattr(row, disease_stratum_col))
            key = (hi, dj)
            if key not in pair_models:
                # fallback: average over available disease clusters for this hi
                scores = []
                for (h2, d2), (scaler, clf) in pair_models.items():
                    if h2 != hi:
                        continue
                    scores.append(float(clf.predict_proba(scaler.transform(X))[0, 1]))
                if not scores:
                    continue
                score = float(np.mean(scores))
            else:
                scaler, clf = pair_models[key]
                score = float(clf.predict_proba(scaler.transform(X))[0, 1])
        else:
            scores = []
            for (h2, d2), (scaler, clf) in pair_models.items():
                if h2 != hi:
                    continue
                scores.append(float(clf.predict_proba(scaler.transform(X))[0, 1]))
            if not scores:
                continue
            score = float(np.mean(scores))
        y_true.append(y)
        y_score.append(score)

    if len(y_true) < 4 or len(set(y_true)) < 2:
        return FoldResult(
            strategy="all_pairs_routed",
            balanced_accuracy=None,
            roc_auc=None,
            n_train=int(len(train)),
            n_test=int(len(test)),
            detail={"skipped_pairs": skipped_pairs, "n_models": len(pair_models)},
        )
    y_true_a = np.asarray(y_true, dtype=np.int32)
    y_score_a = np.asarray(y_score, dtype=np.float64)
    y_hat = (y_score_a >= 0.5).astype(np.int32)
    ba = float(balanced_accuracy_score(y_true_a, y_hat))
    try:
        auc = float(roc_auc_score(y_true_a, y_score_a))
    except ValueError:
        auc = None
    return FoldResult(
        strategy="all_pairs_routed",
        balanced_accuracy=ba,
        roc_auc=auc,
        n_train=int(len(train)),
        n_test=int(len(y_true)),
        detail={"skipped_pairs": skipped_pairs, "n_models": len(pair_models)},
    )


def run_omega_cluster_analysis(
    cell_fractions_path: str | Path,
    output_dir: str | Path,
    *,
    cfg: Optional[OmegaClusterConfig] = None,
) -> Dict[str, Any]:
    cfg = cfg or OmegaClusterConfig()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_cell_fractions(cell_fractions_path, cfg=cfg)
    idx = np.arange(len(df))
    train_idx, test_idx = train_test_split(
        idx,
        test_size=cfg.test_size,
        random_state=cfg.random_state,
        stratify=df["y"].to_numpy(),
    )
    train = df.iloc[train_idx].reset_index(drop=True)
    test = df.iloc[test_idx].reset_index(drop=True)

    # --- Healthy-only clustering on all 6 Ω ---
    healthy_train = train[train["y"] == 0]
    fit_h = fit_healthy_clusters(_omega_matrix(healthy_train), cfg=cfg)
    train = train.copy()
    test = test.copy()
    train["healthy_stratum"] = assign_strata(
        _omega_matrix(train), fit_h, use_clr=cfg.use_clr
    )
    test["healthy_stratum"] = assign_strata(
        _omega_matrix(test), fit_h, use_clr=cfg.use_clr
    )

    # Disease clustered separately (train disease only) for all-pairs
    disease_train = train[train["y"] == 1]
    fit_d = fit_healthy_clusters(_omega_matrix(disease_train), cfg=cfg)
    train["disease_stratum"] = -1
    test["disease_stratum"] = -1
    train.loc[train["y"] == 1, "disease_stratum"] = assign_strata(
        _omega_matrix(train[train["y"] == 1]), fit_d, use_clr=cfg.use_clr
    )
    test.loc[test["y"] == 1, "disease_stratum"] = assign_strata(
        _omega_matrix(test[test["y"] == 1]), fit_d, use_clr=cfg.use_clr
    )
    # Healthy samples: disease stratum unused; set to nearest disease centroid for bookkeeping only
    train.loc[train["y"] == 0, "disease_stratum"] = assign_strata(
        _omega_matrix(train[train["y"] == 0]), fit_d, use_clr=cfg.use_clr
    )
    test.loc[test["y"] == 0, "disease_stratum"] = assign_strata(
        _omega_matrix(test[test["y"] == 0]), fit_d, use_clr=cfg.use_clr
    )

    results: List[FoldResult] = [
        evaluate_baseline(train, test, cfg=cfg),
        evaluate_matched_stratum(
            train, test, stratum_col="healthy_stratum", cfg=cfg
        ),
        evaluate_all_pairs(
            train,
            test,
            healthy_stratum_col="healthy_stratum",
            disease_stratum_col="disease_stratum",
            cfg=cfg,
        ),
    ]

    sensitivity: Dict[str, Any] = {"enabled": False}
    if cfg.lda_residual_sensitivity:
        try:
            X_all_tr = _fit_space(_omega_matrix(train), use_clr=cfg.use_clr)
            direction = _lda_direction(X_all_tr, train["y"].to_numpy())
            X_h = _fit_space(_omega_matrix(healthy_train), use_clr=cfg.use_clr)
            X_h_res = project_out_direction(X_h, direction)
            # Fit clusters in residual space with a dedicated scaler
            scaler = StandardScaler()
            Xs = scaler.fit_transform(X_h_res)
            k, sil, km = choose_k_kmeans(
                Xs,
                k_min=cfg.k_min,
                k_max=cfg.k_max,
                random_state=cfg.random_state,
            )
            fit_res = ClusterFit(
                k=k,
                centroids=np.asarray(km.cluster_centers_, dtype=np.float64),
                scaler_mean=np.asarray(scaler.mean_, dtype=np.float64),
                scaler_scale=np.asarray(scaler.scale_, dtype=np.float64),
                silhouette=sil,
                method="kmeans_lda_residual",
            )

            def _assign_residual(raw: np.ndarray) -> np.ndarray:
                X = project_out_direction(_fit_space(raw, use_clr=cfg.use_clr), direction)
                scale = np.where(fit_res.scaler_scale == 0, 1.0, fit_res.scaler_scale)
                Xs2 = (X - fit_res.scaler_mean) / scale
                d2 = ((Xs2[:, None, :] - fit_res.centroids[None, :, :]) ** 2).sum(axis=2)
                return np.argmin(d2, axis=1).astype(np.int32)

            train_s = train.copy()
            test_s = test.copy()
            train_s["healthy_stratum"] = _assign_residual(_omega_matrix(train_s))
            test_s["healthy_stratum"] = _assign_residual(_omega_matrix(test_s))
            matched_res = evaluate_matched_stratum(
                train_s, test_s, stratum_col="healthy_stratum", cfg=cfg
            )
            sensitivity = {
                "enabled": True,
                "healthy_k": fit_res.k,
                "silhouette": fit_res.silhouette,
                "matched_stratum": asdict(matched_res),
            }
        except Exception as exc:
            sensitivity = {"enabled": True, "error": str(exc)}

    assignments = pd.concat(
        [
            train.assign(split="train"),
            test.assign(split="test"),
        ],
        axis=0,
        ignore_index=True,
    )
    assign_path = output_dir / "omega_stratum_assignments.csv"
    assignments.to_csv(assign_path, index=False)

    # Stratum size table
    size_rows = []
    for split_name, part in (("train", train), ("test", test)):
        for y_val, y_name in ((0, "healthy"), (1, "disease")):
            sub = part[part["y"] == y_val]
            vc = sub["healthy_stratum"].value_counts().sort_index()
            for s, n in vc.items():
                size_rows.append(
                    {
                        "split": split_name,
                        "label": y_name,
                        "healthy_stratum": int(s),
                        "n": int(n),
                    }
                )
    sizes = pd.DataFrame(size_rows)
    sizes_path = output_dir / "omega_stratum_sizes.csv"
    sizes.to_csv(sizes_path, index=False)

    summary: Dict[str, Any] = {
        "cell_fractions_path": str(Path(cell_fractions_path).resolve()),
        "n_samples": int(len(df)),
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "omega_columns": list(OMEGA_COLS),
        "clustering": {
            "dimensions": "all_6",
            "use_clr": cfg.use_clr,
            "healthy_k": fit_h.k,
            "healthy_silhouette": fit_h.silhouette,
            "disease_k": fit_d.k,
            "disease_silhouette": fit_d.silhouette,
        },
        "config": asdict(cfg),
        "results": [asdict(r) for r in results],
        "lda_residual_sensitivity": sensitivity,
        "artifacts": {
            "assignments_csv": str(assign_path),
            "stratum_sizes_csv": str(sizes_path),
        },
        "recommendation": _recommend(results),
    }
    summary_path = output_dir / "omega_cluster_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    summary["artifacts"]["summary_json"] = str(summary_path)

    pca_plot = _try_write_pca_plot(
        assignments, output_dir / "omega_pca_by_stratum.html", cfg=cfg
    )
    if pca_plot is not None:
        summary["artifacts"]["pca_by_stratum_html"] = str(pca_plot)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def _recommend(results: Sequence[FoldResult]) -> Dict[str, Any]:
    by_name = {r.strategy: r for r in results}
    base = by_name.get("baseline_healthy_vs_disease")
    matched = by_name.get("matched_stratum_routed")
    pairs = by_name.get("all_pairs_routed")

    def _key(r: Optional[FoldResult]) -> Tuple[float, float]:
        if r is None or r.balanced_accuracy is None:
            return (-1.0, -1.0)
        auc = r.roc_auc if r.roc_auc is not None else -1.0
        return (float(r.balanced_accuracy), float(auc))

    ranked = sorted(
        [r for r in results if r.balanced_accuracy is not None],
        key=_key,
        reverse=True,
    )
    best = ranked[0] if ranked else None
    matched_beats = bool(
        matched
        and base
        and matched.balanced_accuracy is not None
        and base.balanced_accuracy is not None
        and matched.balanced_accuracy > base.balanced_accuracy + 0.01
    )
    strata_used = int((matched.detail or {}).get("strata_used", 0)) if matched else 0
    # Require multi-stratum coverage so a single large cell cannot alone unlock DomainProgram work.
    stable_strata = strata_used >= 2
    follow_on = bool(
        matched_beats
        and stable_strata
        and best is not None
        and best.strategy == "matched_stratum_routed"
    )
    return {
        "best_strategy": best.strategy if best else None,
        "best_balanced_accuracy": best.balanced_accuracy if best else None,
        "baseline_balanced_accuracy": base.balanced_accuracy if base else None,
        "matched_beats_baseline": matched_beats,
        "matched_strata_used": strata_used,
        "pipeline_follow_on_justified": follow_on,
        "note": (
            "Prefer matched_stratum when it beats baseline with ≥2 usable strata; "
            "defer DomainProgram leaves until pipeline_follow_on_justified."
        ),
        "all_pairs_balanced_accuracy": pairs.balanced_accuracy if pairs else None,
    }


def _pca_showlegend_for_visibility(
    visible: Sequence[bool], legend_keys: Sequence[str]
) -> List[bool]:
    """One legend entry per group among currently visible traces.

    Plotly drops legend rows for invisible traces, so each split filter must
    re-assign ``showlegend`` to a visible member of the legendgroup.
    """
    seen: set[str] = set()
    flags: List[bool] = []
    for vis, key in zip(visible, legend_keys):
        if vis and key not in seen:
            flags.append(True)
            seen.add(key)
        else:
            flags.append(False)
    return flags


def _try_write_pca_plot(
    assignments: pd.DataFrame,
    path: Path,
    *,
    cfg: OmegaClusterConfig,
) -> Optional[Path]:
    """Write an interactive Plotly HTML scatter (PC1/PC2 × stratum × label).

    Includes All / Train / Test buttons so analysts can filter by holdout split.
    """
    try:
        import plotly.graph_objects as go
        from plotly.colors import qualitative
        from sklearn.decomposition import PCA
    except Exception:
        return None
    X = _fit_space(_omega_matrix(assignments), use_clr=cfg.use_clr)
    pcs = PCA(n_components=2, random_state=cfg.random_state).fit_transform(X)
    plot_df = assignments.copy()
    plot_df["PC1"] = pcs[:, 0]
    plot_df["PC2"] = pcs[:, 1]
    plot_df["label"] = plot_df["y"].map({0: "healthy", 1: "disease"})
    plot_df["healthy_stratum"] = plot_df["healthy_stratum"].astype(str)
    if "split" not in plot_df.columns:
        plot_df["split"] = "all"
    plot_df["split"] = plot_df["split"].astype(str)

    def _stratum_key(s: str) -> Tuple[int, str]:
        try:
            return (0, f"{int(s):05d}")
        except (TypeError, ValueError):
            return (1, str(s))

    strata = sorted(plot_df["healthy_stratum"].unique().tolist(), key=_stratum_key)
    labels = ["healthy", "disease"]
    symbols = {"healthy": "circle", "disease": "triangle-up"}
    colors = qualitative.Plotly
    splits = [s for s in ("train", "test") if s in set(plot_df["split"])]
    if not splits:
        splits = sorted(plot_df["split"].unique().tolist())

    fig = go.Figure()
    split_per_trace: List[str] = []
    legend_keys: List[str] = []
    for si, stratum in enumerate(strata):
        color = colors[si % len(colors)]
        for label in labels:
            for split in splits:
                sub = plot_df[
                    (plot_df["healthy_stratum"] == stratum)
                    & (plot_df["label"] == label)
                    & (plot_df["split"] == split)
                ]
                if sub.empty:
                    continue
                legend_key = f"{stratum}|{label}"
                custom = np.column_stack(
                    [
                        sub["sample_id"].astype(str).to_numpy(),
                        sub["group"].astype(str).to_numpy()
                        if "group" in sub.columns
                        else np.array([""] * len(sub)),
                        sub["split"].astype(str).to_numpy(),
                        sub["healthy_stratum"].astype(str).to_numpy(),
                        sub["disease_stratum"].astype(str).to_numpy()
                        if "disease_stratum" in sub.columns
                        else np.array([""] * len(sub)),
                    ]
                )
                fig.add_trace(
                    go.Scatter(
                        x=sub["PC1"],
                        y=sub["PC2"],
                        mode="markers",
                        name=f"stratum {stratum} · {label}",
                        legendgroup=legend_key,
                        # Initial flags filled below for the default "All" view.
                        showlegend=False,
                        marker=dict(
                            symbol=symbols.get(label, "circle"),
                            color=color,
                            size=9,
                            line=dict(width=0.5, color="#333"),
                        ),
                        customdata=custom,
                        hovertemplate=(
                            "sample_id=%{customdata[0]}<br>"
                            "group=%{customdata[1]}<br>"
                            "split=%{customdata[2]}<br>"
                            "healthy_stratum=%{customdata[3]}<br>"
                            "disease_stratum=%{customdata[4]}<br>"
                            "PC1=%{x:.3f}<br>PC2=%{y:.3f}"
                            "<extra></extra>"
                        ),
                    )
                )
                split_per_trace.append(split)
                legend_keys.append(legend_key)

    n_traces = len(split_per_trace)
    all_visible = [True] * n_traces
    initial_showlegend = _pca_showlegend_for_visibility(all_visible, legend_keys)
    for i, show in enumerate(initial_showlegend):
        fig.data[i].showlegend = show

    filter_buttons = [
        {
            "label": "All",
            "method": "update",
            "args": [
                {
                    "visible": all_visible,
                    "showlegend": initial_showlegend,
                }
            ],
        }
    ]
    for split in splits:
        visible = [s == split for s in split_per_trace]
        filter_buttons.append(
            {
                "label": split.capitalize(),
                "method": "update",
                "args": [
                    {
                        "visible": visible,
                        "showlegend": _pca_showlegend_for_visibility(
                            visible, legend_keys
                        ),
                    }
                ],
            }
        )

    fig.update_layout(
        title="Ω PCA colored by healthy-derived stratum",
        xaxis_title="PC1 (CLR Ω)",
        yaxis_title="PC2 (CLR Ω)",
        template="plotly_white",
        legend_title_text="stratum · group",
        hovermode="closest",
        margin=dict(t=80),
        updatemenus=[
            {
                "type": "buttons",
                "direction": "right",
                "x": 0.0,
                "y": 1.12,
                "xanchor": "left",
                "yanchor": "top",
                "pad": {"r": 8, "t": 0},
                "showactive": True,
                "active": 0,
                "buttons": filter_buttons,
            }
        ],
        annotations=[
            {
                "text": "Split filter:",
                "xref": "paper",
                "yref": "paper",
                "x": 0.0,
                "y": 1.18,
                "xanchor": "left",
                "yanchor": "bottom",
                "showarrow": False,
                "font": {"size": 12},
            }
        ],
    )
    fig.write_html(str(path), include_plotlyjs="cdn", full_html=True)
    return path
