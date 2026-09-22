"""RNA cohort expression matrix loader (thin adapter over the shared omics_features seam)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from omics_features.matrix import load_feature_matrix


def load_expression_matrix(
    sample_paths: Sequence[str | Path],
    *,
    gene_order: Optional[Sequence[str]] = None,
    transform: str = "logcpm",
    min_total_count: float = 1.0,
) -> Tuple[np.ndarray, List[str], List[str]]:
    """Stack per-sample ``expression.h5`` into a dense ``samples x genes`` matrix.

    Delegates to :func:`omics_features.matrix.load_feature_matrix` with ``kind=expression``.
    Returns (X, gene_ids, sample_ids); missing genes for a sample are 0.
    """
    return load_feature_matrix(
        sample_paths,
        kind="expression",
        feature_order=gene_order,
        transform=transform,
        normalize="none",
        impute="none",
        missing_fill=0.0,
        min_total=min_total_count,
    )
