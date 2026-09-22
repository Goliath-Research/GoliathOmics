"""
In-process biomarker gene pool for MC stability (PPI-only, no Enrichr).

Applies enricher-style CSV filters to mapper combined genes, optional region hits,
then optional STRING PPI hub ranking via methylenricher.ppi_network.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd
from methyl_utils.action_config_resolver import resolve_for_project

logger = logging.getLogger(__name__)

REGION_HITS_COLUMNS: Dict[str, str] = {
    "promoter": "hits_promoter",
    "exon": "hits_exon",
    "intron": "hits_intron",
    "gene_body": "hits_gene_body",
    "body": "hits_gene_body",
    "terminator": "hits_terminator",
}

BIOMARKER_PPI_HUBS_CSV = "biomarker_ppi_hubs.csv"


def normalize_region_hits(region_hits: Optional[Sequence[str]]) -> List[str]:
    if not region_hits:
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for raw in region_hits:
        token = str(raw or "").strip().lower()
        if token == "body":
            token = "gene_body"
        if not token or token in seen:
            continue
        if token not in REGION_HITS_COLUMNS:
            valid = ", ".join(sorted(REGION_HITS_COLUMNS))
            raise ValueError(f"Unknown stability_gene_region_hits token {raw!r}; valid: {valid}")
        seen.add(token)
        out.append(token)
    return out


def apply_region_hits_filter(
    df: pd.DataFrame,
    region_hits: Optional[Sequence[str]],
) -> pd.DataFrame:
    """Keep genes with hits_* > 0 for at least one requested region."""
    tokens = normalize_region_hits(region_hits)
    if not tokens or df.empty:
        return df
    work = df.copy()
    mask = pd.Series(False, index=work.index)
    for token in tokens:
        col = REGION_HITS_COLUMNS[token]
        if col not in work.columns:
            logger.warning("Region hits filter requested %s but column %s missing; skipping.", token, col)
            continue
        hits = pd.to_numeric(work[col], errors="coerce").fillna(0)
        mask = mask | (hits > 0)
    if not mask.any():
        return work.iloc[0:0].copy()
    return work[mask].copy()


def _resolve_enricher_filter_kwargs(enricher_config: Dict[str, Any]) -> Dict[str, Any]:
    """Map step_config.enricher keys to EnrichmentAnalyzer._apply_csv_filters kwargs."""
    assoc = enricher_config.get("disease_association_type")
    if assoc is not None and not isinstance(assoc, list):
        assoc = [assoc]
    network = enricher_config.get("network_refinement") or {}
    min_dmp_count = enricher_config.get("min_dmp_count")
    min_unique_dmps = enricher_config.get("min_unique_dmps")
    legacy_unique_dmps = enricher_config.get("unique_dmps")
    if legacy_unique_dmps is not None:
        if min_dmp_count is None and min_unique_dmps is None:
            logger.warning(
                "step_config.enricher.unique_dmps is not a valid filter key; "
                "using it as min_unique_dmps=%s. Prefer min_dmp_count or min_unique_dmps.",
                legacy_unique_dmps,
            )
            min_unique_dmps = legacy_unique_dmps
        else:
            logger.warning(
                "Ignoring step_config.enricher.unique_dmps (%s); "
                "use min_dmp_count or min_unique_dmps instead.",
                legacy_unique_dmps,
            )
    return {
        "disease_only": bool(enricher_config.get("disease_only", False)),
        "disease_column": str(enricher_config.get("disease_column") or "disease_associated"),
        "disease_association_types": assoc,
        "min_disease_evidence_level": enricher_config.get("min_disease_evidence_level"),
        "min_disease_publications": enricher_config.get("min_disease_publications"),
        "min_disease_score": enricher_config.get("min_disease_score"),
        "min_dmp_count": min_dmp_count,
        "min_unique_dmps": min_unique_dmps,
        "max_gene_q_value": enricher_config.get("max_gene_q_value"),
        "min_gene_z": enricher_config.get("min_gene_z"),
        "min_gene_importance": enricher_config.get("min_gene_importance"),
        "feature_types": enricher_config.get("feature_types"),
        "_network_refinement": network,
    }


def resolve_biomarker_ppi_cache_path(
    config: Any,
    enricher_config: Dict[str, Any],
) -> Optional[str]:
    explicit = getattr(config, "stability_gene_biomarker_ppi_cache_path", None)
    if explicit:
        return str(explicit)
    network = enricher_config.get("network_refinement") or {}
    cache = network.get("cache_path")
    return str(cache) if cache else None


def resolve_biomarker_ppi_score_threshold(enricher_config: Dict[str, Any]) -> float:
    network = enricher_config.get("network_refinement") or {}
    return float(network.get("score_threshold", 400.0))


def _sort_gene_importance(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "gene_importance" not in work.columns:
        work["gene_importance"] = 0.0
    work["gene_importance"] = pd.to_numeric(work["gene_importance"], errors="coerce").fillna(0.0)
    sort_cols = ["gene_importance"]
    ascending = [False]
    if "gene_support_n" in work.columns:
        work["gene_support_n"] = pd.to_numeric(work["gene_support_n"], errors="coerce").fillna(0)
        sort_cols.append("gene_support_n")
        ascending.append(False)
    if "gene_name" in work.columns:
        sort_cols.append("gene_name")
        ascending.append(True)
    return work.sort_values(sort_cols, ascending=ascending, na_position="last")


def _apply_csv_filters_silent(df: pd.DataFrame, filter_kwargs: Dict[str, Any]) -> pd.DataFrame:
    from methyl_enricher.enricher import EnrichmentAnalyzer

    kwargs = {k: v for k, v in filter_kwargs.items() if not k.startswith("_")}
    analyzer = EnrichmentAnalyzer()
    return analyzer._apply_csv_filters(df, **kwargs)


def _rank_ppi_hubs(
    genes: Sequence[str],
    gene_weights: Dict[str, float],
    *,
    cache_path: Optional[str],
    score_threshold: float,
    ppi_top_hubs: int,
    min_degree: int,
    hub_ranking_mode: str = "signal_weighted",
    disease_genes: Optional[Set[str]] = None,
    hub_disease_boost: float = 0.0,
) -> Tuple[List[str], pd.DataFrame, Dict[str, Any]]:
    from methyl_enricher.ppi_network import (
        attach_signal_to_node_metrics,
        build_ppi_graph,
        compute_network_metrics,
        fetch_string_edges,
        rank_hubs,
    )

    meta: Dict[str, Any] = {"ppi_fetch_attempted": True, "ppi_cache_path": cache_path}
    gene_list = [str(g).strip() for g in genes if str(g).strip()]
    if len(gene_list) < 2:
        meta["ppi_skip_reason"] = "fewer_than_two_genes"
        return list(gene_list), pd.DataFrame(), meta

    edges = fetch_string_edges(
        gene_list,
        required_score=float(score_threshold),
        cache_path=cache_path,
    )
    meta["n_string_edges"] = int(len(edges))
    graph = build_ppi_graph(edges, gene_list, min_component_size=1)
    node_metrics = compute_network_metrics(graph)
    if node_metrics.empty:
        meta["ppi_skip_reason"] = "empty_network_metrics"
        return list(gene_list[: int(ppi_top_hubs)]), pd.DataFrame(), meta

    if int(min_degree) > 0 and "degree" in node_metrics.columns:
        deg = pd.to_numeric(node_metrics["degree"], errors="coerce").fillna(0)
        node_metrics = node_metrics[deg >= int(min_degree)].copy()
        if node_metrics.empty:
            meta["ppi_skip_reason"] = "all_nodes_below_min_degree"
            return list(gene_list[: int(ppi_top_hubs)]), pd.DataFrame(), meta

    node_metrics = attach_signal_to_node_metrics(
        node_metrics,
        gene_weights,
        hub_ranking_mode=str(hub_ranking_mode),
        disease_genes=disease_genes,
        hub_disease_boost=float(hub_disease_boost),
    )
    hubs = rank_hubs(node_metrics, top_k=int(ppi_top_hubs), hub_ranking_mode=hub_ranking_mode)
    if hubs.empty or "gene" not in hubs.columns:
        meta["ppi_skip_reason"] = "no_hubs_ranked"
        return list(gene_list[: int(ppi_top_hubs)]), pd.DataFrame(), meta

    ordered = [str(g).strip() for g in hubs["gene"].tolist() if str(g).strip()]
    meta["n_ppi_hubs"] = int(len(ordered))
    return ordered, hubs, meta


def build_biomarker_gene_pool(
    mapper_gene_df: pd.DataFrame,
    *,
    enricher_config: Dict[str, Any],
    mode: str = "ppi_only",
    region_hits: Optional[Sequence[str]] = None,
    top_genes: int = 150,
    ppi_top_hubs: int = 100,
    min_degree: int = 1,
    cache_path: Optional[str] = None,
    score_threshold: float = 400.0,
) -> Tuple[List[str], pd.DataFrame, Dict[str, Any]]:
    """
    Build an ordered biomarker gene pool from mapper combined CSV.

    Returns (gene_symbols, ppi_hubs_df, metadata).
    """
    meta: Dict[str, Any] = {
        "mode": str(mode),
        "n_mapper_genes_input": int(len(mapper_gene_df)),
    }
    if mapper_gene_df.empty or "gene_name" not in mapper_gene_df.columns:
        meta["empty_reason"] = "empty_mapper_input"
        return [], pd.DataFrame(), meta

    filter_kwargs = _resolve_enricher_filter_kwargs(enricher_config)
    network_cfg = filter_kwargs.pop("_network_refinement", {}) or {}

    try:
        filtered = _apply_csv_filters_silent(mapper_gene_df, filter_kwargs)
    except ValueError as exc:
        meta["filter_error"] = str(exc)
        raise

    meta["n_after_csv_filters"] = int(len(filtered))
    filtered = apply_region_hits_filter(filtered, region_hits)
    meta["n_after_region_hits"] = int(len(filtered))
    meta["region_hits"] = normalize_region_hits(region_hits)

    if filtered.empty:
        meta["empty_reason"] = "no_genes_after_filters"
        return [], pd.DataFrame(), meta

    filtered = _sort_gene_importance(filtered)
    if int(top_genes) > 0 and len(filtered) > int(top_genes):
        filtered = filtered.head(int(top_genes)).copy()
    meta["n_after_top_genes_cap"] = int(len(filtered))

    gene_names = [str(g).strip() for g in filtered["gene_name"].tolist() if str(g).strip()]
    weights: Dict[str, float] = {}
    for _, row in filtered.iterrows():
        g = str(row.get("gene_name") or "").strip()
        if not g:
            continue
        imp = float(pd.to_numeric(row.get("gene_importance"), errors="coerce") or 0.0)
        weights[g.upper()] = imp

    mode_norm = str(mode or "ppi_only").strip().lower()
    use_ppi = mode_norm in {"ppi_only", "disease_and_ppi"}

    if not use_ppi:
        meta["ppi_fetch_attempted"] = False
        return gene_names, pd.DataFrame(), meta

    disease_genes: Optional[Set[str]] = None
    if bool(filter_kwargs.get("disease_only")) and "disease_associated" in filtered.columns:
        disease_genes = {
            str(row["gene_name"]).strip().upper()
            for _, row in filtered.iterrows()
            if str(row.get("disease_associated", "")).upper() in {"TRUE", "1", "YES"}
        }

    hub_mode = str(network_cfg.get("hub_ranking_mode") or "signal_weighted")
    hub_boost = float(network_cfg.get("hub_disease_boost") or 0.0)

    ordered, hubs_df, ppi_meta = _rank_ppi_hubs(
        gene_names,
        weights,
        cache_path=cache_path,
        score_threshold=float(score_threshold),
        ppi_top_hubs=int(ppi_top_hubs),
        min_degree=int(min_degree),
        hub_ranking_mode=hub_mode,
        disease_genes=disease_genes,
        hub_disease_boost=hub_boost,
    )
    meta.update(ppi_meta)
    if not ordered:
        meta["empty_reason"] = "ppi_pool_empty"
    return ordered, hubs_df, meta


def load_enricher_config_from_project(project_json: Any) -> Dict[str, Any]:
    from methyl_utils import load_project

    from .eval_split_resolver import _project_cwd

    path = project_json
    with _project_cwd(path):
        project = load_project(path)
    return dict(resolve_for_project("enricher", project))


def compute_biomarker_stability_diagnostics(monte_carlo_runs_root: Any) -> Dict[str, Any]:
    """Aggregate biomarker pool metrics from per-run gene_featurecuts_metrics.json files."""
    root = monte_carlo_runs_root
    if not hasattr(root, "glob"):
        root = __import__("pathlib").Path(root)
    pool_sizes: List[int] = []
    empty_runs = 0
    runs_with_biomarker = 0
    for run_dir in sorted(root.glob("run_*")):
        metrics_path = run_dir / "gene_stability" / "gene_featurecuts_metrics.json"
        if not metrics_path.is_file():
            continue
        try:
            import json

            with open(metrics_path, encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue
        bio = payload.get("biomarker_filter") or {}
        if not bio:
            continue
        runs_with_biomarker += 1
        size = bio.get("biomarker_pool_size")
        if size is not None:
            pool_sizes.append(int(size))
        if int(size or 0) == 0:
            empty_runs += 1

    diag: Dict[str, Any] = {
        "enabled": runs_with_biomarker > 0,
        "runs_with_biomarker_filter": int(runs_with_biomarker),
        "runs_empty_pool": int(empty_runs),
    }
    if pool_sizes:
        diag["median_pool_size"] = float(np.median(pool_sizes))
        diag["mean_pool_size"] = float(np.mean(pool_sizes))
        diag["min_pool_size"] = int(min(pool_sizes))
        diag["max_pool_size"] = int(max(pool_sizes))
    return diag
