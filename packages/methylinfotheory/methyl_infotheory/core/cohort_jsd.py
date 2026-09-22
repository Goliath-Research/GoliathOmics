"""Cohort-level Jensen–Shannon distance over read-level pattern tiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
from scipy.spatial.distance import jensenshannon

from methyl_utils.core.read_level_io import load_read_level_patterns

from ..config import InfoTheoryStepConfig


@dataclass(frozen=True)
class TileJsdRecord:
    chrom: str
    context: str
    tile_start_pos: int
    tile_cpg_positions: Tuple[int, ...]
    jsd: float
    n_reads_group1: int
    n_reads_group2: int


def _tile_key(chrom: str, context: str, start_pos: int, positions: Sequence[int]) -> Tuple:
    return (str(chrom), str(context), int(start_pos), tuple(int(p) for p in positions))


def _accumulate_group_histograms(
    sample_dirs: Sequence[str],
    chromosomes: Sequence[str],
    contexts: Sequence[str],
    *,
    min_cohort_reads: int,
) -> Dict[Tuple, np.ndarray]:
    """Return tile_key -> summed pattern count vector."""
    accum: Dict[Tuple, np.ndarray] = {}
    k_by_key: Dict[Tuple, int] = {}

    for sample_dir in sample_dirs:
        root = Path(sample_dir)
        for chrom in chromosomes:
            for ctx in contexts:
                path = root / f"{chrom}-{ctx}.patterns.h5"
                if not path.is_file():
                    continue
                try:
                    patterns = load_read_level_patterns(path)
                except Exception:
                    continue
                k = int(patterns.tile_size)
                n_patterns = 1 << k
                for tile_idx in range(patterns.n_tiles):
                    start = int(patterns.tile_start_pos[tile_idx])
                    positions = tuple(int(x) for x in patterns.tile_cpg_positions[tile_idx].tolist())
                    key = _tile_key(chrom, ctx, start, positions)
                    hist = patterns.tile_histogram(tile_idx)
                    if not hist:
                        continue
                    vec = np.zeros(n_patterns, dtype=np.float64)
                    for pid, count in hist.items():
                        if 0 <= int(pid) < n_patterns:
                            vec[int(pid)] += float(count)
                    if float(np.sum(vec)) < float(min_cohort_reads):
                        continue
                    if key not in accum:
                        accum[key] = vec
                        k_by_key[key] = k
                    else:
                        if accum[key].size != vec.size:
                            continue
                        accum[key] += vec
    return accum


def compute_cohort_jsd_records(
    group1_dirs: Sequence[str],
    group2_dirs: Sequence[str],
    *,
    chromosomes: Sequence[str],
    contexts: Sequence[str],
    cfg: InfoTheoryStepConfig,
) -> List[TileJsdRecord]:
    min_reads = int(cfg.jsd_min_cohort_reads) if cfg.jsd_min_cohort_reads is not None else 10
    g1 = _accumulate_group_histograms(
        group1_dirs, chromosomes, contexts, min_cohort_reads=min_reads
    )
    g2 = _accumulate_group_histograms(
        group2_dirs, chromosomes, contexts, min_cohort_reads=min_reads
    )
    records: List[TileJsdRecord] = []
    for key in sorted(set(g1) & set(g2)):
        chrom, ctx, start, positions = key
        v1 = g1[key]
        v2 = g2[key]
        s1 = float(np.sum(v1))
        s2 = float(np.sum(v2))
        if s1 < min_reads or s2 < min_reads:
            continue
        p1 = v1 / s1
        p2 = v2 / s2
        jsd = float(jensenshannon(p1, p2, base=2.0) ** 2)
        if not np.isfinite(jsd):
            continue
        records.append(
            TileJsdRecord(
                chrom=str(chrom),
                context=str(ctx),
                tile_start_pos=int(start),
                tile_cpg_positions=tuple(int(p) for p in positions),
                jsd=jsd,
                n_reads_group1=int(s1),
                n_reads_group2=int(s2),
            )
        )
    records.sort(key=lambda r: r.jsd, reverse=True)
    top_n = int(cfg.jsd_top_windows) if cfg.jsd_top_windows is not None else 100
    return records[:top_n]
