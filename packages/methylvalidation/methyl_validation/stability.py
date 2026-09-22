"""
Stability analysis across Monte Carlo runs.

Counts DMPs in detector discovery exports per iteration; optionally restricts
to iterations with ``balanced_accuracy`` above a threshold (from detector
validation or, when present, predictor metrics). Gene stability can use
classifier gene panels from gene FeatureCuts (MC) or enricher outputs (freeze).
"""

from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from methyl_utils import load_project
from methyl_utils.logging_utils import setup_module_logging
from scipy.stats import norm

logger = setup_module_logging(__name__)

if TYPE_CHECKING:
    from .config import MonteCarloConfig


def find_validation_metrics_json(run_dir: Path) -> Optional[Path]:
    """Locate validation_metrics.json under run_dir/predictors (binary nested or flat)."""
    predictors = run_dir / "predictors"
    if not predictors.is_dir():
        for p in run_dir.glob("**/predictors"):
            if p.is_dir():
                predictors = p
                break
        else:
            return None
    for path in predictors.rglob("validation_metrics.json"):
        return path
    return None


def run_balanced_accuracy(run_dir: Path) -> Optional[float]:
    """
    Return balanced_accuracy for this run: prefer MethylPredictor validation_metrics.json,
    else mean balanced_accuracy from MethylDetector result*.json under detections/.
    """
    from .validator_metrics import iteration_scalar_metrics_from_run_dir

    row = iteration_scalar_metrics_from_run_dir(run_dir)
    ba = row.get("balanced_accuracy")
    if ba is None:
        bas: List[float] = []
        for path in _detector_results_files_for_run(run_dir):
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                continue
            # New nested format: optimization_validation.performance.balanced_accuracy
            opt = payload.get("optimization_validation")
            if isinstance(opt, dict):
                perf = opt.get("performance")
                if isinstance(perf, dict):
                    v = perf.get("balanced_accuracy")
                    if isinstance(v, (int, float)):
                        bas.append(float(v))
                        continue
            # Legacy/top-level fallback
            v = payload.get("balanced_accuracy")
            if isinstance(v, (int, float)):
                bas.append(float(v))
        if bas:
            return float(sum(bas) / len(bas))
        return None
    try:
        return float(ba)
    except (TypeError, ValueError):
        return None


def stability_dmp_panel_source_label(
    *,
    prefer_classifier_panel_dmps: bool,
    min_frequency: Optional[float] = None,
) -> str:
    """Human-readable label for which detector exports stability aggregation reads.

    When ``min_frequency`` is 0 (or below), DMP FeatureCuts is typically off and the
    CSV is a **discovery locus pool** for gene mapping — not a recurrence-filtered
    stable DMP biomarker panel. Callers should pass ``min_frequency`` whenever known.
    """
    if prefer_classifier_panel_dmps:
        base = "classifier DMP panels (dmps-*-classifier.csv)"
    else:
        base = "discovery DMP exports (dmps-*-discovery.csv)"
    if min_frequency is not None and float(min_frequency) <= 0.0:
        return (
            f"{base}; frequency filter disabled "
            "(union discovery locus pool for gene mapping, not a recurrence-stable DMP panel)"
        )
    return base


def _dmp_panel_role(*, min_frequency: float, prefer_classifier_panel_dmps: bool) -> str:
    """Semantic role of the DMP CSV emitted by stability (for operators/provenance)."""
    if float(min_frequency) <= 0.0:
        return "discovery_locus_pool"
    if prefer_classifier_panel_dmps:
        return "frequency_filtered_classifier_stable"
    return "frequency_filtered_discovery_stable"


def load_discovery_dmps(run_dir: Path, prefer_classifier_panel: bool = False) -> Optional[pd.DataFrame]:
    """Load and concatenate detector DMP exports from a MC run directory.

    Searches all detection subdirectories so that multi-chromosome runs are
    correctly aggregated into a single DataFrame before stability counting.

    When ``prefer_classifier_panel`` is true, the function prefers classifier
    exports (``dmps-*-classifier.csv`` / ``dmps-*.csv``) and falls back to
    discovery exports.
    """
    detection_dirs = list(run_dir.glob("**/detections/*/*"))
    if not detection_dirs:
        detection_dirs = list(run_dir.glob("detections/*/*"))
    frames: list = []
    for d in detection_dirs:
        patterns: List[str]
        if prefer_classifier_panel:
            patterns = ["dmps-*-classifier.csv", "dmps-*-discovery.csv"]
        else:
            patterns = ["dmps-*-discovery.csv"]
        found_local = False
        for pat in patterns:
            for csv in sorted(d.glob(pat)):
                try:
                    frames.append(pd.read_csv(csv))
                    found_local = True
                except Exception:
                    continue
            if found_local:
                break
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def load_classifier_dmp_panel(
    run_dir: Path,
    *,
    max_dmps: Optional[int] = None,
) -> Optional[pd.DataFrame]:
    """
    Load the detector FeatureCuts classifier DMP panel for gene-axis work.

    Uses ``dmps-*-classifier-extended.csv`` when present (mapper/gene annotation panel),
    otherwise ``dmps-*-classifier.csv``. Deduplicates loci genome-wide and optionally caps to the
    top ``max_dmps`` by absolute effect size.
    """
    detection_dirs = list(run_dir.glob("**/detections/*/*"))
    if not detection_dirs:
        detection_dirs = list(run_dir.glob("detections/*/*"))
    frames: list = []
    for d in detection_dirs:
        csvs = sorted(d.glob("dmps-*-classifier-extended.csv"))
        if not csvs:
            csvs = sorted(
                p
                for p in d.glob("dmps-*-classifier.csv")
                if not p.stem.endswith("-classifier-extended")
            )
        for csv in csvs:
            try:
                frames.append(pd.read_csv(csv))
            except Exception:
                continue
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return df
    if "chromosome" not in df.columns or "position" not in df.columns:
        return df

    work = df.copy()
    if "effect_size" not in work.columns:
        work["effect_size"] = 0.0
    work["_abs_effect"] = pd.to_numeric(work["effect_size"], errors="coerce").fillna(0.0).abs()
    keys: List[Tuple[Any, int]] = []
    for _, row in work.iterrows():
        try:
            keys.append(_dmp_key_from_row(row))
        except Exception:
            keys.append((None, -1))
    work["_dmp_key"] = keys
    work = work[work["_dmp_key"].map(lambda k: k[1] >= 0)].copy()
    work = work.sort_values(["_abs_effect"], ascending=False, na_position="last")
    work = work.drop_duplicates(subset=["_dmp_key"], keep="first")
    if max_dmps is not None and int(max_dmps) > 0 and len(work) > int(max_dmps):
        work = work.head(int(max_dmps)).copy()
    return work.drop(columns=["_abs_effect", "_dmp_key"], errors="ignore")


def load_enricher_genes(run_dir: Path) -> Optional[pd.DataFrame]:
    """Load enricher gene output (all-gene_name-combined.csv or similar)."""
    enricher_dirs = list(run_dir.glob("**/enricher/*/*")) or list(run_dir.glob("enricher/*/*"))
    for d in enricher_dirs:
        for csv in d.glob("*gene*.csv"):
            try:
                df = pd.read_csv(csv)
                if "gene_name" in df.columns:
                    return df
            except Exception:
                continue
    return None


def load_mapper_genes(run_dir: Path) -> Optional[pd.DataFrame]:
    """Load mapper-ranked genes from all-gene_name-combined.csv exports."""
    mapper_dirs = list(run_dir.glob("**/mapper/*/*")) or list(run_dir.glob("mapper/*/*"))
    for d in mapper_dirs:
        for csv in sorted(d.glob("*all-gene_name-combined.csv")):
            try:
                df = pd.read_csv(csv)
                if "gene_name" in df.columns:
                    return df
            except Exception:
                continue
        for csv in sorted(d.glob("*gene_name*.csv")):
            try:
                df = pd.read_csv(csv)
                if "gene_name" in df.columns and "gene_importance" in df.columns:
                    return df
            except Exception:
                continue
    return None


def load_classifier_genes(run_dir: Path) -> Optional[pd.DataFrame]:
    """Load gene FeatureCuts classifier panel from a MC run directory."""
    csv_path = run_dir / "gene_stability" / "genes-classifier.csv"
    if not csv_path.is_file():
        return None
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None
    if df.empty or "gene_name" not in df.columns:
        return None
    return df


def _numeric_summary(values: List[float]) -> Dict[str, Any]:
    """Compact numeric summary helper."""
    if not values:
        return {}
    vals = [float(v) for v in values]
    return {
        "count": int(len(vals)),
        "mean": float(sum(vals) / len(vals)),
        "min": float(min(vals)),
        "max": float(max(vals)),
    }


def _consistent_value(values: List[Any]) -> tuple[Optional[Any], bool]:
    """
    Return a value only when all non-null entries are identical.

    Returns (value, is_consistent). If no usable values exist, returns (None, True).
    """
    clean = [v for v in values if v is not None]
    if not clean:
        return None, True
    first = clean[0]
    if all(v == first for v in clean[1:]):
        return first, True
    return None, False


def _detector_results_files_for_run(run_dir: Path) -> List[Path]:
    """Locate detector run summary files under run_dir detections."""
    result_files: List[Path] = []
    detections_roots = [p for p in run_dir.rglob("detections") if p.is_dir()]
    for det_root in detections_roots:
        result_files.extend(sorted(det_root.rglob("results-*.json")))
    return result_files


def extract_detector_parameters_for_run(run_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Extract minimal detector/filter parameters from one MC run.

    Uses detector ``results-*.json`` files (typically one per chromosome).
    """
    result_files = _detector_results_files_for_run(run_dir)
    if not result_files:
        return None

    n_dmps_exported_vals: List[float] = []
    total_statistical_dmps_vals: List[float] = []
    total_biological_dmps_vals: List[float] = []
    effect_size_coverage_vals: List[Any] = []
    delta_mean_reduction_vals: List[Any] = []
    classifier_dmp_selection_vals: List[Any] = []
    dynamic_dmp_cutoff_enabled_vals: List[Any] = []

    for path in result_files:
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue

        if isinstance(payload.get("n_dmps_exported"), (int, float)):
            n_dmps_exported_vals.append(float(payload["n_dmps_exported"]))
        if isinstance(payload.get("total_statistical_dmps"), (int, float)):
            total_statistical_dmps_vals.append(float(payload["total_statistical_dmps"]))
        if isinstance(payload.get("total_biological_dmps"), (int, float)):
            total_biological_dmps_vals.append(float(payload["total_biological_dmps"]))

        cfg = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        effect_size_coverage_vals.append(cfg.get("effect_size_coverage"))
        delta_mean_reduction_vals.append(cfg.get("delta_mean_reduction"))
        classifier_dmp_selection_vals.append(cfg.get("classifier_dmp_selection"))
        dynamic_dmp_cutoff_enabled_vals.append(cfg.get("dynamic_dmp_cutoff_enabled"))

    if not (
        n_dmps_exported_vals
        or total_statistical_dmps_vals
        or total_biological_dmps_vals
        or any(v is not None for v in effect_size_coverage_vals)
        or any(v is not None for v in delta_mean_reduction_vals)
        or any(v is not None for v in classifier_dmp_selection_vals)
        or any(v is not None for v in dynamic_dmp_cutoff_enabled_vals)
    ):
        return None

    effect_size_coverage, esc_ok = _consistent_value(effect_size_coverage_vals)
    delta_mean_reduction, dmr_ok = _consistent_value(delta_mean_reduction_vals)
    classifier_dmp_selection, cds_ok = _consistent_value(classifier_dmp_selection_vals)
    dynamic_dmp_cutoff_enabled, ddc_ok = _consistent_value(dynamic_dmp_cutoff_enabled_vals)

    inconsistent_fields: List[str] = []
    if not esc_ok:
        inconsistent_fields.append("effect_size_coverage")
    if not dmr_ok:
        inconsistent_fields.append("delta_mean_reduction")
    if not cds_ok:
        inconsistent_fields.append("classifier_dmp_selection")
    if not ddc_ok:
        inconsistent_fields.append("dynamic_dmp_cutoff_enabled")

    return {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "n_result_files": int(len(result_files)),
        "n_dmps_exported": _numeric_summary(n_dmps_exported_vals),
        "total_statistical_dmps": _numeric_summary(total_statistical_dmps_vals),
        "total_biological_dmps": _numeric_summary(total_biological_dmps_vals),
        "effect_size_coverage": effect_size_coverage,
        "delta_mean_reduction": delta_mean_reduction,
        "classifier_dmp_selection": classifier_dmp_selection,
        "dynamic_dmp_cutoff_enabled": dynamic_dmp_cutoff_enabled,
        "inconsistent_fields": inconsistent_fields,
    }


def compute_detector_parameter_stability(monte_carlo_runs_root: Path) -> Dict[str, Any]:
    """Aggregate minimal detector/filter parameter summaries across Monte Carlo runs."""
    runs = sorted(monte_carlo_runs_root.glob("run_*"))
    per_run: List[Dict[str, Any]] = []
    n_runs_without_results = 0

    for run_dir in runs:
        row = extract_detector_parameters_for_run(run_dir)
        if row is None:
            n_runs_without_results += 1
            continue
        per_run.append(row)

    if not per_run:
        return {
            "per_run": [],
            "aggregates": {
                "n_runs_with_results": 0,
                "n_runs_without_results": n_runs_without_results,
            },
        }

    numeric_values: Dict[str, List[float]] = defaultdict(list)
    categorical_values: Dict[str, Counter] = defaultdict(Counter)
    numeric_scalar_keys = [
        "n_result_files",
        "effect_size_coverage",
        "delta_mean_reduction",
    ]
    categorical_keys = [
        "classifier_dmp_selection",
        "dynamic_dmp_cutoff_enabled",
    ]
    nested_numeric_keys = [
        "n_dmps_exported",
        "total_statistical_dmps",
        "total_biological_dmps",
    ]

    for row in per_run:
        for key in numeric_scalar_keys:
            val = row.get(key)
            if isinstance(val, (int, float)):
                numeric_values[key].append(float(val))
        for key in categorical_keys:
            val = row.get(key)
            if val is not None:
                categorical_values[key][str(val)] += 1
        for key in nested_numeric_keys:
            node = row.get(key)
            if isinstance(node, dict):
                for suffix in ("mean", "min", "max"):
                    val = node.get(suffix)
                    if isinstance(val, (int, float)):
                        numeric_values[f"{key}_{suffix}"].append(float(val))

    aggregates = {
        "n_runs_with_results": int(len(per_run)),
        "n_runs_without_results": int(n_runs_without_results),
        "numeric": {
            key: _numeric_summary(vals)
            for key, vals in numeric_values.items()
            if vals
        },
        "categorical": {
            key: dict(counter)
            for key, counter in categorical_values.items()
            if counter
        },
    }
    return {"per_run": per_run, "aggregates": aggregates}


def _dmp_key_from_row(row: Any) -> Tuple[Any, int]:
    chrom = row["chromosome"]
    try:
        chrom_n = int(chrom)
    except (TypeError, ValueError):
        chrom_n = str(chrom).strip()
    return (chrom_n, int(row["position"]))


def _bh_qvalues(pvals: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg q-values for finite p-values in [0,1]."""
    n = int(len(pvals))
    if n == 0:
        return np.array([], dtype=float)
    order = np.argsort(pvals)
    ranked = pvals[order]
    q = np.empty(n, dtype=float)
    prev = 1.0
    for i in range(n - 1, -1, -1):
        rank = i + 1
        val = float(ranked[i] * n / rank)
        prev = min(prev, val)
        q[i] = prev
    out = np.empty(n, dtype=float)
    out[order] = np.clip(q, 0.0, 1.0)
    return out


def _signed_stouffer(pvals: np.ndarray, signs: np.ndarray, weights: np.ndarray) -> float:
    """Return two-sided p-value from signed weighted Stouffer aggregation."""
    if pvals.size == 0:
        return float("nan")
    pvals = np.clip(pvals.astype(float), 1e-300, 1.0 - 1e-16)
    z = norm.ppf(1.0 - pvals / 2.0) * signs.astype(float)
    w = weights.astype(float)
    valid = np.isfinite(z) & np.isfinite(w) & (w > 0)
    if not np.any(valid):
        return float("nan")
    z = z[valid]
    w = w[valid]
    denom = np.sqrt(np.sum(w ** 2))
    if not np.isfinite(denom) or denom <= 0:
        return float("nan")
    z_combined = float(np.sum(w * z) / denom)
    p = float(2.0 * (1.0 - norm.cdf(abs(z_combined))))
    return float(np.clip(p, 0.0, 1.0))


def _frequency_count_distribution(
    dmp_freq_df: pd.DataFrame,
    chromosome: Any,
) -> pd.DataFrame:
    """
    Frequency histogram for one chromosome with count-based Y axis.

    Returns columns: frequency_pct, dmps_count.
    """
    subset = dmp_freq_df[dmp_freq_df["chromosome"].astype(str) == str(chromosome)].copy()
    if subset.empty:
        return pd.DataFrame(columns=["frequency_pct", "dmps_count"])
    dist = (
        subset["frequency"]
        .dropna()
        .astype(float)
        .value_counts()
        .sort_index()
        .reset_index()
    )
    if dist.empty:
        return pd.DataFrame(columns=["frequency_pct", "dmps_count"])
    dist.columns = ["frequency", "dmps_count"]
    dist["frequency_pct"] = dist["frequency"] * 100.0
    return dist[["frequency_pct", "dmps_count"]]


def _validate_stability_recurrence_metadata(
    df: pd.DataFrame,
    *,
    source_label: str,
    eps: float = 1e-9,
) -> pd.DataFrame:
    """
    Validate recurrence metadata invariants for stability/fixed-panel tables.

    Enforced when columns are present:
      - frequency is finite and within [0, 1]
      - count and n_runs are finite and non-negative
      - n_runs > 0
      - count <= n_runs
      - frequency ~= count / n_runs when all three are present
    """
    if df is None or df.empty:
        return df

    recurrence_cols = {"frequency", "count", "n_runs"}
    present = recurrence_cols.intersection(df.columns)
    if not present:
        return df

    numeric: Dict[str, pd.Series] = {}
    for col in present:
        s = pd.to_numeric(df[col], errors="coerce")
        bad = ~np.isfinite(s.to_numpy(dtype=float))
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid stability recurrence metadata in {source_label}: "
                f"column '{col}' must be finite numeric values. "
                f"Example row(s): {bad_rows}"
            )
        numeric[col] = s.astype(float)

    if "frequency" in numeric:
        freq = numeric["frequency"]
        bad = (freq < 0.0 - eps) | (freq > 1.0 + eps)
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid stability recurrence metadata in {source_label}: "
                "column 'frequency' must be in [0, 1]. "
                f"Example row(s): {bad_rows}"
            )

    for col in ("count", "n_runs"):
        if col in numeric:
            bad = numeric[col] < 0.0 - eps
            if bad.any():
                bad_rows = (df.index[bad][:5] + 1).tolist()
                raise ValueError(
                    f"Invalid stability recurrence metadata in {source_label}: "
                    f"column '{col}' must be >= 0. "
                    f"Example row(s): {bad_rows}"
                )

    if "n_runs" in numeric:
        bad = numeric["n_runs"] <= eps
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid stability recurrence metadata in {source_label}: "
                "column 'n_runs' must be > 0. "
                f"Example row(s): {bad_rows}"
            )

    if {"count", "n_runs"}.issubset(numeric):
        bad = numeric["count"] > (numeric["n_runs"] + eps)
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid stability recurrence metadata in {source_label}: "
                "column 'count' must be <= 'n_runs'. "
                f"Example row(s): {bad_rows}"
            )

    if {"frequency", "count", "n_runs"}.issubset(numeric):
        expected = numeric["count"] / numeric["n_runs"]
        bad = (numeric["frequency"] - expected).abs() > eps
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid stability recurrence metadata in {source_label}: "
                "'frequency' must equal count / n_runs. "
                f"Example row(s): {bad_rows}"
            )

    return df


def _select_stable_dmps_df(
    dmp_freq_df: pd.DataFrame,
    min_frequency: float = 0.7,
    top_n: Optional[int] = None,
) -> pd.DataFrame:
    """Build selected stable DMP table from full frequency table."""
    if dmp_freq_df is None or dmp_freq_df.empty or "frequency" not in dmp_freq_df.columns:
        return pd.DataFrame(
            columns=[
                "chromosome",
                "position",
                "frequency",
                "count",
                "n_runs",
                "effect_size",
                "p_value",
                "q_value",
            ]
        )
    selected = dmp_freq_df[dmp_freq_df["frequency"] >= min_frequency].copy()
    if top_n is not None and len(selected) > top_n:
        selected = selected.head(top_n)
    for col in ("chromosome", "position", "frequency", "count", "n_runs", "effect_size", "p_value", "q_value"):
        if col not in selected.columns:
            selected[col] = pd.Series(
                dtype="float64" if col in {"frequency", "effect_size", "p_value", "q_value"} else "object"
            )
    return selected


def _score_stable_dmps(
    selected_df: pd.DataFrame,
    score_eps: float = 1e-12,
) -> pd.DataFrame:
    """Score stable DMPs using effect_size * sqrt(frequency) and return sorted table."""
    if selected_df is None or selected_df.empty:
        out = selected_df.copy() if selected_df is not None else pd.DataFrame()
        if "combined_score" not in out.columns:
            out["combined_score"] = pd.Series(dtype="float64")
        return out

    out = selected_df.copy()
    freq = pd.to_numeric(out.get("frequency"), errors="coerce").clip(lower=0.0).fillna(0.0)
    effect = pd.to_numeric(out.get("effect_size"), errors="coerce").fillna(0.0)
    effect = effect.where(np.isfinite(effect), 0.0)
    effect = effect.clip(lower=0.0)
    out["combined_score"] = effect * np.sqrt(freq)
    out["combined_score"] = pd.to_numeric(out["combined_score"], errors="coerce").fillna(0.0)
    out["log_combined_score"] = np.log(out["combined_score"] + float(score_eps))
    out = out.sort_values(
        ["combined_score", "frequency", "effect_size"],
        ascending=[False, False, False],
        na_position="last",
    ).reset_index(drop=True)
    return out


def _detect_log_score_elbow(
    sorted_scores: pd.Series,
    score_eps: float = 1e-12,
) -> tuple[int, float]:
    """Detect a robust elbow for descending scores using log-scale distance-to-line."""
    scores = pd.to_numeric(sorted_scores, errors="coerce").fillna(0.0).astype(float).to_numpy()
    n = int(len(scores))
    if n == 0:
        return 0, 0.0
    if n == 1:
        return 0, float(scores[0])

    log_scores = np.log(np.clip(scores, 0.0, None) + float(score_eps))
    if not np.isfinite(log_scores).any() or n <= 2:
        idx = int(max(0, n - 1))
        return idx, float(scores[idx])

    x = np.linspace(0.0, 1.0, n, dtype=float)
    y = log_scores
    y_min = float(np.nanmin(y))
    y_max = float(np.nanmax(y))
    if not np.isfinite(y_min) or not np.isfinite(y_max) or np.isclose(y_max, y_min):
        idx = int(max(0, n - 1))
        return idx, float(scores[idx])
    y_norm = (y - y_min) / (y_max - y_min)

    x0, y0 = x[0], y_norm[0]
    x1, y1 = x[-1], y_norm[-1]
    denom = float(np.hypot(y1 - y0, x1 - x0))
    if denom <= 0.0 or not np.isfinite(denom):
        idx = int(max(0, n - 1))
        return idx, float(scores[idx])

    # Perpendicular distance from each point to endpoint line.
    distances = np.abs((y1 - y0) * x - (x1 - x0) * y_norm + x1 * y0 - y1 * x0) / denom
    if n > 2:
        distances[0] = -1.0
        distances[-1] = -1.0
    idx = int(np.argmax(distances))
    if idx < 0 or idx >= n:
        idx = int(max(0, n - 1))
    return idx, float(scores[idx])


def _select_dual_cutoff_dmps(
    dmp_freq_df: pd.DataFrame,
    min_frequency: float = 0.7,
    top_n: Optional[int] = None,
    relaxed_cutoff_mode: str = "elbow_log_score",
    relaxed_multiplier: float = 0.5,
    score_eps: float = 1e-12,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    """Select strict and relaxed stable DMP sets from a frequency table."""
    selected = _select_stable_dmps_df(dmp_freq_df, min_frequency=min_frequency, top_n=None)
    scored = _score_stable_dmps(selected, score_eps=score_eps)
    if scored.empty:
        return selected.copy(), selected.copy(), scored, {
            "enabled": True,
            "strict_cutoff_mode": "elbow_log_score",
            "relaxed_cutoff_mode": relaxed_cutoff_mode,
            "strict_cutoff_index": 0,
            "strict_cutoff_score": 0.0,
            "relaxed_cutoff_index": 0,
            "relaxed_cutoff_score": 0.0,
            "min_frequency": float(min_frequency),
            "n_candidates_after_min_frequency": 0,
            "n_strict_selected": 0,
            "n_relaxed_selected": 0,
            "relaxed_multiplier": float(relaxed_multiplier),
        }

    strict_idx, strict_score = _detect_log_score_elbow(
        scored["combined_score"], score_eps=score_eps
    )
    strict = scored.iloc[: strict_idx + 1].copy()

    relaxed_mode = str(relaxed_cutoff_mode).strip().lower()
    relaxed_idx = strict_idx
    relaxed_score = strict_score
    if relaxed_mode == "strict_multiplier":
        mult = float(relaxed_multiplier)
        if mult <= 0:
            mult = 1.0
        relaxed_score = float(strict_score * mult)
        relaxed = scored[scored["combined_score"] >= relaxed_score].copy()
        if relaxed.empty:
            relaxed = strict.copy()
    else:
        tail = scored.iloc[strict_idx:].reset_index(drop=True)
        tail_idx, tail_score = _detect_log_score_elbow(
            tail["combined_score"], score_eps=score_eps
        )
        relaxed_idx = int(strict_idx + tail_idx)
        relaxed_score = float(tail_score)
        if relaxed_score > strict_score:
            relaxed_score = strict_score
            relaxed_idx = strict_idx
        relaxed = scored.iloc[: relaxed_idx + 1].copy()

    # Guarantee strict subset semantics.
    if len(relaxed) < len(strict):
        relaxed = strict.copy()
        relaxed_idx = strict_idx
        relaxed_score = strict_score

    if top_n is not None:
        strict = strict.head(top_n).copy()
        relaxed = relaxed.head(top_n).copy()

    diagnostics = {
        "enabled": True,
        "strict_cutoff_mode": "elbow_log_score",
        "relaxed_cutoff_mode": relaxed_mode,
        "strict_cutoff_index": int(strict_idx),
        "strict_cutoff_score": float(strict_score),
        "relaxed_cutoff_index": int(relaxed_idx),
        "relaxed_cutoff_score": float(relaxed_score),
        "min_frequency": float(min_frequency),
        "n_candidates_after_min_frequency": int(len(scored)),
        "n_strict_selected": int(len(strict)),
        "n_relaxed_selected": int(len(relaxed)),
        "relaxed_multiplier": float(relaxed_multiplier),
    }
    return strict, relaxed, scored, diagnostics


def write_dmp_frequency_plot_by_chromosome(
    dmp_freq_df: pd.DataFrame,
    selected_dmp_df: pd.DataFrame,
    output_dir: Path,
) -> tuple[Optional[Path], Dict[str, str], Dict[str, Dict[str, int]]]:
    """
    Write Plotly frequency charts with one series per chromosome and all-vs-selected traces.

    Returns:
      - combined chart path
      - per chromosome chart paths
      - count summaries by chromosome
    """
    if dmp_freq_df.empty or "frequency" not in dmp_freq_df.columns:
        return None, {}, {}
    try:
        import plotly.graph_objects as go
    except Exception as e:
        logger.warning(f"Plotly not available; skipping DMP frequency chart: {e}")
        return None, {}, {}

    if "chromosome" not in dmp_freq_df.columns:
        return None, {}, {}

    output_dir.mkdir(parents=True, exist_ok=True)
    chromosomes = [str(c) for c in sorted(dmp_freq_df["chromosome"].dropna().unique(), key=str)]
    if not chromosomes:
        return None, {}, {}

    combined = go.Figure()
    # 25 colors: 24 chromosome series + selected marker
    palette_25 = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
        "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#393b79", "#637939",
        "#8c6d31", "#843c39", "#7b4173", "#3182bd", "#31a354", "#756bb1",
        "#636363", "#e6550d", "#969696", "#6baed6", "#fd8d3c", "#74c476",
        "#000000", "#e41a1c",
    ]
    selected_color = palette_25[24]

    per_chrom_paths: Dict[str, str] = {}
    count_summary_by_chrom: Dict[str, Dict[str, int]] = {}
    selected_points_x: List[float] = []
    selected_points_y: List[float] = []
    selected_points_text: List[str] = []

    for i, chrom in enumerate(chromosomes):
        all_dist = _frequency_count_distribution(dmp_freq_df, chrom)
        sel_dist = _frequency_count_distribution(selected_dmp_df, chrom)
        if all_dist.empty:
            continue

        # Combined chart traces
        combined.add_trace(
            go.Scatter(
                x=all_dist["frequency_pct"],
                y=all_dist["dmps_count"],
                mode="lines+markers",
                name=f"chr{chrom}",
                line={"color": palette_25[i % 24]},
                marker={"color": palette_25[i % 24]},
                hovertemplate=f"chr{chrom} all<br>Frequency: %{{x:.2f}}%<br>DMPs: %{{y:.0f}}<extra></extra>",
            )
        )
        selected_threshold = None
        if not sel_dist.empty:
            # Selection point: left-most selected frequency on X axis
            selected_threshold = float(sel_dist["frequency_pct"].min())
            selected_points_x.append(selected_threshold)
            selected_points_y.append(0.0)
            selected_points_text.append(f"chr{chrom} selected@{selected_threshold:.2f}%")

        # Per chromosome chart with all-vs-selected
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=all_dist["frequency_pct"],
                y=all_dist["dmps_count"],
                mode="lines+markers",
                name="all",
                hovertemplate="All DMPs<br>Frequency: %{x:.2f}%<br>DMPs: %{y:.0f}<extra></extra>",
            )
        )
        if selected_threshold is not None:
            fig.add_trace(
                go.Scatter(
                    x=[selected_threshold],
                    y=[0.0],
                    mode="markers",
                    marker={"size": 10, "symbol": "circle", "color": selected_color},
                    name="selected",
                    hovertemplate="Selected threshold<br>Frequency: %{x:.2f}%<extra></extra>",
                )
            )
            fig.add_vline(
                x=selected_threshold,
                line_dash="dot",
                annotation_text=f"Selected ≈ {selected_threshold:.2f}%",
                annotation_position="top left",
            )

        fig.update_layout(
            title=f"DMP Frequency Distribution (chr{chrom})",
            xaxis_title="Frequency across runs (%)",
            yaxis_title="DMP count",
            template="plotly_white",
        )
        chrom_path = output_dir / f"dmp_frequency_chr_{chrom}.html"
        fig.write_html(str(chrom_path), include_plotlyjs="cdn", full_html=True)
        per_chrom_paths[str(chrom)] = str(chrom_path)

        all_total = int(
            len(dmp_freq_df[dmp_freq_df["chromosome"].astype(str) == str(chrom)])
        )
        sel_total = int(
            len(selected_dmp_df[selected_dmp_df["chromosome"].astype(str) == str(chrom)])
        )
        count_summary_by_chrom[str(chrom)] = {
            "all_dmps": all_total,
            "selected_dmps": sel_total,
        }

    if not per_chrom_paths:
        return None, {}, {}

    if selected_points_x:
        combined.add_trace(
            go.Scatter(
                x=selected_points_x,
                y=selected_points_y,
                mode="markers",
                marker={"size": 9, "symbol": "circle", "color": selected_color},
                name="selected",
                text=selected_points_text,
                hovertemplate="%{text}<extra></extra>",
            )
        )

    combined.update_layout(
        title="DMP Frequency Distribution by Chromosome",
        xaxis_title="Frequency across runs (%)",
        yaxis_title="DMP count",
        template="plotly_white",
    )
    combined_path = output_dir / "dmp_frequency_by_chromosome.html"
    combined.write_html(str(combined_path), include_plotlyjs="cdn", full_html=True)
    return combined_path, per_chrom_paths, count_summary_by_chrom


def _list_monte_carlo_run_dirs(monte_carlo_runs_root: Path) -> List[Path]:
    runs = sorted(monte_carlo_runs_root.glob("run_*"))
    if not runs:
        runs = sorted(monte_carlo_runs_root.glob("run_0*"))
    return [p for p in runs if p.is_dir()]


def _compute_dmp_stability_from_run_dirs(
    run_dirs: List[Path],
    min_frequency: float = 0.7,
    min_balanced_accuracy: Optional[float] = None,
    prefer_classifier_panel_dmps: bool = False,
    source_label: str = "stability::dmp_frequency",
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    dmp_run_hits: Dict[Tuple[Any, int], set[str]] = defaultdict(set)
    dmp_effect_sum: Dict[Tuple[Any, int], float] = defaultdict(float)
    dmp_effect_runs: Dict[Tuple[Any, int], int] = defaultdict(int)
    dmp_run_pvals: Dict[Tuple[Any, int], List[float]] = defaultdict(list)
    dmp_run_signs: Dict[Tuple[Any, int], List[float]] = defaultdict(list)
    dmp_run_weights: Dict[Tuple[Any, int], List[float]] = defaultdict(list)
    run_count = 0
    skipped_no_discovery = 0
    skipped_low_ba = 0

    for run_dir in run_dirs:
        df = load_discovery_dmps(run_dir, prefer_classifier_panel=prefer_classifier_panel_dmps)
        if df is None or "position" not in df.columns or "chromosome" not in df.columns:
            skipped_no_discovery += 1
            continue
        if min_balanced_accuracy is not None:
            ba = run_balanced_accuracy(run_dir)
            if ba is None or ba < float(min_balanced_accuracy):
                skipped_low_ba += 1
                continue
        run_count += 1
        seen_keys: set[Tuple[Any, int]] = set()
        for _, row in df.iterrows():
            try:
                key = _dmp_key_from_row(row)
            except Exception:
                continue
            seen_keys.add(key)
        run_id = str(run_dir.name)
        for key in seen_keys:
            dmp_run_hits[key].add(run_id)

        # Compute one effect-size value per DMP key per run (mean over run duplicates),
        # then average those values across runs where the DMP is present.
        run_effect_values: Dict[Tuple[Any, int], List[float]] = defaultdict(list)
        run_p_values: Dict[Tuple[Any, int], List[float]] = defaultdict(list)
        run_delta_values: Dict[Tuple[Any, int], List[float]] = defaultdict(list)
        for _, row in df.iterrows():
            try:
                key = _dmp_key_from_row(row)
            except Exception:
                continue
            effect_val = pd.to_numeric(row.get("effect_size"), errors="coerce")
            if pd.notna(effect_val):
                run_effect_values[key].append(float(effect_val))
            p_val = pd.to_numeric(row.get("p_value"), errors="coerce")
            if pd.notna(p_val) and np.isfinite(p_val):
                run_p_values[key].append(float(p_val))
            delta_val = pd.to_numeric(row.get("delta_mean"), errors="coerce")
            if pd.notna(delta_val):
                run_delta_values[key].append(float(delta_val))

        for key, vals in run_effect_values.items():
            if not vals:
                continue
            dmp_effect_sum[key] += float(sum(vals) / len(vals))
            dmp_effect_runs[key] += 1

        # Per-run collapse -> one p-value and one signed direction per key.
        for key in seen_keys:
            pvals = run_p_values.get(key, [])
            if pvals:
                p_run = float(np.median(np.asarray(pvals, dtype=float)))
            else:
                p_run = float("nan")
            deltas = run_delta_values.get(key, [])
            if deltas:
                direction = float(np.sign(np.mean(np.asarray(deltas, dtype=float))))
            else:
                direction = 1.0
            if not np.isfinite(direction) or direction == 0.0:
                direction = 1.0
            if np.isfinite(p_run):
                dmp_run_pvals[key].append(p_run)
                dmp_run_signs[key].append(direction)
                dmp_run_weights[key].append(1.0)

    if run_count == 0:
        return pd.DataFrame(), {
            "n_runs_analyzed": 0,
            "skipped_no_discovery": skipped_no_discovery,
            "skipped_low_balanced_accuracy": skipped_low_ba,
            "min_balanced_accuracy": min_balanced_accuracy,
            "min_frequency": float(min_frequency),
            "dmp_recurrence_filtering": float(min_frequency) > 0.0,
            "dmp_panel_role": _dmp_panel_role(
                min_frequency=float(min_frequency),
                prefer_classifier_panel_dmps=bool(prefer_classifier_panel_dmps),
            ),
            "prefer_classifier_panel_dmps": bool(prefer_classifier_panel_dmps),
            "dmp_panel_source": stability_dmp_panel_source_label(
                prefer_classifier_panel_dmps=prefer_classifier_panel_dmps,
                min_frequency=float(min_frequency),
            ),
        }

    data = []
    for (chrom, pos), runs_with_hit in dmp_run_hits.items():
        count = int(len(runs_with_hit))
        freq = count / run_count
        effect_runs = int(dmp_effect_runs.get((chrom, pos), 0))
        effect_size = (
            float(dmp_effect_sum[(chrom, pos)] / effect_runs)
            if effect_runs > 0
            else float("nan")
        )
        pvals = np.asarray(dmp_run_pvals.get((chrom, pos), []), dtype=float)
        signs = np.asarray(dmp_run_signs.get((chrom, pos), []), dtype=float)
        weights = np.asarray(dmp_run_weights.get((chrom, pos), []), dtype=float)
        dmp_p = _signed_stouffer(pvals, signs, weights)
        data.append({
            "chromosome": chrom,
            "position": pos,
            "frequency": freq,
            "count": count,
            "n_runs": run_count,
            "effect_size": effect_size,
            "p_value": dmp_p,
        })

    df = pd.DataFrame(data)
    if not df.empty:
        pvals = pd.to_numeric(df.get("p_value"), errors="coerce")
        finite_mask = pvals.notna().to_numpy()
        qvals = np.full(len(df), np.nan, dtype=float)
        if finite_mask.any():
            qvals[finite_mask] = _bh_qvalues(pvals.to_numpy(dtype=float)[finite_mask])
        df["q_value"] = qvals
        df = df.sort_values(
            ["frequency", "effect_size"],
            ascending=[False, False],
            na_position="last",
        )

    if not df.empty:
        df = _validate_stability_recurrence_metadata(
            df,
            source_label=source_label,
        )
    stable = df[df["frequency"] >= min_frequency] if not df.empty else pd.DataFrame()
    summary = {
        "n_runs_analyzed": run_count,
        "skipped_no_discovery": skipped_no_discovery,
        "skipped_low_balanced_accuracy": skipped_low_ba,
        "min_balanced_accuracy": min_balanced_accuracy,
        "total_unique_dmps": len(dmp_run_hits),
        "stable_dmps_at_threshold": len(stable),
        "min_frequency": min_frequency,
        "dmp_recurrence_filtering": float(min_frequency) > 0.0,
        "dmp_panel_role": _dmp_panel_role(
            min_frequency=float(min_frequency),
            prefer_classifier_panel_dmps=bool(prefer_classifier_panel_dmps),
        ),
        "stable_dmp_fraction": len(stable) / len(dmp_run_hits) if len(dmp_run_hits) > 0 else 0.0,
        "prefer_classifier_panel_dmps": bool(prefer_classifier_panel_dmps),
        "dmp_panel_source": stability_dmp_panel_source_label(
            prefer_classifier_panel_dmps=prefer_classifier_panel_dmps,
            min_frequency=float(min_frequency),
        ),
    }
    return df, summary


def compute_dmp_stability(
    monte_carlo_runs_root: Path,
    min_frequency: float = 0.7,
    min_balanced_accuracy: Optional[float] = None,
    prefer_classifier_panel_dmps: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Count how often each DMP appears in detector exports across runs.

    If ``min_balanced_accuracy`` is set, only iterations whose ``validation_metrics.json``
    reports ``balanced_accuracy >= min_balanced_accuracy`` contribute DMPs and define the
    frequency denominator.
    """
    runs = _list_monte_carlo_run_dirs(monte_carlo_runs_root)
    return _compute_dmp_stability_from_run_dirs(
        run_dirs=runs,
        min_frequency=min_frequency,
        min_balanced_accuracy=min_balanced_accuracy,
        prefer_classifier_panel_dmps=prefer_classifier_panel_dmps,
        source_label=f"{monte_carlo_runs_root}/stability::dmp_frequency",
    )


def _stable_dmp_key_set(dmp_freq_df: pd.DataFrame, min_frequency: float) -> set[Tuple[str, int]]:
    if dmp_freq_df.empty:
        return set()
    selected = dmp_freq_df[dmp_freq_df["frequency"] >= float(min_frequency)]
    keys: set[Tuple[str, int]] = set()
    for _, row in selected.iterrows():
        chrom = row.get("chromosome")
        pos = row.get("position")
        if pd.isna(chrom) or pd.isna(pos):
            continue
        try:
            keys.add((str(chrom), int(pos)))
        except Exception:
            continue
    return keys


def _jaccard_similarity(a: set[Any], b: set[Any]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return float(len(a & b) / len(union))


def evaluate_dmp_stability_convergence(
    monte_carlo_runs_root: Path,
    *,
    min_frequency: float = 0.7,
    min_balanced_accuracy: Optional[float] = None,
    prefer_classifier_panel_dmps: bool = False,
    min_iterations: int = 20,
    convergence_window: int = 5,
    convergence_jaccard: float = 0.98,
    convergence_max_size_delta: float = 0.02,
) -> Dict[str, Any]:
    """
    Compare stable panel at k qualifying runs vs k-window qualifying runs.

    Returns convergence diagnostics for one checkpoint. Caller handles patience.
    """
    all_runs = _list_monte_carlo_run_dirs(monte_carlo_runs_root)
    qualifying_runs: List[Path] = []
    skipped_no_discovery = 0
    skipped_low_ba = 0
    for run_dir in all_runs:
        df = load_discovery_dmps(run_dir, prefer_classifier_panel=prefer_classifier_panel_dmps)
        if df is None or "position" not in df.columns or "chromosome" not in df.columns:
            skipped_no_discovery += 1
            continue
        if min_balanced_accuracy is not None:
            ba = run_balanced_accuracy(run_dir)
            if ba is None or ba < float(min_balanced_accuracy):
                skipped_low_ba += 1
                continue
        qualifying_runs.append(run_dir)

    k = len(qualifying_runs)
    eligible = (k >= int(min_iterations)) and (k > int(convergence_window))
    if not eligible:
        return {
            "axis": "dmp",
            "eligible_for_check": False,
            "converged_checkpoint": False,
            "n_attempted_runs": len(all_runs),
            "n_runs_analyzed": k,
            "skipped_no_discovery": skipped_no_discovery,
            "skipped_low_balanced_accuracy": skipped_low_ba,
            "min_balanced_accuracy": min_balanced_accuracy,
            "min_frequency": float(min_frequency),
            "min_iterations": int(min_iterations),
            "convergence_window": int(convergence_window),
            "convergence_jaccard": float(convergence_jaccard),
            "convergence_max_size_delta": float(convergence_max_size_delta),
            "reason": "insufficient_qualifying_runs",
        }

    current_df, _ = _compute_dmp_stability_from_run_dirs(
        run_dirs=qualifying_runs,
        min_frequency=min_frequency,
        min_balanced_accuracy=None,
        prefer_classifier_panel_dmps=prefer_classifier_panel_dmps,
        source_label=f"{monte_carlo_runs_root}/stability::convergence_current",
    )
    previous_df, _ = _compute_dmp_stability_from_run_dirs(
        run_dirs=qualifying_runs[: k - int(convergence_window)],
        min_frequency=min_frequency,
        min_balanced_accuracy=None,
        prefer_classifier_panel_dmps=prefer_classifier_panel_dmps,
        source_label=f"{monte_carlo_runs_root}/stability::convergence_previous",
    )
    stable_current = _stable_dmp_key_set(current_df, min_frequency=min_frequency)
    stable_previous = _stable_dmp_key_set(previous_df, min_frequency=min_frequency)
    jaccard = _jaccard_similarity(stable_current, stable_previous)
    prev_size = len(stable_previous)
    curr_size = len(stable_current)
    size_delta = float(abs(curr_size - prev_size) / max(1, prev_size))
    converged_checkpoint = (
        (jaccard >= float(convergence_jaccard))
        and (size_delta <= float(convergence_max_size_delta))
    )
    return {
        "axis": "dmp",
        "eligible_for_check": True,
        "converged_checkpoint": bool(converged_checkpoint),
        "n_attempted_runs": len(all_runs),
        "n_runs_analyzed": k,
        "skipped_no_discovery": skipped_no_discovery,
        "skipped_low_balanced_accuracy": skipped_low_ba,
        "min_balanced_accuracy": min_balanced_accuracy,
        "min_frequency": float(min_frequency),
        "min_iterations": int(min_iterations),
        "convergence_window": int(convergence_window),
        "convergence_jaccard": float(convergence_jaccard),
        "convergence_max_size_delta": float(convergence_max_size_delta),
        "jaccard": float(jaccard),
        "relative_size_delta": float(size_delta),
        "stable_panel_size_current": int(curr_size),
        "stable_panel_size_previous": int(prev_size),
        "k_qualifying_runs_current": int(k),
        "k_qualifying_runs_previous": int(k - int(convergence_window)),
    }


def _load_gene_panel_for_run(
    run_dir: Path,
    *,
    gene_recurrence_source: str = "enricher",
    prefer_classifier_gene_panels: bool = False,
    prefer_mapper_gene_panels: bool = False,
) -> Optional[pd.DataFrame]:
    source = str(gene_recurrence_source or "enricher").strip().lower()
    if prefer_mapper_gene_panels or source == "mapper":
        df = load_mapper_genes(run_dir)
        if df is not None:
            return df
    if prefer_classifier_gene_panels or source == "classifier":
        df = load_classifier_genes(run_dir)
        if df is not None:
            return df
        return load_enricher_genes(run_dir)
    df = load_enricher_genes(run_dir)
    if df is not None:
        return df
    return load_mapper_genes(run_dir)


def _compute_gene_stability_from_run_dirs(
    run_dirs: Sequence[Path],
    min_frequency: float = 0.5,
    *,
    min_balanced_accuracy: Optional[float] = None,
    prefer_classifier_gene_panels: bool = False,
    prefer_mapper_gene_panels: bool = False,
    gene_recurrence_source: str = "enricher",
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Count gene appearance frequency across an explicit list of MC run dirs."""
    runs = list(run_dirs)
    gene_counts: Counter = Counter()
    gene_importance_sum: Dict[str, float] = defaultdict(float)
    gene_importance_runs: Dict[str, int] = defaultdict(int)
    run_count = 0
    skipped_no_genes = 0
    skipped_low_ba = 0

    for run_dir in runs:
        df = _load_gene_panel_for_run(
            run_dir,
            gene_recurrence_source=gene_recurrence_source,
            prefer_classifier_gene_panels=prefer_classifier_gene_panels,
            prefer_mapper_gene_panels=prefer_mapper_gene_panels,
        )
        if df is None or "gene_name" not in df.columns:
            skipped_no_genes += 1
            continue
        if min_balanced_accuracy is not None:
            ba = run_balanced_accuracy(run_dir)
            if ba is None or ba < float(min_balanced_accuracy):
                skipped_low_ba += 1
                continue
        run_count += 1
        seen_genes: set[str] = set()
        for _, row in df.iterrows():
            gene = str(row.get("gene_name") or "").strip()
            if not gene:
                continue
            seen_genes.add(gene)
        for gene in seen_genes:
            gene_counts[gene] += 1
        for gene in seen_genes:
            gene_rows = df[df["gene_name"].astype(str).str.strip() == gene]
            if gene_rows.empty:
                continue
            if "gene_importance" in gene_rows.columns:
                imp = pd.to_numeric(gene_rows["gene_importance"], errors="coerce")
                if imp.notna().any():
                    gene_importance_sum[gene] += float(imp.mean())
                    gene_importance_runs[gene] += 1

    if run_count == 0:
        return pd.DataFrame(), {
            "n_runs_analyzed": 0,
            "total_unique_genes": 0,
            "stable_genes_at_threshold": 0,
            "min_frequency": min_frequency,
            "skipped_no_gene_panel": skipped_no_genes,
            "skipped_low_balanced_accuracy": skipped_low_ba,
            "prefer_classifier_gene_panels": bool(prefer_classifier_gene_panels),
            "prefer_mapper_gene_panels": bool(prefer_mapper_gene_panels),
            "gene_recurrence_source": gene_recurrence_source,
        }

    data = []
    for gene, count in gene_counts.items():
        freq = count / run_count
        row: Dict[str, Any] = {
            "gene_name": gene,
            "frequency": freq,
            "count": count,
            "n_runs": run_count,
        }
        if gene_importance_runs.get(gene, 0) > 0:
            row["gene_importance"] = gene_importance_sum[gene] / gene_importance_runs[gene]
        data.append(row)

    df = pd.DataFrame(data).sort_values("frequency", ascending=False)

    stable = df[df["frequency"] >= min_frequency] if not df.empty else pd.DataFrame()
    summary = {
        "n_runs_analyzed": run_count,
        "total_unique_genes": len(gene_counts),
        "stable_genes_at_threshold": len(stable),
        "min_frequency": min_frequency,
        "skipped_no_gene_panel": skipped_no_genes,
        "skipped_low_balanced_accuracy": skipped_low_ba,
        "prefer_classifier_gene_panels": bool(prefer_classifier_gene_panels),
        "prefer_mapper_gene_panels": bool(prefer_mapper_gene_panels),
        "gene_recurrence_source": gene_recurrence_source,
    }

    return df, summary


def compute_gene_stability(
    monte_carlo_runs_root: Path,
    min_frequency: float = 0.5,
    *,
    min_balanced_accuracy: Optional[float] = None,
    prefer_classifier_gene_panels: bool = False,
    prefer_mapper_gene_panels: bool = False,
    gene_recurrence_source: str = "enricher",
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Count gene appearance frequency across MC runs."""
    runs = _list_monte_carlo_run_dirs(monte_carlo_runs_root)
    return _compute_gene_stability_from_run_dirs(
        run_dirs=runs,
        min_frequency=min_frequency,
        min_balanced_accuracy=min_balanced_accuracy,
        prefer_classifier_gene_panels=prefer_classifier_gene_panels,
        prefer_mapper_gene_panels=prefer_mapper_gene_panels,
        gene_recurrence_source=gene_recurrence_source,
    )


def _stable_gene_key_set(gene_freq_df: pd.DataFrame, min_frequency: float) -> set[str]:
    if gene_freq_df.empty or "frequency" not in gene_freq_df.columns:
        return set()
    selected = gene_freq_df[gene_freq_df["frequency"] >= float(min_frequency)]
    keys: set[str] = set()
    for _, row in selected.iterrows():
        gene = str(row.get("gene_name") or "").strip()
        if gene:
            keys.add(gene)
    return keys


def evaluate_gene_stability_convergence(
    monte_carlo_runs_root: Path,
    *,
    min_frequency: float = 0.5,
    min_balanced_accuracy: Optional[float] = None,
    prefer_classifier_gene_panels: bool = False,
    prefer_mapper_gene_panels: bool = False,
    gene_recurrence_source: str = "enricher",
    min_iterations: int = 20,
    convergence_window: int = 5,
    convergence_jaccard: float = 0.98,
    convergence_max_size_delta: float = 0.02,
) -> Dict[str, Any]:
    """
    Gene-axis analogue of ``evaluate_dmp_stability_convergence``.

    Compares the stable GENE panel at k qualifying runs vs k-window qualifying
    runs. Use this when the production model feature axis is ``raw_gene`` so the
    early-stop criterion tracks the genes the model is actually built from rather
    than the underlying DMP loci. Returns convergence diagnostics for one
    checkpoint; caller handles patience.
    """
    all_runs = _list_monte_carlo_run_dirs(monte_carlo_runs_root)
    qualifying_runs: List[Path] = []
    skipped_no_genes = 0
    skipped_low_ba = 0
    for run_dir in all_runs:
        df = _load_gene_panel_for_run(
            run_dir,
            gene_recurrence_source=gene_recurrence_source,
            prefer_classifier_gene_panels=prefer_classifier_gene_panels,
            prefer_mapper_gene_panels=prefer_mapper_gene_panels,
        )
        if df is None or "gene_name" not in df.columns:
            skipped_no_genes += 1
            continue
        if min_balanced_accuracy is not None:
            ba = run_balanced_accuracy(run_dir)
            if ba is None or ba < float(min_balanced_accuracy):
                skipped_low_ba += 1
                continue
        qualifying_runs.append(run_dir)

    k = len(qualifying_runs)
    eligible = (k >= int(min_iterations)) and (k > int(convergence_window))
    if not eligible:
        return {
            "axis": "gene",
            "eligible_for_check": False,
            "converged_checkpoint": False,
            "n_attempted_runs": len(all_runs),
            "n_runs_analyzed": k,
            "skipped_no_gene_panel": skipped_no_genes,
            "skipped_low_balanced_accuracy": skipped_low_ba,
            "min_balanced_accuracy": min_balanced_accuracy,
            "min_frequency": float(min_frequency),
            "min_iterations": int(min_iterations),
            "convergence_window": int(convergence_window),
            "convergence_jaccard": float(convergence_jaccard),
            "convergence_max_size_delta": float(convergence_max_size_delta),
            "reason": "insufficient_qualifying_runs",
        }

    current_df, _ = _compute_gene_stability_from_run_dirs(
        run_dirs=qualifying_runs,
        min_frequency=min_frequency,
        min_balanced_accuracy=None,
        prefer_classifier_gene_panels=prefer_classifier_gene_panels,
        prefer_mapper_gene_panels=prefer_mapper_gene_panels,
        gene_recurrence_source=gene_recurrence_source,
    )
    previous_df, _ = _compute_gene_stability_from_run_dirs(
        run_dirs=qualifying_runs[: k - int(convergence_window)],
        min_frequency=min_frequency,
        min_balanced_accuracy=None,
        prefer_classifier_gene_panels=prefer_classifier_gene_panels,
        prefer_mapper_gene_panels=prefer_mapper_gene_panels,
        gene_recurrence_source=gene_recurrence_source,
    )
    stable_current = _stable_gene_key_set(current_df, min_frequency=min_frequency)
    stable_previous = _stable_gene_key_set(previous_df, min_frequency=min_frequency)
    jaccard = _jaccard_similarity(stable_current, stable_previous)
    prev_size = len(stable_previous)
    curr_size = len(stable_current)
    size_delta = float(abs(curr_size - prev_size) / max(1, prev_size))
    converged_checkpoint = (
        (jaccard >= float(convergence_jaccard))
        and (size_delta <= float(convergence_max_size_delta))
    )
    return {
        "axis": "gene",
        "eligible_for_check": True,
        "converged_checkpoint": bool(converged_checkpoint),
        "n_attempted_runs": len(all_runs),
        "n_runs_analyzed": k,
        "skipped_no_gene_panel": skipped_no_genes,
        "skipped_low_balanced_accuracy": skipped_low_ba,
        "min_balanced_accuracy": min_balanced_accuracy,
        "min_frequency": float(min_frequency),
        "min_iterations": int(min_iterations),
        "convergence_window": int(convergence_window),
        "convergence_jaccard": float(convergence_jaccard),
        "convergence_max_size_delta": float(convergence_max_size_delta),
        "jaccard": float(jaccard),
        "relative_size_delta": float(size_delta),
        "stable_panel_size_current": int(curr_size),
        "stable_panel_size_previous": int(prev_size),
        "k_qualifying_runs_current": int(k),
        "k_qualifying_runs_previous": int(k - int(convergence_window)),
    }


def write_stable_gene_panel(
    gene_freq_df: pd.DataFrame,
    output_dir: Path,
    min_frequency: float = 0.5,
    top_n: Optional[int] = None,
) -> Path:
    """Write stable genes as a production gene panel."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if gene_freq_df.empty:
        stable = pd.DataFrame(
            columns=["gene_name", "frequency", "count", "n_runs", "gene_importance"]
        )
    else:
        stable = gene_freq_df[gene_freq_df["frequency"] >= float(min_frequency)].copy()
        stable = stable.sort_values(
            ["frequency", "gene_importance", "gene_name"],
            ascending=[False, False, True],
            na_position="last",
        )
        if top_n is not None and int(top_n) > 0:
            stable = stable.head(int(top_n))
    out_path = output_dir / "stable_genes_production.csv"
    stable.to_csv(out_path, index=False)
    return out_path


def write_stable_panel(
    dmp_freq_df: pd.DataFrame,
    output_dir: Path,
    min_frequency: float = 0.7,
    top_n: Optional[int] = None,
) -> Path:
    """Write stable DMPs as a production classifier panel."""
    output_dir.mkdir(parents=True, exist_ok=True)
    stable = _select_stable_dmps_df(dmp_freq_df, min_frequency=min_frequency, top_n=top_n)
    for col in ("effect_size", "p_value", "q_value"):
        if col not in stable.columns:
            stable[col] = pd.Series(dtype="float64")

    out_path = output_dir / "stable_dmps_production.csv"
    stable.to_csv(out_path, index=False)
    return out_path


def write_stable_dual_panels(
    dmp_freq_df: pd.DataFrame,
    output_dir: Path,
    min_frequency: float = 0.7,
    top_n: Optional[int] = None,
    relaxed_cutoff_mode: str = "elbow_log_score",
    relaxed_multiplier: float = 0.5,
    score_eps: float = 1e-12,
) -> Dict[str, Any]:
    """Write strict and relaxed stability panels plus scoring diagnostics."""
    output_dir.mkdir(parents=True, exist_ok=True)
    strict, relaxed, scored, diagnostics = _select_dual_cutoff_dmps(
        dmp_freq_df=dmp_freq_df,
        min_frequency=min_frequency,
        top_n=top_n,
        relaxed_cutoff_mode=relaxed_cutoff_mode,
        relaxed_multiplier=relaxed_multiplier,
        score_eps=score_eps,
    )

    strict_path = output_dir / "stable_dmps_strict.csv"
    relaxed_path = output_dir / "stable_dmps_relaxed.csv"
    production_path = output_dir / "stable_dmps_production.csv"
    diagnostics_json_path = output_dir / "stable_dmps_score_diagnostics.json"
    diagnostics_csv_path = output_dir / "stable_dmps_score_diagnostics.csv"
    ranking_path = output_dir / "stable_dmps_scored.csv"

    strict.to_csv(strict_path, index=False)
    relaxed.to_csv(relaxed_path, index=False)
    # Backward-compatible production panel: strict by default.
    strict.to_csv(production_path, index=False)
    scored.to_csv(ranking_path, index=False)
    with open(diagnostics_json_path, "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, default=str)
    pd.DataFrame([diagnostics]).to_csv(diagnostics_csv_path, index=False)

    return {
        "strict_path": strict_path,
        "relaxed_path": relaxed_path,
        "production_path": production_path,
        "scored_path": ranking_path,
        "diagnostics_json_path": diagnostics_json_path,
        "diagnostics_csv_path": diagnostics_csv_path,
        "strict_df": strict,
        "relaxed_df": relaxed,
        "diagnostics": diagnostics,
    }


def _write_tiered_stable_panels(
    dmp_freq_df: pd.DataFrame,
    output_dir: Path,
    top_n: Optional[int],
    relaxed_cutoff_mode: str,
    relaxed_multiplier: float,
    score_eps: float,
    tier_thresholds: Dict[str, float],
    default_tier: str,
) -> Dict[str, Any]:
    """Write tiered dual-panel outputs and alias root production panel."""
    tiers: Dict[str, Dict[str, Any]] = {}
    for tier_name, threshold in tier_thresholds.items():
        tier_out = output_dir / f"tier_{tier_name}"
        artifact = write_stable_dual_panels(
            dmp_freq_df=dmp_freq_df,
            output_dir=tier_out,
            min_frequency=float(threshold),
            top_n=top_n,
            relaxed_cutoff_mode=relaxed_cutoff_mode,
            relaxed_multiplier=relaxed_multiplier,
            score_eps=score_eps,
        )
        diag = artifact.get("diagnostics") or {}
        tiers[tier_name] = {
            "name": tier_name,
            "min_frequency": float(threshold),
            "output_dir": str(tier_out),
            "stable_dmp_csv": str(artifact["production_path"]),
            "stable_dmp_csv_strict": str(artifact["strict_path"]),
            "stable_dmp_csv_relaxed": str(artifact["relaxed_path"]),
            "stable_dmp_scored_csv": str(artifact["scored_path"]),
            "stable_dmp_score_diagnostics_json": str(artifact["diagnostics_json_path"]),
            "stable_dmp_score_diagnostics_csv": str(artifact["diagnostics_csv_path"]),
            "stable_dmp_score_diagnostics": diag,
            "n_candidates_after_min_frequency": int(diag.get("n_candidates_after_min_frequency", 0)),
            "n_strict_selected": int(diag.get("n_strict_selected", 0)),
            "n_relaxed_selected": int(diag.get("n_relaxed_selected", 0)),
        }

    selected_tier = default_tier if default_tier in tiers else "extended"
    if selected_tier not in tiers:
        selected_tier = next(iter(tiers.keys()))
    selected = tiers[selected_tier]

    root_production_path = output_dir / "stable_dmps_production.csv"
    shutil.copy2(Path(selected["stable_dmp_csv"]), root_production_path)
    selected["root_production_alias"] = str(root_production_path)

    return {
        "tiers": tiers,
        "default_tier": selected_tier,
        "root_production_path": root_production_path,
    }


def run_stability_analysis(
    monte_carlo_runs_root: Path,
    output_dir: Optional[Path] = None,
    dmp_min_freq: float = 0.7,
    gene_min_freq: float = 0.5,
    top_n_dmps: Optional[int] = None,
    min_balanced_accuracy: Optional[float] = None,
    prefer_classifier_panel_dmps: bool = False,
    prefer_classifier_gene_panels: bool = False,
    prefer_mapper_gene_panels: bool = False,
    gene_recurrence_source: str = "enricher",
    dual_cutoff_enabled: bool = False,
    relaxed_cutoff_mode: str = "elbow_log_score",
    relaxed_multiplier: float = 0.5,
    score_eps: float = 1e-12,
    tiered_stability_enabled: bool = False,
    tier_core_frequency: float = 0.85,
    tier_extended_frequency: float = 0.80,
    tier_exploratory_frequency: float = 0.70,
    default_freeze_tier: str = "extended",
    convergence_diagnostics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Main entry point for stability analysis."""
    if output_dir is None:
        output_dir = monte_carlo_runs_root / "stability"

    # Prefer boolean panel flags over a stale default gene_recurrence_source="enricher".
    if prefer_mapper_gene_panels and str(gene_recurrence_source).strip().lower() == "enricher":
        gene_recurrence_source = "mapper"
    elif prefer_classifier_gene_panels and str(gene_recurrence_source).strip().lower() == "enricher":
        gene_recurrence_source = "classifier"

    dmp_df, dmp_summary = compute_dmp_stability(
        monte_carlo_runs_root,
        dmp_min_freq,
        min_balanced_accuracy=min_balanced_accuracy,
        prefer_classifier_panel_dmps=prefer_classifier_panel_dmps,
    )
    gene_df, gene_summary = compute_gene_stability(
        monte_carlo_runs_root,
        gene_min_freq,
        min_balanced_accuracy=min_balanced_accuracy,
        prefer_classifier_gene_panels=prefer_classifier_gene_panels,
        prefer_mapper_gene_panels=prefer_mapper_gene_panels,
        gene_recurrence_source=gene_recurrence_source,
    )
    detector_param_summary = compute_detector_parameter_stability(monte_carlo_runs_root)

    stable_dmp_path = None
    stable_gene_path = None
    selected_dmp_df = pd.DataFrame()
    strict_dmp_path = None
    relaxed_dmp_path = None
    scored_dmp_path = None
    score_diagnostics_json_path = None
    score_diagnostics_csv_path = None
    score_diagnostics = None
    tier_outputs: Dict[str, Any] = {}
    selected_tier_name: Optional[str] = None
    dmp_frequency_plot_path = None
    dmp_frequency_plot_by_chrom = {}
    dmp_frequency_counts_by_chrom = {}
    root_strict_path = output_dir / "stable_dmps_strict.csv"
    root_relaxed_path = output_dir / "stable_dmps_relaxed.csv"
    root_scored_path = output_dir / "stable_dmps_scored.csv"
    root_diag_json_path = output_dir / "stable_dmp_score_diagnostics.json"
    root_diag_csv_path = output_dir / "stable_dmp_score_diagnostics.csv"
    root_dmps_diag_json_path = output_dir / "stable_dmps_score_diagnostics.json"
    root_dmps_diag_csv_path = output_dir / "stable_dmps_score_diagnostics.csv"
    if tiered_stability_enabled:
        # Tiered mode writes strict/relaxed/scored artifacts under tier_* directories.
        # Remove root dual-cutoff files to avoid stale-table confusion.
        for stale in (
            root_strict_path,
            root_relaxed_path,
            root_scored_path,
            root_diag_json_path,
            root_diag_csv_path,
            root_dmps_diag_json_path,
            root_dmps_diag_csv_path,
        ):
            try:
                if stale.exists():
                    stale.unlink()
            except OSError:
                pass
        tier_thresholds = {
            "core": float(tier_core_frequency),
            "extended": float(tier_extended_frequency),
            "exploratory": float(tier_exploratory_frequency),
        }
        tier_payload = _write_tiered_stable_panels(
            dmp_freq_df=dmp_df,
            output_dir=output_dir,
            top_n=top_n_dmps,
            relaxed_cutoff_mode=relaxed_cutoff_mode,
            relaxed_multiplier=relaxed_multiplier,
            score_eps=score_eps,
            tier_thresholds=tier_thresholds,
            default_tier=str(default_freeze_tier).strip().lower(),
        )
        tier_outputs = tier_payload["tiers"]
        selected_tier_name = tier_payload["default_tier"]
        stable_dmp_path = tier_payload["root_production_path"]

        selected_tier = tier_outputs[selected_tier_name]
        strict_dmp_path = Path(selected_tier["stable_dmp_csv_strict"])
        relaxed_dmp_path = Path(selected_tier["stable_dmp_csv_relaxed"])
        scored_dmp_path = Path(selected_tier["stable_dmp_scored_csv"])
        score_diagnostics_json_path = Path(selected_tier["stable_dmp_score_diagnostics_json"])
        score_diagnostics_csv_path = Path(selected_tier["stable_dmp_score_diagnostics_csv"])
        score_diagnostics = selected_tier["stable_dmp_score_diagnostics"]
        selected_dmp_df = pd.read_csv(strict_dmp_path) if strict_dmp_path.exists() else pd.DataFrame()
    elif dual_cutoff_enabled:
        dual_paths = write_stable_dual_panels(
            dmp_df,
            output_dir,
            min_frequency=dmp_min_freq,
            top_n=top_n_dmps,
            relaxed_cutoff_mode=relaxed_cutoff_mode,
            relaxed_multiplier=relaxed_multiplier,
            score_eps=score_eps,
        )
        selected_dmp_df = dual_paths["strict_df"]
        stable_dmp_path = dual_paths["production_path"]
        strict_dmp_path = dual_paths["strict_path"]
        relaxed_dmp_path = dual_paths["relaxed_path"]
        scored_dmp_path = dual_paths["scored_path"]
        score_diagnostics_json_path = dual_paths["diagnostics_json_path"]
        score_diagnostics_csv_path = dual_paths["diagnostics_csv_path"]
        score_diagnostics = dual_paths["diagnostics"]
    else:
        # Legacy single-cutoff mode: materialize strict/relaxed aliases so callers
        # never read stale dual-cutoff files from previous runs.
        selected_dmp_df = _select_stable_dmps_df(
            dmp_df, min_frequency=dmp_min_freq, top_n=top_n_dmps
        )
        # Always materialize the stable panel path so --freeze has a deterministic input artifact,
        # even when no DMP passes thresholds (empty CSV with canonical headers).
        stable_dmp_path = write_stable_panel(dmp_df, output_dir, dmp_min_freq, top_n_dmps)
        selected_dmp_df.to_csv(root_strict_path, index=False)
        selected_dmp_df.to_csv(root_relaxed_path, index=False)
        strict_dmp_path = root_strict_path
        relaxed_dmp_path = root_relaxed_path
        for stale in (
            root_scored_path,
            root_diag_json_path,
            root_diag_csv_path,
            root_dmps_diag_json_path,
            root_dmps_diag_csv_path,
        ):
            try:
                if stale.exists():
                    stale.unlink()
            except OSError:
                pass
    if not dmp_df.empty:
        (
            dmp_frequency_plot_path,
            dmp_frequency_plot_by_chrom,
            dmp_frequency_counts_by_chrom,
        ) = write_dmp_frequency_plot_by_chromosome(
            dmp_df, selected_dmp_df, output_dir
        )

    if prefer_classifier_gene_panels or prefer_mapper_gene_panels or not gene_df.empty:
        stable_gene_path = write_stable_gene_panel(
            gene_df,
            output_dir,
            min_frequency=gene_min_freq,
        )

    from .biomarker_gene_pool import compute_biomarker_stability_diagnostics

    dmp_axis = "classifier" if prefer_classifier_panel_dmps else "discovery"
    if float(dmp_min_freq) <= 0.0:
        # Frequency filter off: panel is a locus pool for gene mapping, not DMP stability.
        dmp_axis = "discovery_locus_pool" if not prefer_classifier_panel_dmps else "classifier_locus_pool"
    if dmp_min_freq <= 0.0 and dmp_df.empty:
        dmp_axis = "none"
    if prefer_mapper_gene_panels or gene_recurrence_source == "mapper":
        gene_axis = "mapper"
    elif prefer_classifier_gene_panels or gene_recurrence_source == "classifier":
        gene_axis = "classifier"
    else:
        gene_axis = "enricher"
    if gene_min_freq <= 0.0 and gene_df.empty:
        gene_axis = "none"

    summary = {
        "pipeline_axes": {
            "dmp_axis": dmp_axis,
            "gene_axis": gene_axis,
            "prefer_classifier_panel_dmps": bool(prefer_classifier_panel_dmps),
            "prefer_classifier_gene_panels": bool(prefer_classifier_gene_panels),
            "prefer_mapper_gene_panels": bool(prefer_mapper_gene_panels),
            "gene_recurrence_source": gene_recurrence_source,
        },
        "dmp_stability": dmp_summary,
        "gene_stability": gene_summary,
        "detector_parameters": detector_param_summary,
        "stable_dmp_csv": str(stable_dmp_path) if stable_dmp_path else None,
        "stable_gene_csv": str(stable_gene_path) if stable_gene_path else None,
        "stable_dmp_csv_strict": str(strict_dmp_path) if strict_dmp_path else None,
        "stable_dmp_csv_relaxed": str(relaxed_dmp_path) if relaxed_dmp_path else None,
        "stable_dmp_scored_csv": str(scored_dmp_path) if scored_dmp_path else None,
        "stable_dmp_score_diagnostics_json": (
            str(score_diagnostics_json_path) if score_diagnostics_json_path else None
        ),
        "stable_dmp_score_diagnostics_csv": (
            str(score_diagnostics_csv_path) if score_diagnostics_csv_path else None
        ),
        "stable_dmp_score_diagnostics": score_diagnostics,
        "dual_cutoff_enabled": bool(dual_cutoff_enabled or tiered_stability_enabled),
        "tiered_stability_enabled": bool(tiered_stability_enabled),
        "stability_default_freeze_tier": selected_tier_name,
        "stability_tiers": tier_outputs,
        "dmp_frequency_plot_html": str(dmp_frequency_plot_path) if dmp_frequency_plot_path else None,
        "dmp_frequency_charts_by_chromosome": dmp_frequency_plot_by_chrom,
        "dmp_frequency_counts_by_chromosome": dmp_frequency_counts_by_chrom,
        "early_stopping": convergence_diagnostics or {},
        "output_dir": str(output_dir),
        "biomarker_filter": compute_biomarker_stability_diagnostics(monte_carlo_runs_root),
    }

    summary_path = output_dir / "stability_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    if not dmp_df.empty:
        dmp_df.to_csv(output_dir / "dmp_frequency.csv", index=False)
    if not gene_df.empty:
        gene_df.to_csv(output_dir / "gene_frequency.csv", index=False)

    return summary


def _merge_stable_dmp_panels(
    stable_dmp_csv: str | Path,
    output_dir: Path,
) -> Path:
    """Merge one or more per-chromosome (or single genome-wide) stable DMP CSVs into a unified panel.

    The fixed_dmp_panel only requires 'chromosome' and 'position' columns (others are preserved).
    Deduplicates by (chromosome, position) keeping the highest-frequency entry if available.
    """
    stable_path = Path(stable_dmp_csv)
    if not stable_path.exists():
        raise FileNotFoundError(f"Stable DMP path not found: {stable_path}")

    merged_path = output_dir / "stable_dmps_genomewide.csv"
    output_dir.mkdir(parents=True, exist_ok=True)

    if stable_path.is_dir():
        # Support per-chromosome stable files (e.g. in stability/ dir or custom)
        csv_files = sorted(stable_path.glob("**/*stable*.csv")) or sorted(
            stable_path.glob("**/*.csv")
        )
        if not csv_files:
            csv_files = [stable_path / "stable_dmps_production.csv"]
        frames: List[pd.DataFrame] = []
        for csv_f in csv_files:
            if csv_f.exists() and csv_f.suffix.lower() == ".csv":
                try:
                    df = pd.read_csv(csv_f)
                    if {"chromosome", "position"}.issubset(df.columns):
                        frames.append(df)
                except Exception as e:
                    logger.warning(f"Skipping unreadable CSV {csv_f}: {e}")
        if frames:
            merged_df = pd.concat(frames, ignore_index=True)
            if "effect_size" not in merged_df.columns:
                merged_df["effect_size"] = pd.Series(dtype="float64")
            # Deduplicate, preferring higher frequency if column present
            if "frequency" in merged_df.columns:
                sort_cols = ["frequency"]
                sort_asc = [False]
                if "effect_size" in merged_df.columns:
                    sort_cols.append("effect_size")
                    sort_asc.append(False)
                merged_df = merged_df.sort_values(
                    sort_cols,
                    ascending=sort_asc,
                    na_position="last",
                )
            merged_df = merged_df.drop_duplicates(
                subset=["chromosome", "position"], keep="first"
            ).reset_index(drop=True)
            merged_df = _validate_stability_recurrence_metadata(
                merged_df,
                source_label=str(stable_path),
            )
            merged_df.to_csv(merged_path, index=False)
            logger.info(
                f"Merged {len(frames)} stable DMP CSVs → {len(merged_df):,} unique DMPs "
                f"(genome-wide panel at {merged_path})"
            )
            return merged_path
        else:
            # Fallback to single file in dir
            fallback = stable_path / "stable_dmps_production.csv"
            if fallback.exists():
                stable_path = fallback
            else:
                raise ValueError(f"No valid stable DMP CSVs found in directory: {stable_path}")

    # Single CSV case (default from write_stable_panel)
    if stable_path.suffix.lower() == ".csv":
        stable_df = pd.read_csv(stable_path)
        stable_df = _validate_stability_recurrence_metadata(
            stable_df,
            source_label=str(stable_path),
        )
        stable_df.to_csv(merged_path, index=False)
        logger.info(f"Copied stable DMP panel to {merged_path}")
    else:
        merged_path = stable_path

    return merged_path


def _normalize_production_ecdf_backend(
    project_dict: Dict[str, Any],
    *,
    config: Optional["MonteCarloConfig"] = None,
    stable_gene_csv: Optional[str | Path] = None,
) -> None:
    """
    Patch production validation ECDF params so --model uses a safe default axis.

    Honors explicit raw_gene in the source project; when gene stability is enabled
    or a stable gene panel is present, defaults to raw_gene. Otherwise raw_dmp.
    Always disables auto aggregated observed-hybrid unless explicitly enabled.
    """
    # Production freeze projects must remain loadable by ProjectConfig (no step_config).
    action_cfg = project_dict.setdefault("actionConfig", {})
    validation_cfg = action_cfg.setdefault("validation", {})
    backend_profiles = validation_cfg.setdefault("backend_profiles", {})
    ecdf_profile = backend_profiles.setdefault("ecdf", {})
    params = ecdf_profile.setdefault("params", {})

    requested_mode = str(params.get("feature_mode") or "raw_dmp").strip().lower()
    use_raw_gene = requested_mode == "raw_gene"
    if not use_raw_gene:
        if config is not None and bool(getattr(config, "stability_gene_featurecuts_enabled", False)):
            use_raw_gene = True
        elif stable_gene_csv is not None and Path(stable_gene_csv).is_file():
            use_raw_gene = True
        else:
            model_bundle_cfg = action_cfg.get("model_bundle") or {}
            stability_gene_panel = model_bundle_cfg.get("stability_gene_panel")
            if stability_gene_panel and Path(str(stability_gene_panel)).is_file():
                use_raw_gene = True

    if use_raw_gene:
        params["feature_mode"] = "raw_gene"
        params["feature_family_set"] = "gene_scored"
    else:
        params["feature_mode"] = "raw_dmp"
        params["feature_family_set"] = "dmp_scored"
    params["model_weight_column"] = "effect_size"
    if params.get("ecdf_aggregated_enabled") is not True:
        params["ecdf_aggregated_enabled"] = False


def prepare_freeze_project(
    base_project: Path,
    stable_dmp_csv: str,
    monte_carlo_runs_root: Path,
    production_output_dir: Optional[str] = None,
    config: Optional["MonteCarloConfig"] = None,
) -> Dict[str, Any]:
    """
    Write ``production/project.json`` with ``fixed_dmp_panel`` for granular freeze workflows.

    Does not run centroid/detector/mapper/enricher — those are separate workflow ACTION nodes.
    """
    if production_output_dir is None:
        production_output_dir = str(monte_carlo_runs_root / "production")
    prod_dir = Path(production_output_dir)
    prod_dir.mkdir(parents=True, exist_ok=True)

    merged_panel = _merge_stable_dmp_panels(stable_dmp_csv, prod_dir)

    with open(base_project, encoding="utf-8") as f:
        project_dict = json.load(f)

    pr = config.path_remap if config is not None else None
    pr_dict = dict(pr) if pr else None
    if pr_dict is not None:
        from .path_remap import (
            apply_path_remap_to_nested,
            remap_cohort_list_files_in_project,
            remap_path_string,
        )

        apply_path_remap_to_nested(project_dict, pr_dict)
    if config is not None and config.samples_base_path:
        sbp = str(config.samples_base_path).rstrip("/")
        if pr_dict is not None:
            sbp = remap_path_string(sbp, pr_dict)
        project_dict["samples_base_path"] = sbp
    if pr_dict is not None:
        remap_cohort_list_files_in_project(project_dict, pr_dict, prod_dir)

    # Never write step_config: ProjectConfig rejects it. Bake freeze knobs under actionConfig.
    project_dict.pop("step_config", None)
    action_cfg = project_dict.setdefault("actionConfig", {})
    detection_cfg = action_cfg.setdefault("detection", {})
    detection_cfg["fixed_dmp_panel"] = str(merged_panel)
    project_dict["project_name"] = "production"
    if prod_dir.parent.name == "monte_carlo_runs":
        project_dict["output_base"] = str(prod_dir.parent)
    else:
        project_dict["output_base"] = str(prod_dir)

    prod_project_path = prod_dir / "project.json"
    stable_gene_csv: Optional[Path] = None
    if config is not None and getattr(config, "freeze_stable_gene_csv", None):
        stable_gene_csv = Path(str(config.freeze_stable_gene_csv))
    if stable_gene_csv is None or not stable_gene_csv.is_file():
        default_gene_csv = monte_carlo_runs_root / "stability" / "stable_genes_production.csv"
        if default_gene_csv.is_file():
            stable_gene_csv = default_gene_csv

    bundle_dir = prod_dir / "model_bundle"
    stability_gene_panel_path: Optional[Path] = None
    if stable_gene_csv is not None and stable_gene_csv.is_file():
        bundle_dir.mkdir(parents=True, exist_ok=True)
        stability_gene_panel_path = bundle_dir / "stable_genes_from_stability.csv"
        shutil.copy2(stable_gene_csv, stability_gene_panel_path)
        model_bundle_cfg = action_cfg.setdefault("model_bundle", {})
        model_bundle_cfg["stability_gene_panel"] = str(stability_gene_panel_path)

    _normalize_production_ecdf_backend(
        project_dict,
        config=config,
        stable_gene_csv=stability_gene_panel_path or stable_gene_csv,
    )

    # Held-out batch: exclude locked_test from production cohorts + emit eval sidecars.
    # DomainProgram freeze uses this path (not freeze_production_model).
    if config is not None:
        from .holdout_eval import apply_config_holdout_to_project

        holdout_info = apply_config_holdout_to_project(
            project_dict,
            config,
            prod_dir,
            monte_carlo_runs_root=monte_carlo_runs_root,
            project_json=prod_project_path,
        )
        if holdout_info is not None:
            logger.info(
                "Held-out batch (%s): excluded %d sample(s) from production training cohorts.",
                holdout_info["partition"],
                len(holdout_info["excluded"]),
            )

    with open(prod_project_path, "w", encoding="utf-8") as f:
        json.dump(project_dict, f, indent=2)
    # Sidecar for operators / tooling that read freeze knobs without loading ProjectConfig.
    (prod_dir / "freeze_action_config.json").write_text(
        json.dumps(action_cfg, indent=2) + "\n",
        encoding="utf-8",
    )

    # Re-emit eval artifacts after project.json exists (covers CAAS symlink parent later).
    if config is not None and (prod_dir / "holdout_manifest.json").is_file():
        from .holdout_eval import write_holdout_eval_artifacts

        hm = json.loads((prod_dir / "holdout_manifest.json").read_text(encoding="utf-8"))
        partitions = getattr(config, "validation_partitions", None)
        partition = str(hm.get("partition") or getattr(config, "holdout_partition", "locked_test"))
        holdout_paths = (
            list(getattr(partitions, partition, []) or []) if partitions is not None else []
        )
        if holdout_paths and hm.get("holdout_class_map"):
            write_holdout_eval_artifacts(
                production_dir=prod_dir,
                holdout_paths=holdout_paths,
                class_map=dict(hm.get("holdout_class_map") or {}),
                samples_base_path=str(project_dict.get("samples_base_path") or ""),
                project_json=prod_project_path,
                monte_carlo_runs_root=monte_carlo_runs_root,
            )

    return {
        "status": "ok",
        "productionProject": str(prod_project_path.resolve()),
        "fixedDmpPanel": str(merged_panel),
        "outputDir": str(prod_dir.resolve()),
    }


def resolve_production_ecdf_feature_settings(
    production_project: Path,
    config: Optional["MonteCarloConfig"] = None,
) -> tuple[str, str]:
    """
    Resolve ``(feature_mode, feature_family_set)`` for freeze finalize.

    Prefer production ``actionConfig.validation.backend_profiles.ecdf.params``
    (written by ``prepare_freeze_project`` via ``_normalize_production_ecdf_backend``),
    then fall back to MonteCarloConfig attributes.
    """
    feature_mode = ""
    family = ""
    prod_path = Path(production_project)
    if prod_path.is_file():
        try:
            payload = json.loads(prod_path.read_text(encoding="utf-8"))
            ecdf_params = (
                ((payload.get("actionConfig") or {}).get("validation") or {})
                .get("backend_profiles", {})
                .get("ecdf", {})
                .get("params", {})
                or {}
            )
            feature_mode = str(ecdf_params.get("feature_mode") or "").strip().lower()
            family = str(ecdf_params.get("feature_family_set") or "").strip().lower()
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            feature_mode = ""
            family = ""
    if not feature_mode and config is not None:
        feature_mode = str(getattr(config, "feature_mode", "") or "").strip().lower()
    if not family and config is not None:
        family = str(getattr(config, "feature_family_set", "") or "").strip().lower()
    return feature_mode, family


def production_requires_mapper_annotations(feature_mode: str, family: str) -> bool:
    """True when gene/non-DMP ECDF mode needs mapper_dmp_annotations.csv."""
    mode = str(feature_mode or "").strip().lower()
    fam = str(family or "").strip().lower()
    return mode == "raw_gene" or fam not in ("", "dmp_scored")


def production_requires_frozen_genes(feature_mode: str, family: str) -> bool:
    """True when ECDF mode requires a non-empty frozen gene panel."""
    mode = str(feature_mode or "").strip().lower()
    fam = str(family or "").strip().lower()
    return mode == "raw_gene" or fam in ("gene", "gene_scored", "observed_hybrid")


def finalize_production_model_bundle(
    *,
    production_project: Path,
    config: Optional["MonteCarloConfig"] = None,
    stability_gene_panel_path: Optional[Path] = None,
    require_mapper: bool = True,
) -> Dict[str, Any]:
    """
    After DomainProgram freeze mapper (or legacy --freeze pipeline), build gene-mode
    artifacts under ``production/model_bundle`` and wire pointers into project.json.

    DomainProgram ``validation_freeze`` runs prepare → centroid → detect → mapper →
    enricher but historically skipped this step (legacy ``freeze_production_model``
    did it). Without it, gene ECDF model-MC fails looking for mapper_dmp_annotations.
    """
    from .model_bundle import (
        FROZEN_GENE_FEATURES_NAME,
        FROZEN_GENE_PANEL_NAME,
        MAPPER_ANNOTATION_NAME,
        build_frozen_gene_panel,
        build_mapper_annotation_cache,
    )

    prod_project_path = Path(production_project)
    if not prod_project_path.is_file():
        raise FileNotFoundError(f"production project not found: {prod_project_path}")
    prod_dir = prod_project_path.parent
    bundle_dir = prod_dir / "model_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    feature_mode, family = resolve_production_ecdf_feature_settings(
        prod_project_path, config=config
    )
    # Honor production project gene-mode even if the caller passed require_mapper=False.
    effective_require_mapper = bool(require_mapper) or production_requires_mapper_annotations(
        feature_mode, family
    )
    needs_genes = production_requires_frozen_genes(feature_mode, family)

    if stability_gene_panel_path is None:
        candidate = bundle_dir / "stable_genes_from_stability.csv"
        if candidate.is_file():
            stability_gene_panel_path = candidate
        else:
            default_gene_csv = prod_dir.parent / "stability" / "stable_genes_production.csv"
            if default_gene_csv.is_file():
                stability_gene_panel_path = default_gene_csv

    mapper_annotation_cache = build_mapper_annotation_cache(
        project_json=prod_project_path,
        output_csv=bundle_dir / MAPPER_ANNOTATION_NAME,
    )
    n_mapper_rows = int(mapper_annotation_cache.get("rows") or 0)
    if effective_require_mapper and n_mapper_rows <= 0:
        raise RuntimeError(
            "finalize_production_model_bundle: mapper annotation cache is empty. "
            "Freeze mapper must run before gene ECDF (expected production/mapper/...)."
        )

    frozen_gene_panel = build_frozen_gene_panel(
        project_json=prod_project_path,
        output_dir=bundle_dir,
        min_dmps_per_feature=max(
            1,
            int(
                getattr(config, "freeze_min_dmps_per_feature", 1)
                if config is not None
                else 1
            ),
        ),
        gene_importance_min=(
            float(getattr(config, "freeze_gene_importance_min"))
            if (config is not None and getattr(config, "freeze_gene_importance_min", None) is not None)
            else None
        ),
        top_genes=(
            int(getattr(config, "freeze_top_genes"))
            if (config is not None and getattr(config, "freeze_top_genes", None) is not None)
            else None
        ),
        stability_gene_panel_path=(
            str(stability_gene_panel_path)
            if stability_gene_panel_path is not None and Path(stability_gene_panel_path).is_file()
            else None
        ),
    )

    gene_panel_path = Path(
        str(frozen_gene_panel.get("gene_panel_path") or (bundle_dir / FROZEN_GENE_PANEL_NAME))
    )
    if not gene_panel_path.is_absolute():
        gene_panel_path = bundle_dir / gene_panel_path.name
    gene_feat_path = Path(
        str(frozen_gene_panel.get("gene_features_path") or (bundle_dir / FROZEN_GENE_FEATURES_NAME))
    )
    if not gene_feat_path.is_absolute():
        gene_feat_path = bundle_dir / gene_feat_path.name

    n_genes = int(frozen_gene_panel.get("n_genes") or frozen_gene_panel.get("genes_rows") or 0)
    if n_genes <= 0 and gene_panel_path.is_file():
        try:
            gene_df = pd.read_csv(gene_panel_path)
            if not gene_df.empty and "gene_name" in gene_df.columns:
                n_genes = int(gene_df["gene_name"].dropna().astype(str).str.strip().ne("").sum())
        except Exception:
            n_genes = 0
    if needs_genes and n_genes <= 0:
        # Fall back to stability panel row count when freeze builder omitted n_genes.
        if stability_gene_panel_path is not None and Path(stability_gene_panel_path).is_file():
            try:
                stab_df = pd.read_csv(stability_gene_panel_path)
                if not stab_df.empty and "gene_name" in stab_df.columns:
                    n_genes = int(stab_df["gene_name"].dropna().astype(str).str.strip().ne("").sum())
            except Exception:
                pass
    if needs_genes and n_genes <= 0:
        raise RuntimeError(
            "finalize_production_model_bundle: no frozen genes for gene ECDF. "
            "mc_stability must produce stable_genes_production.csv via runGeneFeaturecuts "
            f"(checked {stability_gene_panel_path})."
        )

    with open(prod_project_path, encoding="utf-8") as f:
        prod_project_payload = json.load(f)
    prod_project_payload.pop("step_config", None)
    action_cfg = prod_project_payload.setdefault("actionConfig", {})
    model_bundle_cfg = action_cfg.setdefault("model_bundle", {})
    # Prefer configured mount paths (/work/...) over OS realpath (/lambda/nfs/...).
    mapper_path = Path(str(mapper_annotation_cache.get("path") or (bundle_dir / MAPPER_ANNOTATION_NAME)))
    if not mapper_path.is_absolute():
        mapper_path = bundle_dir / mapper_path.name

    model_bundle_cfg["mapper_annotation_csv"] = str(mapper_path)
    model_bundle_cfg["fixed_gene_panel"] = str(gene_panel_path)
    model_bundle_cfg["fixed_gene_features"] = str(gene_feat_path)
    if stability_gene_panel_path is not None and Path(stability_gene_panel_path).is_file():
        model_bundle_cfg["stability_gene_panel"] = str(Path(stability_gene_panel_path))
    with open(prod_project_path, "w", encoding="utf-8") as f:
        json.dump(prod_project_payload, f, indent=2)

    logger.info(
        "Finalized production model_bundle: mapper_rows=%s n_genes=%s path=%s "
        "(feature_mode=%s family=%s require_mapper=%s)",
        n_mapper_rows,
        n_genes,
        bundle_dir,
        feature_mode,
        family,
        effective_require_mapper,
    )
    return {
        "status": "ok",
        "productionProject": str(prod_project_path),
        "modelBundleDir": str(bundle_dir),
        "mapper_annotation_cache": mapper_annotation_cache,
        "frozen_gene_panel": frozen_gene_panel,
        "n_mapper_rows": n_mapper_rows,
        "n_genes": n_genes,
        "feature_mode": feature_mode,
        "feature_family_set": family,
    }


def freeze_production_model(
    base_project: Path,
    stable_dmp_csv: str,
    monte_carlo_runs_root: Path,
    production_output_dir: Optional[str] = None,
    skip_centroid: bool = False,
    skip_detection: bool = False,
    config: Optional["MonteCarloConfig"] = None,
) -> Dict[str, Any]:
    """
    Merge per-chromosome stable DMPs (if multiple files/dir provided) into one
    genome-wide panel, generate production project.json with fixed_dmp_panel,
    and run the full production pipeline: centroid → detector(fixed panel)
    → mapper → enricher (no classifier or predictor).
    """
    if production_output_dir is None:
        production_output_dir = str(monte_carlo_runs_root / "production")
    prod_dir = Path(production_output_dir)
    prod_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Production freeze output dir: {prod_dir}")

    # Merge stable DMPs (handles single CSV from stability or per-chrom CSVs)
    merged_panel = _merge_stable_dmp_panels(stable_dmp_csv, prod_dir)

    logger.info(f"Using fixed DMP panel: {merged_panel} for production run")

    # Load base project JSON and modify for production
    with open(base_project, encoding="utf-8") as f:
        project_dict = json.load(f)

    pr = config.path_remap if config is not None else None
    pr_dict = dict(pr) if pr else None
    if pr_dict is not None:
        from .path_remap import (
            apply_path_remap_to_nested,
            remap_cohort_list_files_in_project,
            remap_path_string,
        )

        apply_path_remap_to_nested(project_dict, pr_dict)
    if config is not None and config.samples_base_path:
        # MonteCarloConfig.samples_base_path is copied from the template project at CLI load time.
        # Without remapping here it would overwrite apply_path_remap_to_nested's samples_base_path
        # with a stale OLD-prefix value.
        sbp = str(config.samples_base_path).rstrip("/")
        if pr_dict is not None:
            sbp = remap_path_string(sbp, pr_dict)
        project_dict["samples_base_path"] = sbp
    if pr_dict is not None:
        remap_cohort_list_files_in_project(project_dict, pr_dict, prod_dir)

    # Bake freeze knobs under actionConfig (ProjectConfig rejects step_config).
    project_dict.pop("step_config", None)
    action_cfg = project_dict.setdefault("actionConfig", {})
    detection_cfg = action_cfg.setdefault("detection", {})
    # Preserve configured path style (e.g., /work mount aliases) instead of
    # canonicalizing through OS realpath resolution, which can rewrite to
    # environment-specific NFS prefixes (e.g., /lambda/nfs/...).
    detection_cfg["fixed_dmp_panel"] = str(merged_panel)

    # Production run uses full dataset (no MC train/val split), unique project name
    project_dict["project_name"] = "production"
    # Set output_base so outputs land under .../monte_carlo_runs/production/...
    if prod_dir.parent.name == "monte_carlo_runs":
        project_dict["output_base"] = str(prod_dir.parent)
    else:
        project_dict["output_base"] = str(prod_dir)

    prod_project_path = prod_dir / "project.json"

    stable_gene_csv: Optional[Path] = None
    if config is not None and getattr(config, "freeze_stable_gene_csv", None):
        stable_gene_csv = Path(str(config.freeze_stable_gene_csv))
    if stable_gene_csv is None or not stable_gene_csv.is_file():
        default_gene_csv = monte_carlo_runs_root / "stability" / "stable_genes_production.csv"
        if default_gene_csv.is_file():
            stable_gene_csv = default_gene_csv

    bundle_dir = prod_dir / "model_bundle"
    stability_gene_panel_path: Optional[Path] = None
    if stable_gene_csv is not None and stable_gene_csv.is_file():
        bundle_dir.mkdir(parents=True, exist_ok=True)
        stability_gene_panel_path = bundle_dir / "stable_genes_from_stability.csv"
        shutil.copy2(stable_gene_csv, stability_gene_panel_path)
        model_bundle_cfg = action_cfg.setdefault("model_bundle", {})
        model_bundle_cfg["stability_gene_panel"] = str(stability_gene_panel_path)

    _normalize_production_ecdf_backend(
        project_dict,
        config=config,
        stable_gene_csv=stability_gene_panel_path or stable_gene_csv,
    )

    # True held-out batch support: keep hold-out samples out of the production training
    # cohorts so the frozen model is genuinely disjoint from the evaluation batch.
    if config is not None:
        from .holdout_eval import apply_config_holdout_to_project

        holdout_info = apply_config_holdout_to_project(
            project_dict,
            config,
            prod_dir,
            monte_carlo_runs_root=monte_carlo_runs_root,
            project_json=prod_project_path,
        )
        if holdout_info is not None:
            logger.info(
                "Held-out batch (%s): excluded %d sample(s) from production training cohorts.",
                holdout_info["partition"],
                len(holdout_info["excluded"]),
            )

    with open(prod_project_path, "w", encoding="utf-8") as f:
        json.dump(project_dict, f, indent=2)

    # Run production pipeline (centroid on full data + fixed-panel detector etc.)
    from .pipeline_runner import run_pipeline_for_production
    success, errors, timings = run_pipeline_for_production(
        prod_project_path,
        logs_dir=prod_dir / "logs",
        skip_centroid=bool(skip_centroid or skip_detection),
        skip_detection=bool(skip_detection),
        config=config,
    )
    mapper_annotation_cache: Dict[str, Any] = {}
    frozen_gene_panel: Dict[str, Any] = {}
    if success:
        try:
            feature_mode, family = resolve_production_ecdf_feature_settings(
                prod_project_path, config=config
            )
            require_mapper = production_requires_mapper_annotations(feature_mode, family)
            finalized = finalize_production_model_bundle(
                production_project=prod_project_path,
                config=config,
                stability_gene_panel_path=stability_gene_panel_path,
                require_mapper=require_mapper,
            )
            mapper_annotation_cache = dict(finalized.get("mapper_annotation_cache") or {})
            frozen_gene_panel = dict(finalized.get("frozen_gene_panel") or {})
        except Exception as e:
            success = False
            errors = list(errors) + [f"Mapper annotation cache build failed: {e}"]
            mapper_annotation_cache = {}
            frozen_gene_panel = {}

    summary = {
        "output_dir": str(prod_dir),
        "fixed_dmp_panel": str(merged_panel),
        "stable_gene_panel": (
            str(stability_gene_panel_path) if stability_gene_panel_path is not None else None
        ),
        "production_project": str(prod_project_path),
        "mapper_annotation_cache": mapper_annotation_cache,
        "frozen_gene_panel": frozen_gene_panel,
        "success": success,
        "errors": errors,
        "timings": timings,
    }
    summary_path = prod_dir / "production_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    try:
        from .locked_model_spec import write_locked_model_spec

        write_locked_model_spec(
            production_dir=prod_dir,
            source_event="freeze",
            config=config,
            extra={"production_summary_path": str(summary_path)},
        )
    except Exception as e:
        logger.warning("Could not write locked_model_spec after freeze: %s", e)
    try:
        from .regulatory_artifacts import write_pccp_draft, write_post_market_monitoring_scaffold

        write_pccp_draft(
            production_dir=prod_dir,
            config=config,
            source_event="freeze",
        )
        write_post_market_monitoring_scaffold(
            production_dir=prod_dir,
            config=config,
            source_event="freeze",
        )
    except Exception as e:
        logger.warning("Could not write regulatory scaffold artifacts after freeze: %s", e)

    logger.info(f"Production freeze complete: {summary_path}")
    return summary


def build_production_model(
    monte_carlo_runs_root: Path,
    production_output_dir: Optional[str] = None,
    config: Optional["MonteCarloConfig"] = None,
) -> Dict[str, Any]:
    """
    Run the model building and evaluation steps on the production project:
    classifier → predictor.
    Assumes --freeze has already generated the production project.json.
    """
    if production_output_dir is None:
        production_output_dir = str(monte_carlo_runs_root / "production")
    prod_dir = Path(production_output_dir)
    prod_project_path = prod_dir / "project.json"

    if not prod_project_path.exists():
        raise FileNotFoundError(f"Production project not found: {prod_project_path}. Run --freeze first.")

    logger.info(f"Production model output dir: {prod_dir}")

    # DomainProgram freeze may not have run legacy --freeze mapper-cache prep; ensure gene-mode artifacts.
    try:
        feature_mode = (
            str(getattr(config, "feature_mode", "") or "").strip().lower()
            if config is not None
            else ""
        )
        family = (
            str(getattr(config, "feature_family_set", "") or "").strip().lower()
            if config is not None
            else ""
        )
        needs_mapper_cache = feature_mode == "raw_gene" or family not in ("", "dmp_scored")
        ann_path = prod_dir / "model_bundle" / "mapper_dmp_annotations.csv"
        if needs_mapper_cache and not ann_path.is_file():
            from .model_bundle import MAPPER_ANNOTATION_NAME, build_mapper_annotation_cache

            bundle_dir = prod_dir / "model_bundle"
            bundle_dir.mkdir(parents=True, exist_ok=True)
            cache = build_mapper_annotation_cache(
                project_json=prod_project_path,
                output_csv=bundle_dir / MAPPER_ANNOTATION_NAME,
            )
            with open(prod_project_path, encoding="utf-8") as f:
                payload = json.load(f)
            payload.pop("step_config", None)
            mb = payload.setdefault("actionConfig", {}).setdefault("model_bundle", {})
            mb["mapper_annotation_csv"] = str(cache.get("path") or (bundle_dir / MAPPER_ANNOTATION_NAME))
            with open(prod_project_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info("Built missing mapper annotation cache for production model: %s", mb["mapper_annotation_csv"])
    except Exception as exc:
        logger.warning("Could not ensure mapper annotation cache before model build: %s", exc)

    from .pipeline_runner import run_pipeline_for_model

    predictor_out = prod_dir / "predictors"
    predictor_out.mkdir(parents=True, exist_ok=True)
    success, errors, timings = run_pipeline_for_model(
        prod_project_path,
        logs_dir=prod_dir / "logs",
        predictor_output_dir=predictor_out,
        config=config,
    )

    summary = {
        "output_dir": str(prod_dir),
        "production_project": str(prod_project_path),
        "model_backend": config.model_backend if config is not None else "ecdf",
        "success": success,
        "errors": errors,
        "timings": timings,
    }
    summary_path = prod_dir / "model_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    logger.info(f"Production model build complete: {summary_path}")
    return summary

