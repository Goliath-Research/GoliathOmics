"""
Mode 1A: CIS-BP TF -> target-gene over-representation analysis.

Builds a TF -> target-gene mapping (a GMT) either from a prebuilt file or by
scanning gene promoter sequences with CIS-BP PWMs, caches it, then runs offline
over-representation analysis (gseapy) against the enricher's gene list. The
output ``enrich_<label>.csv`` matches the Enrichr per-library schema so it
merges into ``enrichment_merged.csv`` and flows through the module pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

from ..enricher_completeness import sanitize_enrichment_df
from . import pwm as pwm_mod
from .download import resolve_bundle

logger = logging.getLogger(__name__)

_ENRICH_COLUMNS = [
    "Term",
    "Overlap",
    "P-value",
    "Adjusted P-value",
    "Odds Ratio",
    "Combined Score",
    "Genes",
]


def _gmt_cache_key(params: dict) -> str:
    blob = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def load_gmt(path: Path) -> Dict[str, List[str]]:
    """Load a GMT file (term<TAB>description<TAB>gene1<TAB>gene2...)."""
    gmt: Dict[str, List[str]] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            # Keep column positions intact (description may be intentionally empty in GMT).
            parts = [p.strip() for p in line.rstrip("\n").split("\t")]
            if len(parts) < 3:
                continue
            term = parts[0]
            genes = [g for g in parts[2:] if g]
            if genes:
                gmt[term] = genes
    return gmt


def write_gmt(gmt: Dict[str, List[str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for term, genes in gmt.items():
            fh.write("\t".join([term, "CIS-BP", *genes]) + "\n")


def build_tf_target_gmt_from_scan(
    *,
    bundle,
    promoter_sequences: Dict[str, str],
    motif_evidence: Optional[List[str]] = None,
    score_threshold: float = 0.85,
    min_targets_per_tf: int = 5,
    max_targets_per_tf: int = 3000,
    progress_every: int = 250,
) -> Dict[str, List[str]]:
    """
    Scan promoter sequences with CIS-BP PWMs and aggregate TF -> target genes.

    A gene is a target of a TF if any of that TF's motifs scores at/above
    ``score_threshold`` (relative log-odds in [0,1]) in the gene promoter.
    """
    tf_info = bundle.load_tf_info(motif_evidence=motif_evidence)
    if tf_info.empty:
        logger.warning("[CIS-BP] no TF motifs after evidence filter; empty GMT")
        return {}

    # Pre-encode promoter sequences once.
    encoded = {g: pwm_mod._encode_seq(s) for g, s in promoter_sequences.items()}
    genes = list(encoded.keys())

    # Map motif_id -> set of TF names (a motif can map to multiple TFs).
    motif_to_tfs: Dict[str, Set[str]] = {}
    for _, row in tf_info.iterrows():
        mid = str(row["Motif_ID"]).strip()
        tf = str(row["TF_Name"]).strip()
        if mid and tf:
            motif_to_tfs.setdefault(mid, set()).add(tf)

    tf_targets: Dict[str, Set[str]] = {}
    n_motifs = len(motif_to_tfs)
    scanned = 0
    for mid, tfs in motif_to_tfs.items():
        scanned += 1
        pwm_path = bundle.pwm_dir / f"{mid}.txt"
        if not pwm_path.is_file():
            continue
        mat = pwm_mod.load_pwm(pwm_path)
        if mat is None:
            continue
        log_odds = pwm_mod.to_log_odds(mat)
        hits = [g for g in genes if pwm_mod.best_relative_score(log_odds, encoded[g]) >= score_threshold]
        if not hits:
            continue
        for tf in tfs:
            tf_targets.setdefault(tf, set()).update(hits)
        if progress_every and scanned % progress_every == 0:
            logger.info("[CIS-BP] scanned %d/%d motifs", scanned, n_motifs)

    gmt: Dict[str, List[str]] = {}
    for tf, targets in tf_targets.items():
        if len(targets) < min_targets_per_tf:
            continue
        target_list = sorted(targets)
        if max_targets_per_tf and len(target_list) > max_targets_per_tf:
            target_list = target_list[:max_targets_per_tf]
        gmt[tf] = target_list
    logger.info("[CIS-BP] built GMT: %d TFs (from %d motifs)", len(gmt), n_motifs)
    return gmt


def resolve_gmt(
    cfg,
    *,
    cache_dir: Path,
    gene_universe: Optional[Set[str]] = None,
    gtf: Optional[str] = None,
    genome_fasta: Optional[str] = None,
) -> Tuple[Dict[str, List[str]], Path]:
    """
    Resolve the TF -> target GMT for mode 1A, using cache when possible.

    Returns ``(gmt_dict, gmt_path)``.
    """
    source = (cfg.gene_set_source or "promoter_scan").strip().lower()

    if source == "prebuilt_gmt":
        if not cfg.gmt_path:
            raise ValueError(
                "cisbp.gene_set_source='prebuilt_gmt' requires cisbp.gmt_path."
            )
        gmt_path = Path(cfg.gmt_path)
        if not gmt_path.is_file():
            raise FileNotFoundError(f"CIS-BP prebuilt GMT not found: {gmt_path}")
        return load_gmt(gmt_path), gmt_path

    if source != "promoter_scan":
        raise ValueError(
            f"Unknown cisbp.gene_set_source '{cfg.gene_set_source}'. "
            f"Use 'promoter_scan' or 'prebuilt_gmt'."
        )

    gtf = gtf or cfg.gtf
    genome_fasta = genome_fasta or cfg.genome_fasta
    if not gtf or not genome_fasta:
        raise ValueError(
            "cisbp.gene_set_source='promoter_scan' requires a GTF and a genome FASTA. "
            "Set cisbp.gtf + cisbp.genome_fasta (or rely on the project's mapper GTF / "
            "GENE_GTF env var), or switch to gene_set_source='prebuilt_gmt'."
        )

    universe_sig = None
    if gene_universe:
        universe_sig = hashlib.sha1(
            "\n".join(sorted(gene_universe)).encode("utf-8")
        ).hexdigest()[:12]

    key = _gmt_cache_key(
        {
            "species": cfg.species,
            "build": cfg.build,
            "evidence": cfg.motif_evidence or ["Direct", "Inferred"],
            "upstream": cfg.promoter_upstream,
            "downstream": cfg.promoter_downstream,
            "threshold": cfg.motif_score_threshold,
            "min_targets": cfg.min_targets_per_tf,
            "max_targets": cfg.max_targets_per_tf,
            "gtf": str(gtf),
            "fasta": str(genome_fasta),
            "universe": universe_sig,
        }
    )
    gmt_path = Path(cache_dir) / "cisbp" / "gmt" / f"cisbp_tf_targets_{key}.gmt"
    if gmt_path.is_file() and not cfg.rebuild_gmt:
        logger.info("[CIS-BP] using cached GMT: %s", gmt_path)
        return load_gmt(gmt_path), gmt_path

    from .promoters import build_promoter_sequences

    bundle = resolve_bundle(
        species=cfg.species or "Homo_sapiens",
        build=cfg.build or "3.10",
        data_dir=cfg.data_dir,
        cache_dir=str(cache_dir),
        auto_download=True if cfg.auto_download is None else bool(cfg.auto_download),
        base_url=cfg.base_url or "https://cisbp.ccbr.utoronto.ca",
        archive_url=cfg.archive_url,
    )
    promoter_sequences = build_promoter_sequences(
        gtf_path=gtf,
        genome_fasta=genome_fasta,
        upstream=int(cfg.promoter_upstream or 5000),
        downstream=int(cfg.promoter_downstream or 200),
        gene_universe=gene_universe,
    )
    gmt = build_tf_target_gmt_from_scan(
        bundle=bundle,
        promoter_sequences=promoter_sequences,
        motif_evidence=cfg.motif_evidence,
        score_threshold=float(cfg.motif_score_threshold or 0.85),
        min_targets_per_tf=int(cfg.min_targets_per_tf or 5),
        max_targets_per_tf=int(cfg.max_targets_per_tf or 3000),
    )
    write_gmt(gmt, gmt_path)
    return gmt, gmt_path


def run_gene_set_ora(
    *,
    genes: Sequence[str],
    gmt: Dict[str, List[str]],
    output_dir: Path,
    label: str = "CIS-BP",
    background: Optional[object] = None,
    cutoff: float = 0.05,
) -> Tuple[int, Path]:
    """
    Run offline over-representation analysis against ``gmt`` and write
    ``enrich_<label>.csv`` in the Enrichr per-library schema.

    Returns ``(n_terms, csv_path)``. ``n_terms == 0`` means nothing was written.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"enrich_{label}.csv"

    if not gmt:
        logger.warning("[CIS-BP] empty GMT; no enrichment to run")
        return 0, out_path
    gene_list = [str(g).strip() for g in genes if str(g).strip()]
    if not gene_list:
        logger.warning("[CIS-BP] empty gene list; skipping ORA")
        return 0, out_path

    import gseapy as gp

    if background is None:
        background = 20000

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        enr = gp.enrich(
            gene_list=gene_list,
            gene_sets=gmt,
            background=background,
            outdir=None,
            cutoff=1.0,
            no_plot=True,
            verbose=False,
        )
    results = getattr(enr, "results", None)
    if results is None or results.empty:
        logger.warning("[CIS-BP] ORA returned no overlapping terms")
        return 0, out_path

    df = sanitize_enrichment_df(results.copy())
    for col in _ENRICH_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    df["library"] = label
    df.to_csv(out_path, index=False)
    logger.info("[CIS-BP] wrote %d terms -> %s", len(df), out_path)
    return len(df), out_path
