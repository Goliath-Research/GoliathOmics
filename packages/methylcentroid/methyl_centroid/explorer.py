"""
MethylCentroidExplorer: Inspect a MethylFrame (single H5, single JSON mixture, or folder of H5 files).
Identifies type (MethylSample, MethylCentroid, MethylBetaMixtureCentroid), prints metadata,
and optionally describes a range of positions in detail. Single data-driven centroid (N, Sx, Sx2, binned_stats).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import h5py
except ImportError:
    h5py = None

import numpy as np
import pandas as pd

try:
    import plotly.graph_objects as go
except ImportError:
    go = None

try:
    from scipy.stats import beta as scipy_beta
    from scipy.stats import truncnorm as scipy_truncnorm
    from scipy.stats import betabinom as scipy_betabinom
except ImportError:
    scipy_beta = None
    scipy_truncnorm = None
    scipy_betabinom = None


def _get_methyl_group(f) -> Any:
    """Return the group containing pos, mC, uC, tnc (methylation_data or root)."""
    if "methylation_data" in f and hasattr(f["methylation_data"], "keys"):
        return f["methylation_data"]
    return f


def _detect_type_from_keys(keys: List[str]) -> str:
    """Infer MethylFrame type from HDF5 dataset keys (single centroid: N, Sx, Sx2)."""
    keys_set = set(keys)
    if "N" not in keys_set:
        return "MethylSample"
    if {"Sx", "Sx2"}.issubset(keys_set):
        return "MethylCentroid"
    return "MethylSample"


def _is_project_config(path: Path) -> bool:
    """Return True if the JSON file looks like a project config (not a MethylBetaMixtureCentroid)."""
    path = Path(path)
    if path.suffix.lower() != ".json" or not path.is_file():
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    has_project = "project_name" in data
    has_project_keys = any(k in data for k in ("output_base", "controls", "diseases", "step_config"))
    return bool(has_project and has_project_keys)


def _get_mixture_info(path: Path) -> Tuple[str, int, Dict[str, Any], List[str]]:
    """Load MethylBetaMixtureCentroid from JSON. Returns (type_name, n_positions, metadata, column_names)."""
    path = Path(path)
    if _is_project_config(path):
        raise ValueError(
            f"{path} is a project config file, not a MethylFrame or mixture centroid. "
            "Point to a centroid output folder (containing .h5 files such as 1-CG.h5) or to a single .h5 file."
        )
    from methyl_utils.core.methyl_mixture_centroid import MethylBetaMixtureCentroid
    centroid = MethylBetaMixtureCentroid.from_json(path)
    df = centroid.df
    n_positions = len(df)
    metadata = dict(centroid.metadata)
    keys = list(df.columns)
    return "MethylBetaMixtureCentroid", n_positions, metadata, keys


def get_frame_info(path: Path) -> Tuple[str, int, Dict[str, Any], List[str]]:
    """
    Read HDF5 or JSON without loading full data. Returns (type_name, n_positions, metadata, dataset_keys).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")
    if path.suffix.lower() == ".json":
        return _get_mixture_info(path)
    if not h5py:
        raise RuntimeError("h5py is required for MethylCentroidExplorer")
    with h5py.File(path, "r") as f:
        group = _get_methyl_group(f)
        if isinstance(group, h5py.Group):
            keys = list(group.keys())
        else:
            keys = list(f.keys())
        required = {"pos", "mC", "uC", "tnc"}
        if not required.issubset(set(keys)):
            raise ValueError(f"Missing required datasets {required - set(keys)} in {path}")
        pos_ds = group["pos"] if "pos" in group.keys() else f["pos"]
        n_positions = int(pos_ds.shape[0])
        type_name = _detect_type_from_keys(keys)
        metadata = {}
        for name, value in f.attrs.items():
            try:
                if isinstance(value, (bytes, str)):
                    s = value.decode("utf-8") if isinstance(value, bytes) else value
                    metadata[name] = json.loads(s)
                else:
                    metadata[name] = value
            except (json.JSONDecodeError, TypeError):
                metadata[name] = value
        return type_name, n_positions, metadata, keys


def get_positions_in_range(path: Path, pos_start: int, pos_end: int) -> np.ndarray:
    """Read pos/position dataset and return positions that fall in [pos_start, pos_end] (inclusive)."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        if _is_project_config(path):
            raise ValueError(
                f"{path} is a project config file, not a MethylFrame or mixture centroid. "
                "Point to a centroid output folder (containing .h5 files) or to a single .h5 file."
            )
        from methyl_utils.core.methyl_mixture_centroid import MethylBetaMixtureCentroid
        centroid = MethylBetaMixtureCentroid.from_json(path)
        pos = np.asarray(centroid.df["position"].values, dtype=np.uint32)
        mask = (pos >= pos_start) & (pos <= pos_end)
        return np.unique(pos[mask])
    if not h5py:
        raise RuntimeError("h5py is required")
    with h5py.File(path, "r") as f:
        group = _get_methyl_group(f)
        pos_ds = group["pos"]
        pos = np.asarray(pos_ds[:], dtype=np.uint32)
    mask = (pos >= pos_start) & (pos <= pos_end)
    return np.unique(pos[mask])


def load_frame(path: Path, positions: Optional[np.ndarray] = None):
    """Load a MethylFrame from H5 or MethylBetaMixtureCentroid from JSON; optionally filter by positions."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        if _is_project_config(path):
            raise ValueError(
                f"{path} is a project config file, not a MethylFrame or mixture centroid. "
                "Point to a centroid output folder (containing .h5 files) or to a single .h5 file."
            )
        from methyl_utils.core.methyl_mixture_centroid import MethylBetaMixtureCentroid
        centroid = MethylBetaMixtureCentroid.from_json(path)
        if positions is not None and len(positions) > 0:
            df = centroid.df
            mask = np.isin(np.asarray(df["position"].values, dtype=np.uint32), positions)
            subset = df.loc[mask].copy()
            return MethylBetaMixtureCentroid(subset, metadata=centroid.metadata)
        return centroid
    from methyl_utils.core.io import load_from_h5
    return load_from_h5(path, positions=positions)


# --- Mean/variance formulas per distribution (for position table) ---

def _mean_var_normal_from_sufficient(N: float, Sx: float, Sx2: float):
    """Normal (sample) mean and variance from sufficient stats: mean = Sx/N, var = (Sx2 - Sx²/N)/(N-1)."""
    if N is None or N < 1:
        return None, None
    mean = Sx / N
    if N <= 1:
        return mean, None
    var = (Sx2 - (Sx * Sx) / N) / (N - 1)
    var = max(0.0, var) if var is not None else None
    return mean, var


def _mean_var_normal_from_counts(mC: int, uC: int):
    """Normal (empirical proportion) mean and variance from counts: mean = mC/(mC+uC), var = p(1-p)/n."""
    cov = mC + uC
    if cov <= 0:
        return None, None
    mean = mC / cov
    var = (mean * (1 - mean) / cov) if cov > 0 else None
    return mean, var


def _mean_var_betamixture_row(weights: Any, alphas: Any, betas: Any):
    """Beta mixture for one row: mean = Σ w_j μ_j, var = Σ w_j(σ²_j + μ²_j) - mean²."""
    if weights is None or alphas is None or betas is None:
        return None, None
    w = np.asarray(weights if isinstance(weights, (list, np.ndarray)) else json.loads(weights) if isinstance(weights, str) else [])
    a = np.asarray(alphas if isinstance(alphas, (list, np.ndarray)) else json.loads(alphas) if isinstance(alphas, str) else [])
    b = np.asarray(betas if isinstance(betas, (list, np.ndarray)) else json.loads(betas) if isinstance(betas, str) else [])
    if len(w) == 0 or len(a) != len(w) or len(b) != len(w):
        return None, None
    a = np.maximum(np.asarray(a, dtype=np.float64), 1e-10)
    b = np.maximum(np.asarray(b, dtype=np.float64), 1e-10)
    comp_mean = a / (a + b)
    comp_var = (a * b) / ((a + b) ** 2 * (a + b + 1))
    mean = float(np.sum(w * comp_mean))
    var = float(np.sum(w * (comp_var + comp_mean ** 2)) - mean ** 2)
    var = max(0.0, var)
    return mean, var


def _safe_series_values(obj, name: str):
    """Get values from a DataFrame column or property; return list for JSON/print."""
    try:
        s = getattr(obj, name, None)
        if s is None and hasattr(obj, "_df") and name in obj._df.columns:
            s = obj._df[name]
        if s is not None:
            v = s.values if hasattr(s, "values") else np.asarray(s)
            return np.asarray(v).tolist()
    except Exception:
        pass
    return None


def _build_mixture_position_table(frame, pos_start: int, pos_end: int) -> pd.DataFrame:
    """Build per-position table for MethylBetaMixtureCentroid with single mean and variance per row."""
    df = frame._df.copy()
    if "position" not in df.columns:
        return pd.DataFrame()
    pos = np.asarray(df["position"], dtype=np.uint32)
    mask = (pos >= pos_start) & (pos <= pos_end)
    df = df.loc[mask].copy()
    if len(df) == 0:
        return pd.DataFrame()
    mean_list = []
    var_list = []
    for _, row in df.iterrows():
        m, v = _mean_var_betamixture_row(
            row.get("weights"), row.get("alphas"), row.get("betas")
        )
        mean_list.append(m if m is not None else 0.0)
        var_list.append(v if v is not None else 0.0)
    out = df.copy()
    out["mean"] = mean_list
    out["variance"] = var_list
    # Serialize list columns for CSV/TSV (weights, alphas, betas)
    for col in ("weights", "alphas", "betas"):
        if col in out.columns:
            out[col] = out[col].apply(lambda x: json.dumps(x) if isinstance(x, (list, np.ndarray)) else x)
    return out


def build_position_table(frame, pos_start: int, pos_end: int) -> pd.DataFrame:
    """
    Build a per-position table with pos, mC, uC, coverage; N, Sx, Sx2; type-specific fields (alpha, beta;
    BetaBinomial: Sx3, Sx4, count_zero, count_one, sum_*); a single mean and variance (unbiased
    estimators); and when binned_stats exist, distribution analysis: which theoretical (Normal, Beta,
    Beta-Binomial) best approximates the ECDF (closest_distribution, ks_normal, ks_beta, ks_betabinomial).
    """
    # MethylBetaMixtureCentroid: no "pos", has "position" and "weights"
    if hasattr(frame, "_df") and "position" in frame._df.columns and "weights" in frame._df.columns:
        return _build_mixture_position_table(frame, pos_start, pos_end)
    frame = frame.to_cpu()
    df = frame._df.copy()
    pos = np.asarray(df["pos"])
    mask = (pos >= pos_start) & (pos <= pos_end)
    df = df.loc[mask].copy()
    if len(df) == 0:
        return pd.DataFrame()
    # Trigger alpha/beta on the full frame so they are in _df, then copy to our slice
    if hasattr(frame, "alpha"):
        try:
            _ = frame.alpha
            _ = frame.beta
            if "alpha" in frame._df.columns and "beta" in frame._df.columns:
                df["alpha"] = frame._df.loc[df.index, "alpha"].values
                df["beta"] = frame._df.loc[df.index, "beta"].values
        except Exception:
            pass
    rows = []
    for _, row in df.iterrows():
        r = {"pos": int(row["pos"]), "mC": int(row["mC"]), "uC": int(row["uC"])}
        cov = int(row["mC"]) + int(row["uC"])
        r["coverage"] = cov
        if "N" in df.columns:
            r["N"] = int(row["N"])
        if "Sx" in df.columns:
            r["Sx"] = float(row["Sx"])
        if "Sx2" in df.columns:
            r["Sx2"] = float(row["Sx2"])
        a = float(row["alpha"]) if "alpha" in df.columns else None
        b = float(row["beta"]) if "beta" in df.columns else None
        r["alpha"] = a
        r["beta"] = b
        # Single mean and variance (unbiased, distribution-agnostic). None variance means zero.
        if "N" in df.columns and "Sx" in df.columns and "Sx2" in df.columns:
            mn, vn = _mean_var_normal_from_sufficient(
                float(row["N"]), float(row["Sx"]), float(row["Sx2"])
            )
            r["mean"] = mn
            r["variance"] = vn if vn is not None else 0.0
        else:
            mn, vn = _mean_var_normal_from_counts(int(row["mC"]), int(row["uC"]))
            r["mean"] = mn
            r["variance"] = vn if vn is not None else 0.0
        rows.append(r)

    # When centroid has binned_stats, add which distribution best approximates the ECDF (KS vs Normal, Beta, Beta-Binomial).
    # Limit to a small position count so export stays fast; ECDF-vs-theoretical is O(positions × grid_size).
    _MAX_POSITIONS_FOR_DISTRIBUTION_ANALYSIS = 1000
    binned = getattr(frame, "binned_stats", None)
    has_binned = (
        binned is not None
        and isinstance(binned, dict)
        and "bin_edges" in binned
        and "bin_counts" in binned
        and "N" in df.columns
        and "Sx" in df.columns
        and "Sx2" in df.columns
    )
    if has_binned and rows and len(rows) <= _MAX_POSITIONS_FOR_DISTRIBUTION_ANALYSIS:
        try:
            from methyl_utils.ecdf_fit import compare_ecdf_to_theoretical_at_positions
            position_indices = df.index.to_numpy(dtype=np.intp)
            results = compare_ecdf_to_theoretical_at_positions(
                frame,
                position_indices=position_indices,
                grid_size=256,
                include_pvalues=True,
            )
            for i, r in enumerate(rows):
                if i < len(results):
                    res = results[i]
                    r["closest_distribution"] = res.get("closest", "")
                    r["ks_normal"] = res.get("ks_normal")
                    r["ks_beta"] = res.get("ks_beta")
                    r["ks_betabinomial"] = res.get("ks_betabinomial")
                    r["p_normal"] = res.get("p_normal")
                    r["p_beta"] = res.get("p_beta")
                    r["p_betabinomial"] = res.get("p_betabinomial")
                    r["could_use_instead"] = res.get("could_use_instead")
        except Exception:
            for r in rows:
                r["closest_distribution"] = None
                r["ks_normal"] = None
                r["ks_beta"] = None
                r["ks_betabinomial"] = None
                r["p_normal"] = None
                r["p_beta"] = None
                r["p_betabinomial"] = None
                r["could_use_instead"] = None
    elif has_binned and rows and len(rows) > _MAX_POSITIONS_FOR_DISTRIBUTION_ANALYSIS:
        print(
            f"Skipping ECDF-vs-theoretical distribution analysis ({len(rows):,} positions > {_MAX_POSITIONS_FOR_DISTRIBUTION_ANALYSIS}). "
            "Use --max-positions 1000 for per-position closest_distribution and KS columns.",
            file=sys.stderr,
        )

    return pd.DataFrame(rows)


def list_h5_in_folder(folder: Path) -> List[Path]:
    """Return sorted list of .h5 files in folder (non-recursive)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.h5"))


def _stem_to_chrom_context(stem: str) -> Tuple[str, str]:
    """Parse chrom and context from stem (e.g. '1-CG' -> ('1', 'CG'), '2-CHG' -> ('2', 'CHG'))."""
    parts = stem.split("-")
    return (parts[0], parts[1]) if len(parts) >= 2 else (stem, "")


def _select_quartile_positions(table: pd.DataFrame) -> List[Tuple[int, int]]:
    """
    Select one position per quartile of coverage (N or coverage column when present).
    Returns list of (table_row_index, position) for up to 4 positions (Q1, Q2, Q3, Q4).
    If no N/coverage column, use 4 evenly spaced row indices so plots are still produced.
    """
    if len(table) == 0:
        return []
    pos_col = "pos" if "pos" in table.columns else ("position" if "position" in table.columns else None)
    if pos_col is None:
        return []
    sort_col = "N" if "N" in table.columns else ("coverage" if "coverage" in table.columns else None)
    if sort_col is not None:
        sorted_idx = table[sort_col].values.argsort()
        n = len(sorted_idx)
        if n < 4:
            indices = [sorted_idx[i] for i in range(n)]
        else:
            indices = [
                int(sorted_idx[n // 8]),
                int(sorted_idx[3 * n // 8]),
                int(sorted_idx[5 * n // 8]),
                int(sorted_idx[7 * n // 8]),
            ]
    else:
        n = len(table)
        if n < 4:
            indices = list(range(n))
        else:
            indices = [n // 8, 3 * n // 8, 5 * n // 8, 7 * n // 8]
    return [(int(i), int(table.iloc[i][pos_col])) for i in indices]


def _select_plot_positions_replace_ecdf(table: pd.DataFrame) -> List[Tuple[int, int]]:
    """
    Select up to 4 positions where a theoretical distribution could replace the ECDF
    (could_use_instead is True). If none or no distribution analysis, fall back to
    quartile-by-coverage selection.
    """
    if len(table) == 0:
        return []
    pos_col = "pos" if "pos" in table.columns else ("position" if "position" in table.columns else None)
    if pos_col is None:
        return []

    if "could_use_instead" not in table.columns:
        return _select_quartile_positions(table)

    # Positions where some theoretical distribution could replace ECDF
    col = table["could_use_instead"]
    mask = (col == True) | (col.astype(str).str.lower() == "true")
    subset = table.loc[mask]
    if len(subset) == 0:
        return _select_quartile_positions(table)

    # Up to 4 positions from this subset (spread by coverage quartile within subset)
    sort_col = "N" if "N" in subset.columns else ("coverage" if "coverage" in subset.columns else None)
    if sort_col is not None:
        sorted_idx = subset[sort_col].values.argsort()
        n = len(sorted_idx)
        if n <= 4:
            indices = [int(subset.index[i]) for i in range(n)]
        else:
            indices = [
                int(subset.index[sorted_idx[n // 8]]),
                int(subset.index[sorted_idx[3 * n // 8]]),
                int(subset.index[sorted_idx[5 * n // 8]]),
                int(subset.index[sorted_idx[7 * n // 8]]),
            ]
    else:
        ilocs = subset.index.tolist()
        n = len(ilocs)
        if n <= 4:
            indices = [int(i) for i in ilocs]
        else:
            indices = [int(ilocs[n // 8]), int(ilocs[3 * n // 8]), int(ilocs[5 * n // 8]), int(ilocs[7 * n // 8])]

    return [(int(i), int(table.loc[i, pos_col])) for i in indices]


def _pdf_normal_truncated(x: np.ndarray, mu: float, sigma2: float) -> np.ndarray:
    """PDF of truncated Normal on [0,1] with mean mu and variance sigma2."""
    if scipy_truncnorm is None or sigma2 <= 0 or not np.isfinite(sigma2):
        return np.zeros_like(x, dtype=np.float64)
    sigma = np.sqrt(max(sigma2, 1e-12))
    a_std = (0.0 - mu) / sigma
    b_std = (1.0 - mu) / sigma
    if a_std >= b_std:
        return np.zeros_like(x, dtype=np.float64)
    x_arr = np.asarray(x, dtype=np.float64)
    return scipy_truncnorm.pdf(x_arr, a_std, b_std, loc=mu, scale=sigma)


def _kde_from_binned(
    bin_edges: np.ndarray,
    bin_counts: np.ndarray,
    x_grid: np.ndarray,
    bandwidth: Optional[float] = None,
) -> np.ndarray:
    """
    KDE (kernel density estimate) from binned counts: smooth density equivalent to
    smoothing the empirical histogram. Uses Gaussian kernel; density integrates to 1.
    """
    bin_edges = np.asarray(bin_edges, dtype=np.float64)
    bin_counts = np.asarray(bin_counts, dtype=np.float64)
    n_bins = len(bin_counts)
    if n_bins == 0 or bin_edges.shape[0] != n_bins + 1:
        return np.zeros_like(x_grid, dtype=np.float64)
    total = float(np.sum(bin_counts))
    if total <= 0:
        return np.zeros_like(x_grid, dtype=np.float64)
    midpoints = (bin_edges[:-1] + bin_edges[1:]) * 0.5
    widths = np.diff(bin_edges)
    if bandwidth is None:
        # Scott-style: h proportional to typical bin width and 1/n_bins
        mean_width = float(np.mean(widths))
        bandwidth = max(mean_width * 1.5, 0.02)
    x_grid = np.asarray(x_grid, dtype=np.float64).ravel()
    # density(x) = (1/N) * sum_i count_i * (1/h) * norm.pdf((x - mid_i) / h)
    density = np.zeros_like(x_grid, dtype=np.float64)
    for i in range(n_bins):
        w = float(bin_counts[i]) / total
        if w <= 0:
            continue
        # Gaussian kernel: (1/h) * phi((x - mid_i)/h) so that integral = 1 per bin contribution
        u = (x_grid - midpoints[i]) / max(bandwidth, 1e-10)
        density += w * np.exp(-0.5 * u * u) / (bandwidth * np.sqrt(2.0 * np.pi))
    return density


def _beta_binomial_mom_diagnostic(
    N: float,
    sum_mC: float,
    sum_mC2: float,
    sum_cov: float,
    position: int,
) -> str:
    """
    Recompute Beta-Binomial MoM from sufficient statistics and return a string explaining
    why the parameters might be invalid (leading to stored (1,1) and a uniform plot).
    Uses the same formulas as methyl_utils.statistical_tests.beta_binomial_mom_estimation.
    """
    if N <= 0 or sum_cov <= 0:
        return f"pos {position}: N={N}, sum_cov={sum_cov} (need both > 0)"
    n_eff = sum_cov / N
    m1 = sum_mC / N
    m2 = sum_mC2 / N
    m1_safe = m1 if m1 > 1e-12 else 1e-12
    ratio = (m2 / m1_safe) if m2 > 1e-20 else m1_safe
    denom = n_eff * (ratio - m1 - 1.0) + m1
    raw_alpha = (n_eff * m1 - m2) / denom if abs(denom) >= 1e-12 else float("nan")
    raw_beta = (n_eff - m1) * (n_eff - ratio) / denom if abs(denom) >= 1e-12 else float("nan")
    reasons = []
    if m1 <= 0 or m1 >= n_eff:
        reasons.append(f"m1={m1:.6g} out of (0, n_eff={n_eff:.4g})")
    if abs(denom) < 1e-12:
        reasons.append(f"denom≈0 (denom={denom:.6g})")
    elif denom <= 0:
        reasons.append(f"denom<=0 (denom={denom:.6g}); ratio-m1-1={ratio - m1 - 1.0:.6g}")
    if not reasons and (raw_alpha <= 0 or raw_beta <= 0):
        reasons.append(f"raw_alpha={raw_alpha:.6g} or raw_beta={raw_beta:.6g} non-positive")
    if not reasons and (not np.isfinite(raw_alpha) or not np.isfinite(raw_beta)):
        reasons.append("raw alpha or beta non-finite")
    reason_str = "; ".join(reasons) if reasons else "valid"
    return (
        f"Beta-Binomial MoM pos {position}: N={N:.0f}, sum_mC={sum_mC:.0f}, sum_mC2={sum_mC2:.2f}, sum_cov={sum_cov:.0f} -> "
        f"n_eff={n_eff:.2f}, m1={m1:.6g}, m2={m2:.4g}, ratio=m2/m1={ratio:.6g}, denom={denom:.6g}, "
        f"raw_alpha={raw_alpha:.6g}, raw_beta={raw_beta:.6g} -> {reason_str}"
    )


def _get_centroid_property(centroid: Any, position_idx: int, prop_name: str) -> Optional[float]:
    """
    Get a scalar at position_idx from a centroid property (mean, variance, alpha, beta, N, binned_stats).
    """
    prop = getattr(centroid, prop_name, None)
    if prop is None:
        return None
    try:
        if hasattr(prop, "iloc"):
            v = prop.iloc[position_idx]
        else:
            v = np.asarray(prop).flat[position_idx]
        v = float(v)
        return v if np.isfinite(v) else None
    except Exception:
        return None


def _export_density_plot(
    frame: Any,
    position_idx: int,
    position: int,
    chrom_str: str,
    context_str: str,
    out_path: Path,
    grid_size: int = 300,
) -> bool:
    """
    Export a single interactive Plotly HTML with density plots for Normal, Beta, and ECDF at the given position.
    Uses centroid's public API (mean, variance, alpha, beta, binned_stats). Always writes an HTML file.
    """
    if go is None:
        print("    plotly not installed; pip install plotly")
        return False

    # Support both _df and .df (e.g. MethylBetaMixtureCentroid uses .df); avoid "or" so DataFrame truthiness is not used
    df = getattr(frame, "_df", None)
    if df is None:
        df = getattr(frame, "df", None)
    n_rows = len(df) if df is not None else 0
    if df is None:
        print("    Skipped: frame has no _df or .df")
        return False
    if position_idx < 0 or position_idx >= n_rows:
        print(f"    Skipped: position_idx={position_idx} out of range [0, {n_rows})")
        return False

    grid = np.linspace(0.0, 1.0, max(2, grid_size), dtype=np.float64)
    grid = np.clip(grid, 1e-9, 1.0 - 1e-9)

    # Use only centroid public API (no Sx, Sx2, or other internal columns)
    mu = _get_centroid_property(frame, position_idx, "mean")
    sigma2 = _get_centroid_property(frame, position_idx, "variance")
    if mu is None:
        mu = 0.5
    if sigma2 is None or sigma2 <= 0:
        sigma2 = 1e-6

    traces = []

    # Normal (truncated on [0,1])
    if scipy_truncnorm is not None:
        pdf_norm = _pdf_normal_truncated(grid, mu, sigma2)
        traces.append(
            go.Scatter(
                x=grid.tolist(),
                y=pdf_norm.tolist(),
                name="Normal",
                mode="lines",
                line=dict(width=2),
            )
        )

    # Beta (centroid.alpha, centroid.beta)
    alpha = _get_centroid_property(frame, position_idx, "alpha")
    beta = _get_centroid_property(frame, position_idx, "beta")
    if scipy_beta is not None and alpha is not None and beta is not None and alpha > 0 and beta > 0:
        pdf_beta = scipy_beta.pdf(grid, alpha, beta)
        traces.append(
            go.Scatter(
                x=grid.tolist(),
                y=pdf_beta.tolist(),
                name="Beta",
                mode="lines",
                line=dict(width=2),
            )
        )

    # Beta-Binomial view removed; use Beta (alpha, beta from MoM) and ECDF only.
    if (
        scipy_betabinom is not None
        and alpha is not None
        and beta is not None
        and alpha > 0
        and beta > 0
    ):
        N_prop = _get_centroid_property(frame, position_idx, "N")
        if N_prop is not None and N_prop > 0:
            n = max(2, min(500, int(round(N_prop))))
        else:
            n = 50
        k_vals = np.arange(0, n + 1, dtype=np.intp)
        pmf_vals = scipy_betabinom.pmf(k_vals, n, alpha, beta)
        x_bb = k_vals.astype(np.float64) / n
        density_bb_discrete = n * pmf_vals
        density_bb = np.interp(grid, x_bb, density_bb_discrete)
        traces.append(
            go.Scatter(
                x=grid.tolist(),
                y=density_bb.tolist(),
                name="Beta-Binomial",
                mode="lines",
                line=dict(width=2),
            )
        )

    # ECDF: KDE from binned data (frame.binned_stats)
    binned = getattr(frame, "binned_stats", None)
    if (
        binned is not None
        and isinstance(binned, dict)
        and "bin_edges" in binned
        and "bin_counts" in binned
    ):
        try:
            bin_edges = np.asarray(binned["bin_edges"], dtype=np.float64)
            bin_counts_arr = np.asarray(binned["bin_counts"], dtype=np.float64)
            if bin_counts_arr.ndim == 2 and position_idx < bin_counts_arr.shape[0]:
                counts_one = bin_counts_arr[position_idx, :]
            else:
                counts_one = bin_counts_arr.ravel()
            pdf_ecdf = _kde_from_binned(bin_edges, counts_one, grid)
            traces.append(
                go.Scatter(
                    x=grid.tolist(),
                    y=pdf_ecdf.tolist(),
                    name="ECDF",
                    mode="lines",
                    line=dict(width=2),
                )
            )
        except Exception:
            pass

    # Always write an HTML file; use placeholder if no distribution curves
    if not traces:
        traces = [
            go.Scatter(
                x=[0.5],
                y=[1.0],
                name="(no distribution data)",
                mode="markers+text",
                text=["No distribution data"],
                textposition="top center",
            )
        ]

    fig = go.Figure(data=traces)
    fig.update_layout(
        title=dict(text=f"Methylation density — {chrom_str}-{context_str} pos {position}"),
        xaxis_title="Methylation level",
        yaxis_title="Density",
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        template="plotly_white",
    )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # File is written below; caller prints "Exported" on success
    fig.write_html(str(out_path))
    return True


def run_explorer(
    path: Path,
    *,
    file_filter: Optional[str] = None,
    chrom: Optional[str] = None,
    context: Optional[str] = None,
    pos_start: Optional[int] = None,
    pos_end: Optional[int] = None,
    max_positions: int = 10_000,
    json_metadata: bool = False,
    output: Optional[Path] = None,
    export_format: str = "csv",
    single_csv: bool = False,
    single_json: bool = False,
    plot_quartiles: bool = False,
    plot_grid_size: int = 300,
) -> None:
    """
    Main explorer logic: resolve path (file or folder), detect type, print metadata,
    optionally print position range detail.
    """
    path = Path(path).resolve()
    if path.is_file() and _is_project_config(path):
        print(
            f"Error: {path} is a project config file, not a centroid or MethylFrame.\n"
            "Point to a centroid output folder (containing .h5 files like 1-CG.h5) or to a single .h5 file.\n"
            "Example: methyl-centroid-explorer /path/to/centroids/controls/healthy/healthy --single-csv --max-positions 1000",
            file=sys.stderr,
        )
        return
    if path.is_file():
        target = path
    elif path.is_dir():
        h5_files = list_h5_in_folder(path)
        if not h5_files:
            print(f"No .h5 files in {path}", file=sys.stderr)
            return
        if file_filter:
            h5_files = [p for p in h5_files if file_filter in p.name]
        if chrom or context:
            # e.g. 1-CG.h5
            parts = []
            if chrom:
                parts.append(str(chrom))
            if context:
                parts.append(str(context))
            pattern = "-".join(parts)
            h5_files = [p for p in h5_files if pattern in p.name or p.name.startswith(pattern + "-")]
        if not h5_files:
            print("No matching .h5 file after filter.", file=sys.stderr)
            return
        # Process all matching .h5 files ({chrom}-{context}.h5)
        targets = [Path(p) if not isinstance(p, Path) else p for p in h5_files]
    else:
        print(f"Path not found: {path}", file=sys.stderr)
        return

    # Single file: path was a file
    if path.is_file():
        targets = [target]

    # Full range when only --max-positions is set; otherwise use --pos-start/--pos-end
    pos_start_val = pos_start if pos_start is not None else 0
    pos_end_val = pos_end if pos_end is not None else (1 << 32) - 1
    has_position_range = (pos_start is not None or pos_end is not None) or max_positions is not None
    combined_tables: List[pd.DataFrame] = []  # for --single-csv
    combined_metadata: Dict[str, Any] = {}   # for --single-json: stem -> {path, type, positions, datasets, metadata}

    for idx, target in enumerate(targets):
        if len(targets) > 1:
            print(f"\n--- {target.name} ({idx + 1}/{len(targets)}) ---")
        type_name, n_positions, metadata, keys = get_frame_info(target)
        if single_json:
            combined_metadata[target.stem] = {
                "path": str(target.resolve()),
                "type": type_name,
                "positions": n_positions,
                "datasets": sorted(keys),
                "metadata": metadata,
            }
        print(f"Path: {target}")
        print(f"Type: {type_name}")
        print(f"Positions: {n_positions:,}")
        print(f"Datasets: {', '.join(sorted(keys))}")
        if json_metadata:
            print("Metadata (JSON):")
            print(json.dumps(metadata, indent=2, default=str))
        else:
            print("Metadata:")
            for k, v in metadata.items():
                print(f"  {k}: {v}")

        if not has_position_range:
            continue

        positions_to_load = get_positions_in_range(target, pos_start_val, pos_end_val)
        if len(positions_to_load) == 0:
            print(f"No positions in range [{pos_start_val}, {pos_end_val}].", file=sys.stderr)
            continue
        if len(positions_to_load) > max_positions:
            print(f"Range has {len(positions_to_load):,} positions; capping to {max_positions} (use --max-positions to change).", file=sys.stderr)
            positions_to_load = positions_to_load[:max_positions]
        # When user gave only --max-positions (no pos-start/pos-end), use actual range for filename and table
        file_start = int(positions_to_load.min()) if pos_start is None and pos_end is None else pos_start_val
        file_end = int(positions_to_load.max()) if pos_start is None and pos_end is None else pos_end_val
        frame = load_frame(target, positions=positions_to_load)
        table = build_position_table(frame, file_start, file_end)
        if table is None or len(table) == 0:
            print("No rows in position table.")
            continue

        chrom_str, context_str = _stem_to_chrom_context(target.stem)

        if single_csv:
            # Prepend chromosome and context; accumulate for one CSV at the end
            out_df = table.copy()
            out_df.insert(0, "context", context_str)
            out_df.insert(0, "chromosome", chrom_str)
            combined_tables.append(out_df)
            print(f"Collected {len(table):,} rows for {target.name} (chromosome={chrom_str}, context={context_str})")
            if "closest_distribution" not in table.columns and len(table) > 1000:
                print(
                    "Tip: No distribution columns (closest_distribution, ks_*, p_*) in this export. "
                    "Use --max-positions 1000 to include them.",
                    file=sys.stderr,
                )
        else:
            # Export to same location as the .h5 by default; filename {chrom}-{context}-{pos-start}-{pos-end}.csv
            out_dir = Path(output).resolve() if output is not None else target.parent
            if out_dir.suffix.lower() in (".csv", ".tsv", ".txt"):
                out_dir = out_dir.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{chrom_str}-{context_str}-{file_start}-{file_end}.{export_format}"
            fmt = export_format

            if fmt == "csv":
                table.to_csv(out_path, index=False)
            else:
                table.to_csv(out_path, index=False, sep="\t")
            print(f"Exported {len(table):,} rows to {out_path} ({fmt.upper()})")
            if "closest_distribution" not in table.columns and len(table) > 1000:
                print(
                    "Tip: No distribution columns in this export. Use --max-positions 1000 to include them.",
                    file=sys.stderr,
                )

        if plot_quartiles:
            print("Generating density plots (--plot-quartiles)...")
            try:
                quartile_pairs = _select_plot_positions_replace_ecdf(table)
            except Exception as e:
                print(f"Density plots: failed to select positions: {e}")
                quartile_pairs = []
            plot_out_dir = None
            if quartile_pairs:
                # HTML is saved to the same location as the centroid (directory of the .h5 file), or -o if given
                plot_out_dir = Path(output).resolve() if output is not None else target.parent
                if plot_out_dir.suffix.lower() in (".csv", ".tsv", ".txt", ".json"):
                    plot_out_dir = plot_out_dir.parent
                plot_out_dir.mkdir(parents=True, exist_ok=True)
                print(f"Density plot output directory (same as centroid): {plot_out_dir}")
                print(f"Selected {len(quartile_pairs)} position(s) for density plots: {[pos for _, pos in quartile_pairs]}")
            else:
                print("Density plots: no positions selected (need could_use_instead or N/coverage in table).")
            n_exported = 0
            for row_idx, pos in quartile_pairs:
                out_html = plot_out_dir / f"{chrom_str}-{context_str}-{pos}.html"
                print(f"  Writing: {out_html}")
                try:
                    if _export_density_plot(frame, row_idx, pos, chrom_str, context_str, out_html, grid_size=plot_grid_size):
                        print(f"  Exported: {out_html}")
                        n_exported += 1
                    else:
                        print(f"  Skipped (plot failed): {out_html}")
                except Exception as e:
                    print(f"  Error writing {out_html}: {e}")
            if quartile_pairs:
                if n_exported == 0:
                    print(f"Density plots: 0 exported to {plot_out_dir} (check plotly/scipy or frame data).")
                else:
                    print(f"Density plots: exported {n_exported} file(s) to {plot_out_dir}")

        pd.set_option("display.max_rows", None)
        pd.set_option("display.width", None)
        print("\nPosition detail (first rows):")
        print(table.head(500).to_string(index=False))
        if len(table) > 500:
            print(f"... and {len(table) - 500} more rows.")

    # Write single combined CSV when --single-csv was used (same location as centroid, like plots)
    if single_csv and combined_tables:
        out_dir = Path(output).resolve() if output is not None else (path if path.is_dir() else path.parent)
        if out_dir.suffix.lower() in (".csv", ".tsv", ".txt"):
            out_dir = out_dir.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        combined = pd.concat(combined_tables, ignore_index=True)
        c_min, c_max = int(combined["pos"].min()), int(combined["pos"].max())
        out_path = out_dir / f"positions_{c_min}_{c_max}.{export_format}"
        if export_format == "csv":
            combined.to_csv(out_path, index=False)
        else:
            combined.to_csv(out_path, index=False, sep="\t")
        print(f"\nExported single {export_format.upper()} with chromosome and context: {out_path} ({len(combined):,} rows)")
        if "closest_distribution" not in combined.columns:
            print(
                "Tip: To include distribution columns (closest_distribution, ks_*, p_*), use --max-positions 1000.",
                file=sys.stderr,
            )

    # Write single JSON with metadata for all chrom-context .h5 when --single-json was used
    if single_json and combined_metadata:
        out_dir = Path(output).resolve() if output is not None else Path.cwd()
        if out_dir.suffix.lower() in (".csv", ".tsv", ".txt", ".json"):
            out_dir = out_dir.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "metadata.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(combined_metadata, f, indent=2, default=str)
        print(f"\nExported single JSON with metadata for {len(combined_metadata)} file(s): {out_path}")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="MethylCentroidExplorer: Inspect a MethylFrame (folder or .h5 file) or MethylBetaMixtureCentroid (.json), detect type, print metadata, and optionally describe a range of positions.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", type=Path, help="Path to a MethylFrame folder, a single .h5 file, or a MethylBetaMixtureCentroid .json file")
    parser.add_argument("--file", dest="file_filter", metavar="SUBSTR", help="If path is a folder, only consider .h5 files whose name contains SUBSTR (e.g. '1-CG')")
    parser.add_argument("--chrom", "-c", help="If path is a folder, filter to .h5 files for this chromosome (e.g. 1)")
    parser.add_argument("--context", "-x", choices=["CG", "CHG", "CHH"], help="If path is a folder, filter to this context")
    parser.add_argument("--pos-start", type=int, default=None, metavar="POS", help="Start of position range (inclusive) for per-position detail")
    parser.add_argument("--pos-end", type=int, default=None, metavar="POS", help="End of position range (inclusive) for per-position detail")
    parser.add_argument("--max-positions", type=int, default=10_000, metavar="N", help="Maximum number of positions to load. If only this is set (no --pos-start/--pos-end), uses the full range of each file capped at N (default 10000). Use 1000 or less to include distribution analysis columns (closest_distribution, ks_*, p_*).")
    parser.add_argument("--json-metadata", action="store_true", help="Print metadata as JSON")
    parser.add_argument("--output", "-o", type=Path, default=None, metavar="DIR", help="Output directory for exported position tables. Files keep the same name (e.g. 1-CG_positions_START_END.csv). Default: current directory.")
    parser.add_argument("--single-csv", action="store_true", help="Export one CSV/TSV with chromosome and context as first two columns (combines all files when path is a folder).")
    parser.add_argument("--single-json", action="store_true", help="Export one valid JSON file with metadata for all chrom-context .h5 in the folder (keyed by stem, e.g. 1-CG).")
    parser.add_argument("--plot-quartiles", action="store_true", help="Export one interactive Plotly HTML density plot per coverage quartile (4 positions: Normal, Beta, Beta-Binomial, ECDF). Files named {chrom}-{context}-{position}.html.")
    parser.add_argument("--grid-size", type=int, default=300, metavar="N", help="Number of points on the X axis for density plots (default 300). Use e.g. 1000 for finer resolution with continuous ECDF.")
    parser.add_argument("--format", "-f", choices=["csv", "tsv", "txt"], default="csv", dest="export_format", help="Format when using default output path (default: csv). With --output, format is inferred from extension.")
    args = parser.parse_args()
    # Infer format from --output extension if provided
    export_format = args.export_format
    if args.output is not None and args.output.suffix.lower() in (".csv", ".tsv", ".txt"):
        export_format = args.output.suffix.lower().lstrip(".")
    run_explorer(
        args.path,
        file_filter=args.file_filter,
        chrom=args.chrom,
        context=args.context,
        pos_start=args.pos_start,
        pos_end=args.pos_end,
        max_positions=args.max_positions,
        json_metadata=args.json_metadata,
        output=args.output,
        export_format=export_format,
        single_csv=args.single_csv,
        single_json=args.single_json,
        plot_quartiles=args.plot_quartiles,
        plot_grid_size=args.grid_size,
    )


if __name__ == "__main__":
    main()
