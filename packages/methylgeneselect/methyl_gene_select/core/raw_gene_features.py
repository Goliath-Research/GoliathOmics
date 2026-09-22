"""
Simple raw-gene feature builder for production ECDF OvR.

One feature per stable gene: weighted mean methylation at mapped stable DMP loci.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import sample_matrix

GENE_FEATURE_PREFIX = "gene::"


def _normalize_gene_name(value: Any) -> str:
    token = str(value or "").strip()
    if not token or token.lower() in {"unknown", "nan", "none", ""}:
        return ""
    return token


def _gene_feature_name(gene_name: str) -> str:
    return f"{GENE_FEATURE_PREFIX}{gene_name}"


def _parse_gene_feature_name(feature_name: str) -> str:
    name = str(feature_name or "").strip()
    if name.lower().startswith(GENE_FEATURE_PREFIX):
        return name.split("::", 1)[1].strip()
    return name


def resolve_stable_gene_names(frozen_gene_panel_df: pd.DataFrame) -> List[str]:
    if frozen_gene_panel_df is None or frozen_gene_panel_df.empty:
        return []
    if "gene_name" not in frozen_gene_panel_df.columns:
        return []
    genes = [
        _normalize_gene_name(g)
        for g in frozen_gene_panel_df["gene_name"].astype(str).tolist()
    ]
    genes = sorted({g for g in genes if g})
    return genes


def build_gene_locus_index_map(
    dmp_df: pd.DataFrame,
    feature_order: Sequence[Tuple[str, str, int]],
    gene_names: Sequence[str],
    *,
    use_region_weight: bool = True,
) -> Tuple[Dict[str, List[int]], np.ndarray]:
    """
    Map each gene to locus column indices and per-locus aggregation weights.
    """
    order = list(feature_order)
    order_to_idx = {key: idx for idx, key in enumerate(order)}
    work = dmp_df.copy()
    if "gene_name" not in work.columns:
        work["gene_name"] = "unknown"
    if "effect_size" not in work.columns:
        work["effect_size"] = 0.0
    if "region_weight" not in work.columns:
        work["region_weight"] = 1.0
    work["chromosome"] = work["chromosome"].astype(str)
    work["context"] = work["context"].astype(str)
    work["position"] = pd.to_numeric(work["position"], errors="coerce").fillna(-1).astype(int)
    work = work[work["position"] >= 0].copy()
    work["gene_key"] = work["gene_name"].map(_normalize_gene_name)
    abs_effect = np.abs(
        pd.to_numeric(work["effect_size"], errors="coerce").fillna(0.0).astype(float).to_numpy()
    )
    region_w = (
        pd.to_numeric(work["region_weight"], errors="coerce").fillna(1.0).astype(float).to_numpy()
        if use_region_weight
        else np.ones((len(work),), dtype=np.float64)
    )
    locus_weights = abs_effect * np.clip(region_w, 0.0, None)

    gene_to_indices: Dict[str, List[int]] = {str(g): [] for g in gene_names}
    per_locus_weight = np.zeros((len(order),), dtype=np.float64)
    for pos, (_, row) in enumerate(work.iterrows()):
        gene_key = str(row["gene_key"])
        key = (str(row["chromosome"]), str(row["context"]), int(row["position"]))
        idx = order_to_idx.get(key)
        if idx is None:
            continue
        w = float(locus_weights[pos])
        per_locus_weight[idx] = max(per_locus_weight[idx], w)
        if gene_key and gene_key in gene_to_indices and idx not in gene_to_indices[gene_key]:
            gene_to_indices[gene_key].append(int(idx))

    return gene_to_indices, per_locus_weight


def _aggregate_gene_values(
    X_loci: np.ndarray,
    gene_to_indices: Dict[str, List[int]],
    gene_names: Sequence[str],
    per_locus_weight: np.ndarray,
) -> np.ndarray:
    n_samples = int(X_loci.shape[0])
    n_genes = len(gene_names)
    out = np.full((n_samples, n_genes), np.nan, dtype=np.float64)
    if n_genes == 0:
        return out
    for j, gene in enumerate(gene_names):
        indices = gene_to_indices.get(str(gene), [])
        if not indices:
            continue
        block = np.asarray(X_loci[:, indices], dtype=np.float64)
        weights = np.asarray([per_locus_weight[i] for i in indices], dtype=np.float64)
        weights = np.where(np.isfinite(weights) & (weights > 0.0), weights, 0.0)
        for row in range(n_samples):
            vals = block[row, :]
            mask = np.isfinite(vals)
            if not np.any(mask):
                continue
            w = weights.copy()
            w[~mask] = 0.0
            wsum = float(np.sum(w[mask]))
            if wsum <= 0.0:
                out[row, j] = float(np.mean(vals[mask]))
            else:
                out[row, j] = float(np.sum(vals[mask] * w[mask]) / wsum)
    return out


def build_gene_panel_feature_weights(
    frozen_gene_panel_df: pd.DataFrame,
    feature_names: Sequence[str],
    *,
    weight_column: str = "gene_importance",
) -> np.ndarray:
    names = [str(x) for x in feature_names]
    if not names:
        return np.zeros((0,), dtype=np.float64)

    panel = frozen_gene_panel_df.copy() if frozen_gene_panel_df is not None else pd.DataFrame()
    gene_weights: Dict[str, float] = {}
    if not panel.empty and "gene_name" in panel.columns:
        col = weight_column if weight_column in panel.columns else None
        if col is None and "gene_importance" in panel.columns:
            col = "gene_importance"
        if col is not None:
            panel["gene_key"] = panel["gene_name"].map(_normalize_gene_name)
            panel["w"] = np.abs(
                pd.to_numeric(panel[col], errors="coerce").fillna(0.0).astype(float)
            )
            grouped = panel.groupby("gene_key", as_index=False)["w"].mean()
            gene_weights = {
                str(r["gene_key"]): float(r["w"])
                for _, r in grouped.iterrows()
                if str(r["gene_key"])
            }

    default_w = float(np.mean(list(gene_weights.values()))) if gene_weights else 1.0
    if default_w <= 0.0:
        default_w = 1.0

    out = np.zeros((len(names),), dtype=np.float64)
    for i, name in enumerate(names):
        gene = _parse_gene_feature_name(name)
        out[i] = float(gene_weights.get(gene, default_w))
    max_w = float(np.max(out)) if out.size else 1.0
    if max_w <= 0.0:
        return np.ones_like(out)
    out = np.clip(out / max_w, 1e-6, 1.0)
    return out


@dataclass
class RawGeneFeatureTable:
    X: np.ndarray
    feature_names: List[str]
    report: Dict[str, Any]


def build_raw_gene_feature_table(
    sample_paths: Sequence[str],
    dmp_df: pd.DataFrame,
    frozen_gene_panel_df: pd.DataFrame,
    *,
    min_coverage: int = 1,
    use_region_weight: bool = True,
    gene_weight_column: str = "gene_importance",
    gene_name_order: Optional[Sequence[str]] = None,
) -> RawGeneFeatureTable:
    if gene_name_order is not None:
        gene_names = [
            _normalize_gene_name(g)
            for g in gene_name_order
            if _normalize_gene_name(g)
        ]
    else:
        gene_names = resolve_stable_gene_names(frozen_gene_panel_df)
    feature_names = [_gene_feature_name(g) for g in gene_names]
    if not gene_names:
        return RawGeneFeatureTable(
            X=np.zeros((len(sample_paths), 0), dtype=np.float64),
            feature_names=[],
            report={
                "n_genes": 0,
                "n_loci": 0,
                "use_region_weight": bool(use_region_weight),
                "gene_weight_column": str(gene_weight_column),
            },
        )

    refs, feature_order = sample_matrix._build_reference_map(dmp_df)
    X_loci = sample_matrix._extract_matrix_for_samples(
        list(sample_paths),
        refs,
        feature_order,
        min_coverage=int(max(1, min_coverage)),
    )
    gene_to_indices, per_locus_weight = build_gene_locus_index_map(
        dmp_df,
        feature_order,
        gene_names,
        use_region_weight=use_region_weight,
    )
    X = _aggregate_gene_values(
        np.asarray(X_loci, dtype=np.float64),
        gene_to_indices,
        gene_names,
        per_locus_weight,
    )

    n_genes_with_loci = sum(1 for g in gene_names if gene_to_indices.get(g))
    report = {
        "n_genes": int(len(gene_names)),
        "n_genes_with_loci": int(n_genes_with_loci),
        "n_loci": int(len(feature_order)),
        "use_region_weight": bool(use_region_weight),
        "gene_weight_column": str(gene_weight_column),
        "feature_names": list(feature_names),
    }
    return RawGeneFeatureTable(
        X=np.asarray(X, dtype=np.float64),
        feature_names=feature_names,
        report=report,
    )
