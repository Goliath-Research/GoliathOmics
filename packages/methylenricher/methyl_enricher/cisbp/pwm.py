"""
CIS-BP PWM parsing and lightweight motif scanning.

PWM files are tab-separated with a ``Pos A C G T`` header followed by one
probability row per position. We convert to a log-odds matrix (vs a uniform
background) and scan sequences on both strands, returning a *relative* score in
[0, 1] (0 = worst possible, 1 = best possible) so a single threshold works
across motifs of different lengths/information content.

The scanner is intentionally dependency-free (numpy only) to avoid pulling in
MOODS/MEME; it is adequate for deriving TF -> target gene sets.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

_BASES = ("A", "C", "G", "T")
_BASE_INDEX = {b: i for i, b in enumerate(_BASES)}
# Encode A,C,G,T -> 0..3, everything else (N, lowercase handled by caller) -> 4.
_ENCODE = np.full(256, 4, dtype=np.int8)
for _b, _i in _BASE_INDEX.items():
    _ENCODE[ord(_b)] = _i
    _ENCODE[ord(_b.lower())] = _i


def load_pwm(path: Path, pseudocount: float = 1e-3) -> Optional[np.ndarray]:
    """
    Load a CIS-BP PWM file into an (L, 4) probability matrix (columns A,C,G,T).

    Returns ``None`` for empty motifs (header-only files).
    """
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        header = fh.readline()
        if not header:
            return None
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            try:
                probs = [float(x) for x in parts[1:5]]
            except ValueError:
                continue
            rows.append(probs)
    if not rows:
        return None
    mat = np.asarray(rows, dtype=float)
    mat = np.clip(mat, 0.0, None) + pseudocount
    mat = mat / mat.sum(axis=1, keepdims=True)
    return mat


def to_log_odds(pwm: np.ndarray, background: Optional[np.ndarray] = None) -> np.ndarray:
    """Convert a probability matrix to a log2 odds matrix vs a background."""
    if background is None:
        background = np.full(4, 0.25)
    return np.log2(pwm / background[np.newaxis, :])


def _encode_seq(seq: str) -> np.ndarray:
    arr = np.frombuffer(seq.encode("ascii", errors="replace"), dtype=np.uint8)
    return _ENCODE[arr]


def best_relative_score(log_odds: np.ndarray, encoded: np.ndarray) -> float:
    """
    Best windowed log-odds score over both strands, normalized to [0, 1].

    Windows containing non-ACGT bases are skipped. Returns 0.0 when the motif
    cannot be placed (sequence shorter than the motif or all-N).
    """
    L = log_odds.shape[0]
    n = encoded.shape[0]
    if L == 0 or n < L:
        return 0.0

    max_score = float(log_odds.max(axis=1).sum())
    min_score = float(log_odds.min(axis=1).sum())
    denom = max_score - min_score
    if denom <= 0:
        return 0.0

    rc = _reverse_complement_log_odds(log_odds)

    best = -np.inf
    # Slide a window of length L; vectorize per offset across the 4-row gather.
    # For typical promoter windows (<= ~7kb) and motif lengths this is fast enough.
    num_windows = n - L + 1
    # Build window index matrix (num_windows, L)
    idx = np.arange(num_windows)[:, None] + np.arange(L)[None, :]
    windows = encoded[idx]  # (num_windows, L) values 0..4
    valid = (windows != 4).all(axis=1)
    if not valid.any():
        return 0.0
    windows = windows[valid]  # (V, L)
    pos = np.arange(L)
    for mat in (log_odds, rc):
        # gather mat[pos, base] for each window
        scores = mat[pos[None, :], windows].sum(axis=1)  # (V,)
        m = float(scores.max())
        if m > best:
            best = m

    rel = (best - min_score) / denom
    return float(min(1.0, max(0.0, rel)))


def _reverse_complement_log_odds(log_odds: np.ndarray) -> np.ndarray:
    # Reverse positions and complement bases: A<->T (0<->3), C<->G (1<->2).
    return log_odds[::-1, ::-1]


def sequence_has_hit(log_odds: np.ndarray, seq: str, threshold: float) -> bool:
    """True if the motif scores >= threshold (relative) anywhere in ``seq``."""
    encoded = _encode_seq(seq)
    return best_relative_score(log_odds, encoded) >= threshold
