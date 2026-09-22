"""
Genomic utility functions for methylation analysis.

This module provides utility functions for working with genomic data,
including region grouping and genomic position manipulation.
"""

import numpy as np
from typing import List, Tuple


def group_significant_positions(
    positions: np.ndarray,
    q_values: np.ndarray,
    threshold: float = 0.05,
    max_gap: int = 100
) -> List[Tuple[int, int]]:
    """
    Group significant genomic positions into contiguous regions.

    This function takes an array of genomic positions and their corresponding
    q-values, and groups significant positions (q-value ≤ threshold) into
    contiguous regions based on proximity.

    Args:
        positions: Array of genomic positions (must be sorted)
        q_values: Array of q-values corresponding to positions
        threshold: Q-value threshold for significance (default: 0.05)
        max_gap: Maximum gap between positions to be considered in the same region

    Returns:
        List of tuples (start_position, end_position) representing significant regions

    Raises:
        ValueError: If input validation fails
    """
    # Input validation
    if positions.size == 0:
        return []

    if positions.shape != q_values.shape:
        raise ValueError(f"Positions shape {positions.shape} must match q-values shape {q_values.shape}")

    if not np.all(positions[:-1] <= positions[1:]):
        raise ValueError("Positions array must be sorted in ascending order")

    if threshold <= 0 or threshold > 1:
        raise ValueError("Threshold must be in range (0, 1]")

    if max_gap <= 0:
        raise ValueError("max_gap must be positive")

    # Find significant positions
    sig_mask = q_values <= threshold
    sig_positions = positions[sig_mask]

    if len(sig_positions) == 0:
        return []

    # Group into contiguous regions
    regions = []
    start = sig_positions[0]
    prev = start

    for pos in sig_positions[1:]:
        if pos - prev > max_gap:
            regions.append((int(start), int(prev)))
            start = pos
        prev = pos

    regions.append((int(start), int(prev)))
    return regions


__all__ = [
    "group_significant_positions"
]
