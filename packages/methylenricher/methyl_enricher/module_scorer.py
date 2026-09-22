"""
Module scoring and disease-aware ranking: compute module score from enrichment
metrics and optional disease relevance prior, then rank modules.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from .pathway_graph import canonical_pathway_key

logger = logging.getLogger(__name__)

def _normalize_score(x: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clip and optionally scale to [0, 1]."""
    return float(np.clip(x, low, high))


def _finite_mean(values: pd.Series, *, default: float = 0.0) -> float:
    """Mean over finite numeric values only."""
    clean = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if clean.empty:
        return float(default)
    return float(clean.mean())


def compute_module_enrichment_score(
    module_pathways: List[str],
    merged_df: pd.DataFrame,
    pathway_to_genes: Dict[str, Set[str]],
    gene_weights: Optional[Dict[str, float]] = None,
    w_mean_log10q: float = 0.35,
    w_n_pathways: float = 0.15,
    w_n_genes: float = 0.15,
    w_mean_gene_weight: float = 0.15,
    w_overlap_weight_abs_mean: float = 0.20,
) -> float:
    """
    Compute a single scalar enrichment score for a module.
    merged_df must have 'Term' and 'Adjusted P-value'.
    """
    if not module_pathways:
        return 0.0
    key_set = set(module_pathways)
    term_keys = merged_df["Term"].map(canonical_pathway_key)
    sub = merged_df[term_keys.isin(key_set)]
    if sub.empty:
        return 0.0
    q = pd.to_numeric(sub["Adjusted P-value"], errors="coerce").fillna(1.0)
    mean_log10q = np.mean(-np.log10(q.clip(lower=1e-10)))
    n_pathways = len(module_pathways)
    all_genes = set()
    for p in module_pathways:
        all_genes |= pathway_to_genes.get(p, set())
    n_genes = len(all_genes)
    mean_gw = 1.0
    if gene_weights and all_genes:
        vals = [gene_weights.get(g, 1.0) for g in all_genes]
        mean_gw = float(np.mean(vals)) if vals else 1.0
    mean_overlap_abs_weight = 0.0
    if "overlap_weight_abs_mean" in sub.columns:
        mean_overlap_abs_weight = float(
            pd.to_numeric(sub["overlap_weight_abs_mean"], errors="coerce").fillna(0.0).mean()
        )
    # Normalize components to ~[0,1] scale then weighted sum
    score = (
        w_mean_log10q * _normalize_score(mean_log10q / 10.0)  # -log10(q) often 1–20
        + w_n_pathways * _normalize_score(n_pathways / 50.0)
        + w_n_genes * _normalize_score(n_genes / 200.0)
        + w_mean_gene_weight * _normalize_score(mean_gw)
        + w_overlap_weight_abs_mean * _normalize_score(mean_overlap_abs_weight)
    )
    return _normalize_score(score)


def compute_module_activity_components(
    module_pathways: List[str],
    merged_df: pd.DataFrame,
    pathway_to_genes: Dict[str, Set[str]],
    gene_effects: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    Compute interpretable per-module activity components.

    These are exported for longitudinal progression analysis and intentionally kept
    independent from final ranking-score weights.
    """
    out = {
        "activity_combined_score_mean": 0.0,
        "activity_log10q_mean": 0.0,
        "activity_overlap_effect_mean": 0.0,
        "activity_gene_effect_mean": 0.0,
    }
    if not module_pathways:
        return out
    key_set = set(module_pathways)
    term_keys = merged_df["Term"].map(canonical_pathway_key)
    sub = merged_df[term_keys.isin(key_set)]
    if sub.empty:
        return out
    if "Adjusted P-value" in sub.columns:
        q = pd.to_numeric(sub["Adjusted P-value"], errors="coerce").fillna(1.0)
    else:
        q = pd.Series([1.0] * len(sub))
    out["activity_log10q_mean"] = float(np.mean(-np.log10(q.clip(lower=1e-10))))
    if "Combined Score" in sub.columns:
        out["activity_combined_score_mean"] = _finite_mean(sub["Combined Score"], default=0.0)
    elif "Odds Ratio" in sub.columns:
        out["activity_combined_score_mean"] = _finite_mean(sub["Odds Ratio"], default=0.0)
    if "overlap_weight_abs_mean" in sub.columns:
        out["activity_overlap_effect_mean"] = _finite_mean(sub["overlap_weight_abs_mean"], default=0.0)
    all_genes: Set[str] = set()
    for p in module_pathways:
        all_genes |= pathway_to_genes.get(p, set())
    if gene_effects and all_genes:
        vals = []
        for g in all_genes:
            raw = gene_effects.get(g.strip().upper(), 0.0)
            try:
                num = float(raw)
            except (TypeError, ValueError):
                continue
            if np.isfinite(num):
                vals.append(abs(num))
        if vals:
            out["activity_gene_effect_mean"] = float(np.mean(vals))
    return out


def compute_disease_relevance(
    module_genes: Set[str],
    disease_genes: Optional[Set[str]] = None,
) -> float:
    """
    Disease relevance score: fraction of module genes that are in disease set,
    or NaN if no disease set. Returns value in [0, 1] when computable.
    """
    disease_genes = disease_genes or set()
    if not disease_genes or not module_genes:
        return float("nan")
    disease_genes = {g.strip().upper() for g in disease_genes}
    module_genes = {g.strip().upper() for g in module_genes}
    overlap = len(module_genes & disease_genes)
    return _normalize_score(overlap / len(module_genes))


def pca_relevance_label(score: float) -> str:
    """Map disease relevance score to High/Medium/Low."""
    if score >= 0.4:
        return "High"
    if score >= 0.2:
        return "Medium"
    return "Low"


def score_and_rank_modules(
    pathway_to_module_id: Dict[str, int],
    pathway_to_genes: Dict[str, Set[str]],
    merged_df: pd.DataFrame,
    gene_weights: Optional[Dict[str, float]] = None,
    disease_genes: Optional[Set[str]] = None,
    weight_enrichment: float = 0.7,
    weight_disease: float = 0.3,
    ppi_coherence_by_module: Optional[Dict[int, float]] = None,
    ppi_weight_in_final_score: float = 0.0,
    gene_effects: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """
    For each module, compute enrichment score and disease relevance, combine into
    final score, and return a DataFrame of modules sorted by final score (descending).
    Columns: module_id, module_label (placeholder), n_pathways, n_genes, enrichment_score,
    disease_relevance, final_score, pca_relevance (High/Medium/Low), and optional
    ppi_coherence_score/blended_score fields.
    """
    # Use only user-supplied disease genes; without a prior, keep disease relevance neutral.
    disease_genes = disease_genes or set()
    has_disease_prior = bool(disease_genes)
    ppi_coherence_by_module = ppi_coherence_by_module or {}
    ppi_weight = float(np.clip(ppi_weight_in_final_score, 0.0, 1.0))
    module_ids = sorted(set(pathway_to_module_id.values()))
    rows = []
    for mid in module_ids:
        pathways = [p for p, m in pathway_to_module_id.items() if m == mid]
        genes = set()
        for p in pathways:
            genes |= pathway_to_genes.get(p, set())
        enrich_score = compute_module_enrichment_score(
            pathways, merged_df, pathway_to_genes, gene_weights
        )
        activity = compute_module_activity_components(
            pathways,
            merged_df,
            pathway_to_genes,
            gene_effects=gene_effects,
        )
        disease_score = compute_disease_relevance(genes, disease_genes)
        if has_disease_prior and np.isfinite(disease_score):
            base_score = weight_enrichment * enrich_score + weight_disease * disease_score
            disease_tier = pca_relevance_label(disease_score)
        else:
            # When no disease prior exists, ranking should be purely enrichment-driven.
            base_score = enrich_score
            disease_tier = None
        ppi_score = float(np.clip(ppi_coherence_by_module.get(int(mid), 0.0), 0.0, 1.0))
        blended_score = (1.0 - ppi_weight) * base_score + ppi_weight * ppi_score
        rows.append({
            "module_id": mid,
            "n_pathways": len(pathways),
            "n_genes": len(genes),
            "enrichment_score": round(enrich_score, 4),
            "disease_relevance": (round(disease_score, 4) if np.isfinite(disease_score) else np.nan),
            "base_score": round(base_score, 4),
            "ppi_coherence_score": round(ppi_score, 4),
            "blended_score": round(blended_score, 4),
            "final_score": round(blended_score, 4),
            "disease_relevance_tier": disease_tier,
            "has_disease_prior": has_disease_prior,
            "activity_combined_score_mean": round(float(activity["activity_combined_score_mean"]), 6),
            "activity_log10q_mean": round(float(activity["activity_log10q_mean"]), 6),
            "activity_overlap_effect_mean": round(float(activity["activity_overlap_effect_mean"]), 6),
            "activity_gene_effect_mean": round(float(activity["activity_gene_effect_mean"]), 6),
        })
    df = pd.DataFrame(rows)
    df.sort_values("final_score", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df
