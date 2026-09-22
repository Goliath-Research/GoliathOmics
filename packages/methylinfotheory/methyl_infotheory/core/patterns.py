"""Per-tile read-level pattern statistics."""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

from methyl_utils.core.read_level_io import ReadLevelPatterns


def _histogram_to_probs(hist: Dict[int, int]) -> Tuple[np.ndarray, float]:
    if not hist:
        return np.array([], dtype=np.float64), 0.0
    counts = np.asarray(list(hist.values()), dtype=np.float64)
    total = float(np.sum(counts))
    if total <= 0.0:
        return np.array([], dtype=np.float64), 0.0
    return counts / total, total


def pattern_shannon_entropy(hist: Dict[int, int], *, tile_size: int) -> float:
    """Normalized Shannon entropy of read patterns in [0, 1]."""
    probs, _ = _histogram_to_probs(hist)
    if probs.size == 0:
        return float("nan")
    eps = 1e-12
    h = -float(np.sum(probs * np.log2(probs + eps)))
    max_h = float(np.log2(2 ** int(tile_size))) if tile_size > 0 else 1.0
    return h / max_h


def pattern_epipolymorphism(hist: Dict[int, int]) -> float:
    """1 - sum(p_i^2) over read patterns (Simpson diversity)."""
    probs, _ = _histogram_to_probs(hist)
    if probs.size == 0:
        return float("nan")
    return float(1.0 - np.sum(probs * probs))


def pattern_pdr(hist: Dict[int, int], *, tile_size: int) -> float:
    """
    Fraction of reads discordant within the tile.

    Discordant = pattern is neither all-unmethylated (0) nor all-methylated (2^k-1).
    """
    probs, total = _histogram_to_probs(hist)
    if total <= 0.0 or probs.size == 0:
        return float("nan")
    all_hypo = 0
    all_hyper = (1 << int(tile_size)) - 1
    discordant = 0.0
    for pid, count in hist.items():
        if int(pid) not in (all_hypo, all_hyper):
            discordant += float(count)
    return discordant / total


def aggregate_file_measures(
    patterns: ReadLevelPatterns,
    *,
    min_tile_reads: int,
) -> Dict[str, float]:
    """Weighted genome-level aggregates for one sidecar file."""
    entropies: list[float] = []
    epipols: list[float] = []
    pdrs: list[float] = []
    weights: list[float] = []

    k = int(patterns.tile_size)
    for tile_idx in range(patterns.n_tiles):
        n_reads = int(patterns.tile_n_reads[tile_idx])
        if n_reads < int(min_tile_reads):
            continue
        hist = patterns.tile_histogram(tile_idx)
        if not hist:
            continue
        ent = pattern_shannon_entropy(hist, tile_size=k)
        epi = pattern_epipolymorphism(hist)
        pdr = pattern_pdr(hist, tile_size=k)
        if not (np.isfinite(ent) and np.isfinite(epi) and np.isfinite(pdr)):
            continue
        w = float(n_reads)
        entropies.append(ent * w)
        epipols.append(epi * w)
        pdrs.append(pdr * w)
        weights.append(w)

    if not weights:
        return {
            "entropy": float("nan"),
            "epipolymorphism": float("nan"),
            "pdr": float("nan"),
            "n_tiles": 0.0,
        }
    w_sum = float(np.sum(weights))
    return {
        "entropy": float(np.sum(entropies) / w_sum),
        "epipolymorphism": float(np.sum(epipols) / w_sum),
        "pdr": float(np.sum(pdrs) / w_sum),
        "n_tiles": float(len(weights)),
    }
