"""
Pathway-to-module pipeline: run enrichment, normalize pathway names, cluster pathways
into modules, score/rank modules, and write modules_ranked.csv.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .enricher import EnrichmentAnalyzer
from .enricher_completeness import is_cisbp_library_label
from .pathway_normalizer import PathwayNormalizer, load_theme_extras
from .pathway_graph import canonical_pathway_key, run_pathway_clustering
from .module_scorer import score_and_rank_modules
from . import module_network_plot
from .ppi_network import (
    attach_signal_to_node_metrics,
    build_ppi_graph,
    compute_module_coherence,
    compute_network_metrics,
    detect_communities,
    fetch_string_edges,
    load_local_edges,
    rank_hubs,
)

logger = logging.getLogger(__name__)

OVERLAP_GENES_CAP = 50
_VALID_MODULE_LABEL_MODES = {"canonical_only", "dual_label"}
_CANONICAL_LIBRARY_TOKENS = (
    "kegg",
    "reactome",
    "wikipathway",
    "go_biological_process",
    "go_molecular_function",
    "go_cellular_component",
    "msigdb_hallmark",
)
_DISEASE_LIBRARY_TOKENS = (
    "disgenet",
    "jensen_diseases",
    "gwas_catalog",
    "disease",
)
_PERTURBATION_LIBRARY_TOKENS = (
    "lincs",
    "dsigdb",
    "drugmatrix",
    "drug",
    "chem_pert",
    "geo_pert",
    "treatment",
)
_TF_LIBRARY_TOKENS = (
    "chea",
    "trrust",
    "chip",
    "transcription_factor",
    "cisbp",
    "cis_bp",
)


def _disease_relevance_tier(score: float) -> str:
    """Map disease relevance score to High/Medium/Low."""
    if score >= 0.4:
        return "High"
    if score >= 0.2:
        return "Medium"
    return "Low"


def _resolve_gene_column(df: pd.DataFrame, gene_column: Optional[str]) -> Optional[str]:
    """Resolve gene column name from explicit value or known candidates."""
    candidates = ["gene_name", "gene_id", "gene_symbol", "gene", "symbol"]
    if gene_column and gene_column in df.columns:
        return gene_column
    return next((c for c in candidates if c in df.columns), None)


def _derive_disease_prior_genes(
    input_path: Path,
    *,
    gene_column: Optional[str] = None,
    disease_only: bool = False,
    disease_association_types: Optional[List[str]] = None,
    min_disease_evidence_level: Optional[str] = None,
    min_disease_publications: Optional[int] = None,
    min_disease_score: Optional[float] = None,
) -> Set[str]:
    """
    Build disease prior genes from mapper-style disease columns when available.

    Priority:
    1) If explicit disease filters are provided, apply them.
    2) Else, if disease_associated exists, use disease_associated=True.
    3) Else, no disease prior.
    """
    path = Path(input_path)
    if path.suffix.lower() not in {".csv", ".tsv"} or not path.exists():
        return set()

    sep = "," if path.suffix.lower() == ".csv" else "\t"
    df = pd.read_csv(path, sep=sep)
    gc = _resolve_gene_column(df, gene_column)
    if gc is None:
        return set()

    disease_columns = {
        "disease_associated",
        "disease_association_type",
        "disease_evidence_level",
        "disease_publications",
        "disease_score",
    }
    has_disease_info = any(c in df.columns for c in disease_columns)
    if not has_disease_info:
        return set()

    work = df.copy()
    has_explicit_filters = any(
        [
            disease_only,
            bool(disease_association_types),
            min_disease_evidence_level is not None,
            min_disease_publications is not None,
            min_disease_score is not None,
        ]
    )

    if "disease_associated" in work.columns and (disease_only or not has_explicit_filters):
        keep = work["disease_associated"].astype(str).str.strip().str.upper().isin(("TRUE", "1", "YES"))
        work = work[keep]

    if disease_association_types and "disease_association_type" in work.columns:
        allowed = {str(s).strip().lower() for s in disease_association_types}
        work = work[
            work["disease_association_type"].astype(str).str.strip().str.lower().isin(allowed)
        ]

    if min_disease_evidence_level is not None and "disease_evidence_level" in work.columns:
        level_order = {"none": 0, "low": 1, "medium": 2, "high": 3}
        min_level = level_order.get(str(min_disease_evidence_level).lower(), 0)
        levels = work["disease_evidence_level"].astype(str).str.strip().str.lower().map(
            lambda x: level_order.get(x, -1)
        )
        work = work[levels >= min_level]

    if min_disease_publications is not None and "disease_publications" in work.columns:
        pubs = pd.to_numeric(work["disease_publications"], errors="coerce").fillna(0)
        work = work[pubs >= int(min_disease_publications)]

    if min_disease_score is not None and "disease_score" in work.columns:
        scores = pd.to_numeric(work["disease_score"], errors="coerce").fillna(0.0)
        work = work[scores >= float(min_disease_score)]

    if work.empty:
        return set()

    genes = {
        g.strip()
        for g in work[gc].dropna().astype(str).tolist()
        if str(g).strip()
    }
    return genes


def _derive_mapper_gene_effects(
    input_path: Path,
    *,
    gene_column: Optional[str] = None,
) -> Dict[str, float]:
    """
    Build gene->effect lookup from mapper combined CSV for activity tracking.
    """
    path = Path(input_path)
    if path.suffix.lower() not in {".csv", ".tsv"} or not path.exists():
        return {}
    sep = "," if path.suffix.lower() == ".csv" else "\t"
    df = pd.read_csv(path, sep=sep)
    gc = _resolve_gene_column(df, gene_column)
    if gc is None:
        return {}
    effect_col = None
    for cand in ("gene_importance", "gene_effect_abs_wsum", "gene_effect_size"):
        if cand in df.columns:
            effect_col = cand
            break
    if effect_col is None:
        return {}
    work = df[[gc, effect_col]].copy()
    work[gc] = work[gc].astype(str).str.strip()
    work = work[work[gc] != ""]
    work[effect_col] = pd.to_numeric(work[effect_col], errors="coerce").fillna(0.0).abs()
    agg = (
        work.groupby(gc, as_index=False)[effect_col]
        .max()
        .sort_values(effect_col, ascending=False)
    )
    return {
        str(row[gc]).strip().upper(): float(row[effect_col])
        for _, row in agg.iterrows()
        if str(row[gc]).strip()
    }


def _collapse_modules_by_theme(
    df: pd.DataFrame,
    *,
    include_disease_columns: bool,
    module_label_mode: str = "dual_label",
) -> pd.DataFrame:
    """
    Collapse module rows by Module_theme so each theme appears once.

    Keeps the highest scoring representative statistics while merging human-readable
    gene/pathway summaries and counting how many clusters contributed to the theme.
    """
    if df.empty or "Module_theme" not in df.columns:
        return df

    rows = []
    for theme, sub in df.groupby("Module_theme", sort=False):
        sub = sub.sort_values("Score", ascending=False).reset_index(drop=True)

        def _tokens_from_csv(col: str, top_k: int) -> str:
            vals = []
            seen = set()
            for raw in sub[col].dropna().astype(str):
                for token in [p.strip() for p in raw.split(",") if p.strip()]:
                    if token not in seen:
                        seen.add(token)
                        vals.append(token)
            return ", ".join(vals[:top_k])

        row0 = sub.iloc[0]
        supporting_vals = []
        seen_supporting: Set[str] = set()
        if "Module_supporting_perturbation" in sub.columns:
            for raw in sub["Module_supporting_perturbation"].dropna().astype(str):
                for token in [p.strip() for p in raw.split(";") if p.strip()]:
                    key = canonical_pathway_key(token)
                    if key and key not in seen_supporting:
                        seen_supporting.add(key)
                        supporting_vals.append(token)
        supporting = "; ".join(supporting_vals[:3])
        variant_family = (
            f"{str(theme)} | perturbation_evidence"
            if supporting
            else str(theme)
        )
        row = {
            "Module": str(theme),
            "Module_primary": str(theme),
            "Module_variant_family": variant_family,
            "Module_supporting_perturbation": supporting,
            "Module_display": _build_module_display(
                str(theme),
                supporting,
                mode=module_label_mode,
            ),
            "Module_theme": str(theme),
            "Theme_cluster_count": int(len(sub)),
            "Score": float(row0["Score"]),
            "Base_score": float(row0["Base_score"]),
            "PPI_coherence_score": float(row0["PPI_coherence_score"]),
            "Blended_score": float(row0["Blended_score"]),
            "Main_genes": _tokens_from_csv("Main_genes", top_k=10),
            "Overlap_genes": _tokens_from_csv("Overlap_genes", top_k=OVERLAP_GENES_CAP),
            "Main_pathways": _tokens_from_csv("Main_pathways", top_k=5),
            "Main_theme": str(row0["Main_theme"]),
            "module_type": "core" if (sub["module_type"] == "core").any() else "candidate",
            "n_pathways": int(pd.to_numeric(sub["n_pathways"], errors="coerce").fillna(0).sum()),
            "n_genes": int(pd.to_numeric(sub["n_genes"], errors="coerce").fillna(0).max()),
        }
        for col in (
            "Activity_combined_score_mean",
            "Activity_log10q_mean",
            "Activity_overlap_effect_mean",
            "Activity_gene_effect_mean",
        ):
            if col in sub.columns:
                row[col] = float(pd.to_numeric(sub[col], errors="coerce").dropna().mean())
        if include_disease_columns:
            disease_score = float(
                pd.to_numeric(sub["Disease_relevance_score"], errors="coerce").dropna().max()
            )
            row["Disease_relevance_score"] = round(disease_score, 4)
            row["Disease_relevance_tier"] = _disease_relevance_tier(disease_score)
        rows.append(row)

    out = pd.DataFrame(rows)
    out.sort_values(by=["Score", "n_genes", "n_pathways"], ascending=[False, False, False], inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


def _reduce_terms_for_clustering(
    merged_df: pd.DataFrame,
    *,
    max_q: Optional[float] = None,
    top_terms_per_library: Optional[int] = None,
) -> pd.DataFrame:
    """
    Reduce enrichment terms before pathway clustering.

    This keeps enrichment exports untouched while allowing module construction
    to operate on a more focused term set when large library presets are used.
    """
    if merged_df.empty:
        return merged_df

    work = merged_df.copy()
    if "library" in work.columns:
        work = work[~work["library"].map(is_cisbp_library_label)].copy()
    q_col = "Adjusted P-value" if "Adjusted P-value" in work.columns else None

    if max_q is not None and q_col is not None:
        q = pd.to_numeric(work[q_col], errors="coerce")
        work = work[q <= max_q].copy()

    if top_terms_per_library is not None and top_terms_per_library > 0 and "library" in work.columns:
        sort_cols: List[str] = []
        ascending: List[bool] = []
        if q_col is not None:
            work["_adj_q"] = pd.to_numeric(work[q_col], errors="coerce").fillna(1.0)
            sort_cols.append("_adj_q")
            ascending.append(True)
        if "P-value" in work.columns:
            work["_pval"] = pd.to_numeric(work["P-value"], errors="coerce").fillna(1.0)
            sort_cols.append("_pval")
            ascending.append(True)
        if "Combined Score" in work.columns:
            work["_combined_score"] = pd.to_numeric(work["Combined Score"], errors="coerce").fillna(0.0)
            sort_cols.append("_combined_score")
            ascending.append(False)

        if sort_cols:
            work = work.sort_values(sort_cols, ascending=ascending)
        work = (
            work.groupby("library", as_index=False, group_keys=False)
            .head(top_terms_per_library)
            .copy()
        )

    drop_cols = [c for c in ["_adj_q", "_pval", "_combined_score"] if c in work.columns]
    if drop_cols:
        work = work.drop(columns=drop_cols)
    return work


def _resolve_library_column(df: pd.DataFrame) -> Optional[str]:
    for name in ("library", "Library", "Gene_set", "gene_set"):
        if name in df.columns:
            return name
    return None


def _library_category(name: object) -> str:
    token = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not token:
        return "unknown"
    if any(t in token for t in _CANONICAL_LIBRARY_TOKENS):
        return "canonical"
    if any(t in token for t in _DISEASE_LIBRARY_TOKENS):
        return "disease"
    if any(t in token for t in _PERTURBATION_LIBRARY_TOKENS):
        return "perturbation"
    if any(t in token for t in _TF_LIBRARY_TOKENS):
        return "tf"
    return "other"


def _annotate_library_categories(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    lib_col = _resolve_library_column(out)
    if lib_col is None:
        out["_library_name"] = ""
        out["_library_category"] = "unknown"
        return out
    out["_library_name"] = out[lib_col].astype(str)
    out["_library_category"] = out["_library_name"].map(_library_category)
    return out


def _module_rows_by_pathways(pathways: List[str], merged_df: pd.DataFrame) -> pd.DataFrame:
    if not pathways or "Term" not in merged_df.columns:
        return pd.DataFrame(columns=list(merged_df.columns))
    key_set = set(pathways)
    ck = merged_df["Term"].map(canonical_pathway_key)
    return merged_df[ck.isin(key_set)].copy()


def _theme_from_terms(terms: List[str], normalizer: PathwayNormalizer) -> str:
    if not terms:
        return "Other"
    themes = [normalizer.normalize(str(t)) for t in terms if str(t).strip()]
    if not themes:
        return "Other"
    from collections import Counter

    return Counter(themes).most_common(1)[0][0]


def _module_primary_theme(
    pathways: List[str],
    merged_df: pd.DataFrame,
    normalizer: PathwayNormalizer,
) -> str:
    module_rows = _module_rows_by_pathways(pathways, merged_df)
    if module_rows.empty:
        return _theme_from_terms(pathways, normalizer)
    canonical_terms = (
        module_rows[module_rows["_library_category"] == "canonical"]["Term"].astype(str).tolist()
        if "_library_category" in module_rows.columns
        else []
    )
    if canonical_terms:
        return _theme_from_terms(canonical_terms, normalizer)
    return _theme_from_terms(module_rows["Term"].astype(str).tolist(), normalizer)


def _module_supporting_perturbation(pathways: List[str], merged_df: pd.DataFrame, *, top_k: int = 3) -> str:
    module_rows = _module_rows_by_pathways(pathways, merged_df)
    if module_rows.empty or "_library_category" not in module_rows.columns:
        return ""
    pert = module_rows[module_rows["_library_category"] == "perturbation"].copy()
    if pert.empty:
        return ""
    if "Adjusted P-value" in pert.columns:
        pert["_q"] = pd.to_numeric(pert["Adjusted P-value"], errors="coerce").fillna(1.0)
        pert.sort_values("_q", ascending=True, inplace=True)
    seen: Set[str] = set()
    terms: List[str] = []
    for term in pert["Term"].astype(str).tolist():
        key = canonical_pathway_key(term)
        if not key or key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= max(1, int(top_k)):
            break
    return "; ".join(terms)


def _build_module_display(primary: str, supporting: str, *, mode: str) -> str:
    if str(mode).strip().lower() == "dual_label" and supporting:
        return f"{primary} | {supporting}"
    return primary


def _module_variant_family(primary: str, pathways: List[str], merged_df: pd.DataFrame, *, top_k: int = 3) -> str:
    """
    Stable variant key for cross-stage tracking.

    Uses canonical primary label plus perturbation library evidence buckets
    instead of raw perturbation term strings (which are stage-volatile).
    """
    module_rows = _module_rows_by_pathways(pathways, merged_df)
    if module_rows.empty or "_library_category" not in module_rows.columns:
        return primary
    pert = module_rows[module_rows["_library_category"] == "perturbation"].copy()
    if pert.empty:
        return primary
    lib_col = "_library_name" if "_library_name" in pert.columns else _resolve_library_column(pert)
    if not lib_col:
        return f"{primary} | perturbation_evidence"
    # Deterministic ordering is required for a stable cross-stage key.
    pert["_lib_key"] = pert[lib_col].astype(str).map(canonical_pathway_key)
    pert["_lib_token"] = pert[lib_col].astype(str).str.strip().str.lower()
    if "Adjusted P-value" in pert.columns:
        pert["_q"] = pd.to_numeric(pert["Adjusted P-value"], errors="coerce").fillna(1.0)
        pert.sort_values(["_q", "_lib_key", "_lib_token"], ascending=[True, True, True], inplace=True)
    else:
        pert.sort_values(["_lib_key", "_lib_token"], ascending=[True, True], inplace=True)
    libs: List[str] = []
    seen: Set[str] = set()
    for raw in pert[lib_col].astype(str).tolist():
        token = str(raw).strip()
        if not token:
            continue
        key = canonical_pathway_key(token)
        if not key or key in seen:
            continue
        seen.add(key)
        libs.append(token)
        if len(libs) >= max(1, int(top_k)):
            break
    if not libs:
        return f"{primary} | perturbation_evidence"
    return f"{primary} | {' + '.join(libs)}"


def _overlap_genes_str(module_genes: Set[str], cap: int = OVERLAP_GENES_CAP) -> str:
    """Comma-separated overlap genes for the module, optionally capped for readability."""
    genes = sorted(module_genes)
    if len(genes) > cap:
        genes = genes[:cap]
    return ", ".join(genes)


def _module_label_from_themes(pathways: List[str], normalizer: PathwayNormalizer) -> str:
    """Assign module label as the most frequent theme among pathways in the module."""
    if not pathways:
        return "Other"
    themes = [normalizer.normalize(p) for p in pathways]
    from collections import Counter
    counts = Counter(themes)
    return counts.most_common(1)[0][0]


def _main_genes_for_module(
    module_genes: Set[str],
    gene_weights: Dict[str, float],
    top_k: int = 10,
) -> str:
    """Top genes in module by weight, comma-separated."""
    if not module_genes:
        return ""
    weight_lookup = {k.upper(): v for k, v in (gene_weights or {}).items()}
    sorted_genes = sorted(
        module_genes,
        key=lambda g: weight_lookup.get(g.upper(), 1.0),
        reverse=True,
    )
    return ", ".join(sorted_genes[:top_k])


def _main_pathways_for_module(
    module_pathways: List[str],
    merged_df: pd.DataFrame,
    top_k: int = 5,
) -> str:
    """Top pathway names in module by adjusted p-value (most significant first)."""
    if not module_pathways or "Term" not in merged_df.columns or "Adjusted P-value" not in merged_df.columns:
        return ""
    key_set = set(module_pathways)
    ck = merged_df["Term"].map(canonical_pathway_key)
    sub = merged_df[ck.isin(key_set)].copy()
    sub["_q"] = pd.to_numeric(sub["Adjusted P-value"], errors="coerce").fillna(1.0)
    sub = sub.sort_values("_q", ascending=True)
    # One representative Term per canonical key (best q), then top_k keys
    seen_keys: Set[str] = set()
    terms: List[str] = []
    for t in sub["Term"].astype(str):
        k = canonical_pathway_key(t)
        if not k or k in seen_keys:
            continue
        seen_keys.add(k)
        terms.append(t)
        if len(terms) >= top_k:
            break
    return "; ".join(terms)


def _display_term_by_canonical_key(merged_df: pd.DataFrame) -> Dict[str, str]:
    """Map canonical_pathway_key -> best (lowest q) original Term for exports."""
    if "Term" not in merged_df.columns:
        return {}
    df = merged_df.copy()
    df["_pk"] = df["Term"].map(canonical_pathway_key)
    df = df[df["_pk"].astype(str).str.len() > 0]
    if df.empty:
        return {}
    if "Adjusted P-value" in df.columns:
        df["_q"] = pd.to_numeric(df["Adjusted P-value"], errors="coerce").fillna(1.0)
        df = df.sort_values("_q", ascending=True)
    out: Dict[str, str] = {}
    for _, row in df.iterrows():
        pk = row["_pk"]
        if pk not in out:
            out[str(pk)] = str(row["Term"])
    return out


def run_ppi_hubs_only(
    input_path: Path,
    output_dir: Path,
    *,
    gene_column: Optional[str] = None,
    top_n: Optional[int] = None,
    disease_only: bool = False,
    disease_association_types: Optional[List[str]] = None,
    min_disease_evidence_level: Optional[str] = None,
    min_disease_publications: Optional[int] = None,
    min_disease_score: Optional[float] = None,
    min_dmp_count: Optional[int] = None,
    min_unique_dmps: Optional[int] = None,
    max_gene_q_value: Optional[float] = None,
    min_gene_z: Optional[float] = None,
    min_gene_importance: Optional[float] = None,
    feature_types: Optional[List[str]] = None,
    sort_by: Optional[str] = None,
    sort_ascending: bool = False,
    top_k_hubs: int = 25,
    network_refinement_source: str = "string_api",
    network_refinement_local_edges_file: Optional[str] = None,
    network_refinement_cache_path: Optional[str] = None,
    network_refinement_score_threshold: float = 400.0,
    network_refinement_community_method: str = "louvain",
    network_refinement_min_component_size: int = 2,
    network_refinement_hub_ranking_mode: str = "signal_weighted",
    network_refinement_hub_disease_boost: float = 0.0,
    network_refinement_hub_w_degree: Optional[float] = None,
    network_refinement_hub_w_betweenness: Optional[float] = None,
    network_refinement_hub_w_closeness: Optional[float] = None,
    network_refinement_species: int = 9606,
    organism: str = "Human",
    disease_genes: Optional[Set[str]] = None,
) -> pd.DataFrame:
    """PPI-only hub extraction: no Enrichr libraries, no pathway modules.

    Builds the STRING PPI graph directly over the top ``gene_importance``-ranked
    mapper genes and scores hubs with the same ``signal_weighted`` recipe as the
    full enricher (topology x normalized ``gene_importance``). Writes
    ``ppi_hubs.csv`` / ``ppi_node_metrics.csv`` / ``ppi_network_edges.csv``.

    This is intentionally kept separate from the full module pipeline: it never
    mixes Enrichr pathway scores with PPI hub scores, so per-run hubs stay
    score-comparable, and it is far faster (one STRING query + centralities, no
    per-library Enrichr calls).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    analyzer = EnrichmentAnalyzer(libraries=[], organism=organism)
    genes, gene_weights = analyzer.load_gene_list_with_weights(
        input_path,
        top_n=top_n,
        gene_column=gene_column,
        disease_only=disease_only,
        disease_association_types=disease_association_types,
        min_disease_evidence_level=min_disease_evidence_level,
        min_disease_publications=min_disease_publications,
        min_disease_score=min_disease_score,
        min_dmp_count=min_dmp_count,
        min_unique_dmps=min_unique_dmps,
        max_gene_q_value=max_gene_q_value,
        min_gene_z=min_gene_z,
        min_gene_importance=min_gene_importance,
        feature_types=feature_types,
        sort_by=sort_by,
        sort_ascending=sort_ascending,
    )
    if len(genes) < 2:
        logger.warning("PPI-only: fewer than 2 genes after filtering; no hubs written.")
        pd.DataFrame(columns=["gene"]).to_csv(output_dir / "ppi_hubs.csv", index=False)
        return pd.DataFrame()

    if network_refinement_source == "local_edges":
        if not network_refinement_local_edges_file:
            raise ValueError(
                "network_refinement_local_edges_file is required when source=local_edges"
            )
        edges_df = load_local_edges(network_refinement_local_edges_file)
        edges_df = edges_df[
            pd.to_numeric(edges_df["score"], errors="coerce").fillna(0.0)
            >= float(network_refinement_score_threshold)
        ].copy()
    else:
        edges_df = fetch_string_edges(
            genes=genes,
            species=int(network_refinement_species),
            required_score=float(network_refinement_score_threshold),
            cache_path=network_refinement_cache_path,
        )

    graph = build_ppi_graph(
        edges_df=edges_df,
        genes=genes,
        min_component_size=int(network_refinement_min_component_size),
    )
    node_metrics_df = compute_network_metrics(graph)
    wd = network_refinement_hub_w_degree
    wb = network_refinement_hub_w_betweenness
    wc = network_refinement_hub_w_closeness
    if wd is None and wb is None and wc is None:
        topology_blend = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    else:
        topology_blend = (
            float(wd if wd is not None else 1.0 / 3.0),
            float(wb if wb is not None else 1.0 / 3.0),
            float(wc if wc is not None else 1.0 / 3.0),
        )
    node_metrics_df = attach_signal_to_node_metrics(
        node_metrics_df,
        gene_weights,
        hub_ranking_mode=network_refinement_hub_ranking_mode,
        disease_genes=disease_genes,
        hub_disease_boost=float(network_refinement_hub_disease_boost),
        topology_blend=topology_blend,
    )
    communities = detect_communities(
        graph, method=network_refinement_community_method
    )
    if not node_metrics_df.empty:
        node_metrics_df["community_id"] = (
            node_metrics_df["gene"].astype(str).str.upper().map(communities)
        )

    edges_df.to_csv(output_dir / "ppi_network_edges.csv", index=False)
    node_metrics_df.to_csv(output_dir / "ppi_node_metrics.csv", index=False)
    hubs = rank_hubs(
        node_metrics_df,
        top_k=int(top_k_hubs),
        hub_ranking_mode=network_refinement_hub_ranking_mode,
    )
    hubs.to_csv(output_dir / "ppi_hubs.csv", index=False)
    logger.info(
        "PPI-only: wrote %s (%d hubs from %d nodes, %d edges).",
        output_dir / "ppi_hubs.csv",
        len(hubs),
        len(node_metrics_df),
        len(edges_df),
    )
    return hubs


def run_cisbp_only(
    input_path: Path,
    output_dir: Path,
    *,
    cisbp: object = None,
    cisbp_context: object = None,
    gene_column: Optional[str] = None,
    top_n: Optional[int] = None,
    disease_only: bool = False,
    disease_association_types: Optional[List[str]] = None,
    min_disease_evidence_level: Optional[str] = None,
    min_disease_publications: Optional[int] = None,
    min_disease_score: Optional[float] = None,
    min_dmp_count: Optional[int] = None,
    min_unique_dmps: Optional[int] = None,
    max_gene_q_value: Optional[float] = None,
    min_gene_z: Optional[float] = None,
    min_gene_importance: Optional[float] = None,
    feature_types: Optional[List[str]] = None,
    sort_by: Optional[str] = None,
    sort_ascending: bool = False,
    organism: str = "Human",
) -> pd.DataFrame:
    """CIS-BP-only TF-motif enrichment: no Enrichr libraries, no PPI, no modules.

    Selects the top ``gene_importance``-ranked mapper genes and runs only the
    configured CIS-BP mode(s), writing the CIS-BP CSV(s) to ``output_dir``. Kept
    separate from the Enrichr path because CIS-BP scores are not comparable to
    Enrichr q-values; this lets callers request CIS-BP without paying for a full
    multi-library enrichment run.

    Returns a DataFrame with one row per emitted CIS-BP merge label (column
    ``cisbp_label``), so the caller's ``.empty`` check works uniformly with the
    other enrichment entry points; empty means nothing was produced.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if cisbp is None or not getattr(cisbp, "enabled", False):
        logger.warning("CIS-BP-only requested but CIS-BP config is disabled/None; nothing to do.")
        return pd.DataFrame(columns=["cisbp_label"])

    analyzer = EnrichmentAnalyzer(libraries=[], organism=organism)
    genes, _weights = analyzer.load_gene_list_with_weights(
        input_path,
        top_n=top_n,
        gene_column=gene_column,
        disease_only=disease_only,
        disease_association_types=disease_association_types,
        min_disease_evidence_level=min_disease_evidence_level,
        min_disease_publications=min_disease_publications,
        min_disease_score=min_disease_score,
        min_dmp_count=min_dmp_count,
        min_unique_dmps=min_unique_dmps,
        max_gene_q_value=max_gene_q_value,
        min_gene_z=min_gene_z,
        min_gene_importance=min_gene_importance,
        feature_types=feature_types,
        sort_by=sort_by,
        sort_ascending=sort_ascending,
    )
    if not genes:
        logger.warning("CIS-BP-only: no genes after filtering; nothing to do.")
        return pd.DataFrame(columns=["cisbp_label"])

    from .cisbp import CisbpContext, run_cisbp

    context = cisbp_context or CisbpContext()
    result = run_cisbp(cisbp, genes, output_dir, context=context)
    labels = (
        [str(x) for x in result if x]
        if isinstance(result, list)
        else ([str(result)] if result else [])
    )
    logger.info(
        "CIS-BP-only: wrote %d gene(s) of CIS-BP output to %s (labels=%s).",
        len(genes),
        output_dir,
        labels or "none",
    )
    return pd.DataFrame({"cisbp_label": labels})


def run_module_pipeline(
    input_path: Path,
    output_dir: Path,
    *,
    gene_column: Optional[str] = None,
    top_n: int = 200,
    libraries: Optional[List[str]] = None,
    organism: str = "Human",
    cutoff: float = 0.05,
    disease_only: bool = False,
    disease_association_types: Optional[List[str]] = None,
    min_disease_evidence_level: Optional[str] = None,
    min_disease_publications: Optional[int] = None,
    min_disease_score: Optional[float] = None,
    min_dmp_count: Optional[int] = None,
    min_unique_dmps: Optional[int] = None,
    max_gene_q_value: Optional[float] = None,
    min_gene_z: Optional[float] = None,
    min_gene_importance: Optional[float] = None,
    feature_types: Optional[List[str]] = None,
    sort_by: Optional[str] = None,
    sort_ascending: bool = False,
    similarity_threshold: float = 0.15,
    cluster_resolution: float = 0.8,
    cluster_seed: int = 42,
    module_cluster_max_q: Optional[float] = None,
    module_cluster_top_terms_per_library: Optional[int] = None,
    module_label_mode: str = "dual_label",
    disease_genes: Optional[Set[str]] = None,
    network_plot: Optional[str] = None,
    network_refinement_enabled: bool = False,
    network_refinement_source: str = "string_api",
    network_refinement_local_edges_file: Optional[str] = None,
    network_refinement_cache_path: Optional[str] = None,
    network_refinement_score_threshold: float = 400.0,
    network_refinement_community_method: str = "louvain",
    network_refinement_min_component_size: int = 2,
    network_refinement_weight_in_final_score: float = 0.3,
    network_refinement_hub_ranking_mode: str = "signal_weighted",
    network_refinement_hub_disease_boost: float = 0.0,
    network_refinement_hub_w_degree: Optional[float] = None,
    network_refinement_hub_w_betweenness: Optional[float] = None,
    network_refinement_hub_w_closeness: Optional[float] = None,
    network_refinement_species: int = 9606,
    dash_host: str = "127.0.0.1",
    dash_port: int = 8050,
    dash_open_browser: bool = False,
    cisbp: Optional[object] = None,
    cisbp_context: Optional[object] = None,
) -> pd.DataFrame:
    """
    Run the full pathway-to-module pipeline (Steps A–E) and write modules_ranked.csv.
    Returns the modules table (DataFrame).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    label_mode = str(module_label_mode or "dual_label").strip().lower()
    if label_mode not in _VALID_MODULE_LABEL_MODES:
        valid = ", ".join(sorted(_VALID_MODULE_LABEL_MODES))
        raise ValueError(f"module_label_mode must be one of: {valid}")

    analyzer_kwargs = {}
    if cisbp is not None:
        analyzer_kwargs["cisbp"] = cisbp
        analyzer_kwargs["cisbp_context"] = cisbp_context
    analyzer = EnrichmentAnalyzer(
        libraries=libraries,
        organism=organism,
        cutoff=cutoff,
        **analyzer_kwargs,
    )
    genes, gene_weights = analyzer.load_gene_list_with_weights(
        input_path,
        top_n=top_n,
        gene_column=gene_column,
        disease_only=disease_only,
        disease_association_types=disease_association_types,
        min_disease_evidence_level=min_disease_evidence_level,
        min_disease_publications=min_disease_publications,
        min_disease_score=min_disease_score,
        min_dmp_count=min_dmp_count,
        min_unique_dmps=min_unique_dmps,
        max_gene_q_value=max_gene_q_value,
        min_gene_z=min_gene_z,
        min_gene_importance=min_gene_importance,
        feature_types=feature_types,
        sort_by=sort_by,
        sort_ascending=sort_ascending,
    )
    if not genes:
        logger.warning("No genes loaded; cannot run module pipeline.")
        return pd.DataFrame()

    merged_df = analyzer.run_enrichment(genes, output_dir, gene_weights=gene_weights)
    if merged_df.empty:
        logger.warning("No enrichment results; cannot build modules.")
        return pd.DataFrame()
    merged_df = _annotate_library_categories(merged_df)

    clustering_df = _reduce_terms_for_clustering(
        merged_df,
        max_q=module_cluster_max_q,
        top_terms_per_library=module_cluster_top_terms_per_library,
    )
    if clustering_df.empty:
        logger.warning(
            "Term reduction produced zero rows for module clustering "
            f"(module_cluster_max_q={module_cluster_max_q}, "
            f"module_cluster_top_terms_per_library={module_cluster_top_terms_per_library})."
        )
        return pd.DataFrame()
    if len(clustering_df) < len(merged_df):
        logger.info(
            "Reduced terms for module clustering: %d -> %d rows",
            len(merged_df),
            len(clustering_df),
        )

    pathway_to_module_id, pathway_to_genes = run_pathway_clustering(
        clustering_df,
        similarity_threshold=similarity_threshold,
        use_jaccard=True,
        cluster_resolution=cluster_resolution,
        cluster_seed=cluster_seed,
    )
    if not pathway_to_module_id:
        logger.warning("Pathway clustering produced no modules.")
        return pd.DataFrame()

    # Build disease prior from mapper-style disease columns unless explicitly provided.
    if disease_genes is None:
        disease_genes = _derive_disease_prior_genes(
            input_path,
            gene_column=gene_column,
            disease_only=disease_only,
            disease_association_types=disease_association_types,
            min_disease_evidence_level=min_disease_evidence_level,
            min_disease_publications=min_disease_publications,
            min_disease_score=min_disease_score,
        )
    if disease_genes:
        logger.info("Disease prior active for module scoring (%d genes).", len(disease_genes))
    else:
        logger.info("No disease prior detected; module scoring will not include disease columns.")
    gene_effects = _derive_mapper_gene_effects(
        input_path,
        gene_column=gene_column,
    )
    if gene_effects:
        logger.info("Mapper gene-effect activity lookup active (%d genes).", len(gene_effects))

    normalizer = PathwayNormalizer()
    _, theme_descriptions = load_theme_extras()

    ppi_coherence_by_module: Dict[int, float] = {}
    ppi_dash_elements: Optional[List[Dict]] = None
    ppi_dash_stylesheet: Optional[List[Dict]] = None
    if network_refinement_enabled:
        try:
            if network_refinement_source == "local_edges":
                if not network_refinement_local_edges_file:
                    raise ValueError(
                        "network_refinement_local_edges_file is required when source=local_edges"
                    )
                edges_df = load_local_edges(network_refinement_local_edges_file)
                edges_df = edges_df[
                    pd.to_numeric(edges_df["score"], errors="coerce").fillna(0.0)
                    >= float(network_refinement_score_threshold)
                ].copy()
            else:
                edges_df = fetch_string_edges(
                    genes=genes,
                    species=int(network_refinement_species),
                    required_score=float(network_refinement_score_threshold),
                    cache_path=network_refinement_cache_path,
                )

            network_genes = sorted(
                {
                    g.upper()
                    for genes_set in pathway_to_genes.values()
                    for g in genes_set
                    if str(g).strip()
                }
            )
            ppi_graph = build_ppi_graph(
                edges_df=edges_df,
                genes=network_genes,
                min_component_size=int(network_refinement_min_component_size),
            )
            node_metrics_df = compute_network_metrics(ppi_graph)
            wd = network_refinement_hub_w_degree
            wb = network_refinement_hub_w_betweenness
            wc = network_refinement_hub_w_closeness
            if wd is None and wb is None and wc is None:
                topology_blend = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
            else:
                topology_blend = (
                    float(wd if wd is not None else 1.0 / 3.0),
                    float(wb if wb is not None else 1.0 / 3.0),
                    float(wc if wc is not None else 1.0 / 3.0),
                )
            node_metrics_df = attach_signal_to_node_metrics(
                node_metrics_df,
                gene_weights,
                hub_ranking_mode=network_refinement_hub_ranking_mode,
                disease_genes=disease_genes,
                hub_disease_boost=float(network_refinement_hub_disease_boost),
                topology_blend=topology_blend,
            )
            coherence_metric_column = (
                "combined_hub_score"
                if str(network_refinement_hub_ranking_mode).lower() == "signal_weighted"
                and "combined_hub_score" in node_metrics_df.columns
                else "degree_centrality"
            )
            communities = detect_communities(
                ppi_graph,
                method=network_refinement_community_method,
            )
            if not node_metrics_df.empty:
                node_metrics_df["community_id"] = (
                    node_metrics_df["gene"].astype(str).str.upper().map(communities)
                )
            module_coherence_df = compute_module_coherence(
                pathway_to_module_id=pathway_to_module_id,
                pathway_to_genes=pathway_to_genes,
                graph=ppi_graph,
                node_metrics=node_metrics_df,
                coherence_metric_column=coherence_metric_column,
            )

            ppi_coherence_by_module = {
                int(r["module_id"]): float(r["ppi_coherence_score"])
                for _, r in module_coherence_df.iterrows()
            }

            # Optional PPI dataset for Dash Cytoscape visualization
            if ppi_graph.number_of_nodes() > 0:
                for node in ppi_graph.nodes():
                    cid = int(communities.get(node, -1))
                    ppi_graph.nodes[node]["module_id"] = cid
                    ppi_graph.nodes[node]["module_label"] = f"Community {cid}" if cid >= 0 else "Other"
                    ppi_graph.nodes[node]["n_genes"] = int(ppi_graph.degree(node))
                ppi_payload = module_network_plot.build_cytoscape_payload_from_graph(
                    ppi_graph,
                    layout="spring",
                    include_positions=True,
                    show_labels=True,
                )
                ppi_dash_elements = ppi_payload.get("elements")
                ppi_dash_stylesheet = ppi_payload.get("stylesheet")

            edges_path = output_dir / "ppi_network_edges.csv"
            edges_df.to_csv(edges_path, index=False)
            logger.info("Wrote %s with %d edges.", edges_path, len(edges_df))

            node_metrics_path = output_dir / "ppi_node_metrics.csv"
            node_metrics_df.to_csv(node_metrics_path, index=False)
            logger.info("Wrote %s with %d nodes.", node_metrics_path, len(node_metrics_df))

            hubs_path = output_dir / "ppi_hubs.csv"
            rank_hubs(
                node_metrics_df,
                top_k=25,
                hub_ranking_mode=network_refinement_hub_ranking_mode,
            ).to_csv(hubs_path, index=False)
            logger.info("Wrote %s.", hubs_path)

            module_coherence_path = output_dir / "ppi_module_coherence.csv"
            module_coherence_df.to_csv(module_coherence_path, index=False)
            logger.info("Wrote %s with %d modules.", module_coherence_path, len(module_coherence_df))
        except Exception as exc:
            logger.warning(
                "Network refinement failed, falling back to baseline module score: %s",
                exc,
            )

    score_df = score_and_rank_modules(
        pathway_to_module_id,
        pathway_to_genes,
        clustering_df,
        gene_weights=gene_weights,
        gene_effects=gene_effects,
        disease_genes=disease_genes,
        ppi_coherence_by_module=ppi_coherence_by_module,
        ppi_weight_in_final_score=(
            float(network_refinement_weight_in_final_score)
            if network_refinement_enabled
            else 0.0
        ),
    )

    out_rows = []
    for _, row in score_df.iterrows():
        mid = row["module_id"]
        pathways = [p for p, m in pathway_to_module_id.items() if m == mid]
        module_genes = set()
        for p in pathways:
            module_genes |= pathway_to_genes.get(p, set())
        label = _module_primary_theme(pathways, clustering_df, normalizer)
        module_name = f"{label} (M{int(mid)})"
        supporting_perturbation = _module_supporting_perturbation(pathways, clustering_df)
        module_display = _build_module_display(label, supporting_perturbation, mode=label_mode)
        module_variant_family = _module_variant_family(label, pathways, clustering_df)
        main_genes = _main_genes_for_module(module_genes, gene_weights, top_k=10)
        main_pathways = _main_pathways_for_module(pathways, clustering_df, top_k=5)
        overlap_genes = _overlap_genes_str(module_genes)
        n_genes = row["n_genes"]
        module_type = "candidate" if n_genes <= 2 else "core"
        main_theme = theme_descriptions.get(label, label)
        out_rows.append({
            "Module": module_name,
            "Module_primary": label,
            "Module_variant_family": module_variant_family,
            "Module_supporting_perturbation": supporting_perturbation,
            "Module_display": module_display,
            "Module_theme": label,
            "Module_id": int(mid),
            "Score": round(row["final_score"], 4),
            "Base_score": round(row.get("base_score", row["final_score"]), 4),
            "PPI_coherence_score": round(row.get("ppi_coherence_score", 0.0), 4),
            "Blended_score": round(row.get("blended_score", row["final_score"]), 4),
            "Main_genes": main_genes,
            "Overlap_genes": overlap_genes,
            "Main_pathways": main_pathways,
            "Main_theme": main_theme,
            "module_type": module_type,
            "n_pathways": row["n_pathways"],
            "n_genes": n_genes,
            "Activity_combined_score_mean": round(float(row.get("activity_combined_score_mean", 0.0)), 6),
            "Activity_log10q_mean": round(float(row.get("activity_log10q_mean", 0.0)), 6),
            "Activity_overlap_effect_mean": round(float(row.get("activity_overlap_effect_mean", 0.0)), 6),
            "Activity_gene_effect_mean": round(float(row.get("activity_gene_effect_mean", 0.0)), 6),
        })
        if bool(row.get("has_disease_prior", False)):
            out_rows[-1]["Disease_relevance_score"] = round(float(row.get("disease_relevance")), 4)
            out_rows[-1]["Disease_relevance_tier"] = row.get("disease_relevance_tier", "Medium")

    out_df = pd.DataFrame(out_rows)
    # Rank purely by final score so output order matches the reported score.
    out_df.sort_values(
        by=["Score", "n_genes", "n_pathways"],
        ascending=[False, False, False],
        inplace=True,
    )
    out_df.reset_index(drop=True, inplace=True)
    include_disease_columns = bool(
        "Disease_relevance_score" in out_df.columns and out_df["Disease_relevance_score"].notna().any()
    )
    if not include_disease_columns:
        out_df = out_df.drop(
            columns=["Disease_relevance_score", "Disease_relevance_tier"],
            errors="ignore",
        )
    # Keep detailed per-cluster output for debugging/traceability.
    detailed_path = output_dir / "modules_ranked_detailed.csv"
    out_df.to_csv(detailed_path, index=False)
    logger.info(f"Wrote {detailed_path} with {len(out_df)} module clusters.")

    # Primary output: one row per normalized theme (what users typically want to review).
    collapsed_df = _collapse_modules_by_theme(
        out_df,
        include_disease_columns=include_disease_columns,
        module_label_mode=label_mode,
    )
    out_path = output_dir / "modules_ranked.csv"
    collapsed_df.to_csv(out_path, index=False)
    logger.info(
        "Wrote %s with %d merged themes (from %d clusters).",
        out_path,
        len(collapsed_df),
        len(out_df),
    )

    # Per-pathway overlap genes (which genes drive each pathway)
    if pathway_to_genes:
        display_by_key = _display_term_by_canonical_key(clustering_df)
        pathway_overlap = [
            {
                "Pathway": display_by_key.get(term, term),
                "Pathway_key": term,
                "Overlap_genes": ", ".join(sorted(genes)),
            }
            for term, genes in sorted(pathway_to_genes.items())
        ]
        if pathway_overlap:
            pathway_df = pd.DataFrame(pathway_overlap)
            pathway_out = output_dir / "pathway_overlap_genes.csv"
            pathway_df.to_csv(pathway_out, index=False)
            logger.info(f"Wrote {pathway_out} with {len(pathway_df)} pathways.")

    # Network plot (pathway similarity graph) when requested
    if network_plot and network_plot.lower() != "none":
        module_id_to_label = {}
        for mid in set(pathway_to_module_id.values()):
            pathways_in_module = [p for p, m in pathway_to_module_id.items() if m == mid]
            module_id_to_label[mid] = _module_primary_theme(pathways_in_module, clustering_df, normalizer)
        module_network_plot.write_network_plots(
            output_dir=output_dir,
            pathway_to_module_id=pathway_to_module_id,
            pathway_to_genes=pathway_to_genes,
            merged_df=clustering_df,
            module_id_to_label=module_id_to_label,
            similarity_threshold=similarity_threshold,
            network_plot=network_plot,
            ppi_elements=ppi_dash_elements,
            ppi_stylesheet=ppi_dash_stylesheet,
            dash_host=dash_host,
            dash_port=dash_port,
            dash_open_browser=dash_open_browser,
        )

    return collapsed_df
