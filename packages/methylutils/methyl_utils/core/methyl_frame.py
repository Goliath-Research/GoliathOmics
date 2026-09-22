# methyl_utils/core/methyl_frame.py
from __future__ import annotations

from typing import Optional, Dict, Any, Union, List, Literal
from pathlib import Path

import numpy as np
import pandas as pd

# GPU support (transparent)
import logging
import gc

try:
    from ..gpu_detection import get_cupy, is_gpu_available, cleanup_gpu_memory
except ImportError:
    try:
        from methyl_utils.gpu_detection import get_cupy, is_gpu_available, cleanup_gpu_memory
    except ImportError:
        def get_cupy():  # type: ignore[override]
            return None

        def is_gpu_available():  # type: ignore[override]
            return False

        def cleanup_gpu_memory() -> bool:  # type: ignore[override]
            return False

try:
    import cudf
except ImportError:
    cudf = None

cp = get_cupy()
HAS_GPU = cp is not None and cudf is not None and is_gpu_available()


# Single source of truth — column name → optimal dtype
# Samples: pos, mC, uC, tnc. Centroids: pos, tnc, N, Sx, Sx2, Sm, Su, Sc2, Swx2 (no mC/uC stored).
COLUMN_DTYPES = {
    "pos": "uint32",
    "mC": "uint32",
    "uC": "uint32",
    "tnc": "uint8",
    "N": "uint32",
    "Sx": "float32",
    "Sx2": "float32",
    "Sm": "uint32",
    "Su": "uint32",
    "Sc2": "uint32",
    "Swx2": "float32",
}

# Categorical definitions
ContextDtype = pd.CategoricalDtype(
    categories=["CG", "CHG", "CHH", "UNKNOWN"], ordered=False
)
StrandDtype = pd.CategoricalDtype(categories=["+", "-"], ordered=False)

# TNC byte bit field layout (C-style)
# Bit 7 (MSB): strand (0 = '+', 1 = '-')
# Bits 0-6: TNC value (7 bits, 0-127)
TNC_VALUE_MASK = 0x7F  # Mask for 7-bit TNC value (bits 0-6)
STRAND_SHIFT = 7       # Bit position of strand (MSB)
STRAND_MASK = 0x1      # Mask for strand bit

# Fast lookup tables
_TNC_CONTEXT_CODES = np.array([0] * 32 + [1] * 32 + [2] * 32 + [3] * 32, dtype=np.uint8)
_TNC_STRAND_CODES = np.array([0] * 128 + [1] * 128, dtype=np.uint8)


class MethylFrame:
    _required_cols = {"pos", "mC", "uC", "tnc"}

    def __init__(
        self,
        df: pd.DataFrame | "cudf.DataFrame",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        missing = self._required_cols - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        # Apply optimal dtypes from single source
        dtypes = {
            col: dtype for col, dtype in COLUMN_DTYPES.items() if col in df.columns
        }
        df = df.astype(dtypes)

        # Decode context/strand once
        if "context" not in df.columns:
            # Ensure we are working with a CPU DataFrame if GPU isn't available
            if not HAS_GPU and cudf is not None and isinstance(df, cudf.DataFrame):
                df = df.to_pandas()

            tnc = df["tnc"].values
            # Extract TNC value (bits 0-6) and strand (bit 7) using bit field masks
            if HAS_GPU and hasattr(tnc, "__cuda_array_interface__"):
                try:
                    tnc_context = tnc & TNC_VALUE_MASK
                    strand_codes = (tnc >> STRAND_SHIFT) & STRAND_MASK
                    ctx_codes = cp.asarray(_TNC_CONTEXT_CODES)[tnc_context]
                    df["context"] = cudf.Series(ctx_codes, dtype=ContextDtype)
                    df["strand"] = cudf.Series(strand_codes, dtype=StrandDtype)
                except Exception as e:
                    logging.getLogger(__name__).warning(
                        f"GPU context decode failed; falling back to CPU: {e}"
                    )
                    if cudf is not None and isinstance(df, cudf.DataFrame):
                        df = df.to_pandas()
                    tnc = df["tnc"].values
                    tnc_context = tnc & TNC_VALUE_MASK
                    strand_codes = (tnc >> STRAND_SHIFT) & STRAND_MASK
                    df["context"] = pd.Categorical.from_codes(
                        _TNC_CONTEXT_CODES[tnc_context], dtype=ContextDtype
                    )
                    df["strand"] = pd.Categorical.from_codes(
                        strand_codes, dtype=StrandDtype
                    )
            else:
                tnc_context = tnc & TNC_VALUE_MASK
                strand_codes = (tnc >> STRAND_SHIFT) & STRAND_MASK
                df["context"] = pd.Categorical.from_codes(
                    _TNC_CONTEXT_CODES[tnc_context], dtype=ContextDtype
                )
                df["strand"] = pd.Categorical.from_codes(
                    strand_codes, dtype=StrandDtype
                )

        self._df = df.sort_values("pos").reset_index(drop=True)
        self._metadata = metadata or {}
        self._binned_stats = None

    @property
    def df(self):
        return self._df

    @property
    def is_gpu(self) -> bool:
        return HAS_GPU and cudf is not None and isinstance(self._df, cudf.DataFrame)

    @property
    def pos(self):
        return self._df["pos"]

    @property
    def mC(self):
        return self._df["mC"]

    @property
    def uC(self):
        return self._df["uC"]

    @property
    def coverage(self):
        return self.mC + self.uC

    @property
    def context(self):
        return self._df["context"]

    @property
    def strand(self):
        return self._df["strand"]

    def to_gpu(self):
        if not HAS_GPU or self.is_gpu:
            return self
        return type(self)(cudf.from_pandas(self._df.to_pandas()), self._metadata)

    def to_cpu(self):
        if not self.is_gpu:
            return self
        return type(self)(self._df.to_pandas(), self._metadata)

    def __len__(self):
        return len(self._df)

    def __getitem__(self, key):
        return type(self)(self._df.loc[key], self._metadata.copy())

    def close(self, free_gpu_pool: bool = False) -> None:
        """
        Release references and (optionally) free GPU memory pools.

        Note: freeing the GPU pool is global and may impact performance if called
        frequently. Use free_gpu_pool=True only when you need to return memory
        to the system.
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True

        df = getattr(self, "_df", None)
        self._df = None
        self._binned_stats = None
        self._metadata = {}

        try:
            if free_gpu_pool and df is not None and self.is_gpu:
                cleanup_gpu_memory()
        except Exception as e:
            logging.getLogger(__name__).warning(
                f"GPU memory cleanup failed during close: {e}"
            )

        gc.collect()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close(free_gpu_pool=False)
        return False

    def __del__(self):
        try:
            self.close(free_gpu_pool=False)
        except Exception:
            pass

    def _get_values(self, series) -> np.ndarray:
        """Safely extract values from pandas/cudf Series, handling GPU compatibility.

        Args:
            series: pandas or cudf Series

        Returns:
            numpy array of values
        """
        if hasattr(series, 'values'):
            return series.values
        else:
            return np.asarray(series)

    @property
    def metadata(self) -> Dict[str, Any]:
        """Get metadata dictionary."""
        return self._metadata

    @metadata.setter
    def metadata(self, value: Dict[str, Any]):
        """Set metadata dictionary."""
        self._metadata = value or {}

    @property
    def binned_stats(self) -> Optional[Dict[str, Any]]:
        """Optional binned stats (bin_edges + bin_counts)."""
        return self._binned_stats

    def set_binned_stats(self, bin_edges: np.ndarray, bin_counts: np.ndarray) -> None:
        """Attach binned stats to this object for HDF5 persistence."""
        bin_edges_arr = np.asarray(bin_edges, dtype=np.float32)
        bin_counts_arr = np.asarray(bin_counts)
        if bin_edges_arr.ndim != 1 or len(bin_edges_arr) < 2:
            raise ValueError("bin_edges must be a 1D array with at least two edges")
        n_bins = int(len(bin_edges_arr) - 1)
        if n_bins < 1:
            raise ValueError("ECDF centroids require at least one histogram bin")
        if bin_counts_arr.ndim != 2:
            raise ValueError("bin_counts must be a 2D array of shape (n_positions, n_bins)")
        if bin_counts_arr.shape[1] != n_bins:
            raise ValueError(
                f"bin_counts second dimension must match len(bin_edges)-1 ({n_bins}), got {bin_counts_arr.shape[1]}"
            )
        self._binned_stats = {
            "bin_edges": bin_edges_arr,
            "bin_counts": bin_counts_arr,
        }

    # Metadata properties (read-write for easy manipulation)
    @property
    def laboratory(self) -> Optional[str]:
        """Get laboratory name from metadata."""
        return self._metadata.get("laboratory") if self._metadata else None

    @laboratory.setter
    def laboratory(self, value: str):
        """Set laboratory name in metadata."""
        if self._metadata is None:
            self._metadata = {}
        self._metadata["laboratory"] = value

    @property
    def disease(self) -> Optional[str]:
        """Get disease from metadata."""
        return self._metadata.get("disease") if self._metadata else None

    @disease.setter
    def disease(self, value: str):
        """Set disease in metadata."""
        if self._metadata is None:
            self._metadata = {}
        self._metadata["disease"] = value

    @property
    def group(self) -> Optional[str]:
        """Get group identifier from metadata."""
        return self._metadata.get("group") if self._metadata else None

    @group.setter
    def group(self, value: str):
        """Set group identifier in metadata."""
        if self._metadata is None:
            self._metadata = {}
        self._metadata["group"] = value

    @property
    def batch(self) -> Optional[str]:
        """Get batch identifier from metadata."""
        return self._metadata.get("batch") if self._metadata else None

    @batch.setter
    def batch(self, value: str):
        """Set batch identifier in metadata."""
        if self._metadata is None:
            self._metadata = {}
        self._metadata["batch"] = value

    @property
    def chromosome(self) -> Optional[str]:
        """Get chromosome from metadata."""
        return self._metadata.get("chromosome") if self._metadata else None

    @chromosome.setter
    def chromosome(self, value: str):
        """Set chromosome in metadata."""
        if self._metadata is None:
            self._metadata = {}
        self._metadata["chromosome"] = value

    @property
    def context_metadata(self) -> Optional[str]:
        """Get methylation context from metadata."""
        return self._metadata.get("context") if self._metadata else None

    @context_metadata.setter
    def context_metadata(self, value: str):
        """Set methylation context in metadata."""
        if self._metadata is None:
            self._metadata = {}
        self._metadata["context"] = value

    @classmethod
    def resolve_samples_used_paths(
        cls,
        raw: List[str],
        metadata_base: Optional[str] = None,
        fallback_base: Optional[str] = None,
    ) -> List[str]:
        """
        Turn centroid metadata sample entries into absolute sample-directory paths.

        Supports:
        - Legacy rows: full paths to sample directories (used as-is when they exist).
        - Compact rows: directory basenames with ``samples_base_path`` in the same H5 metadata,
          or ``fallback_base`` (e.g. project ``samples_base_path``) when metadata has no base.
        """
        if not raw:
            return []
        bases = [b for b in (metadata_base, fallback_base) if b]
        out: List[str] = []
        for item in raw:
            if item is None:
                continue
            s = str(item).strip()
            if not s:
                continue
            p = Path(s)
            if p.exists():
                out.append(str(p.resolve()))
                continue
            resolved = False
            for base in bases:
                joined = Path(base).expanduser() / s
                if joined.exists():
                    out.append(str(joined.resolve()))
                    resolved = True
                    break
            if resolved:
                continue
            if p.is_absolute():
                out.append(str(p))
            elif bases:
                out.append(str(Path(bases[0]).expanduser() / s))
            else:
                out.append(s)
        return out

    @property
    def samples(self) -> List[str]:
        """Resolved list of sample directory paths for this centroid (from metadata)."""
        if not self._metadata:
            return []
        raw = self._metadata.get("sample_paths") or self._metadata.get("samples_used", [])
        if not raw:
            return []
        return type(self).resolve_samples_used_paths(
            [str(x) for x in raw],
            self._metadata.get("samples_base_path"),
            None,
        )

    @property
    def group_name(self) -> str:
        """Group name or label for this centroid (from metadata)."""
        return self._metadata.get('group_name', 'Unknown') if self._metadata else 'Unknown'

    @property
    def is_centroid(self) -> bool:
        """Check if this is a centroid."""
        return False

    @property
    def sample_type(self) -> str:
        """Get the sample type."""
        return "sample"

    @property
    def position_count(self) -> int:
        """Number of genomic positions."""
        return len(self._df)

    def clamp_zero_coverage(self) -> int:
        """Set unmethylated counts to 1 where coverage is 0 to avoid div-by-zero. Returns number of positions clamped."""
        cov = self._get_values(self.coverage)
        zero_mask = np.asarray(cov == 0)
        n = int(np.sum(zero_mask))
        if n > 0:
            if "Su" in self._df.columns:
                self._df.loc[zero_mask, "Su"] = 1
            else:
                self._df.loc[zero_mask, "uC"] = 1
        return n

    @property
    def memory_usage_mb(self) -> float:
        """Memory usage in megabytes."""
        if self.is_gpu:
            return self._df.memory_usage(deep=True).sum() / (1024 * 1024)
        else:
            return self._df.memory_usage(deep=True).sum() / (1024 * 1024)

    def apply_mask(self, mask_or_indices: Union[np.ndarray, List[int], bool]) -> "MethylFrame":
        """
        Apply a boolean mask or integer indices to filter the data.
        Preserves binned_stats when present (slices bin_counts to match filtered rows).

        Args:
            mask_or_indices: Boolean mask array or integer indices

        Returns:
            New instance with filtered data
        """
        if isinstance(mask_or_indices, (list, np.ndarray)) and len(mask_or_indices) > 0:
            # Resolve to (new_instance, indices_for_binned)
            if isinstance(mask_or_indices, np.ndarray) and mask_or_indices.dtype == bool:
                new_self = type(self)(self._df.loc[mask_or_indices], self._metadata.copy())
                binned_idx = mask_or_indices
            elif isinstance(mask_or_indices[0], bool):
                mask_array = np.asarray(mask_or_indices, dtype=bool)
                new_self = type(self)(self._df.loc[mask_array], self._metadata.copy())
                binned_idx = mask_array
            else:
                indices_array = np.asarray(mask_or_indices, dtype=np.int32)
                new_self = type(self)(self._df.iloc[indices_array], self._metadata.copy())
                binned_idx = indices_array
            # Preserve binned_stats so ECDF/KS work after load_and_align
            if getattr(self, "_binned_stats", None) and "bin_edges" in self._binned_stats and "bin_counts" in self._binned_stats:
                bin_edges = self._binned_stats["bin_edges"]
                bin_counts = np.asarray(self._binned_stats["bin_counts"])
                if bin_counts.ndim == 2 and bin_counts.shape[0] == len(self._df):
                    new_self.set_binned_stats(bin_edges, bin_counts[binned_idx, :])
            return new_self
        return self

    def align_to_positions(self, positions: np.ndarray) -> "MethylFrame":
        """
        Align to reference positions, keeping only common positions.

        Args:
            positions: Reference positions to align to

        Returns:
            New instance aligned to reference positions
        """
        pos_values = self.pos.values if hasattr(self.pos, 'values') else np.asarray(self.pos)
        want = np.asarray(positions, dtype=np.uint32)
        common_pos, idx1, _ = np.intersect1d(
            pos_values, want, assume_unique=True, return_indices=True
        )
        return self.apply_mask(np.asarray(idx1, dtype=np.int32))

    def get_methylation_levels(self) -> np.ndarray:
        """Get methylation levels (mC / (mC + uC))."""
        cov = self.coverage.values if hasattr(self.coverage, 'values') else np.asarray(self.coverage)
        mC_vals = self.mC.values if hasattr(self.mC, 'values') else np.asarray(self.mC)
        with np.errstate(divide='ignore', invalid='ignore'):
            return np.where(cov > 0, mC_vals / cov, 0.0)

    def get_coverage(self) -> np.ndarray:
        """Get total coverage (mC + uC)."""
        return self.coverage.values if hasattr(self.coverage, 'values') else np.asarray(self.coverage)

    def lookup_at_positions(
        self,
        reference_positions: np.ndarray,
        *,
        min_coverage: int = 1,
        missing_value: float = np.nan,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Return methylation values in *reference_positions* order.

        The method performs exact-position lookup, preserving caller order, and
        returns both values and an availability mask. Positions that are missing
        (or that fail coverage threshold) are marked unavailable.
        """
        dmp_arr = np.asarray(reference_positions, dtype=np.uint32).ravel()
        out = np.full(dmp_arr.shape[0], float(missing_value), dtype=np.float64)
        availability = np.zeros(dmp_arr.shape[0], dtype=bool)
        if dmp_arr.size == 0 or len(self) == 0:
            return out, availability

        pos_vals = np.asarray(self.pos, dtype=np.uint32).ravel()
        meth_vals = np.asarray(self.get_methylation_levels(), dtype=np.float64).ravel()
        cov_vals = np.asarray(self.get_coverage(), dtype=np.float64).ravel()
        n = min(pos_vals.size, meth_vals.size, cov_vals.size)
        if n == 0:
            return out, availability

        pos_vals = pos_vals[:n]
        meth_vals = meth_vals[:n]
        cov_vals = cov_vals[:n]

        order = np.argsort(pos_vals, kind="mergesort")
        sp = pos_vals[order]
        sm = meth_vals[order]
        sc = cov_vals[order]

        idx = np.searchsorted(sp, dmp_arr, side="left").astype(np.int32, copy=False)
        in_range = idx < sp.size
        safe_idx = np.minimum(idx, max(sp.size - 1, 0))
        matched = in_range & (sp[safe_idx] == dmp_arr)
        if not np.any(matched):
            return out, availability

        match_idx = np.flatnonzero(matched)
        raw = sm[safe_idx[match_idx]]
        cov = sc[safe_idx[match_idx]]
        finite = np.isfinite(raw)
        if int(min_coverage) > 0:
            finite &= cov >= float(min_coverage)
        if np.any(finite):
            clip_vals = np.clip(raw[finite], 0.0, 1.0)
            out_idx = match_idx[finite]
            out[out_idx] = clip_vals
            availability[out_idx] = True
        return out, availability

    @classmethod
    def load_from_h5(
        cls,
        path: Union[str, Path],
        positions: Optional[np.ndarray] = None,
        indices: Optional[np.ndarray] = None,
        *,
        start_bp: Optional[int] = None,
        end_bp: Optional[int] = None,
        align_positions: bool = True,
    ) -> "MethylFrame":
        """
        Load from HDF5 file.

        When ``positions`` is given, only matching rows are read from disk (same
        hyperslice approach used in detector/validation extraction). When
        ``indices`` is given, only those H5 row indices are loaded. ``indices``
        takes precedence over ``positions``. When ``start_bp`` / ``end_bp`` are
        given, load the contiguous half-open genomic range ``[start_bp, end_bp)``
        (cannot combine with ``positions`` / ``indices``).

        Args:
            path: Path to HDF5 file
            positions: Optional positions to load; only these rows are read (saves memory).
            indices: Optional row indices to load directly from the H5 file.
            start_bp: Optional inclusive genomic start (bp) for ranged load.
            end_bp: Optional exclusive genomic end (bp) for ranged load.
            align_positions: If True and ``positions`` is provided (without
                ``indices``), align in-memory output to exactly the requested
                positions.

        Returns:
            MethylSample or MethylCentroid instance
        """
        from .io import load_from_h5
        result = load_from_h5(
            path,
            positions=positions,
            indices=indices,
            start_bp=start_bp,
            end_bp=end_bp,
        )
        if align_positions and indices is None and positions is not None:
            result = result.align_to_positions(positions)
        return result

    @staticmethod
    def position_indices_from_h5(
        path: Union[str, Path],
        positions: np.ndarray,
        *,
        pos_cache: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Resolve H5 row indices for the requested genomic positions.

        This keeps the low-level indexed loading behavior centralized in
        ``MethylSample``/``MethylFrame`` so downstream packages can avoid
        re-implementing indexing logic.
        """
        from .io import _indices_for_positions, load_pos_from_h5

        pos_arr = np.asarray(pos_cache, dtype=np.uint32) if pos_cache is not None else load_pos_from_h5(path)
        want = np.asarray(positions, dtype=np.uint32)
        return np.asarray(_indices_for_positions(pos_arr, want), dtype=np.int32)

    def save_to_h5(self, path: Union[str, Path], compressed: bool = True) -> Path:
        """
        Save to HDF5 file.

        Args:
            path: Path to save to
            compressed: Whether to use compression

        Returns:
            Path to saved file
        """
        path = Path(path)
        import h5py
        import hdf5plugin

        with h5py.File(path, "w") as f:
            # Store metadata as attributes
            for key, value in self._metadata.items():
                if isinstance(value, (dict, list)):
                    import json
                    f.attrs[key] = json.dumps(value)
                else:
                    f.attrs[key] = value

            # Create methylation_data group
            group = f.create_group("methylation_data")

            # Store core columns
            for col in ["pos", "mC", "uC", "tnc"]:
                if col in self._df.columns:
                    data = self._df[col].values if hasattr(self._df[col], 'values') else np.asarray(self._df[col])
                    if compressed:
                        group.create_dataset(col, data=data, **hdf5plugin.Blosc())
                    else:
                        group.create_dataset(col, data=data)

            # Store centroid columns if present (single data-driven centroid: N, Sx, Sx2 only)
            for col in ["N", "Sx", "Sx2"]:
                if col in self._df.columns:
                    data = self._df[col].values if hasattr(self._df[col], 'values') else np.asarray(self._df[col])
                    if compressed:
                        group.create_dataset(col, data=data, **hdf5plugin.Blosc())
                    else:
                        group.create_dataset(col, data=data)

            # Optional binned stats: bins attr + bin_counts dataset in methylation_data only (no bin_edges)
            if hasattr(self, "_binned_stats") and self._binned_stats:
                binned = self._binned_stats
                if "bin_edges" in binned and "bin_counts" in binned:
                    bin_edges = binned["bin_edges"]
                    bin_counts = binned["bin_counts"]
                    n_bins = int(len(bin_edges) - 1)
                    if n_bins > 0:
                        group.attrs["bins"] = n_bins
                        if compressed:
                            group.create_dataset("bin_counts", data=bin_counts, **hdf5plugin.Blosc())
                        else:
                            group.create_dataset("bin_counts", data=bin_counts)

        return path

    @classmethod
    def from_sample_data(
        cls,
        pos: np.ndarray,
        mC: np.ndarray,
        uC: np.ndarray,
        tnc: np.ndarray,
        metadata: Optional[Dict[str, Any]] = None
    ) -> "MethylSample":
        """
        Create MethylSample from arrays.

        Args:
            pos: Genomic positions
            mC: Methylated counts
            uC: Unmethylated counts
            tnc: Trinucleotide context bytes
            metadata: Optional metadata

        Returns:
            MethylSample instance
        """
        df = pd.DataFrame({
            "pos": pos,
            "mC": mC,
            "uC": uC,
            "tnc": tnc,
        })
        return MethylSample(df, metadata)

# Single sample class
class MethylSample(MethylFrame):
    _required_cols = {"pos", "mC", "uC", "tnc"}

    def close(self, free_gpu_pool: bool = False) -> None:
        """
        Release references and free memory used by this sample.

        Call when done with the sample to avoid memory leaks (e.g. after
        extracting data or when evicting from a cache). Idempotent; safe to
        call more than once. After close(), the instance must not be used.
        """
        super().close(free_gpu_pool=free_gpu_pool)

    def cap_coverage_binomial(
        self,
        n_cap: int,
        *,
        seed: Optional[int] = None,
    ) -> "MethylSample":
        """
        Cap per-CpG coverage by binomial thinning (in-place).

        For each position where total coverage n = mC + uC > n_cap, reduce counts
        by random thinning: keep probability p = n_cap / n, draw mC' ~ Binomial(mC, p)
        and uC' ~ Binomial(uC, p). Keeps methylation proportion unbiased in expectation
        while preventing ultra-deep positions from dominating. Mutates this sample
        in place and returns self for chaining.

        Args:
            n_cap: Maximum coverage per position; positions with coverage > n_cap are thinned.
            seed: Optional RNG seed for reproducibility.

        Returns:
            self (for chaining).
        """
        if n_cap < 1:
            raise ValueError("n_cap must be >= 1")
        self = self.to_cpu()
        mC = np.asarray(self._get_values(self.mC), dtype=np.uint32)
        uC = np.asarray(self._get_values(self.uC), dtype=np.uint32)
        cov = mC.astype(np.float64) + uC.astype(np.float64)
        over = cov > n_cap
        if not np.any(over):
            return self
        rng = np.random.default_rng(seed)
        p_over = n_cap / cov[over]
        mC_new = mC.copy()
        uC_new = uC.copy()
        mC_new[over] = rng.binomial(mC[over].astype(np.int64), p_over).astype(np.uint32)
        uC_new[over] = rng.binomial(uC[over].astype(np.int64), p_over).astype(np.uint32)
        self._df = self._df.copy()
        self._df.loc[over, "mC"] = mC_new[over]
        self._df.loc[over, "uC"] = uC_new[over]
        return self

    def median_coverage(
        self,
        *,
        max_positions: int = 100_000,
        seed: Optional[int] = None,
    ) -> float:
        """
        Median coverage across positions (sampled for large samples).

        For samples with more than max_positions positions, uses a random subset
        so the result is fast and scalable (e.g. for 80M+ positions). Intended
        for cohort-level outlier detection.

        Args:
            max_positions: Cap the number of positions used to compute the median.
            seed: Optional RNG seed when sampling positions.

        Returns:
            Median of (mC + uC) over positions (or over a random subset).
        """
        cov = self.get_coverage()
        n = len(cov)
        if n == 0:
            return 0.0
        if n <= max_positions:
            return float(np.median(cov))
        rng = np.random.default_rng(seed)
        idx = rng.choice(n, size=max_positions, replace=False)
        return float(np.median(cov[idx]))

    def coverage_iqr_n_cap(
        self,
        *,
        max_positions: int = 100_000,
        iqr_multiplier: float = 1.5,
        seed: Optional[int] = None,
    ) -> tuple[float, float, int]:
        """
        Estimate median, upper fence (Q3 + k*IQR), and n_cap from a random sample of positions.

        Uses the same random subset of positions as median_coverage. The median is robust
        to outliers (e.g. re-sequencing duplicates). The outlier limit is the upper fence
        Q3 + iqr_multiplier*IQR (standard 1.5*IQR boxplot rule); positions with coverage
        above that are considered impossible given the distribution and should be capped.

        Args:
            max_positions: Cap the number of positions used (same as median_coverage).
            iqr_multiplier: Multiplier for IQR (default 1.5 = standard boxplot fence).
            seed: Optional RNG seed when sampling positions.

        Returns:
            (median, upper_fence, n_cap) where n_cap = max(1, ceil(upper_fence)).
            If IQR == 0, n_cap is set from ceil(median) so we do not cap everything.
        """
        cov = self.get_coverage()
        n = len(cov)
        if n == 0:
            return 0.0, 0.0, 1
        if n <= max_positions:
            cov_sample = np.asarray(cov, dtype=np.float64)
        else:
            rng = np.random.default_rng(seed)
            idx = rng.choice(n, size=max_positions, replace=False)
            cov_sample = np.asarray(cov, dtype=np.float64)[idx]
        median = float(np.median(cov_sample))
        q1 = float(np.percentile(cov_sample, 25))
        q3 = float(np.percentile(cov_sample, 75))
        iqr = q3 - q1
        if iqr <= 0:
            upper_fence = float(q3) if q3 > 0 else median
            n_cap = max(1, int(np.ceil(upper_fence)))
        else:
            upper_fence = q3 + iqr_multiplier * iqr
            n_cap = max(1, int(np.ceil(upper_fence)))
        return median, upper_fence, n_cap

    def mean_coverage(self) -> float:
        """
        Mean coverage across positions (single-pass, O(n)).

        Returns:
            Mean of (mC + uC) over all positions.
        """
        cov = self.get_coverage()
        n = len(cov)
        if n == 0:
            return 0.0
        return float(np.mean(cov))

# Single centroid type: pos, tnc, N, Sx, Sx2, Sm, Su, Sc2, Swx2 + binned_stats (required).
# mean/variance from Sx, Sx2, N; weighted_mean/weighted_variance from Sm, Su, Sc2, Swx2; coverage = Sm+Su.
class MethylCentroid(MethylFrame):
    _required_cols = {"pos", "tnc", "N", "Sx", "Sx2", "Sm", "Su", "Sc2", "Swx2"}
    _required_stats = {"Sx", "Sx2"}

    @property
    def N(self):
        return self._df["N"]

    @property
    def is_centroid(self) -> bool:
        return True

    @property
    def sample_type(self) -> str:
        return "centroid"

    def get_sample_count(self) -> np.ndarray:
        return self.N.values if hasattr(self.N, "values") else np.asarray(self.N)

    def get_methylation_levels(self) -> np.ndarray:
        """Coverage-weighted methylation fraction Sm / (Sm + Su)."""
        wm = self.weighted_mean
        return wm.values if hasattr(wm, "values") else np.asarray(wm)

    @property
    def mean(self):
        """Unbiased sample mean of proportions (Sx/N). Distribution-agnostic; do not override in subclasses."""
        col = "_mean_sx_n"
        if col not in self._df.columns:
            N = self.N
            denom = N if getattr(N, "clip", None) else np.maximum(np.asarray(N), 1)
            if hasattr(denom, "clip"):
                denom = denom.clip(lower=1)
            else:
                denom = np.maximum(np.asarray(denom), 1)
            self._df[col] = (self.Sx / denom).astype("float64")
            if hasattr(self._df[col], "clip"):
                self._df[col] = self._df[col].clip(0.0, 1.0)
        return self._df[col]

    @property
    def variance(self):
        """Unbiased sample variance (Sx2 - Sx²/N)/(N-1). Distribution-agnostic; do not override in subclasses."""
        col = "_var_unbiased"
        if col not in self._df.columns:
            N = self._get_values(self.N).astype(np.float64)
            Sx = self._get_values(self.Sx).astype(np.float64)
            Sx2 = self._get_values(self.Sx2).astype(np.float64)
            denom = np.maximum(N - 1.0, 1.0)
            var = np.maximum((Sx2 - (Sx ** 2) / np.maximum(N, 1.0)) / denom, 1e-12)
            if self.is_gpu:
                self._df[col] = cudf.Series(var, dtype="float64", index=self._df.index)
            else:
                self._df[col] = pd.Series(var, dtype="float64", index=self._df.index)
        return self._df[col]



    # Adaptive mean: same as mean (Sx/N) so we always use the sample mean of proportions.
    @property
    def adaptive_mean(self):
        """Same as mean (Sx/N). Kept for API compatibility with MethylDetector."""
        return self.mean

    # Lightning-fast context filters
    def cg(self):
        return self[self.context == "CG"]

    def chg(self):
        return self[self.context == "CHG"]

    def chh(self):
        return self[self.context == "CHH"]

    @property
    def Sx(self):
        """Sum of methylation levels."""
        return self._df["Sx"]

    @property
    def Sx2(self):
        """Sum of squared methylation levels."""
        return self._df["Sx2"]

    @property
    def Sm(self):
        """Sum of methylated counts across samples."""
        return self._df["Sm"]

    @property
    def Su(self):
        """Sum of unmethylated counts across samples."""
        return self._df["Su"]

    @property
    def methylated_counts(self):
        """Sum of methylated counts across samples (Sm). Use this instead of internal Sm."""
        return self.Sm

    @property
    def unmethylated_counts(self):
        """Sum of unmethylated counts across samples (Su). Use this instead of internal Su."""
        return self.Su

    @property
    def Sc2(self):
        """Sum of squared coverages (c_i^2) across samples."""
        return self._df["Sc2"]

    @property
    def Swx2(self):
        """Sum of c_i * x_i^2 across samples (for weighted variance)."""
        return self._df["Swx2"]

    @property
    def coverage(self):
        """Total coverage (Sm + Su). Use e.g. c.coverage >= min_coverage."""
        Sm = self._get_values(self.Sm).astype(np.uint64)
        Su = self._get_values(self.Su).astype(np.uint64)
        cov = Sm + Su
        if self.is_gpu:
            return cudf.Series(cov, dtype="uint64", index=self._df.index)
        return pd.Series(cov, dtype="uint64", index=self._df.index)

    @property
    def mC(self):
        """Derived per-position mean methylated count (Sm/N) for compatibility."""
        N = self._get_values(self.N).astype(np.float64)
        denom = np.maximum(N, 1.0)
        out = (self._get_values(self.Sm).astype(np.float64) / denom).astype(np.uint32)
        if self.is_gpu:
            return cudf.Series(out, dtype="uint32", index=self._df.index)
        return pd.Series(out, dtype="uint32", index=self._df.index)

    @property
    def uC(self):
        """Derived per-position mean unmethylated count (Su/N) for compatibility."""
        N = self._get_values(self.N).astype(np.float64)
        denom = np.maximum(N, 1.0)
        out = (self._get_values(self.Su).astype(np.float64) / denom).astype(np.uint32)
        if self.is_gpu:
            return cudf.Series(out, dtype="uint32", index=self._df.index)
        return pd.Series(out, dtype="uint32", index=self._df.index)

    @property
    def weighted_mean(self):
        """Coverage-weighted mean: Sm / (Sm + Su)."""
        Sm = self._get_values(self.Sm).astype(np.float64)
        Su = self._get_values(self.Su).astype(np.float64)
        tot = Sm + Su
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(tot > 0, Sm / tot, 0.0)
        if self.is_gpu:
            return cudf.Series(out, dtype="float64", index=self._df.index)
        return pd.Series(out, dtype="float64", index=self._df.index)

    @property
    def weighted_variance(self):
        """Coverage-weighted population variance: Swx2/(Sm+Su) - (Sm/(Sm+Su))^2."""
        Sm = self._get_values(self.Sm).astype(np.float64)
        Su = self._get_values(self.Su).astype(np.float64)
        Swx2 = self._get_values(self.Swx2).astype(np.float64)
        tot = Sm + Su
        with np.errstate(divide="ignore", invalid="ignore"):
            xw = np.where(tot > 0, Sm / tot, 0.0)
            vw = np.where(tot > 0, Swx2 / tot - xw * xw, 0.0)
            vw = np.maximum(vw, 0.0)
        if self.is_gpu:
            return cudf.Series(vw, dtype="float64", index=self._df.index)
        return pd.Series(vw, dtype="float64", index=self._df.index)

    @property
    def mean_coverage(self) -> float:
        """Mean coverage (Sm+Su)/N over positions with N > 0."""
        cov = self.get_coverage()
        N = self._get_values(self.N)
        n = len(cov)
        if n == 0:
            return 0.0
        N = np.asarray(N, dtype=np.float64)
        denom = np.maximum(N, 1.0)
        return float(np.mean(cov / denom))

    def save_to_h5(self, path: Union[str, Path], compressed: bool = True) -> Path:
        """Save centroid to HDF5 with pos, tnc, N, Sx, Sx2, Sm, Su, Sc2, Swx2 and binned_stats (required)."""
        path = Path(path)
        import h5py
        import hdf5plugin
        if not getattr(self, "_binned_stats", None) or "bin_edges" not in self._binned_stats or "bin_counts" not in self._binned_stats:
            raise ValueError("Centroid must have binned_stats (bin_edges, bin_counts) before saving to H5")
        with h5py.File(path, "w") as f:
            for key, value in self._metadata.items():
                if isinstance(value, (dict, list)):
                    import json
                    f.attrs[key] = json.dumps(value)
                else:
                    f.attrs[key] = value
            group = f.create_group("methylation_data")
            for col in ["pos", "tnc", "N", "Sx", "Sx2", "Sm", "Su", "Sc2", "Swx2"]:
                data = self._get_values(self._df[col])
                data = np.asarray(data)
                if compressed:
                    group.create_dataset(col, data=data, **hdf5plugin.Blosc())
                else:
                    group.create_dataset(col, data=data)
            bin_edges = self._binned_stats["bin_edges"]
            bin_counts = self._binned_stats["bin_counts"]
            n_bins = int(len(bin_edges) - 1)
            if n_bins < 1:
                raise ValueError("Centroid must have binned_stats with bins > 0 before saving to H5")
            group.attrs["bins"] = n_bins
            if compressed:
                group.create_dataset("bin_counts", data=np.asarray(bin_counts), **hdf5plugin.Blosc())
            else:
                group.create_dataset("bin_counts", data=np.asarray(bin_counts))
        return path

    def to_numpy(self, extended: bool = True) -> np.ndarray:
        """Convert to structured array (pos, tnc, N, Sx, Sx2, Sm, Su, Sc2, Swx2)."""
        from .. import METHYL_CENTROID_DTYPE
        df_cpu = self.to_cpu()._df
        dtype = np.dtype(METHYL_CENTROID_DTYPE)
        data = np.empty(len(df_cpu), dtype=dtype)
        data["pos"] = np.asarray(self._get_values(df_cpu["pos"]), dtype=np.uint32)
        data["tnc"] = np.asarray(self._get_values(df_cpu["tnc"]), dtype=np.uint8)
        data["N"] = np.asarray(self._get_values(df_cpu["N"]), dtype=np.uint32)
        data["Sx"] = np.asarray(self._get_values(df_cpu["Sx"]), dtype=np.float32)
        data["Sx2"] = np.asarray(self._get_values(df_cpu["Sx2"]), dtype=np.float32)
        data["Sm"] = np.asarray(self._get_values(df_cpu["Sm"]), dtype=np.uint32)
        data["Su"] = np.asarray(self._get_values(df_cpu["Su"]), dtype=np.uint32)
        data["Sc2"] = np.asarray(self._get_values(df_cpu["Sc2"]), dtype=np.uint32)
        data["Swx2"] = np.asarray(self._get_values(df_cpu["Swx2"]), dtype=np.float32)
        return data

    def add_sample(
        self,
        sample: "MethylSample",
        residualize_apply=None,
        sample_path=None,
    ) -> "MethylCentroid":
        """
        Add a sample to this centroid, returning a new MethylCentroid.
        Accumulates N, Sx, Sx2, Sm, Su, Sc2, Swx2 and binned_stats.
        """
        centroid_cpu = self.to_cpu()
        sample_cpu = sample.to_cpu()
        centroid_pos = np.asarray(centroid_cpu.pos.values, dtype=np.uint32)
        centroid_Sm = np.asarray(centroid_cpu.Sm.values, dtype=np.uint32)
        centroid_Su = np.asarray(centroid_cpu.Su.values, dtype=np.uint32)
        centroid_Sc2 = np.asarray(centroid_cpu.Sc2.values, dtype=np.uint32)
        centroid_Swx2 = np.asarray(centroid_cpu.Swx2.values, dtype=np.float32)
        centroid_N = np.asarray(centroid_cpu.N.values, dtype=np.uint32)
        centroid_Sx = np.asarray(centroid_cpu.Sx.values, dtype=np.float32)
        centroid_Sx2 = np.asarray(centroid_cpu.Sx2.values, dtype=np.float32)
        centroid_tnc = np.asarray(centroid_cpu._df["tnc"].values, dtype=np.uint8)
        sample_pos = np.asarray(sample_cpu.pos.values, dtype=np.uint32)
        sample_mC = np.asarray(sample_cpu.mC.values, dtype=np.uint32)
        sample_uC = np.asarray(sample_cpu.uC.values, dtype=np.uint32)
        sample_tnc = np.asarray(sample_cpu._df["tnc"].values, dtype=np.uint8)
        sample_c = sample_mC.astype(np.uint32) + sample_uC.astype(np.uint32)
        with np.errstate(divide="ignore", invalid="ignore"):
            sample_mean = np.where(sample_c > 0, sample_mC.astype(np.float32) / sample_c.astype(np.float32), 0.0)
        if residualize_apply is not None:
            path = sample_path if sample_path is not None else getattr(sample, "sample_id", None)
            adj = np.asarray(
                residualize_apply(path, sample_pos, sample_mean),
                dtype=np.float64,
            )
            if adj.shape != sample_mean.shape:
                raise ValueError("residualize_apply returned the wrong length")
            sample_mean = adj.astype(np.float32, copy=False)
        # c_i * x_i^2 = mC_i^2 / c_i
        sample_swx2 = np.where(sample_c > 0, (sample_mC.astype(np.float64) ** 2) / sample_c.astype(np.float64), 0.0).astype(np.float32)
        common_pos, idx_centroid, idx_sample = np.intersect1d(
            centroid_pos, sample_pos, assume_unique=True, return_indices=True
        )
        common_mask_sample = np.isin(sample_pos, centroid_pos)
        if len(common_pos) > 0:
            centroid_Sm[idx_centroid] += sample_mC[idx_sample]
            centroid_Su[idx_centroid] += sample_uC[idx_sample]
            centroid_Sc2[idx_centroid] += (sample_c[idx_sample].astype(np.uint64) ** 2).astype(np.uint32)
            centroid_Swx2[idx_centroid] += sample_swx2[idx_sample]
            centroid_N[idx_centroid] += np.uint32(1)
            centroid_Sx[idx_centroid] += sample_mean[idx_sample]
            centroid_Sx2[idx_centroid] += sample_mean[idx_sample].astype(np.float32) ** 2
        new_pos_mask = ~common_mask_sample
        if np.any(new_pos_mask):
            new_pos = sample_pos[new_pos_mask]
            new_mC = sample_mC[new_pos_mask]
            new_uC = sample_uC[new_pos_mask]
            new_tnc = sample_tnc[new_pos_mask]
            new_c = new_mC + new_uC
            new_mean = sample_mean[new_pos_mask]
            new_swx2 = np.where(new_c > 0, (new_mC.astype(np.float64) ** 2) / new_c.astype(np.float64), 0.0).astype(np.float32)
            all_pos = np.concatenate([centroid_pos, new_pos])
            all_Sm = np.concatenate([centroid_Sm, new_mC])
            all_Su = np.concatenate([centroid_Su, new_uC])
            all_Sc2 = np.concatenate([centroid_Sc2, (new_c.astype(np.uint64) ** 2).astype(np.uint32)])
            all_Swx2 = np.concatenate([centroid_Swx2, new_swx2])
            all_N = np.concatenate([centroid_N, np.ones(len(new_pos), dtype=np.uint32)])
            all_Sx = np.concatenate([centroid_Sx, new_mean])
            all_Sx2 = np.concatenate([centroid_Sx2, new_mean.astype(np.float32) ** 2])
            all_tnc = np.concatenate([centroid_tnc, new_tnc])
            sort_idx = np.argsort(all_pos)
            all_pos = all_pos[sort_idx]
            all_Sm = all_Sm[sort_idx]
            all_Su = all_Su[sort_idx]
            all_Sc2 = all_Sc2[sort_idx]
            all_Swx2 = all_Swx2[sort_idx]
            all_N = all_N[sort_idx]
            all_Sx = all_Sx[sort_idx]
            all_Sx2 = all_Sx2[sort_idx]
            all_tnc = all_tnc[sort_idx]
        else:
            all_pos = centroid_pos
            all_Sm = centroid_Sm
            all_Su = centroid_Su
            all_Sc2 = centroid_Sc2
            all_Swx2 = centroid_Swx2
            all_N = centroid_N
            all_Sx = centroid_Sx
            all_Sx2 = centroid_Sx2
            all_tnc = centroid_tnc

        new_df = pd.DataFrame({
            "pos": all_pos, "tnc": all_tnc, "N": all_N,
            "Sx": all_Sx.astype(np.float32), "Sx2": all_Sx2.astype(np.float32),
            "Sm": all_Sm, "Su": all_Su, "Sc2": all_Sc2, "Swx2": all_Swx2.astype(np.float32),
        })
        new_metadata = self._metadata.copy() if self._metadata else {}
        new_metadata["n_samples"] = new_metadata.get("n_samples", 0) + 1
        out = MethylCentroid(new_df, metadata=new_metadata)
        if getattr(self, "_binned_stats", None) and "bin_edges" in self._binned_stats and "bin_counts" in self._binned_stats:
            bin_edges = np.asarray(self._binned_stats["bin_edges"], dtype=np.float64)
            centroid_bin_counts = np.asarray(self._binned_stats["bin_counts"], dtype=np.float64)
            n_bins = len(bin_edges) - 1
            sample_mean_at_all = np.zeros(len(all_pos), dtype=np.float64)
            # Position-based lookup: match all_pos to sample positions (no assumption on sample_pos order)
            common_pos_bin, idx_all, idx_samp = np.intersect1d(
                all_pos, sample_pos, assume_unique=True, return_indices=True
            )
            if len(common_pos_bin) > 0:
                sample_mean_at_all[idx_all] = np.clip(
                    sample_mean[idx_samp].astype(np.float64), 1e-9, 1.0 - 1e-9
                )
            bin_idx = np.digitize(sample_mean_at_all, bin_edges[1:-1] if n_bins > 1 else np.array([0.5]))
            bin_idx = np.clip(bin_idx, 0, n_bins - 1)
            all_bin_counts = np.zeros((len(all_pos), n_bins), dtype=centroid_bin_counts.dtype)
            # Position-based copy from centroid bin_counts (no assumption on centroid_pos order)
            common_pos_c, idx_all_c, idx_cent_c = np.intersect1d(
                all_pos, centroid_pos, assume_unique=True, return_indices=True
            )
            if len(common_pos_c) > 0:
                all_bin_counts[idx_all_c] = centroid_bin_counts[idx_cent_c]
            position_in_sample = np.isin(all_pos, sample_pos)
            row_idx = np.arange(len(all_pos))[position_in_sample]
            bc_idx = bin_idx[position_in_sample]
            np.add.at(all_bin_counts, (row_idx, bc_idx), 1)
            out.set_binned_stats(bin_edges, all_bin_counts)
        return out

    def remove_sample(
        self,
        sample: "MethylSample",
        residualize_apply=None,
        sample_path=None,
    ) -> "MethylCentroid":
        """Remove a sample from this centroid; subtracts N, Sx, Sx2, Sm, Su, Sc2, Swx2 and updates binned_stats."""
        centroid_cpu = self.to_cpu()
        sample_cpu = sample.to_cpu()
        centroid_pos = np.asarray(centroid_cpu.pos.values, dtype=np.uint32)
        centroid_Sm = np.asarray(centroid_cpu.Sm.values, dtype=np.uint32)
        centroid_Su = np.asarray(centroid_cpu.Su.values, dtype=np.uint32)
        centroid_Sc2 = np.asarray(centroid_cpu.Sc2.values, dtype=np.uint32)
        centroid_Swx2 = np.asarray(centroid_cpu.Swx2.values, dtype=np.float32)
        centroid_N = np.asarray(centroid_cpu.N.values, dtype=np.uint32)
        centroid_Sx = np.asarray(centroid_cpu.Sx.values, dtype=np.float32)
        centroid_Sx2 = np.asarray(centroid_cpu.Sx2.values, dtype=np.float32)
        centroid_tnc = np.asarray(centroid_cpu._df["tnc"].values, dtype=np.uint8)
        sample_pos = np.asarray(sample_cpu.pos.values, dtype=np.uint32)
        sample_mC = np.asarray(sample_cpu.mC.values, dtype=np.uint32)
        sample_uC = np.asarray(sample_cpu.uC.values, dtype=np.uint32)
        sample_c = sample_mC + sample_uC
        with np.errstate(divide="ignore", invalid="ignore"):
            sample_mean = np.where(sample_c > 0, sample_mC.astype(np.float32) / sample_c.astype(np.float32), 0.0)
        if residualize_apply is not None:
            path = sample_path if sample_path is not None else getattr(sample, "sample_id", None)
            adj = np.asarray(
                residualize_apply(path, sample_pos, sample_mean),
                dtype=np.float64,
            )
            if adj.shape != sample_mean.shape:
                raise ValueError("residualize_apply returned the wrong length")
            sample_mean = adj.astype(np.float32, copy=False)
        sample_swx2 = np.where(sample_c > 0, (sample_mC.astype(np.float64) ** 2) / sample_c.astype(np.float64), 0.0).astype(np.float32)
        common_pos, idx_centroid, idx_sample = np.intersect1d(
            centroid_pos, sample_pos, assume_unique=True, return_indices=True
        )
        if len(common_pos) > 0:
            centroid_Sm[idx_centroid] = (centroid_Sm[idx_centroid].astype(np.int64) - sample_mC[idx_sample].astype(np.int64)).clip(0).astype(np.uint32)
            centroid_Su[idx_centroid] = (centroid_Su[idx_centroid].astype(np.int64) - sample_uC[idx_sample].astype(np.int64)).clip(0).astype(np.uint32)
            centroid_Sc2[idx_centroid] = (centroid_Sc2[idx_centroid].astype(np.int64) - (sample_c[idx_sample].astype(np.uint64) ** 2).astype(np.int64)).clip(0).astype(np.uint32)
            centroid_Swx2[idx_centroid] -= sample_swx2[idx_sample]
            centroid_N[idx_centroid] = np.maximum(0, centroid_N[idx_centroid].astype(np.int32) - 1).astype(np.uint32)
            centroid_Sx[idx_centroid] -= sample_mean[idx_sample]
            centroid_Sx2[idx_centroid] -= sample_mean[idx_sample].astype(np.float32) ** 2
        valid_mask = centroid_N > 0
        if not np.any(valid_mask):
            raise ValueError("Cannot remove sample: centroid would have no valid positions")
        new_df = pd.DataFrame({
            "pos": centroid_pos[valid_mask], "tnc": centroid_tnc[valid_mask],
            "N": centroid_N[valid_mask], "Sx": centroid_Sx[valid_mask].astype(np.float32), "Sx2": centroid_Sx2[valid_mask].astype(np.float32),
            "Sm": centroid_Sm[valid_mask], "Su": centroid_Su[valid_mask],
            "Sc2": centroid_Sc2[valid_mask], "Swx2": centroid_Swx2[valid_mask].astype(np.float32),
        })
        new_metadata = self._metadata.copy() if self._metadata else {}
        new_metadata["n_samples"] = max(0, new_metadata.get("n_samples", 1) - 1)
        out = MethylCentroid(new_df, metadata=new_metadata)
        if getattr(self, "_binned_stats", None) and "bin_edges" in self._binned_stats and "bin_counts" in self._binned_stats:
            bin_edges = np.asarray(self._binned_stats["bin_edges"], dtype=np.float64)
            centroid_bin_counts = np.asarray(self._binned_stats["bin_counts"], dtype=np.float64)
            n_bins = len(bin_edges) - 1
            valid_pos = centroid_pos[valid_mask]
            sample_mean_at_valid = np.zeros(len(valid_pos), dtype=np.float64)
            common_r, idx_valid, idx_samp = np.intersect1d(
                valid_pos, sample_pos, assume_unique=True, return_indices=True
            )
            if len(common_r) > 0:
                sample_mean_at_valid[idx_valid] = np.clip(
                    sample_mean[idx_samp].astype(np.float64), 1e-9, 1.0 - 1e-9
                )
            bin_idx = np.digitize(sample_mean_at_valid, bin_edges[1:-1] if n_bins > 1 else np.array([0.5]))
            bin_idx = np.clip(bin_idx, 0, n_bins - 1)
            new_bin_counts = centroid_bin_counts[valid_mask].copy()
            position_in_sample = np.isin(valid_pos, sample_pos)
            row_idx = np.arange(len(valid_pos))[position_in_sample]
            np.add.at(new_bin_counts, (row_idx, bin_idx[position_in_sample]), -1)
            new_bin_counts = np.maximum(new_bin_counts, 0)
            out.set_binned_stats(bin_edges, new_bin_counts)
        return out

    @classmethod
    def from_centroid_data(
        cls,
        data: Union[Dict[str, np.ndarray], np.ndarray],
        metadata: Optional[Dict[str, Any]] = None
    ) -> "MethylCentroid":
        """
        Create MethylCentroid from data dictionary or structured array.

        Args:
            data: Dictionary with arrays or structured numpy array
            metadata: Optional metadata

        Returns:
            MethylCentroid instance
        """
        if isinstance(data, np.ndarray):
            df = pd.DataFrame({
                "pos": data["pos"], "tnc": data["tnc"], "N": data["N"],
                "Sx": data["Sx"], "Sx2": data["Sx2"],
                "Sm": data["Sm"], "Su": data["Su"], "Sc2": data["Sc2"], "Swx2": data["Swx2"],
            })
        else:
            df = pd.DataFrame(data)
        return cls(df, metadata)


def compute_coverage_outlier_flags(
    samples: List[MethylSample],
    *,
    method: Literal["robust_z", "iqr"] = "robust_z",
    threshold: float = 3.5,
    max_positions: int = 100_000,
    seed: Optional[int] = None,
) -> List[bool]:
    """
    Flag samples with outlying coverage (for optional capping).

    Computes a per-sample coverage summary (sampled median) and flags samples
    that are outliers using robust z-score or IQR. Use the result to cap only
    flagged samples: e.g. for i, s in enumerate(samples): if flags[i]: s.cap_coverage_binomial(n_cap=35, seed=0).

    Args:
        samples: List of MethylSample instances.
        method: "robust_z" (median, MAD, rz = 0.6745*(x-med)/mad, flag rz > threshold)
            or "iqr" (flag summary > Q3 + 1.5*IQR).
        threshold: For robust_z, flag when robust z-score > threshold (default 3.5).
        max_positions: Passed to each sample's median_coverage (sampled median).
        seed: Passed to each sample's median_coverage for reproducibility.

    Returns:
        List of bool, one per sample: True if that sample is flagged as outlier.
    """
    if not samples:
        return []
    summaries = np.array(
        [s.median_coverage(max_positions=max_positions, seed=seed) for s in samples],
        dtype=np.float64,
    )
    if method == "robust_z":
        med = np.median(summaries)
        mad = np.median(np.abs(summaries - med))
        if mad <= 0:
            return [False] * len(samples)
        rz = 0.6745 * (summaries - med) / mad
        return (rz > threshold).tolist()
    if method == "iqr":
        q1, q3 = np.percentile(summaries, [25, 75])
        iqr = q3 - q1
        if iqr <= 0:
            return [False] * len(samples)
        return (summaries > q3 + 1.5 * iqr).tolist()
    raise ValueError(f"method must be 'robust_z' or 'iqr', got {method!r}")
