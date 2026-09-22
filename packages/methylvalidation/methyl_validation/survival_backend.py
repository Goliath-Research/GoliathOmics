"""Cox PH / Kaplan–Meier / time-dependent AUC survival backend (MHL path)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


def load_survival_table(project: Any) -> pd.DataFrame:
    """Load study survival sidecar (CSV) or inline ProjectConfig.survival rows."""
    path = getattr(project, "survival_path", None)
    if path:
        df = pd.read_csv(path)
    elif getattr(project, "survival", None):
        df = pd.DataFrame([row.model_dump() if hasattr(row, "model_dump") else dict(row)
                           for row in project.survival])
    else:
        raise ValueError(
            "Survival backend requires project.survival_path (CSV) or project.survival rows "
            "with columns time, event, and a sample id"
        )
    cols = {c.lower(): c for c in df.columns}
    sid = cols.get("sample_id") or cols.get("sample") or cols.get("id")
    time_c = cols.get("time") or cols.get("os_time") or cols.get("os_months")
    event_c = cols.get("event") or cols.get("os_event") or cols.get("status")
    if sid is None or time_c is None or event_c is None:
        raise ValueError(
            "Survival table must include sample_id (or sample), time, and event columns"
        )
    out = df.rename(columns={sid: "sample_id", time_c: "time", event_c: "event"})
    out["time"] = pd.to_numeric(out["time"], errors="coerce")
    out["event"] = pd.to_numeric(out["event"], errors="coerce").fillna(0).astype(int)
    return out


def _design_matrix(
    survival: pd.DataFrame,
    feature_frame: Optional[pd.DataFrame],
    *,
    id_column: str,
    extra_columns: Optional[Sequence[str]],
) -> Tuple[pd.DataFrame, np.ndarray, np.ndarray, List[str]]:
    frame = survival.copy()
    if feature_frame is not None and not feature_frame.empty:
        feat = feature_frame.copy()
        if id_column not in feat.columns:
            # first column is sample id
            feat = feat.rename(columns={feat.columns[0]: id_column})
        frame = frame.merge(feat, on=id_column, how="inner", suffixes=("", "_feat"))
    drop = {id_column, "time", "event", "group"}
    extra = [c for c in (extra_columns or []) if c in frame.columns]
    feature_cols = [
        c for c in frame.columns
        if c not in drop and c not in extra and pd.api.types.is_numeric_dtype(frame[c])
    ]
    feature_cols = extra + feature_cols
    X = frame[feature_cols].apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.mean(numeric_only=True))
    y_time = frame["time"].to_numpy(dtype=float)
    y_event = frame["event"].to_numpy(dtype=int)
    keep = np.isfinite(y_time) & np.isfinite(X.to_numpy(dtype=float)).all(axis=1)
    return X.loc[keep], y_time[keep], y_event[keep], feature_cols


def cox_ph_fit(
    X: np.ndarray,
    time: np.ndarray,
    event: np.ndarray,
    *,
    max_iter: int = 50,
    tol: float = 1e-6,
    l2: float = 1e-4,
) -> Tuple[np.ndarray, np.ndarray]:
    """Breslow-ties Cox PH via Newton–Raphson. Returns (beta, risk_score)."""
    n, p = X.shape
    if n == 0 or p == 0:
        return np.zeros(p), np.zeros(n)
    # standardize
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    beta = np.zeros(p)
    order = np.argsort(-time)  # descending time for risk sets
    Z = Z[order]
    event_o = event[order]
    for _ in range(max_iter):
        eta = Z @ beta
        eta = np.clip(eta, -20, 20)
        exp_eta = np.exp(eta)
        risk_sum = np.cumsum(exp_eta)
        risk_x = np.cumsum(Z * exp_eta[:, None], axis=0)
        grad = np.zeros(p)
        hess = np.zeros((p, p))
        for i in range(n):
            if event_o[i] <= 0:
                continue
            s0 = risk_sum[i]
            if s0 <= 0:
                continue
            s1 = risk_x[i]
            mean_x = s1 / s0
            grad += Z[i] - mean_x
            # outer product approximation
            hess -= (Z[i] - mean_x)[:, None] @ (Z[i] - mean_x)[None, :]
        hess -= l2 * np.eye(p)
        grad -= l2 * beta
        try:
            delta = np.linalg.solve(-hess, grad)
        except np.linalg.LinAlgError:
            break
        beta = beta + delta
        if float(np.max(np.abs(delta))) < tol:
            break
    # un-standardize
    beta_orig = beta / sd
    risk = X @ beta_orig
    return beta_orig, risk


def harrell_c(risk: np.ndarray, time: np.ndarray, event: np.ndarray) -> float:
    n = len(time)
    conc = 0.0
    tot = 0.0
    for i in range(n):
        if event[i] <= 0:
            continue
        for j in range(n):
            if time[j] <= time[i]:
                continue
            tot += 1
            if risk[i] > risk[j]:
                conc += 1
            elif risk[i] == risk[j]:
                conc += 0.5
    if tot == 0:
        return float("nan")
    return float(conc / tot)


def kaplan_meier(time: np.ndarray, event: np.ndarray) -> pd.DataFrame:
    order = np.argsort(time)
    t = time[order]
    e = event[order]
    n = len(t)
    at_risk = n
    surv = 1.0
    rows = []
    i = 0
    while i < n:
        ti = t[i]
        deaths = 0
        while i < n and t[i] == ti:
            deaths += int(e[i] > 0)
            i += 1
        if deaths:
            surv *= 1.0 - deaths / max(at_risk, 1)
            rows.append({"time": float(ti), "survival": float(surv), "n_event": deaths, "n_risk": at_risk})
        at_risk = n - i
    return pd.DataFrame(rows)


def log_rank(time: np.ndarray, event: np.ndarray, group: np.ndarray) -> float:
    """Two-sample log-rank p-value (chi-square, 1 df)."""
    g = (group > np.median(group)).astype(int)
    times = np.unique(time[event > 0])
    o1 = 0.0
    e1 = 0.0
    v = 0.0
    for ti in times:
        mask = time >= ti
        n = int(mask.sum())
        n1 = int(((g == 1) & mask).sum())
        d = int(((time == ti) & (event > 0)).sum())
        d1 = int(((time == ti) & (event > 0) & (g == 1)).sum())
        if n <= 1 or n1 == 0 or n1 == n:
            continue
        e1 += d * (n1 / n)
        o1 += d1
        v += (n1 * (n - n1) * d * (n - d)) / (n * n * (n - 1))
    if v <= 0:
        return float("nan")
    chi = (o1 - e1) ** 2 / v
    # survival function of chi2(1): erfc(sqrt(chi/2))
    from math import erfc, sqrt

    return float(erfc(sqrt(chi / 2.0)))


def time_dependent_auc(
    risk: np.ndarray,
    time: np.ndarray,
    event: np.ndarray,
    horizons: Sequence[float],
) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for t in horizons:
        case = (time <= t) & (event > 0)
        control = time > t
        if not np.any(case) or not np.any(control):
            out[str(t)] = float("nan")
            continue
        rc = risk[case]
        ro = risk[control]
        # Wilcoxon / Mann–Whitney AUC
        wins = 0.0
        tot = 0.0
        for a in rc:
            tot += ro.size
            wins += float(np.sum(a > ro) + 0.5 * np.sum(a == ro))
        out[str(t)] = float(wins / tot) if tot else float("nan")
    return out


def nested_lrt(
    X_full: np.ndarray,
    X_reduced: np.ndarray,
    time: np.ndarray,
    event: np.ndarray,
) -> Dict[str, float]:
    """Likelihood-ratio test of full vs reduced Cox (labs-only nested)."""
    def _loglik(X: np.ndarray) -> float:
        beta, _ = cox_ph_fit(X, time, event)
        eta = np.clip(X @ beta, -20, 20)
        exp_eta = np.exp(eta)
        order = np.argsort(-time)
        exp_s = np.cumsum(exp_eta[order])
        event_o = event[order]
        ll = 0.0
        for i, ev in enumerate(event_o):
            if ev <= 0:
                continue
            ll += float(eta[order[i]] - np.log(max(exp_s[i], 1e-12)))
        return ll

    if X_reduced.size == 0 or X_full.shape[1] <= X_reduced.shape[1]:
        return {"lrt_stat": float("nan"), "df": 0, "p_value": float("nan")}
    ll_f = _loglik(X_full)
    ll_r = _loglik(X_reduced)
    stat = 2.0 * (ll_f - ll_r)
    df = int(X_full.shape[1] - X_reduced.shape[1])
    from math import erfc, sqrt

    p = float(erfc(sqrt(max(stat, 0.0) / 2.0))) if df == 1 else float("nan")
    return {"lrt_stat": float(stat), "df": df, "p_value": p}


def run_survival_model(
    *,
    project_json: Path,
    output_dir: Path,
    config: Any,
    mhl_matrix_path: Optional[str] = None,
) -> Dict[str, Any]:
    from methyl_utils import load_project

    project = load_project(project_json)
    survival = load_survival_table(project)
    id_col = "sample_id"
    params = config.get_backend_params("cox") if hasattr(config, "get_backend_params") else config
    matrix_path = mhl_matrix_path or getattr(params, "mhl_matrix_path", None)
    feature_frame = None
    if matrix_path:
        feature_frame = pd.read_csv(matrix_path)
    extra = list(getattr(params, "clinical_columns", None) or [])
    X_df, y_time, y_event, cols = _design_matrix(
        survival, feature_frame, id_column=id_col, extra_columns=extra
    )
    X = X_df.to_numpy(dtype=float)
    beta, risk = cox_ph_fit(X, y_time, y_event)
    c_index = harrell_c(risk, y_time, y_event)
    km = kaplan_meier(y_time, y_event)
    lr_p = log_rank(y_time, y_event, risk)
    horizons = list(getattr(params, "time_auc_horizons", None) or [])
    td_auc = time_dependent_auc(risk, y_time, y_event, horizons) if horizons else {}

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    km_path = output_dir / "kaplan_meier.csv"
    km.to_csv(km_path, index=False)
    coef_path = output_dir / "cox_coefficients.csv"
    pd.DataFrame({"feature": cols, "beta": beta}).to_csv(coef_path, index=False)

    lrt = {}
    if extra and getattr(params, "nested_lrt", None):
        X_red = X_df[extra].to_numpy(dtype=float) if extra else np.empty((len(X), 0))
        lrt = nested_lrt(X, X_red, y_time, y_event)

    nomogram = None
    if getattr(params, "write_nomogram", None):
        nomogram = {
            "coefficients": {c: float(b) for c, b in zip(cols, beta)},
            "mean_risk": float(np.mean(risk)) if len(risk) else None,
            "c_index": c_index,
        }
        (output_dir / "nomogram.json").write_text(
            json.dumps(nomogram, indent=2), encoding="utf-8"
        )

    metrics = {
        "backend": "cox",
        "n_samples": int(len(y_time)),
        "n_events": int(y_event.sum()),
        "n_features": len(cols),
        "concordance": c_index,
        "log_rank_p": lr_p,
        "time_auc": td_auc,
        "nested_lrt": lrt,
        "kaplan_meier": str(km_path),
        "coefficients": str(coef_path),
        "nomogram": str(output_dir / "nomogram.json") if nomogram else None,
    }
    (output_dir / "survival_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    return metrics
