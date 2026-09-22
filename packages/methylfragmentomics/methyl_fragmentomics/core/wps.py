"""Binned fragment midpoint coverage (simplified WPS-style signal)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pysam


def compute_wps_bins(
    bam_path: Path,
    *,
    bin_bp: int = 1000,
    chromosomes: Optional[Set[str]] = None,
    max_reads: Optional[int] = None,
) -> Dict[Tuple[str, int], int]:
    """
    Count fragments per (chrom, bin_index) using paired insert size midpoint.

    bin_index = floor(midpoint / bin_bp).
    """
    bins: Dict[Tuple[str, int], int] = defaultdict(int)
    n = 0
    with pysam.AlignmentFile(str(bam_path), "rb") as bam:
        for read in bam.fetch(until_eof=True):
            if not read.is_read1 or read.is_unmapped or read.is_secondary or read.is_supplementary:
                continue
            chrom = read.reference_name
            if chromosomes and chrom not in chromosomes:
                continue
            mate = read.next_reference_name
            if mate != chrom:
                continue
            isize = abs(read.template_length)
            if isize <= 0 or isize > 2000:
                continue
            mid = int(read.reference_start) + isize // 2
            if mid < 0:
                continue
            bidx = mid // bin_bp
            bins[(chrom, bidx)] += 1
            n += 1
            if max_reads is not None and n >= max_reads:
                break
    return dict(bins)


def write_wps_tsv(
    bins: Dict[Tuple[str, int], int],
    out_path: Path,
    *,
    bin_bp: int,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["chrom\tbin_start\tbin_end\tfragment_count"]
    for (chrom, bidx), count in sorted(bins.items()):
        start = bidx * bin_bp
        end = start + bin_bp
        lines.append(f"{chrom}\t{start}\t{end}\t{count}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_wps(bins: Dict[Tuple[str, int], int], bin_bp: int) -> Dict[str, object]:
    if not bins:
        return {"wps_bin_bp": bin_bp, "n_bins": 0, "total_fragments": 0}
    total = sum(bins.values())
    counts = list(bins.values())
    mean_c = total / len(counts)
    return {
        "wps_bin_bp": bin_bp,
        "n_bins": len(bins),
        "total_fragments": total,
        "mean_fragments_per_bin": round(mean_c, 4),
        "max_bin_count": max(counts),
    }
