"""
DMP locus loading and sequence extraction for CIS-BP motif_scan (mode 1C).

Reads per-chromosome DMP CSV exports (discovery branch for enricher by default),
builds strand-neutral windows around each CpG position, and fetches sequence from
the project genome FASTA.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

from .promoters import _norm_chrom_candidates

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DmpLocus:
    """One differentially methylated position with a stable region identifier."""

    region_id: str
    chrom: str
    position: int
    gene_name: Optional[str] = None


def make_region_id(chrom: str, position: int) -> str:
    chrom = str(chrom).strip()
    return f"{chrom}:{int(position)}"


def resolve_dmp_csv_paths(cfg, context) -> List[Path]:  # noqa: ANN001
    """
    Resolve DMP CSV paths from ``cisbp.dmp_csv_path``, ``cisbp.dmp_detection_dir``,
    or ``context.dmp_detection_dir``.
    """
    explicit = getattr(cfg, "dmp_csv_path", None)
    if explicit:
        path = Path(str(explicit))
        if path.is_file():
            return [path]
        raise FileNotFoundError(f"CIS-BP motif_scan: dmp_csv_path not found: {path}")

    detection_dir = getattr(cfg, "dmp_detection_dir", None) or getattr(
        context, "dmp_detection_dir", None
    )
    if not detection_dir:
        raise ValueError(
            "CIS-BP motif_scan requires DMP inputs. Set cisbp.dmp_csv_path, "
            "cisbp.dmp_detection_dir, or run from a project so the detection output "
            "directory can be resolved per comparison."
        )
    det = Path(str(detection_dir))
    if not det.is_dir():
        raise FileNotFoundError(
            f"CIS-BP motif_scan: dmp_detection_dir not found: {detection_dir}"
        )

    source = (getattr(cfg, "dmp_source", None) or "discovery").strip().lower()
    try:
        from methyl_utils.dmp_export_paths import (
            find_classifier_dmps_csvs,
            find_discovery_dmps_csvs,
        )
    except ImportError as exc:
        raise RuntimeError(
            "CIS-BP motif_scan requires methyl_utils.dmp_export_paths."
        ) from exc

    if source == "classifier":
        paths = find_classifier_dmps_csvs(det)
    elif source in ("discovery", "auto", ""):
        paths = find_discovery_dmps_csvs(det)
    else:
        raise ValueError(
            f"Unknown cisbp.dmp_source '{getattr(cfg, 'dmp_source', None)}'. "
            "Use 'discovery' or 'classifier'."
        )
    if not paths:
        raise FileNotFoundError(
            f"CIS-BP motif_scan: no DMP CSV files under {det} (source={source})"
        )
    return paths


def _read_dmp_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    chrom_col = cols.get("chromosome") or cols.get("chrom")
    pos_col = cols.get("position") or cols.get("pos")
    if not chrom_col or not pos_col:
        raise ValueError(
            f"CIS-BP motif_scan: {path} must include chromosome and position columns"
        )
    gene_col = cols.get("gene_name") or cols.get("gene")
    out = pd.DataFrame(
        {
            "chrom": df[chrom_col].astype(str).str.strip(),
            "position": pd.to_numeric(df[pos_col], errors="coerce"),
        }
    )
    if gene_col:
        out["gene_name"] = df[gene_col].astype(str).str.strip()
    out = out.dropna(subset=["position"])
    out["position"] = out["position"].astype(int)
    return out


def load_dmp_loci(
    csv_paths: Sequence[Path],
    *,
    max_regions: Optional[int] = None,
) -> List[DmpLocus]:
    """Load and de-duplicate DMP loci from one or more per-chromosome CSV files."""
    seen: Set[str] = set()
    loci: List[DmpLocus] = []
    for path in csv_paths:
        df = _read_dmp_csv(path)
        for row in df.itertuples(index=False):
            chrom = row.chrom
            pos = int(row.position)
            rid = make_region_id(chrom, pos)
            if rid in seen:
                continue
            seen.add(rid)
            gene = getattr(row, "gene_name", None)
            gene_s = str(gene).strip() if gene and str(gene).strip() not in ("", "nan") else None
            loci.append(DmpLocus(region_id=rid, chrom=chrom, position=pos, gene_name=gene_s))
            if max_regions and len(loci) >= max_regions:
                logger.warning(
                    "[CIS-BP] capped DMP loci at max_dmp_regions=%d", max_regions
                )
                return loci
    logger.info("[CIS-BP] loaded %d unique DMP loci from %d CSV(s)", len(loci), len(csv_paths))
    return loci


def partition_foreground_loci(
    loci: Sequence[DmpLocus],
    genes: Sequence[str],
) -> Tuple[List[DmpLocus], List[DmpLocus]]:
    """
    Split loci into foreground (tested in ORA) and the full pool (ORA background).

    When any locus carries ``gene_name``, foreground loci are those whose gene is in
    ``genes``. Otherwise all loci are treated as foreground (typical when DMP tables
    lack gene annotation).
    """
    gene_set = {str(g).strip() for g in genes if str(g).strip()}
    has_gene = any(loc.gene_name for loc in loci)
    if has_gene and gene_set:
        fg = [loc for loc in loci if loc.gene_name and loc.gene_name in gene_set]
        if not fg:
            logger.warning(
                "[CIS-BP] no DMP loci matched the enricher gene list (%d genes); "
                "skipping motif_scan ORA",
                len(gene_set),
            )
        return fg, list(loci)
    if has_gene and not gene_set:
        logger.warning("[CIS-BP] empty gene list; skipping motif_scan ORA")
        return [], list(loci)
    if not has_gene:
        logger.info(
            "[CIS-BP] DMP tables lack gene_name; using all %d loci as foreground",
            len(loci),
        )
    return list(loci), list(loci)


def build_region_sequences(
    *,
    genome_fasta: str,
    loci: Sequence[DmpLocus],
    flank_bp: int = 250,
) -> Dict[str, str]:
    """
    Fetch ``flank_bp`` bases on each side of each DMP (2*flank_bp total when in bounds).
    """
    try:
        from pyfaidx import Fasta
    except ImportError as exc:
        raise RuntimeError(
            "CIS-BP motif_scan requires 'pyfaidx'. Install it (pip install pyfaidx)."
        ) from exc

    fasta_file = Path(genome_fasta)
    if not fasta_file.is_file():
        raise FileNotFoundError(
            f"CIS-BP motif_scan: genome FASTA not found: {genome_fasta}"
        )

    fasta = Fasta(str(fasta_file), sequence_always_upper=True, rebuild=False)
    contigs = set(fasta.keys())
    flank = max(0, int(flank_bp))
    sequences: Dict[str, str] = {}
    missing_contigs: Set[str] = set()

    for loc in loci:
        contig = next(
            (c for c in _norm_chrom_candidates(loc.chrom) if c in contigs), None
        )
        if contig is None:
            missing_contigs.add(loc.chrom)
            continue
        contig_len = len(fasta[contig])
        pos = loc.position
        start = max(0, pos - flank)
        end = min(contig_len, pos + flank)
        if end <= start:
            continue
        seq = str(fasta[contig][start:end])
        if seq:
            sequences[loc.region_id] = seq

    if missing_contigs:
        logger.warning(
            "[CIS-BP] %d DMP contigs absent from FASTA (e.g. %s)",
            len(missing_contigs),
            ", ".join(sorted(missing_contigs)[:5]),
        )
    logger.info("[CIS-BP] built %d DMP region sequences", len(sequences))
    return sequences
