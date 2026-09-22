"""
Pathway similarity graph and clustering: build a graph from pathway gene-set overlap
(Jaccard), cluster with Louvain (or fallback), and return pathway -> module assignments.
"""

import logging
import unicodedata
from typing import Dict, List, Set, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def canonical_pathway_key(term) -> str:
    """
    Stable key so the same pathway name from different Enrichr libraries (or minor
    spelling/casing differences) is always one graph node and one Louvain community unit.

    Normalization: NFKC, strip, collapse internal whitespace, casefold.
    """
    if term is None or (isinstance(term, float) and pd.isna(term)):
        return ""
    s = unicodedata.normalize("NFKC", str(term)).strip()
    if not s:
        return ""
    s = " ".join(s.split())
    return s.casefold()


def _parse_genes_cell(cell) -> Set[str]:
    """Parse Genes column from Enrichr (semicolon or comma separated, or list)."""
    if pd.isna(cell) or cell is None:
        return set()
    if isinstance(cell, list):
        return {str(g).strip().upper() for g in cell if g}
    s = str(cell).strip()
    if not s:
        return set()
    # Enrichr often returns semicolon- or comma-separated
    genes = set()
    for part in s.replace(";", ",").split(","):
        g = part.strip().upper()
        if g:
            genes.add(g)
    return genes


def pathway_gene_sets_from_merged(merged_df: pd.DataFrame) -> Dict[str, Set[str]]:
    """
    Build pathway -> set(genes) from merged enrichment DataFrame.
    Uses 'Term' for pathway name and 'Genes' (or 'Gene_set' for fallback name only) for genes.
    """
    term_col = "Term" if "Term" in merged_df.columns else None
    if not term_col:
        logger.warning("No 'Term' column in merged enrichment; cannot build pathway graph.")
        return {}
    # Enrichr results: 'Genes' = overlapping genes (semicolon-separated)
    genes_col = "Genes" if "Genes" in merged_df.columns else None
    if not genes_col:
        logger.warning("No 'Genes' column in merged enrichment; cannot build pathway graph.")
        return {}
    out: Dict[str, Set[str]] = {}
    for _, row in merged_df.iterrows():
        term = str(row[term_col]).strip()
        if not term:
            continue
        key = canonical_pathway_key(term)
        if not key:
            continue
        genes = _parse_genes_cell(row[genes_col])
        if key in out:
            out[key] = out[key] | genes
        else:
            out[key] = genes
    return out


def jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity |A ∩ B| / |A ∪ B|."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def overlap_coefficient(a: Set[str], b: Set[str]) -> float:
    """Overlap coefficient |A ∩ B| / min(|A|, |B|)."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b))


def build_similarity_graph(
    pathway_genes: Dict[str, Set[str]],
    similarity_threshold: float = 0.2,
    use_jaccard: bool = True,
):
    """
    Build networkx graph: nodes = pathways, edge if similarity > threshold.
    Returns (G, node_list) for use with community detection.
    """
    import networkx as nx
    nodes = list(pathway_genes.keys())
    G = nx.Graph()
    G.add_nodes_from(nodes)
    sim_fn = jaccard if use_jaccard else overlap_coefficient
    for i, p1 in enumerate(nodes):
        for p2 in nodes[i + 1 :]:
            sim = sim_fn(pathway_genes[p1], pathway_genes[p2])
            if sim >= similarity_threshold:
                G.add_edge(p1, p2, weight=sim)
    return G, nodes


def cluster_pathways_louvain(
    G,
    resolution: float = 0.8,
    random_state: int = 42,
) -> Dict[str, int]:
    """
    Run Louvain community detection. Returns pathway -> community_id (int).
    Singletons get their own community id.
    Lower resolution (e.g. 0.5-0.8) yields fewer, larger communities.
    """
    try:
        import community as community_louvain  # python-louvain
        partition = community_louvain.best_partition(
            G,
            resolution=resolution,
            random_state=random_state,
        )
        return partition
    except ImportError:
        logger.warning("python-louvain not installed; using connected components as fallback.")
        import networkx as nx
        comps = list(nx.connected_components(G))
        pathway_to_module = {}
        for mid, comp in enumerate(comps):
            for node in comp:
                pathway_to_module[node] = mid
        return pathway_to_module


def run_pathway_clustering(
    merged_df: pd.DataFrame,
    similarity_threshold: float = 0.15,
    use_jaccard: bool = True,
    cluster_resolution: float = 0.8,
    cluster_seed: int = 42,
) -> Tuple[Dict[str, int], Dict[str, Set[str]]]:
    """
    From merged enrichment DataFrame, build pathway graph and cluster into modules.
    Returns (pathway_to_module_id, pathway_to_genes).
    Keys are canonical pathway names (see canonical_pathway_key): duplicate Term strings
    across libraries or differing only by case/whitespace share one node and stay in
    the same module.
    pathway_to_genes: canonical key -> union of overlapping gene symbols.
    Lower similarity_threshold or lower cluster_resolution yields fewer, larger modules.
    """
    pathway_genes = pathway_gene_sets_from_merged(merged_df)
    if not pathway_genes:
        return {}, {}
    G, _ = build_similarity_graph(
        pathway_genes,
        similarity_threshold=similarity_threshold,
        use_jaccard=use_jaccard,
    )
    partition = cluster_pathways_louvain(
        G,
        resolution=cluster_resolution,
        random_state=cluster_seed,
    )
    return partition, pathway_genes
