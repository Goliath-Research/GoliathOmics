"""Train-only M-value residualization with inverse-logit betas in (0, 1).

Opt-in: callers with no coefficient artifact must leave betas unchanged.
The residual regression is fit on training samples only (no Group label).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

MANIFEST_NAME = "residualize_manifest.json"
COEF_PREFIX = "residualize"


def sample_id_from_path(path: str | Path) -> str:
    """Map a sample directory or ``{chrom}-{ctx}.h5`` file to a sample_id."""
    p = Path(path)
    if p.suffix in {".h5", ".hdf5"}:
        return p.parent.name
    return p.name


def beta_to_m(beta: np.ndarray, eps: float) -> np.ndarray:
    """Convert betas in [0, 1] to M-values; clip to ``(eps, 1-eps)`` first."""
    x = np.asarray(beta, dtype=np.float64)
    lo = float(eps)
    hi = 1.0 - lo
    x = np.clip(x, lo, hi)
    return np.log2(x / (1.0 - x))


def m_to_beta(m: np.ndarray, eps: float) -> np.ndarray:
    """Inverse logit of M-values back to (0, 1)."""
    m = np.asarray(m, dtype=np.float64)
    two_m = np.power(2.0, m)
    beta = two_m / (1.0 + two_m)
    lo = float(eps)
    hi = 1.0 - lo
    return np.clip(beta, lo, hi)


def drop_near_zero_variance(
    z: np.ndarray,
    names: Sequence[str],
    *,
    threshold: float,
) -> Tuple[np.ndarray, List[str], List[str]]:
    """Drop columns whose sample std is below ``threshold`` (constant covariates)."""
    z = np.asarray(z, dtype=np.float64)
    names_l = [str(n) for n in names]
    if z.ndim != 2 or z.shape[1] != len(names_l):
        raise ValueError("Z and covariate names must align.")
    if z.shape[0] < 2:
        return z, names_l, []
    std = np.nanstd(z, axis=0, ddof=1)
    keep = std >= float(threshold)
    dropped = [n for n, k in zip(names_l, keep) if not k]
    if not np.any(keep):
        return np.zeros((z.shape[0], 0), dtype=np.float64), [], dropped
    return z[:, keep], [n for n, k in zip(names_l, keep) if k], dropped


def alr_coordinates(
    frame: pd.DataFrame,
    columns: Sequence[str],
    *,
    reference: str,
    pseudocount: float,
) -> Tuple[np.ndarray, List[str]]:
    """Neu-referenced additive log-ratio for a composition simplex."""
    cols = [str(c) for c in columns]
    if reference not in cols:
        raise ValueError(f"ALR reference {reference!r} must be in columns {cols}.")
    if float(pseudocount) <= 0:
        raise ValueError("ALR composition pseudocount must be > 0.")
    mat = frame.loc[:, cols].to_numpy(dtype=np.float64)
    mat = np.maximum(mat, 0.0) + float(pseudocount)
    mat = mat / np.sum(mat, axis=1, keepdims=True)
    ref_idx = cols.index(reference)
    ref = mat[:, ref_idx]
    others = [c for c in cols if c != reference]
    other_idx = [cols.index(c) for c in others]
    coords = np.log(mat[:, other_idx] / ref[:, None])
    names = [f"alr_{c}_vs_{reference}" for c in others]
    return coords, names


def design_matrix_from_covariates(
    covariates: pd.DataFrame,
    sample_ids: Sequence[str],
    *,
    numeric_columns: Sequence[str] | None,
    composition_columns: Sequence[str] | None = None,
    composition_reference: str | None = None,
    composition_pseudocount: float | None = None,
    sample_id_column: str = "sample_id",
    variance_threshold: float | None = None,
) -> Tuple[np.ndarray, List[str], List[str], List[str]]:
    """Build Z (n_samples × p, no intercept) aligned to ``sample_ids``.

    Returns ``(Z, names, dropped, missing_sample_ids)``.
    """
    cov = covariates.copy()
    if sample_id_column not in cov.columns:
        raise ValueError(f"Covariate table missing {sample_id_column!r}.")
    cov[sample_id_column] = cov[sample_id_column].astype(str)
    cov = cov.drop_duplicates(subset=[sample_id_column], keep="first")
    cov = cov.set_index(sample_id_column, drop=False)
    ids = [str(s) for s in sample_ids]
    missing = [s for s in ids if s not in cov.index]
    if missing:
        return (
            np.zeros((0, 0), dtype=np.float64),
            [],
            [],
            missing,
        )
    aligned = cov.loc[ids]
    blocks: List[np.ndarray] = []
    names: List[str] = []
    if composition_columns:
        if composition_reference is None or composition_pseudocount is None:
            raise ValueError(
                "composition_reference and composition_pseudocount are required "
                "when composition_columns is set (operator/profile, not a code default)."
            )
        coords, cnames = alr_coordinates(
            aligned,
            composition_columns,
            reference=str(composition_reference),
            pseudocount=float(composition_pseudocount),
        )
        blocks.append(coords)
        names.extend(cnames)
    if numeric_columns:
        num = aligned.loc[:, [str(c) for c in numeric_columns]].to_numpy(dtype=np.float64)
        blocks.append(num)
        names.extend([str(c) for c in numeric_columns])
    if not blocks:
        raise ValueError("No residualize covariates selected.")
    z = np.concatenate(blocks, axis=1)
    dropped: List[str] = []
    if variance_threshold is not None:
        z, names, dropped = drop_near_zero_variance(z, names, threshold=float(variance_threshold))
    if np.any(~np.isfinite(z)):
        raise ValueError("Non-finite covariate values after alignment; drop or impute samples.")
    return z, names, dropped, []


def fit_ols_mvalues(
    m_matrix: np.ndarray,
    z: np.ndarray,
) -> np.ndarray:
    """Fit per-position OLS: ``M = intercept + Z γ``.

    ``m_matrix`` is n_pos × n_samples; ``z`` is n_samples × p (no intercept).
    Returns coef n_pos × (1+p). Positions with any non-finite M are all-NaN rows.
    """
    y = np.asarray(m_matrix, dtype=np.float64)
    z = np.asarray(z, dtype=np.float64)
    if y.ndim != 2:
        raise ValueError("m_matrix must be 2-D (positions × samples).")
    if z.ndim != 2 or z.shape[0] != y.shape[1]:
        raise ValueError("Z rows must match m_matrix columns (samples).")
    n_pos, n_s = y.shape
    p = z.shape[1]
    intercept = np.ones((n_s, 1), dtype=np.float64)
    x = np.concatenate([intercept, z], axis=1) if p else intercept
    finite_pos = np.all(np.isfinite(y), axis=1)
    coef = np.full((n_pos, x.shape[1]), np.nan, dtype=np.float64)
    if not np.any(finite_pos):
        return coef
    y_ok = y[finite_pos]
    # lstsq: X (n_s × q) \ Y.T (n_s × n_ok) → q × n_ok
    sol, *_ = np.linalg.lstsq(x, y_ok.T, rcond=None)
    coef[finite_pos] = sol.T
    return coef


def apply_residualize(
    beta: np.ndarray,
    positions: np.ndarray,
    sample_z: np.ndarray,
    model_positions: np.ndarray,
    coef: np.ndarray,
    eps: float,
) -> np.ndarray:
    """Apply frozen coefficients at matching positions; unmatched stay original (clipped)."""
    beta = np.asarray(beta, dtype=np.float64).ravel()
    positions = np.asarray(positions, dtype=np.uint32).ravel()
    sample_z = np.asarray(sample_z, dtype=np.float64).ravel()
    model_positions = np.asarray(model_positions, dtype=np.uint32).ravel()
    coef = np.asarray(coef, dtype=np.float64)
    if beta.size != positions.size:
        raise ValueError("beta and positions length mismatch.")
    if coef.shape[0] != model_positions.size:
        raise ValueError("coef rows must match model_positions.")
    q = coef.shape[1]
    expected_z = max(q - 1, 0)
    if sample_z.size != expected_z:
        raise ValueError(
            f"sample covariate row length {sample_z.size} != fitted p={expected_z}."
        )
    out = np.clip(beta.copy(), float(eps), 1.0 - float(eps))
    if model_positions.size == 0:
        return out
    order = np.argsort(model_positions, kind="mergesort")
    sp = model_positions[order]
    sc = coef[order]
    idx = np.searchsorted(sp, positions, side="left")
    in_range = idx < sp.size
    safe = np.minimum(idx, max(sp.size - 1, 0))
    match = in_range & (sp[safe] == positions)
    if not np.any(match):
        return out
    mloc = np.flatnonzero(match)
    rows = sc[safe[mloc]]
    z_aug = np.concatenate([[1.0], sample_z]) if expected_z else np.array([1.0])
    predicted = rows @ z_aug
    m_obs = beta_to_m(beta[mloc], eps)
    m_res = m_obs - predicted
    finite = np.isfinite(m_res) & np.isfinite(rows).all(axis=1)
    if np.any(finite):
        out[mloc[finite]] = m_to_beta(m_res[finite], eps)
    return out


@dataclass
class ResidualizeModel:
    """Frozen per-chromosome residualization coefficients."""

    chrom: str
    ctx: str
    positions: np.ndarray
    coef: np.ndarray
    covariate_names: List[str]
    eps: float
    dropped_covariates: List[str] = field(default_factory=list)
    train_sample_ids: List[str] = field(default_factory=list)

    def save(self, output_dir: str | Path) -> Path:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{COEF_PREFIX}-{self.chrom}-{self.ctx}.npz"
        np.savez_compressed(
            path,
            positions=np.asarray(self.positions, dtype=np.uint32),
            coef=np.asarray(self.coef, dtype=np.float32),
            covariate_names=np.asarray(self.covariate_names, dtype=object),
            dropped_covariates=np.asarray(self.dropped_covariates, dtype=object),
            train_sample_ids=np.asarray(self.train_sample_ids, dtype=object),
            chrom=np.asarray(self.chrom),
            ctx=np.asarray(self.ctx),
            eps=np.asarray(float(self.eps)),
        )
        return path

    @classmethod
    def load(cls, path: str | Path) -> "ResidualizeModel":
        p = Path(path)
        with np.load(p, allow_pickle=True) as data:
            names = [str(x) for x in data["covariate_names"].tolist()]
            dropped = [str(x) for x in data["dropped_covariates"].tolist()] if "dropped_covariates" in data else []
            trains = [str(x) for x in data["train_sample_ids"].tolist()] if "train_sample_ids" in data else []
            return cls(
                chrom=str(data["chrom"]),
                ctx=str(data["ctx"]),
                positions=np.asarray(data["positions"], dtype=np.uint32),
                coef=np.asarray(data["coef"], dtype=np.float64),
                covariate_names=names,
                eps=float(np.asarray(data["eps"]).reshape(-1)[0]),
                dropped_covariates=dropped,
                train_sample_ids=trains,
            )


def write_manifest(
    output_dir: str | Path,
    *,
    files: Sequence[str],
    covariate_names: Sequence[str],
    dropped_covariates: Sequence[str],
    train_sample_ids: Sequence[str],
    eps: float,
    extra: Mapping[str, Any] | None = None,
) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "files": [str(f) for f in files],
        "covariate_names": list(covariate_names),
        "dropped_covariates": list(dropped_covariates),
        "n_train_samples": int(len(train_sample_ids)),
        "train_sample_ids": list(train_sample_ids),
        "eps": float(eps),
        "transform": "m_value_ols_inverse_logit",
    }
    if extra:
        payload.update(dict(extra))
    path = out / MANIFEST_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_model_for_chrom(
    coef_dir: str | Path,
    chrom: str,
    ctx: str,
) -> Optional[ResidualizeModel]:
    path = Path(coef_dir) / f"{COEF_PREFIX}-{chrom}-{ctx}.npz"
    if not path.is_file():
        return None
    return ResidualizeModel.load(path)


class ResidualizeApplier:
    """Apply frozen coefficients for one chromosome/context to sample betas."""

    def __init__(
        self,
        model: ResidualizeModel,
        covariates: pd.DataFrame,
        *,
        numeric_columns: Sequence[str] | None,
        composition_columns: Sequence[str] | None,
        composition_reference: str | None,
        composition_pseudocount: float | None,
        sample_id_column: str = "sample_id",
    ) -> None:
        self.model = model
        self._z_by_id: Dict[str, np.ndarray] = {}
        cov = covariates.copy()
        cov[sample_id_column] = cov[sample_id_column].astype(str)
        cov = cov.drop_duplicates(subset=[sample_id_column], keep="first")
        ids = cov[sample_id_column].astype(str).tolist()
        z_all, names_all, _, missing_all = design_matrix_from_covariates(
            cov,
            ids,
            numeric_columns=numeric_columns,
            composition_columns=composition_columns,
            composition_reference=composition_reference,
            composition_pseudocount=composition_pseudocount,
            sample_id_column=sample_id_column,
            variance_threshold=None,
        )
        if missing_all:
            raise ValueError(f"Covariates missing sample_id(s): {missing_all[:5]}")
        name_to_idx = {n: i for i, n in enumerate(names_all)}
        try:
            idx = [name_to_idx[n] for n in model.covariate_names]
        except KeyError as exc:
            raise ValueError(
                f"Fitted covariate {exc} not present at apply time. "
                "Use the same covariate columns as residualize_fit."
            ) from exc
        z_sel = z_all[:, idx] if idx else np.zeros((z_all.shape[0], 0), dtype=np.float64)
        for sid, row in zip(ids, z_sel):
            self._z_by_id[str(sid)] = np.asarray(row, dtype=np.float64)
        self.dropped_at_fit = list(model.dropped_covariates)

    def apply_for_sample(
        self,
        sample_id: str,
        positions: np.ndarray,
        beta: np.ndarray,
    ) -> np.ndarray:
        z = self._z_by_id.get(str(sample_id))
        if z is None:
            raise ValueError(f"No covariate row for sample_id={sample_id!r}.")
        return apply_residualize(
            beta,
            positions,
            z,
            self.model.positions,
            self.model.coef,
            self.model.eps,
        )


def apply_if_model(
    model: Optional[ResidualizeModel],
    applier: Optional[ResidualizeApplier],
    sample_id: str,
    positions: np.ndarray,
    beta: np.ndarray,
) -> np.ndarray:
    """No-op when model/applier is missing (shipped-pack parity path)."""
    if model is None or applier is None:
        return np.asarray(beta, dtype=np.float64)
    return applier.apply_for_sample(sample_id, positions, beta)
