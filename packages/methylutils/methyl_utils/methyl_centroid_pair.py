"""
MethylCentroidPair: ECDF-first comparison of two methylation centroids.

This module owns the low-level per-position comparison used by MethylDetector:
coverage filtering, statistical gating, approximate overlap from bin counts, and
the initial effect-size signal that is later rescored with continuous ECDF overlap.
"""

from typing import Tuple, Dict, Any, List, Union, Optional, Callable
from pathlib import Path

import numpy as np
import pandas as pd

from .core.methyl_frame import MethylSample, MethylCentroid
from .array_backend import get_array_module, to_cpu, prefer_gpu_default
from .memory_manager import get_memory_manager, force_gpu_cleanup
from .metric_validations import validate_methylation_data
from .performance_profiler import (
    get_performance_profiler,
    start_performance_monitoring,
    stop_performance_monitoring,
)
from .statistical_tests import (
    storey_qvalues,
    mann_whitney_from_bin_counts,
    dl_heterogeneity,
    effect_size_from_components,
)
from methyl_utils.logging_utils import setup_module_logging
logger = setup_module_logging(__name__)

# Type aliases for better type hints
ArrayLike = np.ndarray
DataFrameType = Union[pd.DataFrame, Any]  # Any for cuDF when available


# Constants
BD_CAP = 20.0  # Cap Bhattacharyya Distance to prevent overflow when converting to BC (exp(-20) ≈ 0)

# ECDF is the only supported centroid-comparison distribution.
DIST_ECDF = 5

# Simplified dtype for centroid comparison results
# effect_size is the single biological importance measure (computed here; MethylDetector uses as-is)
CENTROID_COMPARISON_DTYPE = np.dtype([
    ('position', np.uint32),
    ('p_value', np.float32),
    ('q_value', np.float32),
    ('mean1', np.float32),
    ('mean2', np.float32),
    ('delta_mean', np.float32),           # |mean1 - mean2| — unsigned magnitude
    ('delta_sign', np.int8),              # sign(mean1 - mean2): +1 = hypermethylated in group1, -1 = hypo
    ('bhattacharyya', np.float32),        # Bhattacharyya Distance (BC = exp(-bhattacharyya))
    ('dist', np.uint8),                   # Distribution selection (see DIST_* constants)
    ('n1', np.uint32),
    ('n2', np.uint32),
    ('variance1', np.float32),
    ('variance2', np.float32),
    ('tau2_1', np.float32),               # Between-sample heterogeneity estimate, group 1
    ('tau2_2', np.float32),               # Between-sample heterogeneity estimate, group 2
    ('effect_size', np.float32),          # Initial biological importance (overwritten by continuous ECDF stage)
    ('overlap_approx', np.float32),       # Discrete overlap from bin counts (NaN when binned_stats not available)
])


class MethylCentroidPair:
    """
    ECDF-first mathematical comparison engine for two methylation centroids.

    The runtime path is intentionally narrow:
    - centroids must provide matching `binned_stats`
    - overlap approximation comes from discrete bin-count overlap
    - the statistical gate uses a non-parametric comparison from bin counts;
      callers (e.g. MethylDetector) may replace p-values with KS on the precise ECDF
    - the returned `effect_size` is an approximate, pre-ECDF score

    MethylDetector later recomputes the final overlap/effect_size on the reduced
    DMP set using continuous ECDF views.
    """

    def __init__(
        self,
        centroid1: Optional[MethylCentroid] = None,
        centroid2: Optional[MethylCentroid] = None,
        min_coverage: int = 4,
        min_samples: Optional[Tuple[int, int]] = None,
        ecdf_ks_grid_size: int = 256,
    ):
        self.centroid1 = centroid1
        self.centroid2 = centroid2
        self.common_pos = None
        self.min_samples = min_samples  # (min_samples_1, min_samples_2) per-centroid N threshold; None = use min_coverage for N only

        if centroid1 is not None and centroid2 is not None:
            self.common_pos = np.intersect1d(centroid1.pos, centroid2.pos)
            if len(self.common_pos) == 0:
                raise ValueError("Centroids have no common positions")

        # Initialize array backend (auto-prefer GPU unless METHYL_DISABLE_GPU)
        self.xp, self.gpu_available = get_array_module()
        self.to_cpu = to_cpu
        if self.gpu_available:
            from .gpu_detection import get_gpu_memory_gb, get_gpu_device_count

            self.gpu_memory_gb = get_gpu_memory_gb()
            self.gpu_device_count = get_gpu_device_count()
            logger.info(
                "GPU backend initialized: %s device(s), %.1fGB memory",
                self.gpu_device_count,
                self.gpu_memory_gb,
            )
        else:
            self.gpu_memory_gb = 0.0
            self.gpu_device_count = 0
            logger.debug("CPU array backend initialized")

        # Initialize utilities
        self.memory_manager = get_memory_manager()
        self.performance_profiler = get_performance_profiler()

        self.min_coverage = int(min_coverage)
        self.ecdf_ks_grid_size = int(ecdf_ks_grid_size)

    @classmethod
    def load_and_align(cls, path1: Union[str, Path], path2: Union[str, Path], min_coverage: int = 4) -> Tuple[MethylSample, MethylSample, np.ndarray]:
        """
        Load two centroids from paths, align on common positions, and return aligned MethylSamples.

        Handles validation, zero-coverage clamping, and efficient indexing using native numpy.

        Args:
            path1, path2: Paths to HDF5 centroid files.
            min_coverage: Minimum coverage threshold for validation (default: 4)

        Returns:
            (centroid1: MethylSample, centroid2: MethylSample, common_pos: np.ndarray)
        """
        # Load via MethylSample (native HDF5 parsing)
        from methyl_utils.core.io import load_from_h5
        centroid1 = load_from_h5(path1)
        centroid2 = load_from_h5(path2)

        # Validate centroids (assume is_centroid checks N, Sx, etc.)
        if not centroid1.is_centroid or not centroid2.is_centroid:
            raise ValueError("Both inputs must be extended centroids with N, Sx, Sx2, log sums.")

        # Assume positions are sorted (typical for genomic data); if not, sort them
        pos1_vals = centroid1.pos.values if hasattr(centroid1.pos, 'values') else np.asarray(centroid1.pos)
        pos2_vals = centroid2.pos.values if hasattr(centroid2.pos, 'values') else np.asarray(centroid2.pos)
        if not np.all(np.diff(pos1_vals) > 0):
            logger.warning("Centroid1 positions not sorted; sorting for alignment.")
            sort_idx1 = np.argsort(pos1_vals)
            centroid1 = centroid1.apply_mask(sort_idx1)
            pos1_vals = centroid1.pos.values if hasattr(centroid1.pos, 'values') else np.asarray(centroid1.pos)
        if not np.all(np.diff(pos2_vals) > 0):
            logger.warning("Centroid2 positions not sorted; sorting for alignment.")
            sort_idx2 = np.argsort(pos2_vals)
            centroid2 = centroid2.apply_mask(sort_idx2)
            pos2_vals = centroid2.pos.values if hasattr(centroid2.pos, 'values') else np.asarray(centroid2.pos)

        # Find common positions efficiently (numpy intersection)
        common_pos = np.intersect1d(pos1_vals, pos2_vals, assume_unique=True)

        if len(common_pos) == 0:
            raise ValueError("No common positions between centroids.")

        # Fast index lookup with np.searchsorted (O(log n) per query, O(n log n) total)
        idx1 = np.searchsorted(centroid1.pos, common_pos, side='left')
        idx2 = np.searchsorted(centroid2.pos, common_pos, side='left')

        # Verify exact matches (positions must be unique and sorted)
        if not np.all(centroid1.pos[idx1] == common_pos) or not np.all(centroid2.pos[idx2] == common_pos):
            raise ValueError("Position mismatch during alignment; duplicates or unsorted positions?")

        # Slice using apply_mask (native method in MethylSample)
        aligned1 = centroid1.apply_mask(idx1)
        aligned2 = centroid2.apply_mask(idx2)

        # Clamp zero-coverage (avoids div-by-zero in comparisons)
        for cent in [aligned1, aligned2]:
            n_clamped = cent.clamp_zero_coverage()
            if n_clamped > 0:
                logger.debug(f"Clamped {n_clamped} zero-coverage positions in centroid")

        # Validate min_coverage post-alignment
        max_n = max(
            aligned1.N.max() if aligned1.N is not None else 0,
            aligned2.N.max() if aligned2.N is not None else 0
        )
        if max_n < min_coverage:
            logger.warning(f"Max coverage {max_n} < min_coverage {min_coverage}; proceeding with warning.")

        return aligned1, aligned2, common_pos

    @classmethod
    def load_and_align_from_samples(
        cls, centroid1, centroid2: MethylSample,
        min_coverage: int = 4) -> Tuple[MethylSample, MethylSample, np.ndarray]:
        """
        Align two already-loaded centroids on common positions.

        Args:
            centroid1, centroid2: Already loaded MethylSample objects
            min_coverage: Minimum coverage threshold for validation (default: 4)

        Returns:
            (centroid1: MethylSample, centroid2: MethylSample, common_pos: np.ndarray)
        """
        # Validate extended centroids
        if not centroid1.is_centroid or not centroid2.is_centroid:
            raise ValueError("Both inputs must be extended centroids with N, Sx, Sx2, log sums.")

        # Assume positions are sorted (typical for genomic data); if not, sort them
        pos1_vals = centroid1.pos.values if hasattr(centroid1.pos, 'values') else np.asarray(centroid1.pos)
        pos2_vals = centroid2.pos.values if hasattr(centroid2.pos, 'values') else np.asarray(centroid2.pos)
        if not np.all(np.diff(pos1_vals) > 0):
            logger.warning("Centroid1 positions not sorted; sorting for alignment.")
            sort_idx1 = np.argsort(pos1_vals)
            centroid1 = centroid1.apply_mask(sort_idx1)
            pos1_vals = centroid1.pos.values if hasattr(centroid1.pos, 'values') else np.asarray(centroid1.pos)
        if not np.all(np.diff(pos2_vals) > 0):
            logger.warning("Centroid2 positions not sorted; sorting for alignment.")
            sort_idx2 = np.argsort(pos2_vals)
            centroid2 = centroid2.apply_mask(sort_idx2)
            pos2_vals = centroid2.pos.values if hasattr(centroid2.pos, 'values') else np.asarray(centroid2.pos)

        # Find common positions efficiently (numpy intersection)
        common_pos = np.intersect1d(pos1_vals, pos2_vals, assume_unique=True)

        if len(common_pos) == 0:
            raise ValueError("No common positions between centroids.")

        # Fast index lookup with np.searchsorted (O(log n) per query, O(n log n) total)
        idx1 = np.searchsorted(centroid1.pos, common_pos, side='left')
        idx2 = np.searchsorted(centroid2.pos, common_pos, side='left')

        # Verify exact matches (positions must be unique and sorted)
        if not np.all(centroid1.pos[idx1] == common_pos) or not np.all(centroid2.pos[idx2] == common_pos):
            raise ValueError("Position mismatch during alignment; duplicates or unsorted positions?")

        # Slice using apply_mask (native method in MethylSample)
        aligned1 = centroid1.apply_mask(idx1)
        aligned2 = centroid2.apply_mask(idx2)

        # Clamp zero-coverage (avoids div-by-zero in comparisons)
        for cent in [aligned1, aligned2]:
            n_clamped = cent.clamp_zero_coverage()
            if n_clamped > 0:
                logger.debug(f"Clamped {n_clamped} zero-coverage positions in centroid")

        # Validate min_coverage post-alignment
        max_n = max(
            aligned1.N.max() if aligned1.N is not None else 0,
            aligned2.N.max() if aligned2.N is not None else 0
        )
        if max_n < min_coverage:
            logger.warning(f"Max coverage {max_n} < min_coverage {min_coverage}; proceeding with warning.")

        return aligned1, aligned2, common_pos

    @classmethod
    def create_reference_sample(
        cls,
        positions: np.ndarray,
        context: str = "CG",
        min_coverage: int = 4
    ) -> MethylCentroid:
        """
        Create an empty extended centroid for use as a reference/alignment template.

        Args:
            positions: Genomic positions to include
            context: Methylation context (CG, CHG, CHH)
            min_coverage: Minimum coverage threshold

        Returns:
            MethylCentroid with zero-filled data
        """
        from methyl_utils.core.methyl_frame import MethylCentroid
        import pandas as pd

        n_positions = len(positions)
        # Create dummy tnc values based on context
        from methyl_utils import CONTEXT_CG, CONTEXT_CHG, CONTEXT_CHH, CONTEXT_SHIFT
        context_map = {
            "CG": CONTEXT_CG << CONTEXT_SHIFT,
            "CHG": CONTEXT_CHG << CONTEXT_SHIFT,
            "CHH": CONTEXT_CHH << CONTEXT_SHIFT
        }
        tnc_base = context_map.get(context, 0)
        dummy_tnc = np.full(n_positions, tnc_base, dtype=np.uint8)

        df = pd.DataFrame({
            "pos": positions.astype(np.uint32),
            "mC": np.zeros(n_positions, dtype=np.uint32),
            "uC": np.ones(n_positions, dtype=np.uint32) * min_coverage,
            "tnc": dummy_tnc,
            "N": np.ones(n_positions, dtype=np.uint32),
            "Sx": np.zeros(n_positions, dtype=np.float32),
            "Sx2": np.zeros(n_positions, dtype=np.float32),
        })
        return MethylCentroid(df, metadata={"context": context})

    @classmethod
    def align_samples(
        cls,
        sample1: MethylSample,
        sample2: MethylSample
    ) -> tuple[MethylSample, MethylSample, np.ndarray]:
        """
        Align two samples on common positions.

        Args:
            sample1: First sample
            sample2: Second sample

        Returns:
            Tuple of (aligned_sample1, aligned_sample2, common_positions)
        """
        return cls.load_and_align_from_samples(sample1, sample2)

    @staticmethod
    def ensure_cpu(sample: MethylSample) -> MethylSample:
        """
        Ensure a sample is on CPU (convert from GPU if needed).

        Args:
            sample: Sample that may be on GPU

        Returns:
            CPU version of the sample
        """
        return sample.to_cpu()

    @classmethod
    def extract_methylation_fractions(
        cls,
        sample_paths: List[Union[str, Path]],
        reference_positions: Dict[str, np.ndarray],
        chromosome: str,
        min_coverage: int = 4
    ) -> Tuple[np.ndarray, np.ndarray, Dict[str, np.ndarray]]:
        """
        Extract methylation fractions from samples aligned to reference positions.
        
        This method efficiently loads samples, aligns them to reference positions by context,
        and extracts methylation fractions without creating full centroid objects.
        
        Args:
            sample_paths: List of paths to sample HDF5 files or directories
            reference_positions: Dictionary mapping context (CG, CHG, CHH) to position arrays
            chromosome: Chromosome identifier
            min_coverage: Minimum coverage threshold
            
        Returns:
            Tuple of (methylation_fractions_matrix, positions_array, contexts_array, context_indices_dict)
            - methylation_fractions_matrix: (n_samples, n_positions) array with NaN for missing positions
            - positions_array: (n_positions,) array of positions in column order
            - contexts_array: (n_positions,) array of context (e.g. 'CG') per column; column j is (positions_array[j], contexts_array[j])
            - context_indices_dict: Dictionary mapping context to index arrays in the full position array
        """
        from pathlib import Path
        import numpy as np
        
        # Build ordered position array and (position, context) -> column index mapping.
        # Use reference_positions key order so column order matches caller's DMP/centroid list
        # (e.g. detector uses np.unique(dmp_contexts) → same order as config.contexts).
        all_positions = []
        all_contexts = []
        position_to_index = {}  # (pos, ctx) -> column index
        context_indices_dict = {}
        
        for ctx in reference_positions.keys():
                ctx_positions = reference_positions[ctx].astype(np.uint32)
                ctx_indices = []
                for pos in ctx_positions:
                    key = (int(pos), ctx)
                    if key not in position_to_index:
                        position_to_index[key] = len(all_positions)
                        all_positions.append(pos)
                        all_contexts.append(ctx)
                    ctx_indices.append(position_to_index[key])
                # Column indices into the feature matrix (≪ 2³²); uint32 matches genomic pos dtype policy.
                context_indices_dict[ctx] = np.array(ctx_indices, dtype=np.uint32)
        
        all_positions = np.array(all_positions, dtype=np.uint32)
        all_contexts = np.array(all_contexts, dtype=object)
        n_positions = len(all_positions)
        n_samples = len(sample_paths)
        chrom_str = chromosome[0] if isinstance(chromosome, (list, tuple)) else str(chromosome)

        # Initialize result matrix with NaN
        X = np.full((n_samples, n_positions), np.nan, dtype=np.float32)
        n_missing_file = 0
        n_empty_align = 0
        n_error = 0
        per_sample_issues: Dict[str, Dict[str, int]] = {}
        first_missing_path = None
        first_ctx = next(iter(reference_positions.keys()), "CG")

        # Process each sample
        for i, sample_path in enumerate(sample_paths):
            sample_path = Path(sample_path)

            # Process each context
            for ctx in reference_positions.keys():
                ctx_positions = reference_positions[ctx].astype(np.uint32)

                # Determine H5 file path
                if sample_path.suffix == '.h5':
                    h5_file = sample_path
                elif sample_path.is_file():
                    h5_file = sample_path
                else:
                    h5_file = sample_path / f"{chrom_str}-{ctx}.h5"

                if not h5_file.exists():
                    if first_missing_path is None:
                        first_missing_path = h5_file
                    n_missing_file += 1
                    sid = sample_path.name or str(sample_path)
                    stats = per_sample_issues.setdefault(
                        sid,
                        {"missing_file": 0, "empty_align": 0, "load_error": 0},
                    )
                    stats["missing_file"] += 1
                    continue
                
                try:
                    sample = MethylSample.load_from_h5(
                        h5_file,
                        positions=ctx_positions,
                        align_positions=False,
                    )
                    
                    if len(sample) == 0:
                        n_empty_align += 1
                        sid = sample_path.name or str(sample_path)
                        stats = per_sample_issues.setdefault(
                            sid,
                            {"missing_file": 0, "empty_align": 0, "load_error": 0},
                        )
                        stats["empty_align"] += 1
                        continue

                    ctx_values, _ctx_availability = sample.lookup_at_positions(
                        ctx_positions,
                        min_coverage=min_coverage,
                        missing_value=np.nan,
                    )
                    ctx_indices = context_indices_dict.get(ctx)
                    if ctx_indices is not None and int(ctx_indices.size) == int(ctx_values.size):
                        X[i, np.asarray(ctx_indices, dtype=np.int32)] = np.asarray(ctx_values, dtype=np.float32)
                            
                except Exception as e:
                    logger.debug(f"Failed to process {h5_file}: {e}")
                    n_error += 1
                    sid = sample_path.name or str(sample_path)
                    stats = per_sample_issues.setdefault(
                        sid,
                        {"missing_file": 0, "empty_align": 0, "load_error": 0},
                    )
                    stats["load_error"] += 1
                    continue

        n_with_data = int(np.sum(~np.isnan(X).all(axis=1)))
        if per_sample_issues:
            n_samples_with_issues = len(per_sample_issues)
            logger.warning(
                "Validation extraction (%s): encountered sample-level issues across %s sample(s): "
                "missing_file=%s empty_align=%s load_error=%s",
                chrom_str,
                n_samples_with_issues,
                n_missing_file,
                n_empty_align,
                n_error,
            )
            for sid in sorted(per_sample_issues.keys()):
                s = per_sample_issues[sid]
                logger.warning(
                    "  sample=%s missing_file=%s empty_align=%s load_error=%s",
                    sid,
                    int(s.get("missing_file", 0)),
                    int(s.get("empty_align", 0)),
                    int(s.get("load_error", 0)),
                )
        if n_with_data == 0 and sample_paths:
            logger.warning(
                "Validation extraction: 0 samples had data. Each path must be a directory containing "
                "%s-%s.h5 (or a path to that .h5 file). Example expected path: %s",
                chrom_str, first_ctx, first_missing_path
            )
            if n_missing_file > 0:
                logger.warning(
                    "  %s path(s) had no such file. Check that validation sample directories contain "
                    "%s-%s.h5 for this chromosome/context.",
                    n_missing_file, chrom_str, first_ctx
                )
            if n_empty_align > 0:
                logger.warning("  %s H5 file(s) existed but had no overlapping positions with the DMP list.", n_empty_align)
            if n_error > 0:
                logger.warning("  %s H5 file(s) raised an error on load/align (see debug log).", n_error)

        return X, all_positions, all_contexts, context_indices_dict

    def _init_cpu_backend(self):
        """Initialize CPU backend."""
        self.cp = None
        self.xp = np
        self.to_cpu = lambda a: a
        logger.info("CPU backend initialized")

    @staticmethod
    def _normalize_metric_mode(value: Optional[str], default: str, allowed: set[str]) -> str:
        if value is None:
            return default
        mode = str(value).strip().lower()
        if mode not in allowed:
            logger.warning(
                f"Unrecognized metric mode '{value}', falling back to '{default}'"
            )
            return default
        return mode

    def compare_centroids(
        self,
        centroid1: MethylSample,
        centroid2: MethylSample,
        position_subset: Optional[np.ndarray] = None,
    ) -> pd.DataFrame:
        """
        Compare two centroids and return statistical results as DataFrame.

        Args:
            centroid1: First methylation centroid (extended centroid required).
            centroid2: Second methylation centroid (extended centroid required).
            position_subset: Optional array of genomic positions to restrict the
                comparison to.  Must be a sorted subset of the positions common to
                both centroids.  When provided the statistical test, FDR correction,
                and all downstream stages run only on these positions.  Pass this to
                avoid computing the expensive statistical gate on positions that are certain
                to fail the downstream biological filter (e.g. all positions with
                |delta_mean| < threshold).

        Returns:
            pandas DataFrame with columns: position, p_value, q_value,
            alpha1, beta1, alpha2, beta2, mean1, mean2, delta_mean,
            overlap_approx, tau2_1, tau2_2, and effect_size.
        """
        start_performance_monitoring()

        try:
            # Validate inputs
            self._validate_centroids(centroid1, centroid2)

            # Align centroids (find common positions)
            common_positions = self._align_centroids(centroid1, centroid2)

            # Optional early reduction: restrict to caller-provided position subset
            if position_subset is not None and len(position_subset) > 0:
                common_positions = np.intersect1d(common_positions, position_subset)
                logger.info(
                    f"Position subset applied: {len(common_positions):,} positions "
                    f"(from {len(position_subset):,} requested)"
                )

            if len(common_positions) == 0:
                logger.warning("No common positions found between centroids")
                return pd.DataFrame()  # Empty DataFrame

            logger.info(
                "Comparing centroids at %s common positions",
                f"{len(common_positions):,}",
            )

            # Perform statistical analysis and overlap approximation.
            results_array = self._compute_statistics(centroid1, centroid2, common_positions)

            # Apply FDR correction
            results_array = self._apply_fdr_correction(results_array)

            logger.info(f"Comparison complete: {len(results_array)} positions analyzed")

            # Always return as DataFrame
            return pd.DataFrame(results_array)

        finally:
            stop_performance_monitoring()

    def _validate_centroids(self, centroid1: MethylSample, centroid2: MethylSample) -> None:
        """Validate that centroids are suitable for comparison."""
        if not (centroid1.is_centroid and centroid2.is_centroid):
            raise ValueError(
                "MethylCentroidPair requires extended centroids. "
                f"Centroid1 is_centroid: {centroid1.is_centroid}, "
                f"Centroid2 is_centroid: {centroid2.is_centroid}"
            )

        # Use MethylUtils validation on the methylation proportions
        # MethylSample.mean contains the proper methylation proportions (0-1)
        validate_methylation_data(centroid1.mean, centroid1.N)
        validate_methylation_data(centroid2.mean, centroid2.N)

        binned1 = centroid1.binned_stats
        binned2 = centroid2.binned_stats
        if not binned1 or "bin_edges" not in binned1 or "bin_counts" not in binned1:
            raise ValueError(
                "MethylCentroidPair requires centroid1 with binned_stats (bin_edges, bin_counts)"
            )
        if not binned2 or "bin_edges" not in binned2 or "bin_counts" not in binned2:
            raise ValueError(
                "MethylCentroidPair requires centroid2 with binned_stats (bin_edges, bin_counts)"
            )
        edges1 = np.asarray(binned1["bin_edges"], dtype=np.float64)
        edges2 = np.asarray(binned2["bin_edges"], dtype=np.float64)
        if edges1.shape != edges2.shape or not np.allclose(edges1, edges2):
            raise ValueError(
                "MethylCentroidPair requires matching ECDF bin_edges in both centroids"
            )

    def _align_centroids(self, centroid1: MethylSample, centroid2: MethylSample) -> np.ndarray:
        """Find common positions valid in both centroids (per-centroid coverage and N filters, then intersection)."""
        pos1_vals = centroid1.pos.values if hasattr(centroid1.pos, 'values') else np.asarray(centroid1.pos)
        pos2_vals = centroid2.pos.values if hasattr(centroid2.pos, 'values') else np.asarray(centroid2.pos)
        common_positions = np.intersect1d(pos1_vals, pos2_vals)
        if len(common_positions) == 0:
            return common_positions

        idx1 = np.searchsorted(pos1_vals, common_positions, side='left')
        idx2 = np.searchsorted(pos2_vals, common_positions, side='left')

        if self.min_samples is not None:
            # Per-centroid valid sets: coverage >= min_coverage and N >= min_samples_i; candidate = intersection
            min_s1, min_s2 = self.min_samples
            # Coverage (Sm+Su) at common positions
            cov1_arr = np.asarray(centroid1.coverage) if hasattr(centroid1, "coverage") else np.full(len(centroid1.pos), self.min_coverage, dtype=np.int64)
            cov2_arr = np.asarray(centroid2.coverage) if hasattr(centroid2, "coverage") else np.full(len(centroid2.pos), self.min_coverage, dtype=np.int64)
            cov1 = cov1_arr[idx1].astype(np.int64)
            cov2 = cov2_arr[idx2].astype(np.int64)
            N1_vals = centroid1.N.values if hasattr(centroid1.N, 'values') else np.asarray(centroid1.N)
            N2_vals = centroid2.N.values if hasattr(centroid2.N, 'values') else np.asarray(centroid2.N)
            N1_common = N1_vals[idx1].astype(np.int64)
            N2_common = N2_vals[idx2].astype(np.int64)
            valid_1 = (cov1 >= self.min_coverage) & (N1_common >= min_s1)
            valid_2 = (cov2 >= self.min_coverage) & (N2_common >= min_s2)
            mask = valid_1 & valid_2
            return common_positions[mask]
        # min_samples is None: backward compat — use min_coverage as N threshold only
        if hasattr(centroid1, 'N') and centroid1.N is not None and hasattr(centroid2, 'N') and centroid2.N is not None:
            N1_vals = centroid1.N.values if hasattr(centroid1.N, 'values') else np.asarray(centroid1.N)
            N2_vals = centroid2.N.values if hasattr(centroid2.N, 'values') else np.asarray(centroid2.N)
            N1_common = N1_vals[idx1]
            N2_common = N2_vals[idx2]
            min_N_both = np.minimum(N1_common.astype(np.int64), N2_common.astype(np.int64))
            coverage_mask = min_N_both >= self.min_coverage
            return common_positions[coverage_mask]
        if hasattr(centroid1, "coverage") and hasattr(centroid2, "coverage"):
            cov1 = centroid1.coverage.values if hasattr(centroid1.coverage, "values") else np.asarray(centroid1.coverage)
            cov2 = centroid2.coverage.values if hasattr(centroid2.coverage, "values") else np.asarray(centroid2.coverage)
            c1_coverage = cov1[idx1]
            c2_coverage = cov2[idx2]
            coverage_mask = (c1_coverage >= self.min_coverage) & (c2_coverage >= self.min_coverage)
            return common_positions[coverage_mask]
        return common_positions

    def _compute_statistics(
        self, centroid1: MethylSample, 
        centroid2: MethylSample,
        positions: np.ndarray
    ) -> np.ndarray:
        """Compute statistical tests and parameter estimates for given positions."""

        # Calculate optimal batch size
        batch_size = self.memory_manager.calculate_optimal_chunk_size(
            total_positions=len(positions),
            data_structure="centroid",
            maximize_gpu_usage=self.gpu_available
        )

        if batch_size > len(positions):
            batch_size = len(positions)

        logger.debug(f"Using batch size: {batch_size} positions")

        # Pre-allocate result array
        results_array = np.empty(len(positions), dtype=CENTROID_COMPARISON_DTYPE)

        for i in range(0, len(positions), batch_size):
            batch_positions = positions[i:i + batch_size]
            batch_end = i + len(batch_positions)

            # Extract batch data from centroids and fill results
            self._process_batch(centroid1, centroid2, batch_positions,
                              results_array[i:batch_end])

        return results_array

    def _process_batch(self, centroid1: MethylSample, centroid2: MethylSample,
                      positions: np.ndarray, results_view: np.ndarray) -> None:
        """Process a batch of positions using the ECDF-only centroid comparison path."""

        pos1 = np.asarray(centroid1.pos.values, dtype=np.uint32)
        pos2 = np.asarray(centroid2.pos.values, dtype=np.uint32)
        indices1 = np.searchsorted(pos1, positions)
        indices2 = np.searchsorted(pos2, positions)

        N1_all = np.asarray(centroid1.N, dtype=np.float64)
        N2_all = np.asarray(centroid2.N, dtype=np.float64)
        Sx1_all = np.asarray(centroid1.Sx, dtype=np.float64)
        Sx2_all = np.asarray(centroid2.Sx, dtype=np.float64)
        Sx2_1_all = np.asarray(centroid1.Sx2, dtype=np.float64)
        Sx2_2_all = np.asarray(centroid2.Sx2, dtype=np.float64)
        Sm1_all = np.asarray(centroid1.Sm, dtype=np.float64)
        Su1_all = np.asarray(centroid1.Su, dtype=np.float64)
        Swx2_1_all = np.asarray(centroid1.Swx2, dtype=np.float64)
        Sc2_1_all = np.asarray(centroid1.Sc2, dtype=np.float64)
        Sm2_all = np.asarray(centroid2.Sm, dtype=np.float64)
        Su2_all = np.asarray(centroid2.Su, dtype=np.float64)
        Swx2_2_all = np.asarray(centroid2.Swx2, dtype=np.float64)
        Sc2_2_all = np.asarray(centroid2.Sc2, dtype=np.float64)
        mean1_all = np.asarray(centroid1.mean, dtype=np.float64)
        mean2_all = np.asarray(centroid2.mean, dtype=np.float64)

        N1 = N1_all[indices1]
        N2 = N2_all[indices2]
        Sx1 = Sx1_all[indices1]
        Sx2_vals = Sx2_all[indices2]
        Sx2_1 = Sx2_1_all[indices1]
        Sx2_2 = Sx2_2_all[indices2]
        Sm1 = Sm1_all[indices1]
        Su1 = Su1_all[indices1]
        Swx2_1 = Swx2_1_all[indices1]
        Sc2_1 = Sc2_1_all[indices1]
        Sm2 = Sm2_all[indices2]
        Su2 = Su2_all[indices2]
        Swx2_2 = Swx2_2_all[indices2]
        Sc2_2 = Sc2_2_all[indices2]
        mean1 = mean1_all[indices1]
        mean2 = mean2_all[indices2]

        variance1 = np.maximum(
            (Sx2_1 - (Sx1 ** 2) / np.maximum(N1, 1.0)) / np.maximum(N1 - 1.0, 1.0),
            1e-12,
        )
        variance2 = np.maximum(
            (Sx2_2 - (Sx2_vals ** 2) / np.maximum(N2, 1.0)) / np.maximum(N2 - 1.0, 1.0),
            1e-12,
        )

        bs1 = centroid1.binned_stats
        bs2 = centroid2.binned_stats
        bc1_batch = np.asarray(bs1["bin_counts"], dtype=np.float64)[indices1]
        bc2_batch = np.asarray(bs2["bin_counts"], dtype=np.float64)[indices2]
        bin_edges = np.asarray(bs1["bin_edges"], dtype=np.float64)
        
        from .statistical_tests import ecdf_bhattacharyya_trapezoidal_from_bin_counts
        
        overlap_approx = np.asarray(
            ecdf_bhattacharyya_trapezoidal_from_bin_counts(
                bc1_batch, bc2_batch, bin_edges, grid_size=256, prefer_gpu=self.gpu_available
            ),
            dtype=np.float64,
        )
        overlap_safe = np.clip(overlap_approx, 1e-10, 1.0)
        bhattacharyya = (-np.log(overlap_safe)).astype(np.float32)

        signed_delta = mean1 - mean2
        delta_mean = np.abs(signed_delta)

        stat_result = mann_whitney_from_bin_counts(
            bc1_batch,
            bc2_batch,
            n1=N1,
            n2=N2,
            prefer_gpu=self.gpu_available,
        )
        p_values = np.asarray(stat_result["p_value"], dtype=np.float32)

        tau2_1 = dl_heterogeneity(
            Sm=Sm1,
            Su=Su1,
            Swx2=Swx2_1,
            Sc2=Sc2_1,
            N=N1,
        )["tau2"]
        tau2_2 = dl_heterogeneity(
            Sm=Sm2,
            Su=Su2,
            Swx2=Swx2_2,
            Sc2=Sc2_2,
            N=N2,
        )["tau2"]

        effect_size = effect_size_from_components(
            delta_mean=delta_mean,
            overlap=overlap_safe,
            var1=variance1,
            var2=variance2,
            lambda_var=2.0,
        )["effect_size"]

        pos_safe = np.nan_to_num(
            np.asarray(positions, dtype=np.float64),
            nan=0,
            posinf=0,
            neginf=0,
        )
        results_view["position"] = np.clip(
            pos_safe, 0, np.iinfo(np.uint32).max
        ).astype(np.uint32)
        results_view["p_value"] = p_values
        results_view["q_value"] = p_values
        results_view["mean1"] = mean1.astype(np.float32)
        results_view["mean2"] = mean2.astype(np.float32)
        results_view["delta_mean"] = delta_mean.astype(np.float32)
        results_view["delta_sign"] = np.sign(signed_delta).astype(np.int8)
        results_view["bhattacharyya"] = bhattacharyya
        results_view["dist"] = np.full(len(positions), DIST_ECDF, dtype=np.uint8)
        results_view["n1"] = np.clip(N1, 0, np.iinfo(np.uint32).max).astype(np.uint32)
        results_view["n2"] = np.clip(N2, 0, np.iinfo(np.uint32).max).astype(np.uint32)
        results_view["variance1"] = variance1.astype(np.float32)
        results_view["variance2"] = variance2.astype(np.float32)
        results_view["tau2_1"] = np.asarray(tau2_1, dtype=np.float32)
        results_view["tau2_2"] = np.asarray(tau2_2, dtype=np.float32)
        results_view["effect_size"] = np.asarray(effect_size, dtype=np.float32)
        results_view["overlap_approx"] = overlap_approx.astype(np.float32)

    def _apply_fdr_correction(self, results_array: np.ndarray) -> np.ndarray:
        """Apply Storey's q-value method (always; no Benjamin-Hochberg)."""
        if len(results_array) == 0:
            return results_array
        p_values = results_array['p_value']
        q_values = self._storey_qvalue(p_values)
        results_array['q_value'] = q_values.astype(np.float32)

        return results_array

    @staticmethod
    def validate_centroid_parameters(centroid1: MethylSample, centroid2: MethylSample,
                                   extreme_threshold: float = 1000) -> dict:
        """
        Return basic centroid statistics (no distribution validation).
        Correct group classification is checked by the classifier centroid self-check
        when running the pipeline.
        """
        if not centroid1.is_centroid or not centroid2.is_centroid:
            return {"error": "Both centroids must be extended centroids"}

        try:
            centroid1, centroid2, common_pos = MethylCentroidPair.load_and_align_from_samples(centroid1, centroid2)
        except ValueError as e:
            return {"error": f"Failed to align centroids: {e}"}

        N1, N2 = centroid1.N, centroid2.N
        mean1 = np.asarray(centroid1.mean, dtype=np.float64)
        mean2 = np.asarray(centroid2.mean, dtype=np.float64)
        # Per-position variance: (Sx2 - Sx²/N) / (N-1)
        Sx1, Sx2_1 = np.asarray(centroid1.Sx.values, dtype=np.float64), np.asarray(centroid1.Sx2.values, dtype=np.float64)
        Sx2, Sx2_2 = np.asarray(centroid2.Sx.values, dtype=np.float64), np.asarray(centroid2.Sx2.values, dtype=np.float64)
        var1 = np.maximum((Sx2_1 - (Sx1 ** 2) / np.maximum(N1, 1)) / np.maximum(N1 - 1, 1), 1e-12)
        var2 = np.maximum((Sx2_2 - (Sx2 ** 2) / np.maximum(N2, 1)) / np.maximum(N2 - 1, 1), 1e-12)
        has_ecdf1 = bool(centroid1.binned_stats and "bin_edges" in centroid1.binned_stats and "bin_counts" in centroid1.binned_stats)
        has_ecdf2 = bool(centroid2.binned_stats and "bin_edges" in centroid2.binned_stats and "bin_counts" in centroid2.binned_stats)

        position_diffs = np.abs(mean1 - mean2)
        n_positions = len(position_diffs)
        trim_bottom = int(0.10 * n_positions)
        if trim_bottom < n_positions:
            sorted_diffs = np.sort(position_diffs)
            trimmed_mean_diff = float(np.mean(sorted_diffs[trim_bottom:]))
        else:
            trimmed_mean_diff = float(np.abs(mean1.mean() - mean2.mean()))

        return {
            "centroid1": {
                "n_positions": len(mean1),
                "mean_median": float(np.median(mean1)),
                "mean_mean": float(np.mean(mean1)),
                "variance_median": float(np.median(var1)),
                "variance_mean": float(np.mean(var1)),
                "sample_stats": {"min_N": int(N1.min()), "max_N": int(N1.max()), "mean_N": float(N1.mean()), "std_N": float(N1.std())},
                "has_ecdf": has_ecdf1,
            },
            "centroid2": {
                "n_positions": len(mean2),
                "mean_median": float(np.median(mean2)),
                "mean_mean": float(np.mean(mean2)),
                "variance_median": float(np.median(var2)),
                "variance_mean": float(np.mean(var2)),
                "sample_stats": {"min_N": int(N2.min()), "max_N": int(N2.max()), "mean_N": float(N2.mean()), "std_N": float(N2.std())},
                "has_ecdf": has_ecdf2,
            },
            "group_separation": trimmed_mean_diff,
            "separation_stats": {
                "mean_diff": float(np.mean(position_diffs)),
                "median_diff": float(np.median(position_diffs)),
                "min_diff": float(np.min(position_diffs)),
                "max_diff": float(np.max(position_diffs)),
            },
        }


    def _storey_qvalue(self, p_values: np.ndarray, lambda_seq=None) -> np.ndarray:
        """
        Manual implementation of Storey's q-value method (adaptive FDR control).
        
        Storey's method estimates π₀ (proportion of true null hypotheses) and uses
        it to adjust the FDR, making it less conservative than Benjamini-Hochberg
        when many true positives exist (common in genomics).

        Args:
            p_values: Array of p-values to correct
            lambda_seq: Sequence of λ values for π₀ estimation (default: 0.05 to 0.95)

        Returns:
            Array of q-values (Storey's FDR-corrected p-values)
        """
        if len(p_values) == 0:
            return np.array([])

        n = len(p_values)
        
        # Default lambda sequence for π₀ estimation
        if lambda_seq is None:
            lambda_seq = np.arange(0.05, 0.96, 0.05)
        
        # Estimate π₀ (proportion of true nulls) using bootstrap method
        pi0_estimates = []
        for lam in lambda_seq:
            # Count p-values > lambda
            w = np.sum(p_values > lam)
            # Estimate π₀ as: (# p-values > λ) / ((1 - λ) * total tests)
            pi0_est = w / (n * (1.0 - lam))
            pi0_estimates.append(pi0_est)
        
        # Use smoothing spline or simple average for π₀
        # For simplicity, use the minimum to be conservative
        pi0 = min(1.0, np.mean(pi0_estimates))
        pi0 = max(0.0, pi0)  # Ensure π₀ is in [0, 1]
        
        logger.debug(f"Storey's π₀ estimate: {pi0:.4f} (proportion of true nulls)")
        
        # Sort p-values and get original indices
        sorted_indices = np.argsort(p_values)
        sorted_p = p_values[sorted_indices]
        
        # Calculate q-values using π₀ adjustment
        # q(p_i) = min(π₀ * n * p_i / i) for all j >= i
        q_values = np.zeros(n)
        q_values[n-1] = min(1.0, pi0 * sorted_p[n-1])
        
        for i in range(n-2, -1, -1):
            q_val = min(1.0, pi0 * n * sorted_p[i] / (i + 1))
            q_values[i] = min(q_val, q_values[i+1])
        
        # Reorder back to original positions
        original_order = np.zeros(n, dtype=int)
        original_order[sorted_indices] = np.arange(n)
        q_values = q_values[original_order]

        return q_values

    @staticmethod
    def resolve_validation_samples(
        config_samples: Optional[Union[str, List[str]]],
        centroid_dir: Optional[str],
        chromosome: str,
        contexts: Optional[List[str]] = None,
        centroid_name: str = "centroid",
        fallback_samples_base_path: Optional[str] = None,
    ) -> List[str]:
        """
        Resolve validation sample paths from config or centroid metadata.

        Args:
            config_samples: "use_metadata", list of paths, or None
            centroid_dir: Directory containing centroid H5 files
            chromosome: Chromosome identifier
            contexts: List of contexts (uses first if provided)
            centroid_name: Name for logging
            fallback_samples_base_path: If centroid metadata lists basenames only and omits
                samples_base_path, join with this directory (e.g. project samples_base_path).
        """
        if config_samples is None:
            config_samples = "use_metadata"

        if isinstance(config_samples, list):
            logger.debug(f"Using {len(config_samples)} validation samples from config for {centroid_name}")
            return config_samples

        if config_samples != "use_metadata":
            return []

        if not centroid_dir:
            logger.warning(f"No centroid_dir provided for {centroid_name}; cannot read metadata samples")
            return []

        try:
            ctx = contexts[0] if contexts else "CG"
            centroid_path = Path(centroid_dir) / f"{chromosome}-{ctx}.h5"
            if not centroid_path.exists():
                logger.warning(f"Centroid file not found: {centroid_path}")
                return []

            centroid = MethylSample.load_from_h5(str(centroid_path))
            if centroid.metadata:
                meta = centroid.metadata
                raw = meta.get("sample_paths") or meta.get("samples_used", [])
                samples = MethylSample.resolve_samples_used_paths(
                    [str(x) for x in raw],
                    meta.get("samples_base_path"),
                    fallback_samples_base_path,
                )
                if samples:
                    logger.info(f"✅ Loaded {len(samples)} validation samples from {centroid_name} metadata")
                    return samples
                logger.warning(
                    f"No sample paths found in {centroid_name} metadata "
                    "(checked 'sample_paths' and 'samples_used')"
                )
            else:
                logger.warning(f"No metadata in {centroid_name} centroid")
        except Exception as e:
            logger.warning(f"Failed to read validation samples from {centroid_name} metadata: {e}")

        return []

    @staticmethod
    def load_binned_counts_from_centroids(
        dmps_df: pd.DataFrame,
        centroid1_dir: str,
        centroid2_dir: str,
        chromosome: str,
    ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """
        Load per-position bin counts from centroid H5 files for given DMPs.

        Returns:
            (bin_edges, counts1, counts2) or None if not available.
        """
        if dmps_df is None or dmps_df.empty:
            return None
        if "context" not in dmps_df.columns:
            dmps_df = dmps_df.copy()
            dmps_df["context"] = "CG"

        try:
            import h5py
        except Exception:
            return None

        counts1 = None
        counts2 = None
        bin_edges_ref = None

        for ctx in np.unique(dmps_df["context"].values):
            ctx_mask = dmps_df["context"].values == ctx
            ctx_positions = dmps_df.loc[ctx_mask, "position"].values.astype(np.uint32)
            if len(ctx_positions) == 0:
                continue

            c1_path = Path(centroid1_dir) / f"{chromosome}-{ctx}.h5"
            c2_path = Path(centroid2_dir) / f"{chromosome}-{ctx}.h5"
            if not c1_path.exists() or not c2_path.exists():
                return None

            try:
                with h5py.File(c1_path, "r") as f1, h5py.File(c2_path, "r") as f2:
                    md1 = f1.get("methylation_data")
                    md2 = f2.get("methylation_data")
                    if not (isinstance(md1, h5py.Group) and isinstance(md2, h5py.Group)):
                        return None
                    if "bins" not in md1.attrs or "bin_counts" not in md1 or "bins" not in md2.attrs or "bin_counts" not in md2:
                        return None
                    bins1 = int(md1.attrs["bins"])
                    bins2 = int(md2.attrs["bins"])
                    if bins1 != bins2 or bins1 <= 0:
                        logger.warning(f"Binned bins mismatch for context {ctx}; falling back to samples")
                        return None
                    n_bins = bins1
                    if bin_edges_ref is None:
                        bin_edges_ref = np.linspace(0, 1, n_bins + 1, dtype=np.float64)
                        counts1 = np.zeros((len(dmps_df), n_bins), dtype=np.int32)
                        counts2 = np.zeros((len(dmps_df), n_bins), dtype=np.int32)
                    else:
                        if n_bins != len(bin_edges_ref) - 1:
                            logger.warning(f"Binned bins mismatch for context {ctx}; falling back to samples")
                            return None

                    pos1 = np.asarray(md1["pos"][:], dtype=np.uint32)
                    pos2 = np.asarray(md2["pos"][:], dtype=np.uint32)
                    idx1 = np.searchsorted(pos1, ctx_positions)
                    idx2 = np.searchsorted(pos2, ctx_positions)
                    valid1 = (idx1 < len(pos1)) & (pos1[idx1] == ctx_positions)
                    valid2 = (idx2 < len(pos2)) & (pos2[idx2] == ctx_positions)
                    valid = valid1 & valid2
                    if not np.any(valid):
                        continue

                    bc1 = np.asarray(md1["bin_counts"][idx1[valid]])
                    bc2 = np.asarray(md2["bin_counts"][idx2[valid]])
                    global_idx = np.where(ctx_mask)[0][valid]
                    counts1[global_idx] = bc1
                    counts2[global_idx] = bc2
            except Exception:
                return None

        if bin_edges_ref is None:
            return None
        return bin_edges_ref, counts1, counts2

    def cleanup(self):
        """Clean up resources."""
        if self.gpu_available:
            force_gpu_cleanup()
            cleanup_gpu_memory()
        logger.info("MethylCentroidPair cleanup completed")
