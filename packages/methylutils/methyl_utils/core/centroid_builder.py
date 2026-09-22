# methyl_utils/core/centroid_builder.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Dict, Any, Union, List

import numpy as np
import pandas as pd

# GPU support via unified array backend (numpy | cupy | mojo)
from ..array_backend import resolve_array_backend

try:
    import cupy as cp
    from cupy import ndarray as CuArray

    HAS_GPU = cp is not None
except ImportError:
    cp = np
    CuArray = np.ndarray
    HAS_GPU = False

from .methyl_frame import MethylCentroid
from .io import load_from_h5

logger = logging.getLogger(__name__)


class MethylCentroidBuilder:
    """
    The one and only way to build extended centroids in 2025+.
    Streaming, GPU-accelerated, memory-efficient, and outputs clean MethylCentroid.
    Replaces PositionAligner + old MethylSample logic completely.
    """

    def __init__(
        self,
        min_coverage: int = 4,
        use_gpu: bool = True,
        chunk_size: int = 100_000_000,
        metadata: Optional[Dict[str, Any]] = None,
        binned_stats_bins: int = 20,
        gpu_backend: Optional[str] = None,
    ):
        if int(binned_stats_bins) < 1:
            raise ValueError(
                f"binned_stats_bins must be >= 1 for ECDF centroids, got {binned_stats_bins}"
            )
        self.min_coverage = min_coverage
        self.gpu_backend, self.xp, self.use_gpu = resolve_array_backend(
            prefer_gpu=use_gpu, gpu_backend=gpu_backend
        )
        self.metadata = metadata or {}
        self.binned_stats_bins = int(binned_stats_bins)
        self.bin_edges = np.linspace(0.0, 1.0, binned_stats_bins + 1, dtype=np.float64)
        self.residualize_apply = None

        logger.info(
            "MethylCentroidBuilder initialized → backend=%s GPU=%s binned_stats_bins=%s",
            self.gpu_backend,
            self.use_gpu,
            binned_stats_bins,
        )

        self.pos: CuArray = self.xp.zeros(chunk_size, dtype=np.uint32)
        self.mC_sum: CuArray = self.xp.zeros(chunk_size, dtype=np.uint32)
        self.uC_sum: CuArray = self.xp.zeros(chunk_size, dtype=np.uint32)
        self.Sc2: CuArray = self.xp.zeros(chunk_size, dtype=np.uint32)
        self.Swx2: CuArray = self.xp.zeros(chunk_size, dtype=np.float32)
        self.N: CuArray = self.xp.zeros(chunk_size, dtype=np.uint32)
        self.Sx: CuArray = self.xp.zeros(chunk_size, dtype=np.float32)
        self.Sx2: CuArray = self.xp.zeros(chunk_size, dtype=np.float32)
        self.tnc: CuArray = self.xp.zeros(chunk_size, dtype=np.uint8)
        self.bin_counts: CuArray = self.xp.zeros((chunk_size, binned_stats_bins), dtype=np.uint32)

        self.size = 0
        self.capacity = chunk_size
        self.samples_processed = 0

    def release_gpu(self) -> None:
        """Release GPU array references so memory can be freed."""
        if self.gpu_backend != "cupy" or not HAS_GPU:
            return
        for attr in ["pos", "mC_sum", "uC_sum", "Sc2", "Swx2", "N", "Sx", "Sx2", "tnc", "bin_counts"]:
            if hasattr(self, attr):
                setattr(self, attr, None)
        logger.debug("MethylCentroidBuilder GPU arrays released")

    def _grow(self, min_needed: int):
        new_cap = max(min_needed, int(self.capacity * 1.6))
        logger.debug(f"Growing accumulators: {self.capacity:,} → {new_cap:,} positions")
        for attr in ["pos", "mC_sum", "uC_sum", "Sc2", "Swx2", "N", "Sx", "Sx2", "tnc"]:
            old = getattr(self, attr)
            new = self.xp.zeros(new_cap, dtype=old.dtype)
            new[: self.size] = old[: self.size]
            setattr(self, attr, new)
        old_bc = self.bin_counts
        new_bc = self.xp.zeros((new_cap, self.binned_stats_bins), dtype=old_bc.dtype)
        new_bc[: self.size, :] = old_bc[: self.size, :]
        self.bin_counts = new_bc
        self.capacity = new_cap

    def add_sample(self, sample_path: Union[str, Path]):
        """Add one sample from disk — memory-safe streaming"""
        from methyl_utils.core.methyl_frame import MethylSample  # lazy import

        # Load sample - load_from_h5 returns the appropriate type directly
        loaded = load_from_h5(sample_path)
        # If it's already a MethylSample, use it directly; otherwise convert if needed
        if isinstance(loaded, MethylSample):
            sample = loaded
        else:
            # If it's a centroid, we can't use it directly - this shouldn't happen
            raise ValueError(f"Expected MethylSample, got {type(loaded)}")
        try:
            if len(sample) == 0:
                return

            pos = sample.pos.values.astype(np.uint32)
            mC = sample.mC.values.astype(np.uint32)
            uC = sample.uC.values.astype(np.uint32)
            # Access tnc from DataFrame directly
            tnc = sample._df["tnc"].values.astype(np.uint8)

            # Move to CuPy if needed (Mojo stages on host NumPy)
            if self.gpu_backend == "cupy":
                pos = self.xp.asarray(pos)
                mC = self.xp.asarray(mC)
                uC = self.xp.asarray(uC)
                tnc = self.xp.asarray(tnc)

            # Find insertion points. Cap pos to current max so searchsorted never returns self.size
            # (CuPy can OOB internally when the return value equals the array length).
            if self.gpu_backend == "mojo":
                from .. import mojo_numeric

                pos, is_new, n_new = mojo_numeric.merge_centroid_positions(
                    np.asarray(self.pos), self.size, np.asarray(pos)
                )
            else:
                max_pos = self.pos[self.size - 1]
                pos_capped = self.xp.minimum(pos, max_pos)
                idx = self.xp.searchsorted(self.pos[: self.size], pos_capped)
                is_new = (pos > max_pos) | (self.pos[idx] != pos)
                n_new = int(is_new.sum())

            if n_new > 0:
                needed = self.size + n_new
                if needed > self.capacity:
                    self._grow(needed)

                # Efficiently merge new positions while maintaining sorted order
                new_pos = pos[is_new]
                new_tnc = tnc[is_new]

                # Get existing positions
                existing_pos = self.pos[: self.size]
                existing_tnc = self.tnc[: self.size]
                existing_mC_sum = self.mC_sum[: self.size]
                existing_uC_sum = self.uC_sum[: self.size]
                existing_Sc2 = self.Sc2[: self.size]
                existing_Swx2 = self.Swx2[: self.size]
                existing_N = self.N[: self.size]
                existing_Sx = self.Sx[: self.size]
                existing_Sx2 = self.Sx2[: self.size]
                existing_bin_counts = self.bin_counts[: self.size, :]
                all_pos = self.xp.concatenate([existing_pos, new_pos])
                sort_indices = self.xp.argsort(all_pos)
                n_all = len(all_pos)
                self.pos[: n_all] = all_pos[sort_indices]
                self.tnc[: n_all] = self.xp.concatenate([existing_tnc, new_tnc])[sort_indices]
                self.mC_sum[: n_all] = self.xp.concatenate([existing_mC_sum, self.xp.zeros(n_new, dtype=self.mC_sum.dtype)])[sort_indices]
                self.uC_sum[: n_all] = self.xp.concatenate([existing_uC_sum, self.xp.zeros(n_new, dtype=self.uC_sum.dtype)])[sort_indices]
                self.Sc2[: n_all] = self.xp.concatenate([existing_Sc2, self.xp.zeros(n_new, dtype=self.Sc2.dtype)])[sort_indices]
                self.Swx2[: n_all] = self.xp.concatenate([existing_Swx2, self.xp.zeros(n_new, dtype=self.Swx2.dtype)])[sort_indices]
                self.N[: n_all] = self.xp.concatenate([existing_N, self.xp.zeros(n_new, dtype=self.N.dtype)])[sort_indices]
                self.Sx[: n_all] = self.xp.concatenate([existing_Sx, self.xp.zeros(n_new, dtype=self.Sx.dtype)])[sort_indices]
                self.Sx2[: n_all] = self.xp.concatenate([existing_Sx2, self.xp.zeros(n_new, dtype=self.Sx2.dtype)])[sort_indices]
                new_bin_rows = self.xp.zeros((n_new, self.binned_stats_bins), dtype=self.bin_counts.dtype)
                self.bin_counts[: n_all, :] = self.xp.concatenate([existing_bin_counts, new_bin_rows])[sort_indices, :]
                self.size = n_all

            # Final indices after merge. Cap pos to current max so searchsorted never returns self.size.
            max_pos = self.pos[self.size - 1]
            pos_capped = self.xp.minimum(pos, max_pos)
            final_idx = self.xp.searchsorted(self.pos[: self.size], pos_capped)
            # Rows with pos > max_pos would be OOB; mark and skip them.
            # Use explicit Python int so the check is reliable on all backends (e.g. CuPy).
            size_int = int(self.size)
            oob_any = self.xp.any(final_idx >= size_int)
            if self.gpu_backend == "cupy":
                oob_any = bool(cp.asnumpy(oob_any))
            else:
                oob_any = bool(oob_any)
            if oob_any:
                valid = final_idx < size_int
                n_skip = int(
                    cp.asnumpy((~valid).sum())
                    if self.gpu_backend == "cupy"
                    else (~valid).sum()
                )
                logger.warning(
                    "Builder: %d sample position(s) not in builder after merge (skipping)",
                    n_skip,
                )
                final_idx = final_idx[valid]
                pos = pos[valid]
                mC = mC[valid]
                uC = uC[valid]
                tnc = tnc[valid]

            # Defensive: clip so we never index with size (avoids OOB on any backend)
            final_idx = self.xp.clip(final_idx, 0, size_int - 1)

            # Update accumulators
            total_cov = mC.astype(self.xp.float64) + uC.astype(self.xp.float64)
            mean = self.xp.where(
                total_cov > 0,
                mC.astype(self.xp.float64) / total_cov,
                self.xp.float64(0.0)
            ).astype(self.xp.float32)
            if self.residualize_apply is not None:
                to_np = (
                    lambda a: cp.asnumpy(a)
                    if self.gpu_backend == "cupy"
                    else np.asarray(a)
                )
                pos_np = np.asarray(to_np(pos), dtype=np.uint32)
                mean_np = np.asarray(to_np(mean), dtype=np.float64)
                if pos_np.shape != mean_np.shape:
                    raise ValueError(
                        f"residualize pos/mean length mismatch: {pos_np.shape} vs {mean_np.shape}"
                    )
                adj = np.asarray(
                    self.residualize_apply(sample_path, pos_np, mean_np),
                    dtype=np.float64,
                )
                if adj.shape != mean_np.shape:
                    raise ValueError("residualize_apply returned the wrong length")
                mean = self.xp.asarray(adj, dtype=self.xp.float32)
            # c_i * x_i^2 = mC_i^2 / c_i
            swx2_inc = self.xp.where(
                total_cov > 0,
                (mC.astype(self.xp.float64) ** 2) / total_cov,
                self.xp.float64(0.0)
            ).astype(self.xp.float32)
            c_sq = (mC + uC).astype(self.xp.uint32)

            ones = self.xp.ones(len(mean), dtype=self.xp.uint32)
            sc2_inc = (c_sq.astype(self.xp.uint64) ** 2).astype(self.xp.uint32)
            sx2_inc = mean ** 2
            if self.gpu_backend == "mojo":
                from .. import mojo_numeric

                fi = np.asarray(final_idx)
                mojo_numeric.scatter_add_u32(self.mC_sum, fi, np.asarray(mC, dtype=np.uint32))
                mojo_numeric.scatter_add_u32(self.uC_sum, fi, np.asarray(uC, dtype=np.uint32))
                mojo_numeric.scatter_add_u32(self.Sc2, fi, np.asarray(sc2_inc, dtype=np.uint32))
                mojo_numeric.scatter_add_f32(self.Swx2, fi, np.asarray(swx2_inc, dtype=np.float32))
                mojo_numeric.scatter_add_u32(self.N, fi, np.asarray(ones, dtype=np.uint32))
                mojo_numeric.scatter_add_f32(self.Sx, fi, np.asarray(mean, dtype=np.float32))
                mojo_numeric.scatter_add_f32(self.Sx2, fi, np.asarray(sx2_inc, dtype=np.float32))
                bin_idx = mojo_numeric.digitize_bins(
                    np.asarray(mean, dtype=np.float32), self.bin_edges
                )
                mojo_numeric.bin_histogram_add(self.bin_counts, fi, bin_idx)
            else:
                self.mC_sum[final_idx] += mC
                self.uC_sum[final_idx] += uC
                self.Sc2[final_idx] += sc2_inc
                self.Swx2[final_idx] += swx2_inc
                self.N[final_idx] += ones
                self.Sx[final_idx] += mean
                self.Sx2[final_idx] += sx2_inc
                bin_edges_xp = self.xp.asarray(self.bin_edges)
                if self.binned_stats_bins > 1:
                    bin_idx = self.xp.digitize(
                        mean.astype(self.xp.float64), bin_edges_xp[1:-1]
                    )
                    bin_idx = self.xp.clip(
                        bin_idx, 0, self.binned_stats_bins - 1
                    ).astype(self.xp.intp)
                else:
                    bin_idx = self.xp.zeros(len(mean), dtype=self.xp.intp)
                self.xp.add.at(self.bin_counts, (final_idx, bin_idx), 1)
            self.samples_processed += 1
            if self.samples_processed % 50 == 0:
                logger.info(
                    f"Processed {self.samples_processed} samples → {self.size:,} unique positions"
                )
        finally:
            try:
                sample.close()
            except Exception as e:
                logger.debug(f"Sample cleanup failed: {e}")

    def finalize(self, log_finalize: bool = True) -> MethylCentroid:
        """Return MethylCentroid with pos, tnc, N, Sx, Sx2, Sm, Su, Sc2, Swx2 and binned_stats."""
        if self.size == 0:
            raise ValueError("No data accumulated")
        to_cpu = cp.asnumpy if self.gpu_backend == "cupy" else np.asarray
        pos = to_cpu(self.pos[: self.size])
        mC_sum = to_cpu(self.mC_sum[: self.size])
        uC_sum = to_cpu(self.uC_sum[: self.size])
        Sc2 = to_cpu(self.Sc2[: self.size])
        Swx2 = to_cpu(self.Swx2[: self.size])
        N = to_cpu(self.N[: self.size])
        Sx = to_cpu(self.Sx[: self.size])
        Sx2 = to_cpu(self.Sx2[: self.size])
        tnc = to_cpu(self.tnc[: self.size])
        bin_counts = to_cpu(self.bin_counts[: self.size, :])
        coverage = mC_sum.astype(np.uint64) + uC_sum.astype(np.uint64)
        mask = coverage >= self.min_coverage
        df = pd.DataFrame({
            "pos": pos[mask].astype(np.uint32),
            "tnc": tnc[mask],
            "N": N[mask].astype(np.uint32),
            "Sx": Sx[mask].astype(np.float32),
            "Sx2": Sx2[mask].astype(np.float32),
            "Sm": mC_sum[mask].astype(np.uint32),
            "Su": uC_sum[mask].astype(np.uint32),
            "Sc2": Sc2[mask].astype(np.uint32),
            "Swx2": Swx2[mask].astype(np.float32),
        }).reset_index(drop=True)
        final_metadata = {
            **self.metadata,
            "builder": "MethylCentroidBuilder",
            "n_samples": self.samples_processed,
            "unique_positions_raw": int(self.size),
            "positions_after_filter": len(df),
            "min_coverage": self.min_coverage,
            "gpu_acceleration": self.use_gpu,
            "gpu_backend": self.gpu_backend,
            "binned_stats_bins": self.binned_stats_bins,
        }
        centroid = MethylCentroid(df, final_metadata)
        centroid.set_binned_stats(self.bin_edges.copy(), bin_counts[mask, :].astype(np.float64))
        if log_finalize:
            logger.info(f"Centroid finalized → {len(df):,} positions from {self.samples_processed} samples")
        self.release_gpu()
        return centroid


# Convenience factory
def build_centroid(
    sample_paths: List[Union[str, Path]],
    min_coverage: int = 4,
    use_gpu: bool = True,
    metadata: Optional[Dict[str, Any]] = None,
    binned_stats_bins: int = 20,
    gpu_backend: Optional[str] = None,
) -> MethylCentroid:
    builder = MethylCentroidBuilder(
        min_coverage=min_coverage,
        use_gpu=use_gpu,
        metadata=metadata,
        binned_stats_bins=binned_stats_bins,
        gpu_backend=gpu_backend,
    )
    for path in sample_paths:
        builder.add_sample(path)
    return builder.finalize()
