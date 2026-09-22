"""
Comparison-level structural directional scores for feature_family_set=structural_scored.

Uses frozen mapper gene-feature panels (frozen_gene_features.csv) and per-comparison DMP
effect sizes with sample methylation at panel loci, pooled by feature_type (promoter, exon, …).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .gene_scored_features import (
    _directional_iqr,
    _feature_label_token,
    _is_known_mapped_token,
    _normalize_feature_key,
    _normalize_structural_feature,
    _pair_delta_column,
    _per_gene_directional_value,
    _prepare_gene_scored_dmp_work,
    _progression_range_column,
    _progression_slope_column,
    build_locus_order_index,
    normalize_region_directional_region_types,
    resolve_gene_scored_progression_order,
)

STRUCTURAL_DIRECTIONAL_SCORE_PREFIX = "structural_directional_score__"
STRUCTURAL_PANEL_OBS_FRACTION_PREFIX = "structural_panel_obs_fraction__"
STRUCTURAL_DIRECTIONAL_IQR_PREFIX = "structural_directional_iqr__"
STRUCTURAL_WEIGHTED_SIGN_AGREEMENT_PREFIX = "structural_weighted_sign_agreement__"
STRUCTURAL_DIRECTIONAL_CONTRAST_PREFIX = "structural_directional_contrast__"
STRUCTURAL_DIRECTIONAL_ADJACENT_PREFIX = "structural_directional_adjacent_delta__"
STRUCTURAL_DIRECTIONAL_PROGRESSION_SLOPE_PREFIX = "structural_directional_progression_slope__"
STRUCTURAL_DIRECTIONAL_RANGE_PREFIX = "structural_directional_range__"
STRUCTURAL_SCORED_SCHEMA_VERSION = "structural_scored_v1_progression_contrast"

ColumnSpec = Tuple[str, str]


def family_includes_structural_scored(feature_family_set: Optional[str]) -> bool:
    from .observed_feature_builder import normalize_feature_family_set

    token = normalize_feature_family_set(feature_family_set)
    return token in {"structural_scored", "dmp_scored+structural_scored"}


def structural_directional_score_column(comparison_label: object, region_type: object) -> str:
    return (
        f"{STRUCTURAL_DIRECTIONAL_SCORE_PREFIX}"
        f"{_feature_label_token(comparison_label)}__{_feature_label_token(region_type)}"
    )


def structural_panel_obs_fraction_column(comparison_label: object, region_type: object) -> str:
    return (
        f"{STRUCTURAL_PANEL_OBS_FRACTION_PREFIX}"
        f"{_feature_label_token(comparison_label)}__{_feature_label_token(region_type)}"
    )


def structural_directional_iqr_column(comparison_label: object, region_type: object) -> str:
    return (
        f"{STRUCTURAL_DIRECTIONAL_IQR_PREFIX}"
        f"{_feature_label_token(comparison_label)}__{_feature_label_token(region_type)}"
    )


def structural_weighted_sign_agreement_column(comparison_label: object, region_type: object) -> str:
    return (
        f"{STRUCTURAL_WEIGHTED_SIGN_AGREEMENT_PREFIX}"
        f"{_feature_label_token(comparison_label)}__{_feature_label_token(region_type)}"
    )


def structural_directional_contrast_column(
    left_label: object,
    right_label: object,
    region_type: object,
) -> str:
    return (
        f"{STRUCTURAL_DIRECTIONAL_CONTRAST_PREFIX}"
        f"{_feature_label_token(left_label)}__{_feature_label_token(right_label)}"
        f"__{_feature_label_token(region_type)}"
    )


def structural_directional_adjacent_delta_column(
    left_label: object,
    right_label: object,
    region_type: object,
) -> str:
    return (
        f"{STRUCTURAL_DIRECTIONAL_ADJACENT_PREFIX}"
        f"{_feature_label_token(left_label)}__{_feature_label_token(right_label)}"
        f"__{_feature_label_token(region_type)}"
    )


def structural_directional_progression_slope_column(region_type: object) -> str:
    return f"{STRUCTURAL_DIRECTIONAL_PROGRESSION_SLOPE_PREFIX}{_feature_label_token(region_type)}"


def structural_directional_range_column(region_type: object) -> str:
    return f"{STRUCTURAL_DIRECTIONAL_RANGE_PREFIX}{_feature_label_token(region_type)}"


def normalize_structural_scored_contrast_pairs(
    contrast_pairs: Optional[Sequence[Sequence[str]]],
) -> List[Tuple[str, str]]:
    if not contrast_pairs:
        return []
    out: List[Tuple[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for pair in contrast_pairs:
        if pair is None or len(pair) != 2:
            raise ValueError(
                "Each structural_scored_contrast_pairs entry must be [left, right] with two labels."
            )
        left = str(pair[0]).strip()
        right = str(pair[1]).strip()
        if not left or not right:
            raise ValueError("structural_scored_contrast_pairs labels must be non-empty strings.")
        key = (left, right)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def validate_structural_scored_contrast_pairs_against_labels(
    contrast_pairs: Optional[Sequence[Sequence[str]]],
    available_labels: Sequence[str],
) -> None:
    available = {str(x).strip() for x in available_labels if str(x).strip()}
    for left, right in normalize_structural_scored_contrast_pairs(contrast_pairs):
        if left not in available:
            raise ValueError(
                f"structural_scored_contrast_pairs label {left!r} is not in available comparisons: "
                f"{sorted(available)}"
            )
        if right not in available:
            raise ValueError(
                f"structural_scored_contrast_pairs label {right!r} is not in available comparisons: "
                f"{sorted(available)}"
            )


def prepare_structural_scored_panels(
    frozen_gene_features_df: pd.DataFrame,
    *,
    min_support_n: int = 2,
    region_types: Optional[Sequence[str]] = None,
) -> Dict[ColumnSpec, pd.DataFrame]:
    if frozen_gene_features_df is None or frozen_gene_features_df.empty:
        return {}
    work = frozen_gene_features_df.copy()
    required = {"comparison_label", "gene_name", "feature_type"}
    if not required.issubset(set(work.columns)):
        return {}
    regions = set(normalize_region_directional_region_types(region_types))
    work["comparison_label"] = work["comparison_label"].astype(str)
    work["gene_name"] = work["gene_name"].map(_normalize_feature_key)
    work["feature_type"] = work["feature_type"].map(_normalize_structural_feature)
    work["n_dmps_in_feature"] = (
        pd.to_numeric(work.get("n_dmps_in_feature"), errors="coerce").fillna(0).astype(int)
    )
    work["feature_effect_compound"] = (
        pd.to_numeric(work.get("feature_effect_compound"), errors="coerce").fillna(0.0)
    )
    min_n = int(max(1, min_support_n))
    n_initial = int(len(work))
    max_support = int(work["n_dmps_in_feature"].max()) if n_initial > 0 else 0
    work = work[work["n_dmps_in_feature"] >= min_n].copy()
    if work.empty:
        if n_initial > 0:
            raise ValueError(
                f"No gene-feature rows passed structural_scored_min_support_n={min_n} "
                f"(frozen panel had {n_initial} row(s), max n_dmps_in_feature={max_support}). "
                "Lower structural_scored_min_support_n or rebuild frozen_gene_features.csv."
            )
        return {}
    work = work[work["gene_name"].apply(_is_known_mapped_token)].copy()
    if work.empty:
        return {}
    work = work[work["feature_type"].apply(_is_known_mapped_token)].copy()
    if work.empty:
        return {}
    work = work[work["feature_type"] != "unknown"].copy()
    if work.empty:
        return {}
    work = work[work["feature_type"].isin(regions)].copy()
    if work.empty:
        return {}
    work = work[work["feature_effect_compound"] > 0.0].copy()
    if work.empty:
        return {}
    panels: Dict[ColumnSpec, pd.DataFrame] = {}
    for (cmp_label, region), cdf in work.groupby(["comparison_label", "feature_type"], sort=True):
        panel = cdf.drop_duplicates(subset=["gene_name"], keep="first").copy()
        if not panel.empty:
            panels[(str(cmp_label), str(region))] = panel.reset_index(drop=True)
    return panels


def _count_panel_loci_in_index(
    dmp_df: pd.DataFrame,
    feature_order: Sequence[Tuple[str, str, int]],
    panel: pd.DataFrame,
    comparison_label: str,
    region_type: str,
) -> int:
    if dmp_df is None or dmp_df.empty or panel is None or panel.empty:
        return 0
    order_index = build_locus_order_index(feature_order)
    work = _prepare_gene_scored_dmp_work(dmp_df, use_region_weight=True)
    if work.empty:
        return 0
    if "feature_type" in work.columns:
        work["feature_type"] = work["feature_type"].map(_normalize_structural_feature)
    else:
        work["feature_type"] = "unknown"
    panel_genes = {str(g) for g in panel["gene_name"].astype(str).tolist()}
    cmp_dmp = work[
        (work["comparison_label"] == str(comparison_label))
        & (work["feature_type"] == str(region_type))
        & work["gene_name"].isin(panel_genes)
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


def _panel_loci_keys_in_index(
    dmp_df: pd.DataFrame,
    feature_order: Sequence[Tuple[str, str, int]],
    panel: pd.DataFrame,
    comparison_label: str,
    region_type: str,
) -> set[Tuple[str, str, int]]:
    if dmp_df is None or dmp_df.empty or panel is None or panel.empty:
        return set()
    order_index = build_locus_order_index(feature_order)
    work = _prepare_gene_scored_dmp_work(dmp_df, use_region_weight=True)
    if work.empty:
        return set()
    if "feature_type" in work.columns:
        work["feature_type"] = work["feature_type"].map(_normalize_structural_feature)
    else:
        work["feature_type"] = "unknown"
    panel_genes = {str(g) for g in panel["gene_name"].astype(str).tolist()}
    cmp_dmp = work[
        (work["comparison_label"] == str(comparison_label))
        & (work["feature_type"] == str(region_type))
        & work["gene_name"].isin(panel_genes)
    ]
    keys: set[Tuple[str, str, int]] = set()
    for _, row in cmp_dmp.iterrows():
        key = (str(row["chromosome"]), str(row["context"]), int(row["position"]))
        if key in order_index:
            keys.add(key)
    return keys


def compute_structural_scored_partition_coverage(
    dmp_df: pd.DataFrame,
    feature_order: Sequence[Tuple[str, str, int]],
    panels: Dict[ColumnSpec, pd.DataFrame],
    column_specs: Sequence[ColumnSpec],
    *,
    region_types: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    order_index = build_locus_order_index(feature_order)
    n_classifier_loci = int(len(order_index))
    regions = set(normalize_region_directional_region_types(region_types))

    work = _prepare_gene_scored_dmp_work(dmp_df, use_region_weight=True) if dmp_df is not None else pd.DataFrame()
    if not work.empty and "feature_type" in work.columns:
        work = work.copy()
        work["feature_type"] = work["feature_type"].map(_normalize_structural_feature)
    else:
        work = pd.DataFrame()

    assigned_keys: set[Tuple[str, str, int]] = set()
    for spec in column_specs:
        cmp_label, region = spec
        panel = panels.get(spec)
        if panel is None or panel.empty:
            continue
        assigned_keys |= _panel_loci_keys_in_index(
            dmp_df,
            feature_order,
            panel,
            cmp_label,
            region,
        )

    n_unknown_or_unmapped = 0
    if n_classifier_loci > 0 and not work.empty:
        for key in order_index:
            chrom, ctx, pos = key
            rows = work[
                (work["chromosome"].astype(str) == chrom)
                & (work["context"].astype(str) == ctx)
                & (work["position"].astype(int) == int(pos))
            ]
            if rows.empty:
                n_unknown_or_unmapped += 1
                continue
            feature_types = {str(x) for x in rows["feature_type"].astype(str).tolist()}
            if "unknown" in feature_types or not (feature_types & regions):
                n_unknown_or_unmapped += 1

    n_assigned = int(len(assigned_keys))
    return {
        "n_classifier_loci": n_classifier_loci,
        "n_loci_assigned_to_emitted_regions": n_assigned,
        "n_unknown_or_unmapped": int(n_unknown_or_unmapped),
        "partition_fraction": (
            float(n_assigned / n_classifier_loci) if n_classifier_loci > 0 else 0.0
        ),
    }


def resolve_structural_scored_column_specs(
    dmp_df: pd.DataFrame,
    feature_order: Sequence[Tuple[str, str, int]],
    panels: Dict[ColumnSpec, pd.DataFrame],
    *,
    min_loci: int = 1,
) -> List[ColumnSpec]:
    if not panels:
        return []
    min_n = int(max(1, min_loci))
    specs: List[ColumnSpec] = []
    for spec_key in sorted(panels.keys()):
        cmp_label, region = spec_key
        panel = panels.get(spec_key)
        if panel is None or panel.empty:
            continue
        n_loci = _count_panel_loci_in_index(dmp_df, feature_order, panel, cmp_label, region)
        if n_loci >= min_n:
            specs.append(spec_key)
    return specs


def preflight_structural_scored_training(
    *,
    dmp_df: pd.DataFrame,
    feature_order: Sequence[Tuple[str, str, int]],
    fixed_gene_features_df: pd.DataFrame,
    feature_family_set: str,
    structural_scored_min_support_n: int = 2,
    region_directional_min_loci: int = 1,
    region_directional_region_types: Optional[Sequence[str]] = None,
) -> None:
    """
    Fail in seconds when structural_scored-only training would emit zero columns.

    Call before sample extraction so long-running feature builds are not started
    on an unusable panel.
    """
    from .observed_feature_builder import _family_flags

    if not family_includes_structural_scored(feature_family_set):
        return
    include_dmp, include_gene, include_structural, include_gene_scored, include_structural_scored, _include_chromosome = (
        _family_flags(feature_family_set)
    )
    structural_scored_only = bool(
        include_structural_scored
        and not (include_dmp or include_gene or include_structural or include_gene_scored)
    )
    if not structural_scored_only:
        return
    if fixed_gene_features_df is None or fixed_gene_features_df.empty:
        raise ValueError(
            "feature_family_set=structural_scored requires frozen_gene_features.csv but the panel is empty. "
            "Run --freeze or ensure production/model_bundle/frozen_gene_features.csv exists."
        )
    panels = prepare_structural_scored_panels(
        fixed_gene_features_df,
        min_support_n=int(structural_scored_min_support_n),
        region_types=region_directional_region_types,
    )
    column_specs = resolve_structural_scored_column_specs(
        dmp_df,
        feature_order,
        panels,
        min_loci=int(max(1, region_directional_min_loci)),
    )
    require_structural_scored_columns_emitted(
        fixed_gene_features_df=fixed_gene_features_df,
        panels=panels,
        column_specs=column_specs,
        structural_scored_only=True,
        structural_scored_min_support_n=int(structural_scored_min_support_n),
        region_directional_min_loci=int(max(1, region_directional_min_loci)),
        region_directional_region_types=region_directional_region_types,
    )


def require_structural_scored_columns_emitted(
    *,
    fixed_gene_features_df: pd.DataFrame,
    panels: Dict[ColumnSpec, pd.DataFrame],
    column_specs: Sequence[ColumnSpec],
    structural_scored_only: bool,
    structural_scored_min_support_n: int = 2,
    region_directional_min_loci: int = 1,
    region_directional_region_types: Optional[Sequence[str]] = None,
) -> None:
    """Fail fast when structural_scored is the sole family but no columns would be emitted."""
    if not structural_scored_only or column_specs:
        return

    regions = set(normalize_region_directional_region_types(region_directional_region_types))
    min_support = int(max(1, structural_scored_min_support_n))
    min_loci = int(max(1, region_directional_min_loci))
    n_rows = int(len(fixed_gene_features_df)) if fixed_gene_features_df is not None else 0

    hints: List[str] = []
    if n_rows == 0:
        hints.append("frozen_gene_features.csv is empty or missing")
    else:
        work = fixed_gene_features_df.copy()
        work["n_dmps_in_feature"] = (
            pd.to_numeric(work.get("n_dmps_in_feature"), errors="coerce").fillna(0).astype(int)
        )
        work["feature_effect_compound"] = pd.to_numeric(
            work.get("feature_effect_compound"), errors="coerce"
        ).fillna(0.0)
        work["feature_type"] = (
            work.get("feature_type", pd.Series(["unknown"] * len(work), index=work.index))
            .map(_normalize_structural_feature)
            .astype(str)
        )
        n_support = int((work["n_dmps_in_feature"] >= min_support).sum())
        n_compound = int((work["feature_effect_compound"] > 0.0).sum())
        n_region = int(work["feature_type"].isin(regions).sum())
        hints.append(
            f"frozen_gene_features rows={n_rows}, "
            f"with n_dmps_in_feature>={min_support}: {n_support}, "
            f"with feature_effect_compound>0: {n_compound}, "
            f"in region_directional_region_types: {n_region}"
        )
        if n_compound == 0:
            hints.append(
                "all feature_effect_compound values are zero; rebuild "
                "production/model_bundle/frozen_gene_features.csv via build_frozen_gene_panel "
                "(mapper feature_effect_compound_* columns must be merged at freeze)"
            )
        if panels:
            panel_summary = ", ".join(
                f"{cmp}::{region}({len(panel)} genes)"
                for (cmp, region), panel in sorted(panels.items())
            )
            hints.append(
                f"panel gene-features exist ({panel_summary}) but none met "
                f"region_directional_min_loci={min_loci} in the classifier DMP index; "
                "check bundle feature_order and mapper locus overlap"
            )
        elif n_support == 0:
            hints.append(
                f"no rows pass structural_scored_min_support_n={min_support}; "
                "lower the threshold or rebuild the panel"
            )
        elif n_region == 0:
            hints.append(
                f"no rows match region_directional_region_types={sorted(regions)}"
            )

    detail = "; ".join(hints) if hints else "no structural columns resolved"
    raise ValueError(
        "feature_family_set=structural_scored would emit zero training features. "
        f"{detail}. Combine with dmp_scored (feature_family_set=dmp_scored+structural_scored) "
        "or fix the frozen gene-feature panel before training."
    )


def resolve_structural_scored_comparison_labels(
    dmp_df: pd.DataFrame,
    frozen_gene_features_df: pd.DataFrame,
) -> List[str]:
    dmp_labels: set[str] = set()
    if dmp_df is not None and not dmp_df.empty and "comparison_label" in dmp_df.columns:
        dmp_labels = {
            str(x).strip()
            for x in dmp_df["comparison_label"].astype(str).tolist()
            if str(x).strip()
        }
    panel_labels: set[str] = set()
    if frozen_gene_features_df is not None and not frozen_gene_features_df.empty:
        if "comparison_label" in frozen_gene_features_df.columns:
            panel_labels = {
                str(x).strip()
                for x in frozen_gene_features_df["comparison_label"].astype(str).tolist()
                if str(x).strip()
            }
    if dmp_labels and panel_labels:
        return sorted(dmp_labels & panel_labels)
    if dmp_labels:
        return sorted(dmp_labels)
    if panel_labels:
        return sorted(panel_labels)
    return []


def resolve_structural_scored_labels_for_features(
    dmp_df: pd.DataFrame,
    frozen_gene_features_df: pd.DataFrame,
    *,
    project_json: Optional[str | Path] = None,
    explicit_order: Optional[Sequence[str]] = None,
) -> Tuple[List[str], List[str]]:
    available = resolve_structural_scored_comparison_labels(dmp_df, frozen_gene_features_df)
    progression_order = resolve_gene_scored_progression_order(
        available,
        project_json=project_json,
        explicit_order=explicit_order,
    )
    return available, progression_order


def structural_scored_progression_feature_names(
    ordered_labels: Sequence[str],
    region_type: str,
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
    _add(structural_directional_contrast_column(first, last, region_type))
    _add(structural_directional_progression_slope_column(region_type))

    if k >= 3:
        _add(structural_directional_range_column(region_type))
        for i in range(k - 1):
            _add(structural_directional_adjacent_delta_column(labels[i], labels[i + 1], region_type))

    for left, right in normalize_structural_scored_contrast_pairs(contrast_pairs):
        _add(structural_directional_contrast_column(left, right, region_type))

    return names


def structural_scored_feature_names(
    column_specs: Sequence[ColumnSpec],
    progression_order: Sequence[str],
    *,
    contrast_pairs: Optional[Sequence[Sequence[str]]] = None,
) -> List[str]:
    names: List[str] = []
    for cmp_label, region in column_specs:
        names.append(structural_directional_score_column(cmp_label, region))
        names.append(structural_panel_obs_fraction_column(cmp_label, region))
        names.append(structural_directional_iqr_column(cmp_label, region))
        names.append(structural_weighted_sign_agreement_column(cmp_label, region))

    regions_with_specs = sorted({region for _, region in column_specs})
    cmp_labels_in_specs = {cmp for cmp, _ in column_specs}
    progression_labels = [lbl for lbl in progression_order if lbl in cmp_labels_in_specs]
    if len(progression_labels) >= 2:
        for region in regions_with_specs:
            region_specs = [(c, r) for c, r in column_specs if r == region]
            if len({c for c, _ in region_specs}) < 2:
                continue
            names.extend(
                structural_scored_progression_feature_names(
                    progression_labels,
                    region,
                    contrast_pairs=contrast_pairs,
                )
            )
    return names


def _structural_weight(row: pd.Series, *, weight_mode: str) -> float:
    compound = float(pd.to_numeric(row.get("feature_effect_compound"), errors="coerce") or 0.0)
    support_n = float(pd.to_numeric(row.get("n_dmps_in_feature"), errors="coerce") or 0.0)
    mode = str(weight_mode or "compound_x_sqrt_support").strip().lower()
    if mode == "compound_only":
        return compound
    if compound <= 0.0 or support_n <= 0.0:
        return 0.0
    return compound * float(np.sqrt(support_n))


def _structural_prior_sign(row: pd.Series) -> Optional[float]:
    effect = float(pd.to_numeric(row.get("feature_effect_compound"), errors="coerce") or 0.0)
    if not np.isfinite(effect) or effect == 0.0:
        return None
    return float(np.sign(effect))


def _build_loci_by_gene_region(
    cmp_dmp: pd.DataFrame,
    order_index: Dict[Tuple[str, str, int], int],
    region_type: str,
) -> Dict[str, List[Tuple[int, float, float]]]:
    loci_by_gene: Dict[str, List[Tuple[int, float, float]]] = {}
    region_norm = str(region_type)
    for _, row in cmp_dmp.iterrows():
        if str(row.get("feature_type", "unknown")) != region_norm:
            continue
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


def compute_structural_scored_progression_features(
    directional_by_spec: Dict[ColumnSpec, np.ndarray],
    column_specs: Sequence[ColumnSpec],
    progression_order: Sequence[str],
    *,
    contrast_pairs: Optional[Sequence[Sequence[str]]] = None,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    regions = sorted({region for _, region in column_specs})
    cmp_labels_in_specs = {cmp for cmp, _ in column_specs}
    labels = [str(x) for x in progression_order if str(x) in cmp_labels_in_specs]
    if len(labels) < 2:
        return {}, []

    all_features: Dict[str, np.ndarray] = {}
    all_names: List[str] = []
    n_samples = 0
    for _, vec in directional_by_spec.items():
        n_samples = int(vec.shape[0])
        break

    for region in regions:
        region_specs = [(c, r) for c, r in column_specs if r == region]
        region_cmps = [c for c in labels if (c, region) in set(region_specs)]
        if len(region_cmps) < 2:
            continue
        matrix = np.full((n_samples, len(region_cmps)), np.nan, dtype=np.float64)
        for j, cmp_label in enumerate(region_cmps):
            vec = directional_by_spec.get((cmp_label, region))
            if vec is not None:
                matrix[:, j] = vec
        prog_feats, prog_names = _compute_progression_for_region_matrix(
            matrix,
            region_cmps,
            region,
            contrast_pairs=contrast_pairs,
        )
        all_features.update(prog_feats)
        all_names.extend(prog_names)

    return all_features, all_names


def _compute_progression_for_region_matrix(
    directional_matrix: np.ndarray,
    ordered_labels: Sequence[str],
    region_type: str,
    *,
    contrast_pairs: Optional[Sequence[Sequence[str]]] = None,
) -> Tuple[Dict[str, np.ndarray], List[str]]:
    labels = [str(x) for x in ordered_labels]
    feature_names = structural_scored_progression_feature_names(
        labels,
        region_type,
        contrast_pairs=contrast_pairs,
    )
    n_samples = int(directional_matrix.shape[0]) if directional_matrix.ndim == 2 else 0
    features: Dict[str, np.ndarray] = {
        name: np.full((n_samples,), np.nan, dtype=np.float64) for name in feature_names
    }
    if n_samples == 0 or len(labels) < 2:
        return features, feature_names

    label_to_idx = {label: idx for idx, label in enumerate(labels)}
    k = len(labels)

    contrast_col = structural_directional_contrast_column(labels[0], labels[-1], region_type)
    if contrast_col in features:
        features[contrast_col] = _pair_delta_column(directional_matrix, 0, k - 1)
    slope_col = structural_directional_progression_slope_column(region_type)
    if slope_col in features:
        features[slope_col] = _progression_slope_column(directional_matrix)
    range_col = structural_directional_range_column(region_type)
    if range_col in features:
        features[range_col] = _progression_range_column(directional_matrix)
    if k >= 3:
        for i in range(k - 1):
            col = structural_directional_adjacent_delta_column(labels[i], labels[i + 1], region_type)
            if col in features:
                features[col] = _pair_delta_column(directional_matrix, i, i + 1)

    for left, right in normalize_structural_scored_contrast_pairs(contrast_pairs):
        col = structural_directional_contrast_column(left, right, region_type)
        if col not in features:
            continue
        if col == contrast_col:
            continue
        j_left = label_to_idx.get(left)
        j_right = label_to_idx.get(right)
        if j_left is not None and j_right is not None:
            features[col] = _pair_delta_column(directional_matrix, j_left, j_right)

    return features, feature_names


def compute_structural_scored_matrices(
    X_raw: np.ndarray,
    feature_order: Sequence[Tuple[str, str, int]],
    dmp_df: pd.DataFrame,
    panels: Dict[ColumnSpec, pd.DataFrame],
    column_specs: Sequence[ColumnSpec],
    *,
    use_region_weight: bool = True,
    weight_mode: str = "compound_x_sqrt_support",
) -> Tuple[
    Dict[ColumnSpec, np.ndarray],
    Dict[ColumnSpec, np.ndarray],
    Dict[ColumnSpec, np.ndarray],
    Dict[ColumnSpec, np.ndarray],
]:
    n_samples = int(X_raw.shape[0])
    directional: Dict[ColumnSpec, np.ndarray] = {}
    obs_fraction: Dict[ColumnSpec, np.ndarray] = {}
    directional_iqr: Dict[ColumnSpec, np.ndarray] = {}
    sign_agreement: Dict[ColumnSpec, np.ndarray] = {}

    for spec in column_specs:
        directional[spec] = np.full((n_samples,), np.nan, dtype=np.float64)
        obs_fraction[spec] = np.full((n_samples,), np.nan, dtype=np.float64)
        directional_iqr[spec] = np.full((n_samples,), np.nan, dtype=np.float64)
        sign_agreement[spec] = np.full((n_samples,), np.nan, dtype=np.float64)

    if n_samples == 0 or not column_specs:
        return directional, obs_fraction, directional_iqr, sign_agreement

    order_index = build_locus_order_index(feature_order)
    if dmp_df is None or dmp_df.empty:
        return directional, obs_fraction, directional_iqr, sign_agreement

    work = _prepare_gene_scored_dmp_work(dmp_df, use_region_weight=use_region_weight)
    if work.empty:
        return directional, obs_fraction, directional_iqr, sign_agreement

    if "feature_type" not in work.columns:
        work["feature_type"] = work.get("feature_type", "unknown")
    work["feature_type"] = work["feature_type"].map(_normalize_structural_feature)

    for spec in column_specs:
        cmp_label, region = spec
        panel = panels.get(spec)
        if panel is None or panel.empty:
            continue
        cmp_dmp = work[work["comparison_label"] == cmp_label]
        if cmp_dmp.empty:
            continue

        loci_by_gene = _build_loci_by_gene_region(cmp_dmp, order_index, region)
        if not loci_by_gene:
            continue

        panel_size = int(len(panel))
        if panel_size <= 0:
            continue

        dir_vec = directional[spec]
        obs_vec = obs_fraction[spec]
        iqr_vec = directional_iqr[spec]
        agree_vec = sign_agreement[spec]

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
                w_g = _structural_weight(grow, weight_mode=weight_mode)
                if w_g <= 0.0:
                    continue
                score_num += w_g * dir_g
                score_den += w_g
                prior_sign = _structural_prior_sign(grow)
                if prior_sign is None:
                    continue
                agree_den += w_g
                if float(np.sign(dir_g)) == prior_sign:
                    agree_num += w_g
            obs_vec[i] = float(n_genes_observed / panel_size)
            if score_den > 0.0:
                dir_vec[i] = float(score_num / score_den)
            iqr_vec[i] = _directional_iqr(dir_values)
            if agree_den > 0.0:
                agree_vec[i] = float(agree_num / agree_den)

    return directional, obs_fraction, directional_iqr, sign_agreement
