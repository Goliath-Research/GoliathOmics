"""Guo/mHap-style MHB discovery and locked-BED load (vectorized over haplotype store)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from methyl_utils.core.mhap_io import MhapStore, load_mhap_store


def load_bed_intervals(path: str | Path) -> List[Tuple[str, int, int, str]]:
    rows: List[Tuple[str, int, int, str]] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.startswith(("#", "track", "browser")):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            name = parts[3] if len(parts) > 3 else f"{parts[0]}:{parts[1]}-{parts[2]}"
            rows.append((parts[0], int(parts[1]), int(parts[2]), name))
    return rows


def _site_matrix(stores: Sequence[MhapStore]) -> Tuple[np.ndarray, np.ndarray]:
    """Return sorted unique positions and (n_reads, n_sites) meth matrix with NaN missing."""
    all_pos = np.unique(np.concatenate([s.cpg_pos for s in stores if s.n_reads]))
    if all_pos.size == 0:
        return all_pos, np.empty((0, 0), dtype=float)
    pos_index = {int(p): i for i, p in enumerate(all_pos.tolist())}
    n_reads = int(sum(s.n_reads for s in stores))
    mat = np.full((n_reads, all_pos.size), np.nan, dtype=float)
    row = 0
    for store in stores:
        for _start, _strand, positions, meth in store.iter_reads():
            for p, m in zip(positions.tolist(), meth.tolist()):
                col = pos_index.get(int(p))
                if col is not None:
                    mat[row, col] = float(m)
            row += 1
    return all_pos.astype(np.int32), mat


def _pearson_pair(a: np.ndarray, b: np.ndarray) -> Tuple[float, float, int]:
    mask = np.isfinite(a) & np.isfinite(b)
    n = int(mask.sum())
    if n < 3:
        return 0.0, 1.0, n
    r, p = stats.pearsonr(a[mask], b[mask])
    if not np.isfinite(r):
        return 0.0, 1.0, n
    return float(r), float(p), n


def discover_mhbs(
    stores: Sequence[MhapStore],
    *,
    r2_min: float,
    p_max: float,
    core_window: int,
    min_cpgs: int,
    min_median_reads: int,
    control_intervals: Optional[Sequence[Tuple[str, int, int, str]]] = None,
    chrom: str = "",
) -> pd.DataFrame:
    """Return a BED-like frame: chrom, start, end, name, n_cpgs, median_reads, mean_r2."""
    positions, mat = _site_matrix(stores)
    if positions.size == 0 or mat.size == 0:
        return pd.DataFrame(
            columns=["chrom", "start", "end", "name", "n_cpgs", "median_reads", "mean_r2"]
        )

    control_mask = np.zeros(positions.size, dtype=bool)
    if control_intervals:
        for _c, start, end, _name in control_intervals:
            if chrom and _c and _c != chrom:
                continue
            control_mask |= (positions >= start) & (positions < end)

    keep = ~control_mask
    positions = positions[keep]
    mat = mat[:, keep]
    if positions.size < min_cpgs:
        return pd.DataFrame(
            columns=["chrom", "start", "end", "name", "n_cpgs", "median_reads", "mean_r2"]
        )

    n_sites = positions.size
    pair_ok = np.zeros(max(n_sites - 1, 0), dtype=bool)
    pair_r2 = np.zeros(max(n_sites - 1, 0), dtype=float)
    for i in range(n_sites - 1):
        r, p, n = _pearson_pair(mat[:, i], mat[:, i + 1])
        r2 = r * r
        pair_r2[i] = r2
        pair_ok[i] = (n >= 3) and (r2 >= r2_min) and (p <= p_max)

    blocks: List[Tuple[int, int]] = []
    i = 0
    seed = max(int(core_window) - 1, 1)
    while i < n_sites - seed:
        if bool(np.all(pair_ok[i : i + seed])):
            j = i + seed
            while j < n_sites - 1 and pair_ok[j]:
                j += 1
            blocks.append((i, j + 1))
            i = j + 1
        else:
            i += 1

    rows = []
    for start_i, end_i in blocks:
        n_cpg = end_i - start_i
        if n_cpg < min_cpgs:
            continue
        sub = mat[:, start_i:end_i]
        reads_per = np.isfinite(sub).sum(axis=1)
        overlapping = reads_per[reads_per > 0]
        if overlapping.size == 0:
            continue
        median_reads = float(np.median(overlapping))
        if median_reads < min_median_reads:
            continue
        mean_r2 = float(np.mean(pair_r2[start_i : end_i - 1])) if end_i - start_i > 1 else 0.0
        start = int(positions[start_i])
        end = int(positions[end_i - 1]) + 2
        name = f"{chrom}:{start}-{end}" if chrom else f"{start}-{end}"
        rows.append(
            {
                "chrom": chrom,
                "start": start,
                "end": end,
                "name": name,
                "n_cpgs": n_cpg,
                "median_reads": median_reads,
                "mean_r2": mean_r2,
            }
        )
    return pd.DataFrame(rows)


def blocks_from_bed(bed_path: str | Path) -> pd.DataFrame:
    rows = []
    for chrom, start, end, name in load_bed_intervals(bed_path):
        rows.append(
            {
                "chrom": chrom,
                "start": start,
                "end": end,
                "name": name,
                "n_cpgs": None,
                "median_reads": None,
                "mean_r2": None,
            }
        )
    return pd.DataFrame(rows)


def load_stores_for_sample(
    sample_dir: str | Path,
    chromosomes: Iterable[str],
    context: str = "CG",
) -> List[MhapStore]:
    stores: List[MhapStore] = []
    root = Path(sample_dir)
    for chrom in chromosomes:
        path = root / f"{chrom}-{context}.mhap.h5"
        if path.is_file():
            stores.append(load_mhap_store(path))
    return stores
