"""5' end k-mer frequencies from aligned paired reads."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Set

import pysam


def compute_end_motifs(
    bam_path: Path,
    *,
    k: int = 4,
    chromosomes: Optional[Set[str]] = None,
    max_reads: Optional[int] = None,
) -> Dict[str, int]:
    """Count 5' sequenced k-mers at fragment starts (read1, forward orientation only)."""
    counts: Counter[str] = Counter()
    n = 0
    with pysam.AlignmentFile(str(bam_path), "rb") as bam:
        for read in bam.fetch(until_eof=True):
            if read.is_unmapped or read.is_secondary or read.is_supplementary:
                continue
            if read.is_read2:
                continue
            if chromosomes and read.reference_name not in chromosomes:
                continue
            seq = read.query_sequence
            if not seq or len(seq) < k:
                continue
            motif = seq[:k].upper()
            if "N" in motif:
                continue
            counts[motif] += 1
            n += 1
            if max_reads is not None and n >= max_reads:
                break
    return dict(counts)


def write_end_motifs_tsv(counts: Dict[str, int], out_path: Path) -> None:
    total = sum(counts.values()) or 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["motif\tcount\tfraction"]
    for motif, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"{motif}\t{count}\t{count / total:.6f}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_end_motifs(counts: Dict[str, int], k: int) -> Dict[str, object]:
    total = sum(counts.values())
    top = sorted(counts.items(), key=lambda x: -x[1])[:10]
    return {
        "end_motif_k": k,
        "total_fragments_scored": total,
        "unique_motifs": len(counts),
        "top_motifs": [{"motif": m, "count": c} for m, c in top],
    }
