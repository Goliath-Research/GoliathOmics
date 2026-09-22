"""Bridge from methylutils to mojo-align ``numeric/`` centroid kernels.

Resolves ``MOJO_ALIGN_ROOT``, ``/opt/mojo-align``, or a sibling ``../mojo-align``.
``gpu_backend=mojo`` is fail-closed: missing tree or DeviceContext (when a GPU
is required) raises; there is no silent CuPy fallback.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np

_KERNELS: Any = None


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env = os.environ.get("MOJO_ALIGN_ROOT", "").strip()
    if env:
        roots.append(Path(env))
    roots.append(Path("/opt/mojo-align"))
    here = Path(__file__).resolve()
    for parent in here.parents:
        sibling = parent.parent / "mojo-align"
        if sibling.is_dir():
            roots.append(sibling)
            break
    return roots


def find_mojo_align_root() -> Optional[Path]:
    """Return the mojo-align checkout or staged tree if numeric kernels are present."""
    for root in _candidate_roots():
        if not root.is_dir():
            continue
        if (root / "numeric" / "python" / "centroid_kernels.py").is_file():
            return root
        if (root / "engine" / "centroid_kernels.py").is_file():
            return root
        if (root / "centroid_kernels.py").is_file():
            return root
    return None


def mojo_numeric_available() -> bool:
    return find_mojo_align_root() is not None


@lru_cache(maxsize=1)
def _load_kernels():
    root = find_mojo_align_root()
    if root is None:
        raise RuntimeError(
            "gpu_backend=mojo requires mojo-align numeric kernels. "
            "Set MOJO_ALIGN_ROOT or place a sibling mojo-align checkout "
            "with numeric/python/centroid_kernels.py."
        )
    python_dir = root / "numeric" / "python"
    gpu_common = root / "gpu-common" / "python"
    staged = root / "engine"
    flat = root
    if gpu_common.is_dir():
        sys.path.insert(0, str(gpu_common))
    for path in (python_dir, staged, flat):
        if (path / "centroid_kernels.py").is_file():
            sys.path.insert(0, str(path))
            break
    import centroid_kernels  # type: ignore

    return centroid_kernels


def require_mojo_numeric() -> None:
    """Import the numeric package and fail closed if a GPU DeviceContext is required."""
    kernels = _load_kernels()
    kernels.require_device()


def merge_centroid_positions(
    existing_pos: np.ndarray,
    existing_size: int,
    sample_pos: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, int]:
    """Return ``(merged_pos, is_new, n_new)`` matching MethylCentroidBuilder merge."""
    return _load_kernels().merge_centroid_positions(
        existing_pos, existing_size, sample_pos
    )


def scatter_add_u32(acc: np.ndarray, idx: np.ndarray, values: np.ndarray) -> None:
    _load_kernels().scatter_add_u32(acc, idx, values)


def scatter_add_f32(acc: np.ndarray, idx: np.ndarray, values: np.ndarray) -> None:
    _load_kernels().scatter_add_f32(acc, idx, values)


def bin_histogram_add(
    bin_counts: np.ndarray, pos_idx: np.ndarray, bin_idx: np.ndarray
) -> None:
    _load_kernels().bin_histogram_add(bin_counts, pos_idx, bin_idx)


def digitize_bins(mean: np.ndarray, bin_edges: np.ndarray) -> np.ndarray:
    return _load_kernels().digitize_bins(mean, bin_edges)
