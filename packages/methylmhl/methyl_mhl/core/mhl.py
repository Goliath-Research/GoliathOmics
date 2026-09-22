"""Wong/Guo methylation haplotype load (consecutive meth, lengths 1..L)."""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from methyl_utils.core.mhap_io import MhapStore


def _weights(max_len: int) -> np.ndarray:
    k = np.arange(1, max_len + 1, dtype=float)
    return k / k.sum()


def mhl_for_block(
    stores: Sequence[MhapStore],
    start: int,
    end: int,
    *,
    max_len: int,
) -> float:
    """MHL for one genomic interval using consecutive fully-methylated haplotypes."""
    weights = _weights(max_len)
    meth_counts = np.zeros(max_len, dtype=np.int64)
    tot_counts = np.zeros(max_len, dtype=np.int64)
    for store in stores:
        for _rs, _strand, positions, meth in store.iter_reads():
            mask = (positions >= start) & (positions < end)
            if not np.any(mask):
                continue
            bits = meth[mask].astype(np.uint8)
            n = int(bits.size)
            if n == 0:
                continue
            L = min(n, max_len)
            for k in range(1, L + 1):
                windows = n - k + 1
                tot_counts[k - 1] += windows
                if windows <= 0:
                    continue
                # sliding all-1s via convolution
                kernel = np.ones(k, dtype=np.int32)
                sums = np.convolve(bits, kernel, mode="valid")
                meth_counts[k - 1] += int(np.sum(sums == k))
    frac = np.zeros(max_len, dtype=float)
    nz = tot_counts > 0
    frac[nz] = meth_counts[nz] / tot_counts[nz]
    if not np.any(nz):
        return float("nan")
    return float(np.dot(weights, frac))


def mhl_matrix_for_sample(
    stores: Sequence[MhapStore],
    blocks: pd.DataFrame,
    *,
    max_len: int,
) -> np.ndarray:
    values = np.full(len(blocks), np.nan, dtype=float)
    if not stores:
        return values
    by_chrom: dict[str, list[MhapStore]] = {}
    for store in stores:
        by_chrom.setdefault(str(store.chrom), []).append(store)
    for i, row in enumerate(blocks.itertuples(index=False)):
        chrom = str(row.chrom)
        subset = by_chrom.get(chrom, stores)
        values[i] = mhl_for_block(
            subset,
            int(row.start),
            int(row.end),
            max_len=max_len,
        )
    return values
