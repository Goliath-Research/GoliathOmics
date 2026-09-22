"""
Comparison-level gene directional scores for feature_family_set=gene_scored.

Uses frozen mapper gene panels (gene_support_n, gene_importance) and per-comparison DMP
effect sizes with sample methylation at panel loci.

Region-directional helpers below are reserved for a future structural_scored family
(frozen-panel-restricted); they are not emitted under gene_scored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from methyl_utils.action_config_resolver import resolve_for_project

def _normalize_feature_key(value: object) -> str:
    text = str(value or "").strip()
    return text if text else "unknown"


def _is_known_mapped_token(value: object) -> bool:
    token = str(value or "").strip().lower()
    return token not in {"", "unknown", "nan", "none"}


def _feature_label_token(label: object) -> str:
    token = str(label or "").strip().lower().replace("-", "_").replace(" ", "_")
    cleaned = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in token)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("_")
    return cleaned or "cancer"


GENE_SCORED_FEATURE_PREFIX = "gene_directional_score__"
GENE_PANEL_OBS_FRACTION_PREFIX = "gene_panel_obs_fraction__"
GENE_DIRECTIONAL_IQR_PREFIX = "gene_directional_iqr__"
GENE_WEIGHTED_SIGN_AGREEMENT_PREFIX = "gene_weighted_sign_agreement__"
GENE_DIRECTIONAL_CONTRAST_PREFIX = "gene_directional_contrast__"
GENE_DIRECTIONAL_ADJACENT_PREFIX = "gene_directional_adjacent_delta__"
GENE_DIRECTIONAL_PROGRESSION_SLOPE = "gene_directional_progression_slope"
GENE_DIRECTIONAL_RANGE = "gene_directional_range"
GENE_MAX_WEIGHTED_DIRECTIONAL_SCORE = "gene_max_weighted_directional_score"
GENE_WEIGHTED_CENTROID_CONTRAST_SCORE = "gene_weighted_centroid_contrast_score"
GENE_WEIGHTED_DIRECTIONAL_AGREEMENT_PREFIX = "gene_weighted_directional_agreement__"
GENE_WEIGHTED_COSINE_SIM_TO_CANCER_PREFIX = "gene_weighted_cosine_similarity_to_cancer_centroid__"
GENE_WEIGHTED_COSINE_DIST_TO_CENTROID_PREFIX = "gene_weighted_cosine_distance_to_centroid__"
REGION_DIRECTIONAL_FEATURE_PREFIX = "region_directional_score__"
GENE_SCORED_SCHEMA_VERSION = "gene_scored_v6_centroid_analogs"
DEFAULT_REGION_DIRECTIONAL_TYPES: Tuple[str, ...] = (
    "promoter",
    "exon",
    "intron",
    "gene_body",
    "terminator",
)
_ALLOWED_REGION_DIRECTIONAL_TYPES = frozenset(
    {"promoter", "exon", "intron", "gene_body", "terminator"}
)


def gene_scored_feature_column(comparison_label: object) -> str:
    return f"{GENE_SCORED_FEATURE_PREFIX}{_feature_label_token(comparison_label)}"


def gene_panel_obs_fraction_column(comparison_label: object) -> str:
    return f"{GENE_PANEL_OBS_FRACTION_PREFIX}{_feature_label_token(comparison_label)}"


def gene_directional_iqr_column(comparison_label: object) -> str:
    return f"{GENE_DIRECTIONAL_IQR_PREFIX}{_feature_label_token(comparison_label)}"


def gene_weighted_sign_agreement_column(comparison_label: object) -> str:
    return f"{GENE_WEIGHTED_SIGN_AGREEMENT_PREFIX}{_feature_label_token(comparison_label)}"


def gene_weighted_directional_agreement_column(cancer_label: object) -> str:
    return f"{GENE_WEIGHTED_DIRECTIONAL_AGREEMENT_PREFIX}{_feature_label_token(cancer_label)}"


def gene_weighted_cosine_similarity_to_cancer_centroid_column(cancer_label: object) -> str:
    return f"{GENE_WEIGHTED_COSINE_SIM_TO_CANCER_PREFIX}{_feature_label_token(cancer_label)}"


def gene_weighted_cosine_distance_to_centroid_column(class_label: object) -> str:
    return f"{GENE_WEIGHTED_COSINE_DIST_TO_CENTROID_PREFIX}{_feature_label_token(class_label)}"


def gene_directional_contrast_column(left_label: object, right_label: object) -> str:
    return (
        f"{GENE_DIRECTIONAL_CONTRAST_PREFIX}"
        f"{_feature_label_token(left_label)}__{_feature_label_token(right_label)}"
    )


def gene_directional_adjacent_delta_column(left_label: object, right_label: object) -> str:
    return (
        f"{GENE_DIRECTIONAL_ADJACENT_PREFIX}"
        f"{_feature_label_token(left_label)}__{_feature_label_token(right_label)}"
    )


def normalize_gene_scored_contrast_pairs(
    contrast_pairs: Optional[Sequence[Sequence[str]]],
) -> List[Tuple[str, str]]:
    if not contrast_pairs:
        return []
    out: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for pair in contrast_pairs:
        if pair is None or len(pair) != 2:
            raise ValueError("Each gene_scored_contrast_pairs entry must be [left, right] with two labels.")
        left = str(pair[0]).strip()
        right = str(pair[1]).strip()
        if not left or not right:
            raise ValueError("gene_scored_contrast_pairs labels must be non-empty strings.")
        key = (left, right)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def resolve_gene_scored_progression_order(
    available_labels: Sequence[str],
    *,
    project_json: Optional[str | Path] = None,
    explicit_order: Optional[Sequence[str]] = None,
) -> List[str]:
    available_set = {str(x).strip() for x in available_labels if str(x).strip()}
    if not available_set:
        return []

    order_tokens: List[str] = []
    if explicit_order:
        order_tokens = [str(x).strip() for x in explicit_order if str(x).strip()]
    elif project_json is not None:
        project_path = Path(project_json)
        if project_path.exists():
            from methyl_utils import load_project

            project = load_project(project_path)
            progression_cfg = resolve_for_project("progression", project)
            cfg_order = progression_cfg.get("ordered_comparison_labels") or progression_cfg.get(
                "ordered_disease_groups"
            )
            if isinstance(cfg_order, list) and cfg_order:
                order_tokens = [str(x).strip() for x in cfg_order if str(x).strip()]
            else:
                get_ordered = getattr(project, "get_ordered_comparison_labels", None)
                if callable(get_ordered):
                    order_tokens = [str(x).strip() for x in get_ordered() if str(x).strip()]

    filtered: List[str] = []
    seen: set[str] = set()
    for token in order_tokens:
        if token in available_set and token not in seen:
            filtered.append(token)
            seen.add(token)
    for token in sorted(available_set):
        if token not in seen:
            filtered.append(token)
            seen.add(token)
    return filtered


def gene_scored_progression_feature_names(
    ordered_labels: Sequence[str],
    *,
    contrast_pairs: Optional[Sequence[Sequence[str]]] = None,
) -> List[str]:
    labels = [str(x) for x in ordered_labels]
    k = len(labels)
    if k < 2:
        return []

    names: List[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            names.append(name)

    first, last = labels[0], labels[-1]
    _add(gene_directional_contrast_column(first, last))
    _add(GENE_DIRECTIONAL_PROGRESSION_SLOPE)

    if k >= 3:
        _add(GENE_DIRECTIONAL_RANGE)
        for i in range(k - 1):
            _add(gene_directional_adjacent_delta_column(labels[i], labels[i + 1]))

    for left, right in normalize_gene_scored_contrast_pairs(contrast_pairs):
        _add(gene_directional_contrast_column(left, right))

    return names


def _pair_delta_column(
    matrix: np.ndarray,
    j_left: int,
    j_right: int,
) -> np.ndarray:
    n_samples = int(matrix.shape[0])
    out = np.full((n_samples,), np.nan, dtype=np.float64)
    for i in range(n_samples):
        left_v = float(matrix[i, j_left])
        right_v = float(matrix[i, j_right])
        if np.isfinite(left_v) and np.isfinite(right_v):
            out[i] = right_v - left_v
    return out


def _progression_slope_column(matrix: np.ndarray) -> np.ndarray:
    n_samples, k = matrix.shape
    out = np.full((n_samples,), np.nan, dtype=np.float64)
    if k < 2:
        return out
    x_all = np.arange(k, dtype=np.float64)
    for i in range(n_samples):
        row = np.asarray(matrix[i, :], dtype=np.float64)
        mask = np.isfinite(row)
        if int(mask.sum()) < 2:
            continue
        x = x_all[mask]
        y = row[mask]
        x_mean = float(np.mean(x))
        y_mean = float(np.mean(y))
        denom = float(np.sum((x - x_mean) ** 2))
        if denom <= 0.0:
            continue
        slope = float(np.sum((x - x_mean) * (y - y_mean)) / denom)
        out[i] = slope
    return out


def _progression_range_column(matrix: np.ndarray) -> np.ndarray:
    n_samples, k = matrix.shape
    out = np.full((n_samples,), np.nan, dtype=np.float64)
    if k < 2:
        return out
    for i in range(n_samples):
        row = np.asarray(matrix[i, :], dtype=np.float64)
        if not np.all(np.isfinite(row)):
            continue
        out[i] = float(np.max(row) - np.min(row))
    return out


def compute_gene_scored_progression_features(
    directional_matrix: np.ndarray,
    ordered_labels: Sequence[str],
    *,
    contrast_pairs: Optional[Sequence[Sequence[str]]] = None,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    labels = [str(x) for x in ordered_labels]
    feature_names = gene_scored_progression_feature_names(labels, contrast_pairs=contrast_pairs)
    n_samples = int(directional_matrix.shape[0]) if directional_matrix.ndim == 2 else 0
    features: Dict[str, np.ndarray] = {
        name: np.full((n_samples,), np.nan, dtype=np.float64) for name in feature_names
    }
    if n_samples == 0 or len(labels) < 2:
        return features, feature_names

    label_to_idx = {label: idx for idx, label in enumerate(labels)}
    k = len(labels)

    if gene_directional_contrast_column(labels[0], labels[-1]) in features:
        features[gene_directional_contrast_column(labels[0], labels[-1])] = _pair_delta_column(
            directional_matrix, 0, k - 1
        )
    if GENE_DIRECTIONAL_PROGRESSION_SLOPE in features:
        features[GENE_DIRECTIONAL_PROGRESSION_SLOPE] = _progression_slope_column(directional_matrix)
    if GENE_DIRECTIONAL_RANGE in features:
        features[GENE_DIRECTIONAL_RANGE] = _progression_range_column(directional_matrix)
    if k >= 3:
        for i in range(k - 1):
            col = gene_directional_adjacent_delta_column(labels[i], labels[i + 1])
            if col in features:
                features[col] = _pair_delta_column(directional_matrix, i, i + 1)

    for left, right in normalize_gene_scored_contrast_pairs(contrast_pairs):
        col = gene_directional_contrast_column(left, right)
        if col not in features:
            continue
        if col == gene_directional_contrast_column(labels[0], labels[-1]):
            continue
        j_left = label_to_idx.get(left)
        j_right = label_to_idx.get(right)
        if j_left is not None and j_right is not None:
            features[col] = _pair_delta_column(directional_matrix, j_left, j_right)

    return features, feature_names


def family_includes_gene_scored(feature_family_set: Optional[str]) -> bool:
    from .observed_feature_builder import normalize_feature_family_set

    token = normalize_feature_family_set(feature_family_set)
    return token in {"gene_scored", "dmp_scored+gene_scored"}


def resolve_gene_scored_comparison_labels(
    dmp_df: pd.DataFrame,
    frozen_gene_panel_df: pd.DataFrame,
) -> List[str]:
    dmp_labels: set[str] = set()
    if dmp_df is not None and not dmp_df.empty and "comparison_label" in dmp_df.columns:
        dmp_labels = {
            str(x).strip()
            for x in dmp_df["comparison_label"].astype(str).tolist()
            if str(x).strip()
        }
    panel_labels: set[str] = set()
    if frozen_gene_panel_df is not None and not frozen_gene_panel_df.empty:
        if "comparison_label" in frozen_gene_panel_df.columns:
            panel_labels = {
                str(x).strip()
                for x in frozen_gene_panel_df["comparison_label"].astype(str).tolist()
                if str(x).strip()
            }
    if dmp_labels and panel_labels:
        labels = sorted(dmp_labels & panel_labels)
    elif dmp_labels:
        labels = sorted(dmp_labels)
    elif panel_labels:
        labels = sorted(panel_labels)
    else:
        labels = []
    return labels


def validate_gene_scored_contrast_pairs_against_labels(
    contrast_pairs: Optional[Sequence[Sequence[str]]],
    available_labels: Sequence[str],
) -> None:
    available = {str(x).strip() for x in available_labels if str(x).strip()}
    for left, right in normalize_gene_scored_contrast_pairs(contrast_pairs):
        if left not in available:
            raise ValueError(
                f"gene_scored_contrast_pairs label {left!r} is not in available comparisons: "
                f"{sorted(available)}"
            )
        if right not in available:
            raise ValueError(
                f"gene_scored_contrast_pairs label {right!r} is not in available comparisons: "
                f"{sorted(available)}"
            )


def resolve_gene_scored_labels_for_features(
    dmp_df: pd.DataFrame,
    frozen_gene_panel_df: pd.DataFrame,
    *,
    project_json: Optional[str | Path] = None,
    explicit_order: Optional[Sequence[str]] = None,
) -> Tuple[List[str], List[str]]:
    available = resolve_gene_scored_comparison_labels(dmp_df, frozen_gene_panel_df)
    progression_order = resolve_gene_scored_progression_order(
        available,
        project_json=project_json,
        explicit_order=explicit_order,
    )
    return available, progression_order


def gene_scored_feature_names(
    comparison_labels: Sequence[str],
    *,
    contrast_pairs: Optional[Sequence[Sequence[str]]] = None,
    cancer_class_labels: Optional[Sequence[str]] = None,
    all_class_labels: Optional[Sequence[str]] = None,
) -> List[str]:
    names: List[str] = []
    for label in comparison_labels:
        names.append(gene_scored_feature_column(label))
        names.append(gene_panel_obs_fraction_column(label))
        names.append(gene_directional_iqr_column(label))
        names.append(gene_weighted_sign_agreement_column(label))
    names.extend(
        gene_scored_progression_feature_names(
            comparison_labels,
            contrast_pairs=contrast_pairs,
        )
    )
    names.extend(
        gene_scored_centroid_feature_names(
            cancer_class_labels=cancer_class_labels,
            all_class_labels=all_class_labels,
            comparison_labels=comparison_labels,
        )
    )
    return names


def gene_scored_centroid_feature_names(
    *,
    cancer_class_labels: Optional[Sequence[str]] = None,
    all_class_labels: Optional[Sequence[str]] = None,
    comparison_labels: Optional[Sequence[str]] = None,
) -> List[str]:
    """Gene-level analogs of dmp_scored centroid/directional aggregates."""
    names: List[str] = [
        GENE_MAX_WEIGHTED_DIRECTIONAL_SCORE,
        GENE_WEIGHTED_CENTROID_CONTRAST_SCORE,
    ]
    cancer_labels = [str(x) for x in (cancer_class_labels or []) if str(x).strip()]
    if not cancer_labels:
        cancer_labels = [str(x) for x in (comparison_labels or []) if str(x).strip()]
    if not cancer_labels:
        cancer_labels = ["cancer"]
    for label in cancer_labels:
        names.append(gene_weighted_directional_agreement_column(label))
        names.append(gene_weighted_cosine_similarity_to_cancer_centroid_column(label))
    for label in [str(x) for x in (all_class_labels or []) if str(x).strip()]:
        names.append(gene_weighted_cosine_distance_to_centroid_column(label))
    return names


def _weighted_cosine_similarity_gene(
    values_a: np.ndarray,
    values_b: np.ndarray,
    weights: np.ndarray,
) -> float:
    if values_a.size == 0 or values_b.size == 0 or values_a.size != values_b.size:
        return float("nan")
    if weights.size != values_a.size:
        return float("nan")
    w = np.asarray(weights, dtype=np.float64)
    w = np.abs(np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0))
    if float(np.sum(w)) <= 0.0:
        return float("nan")
    a = np.asarray(values_a, dtype=np.float64)
    b = np.asarray(values_b, dtype=np.float64)
    num = float(np.sum(w * a * b))
    na = float(np.sqrt(np.sum(w * (a**2))))
    nb = float(np.sqrt(np.sum(w * (b**2))))
    if na <= 1e-12 or nb <= 1e-12:
        return float("nan")
    return float(num / (na * nb))


def _weighted_jensen_shannon_distance_gene(
    values_a: np.ndarray,
    values_b: np.ndarray,
    weights: np.ndarray,
    eps: float = 1e-10,
) -> float:
    from scipy.stats import entropy

    if values_a.size == 0 or values_b.size == 0 or values_a.size != values_b.size:
        return float("nan")
    if weights.size != values_a.size:
        return float("nan")
    w = np.asarray(weights, dtype=np.float64)
    w = np.abs(np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0))
    w_sum = float(np.sum(w))
    if w_sum <= 0.0:
        return float("nan")
    w = w / w_sum
    # Directional scores are roughly in [-0.5, 0.5]; map to (0,1) for JS.
    p = np.clip(0.5 + np.asarray(values_a, dtype=np.float64), eps, 1.0 - eps)
    q = np.clip(0.5 + np.asarray(values_b, dtype=np.float64), eps, 1.0 - eps)
    p_w = p * w
    q_w = q * w
    p_sum = float(np.sum(p_w))
    q_sum = float(np.sum(q_w))
    if p_sum <= 0.0 or q_sum <= 0.0:
        return float("nan")
    p_w = p_w / p_sum
    q_w = q_w / q_sum
    m = 0.5 * (p_w + q_w)
    js_div = 0.5 * (entropy(p_w, m, base=2) + entropy(q_w, m, base=2))
    if not np.isfinite(js_div):
        return float("nan")
    return float(np.sqrt(max(float(js_div), 0.0)))


def _panel_gene_vector_from_row(
    sample_row: np.ndarray,
    panel: pd.DataFrame,
    loci_by_gene: Dict[str, List[Tuple[int, float, float]]],
    *,
    gene_weight_mode: str,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (directional_values, weights) aligned to panel row order (NaN if missing)."""
    n = int(len(panel))
    values = np.full(n, np.nan, dtype=np.float64)
    weights = np.zeros(n, dtype=np.float64)
    for idx_g, (_, grow) in enumerate(panel.iterrows()):
        gene = str(grow["gene_name"])
        loci = loci_by_gene.get(gene)
        if not loci:
            continue
        dir_g = _per_gene_directional_value(loci, sample_row)
        if dir_g is None:
            continue
        w_g = _gene_weight(grow, gene_weight_mode=gene_weight_mode)
        if w_g <= 0.0:
            continue
        values[idx_g] = float(dir_g)
        weights[idx_g] = float(w_g)
    return values, weights


def compute_gene_scored_centroid_features(
    X_raw: np.ndarray,
    feature_order: Sequence[Tuple[str, str, int]],
    dmp_df: pd.DataFrame,
    panels: Dict[str, pd.DataFrame],
    comparison_labels: Sequence[str],
    *,
    healthy_reference_vector: np.ndarray,
    cancer_reference_vector: np.ndarray,
    per_cancer_reference_vectors: Optional[Sequence[np.ndarray]] = None,
    cancer_class_labels: Optional[Sequence[str]] = None,
    all_class_labels: Optional[Sequence[str]] = None,
    centroid_refs_by_label: Optional[Dict[str, np.ndarray]] = None,
    use_region_weight: bool = True,
    gene_weight_mode: str = "importance_x_sqrt_support",
    eps: float = 1e-6,
) -> Dict[str, np.ndarray]:
    """
    Gene-level analogs of dmp_scored centroid/directional aggregates.

    Operates in per-gene directional space (same ``dir_g`` as gene_directional_score).
    """
    n_samples = int(X_raw.shape[0])
    out: Dict[str, np.ndarray] = {
        GENE_MAX_WEIGHTED_DIRECTIONAL_SCORE: np.full(n_samples, np.nan, dtype=np.float64),
        GENE_WEIGHTED_CENTROID_CONTRAST_SCORE: np.full(n_samples, np.nan, dtype=np.float64),
    }
    cancer_labels = [str(x) for x in (cancer_class_labels or []) if str(x).strip()]
    if not cancer_labels:
        cancer_labels = [str(x) for x in comparison_labels if str(x).strip()]
    if not cancer_labels:
        cancer_labels = ["cancer"]
    for label in cancer_labels:
        out[gene_weighted_directional_agreement_column(label)] = np.full(
            n_samples, np.nan, dtype=np.float64
        )
        out[gene_weighted_cosine_similarity_to_cancer_centroid_column(label)] = np.full(
            n_samples, np.nan, dtype=np.float64
        )
    class_labels = [str(x) for x in (all_class_labels or []) if str(x).strip()]
    for label in class_labels:
        out[gene_weighted_cosine_distance_to_centroid_column(label)] = np.full(
            n_samples, np.nan, dtype=np.float64
        )

    if n_samples == 0 or not comparison_labels:
        return out
    if dmp_df is None or dmp_df.empty:
        return out

    order_index = build_locus_order_index(feature_order)
    work = _prepare_gene_scored_dmp_work(dmp_df, use_region_weight=use_region_weight)
    if work.empty:
        return out

    healthy_ref = np.asarray(healthy_reference_vector, dtype=np.float64).reshape(-1)
    cancer_ref = np.asarray(cancer_reference_vector, dtype=np.float64).reshape(-1)
    per_cancer_refs: List[np.ndarray] = []
    if per_cancer_reference_vectors is not None:
        for vec in per_cancer_reference_vectors:
            per_cancer_refs.append(np.asarray(vec, dtype=np.float64).reshape(-1))
    if not per_cancer_refs:
        per_cancer_refs = [cancer_ref]
    # Pad/trim cancer refs to cancer_labels length
    while len(per_cancer_refs) < len(cancer_labels):
        per_cancer_refs.append(cancer_ref)
    per_cancer_refs = per_cancer_refs[: len(cancer_labels)]

    centroid_map = dict(centroid_refs_by_label or {})

    # Prefer the first comparison panel that has genes (binary studies have one).
    panel: Optional[pd.DataFrame] = None
    cmp_label_for_loci: Optional[str] = None
    for cmp_label in comparison_labels:
        cand = panels.get(str(cmp_label))
        if cand is not None and not cand.empty:
            panel = cand
            cmp_label_for_loci = str(cmp_label)
            break
    if panel is None or cmp_label_for_loci is None:
        return out

    cmp_dmp = work[work["comparison_label"] == cmp_label_for_loci]
    if cmp_dmp.empty:
        # Fall back: use all DMP rows for locus membership
        cmp_dmp = work
    loci_by_gene = _build_loci_by_gene(cmp_dmp, order_index)
    if not loci_by_gene:
        return out

    n_loci = int(X_raw.shape[1]) if X_raw.ndim == 2 else 0

    def _as_locus_row(vec: np.ndarray) -> np.ndarray:
        row = np.asarray(vec, dtype=np.float64).reshape(-1)
        if row.shape[0] != n_loci:
            # Length mismatch: cannot map; return empty-like zeros marked missing via nan weights
            return np.full(n_loci, np.nan, dtype=np.float64)
        return row

    g_h, w_h = _panel_gene_vector_from_row(
        _as_locus_row(healthy_ref),
        panel,
        loci_by_gene,
        gene_weight_mode=gene_weight_mode,
    )
    g_c_primary, _w_c_primary = _panel_gene_vector_from_row(
        _as_locus_row(cancer_ref),
        panel,
        loci_by_gene,
        gene_weight_mode=gene_weight_mode,
    )
    g_cancer_by_label: Dict[str, np.ndarray] = {}
    for k_idx, label in enumerate(cancer_labels):
        ref_k = per_cancer_refs[k_idx] if k_idx < len(per_cancer_refs) else cancer_ref
        g_k, _ = _panel_gene_vector_from_row(
            _as_locus_row(ref_k),
            panel,
            loci_by_gene,
            gene_weight_mode=gene_weight_mode,
        )
        g_cancer_by_label[label] = g_k

    g_class_by_label: Dict[str, np.ndarray] = {}
    for label in class_labels:
        vec = centroid_map.get(label)
        if vec is None:
            # Fallbacks: control-like -> healthy, else cancer primary / matching cancer label
            token = str(label).strip().lower()
            if any(t in token for t in ("healthy", "control", "normal", "all")):
                vec = healthy_ref
            elif label in g_cancer_by_label:
                # already have gene vector
                g_class_by_label[label] = g_cancer_by_label[label]
                continue
            else:
                vec = cancer_ref
        g_cls, _ = _panel_gene_vector_from_row(
            _as_locus_row(vec),
            panel,
            loci_by_gene,
            gene_weight_mode=gene_weight_mode,
        )
        g_class_by_label[label] = g_cls

    for i in range(n_samples):
        sample_row = np.asarray(X_raw[i, :], dtype=np.float64)
        g_s, w_s = _panel_gene_vector_from_row(
            sample_row,
            panel,
            loci_by_gene,
            gene_weight_mode=gene_weight_mode,
        )
        if g_s.size == 0 or not np.any(np.isfinite(g_s) & (w_s > 0.0)):
            continue

        max_dir = float("nan")
        for label in cancer_labels:
            g_k = g_cancer_by_label.get(label)
            if g_k is None or g_k.size != g_s.size or g_h.size != g_s.size:
                continue
            mask = (
                np.isfinite(g_s)
                & np.isfinite(g_h)
                & np.isfinite(g_k)
                & np.isfinite(w_s)
                & (w_s > 0.0)
            )
            if not np.any(mask):
                continue
            gs, gh, gk, ww = g_s[mask], g_h[mask], g_k[mask], w_s[mask]
            directional = np.clip((2.0 * (gs - gh) / (gk - gh + float(eps))) - 1.0, -1.0, 1.0)
            ww_sum = float(np.sum(ww))
            if ww_sum <= 0.0:
                continue
            fk = float(np.sum(ww * directional) / ww_sum)
            agree = float(np.sum(ww * (directional > 0.0).astype(np.float64)) / ww_sum)
            out[gene_weighted_directional_agreement_column(label)][i] = agree
            # Cosine in gene-directional space
            sim = _weighted_cosine_similarity_gene(gs, gk, ww)
            out[gene_weighted_cosine_similarity_to_cancer_centroid_column(label)][i] = sim
            if not np.isfinite(max_dir) or fk > max_dir:
                max_dir = fk
        out[GENE_MAX_WEIGHTED_DIRECTIONAL_SCORE][i] = max_dir

        # Contrast vs primary healthy/cancer gene vectors
        mask_c = (
            np.isfinite(g_s)
            & np.isfinite(g_h)
            & np.isfinite(g_c_primary)
            & np.isfinite(w_s)
            & (w_s > 0.0)
        )
        if np.any(mask_c):
            gs, gh, gc, ww = g_s[mask_c], g_h[mask_c], g_c_primary[mask_c], w_s[mask_c]
            wcos_h = _weighted_cosine_similarity_gene(gs, gh, ww)
            wcos_c = _weighted_cosine_similarity_gene(gs, gc, ww)
            wjs_h = _weighted_jensen_shannon_distance_gene(gs, gh, ww)
            wjs_c = _weighted_jensen_shannon_distance_gene(gs, gc, ww)
            if all(np.isfinite(v) for v in (wcos_h, wcos_c, wjs_h, wjs_c)):
                out[GENE_WEIGHTED_CENTROID_CONTRAST_SCORE][i] = float(
                    (wcos_c - wcos_h) + (wjs_h - wjs_c)
                )

        for label, g_cls in g_class_by_label.items():
            key = gene_weighted_cosine_distance_to_centroid_column(label)
            if key not in out or g_cls.size != g_s.size:
                continue
            mask = (
                np.isfinite(g_s)
                & np.isfinite(g_cls)
                & np.isfinite(w_s)
                & (w_s > 0.0)
            )
            if not np.any(mask):
                continue
            sim = _weighted_cosine_similarity_gene(g_s[mask], g_cls[mask], w_s[mask])
            out[key][i] = float(1.0 - sim) if np.isfinite(sim) else float("nan")

    return out


def _normalize_structural_feature(value: object) -> str:
    token = _normalize_feature_key(value).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "promoter_region": "promoter",
        "genebody": "gene_body",
        "body": "gene_body",
        "terminator_region": "terminator",
    }
    token = aliases.get(token, token)
    return token if token in _ALLOWED_REGION_DIRECTIONAL_TYPES else "unknown"


def normalize_region_directional_region_types(
    region_types: Optional[Sequence[str]] = None,
) -> Tuple[str, ...]:
    if not region_types:
        return DEFAULT_REGION_DIRECTIONAL_TYPES
    out: List[str] = []
    seen: set[str] = set()
    for raw in region_types:
        token = _normalize_structural_feature(raw)
        if token == "unknown" or token in seen:
            continue
        seen.add(token)
        out.append(token)
    if not out:
        return DEFAULT_REGION_DIRECTIONAL_TYPES
    return tuple(out)


def region_directional_feature_column(
    comparison_label: object,
    region_type: object,
) -> str:
    return (
        f"{REGION_DIRECTIONAL_FEATURE_PREFIX}"
        f"{_feature_label_token(comparison_label)}__{_feature_label_token(region_type)}"
    )


def _prepare_dmp_work_df(
    dmp_df: pd.DataFrame,
    *,
    use_region_weight: bool,
) -> pd.DataFrame:
    work = dmp_df.copy()
    if "comparison_label" not in work.columns:
        return pd.DataFrame()
    work["comparison_label"] = work["comparison_label"].astype(str)
    work["chromosome"] = work["chromosome"].astype(str)
    work["context"] = work["context"].astype(str)
    work["position"] = pd.to_numeric(work["position"], errors="coerce").fillna(-1).astype(int)
    work["effect_size"] = pd.to_numeric(work.get("effect_size"), errors="coerce")
    if "feature_type" in work.columns:
        work["feature_type"] = work["feature_type"].map(_normalize_structural_feature)
    else:
        work["feature_type"] = "unknown"
    if use_region_weight and "region_weight" in work.columns:
        work["_region_w"] = pd.to_numeric(work["region_weight"], errors="coerce").fillna(1.0).clip(lower=0.0)
    else:
        work["_region_w"] = 1.0
    work = work[
        np.isfinite(work["effect_size"])
        & (work["position"] >= 0)
        & work["feature_type"].apply(_is_known_mapped_token)
        & (work["feature_type"] != "unknown")
    ].copy()
    return work


def count_region_directional_panel_loci(
    dmp_df: pd.DataFrame,
    feature_order: Sequence[Tuple[str, str, int]],
    comparison_label: str,
    region_type: str,
) -> int:
    if dmp_df is None or dmp_df.empty:
        return 0
    order_index = build_locus_order_index(feature_order)
    work = _prepare_dmp_work_df(dmp_df, use_region_weight=True)
    if work.empty:
        return 0
    cmp_dmp = work[
        (work["comparison_label"] == str(comparison_label))
        & (work["feature_type"] == str(region_type))
    ]
    if cmp_dmp.empty:
        return 0
    seen_keys: set[Tuple[str, str, int]] = set()
    n_panel = 0
    for _, row in cmp_dmp.iterrows():
        key = (str(row["chromosome"]), str(row["context"]), int(row["position"]))
        if key not in order_index or key in seen_keys:
            continue
        seen_keys.add(key)
        n_panel += 1
    return n_panel


def resolve_region_directional_column_specs(
    dmp_df: pd.DataFrame,
    feature_order: Sequence[Tuple[str, str, int]],
    comparison_labels: Sequence[str],
    region_types: Sequence[str],
    *,
    min_loci: int = 1,
) -> List[Tuple[str, str]]:
    if dmp_df is None or dmp_df.empty:
        return []
    order_index = build_locus_order_index(feature_order)
    work = _prepare_dmp_work_df(dmp_df, use_region_weight=True)
    if work.empty:
        return []
    min_n = int(max(1, min_loci))
    region_set = set(str(r) for r in region_types)
    specs: List[Tuple[str, str]] = []
    for cmp_label in [str(x) for x in comparison_labels]:
        cmp_dmp = work[work["comparison_label"] == cmp_label]
        if cmp_dmp.empty:
            continue
        for region in [str(r) for r in region_types]:
            if region not in region_set:
                continue
            cdf = cmp_dmp[cmp_dmp["feature_type"] == region]
            if cdf.empty:
                continue
            n_panel = 0
            seen_keys: set[Tuple[str, str, int]] = set()
            for _, row in cdf.iterrows():
                key = (str(row["chromosome"]), str(row["context"]), int(row["position"]))
                if key not in order_index or key in seen_keys:
                    continue
                seen_keys.add(key)
                n_panel += 1
            if n_panel >= min_n:
                specs.append((cmp_label, region))
    return specs


def region_directional_feature_names(
    dmp_df: pd.DataFrame,
    feature_order: Sequence[Tuple[str, str, int]],
    comparison_labels: Sequence[str],
    region_types: Optional[Sequence[str]] = None,
    *,
    min_loci: int = 1,
) -> List[str]:
    regions = normalize_region_directional_region_types(region_types)
    specs = resolve_region_directional_column_specs(
        dmp_df,
        feature_order,
        comparison_labels,
        regions,
        min_loci=min_loci,
    )
    return [region_directional_feature_column(cmp_label, region) for cmp_label, region in specs]


def compute_region_directional_score_matrix(
    X_raw: np.ndarray,
    feature_order: Sequence[Tuple[str, str, int]],
    dmp_df: pd.DataFrame,
    comparison_labels: Sequence[str],
    region_types: Optional[Sequence[str]] = None,
    *,
    min_loci: int = 1,
    use_region_weight: bool = True,
) -> Tuple[np.ndarray, List[Tuple[str, str]]]:
    n_samples = int(X_raw.shape[0])
    regions = normalize_region_directional_region_types(region_types)
    specs = resolve_region_directional_column_specs(
        dmp_df,
        feature_order,
        comparison_labels,
        regions,
        min_loci=min_loci,
    )
    out = np.full((n_samples, len(specs)), np.nan, dtype=np.float64)
    if n_samples == 0 or not specs:
        return out, specs

    order_index = build_locus_order_index(feature_order)
    work = _prepare_dmp_work_df(dmp_df, use_region_weight=use_region_weight)
    if work.empty:
        return out, specs

    loci_by_cmp_region: Dict[Tuple[str, str], List[Tuple[int, float, float]]] = {}
    spec_set = set(specs)
    for _, row in work.iterrows():
        cmp_label = str(row["comparison_label"])
        region = str(row["feature_type"])
        key = (cmp_label, region)
        if key not in spec_set:
            continue
        locus_key = (str(row["chromosome"]), str(row["context"]), int(row["position"]))
        col_idx = order_index.get(locus_key)
        if col_idx is None:
            continue
        effect = float(row["effect_size"])
        abs_w = abs(effect) * float(row["_region_w"])
        if abs_w <= 0.0:
            continue
        sign_e = float(np.sign(effect))
        loci_by_cmp_region.setdefault(key, []).append((int(col_idx), abs_w, sign_e))

    for j, spec_key in enumerate(specs):
        loci = loci_by_cmp_region.get(spec_key)
        if not loci:
            continue
        for i in range(n_samples):
            locus_num = 0.0
            locus_den = 0.0
            for col_idx, abs_w, sign_e in loci:
                beta = float(X_raw[i, col_idx])
                if not np.isfinite(beta):
                    continue
                locus_num += sign_e * abs_w * (beta - 0.5)
                locus_den += abs_w
            if locus_den > 0.0:
                out[i, j] = float(locus_num / locus_den)

    return out, specs


def prepare_gene_scored_panels(
    frozen_gene_panel_df: pd.DataFrame,
    *,
    min_support_n: int = 2,
) -> Dict[str, pd.DataFrame]:
    if frozen_gene_panel_df is None or frozen_gene_panel_df.empty:
        return {}
    work = frozen_gene_panel_df.copy()
    if "comparison_label" not in work.columns or "gene_name" not in work.columns:
        return {}
    work["comparison_label"] = work["comparison_label"].astype(str)
    work["gene_name"] = work["gene_name"].map(_normalize_feature_key)
    work["gene_support_n"] = pd.to_numeric(work.get("gene_support_n"), errors="coerce").fillna(0).astype(int)
    work["gene_importance"] = pd.to_numeric(work.get("gene_importance"), errors="coerce").fillna(0.0)
    min_n = int(max(1, min_support_n))
    n_initial = int(len(work))
    max_support = int(work["gene_support_n"].max()) if n_initial > 0 else 0
    work = work[work["gene_support_n"] >= min_n].copy()
    if work.empty:
        if n_initial > 0:
            raise ValueError(
                f"No genes passed gene_scored_min_support_n={min_n} "
                f"(frozen panel had {n_initial} row(s), max gene_support_n={max_support}). "
                "Lower gene_scored_min_support_n or rebuild frozen_genes_production.csv."
            )
        return {}
    work = work[work["gene_name"].apply(_is_known_mapped_token)].copy()
    if work.empty:
        return {}
    work = work[work["gene_importance"] > 0.0].copy()
    if work.empty:
        return {}
    panels: Dict[str, pd.DataFrame] = {}
    for cmp_label, cdf in work.groupby("comparison_label", sort=True):
        panel = cdf.drop_duplicates(subset=["gene_name"], keep="first").copy()
        if not panel.empty:
            panels[str(cmp_label)] = panel.reset_index(drop=True)
    return panels


def build_locus_order_index(
    feature_order: Sequence[Tuple[str, str, int]],
) -> Dict[Tuple[str, str, int], int]:
    return {(str(c), str(ctx), int(pos)): int(i) for i, (c, ctx, pos) in enumerate(feature_order)}


def _gene_weight(
    row: pd.Series,
    *,
    gene_weight_mode: str,
) -> float:
    importance = float(pd.to_numeric(row.get("gene_importance"), errors="coerce") or 0.0)
    support_n = float(pd.to_numeric(row.get("gene_support_n"), errors="coerce") or 0.0)
    mode = str(gene_weight_mode or "importance_x_sqrt_support").strip().lower()
    if mode == "importance_only":
        return importance
    if importance <= 0.0 or support_n <= 0.0:
        return 0.0
    return importance * float(np.sqrt(support_n))


def _gene_prior_sign(row: pd.Series) -> Optional[float]:
    signed = float(pd.to_numeric(row.get("gene_effect_signed_wsum"), errors="coerce") or 0.0)
    if np.isfinite(signed) and signed != 0.0:
        return float(np.sign(signed))
    direction = float(pd.to_numeric(row.get("gene_direction"), errors="coerce") or 0.0)
    if np.isfinite(direction) and direction != 0.0:
        return float(np.sign(direction))
    return None


def _prepare_gene_scored_dmp_work(
    dmp_df: pd.DataFrame,
    *,
    use_region_weight: bool,
) -> pd.DataFrame:
    work = dmp_df.copy()
    if "comparison_label" not in work.columns:
        return pd.DataFrame()
    work["comparison_label"] = work["comparison_label"].astype(str)
    work["gene_name"] = work["gene_name"].map(_normalize_feature_key) if "gene_name" in work.columns else "unknown"
    work["chromosome"] = work["chromosome"].astype(str)
    work["context"] = work["context"].astype(str)
    work["position"] = pd.to_numeric(work["position"], errors="coerce").fillna(-1).astype(int)
    work["effect_size"] = pd.to_numeric(work.get("effect_size"), errors="coerce")
    if use_region_weight and "region_weight" in work.columns:
        work["_region_w"] = pd.to_numeric(work["region_weight"], errors="coerce").fillna(1.0).clip(lower=0.0)
    else:
        work["_region_w"] = 1.0
    work = work[
        np.isfinite(work["effect_size"])
        & (work["position"] >= 0)
        & work["gene_name"].apply(_is_known_mapped_token)
    ].copy()
    return work


def _build_loci_by_gene(
    cmp_dmp: pd.DataFrame,
    order_index: Dict[Tuple[str, str, int], int],
) -> Dict[str, List[Tuple[int, float, float]]]:
    loci_by_gene: Dict[str, List[Tuple[int, float, float]]] = {}
    for _, row in cmp_dmp.iterrows():
        key = (str(row["chromosome"]), str(row["context"]), int(row["position"]))
        col_idx = order_index.get(key)
        if col_idx is None:
            continue
        effect = float(row["effect_size"])
        abs_w = abs(effect) * float(row["_region_w"])
        if abs_w <= 0.0:
            continue
        sign_e = float(np.sign(effect))
        gene = str(row["gene_name"])
        loci_by_gene.setdefault(gene, []).append((int(col_idx), abs_w, sign_e))
    return loci_by_gene


def _per_gene_directional_value(
    loci: Sequence[Tuple[int, float, float]],
    sample_row: np.ndarray,
) -> Optional[float]:
    locus_num = 0.0
    locus_den = 0.0
    for col_idx, abs_w, sign_e in loci:
        beta = float(sample_row[col_idx])
        if not np.isfinite(beta):
            continue
        locus_num += sign_e * abs_w * (beta - 0.5)
        locus_den += abs_w
    if locus_den <= 0.0:
        return None
    return float(locus_num / locus_den)


def _directional_iqr(dir_values: Sequence[float]) -> float:
    finite = [float(v) for v in dir_values if np.isfinite(v)]
    if len(finite) < 2:
        return float("nan")
    q75, q25 = np.nanpercentile(finite, [75.0, 25.0])
    return float(q75 - q25)


def compute_gene_scored_matrices(
    X_raw: np.ndarray,
    feature_order: Sequence[Tuple[str, str, int]],
    dmp_df: pd.DataFrame,
    panels: Dict[str, pd.DataFrame],
    comparison_labels: Sequence[str],
    *,
    use_region_weight: bool = True,
    gene_weight_mode: str = "importance_x_sqrt_support",
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n_samples = int(X_raw.shape[0])
    labels = [str(x) for x in comparison_labels]
    directional = np.full((n_samples, len(labels)), np.nan, dtype=np.float64)
    obs_fraction = np.full((n_samples, len(labels)), np.nan, dtype=np.float64)
    directional_iqr = np.full((n_samples, len(labels)), np.nan, dtype=np.float64)
    sign_agreement = np.full((n_samples, len(labels)), np.nan, dtype=np.float64)
    if n_samples == 0 or not labels:
        return directional, obs_fraction, directional_iqr, sign_agreement

    order_index = build_locus_order_index(feature_order)
    if dmp_df is None or dmp_df.empty:
        return directional, obs_fraction, directional_iqr, sign_agreement

    work = _prepare_gene_scored_dmp_work(dmp_df, use_region_weight=use_region_weight)
    if work.empty:
        return directional, obs_fraction, directional_iqr, sign_agreement

    for j, cmp_label in enumerate(labels):
        panel = panels.get(cmp_label)
        if panel is None or panel.empty:
            continue
        cmp_dmp = work[work["comparison_label"] == cmp_label]
        if cmp_dmp.empty:
            continue

        loci_by_gene = _build_loci_by_gene(cmp_dmp, order_index)
        if not loci_by_gene:
            continue

        panel_size = int(len(panel))
        if panel_size <= 0:
            continue

        for i in range(n_samples):
            sample_row = np.asarray(X_raw[i, :], dtype=np.float64)
            score_num = 0.0
            score_den = 0.0
            agree_num = 0.0
            agree_den = 0.0
            dir_values: List[float] = []
            n_genes_observed = 0
            for _, grow in panel.iterrows():
                gene = str(grow["gene_name"])
                loci = loci_by_gene.get(gene)
                if not loci:
                    continue
                dir_g = _per_gene_directional_value(loci, sample_row)
                if dir_g is None:
                    continue
                n_genes_observed += 1
                dir_values.append(dir_g)
                w_g = _gene_weight(grow, gene_weight_mode=gene_weight_mode)
                if w_g <= 0.0:
                    continue
                score_num += w_g * dir_g
                score_den += w_g
                prior_sign = _gene_prior_sign(grow)
                if prior_sign is None:
                    continue
                agree_den += w_g
                if float(np.sign(dir_g)) == prior_sign:
                    agree_num += w_g
            obs_fraction[i, j] = float(n_genes_observed / panel_size)
            if score_den > 0.0:
                directional[i, j] = float(score_num / score_den)
            directional_iqr[i, j] = _directional_iqr(dir_values)
            if agree_den > 0.0:
                sign_agreement[i, j] = float(agree_num / agree_den)

    return directional, obs_fraction, directional_iqr, sign_agreement


def compute_gene_directional_score_matrix(
    X_raw: np.ndarray,
    feature_order: Sequence[Tuple[str, str, int]],
    dmp_df: pd.DataFrame,
    panels: Dict[str, pd.DataFrame],
    comparison_labels: Sequence[str],
    *,
    use_region_weight: bool = True,
    gene_weight_mode: str = "importance_x_sqrt_support",
) -> np.ndarray:
    directional, _, _, _ = compute_gene_scored_matrices(
        X_raw,
        feature_order,
        dmp_df,
        panels,
        comparison_labels,
        use_region_weight=use_region_weight,
        gene_weight_mode=gene_weight_mode,
    )
    return directional
