"""Equilibrium Ising-derived measures: MML, NME, ESI, MSI."""

from __future__ import annotations

from typing import Dict, List, Sequence

import numpy as np

from methyl_utils.array_backend import get_array_module, to_cpu
from methyl_utils.core.read_level_io import ReadLevelPatterns

from ..config import InfoTheoryStepConfig
from .ising import (
    StateDesign,
    build_state_design,
    fit_ising_batch,
    histograms_to_dense,
    resolve_ising_runtime,
)


def compute_measures_from_prob(
    prob: np.ndarray,
    design: StateDesign,
    *,
    xp,
) -> Dict[str, np.ndarray]:
    """
    Compute MML, NME, ESI, MSI for fitted probabilities (n_tiles, n_states).

    Returns CPU arrays of shape (n_tiles,).
    """
    S = xp.asarray(design.design, dtype=xp.float64)
    mml_state = xp.asarray(design.mml_per_state, dtype=xp.float64)
    p = xp.asarray(prob, dtype=xp.float64)
    k = float(design.k)
    eps = 1e-12
    ln2 = xp.log(xp.array(2.0, dtype=xp.float64))

    mml = p @ mml_state
    log_p = xp.log(p + eps)
    entropy_bits = -xp.sum(p * log_p, axis=1) / ln2
    nme = entropy_bits / k

    e_s = p @ S
    centered_s = S[None, :, :] - e_s[:, None, :]
    m_centered = mml_state[None, :] - mml[:, None]

    cov_sm = xp.einsum("ts, ts, tsf->tf", p, m_centered, centered_s)
    msi = xp.linalg.norm(cov_sm, axis=1)

    h = -xp.sum(p * log_p, axis=1, keepdims=True)
    dH_dlogit = -p * (log_p + h)
    dNME_dlogit = dH_dlogit / (k * ln2)
    grad_nme = dNME_dlogit @ S
    esi = xp.linalg.norm(grad_nme, axis=1)

    return {
        "mml": to_cpu(mml),
        "nme": to_cpu(nme),
        "esi": to_cpu(esi),
        "msi": to_cpu(msi),
    }


def _fit_tiles_batch(
    patterns: ReadLevelPatterns,
    tile_indices: Sequence[int],
    design: StateDesign,
    runtime: dict,
) -> Dict[str, np.ndarray]:
    xp, used_gpu = get_array_module(runtime.get("prefer_gpu"))
    dense = histograms_to_dense(patterns, tile_indices, xp)
    fit = fit_ising_batch(
        dense,
        design,
        xp=xp,
        max_iter=runtime["max_iter"],
        tol=runtime["tol"],
        l2=runtime["l2"],
    )
    measures = compute_measures_from_prob(fit.prob, design, xp=xp)
    measures["converged"] = fit.converged.astype(np.float64)
    measures["used_gpu"] = np.full(len(tile_indices), 1.0 if used_gpu else 0.0)
    return measures


def aggregate_file_ising_measures(
    patterns: ReadLevelPatterns,
    *,
    cfg: InfoTheoryStepConfig,
) -> Dict[str, float]:
    """Read-weighted genome-level Ising aggregates for one sidecar file."""
    runtime = resolve_ising_runtime(cfg)
    min_reads = int(runtime["min_tile_reads"])
    batch_size = int(runtime["batch_tiles"])
    coupling = runtime["coupling"]
    if coupling not in ("nearest", "all"):
        coupling = "nearest"
    design = build_state_design(int(patterns.tile_size), coupling)  # type: ignore[arg-type]

    eligible: List[int] = []
    weights: List[float] = []
    for tile_idx in range(patterns.n_tiles):
        n_reads = int(patterns.tile_n_reads[tile_idx])
        if n_reads < min_reads:
            continue
        if not patterns.tile_histogram(tile_idx):
            continue
        eligible.append(tile_idx)
        weights.append(float(n_reads))

    if not eligible:
        return {
            "mml": float("nan"),
            "nme": float("nan"),
            "esi": float("nan"),
            "msi": float("nan"),
            "n_tiles": 0.0,
        }

    acc = {key: [] for key in ("mml", "nme", "esi", "msi", "weight")}
    for start in range(0, len(eligible), batch_size):
        batch_idx = eligible[start : start + batch_size]
        batch_w = weights[start : start + batch_size]
        measures = _fit_tiles_batch(patterns, batch_idx, design, runtime)
        for i, w in enumerate(batch_w):
            if not np.isfinite(measures["mml"][i]):
                continue
            for key in ("mml", "nme", "esi", "msi"):
                acc[key].append(float(measures[key][i]) * w)
            acc["weight"].append(w)

    if not acc["weight"]:
        return {
            "mml": float("nan"),
            "nme": float("nan"),
            "esi": float("nan"),
            "msi": float("nan"),
            "n_tiles": 0.0,
        }
    w_sum = float(np.sum(acc["weight"]))
    return {
        "mml": float(np.sum(acc["mml"]) / w_sum),
        "nme": float(np.sum(acc["nme"]) / w_sum),
        "esi": float(np.sum(acc["esi"]) / w_sum),
        "msi": float(np.sum(acc["msi"]) / w_sum),
        "n_tiles": float(len(acc["weight"])),
    }
