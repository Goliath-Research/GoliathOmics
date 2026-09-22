"""
Mode 1C: CIS-BP motif enrichment over DMP/DMR genomic regions.

Scans differentially-methylated locus windows with CIS-BP PWMs, builds a TF -> region
GMT, and runs offline over-representation analysis against foreground DMP regions
(linked to the enricher gene list when ``gene_name`` is present in DMP exports).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .download import resolve_bundle
from .gene_sets import (
    _gmt_cache_key,
    build_tf_target_gmt_from_scan,
    load_gmt,
    run_gene_set_ora,
    write_gmt,
)
from .regions import (
    build_region_sequences,
    load_dmp_loci,
    partition_foreground_loci,
    resolve_dmp_csv_paths,
)

logger = logging.getLogger(__name__)


def resolve_region_gmt(
    cfg,
    *,
    cache_dir: Path,
    region_sequences: Dict[str, str],
    genome_fasta: str,
    dmp_sig: str,
) -> Tuple[Dict[str, List[str]], Path]:
    """Build or load cached TF -> DMP-region GMT for motif_scan."""
    key = _gmt_cache_key(
        {
            "mode": "motif_scan",
            "species": cfg.species,
            "build": cfg.build,
            "evidence": cfg.motif_evidence or ["Direct", "Inferred"],
            "flank": cfg.region_flank_bp,
            "threshold": cfg.motif_score_threshold,
            "min_regions": cfg.min_regions_per_tf,
            "max_regions": cfg.max_regions_per_tf,
            "fasta": str(genome_fasta),
            "dmp_sig": dmp_sig,
            "n_regions": len(region_sequences),
        }
    )
    gmt_path = Path(cache_dir) / "cisbp" / "gmt" / f"cisbp_dmp_regions_{key}.gmt"
    rebuild = getattr(cfg, "rebuild_region_gmt", None)
    if rebuild is None:
        rebuild = getattr(cfg, "rebuild_gmt", None)
    if gmt_path.is_file() and not rebuild:
        logger.info("[CIS-BP] using cached region GMT: %s", gmt_path)
        return load_gmt(gmt_path), gmt_path

    bundle = resolve_bundle(
        species=cfg.species or "Homo_sapiens",
        build=cfg.build or "3.10",
        data_dir=cfg.data_dir,
        cache_dir=str(cache_dir),
        auto_download=True if cfg.auto_download is None else bool(cfg.auto_download),
        base_url=cfg.base_url or "https://cisbp.ccbr.utoronto.ca",
        archive_url=cfg.archive_url,
    )
    gmt = build_tf_target_gmt_from_scan(
        bundle=bundle,
        promoter_sequences=region_sequences,
        motif_evidence=cfg.motif_evidence,
        score_threshold=float(cfg.motif_score_threshold or 0.85),
        min_targets_per_tf=int(cfg.min_regions_per_tf or 3),
        max_targets_per_tf=int(cfg.max_regions_per_tf or 5000),
    )
    write_gmt(gmt, gmt_path)
    return gmt, gmt_path


def _dmp_signature(csv_paths: Sequence[Path]) -> str:
    parts = []
    for p in sorted(csv_paths):
        st = p.stat()
        parts.append(f"{p}:{st.st_size}:{int(st.st_mtime)}")
    blob = "\n".join(parts).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:12]


def run(cfg, genes: Sequence[str], output_dir, context) -> Optional[str]:  # noqa: ANN001
    label = cfg.label or "CIS-BP"
    genome_fasta = cfg.genome_fasta or getattr(context, "genome_fasta", None)
    if not genome_fasta:
        raise ValueError(
            "CIS-BP motif_scan requires a genome FASTA. Set cisbp.genome_fasta or "
            "step_config.alignment_qc.genome_fasta on the project."
        )

    csv_paths = resolve_dmp_csv_paths(cfg, context)
    max_regions = int(cfg.max_dmp_regions or 5000)
    all_loci = load_dmp_loci(csv_paths, max_regions=max_regions)
    if not all_loci:
        logger.warning("[CIS-BP] no DMP loci loaded; skipping motif_scan")
        return None

    foreground_loci, pool_loci = partition_foreground_loci(all_loci, genes)
    if not foreground_loci:
        return None

    flank = int(cfg.region_flank_bp or 250)
    pool_sequences = build_region_sequences(
        genome_fasta=genome_fasta,
        loci=pool_loci,
        flank_bp=flank,
    )
    if not pool_sequences:
        logger.warning("[CIS-BP] no DMP region sequences; skipping motif_scan")
        return None

    gmt, _gmt_path = resolve_region_gmt(
        cfg,
        cache_dir=context.cache_dir,
        region_sequences=pool_sequences,
        genome_fasta=genome_fasta,
        dmp_sig=_dmp_signature(csv_paths),
    )

    foreground_ids = [
        loc.region_id for loc in foreground_loci if loc.region_id in pool_sequences
    ]
    if not foreground_ids:
        logger.warning("[CIS-BP] foreground DMP regions lack sequence; skipping motif_scan")
        return None

    background = cfg.background_size or context.background
    if background is None:
        background = max(len(pool_sequences), len(foreground_ids))

    n_terms, _path = run_gene_set_ora(
        genes=foreground_ids,
        gmt=gmt,
        output_dir=output_dir,
        label=label,
        background=background,
        cutoff=context.cutoff,
    )
    return label if n_terms > 0 else None
