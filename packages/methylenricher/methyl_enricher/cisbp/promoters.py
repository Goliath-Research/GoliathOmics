"""
Extract gene promoter sequences for CIS-BP motif scanning.

Promoter windows mirror MethylMapper's convention (TSS-relative, strand-aware):
``[TSS - upstream, TSS + downstream]``. Coordinates come from a user-supplied
GTF (the same annotation the mapper uses); sequences are fetched from a genome
FASTA via pyfaidx.
"""

from __future__ import annotations

import gzip
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_ATTR_GENE_NAME = re.compile(r'gene_name "([^"]+)"')
_ATTR_GENE_ID = re.compile(r'gene_id "([^"]+)"')


def _open_maybe_gzip(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    return open(path, "r", encoding="utf-8", errors="ignore")


def parse_gene_tss(
    gtf_path: Path,
    gene_universe: Optional[Set[str]] = None,
) -> Dict[str, Tuple[str, int, str]]:
    """
    Parse gene rows from a GTF into ``{gene_name: (chrom, tss, strand)}``.

    For genes appearing multiple times, the first ``gene`` feature wins. When
    ``gene_universe`` is given, only those gene names are kept.
    """
    universe = {g.strip() for g in gene_universe} if gene_universe else None
    out: Dict[str, Tuple[str, int, str]] = {}
    with _open_maybe_gzip(gtf_path) as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 9 or cols[2] != "gene":
                continue
            chrom, start, end, strand, attrs = cols[0], cols[3], cols[4], cols[6], cols[8]
            m = _ATTR_GENE_NAME.search(attrs) or _ATTR_GENE_ID.search(attrs)
            if not m:
                continue
            name = m.group(1).strip()
            if universe is not None and name not in universe:
                continue
            if name in out:
                continue
            try:
                s, e = int(start), int(end)
            except ValueError:
                continue
            tss = s if strand != "-" else e
            out[name] = (chrom, tss, strand)
    return out


def _norm_chrom_candidates(chrom: str) -> Iterable[str]:
    chrom = str(chrom).strip()
    if chrom.startswith("chr"):
        yield chrom
        yield chrom[3:]
    else:
        yield chrom
        yield f"chr{chrom}"


def build_promoter_sequences(
    *,
    gtf_path: str,
    genome_fasta: str,
    upstream: int = 5000,
    downstream: int = 200,
    gene_universe: Optional[Set[str]] = None,
) -> Dict[str, str]:
    """
    Return ``{gene_name: promoter_sequence}`` for genes found in both GTF + FASTA.

    Requires pyfaidx. Sequences are upper-cased; genes whose contig is absent
    from the FASTA are skipped (with a single aggregated warning).
    """
    try:
        from pyfaidx import Fasta
    except ImportError as exc:
        raise RuntimeError(
            "CIS-BP promoter scanning requires 'pyfaidx'. Install it (pip install pyfaidx) "
            "or use cisbp.gene_set_source='prebuilt_gmt'."
        ) from exc

    gtf_file = Path(gtf_path)
    if not gtf_file.is_file():
        raise FileNotFoundError(f"CIS-BP promoter scan: GTF not found: {gtf_path}")
    fasta_file = Path(genome_fasta)
    if not fasta_file.is_file():
        raise FileNotFoundError(
            f"CIS-BP promoter scan: genome FASTA not found: {genome_fasta}"
        )

    tss_by_gene = parse_gene_tss(gtf_file, gene_universe=gene_universe)
    logger.info("[CIS-BP] parsed %d gene TSS from GTF", len(tss_by_gene))

    fasta = Fasta(str(fasta_file), sequence_always_upper=True, rebuild=False)
    contigs = set(fasta.keys())

    sequences: Dict[str, str] = {}
    missing_contigs: Set[str] = set()
    for gene, (chrom, tss, strand) in tss_by_gene.items():
        contig = next((c for c in _norm_chrom_candidates(chrom) if c in contigs), None)
        if contig is None:
            missing_contigs.add(chrom)
            continue
        contig_len = len(fasta[contig])
        if strand != "-":
            start = max(0, tss - upstream)
            end = min(contig_len, tss + downstream)
        else:
            start = max(0, tss - downstream)
            end = min(contig_len, tss + upstream)
        if end <= start:
            continue
        seq = str(fasta[contig][start:end])
        if strand == "-":
            seq = _reverse_complement(seq)
        if seq:
            sequences[gene] = seq

    if missing_contigs:
        logger.warning(
            "[CIS-BP] %d gene contigs absent from FASTA (e.g. %s); those genes skipped",
            len(missing_contigs),
            ", ".join(sorted(missing_contigs)[:5]),
        )
    logger.info("[CIS-BP] built %d promoter sequences", len(sequences))
    return sequences


_COMPLEMENT = str.maketrans("ACGTNacgtn", "TGCANtgcan")


def _reverse_complement(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]
