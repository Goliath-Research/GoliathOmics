# methyl_utils/core/io.py
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional, Union
import hdf5plugin  # noqa: F401 - Must be imported before h5py
import h5py
import numpy as np
import pandas as pd
from .methyl_frame import MethylCentroid, MethylSample


def _indices_for_positions(pos_arr: np.ndarray, positions: np.ndarray):
    """Return row indices where pos_arr is in positions (same as align_to_positions subset)."""
    want = np.asarray(positions, dtype=np.uint32)
    mask = np.isin(pos_arr, want)
    return np.flatnonzero(mask).astype(np.int32, copy=False)


def _indices_for_positions_h5(pos_dset, positions: np.ndarray) -> np.ndarray:
    """Return row indices where pos_dset values are in positions, via binary search (no full pos load).
    Assumes pos_dset is sorted (genomic order). Returns indices in file order, unique.
    """
    want = np.asarray(positions, dtype=np.uint32)
    n = pos_dset.shape[0]
    if n == 0:
        return np.array([], dtype=np.intp)
    found = set()
    for target in want:
        target = int(target)
        lo, hi = 0, n - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            val = int(pos_dset[mid])
            if val < target:
                lo = mid + 1
            elif val > target:
                hi = mid - 1
            else:
                found.add(mid)
                break
    # Row indices into pos_dset; methylation rows per chrom are ≪ 2³¹.
    return np.array(sorted(found), dtype=np.int32)


def _searchsorted_left(pos_dset_or_arr, value: int) -> int:
    """Lower-bound index of *value* in sorted genomic positions (searchsorted side='left')."""
    value = int(value)
    if isinstance(pos_dset_or_arr, np.ndarray):
        return int(np.searchsorted(pos_dset_or_arr, value, side="left"))
    n = int(pos_dset_or_arr.shape[0])
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if int(pos_dset_or_arr[mid]) < value:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _row_range_for_bp(
    pos_dset_or_arr,
    start_bp: int,
    end_bp: int,
) -> tuple[int, int]:
    """
    Contiguous row range [lo, hi) for genomic half-open interval [start_bp, end_bp).

    Assumes *pos_dset_or_arr* is sorted ascending (genomic order). Works on a NumPy
    array or an h5py Dataset (element-wise binary search; no full pos load).
    """
    start_bp = int(start_bp)
    end_bp = int(end_bp)
    if end_bp < start_bp:
        raise ValueError(f"end_bp ({end_bp}) must be >= start_bp ({start_bp})")
    n = int(pos_dset_or_arr.shape[0])
    if n == 0:
        return 0, 0
    lo = _searchsorted_left(pos_dset_or_arr, start_bp)
    hi = _searchsorted_left(pos_dset_or_arr, end_bp)
    return lo, hi


def _pos_dataset_from_open_file(f: h5py.File):
    """Return the ``pos`` dataset handle from an open methylation H5 file."""
    if "methylation_data" in f:
        group = f["methylation_data"]
        if isinstance(group, h5py.Group) and "pos" in group:
            return group["pos"]
        if isinstance(group, h5py.Dataset) and group.dtype.names and "pos" in group.dtype.names:
            return group["pos"]
    if "pos" in f:
        return f["pos"]
    raise ValueError("No 'pos' dataset found in open HDF5 file")


def _validate_bp_range_args(
    positions: Optional[np.ndarray],
    indices: Optional[np.ndarray],
    start_bp: Optional[int],
    end_bp: Optional[int],
) -> bool:
    """Return True when a bp range load is requested; raise on invalid combinations."""
    bp_range = start_bp is not None or end_bp is not None
    if bp_range and (positions is not None or indices is not None):
        raise ValueError("start_bp/end_bp cannot be combined with positions= or indices=")
    if bp_range and start_bp is not None and end_bp is not None and int(end_bp) < int(start_bp):
        raise ValueError(f"end_bp ({end_bp}) must be >= start_bp ({start_bp})")
    return bp_range


def load_pos_from_h5(path: Union[str, Path]) -> np.ndarray:
    """
    Load only the position array from an HDF5 methylation file (lightweight read for indexing).
    Use with load_from_h5(path, indices=...) to avoid reading the full file repeatedly.
    Supports the same three formats as load_from_h5: methylation_data as Group, as compound
    Dataset (structured array), or pos at root.
    """
    path = Path(path)
    with h5py.File(path, "r") as f:
        if "methylation_data" in f:
            group = f["methylation_data"]
            if isinstance(group, h5py.Group) and "pos" in group:
                return np.asarray(group["pos"][:], dtype=np.uint32)
            if isinstance(group, h5py.Dataset) and group.dtype.names and "pos" in group.dtype.names:
                return np.asarray(group["pos"][:], dtype=np.uint32)
        if "pos" in f:
            return np.asarray(f["pos"][:], dtype=np.uint32)
    raise ValueError(f"No 'pos' dataset found in {path}")


def _h5_take(dset, sel):
    """Load *dset* with selection: None (all), slice (hyperslab), or integer index array."""
    if sel is None:
        return np.asarray(dset[:])
    return np.asarray(dset[sel])


def _selection_for_pos(
    pos_source,
    *,
    positions: Optional[np.ndarray],
    indices: Optional[np.ndarray],
    start_bp: Optional[int],
    end_bp: Optional[int],
    bp_range: bool,
):
    """
    Resolve row selection for a sorted ``pos`` source.

    Returns None (full load), a ``slice`` (contiguous bp range), or an index ndarray.
    """
    if indices is not None:
        return np.asarray(indices, dtype=np.intp)
    if positions is not None:
        if isinstance(pos_source, np.ndarray):
            return _indices_for_positions(pos_source, positions)
        return _indices_for_positions_h5(pos_source, positions)
    if bp_range:
        lo, hi = _row_range_for_bp(
            pos_source,
            0 if start_bp is None else int(start_bp),
            int(np.iinfo(np.uint32).max) if end_bp is None else int(end_bp),
        )
        return slice(lo, hi)
    return None


def load_from_h5(
    path: Union[str, Path],
    positions: Optional[np.ndarray] = None,
    indices: Optional[np.ndarray] = None,
    *,
    start_bp: Optional[int] = None,
    end_bp: Optional[int] = None,
) -> MethylCentroid | MethylSample:
    """
    Load methylation data from HDF5 file.

    When positions is provided, only those rows are read from disk (hyperslice).
    When indices is provided, only those row indices are read (no full pos read); use with
    load_pos_from_h5() + _indices_for_positions for chunked centroid building.
    When start_bp/end_bp are provided, load the contiguous half-open genomic range
    ``[start_bp, end_bp)`` via a hyperslab (sorted ``pos``). Cannot combine with
    ``positions`` or ``indices``.

    Centroid detection: presence of pos, tnc, N, Sx, Sx2, Sm, Su, Sc2, Swx2. Requires binned_stats (bins attr + bin_counts).
    No backward compatibility: only the new centroid schema is supported for centroids.

    Returns:
        MethylSample or MethylCentroid instance

    Raises:
        ValueError: If file doesn't have required datasets in any format
        KeyError: If required datasets are missing
    """
    path = Path(path)
    bp_range = _validate_bp_range_args(positions, indices, start_bp, end_bp)
    load_idx = None  # when set, bin_counts will be sliced to match data rows
    loaded_bins = None
    loaded_bin_counts = None
    with h5py.File(path, "r") as f:
        data = {}
        datasets = []
        methyl_data = None

        # Try new format first: 'methylation_data' as a group
        centroid_required = ["pos", "tnc", "N", "Sx", "Sx2", "Sm", "Su", "Sc2", "Swx2"]
        sample_required = ["pos", "mC", "uC", "tnc"]
        if "methylation_data" in f:
            methyl_data = f["methylation_data"]
            if isinstance(methyl_data, h5py.Group):
                datasets = list(methyl_data.keys())
                # Centroid: all nine fields + binned_stats required
                if all(d in datasets for d in centroid_required):
                    sel = _selection_for_pos(
                        methyl_data["pos"],
                        positions=positions,
                        indices=indices,
                        start_bp=start_bp,
                        end_bp=end_bp,
                        bp_range=bp_range,
                    )
                    load_idx = sel if isinstance(sel, np.ndarray) else None

                    def _load(key):
                        return _h5_take(methyl_data[key], sel)

                    data = {
                        "pos": _load("pos").astype(np.uint32),
                        "tnc": _load("tnc").astype(np.uint8),
                        "N": _load("N").astype(np.uint32),
                        "Sx": _load("Sx").astype(np.float32),
                        "Sx2": _load("Sx2").astype(np.float32),
                        "Sm": _load("Sm").astype(np.uint32),
                        "Su": _load("Su").astype(np.uint32),
                        "Sc2": _load("Sc2").astype(np.uint32),
                        "Swx2": _load("Swx2").astype(np.float32),
                    }
                    if "bins" not in methyl_data.attrs or "bin_counts" not in datasets:
                        raise ValueError(
                            "Centroid file is missing required ECDF histogram data "
                            "(methylation_data.attrs['bins'] and methylation_data['bin_counts'])"
                        )
                    loaded_bins = int(methyl_data.attrs["bins"])
                    if loaded_bins > 0:
                        loaded_bin_counts = _load("bin_counts")
                    else:
                        raise ValueError(
                            f"Centroid file has invalid ECDF bin count: bins={loaded_bins}. "
                            "Supported centroids require bins >= 1."
                        )
                elif all(d in datasets for d in sample_required):
                    # Sample
                    sel = _selection_for_pos(
                        methyl_data["pos"],
                        positions=positions,
                        indices=indices,
                        start_bp=start_bp,
                        end_bp=end_bp,
                        bp_range=bp_range,
                    )
                    load_idx = sel if isinstance(sel, np.ndarray) else None
                    data = {
                        "pos": _h5_take(methyl_data["pos"], sel).astype(np.uint32),
                        "mC": _h5_take(methyl_data["mC"], sel).astype(np.uint32),
                        "uC": _h5_take(methyl_data["uC"], sel).astype(np.uint32),
                        "tnc": _h5_take(methyl_data["tnc"], sel).astype(np.uint8),
                    }

        # Fallback to old format: datasets at root level
        if not data:
            root_keys = list(f.keys())
            required_core = ["pos", "mC", "uC", "tnc"]

            # Check if required datasets exist at root level
            missing = [d for d in required_core if d not in root_keys]
            if not missing:
                datasets = root_keys
                sel = _selection_for_pos(
                    f["pos"],
                    positions=positions,
                    indices=indices,
                    start_bp=start_bp,
                    end_bp=end_bp,
                    bp_range=bp_range,
                )
                load_idx = sel if isinstance(sel, np.ndarray) else None
                data = {
                    "pos": _h5_take(f["pos"], sel).astype(np.uint32),
                    "mC": _h5_take(f["mC"], sel).astype(np.uint32),
                    "uC": _h5_take(f["uC"], sel).astype(np.uint32),
                    "tnc": _h5_take(f["tnc"], sel).astype(np.uint8),
                }
                if "N" in root_keys:
                    data["N"] = _h5_take(f["N"], sel).astype(np.uint32)
                for col in ["Sx", "Sx2"]:
                    if col in root_keys:
                        data[col] = _h5_take(f[col], sel).astype(np.float32)

        # Fallback to old format: 'methylation_data' as structured array
        if not data and "methylation_data" in f:
            methyl_data = f["methylation_data"]
            if isinstance(methyl_data, h5py.Dataset) and methyl_data.dtype.names:
                # Structured array format (single dataset; must load then filter)
                struct_data = methyl_data[:]
                datasets = list(methyl_data.dtype.names)
                required_core = ["pos", "mC", "uC", "tnc"]
                missing = [d for d in required_core if d not in datasets]
                if not missing:
                    pos_arr = np.asarray(struct_data["pos"], dtype=np.uint32)
                    sel = _selection_for_pos(
                        pos_arr,
                        positions=positions,
                        indices=indices,
                        start_bp=start_bp,
                        end_bp=end_bp,
                        bp_range=bp_range,
                    )
                    load_idx = sel if isinstance(sel, np.ndarray) else None
                    data = {
                        "pos": _h5_take(struct_data["pos"], sel).astype(np.uint32),
                        "mC": _h5_take(struct_data["mC"], sel).astype(np.uint32),
                        "uC": _h5_take(struct_data["uC"], sel).astype(np.uint32),
                        "tnc": _h5_take(struct_data["tnc"], sel).astype(np.uint8),
                    }
                    if "N" in datasets:
                        data["N"] = _h5_take(struct_data["N"], sel).astype(np.uint32)
                    for col in ["Sx", "Sx2"]:
                        if col in datasets:
                            data[col] = _h5_take(struct_data[col], sel).astype(np.float32)

        # If still no data, raise error
        if not data:
            available_keys = list(f.keys())
            raise ValueError(
                f"HDF5 file does not contain required datasets in any recognized format. "
                f"Required: ['pos', 'mC', 'uC', 'tnc']. Available keys: {available_keys}"
            )

        # Centroid: full schema (Sm, Su, Sc2, Swx2) present -> MethylCentroid. Else sample.
        if "Sm" in data and "Su" in data and "Sc2" in data and "Swx2" in data:
            cls = MethylCentroid
        else:
            cls = MethylSample

        # Load metadata from file attributes
        metadata = {}
        if f.attrs:
            import json
            for key, value in f.attrs.items():
                # Parse JSON strings (as written by save_to_h5)
                if isinstance(value, (str, bytes)):
                    try:
                        if isinstance(value, bytes):
                            value = value.decode('utf-8')
                        parsed = json.loads(value)
                        metadata[key] = parsed
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        # Not JSON, use as-is
                        metadata[key] = value
                else:
                    metadata[key] = value

        df = pd.DataFrame(data)
        obj = cls(df, metadata)

        # Binned stats from methylation_data (bins attr + bin_counts); derive bin_edges
        if loaded_bins is not None and loaded_bin_counts is not None:
            bin_edges = np.linspace(0, 1, loaded_bins + 1, dtype=np.float32)
            if hasattr(obj, "set_binned_stats"):
                obj.set_binned_stats(bin_edges, loaded_bin_counts)
            else:
                obj._binned_stats = {"bin_edges": bin_edges, "bin_counts": loaded_bin_counts}

        return obj


def iter_bp_shards(
    path: Union[str, Path],
    shard_bp: int,
    *,
    start_bp: Optional[int] = None,
    end_bp: Optional[int] = None,
) -> Iterator[MethylCentroid | MethylSample]:
    """
    Yield successive non-overlapping genomic shards from a methylation H5 file.

    Each shard is loaded via ``load_from_h5(..., start_bp=..., end_bp=...)`` using
    half-open intervals of width ``shard_bp`` (caller-provided; no package default).
    Empty shards (no sites in the interval) are skipped.

    When *start_bp* / *end_bp* are omitted, the span is taken from the file's
    first and last ``pos`` values (``[pos_min, pos_max + 1)``).
    """
    path = Path(path)
    shard_bp = int(shard_bp)
    if shard_bp <= 0:
        raise ValueError(f"shard_bp must be a positive integer, got {shard_bp}")

    with h5py.File(path, "r") as f:
        pos_dset = _pos_dataset_from_open_file(f)
        n = int(pos_dset.shape[0])
        if n == 0:
            return
        file_min = int(pos_dset[0])
        file_max = int(pos_dset[n - 1])

    outer_start = file_min if start_bp is None else int(start_bp)
    outer_end = (file_max + 1) if end_bp is None else int(end_bp)
    if outer_end < outer_start:
        raise ValueError(f"end_bp ({outer_end}) must be >= start_bp ({outer_start})")

    cur = outer_start
    while cur < outer_end:
        nxt = min(cur + shard_bp, outer_end)
        obj = load_from_h5(path, start_bp=cur, end_bp=nxt)
        if len(obj) == 0:
            if hasattr(obj, "close"):
                obj.close()
        else:
            yield obj
        cur = nxt


def estimate_n_cap_from_sample_path(
    path: Union[str, Path],
    *,
    max_positions: int = 100_000,
    iqr_multiplier: float = 1.5,
    seed: Optional[int] = None,
) -> int:
    """
    Estimate n_cap (coverage cap for outlier limiting) from one sample using IQR on sampled positions.

    Loads a random subset of positions (up to max_positions) from the file. The median is
    estimated from this sample (robust to outliers). The outlier limit is the upper fence
    Q3 + iqr_multiplier*IQR (standard 1.5*IQR rule); positions with coverage above that
    (e.g. re-sequencing duplicates) are capped. Returns n_cap = ceil(upper_fence) for use
    with cap_coverage_binomial.

    Args:
        path: Path to a methylation sample HDF5 file (e.g. 1-CG.h5).
        max_positions: Max number of positions to sample for the estimate.
        iqr_multiplier: Multiplier for IQR (default 1.5 = standard boxplot fence).
        seed: Optional RNG seed for reproducible sampling.

    Returns:
        Integer n_cap >= 1 (ceil of upper fence Q3 + iqr_multiplier*IQR).
    """
    path = Path(path)
    pos = load_pos_from_h5(path)
    n = len(pos)
    if n == 0:
        return 1
    rng = np.random.default_rng(seed)
    n_sample = min(n, max_positions)
    idx = rng.choice(n, size=n_sample, replace=False)
    obj = load_from_h5(path, indices=idx)
    try:
        _, _, n_cap = obj.coverage_iqr_n_cap(
            max_positions=n_sample,
            iqr_multiplier=iqr_multiplier,
            seed=seed,
        )
        return n_cap
    finally:
        if hasattr(obj, "close"):
            obj.close()


def estimate_n_cap_from_sample_path_with_log(
    path: Union[str, Path],
    *,
    max_positions: int = 100_000,
    iqr_multiplier: float = 1.5,
    seed: Optional[int] = None,
) -> tuple[float, float, int]:
    """
    Like estimate_n_cap_from_sample_path but returns (median, upper_fence, n_cap) for logging.
    """
    path = Path(path)
    pos = load_pos_from_h5(path)
    n = len(pos)
    if n == 0:
        return 0.0, 0.0, 1
    rng = np.random.default_rng(seed)
    n_sample = min(n, max_positions)
    idx = rng.choice(n, size=n_sample, replace=False)
    obj = load_from_h5(path, indices=idx)
    try:
        return obj.coverage_iqr_n_cap(
            max_positions=n_sample,
            iqr_multiplier=iqr_multiplier,
            seed=seed,
        )
    finally:
        if hasattr(obj, "close"):
            obj.close()
