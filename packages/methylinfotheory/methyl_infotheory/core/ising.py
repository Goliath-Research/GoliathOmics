"""Batched per-tile Ising / max-entropy model fit on read-level pattern histograms."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Sequence, Tuple

import numpy as np

from methyl_utils.array_backend import to_cpu
from methyl_utils.core.read_level_io import ReadLevelPatterns

CouplingMode = Literal["nearest", "all"]


@dataclass(frozen=True)
class StateDesign:
    """Precomputed Ising sufficient statistics for all 2^k pattern states."""

    k: int
    coupling: CouplingMode
    states: np.ndarray  # (n_states, k) spins in {-1, +1}
    design: np.ndarray  # (n_states, n_features)
    mml_per_state: np.ndarray  # (n_states,) mean methylation level in [0, 1]


@dataclass
class IsingFit:
    """Result of batched Ising fit for one tile batch."""

    theta: np.ndarray  # (n_tiles, n_features)
    prob: np.ndarray  # (n_tiles, n_states)
    log_z: np.ndarray  # (n_tiles,)
    converged: np.ndarray  # (n_tiles,) bool
    n_features: int
    k: int


def _spin_from_pattern(pattern_id: int, k: int) -> np.ndarray:
    spins = np.empty(k, dtype=np.float64)
    for i in range(k):
        bit = (int(pattern_id) >> (k - 1 - i)) & 1
        spins[i] = 1.0 if bit else -1.0
    return spins


def _pair_indices(k: int, coupling: CouplingMode) -> list[tuple[int, int]]:
    if coupling == "nearest":
        return [(i, i + 1) for i in range(k - 1)]
    pairs: list[tuple[int, int]] = []
    for i in range(k):
        for j in range(i + 1, k):
            pairs.append((i, j))
    return pairs


@lru_cache(maxsize=16)
def build_state_design(k: int, coupling: CouplingMode = "nearest") -> StateDesign:
    """Enumerate 2^k bitmask states and build sufficient-statistic design matrix S."""
    n_states = 1 << int(k)
    pairs = _pair_indices(int(k), coupling)
    n_features = int(k) + len(pairs)
    states = np.zeros((n_states, int(k)), dtype=np.float64)
    design = np.zeros((n_states, n_features), dtype=np.float64)
    mml = np.zeros(n_states, dtype=np.float64)

    for pid in range(n_states):
        spin = _spin_from_pattern(pid, int(k))
        states[pid] = spin
        mml[pid] = float(np.mean((spin + 1.0) * 0.5))
        col = 0
        for i in range(int(k)):
            design[pid, col] = spin[i]
            col += 1
        for i, j in pairs:
            design[pid, col] = spin[i] * spin[j]
            col += 1

    return StateDesign(k=int(k), coupling=coupling, states=states, design=design, mml_per_state=mml)


def histograms_to_dense(
    patterns: ReadLevelPatterns,
    tile_indices: Sequence[int],
    xp,
) -> Tuple:
    """Build dense (n_tiles, 2^k) count matrix for selected tile indices."""
    k = int(patterns.tile_size)
    n_states = 1 << k
    n_tiles = len(tile_indices)
    dense = xp.zeros((n_tiles, n_states), dtype=xp.float64)

    tile_id_arr = patterns.pattern_tile_id
    pid_arr = patterns.pattern_id
    count_arr = patterns.pattern_count

    index_map = {int(t): i for i, t in enumerate(tile_indices)}
    for row in range(tile_id_arr.size):
        tid = int(tile_id_arr[row])
        if tid not in index_map:
            continue
        pid = int(pid_arr[row])
        if 0 <= pid < n_states:
            dense[index_map[tid], pid] += float(count_arr[row])

    for i, tid in enumerate(tile_indices):
        row_sum = float(to_cpu(xp.sum(dense[i])))
        n_reads = int(patterns.tile_n_reads[int(tid)])
        if row_sum <= 0.0 and n_reads > 0:
            dense[i, :] = float(n_reads) / float(n_states)

    return dense


def _softmax(logits, xp):
    shifted = logits - xp.max(logits, axis=1, keepdims=True)
    expv = xp.exp(shifted)
    return expv / xp.sum(expv, axis=1, keepdims=True)


def fit_ising_batch(
    hist_dense,
    design: StateDesign,
    *,
    xp,
    max_iter: int = 50,
    tol: float = 1e-6,
    l2: float = 1e-4,
) -> IsingFit:
    """
    Batched maximum-entropy / MLE Ising fit via Newton moment matching.

    Args:
        hist_dense: (n_tiles, 2^k) count matrix on xp backend.
        design: precomputed StateDesign (CPU arrays copied to xp internally).
    """
    S = xp.asarray(design.design, dtype=xp.float64)
    n_tiles = int(hist_dense.shape[0])
    n_states = int(S.shape[0])
    n_features = int(S.shape[1])

    totals = xp.sum(hist_dense, axis=1, keepdims=True)
    totals = xp.maximum(totals, 1.0)
    p_emp = hist_dense / totals
    e_emp = p_emp @ S

    theta = xp.zeros((n_tiles, n_features), dtype=xp.float64)
    eye = xp.eye(n_features, dtype=xp.float64)
    converged = xp.zeros(n_tiles, dtype=bool)

    for _ in range(int(max_iter)):
        logits = theta @ S.T  # (n_tiles, n_states)
        prob = _softmax(logits, xp)
        e_model = prob @ S
        diff = e_emp - e_model
        max_res = float(to_cpu(xp.max(xp.linalg.norm(diff, axis=1)))[()] if n_tiles else 0.0)
        if max_res < float(tol):
            converged = xp.ones(n_tiles, dtype=bool)
            break

        # Batched covariance: (n_tiles, n_features, n_features)
        centered = S[None, :, :] - e_model[:, None, :]
        cov = xp.einsum("ts, tsf, tsg->tfg", prob, centered, centered)
        cov = cov + float(l2) * eye[None, :, :]

        try:
            step = xp.linalg.solve(cov, diff[:, :, None])[..., 0]
        except Exception:
            step = diff
        theta = theta + step

    logits = theta @ S.T
    prob = _softmax(logits, xp)
    log_z = xp.log(xp.sum(xp.exp(logits - xp.max(logits, axis=1, keepdims=True)), axis=1))
    log_z = log_z + xp.max(logits, axis=1)

    if not bool(to_cpu(xp.all(converged))):
        e_model = prob @ S
        diff = e_emp - e_model
        converged = xp.linalg.norm(diff, axis=1) < float(tol)

    return IsingFit(
        theta=to_cpu(theta),
        prob=to_cpu(prob),
        log_z=to_cpu(log_z),
        converged=to_cpu(converged).astype(bool),
        n_features=n_features,
        k=design.k,
    )


def resolve_ising_runtime(cfg) -> dict:
    """Resolve runtime defaults for Ising knobs (config-not-code fallbacks)."""
    return {
        "max_iter": int(cfg.ising_max_iter) if cfg.ising_max_iter is not None else 50,
        "tol": float(cfg.ising_tol) if cfg.ising_tol is not None else 1e-6,
        "l2": float(cfg.ising_l2) if cfg.ising_l2 is not None else 1e-4,
        "coupling": str(cfg.ising_coupling) if cfg.ising_coupling is not None else "nearest",
        "min_tile_reads": int(cfg.ising_min_tile_reads)
        if cfg.ising_min_tile_reads is not None
        else int(cfg.min_tile_reads or 1),
        "batch_tiles": int(cfg.ising_batch_tiles) if cfg.ising_batch_tiles is not None else 4096,
        "prefer_gpu": cfg.prefer_gpu,
    }
