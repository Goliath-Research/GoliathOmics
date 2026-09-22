# ruff: noqa: E402
# methyl_centroid.py
import sys
from pathlib import Path

# Add methylutils from monorepo to Python path (for development/direct execution)
# In monorepo: packages/methylcentroid/../methylutils/methyl_utils
methyl_utils_path = Path(__file__).parent.parent.parent / "methylutils" / "methyl_utils"
if methyl_utils_path.exists() and str(methyl_utils_path) not in sys.path:
    sys.path.insert(0, str(methyl_utils_path))

# Note: genomic_position_aligner (gpa_pkg) is now part of methylutils package
# No separate path addition needed - it's imported via methylutils

import numpy as np
from typing import List, Union, Optional, Tuple, Dict
from datetime import datetime
import json
import psutil
try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable=None, *args, **kwargs):
        return iterable if iterable is not None else []
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from methyl_utils.core.methyl_frame import (
    MethylSample,
    MethylCentroid as MethylCentroidData,
)

try:
    from .config import MethylCentroidConfig, CentroidResults
except ImportError:
    try:
        from methyl_centroid.config import MethylCentroidConfig, CentroidResults
    except ImportError:
        local_pkg_root = Path(__file__).parent
        if str(local_pkg_root) not in sys.path:
            sys.path.insert(0, str(local_pkg_root))
        from config import MethylCentroidConfig, CentroidResults  # type: ignore[reportMissingImports]
try:
    from .core.sample_manager import SmartSampleCache
except ImportError:
    try:
        from methyl_centroid.core.sample_manager import SmartSampleCache
    except ImportError:
        local_pkg_root = Path(__file__).parent
        if str(local_pkg_root) not in sys.path:
            sys.path.insert(0, str(local_pkg_root))
        from core.sample_manager import SmartSampleCache  # type: ignore[reportMissingImports]
# Distance calculation functions
from methyl_utils import (
    get_methyl_dtype,
    # Memory management
    get_memory_manager,
)


def _create_centroid_builder(
    min_coverage: int,
    use_gpu: bool,
    binned_stats_bins: int = 20,
    chunk_size: Optional[int] = None,
    metadata: Optional[Dict] = None,
    gpu_backend: Optional[str] = None,
):
    """Create the ECDF centroid builder with an explicit positive bin count."""
    from methyl_utils.core.centroid_builder import MethylCentroidBuilder

    if int(binned_stats_bins) < 1:
        raise ValueError(
            f"binned_stats_bins must be >= 1 for ECDF centroids, got {binned_stats_bins}"
        )
    kwargs: Dict = {
        "min_coverage": min_coverage,
        "use_gpu": use_gpu,
        "gpu_backend": gpu_backend,
    }
    if chunk_size is not None:
        kwargs["chunk_size"] = chunk_size
    if metadata is not None:
        kwargs["metadata"] = metadata
    return MethylCentroidBuilder(binned_stats_bins=int(binned_stats_bins), **kwargs)


def _common_parent_directory(dir_paths: List[str]) -> Optional[Path]:
    """If all paths resolve to directories sharing the same parent, return that parent."""
    if not dir_paths:
        return None
    try:
        resolved = [Path(p).resolve() for p in dir_paths]
    except OSError:
        return None
    parents = {p.parent for p in resolved}
    if len(parents) == 1:
        return parents.pop()
    return None


def _normalize_cohort_dir(sample: Union[str, Path]) -> str:
    """Normalize a sample directory or {chrom}-{ctx}.h5 path to the sample directory string."""
    path = Path(sample)
    if path.suffix.lower() == ".h5":
        path = path.parent
    return str(path)


def _read_centroid_baseline_dirs(centroid_path: Path) -> List[str]:
    """Resolve sample directory paths from existing centroid HDF5 metadata (samples_used)."""
    if not centroid_path.exists():
        return []
    try:
        import h5py

        with h5py.File(centroid_path, "r") as f:
            raw_used = f.attrs.get("samples_used")
            raw_base = f.attrs.get("samples_base_path")
            if raw_used is None:
                return []
            if isinstance(raw_used, bytes):
                raw_used = raw_used.decode("utf-8")
            if isinstance(raw_used, str):
                raw_used = json.loads(raw_used)
            if not isinstance(raw_used, (list, tuple)):
                return []
            meta_base: Optional[str] = None
            if raw_base is not None:
                meta_base = (
                    raw_base.decode("utf-8") if isinstance(raw_base, bytes) else str(raw_base)
                )
            return MethylSample.resolve_samples_used_paths(
                [str(x) for x in raw_used],
                meta_base,
                None,
            )
    except Exception:
        return []


def _plan_cohort_lists_for_runner(
    add_samples: Optional[List[str]],
    remove_samples: Optional[List[str]],
    output_dir: Union[str, Path],
    chrom: str,
    ctx: str,
) -> Tuple[List[str], List[str], List[str]]:
    """
    Produce (_original_samples, _original_add_samples, _original_remove_samples) directory strings
    for cohort resolution without a config ``samples`` field.
    """
    add_dirs = [
        _normalize_cohort_dir(s)
        for s in (add_samples or [])
        if s is not None and str(s).strip()
    ]
    rem_dirs = [
        _normalize_cohort_dir(s)
        for s in (remove_samples or [])
        if s is not None and str(s).strip()
    ]
    centroid_path = Path(output_dir) / f"{chrom}-{ctx}.h5"
    baseline = _read_centroid_baseline_dirs(centroid_path)

    if rem_dirs:
        return baseline, add_dirs, rem_dirs
    if not add_dirs:
        return baseline, [], []
    if not baseline:
        return [], add_dirs, []
    b_names = {Path(p).name for p in baseline}
    a_names = {Path(p).name for p in add_dirs}
    if b_names.isdisjoint(a_names):
        return baseline, add_dirs, []
    return [], add_dirs, []


def _is_gpu_oom_error(exc: BaseException) -> bool:
    """Best-effort detection for CuPy/RMM CUDA OOM failures."""
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "out_of_memory",
            "cudaerrormemoryallocation",
            "memoryallocation",
            "cuda error",
            "bad_alloc",
        )
    )


from methyl_utils import (
    # Performance profiling
    get_performance_profiler,
    # Chunked processing
    ChunkedGenomicProcessor,
    # GPU detection
    is_gpu_available,
    prefer_gpu_default,
    cleanup_gpu_memory,
    # Logging
    get_logger,
)


class MethylCentroid:
    """
    Class to calculate methylation centroid for a group of samples / (chromosome, context).

    Designed for performance with large genomic datasets by using dense matrices
    and minimizing memory overhead. Processes each (chromosome, context) independently to
    optimize memory usage, with coverage-based filtering for centroids.

    Supports incremental operations for centroid updates.
    Uses MethylUtils for optimized distance calculations, GPU acceleration, and memory management.

    Workflows:
    - Initial centroid creation: provide add_samples and output_dir
    - Centroid updates: provide add_samples/remove_samples; baseline cohort is inferred from
      existing centroid HDF5 when needed (see _plan_cohort_lists_for_runner)

    The centroid represents the average of mC and uC values across all samples:
    - centroid.mC = sum(all sample.mC) / sample_count
    - centroid.uC = sum(all sample.uC) / sample_count
    - sample_count = number of samples that contributed to the centroid (parameter-based)
    - Centroid structure is similar to a sample (pos, mC, uC, tnc)
    """

    def __init__(
        self,
        chrom: str,
        ctx: str,
        output_dir: Path,
        add_samples: List[str] = None,
        remove_samples: List[str] = None,
        min_coverage: int = 4,
        min_samples: int = 1,
        max_sample_workers: Optional[int] = None,
        verbose: bool = True,
        binned_stats_bins: int = 20,
        use_gpu: Optional[bool] = None,
        # Metadata fields
        laboratory: str = None,
        disease: str = None,
        group: str = None,
        batch: str = None,
        # Coverage capping (binomial thinning) for outlier correction
        cap_coverage: bool = False,
        cap_coverage_n_cap: Optional[int] = None,
        cap_coverage_seed: Optional[int] = None,
        cap_coverage_auto_n_cap: bool = False,
        cap_coverage_n_cap_method: str = "iqr",
        cap_coverage_n_cap_iqr_multiplier: float = 1.5,
        cap_coverage_n_cap_max_positions: int = 100_000,
        residualize_coef_dir: Optional[str] = None,
        gpu_backend: Optional[str] = None,
    ):
        from contextlib import contextmanager
        import logging

        @contextmanager
        def tqdm_logging_disabled():
            """Context manager to temporarily disable debug logging during tqdm operations."""
            if not self.verbose:
                # Only disable debug logging if not in verbose mode
                original_level = self.logger.level
                self.logger.setLevel(logging.INFO)
                try:
                    yield
                finally:
                    self.logger.setLevel(original_level)
            else:
                yield

        self.tqdm_logging_disabled = tqdm_logging_disabled
        """
        Initialize MethylCentroid for centroid calculation.

        Args:
            chrom: Chromosome identifier (e.g., '1', 'X').
            ctx: Context type (e.g., 'CG', 'CHG', 'CHH').
            output_dir: Directory to save centroid files.
            add_samples: Sample directory paths to include (full cohort for typical pipeline runs, or
                incremental adds when disjoint from the existing centroid cohort on disk).
            remove_samples: Sample directory paths to remove (incremental updates; implies baseline
                from existing centroid HDF5 metadata).
            min_coverage: Minimum sum of mC and uC for a position to be included.
            verbose: Enable verbose logging.

        Workflow:
            - Initial centroid creation: provide add_samples, output_dir
            - Centroid updates: provide add_samples/remove_samples (baseline from HDF5 when applicable)
        """

        # Setup logging using MethylUtils
        self.verbose = verbose
        self.logger = get_logger(__name__, verbose=verbose)

        out_path = Path(output_dir) if isinstance(output_dir, str) else output_dir
        _orig_s, _orig_a, _orig_r = _plan_cohort_lists_for_runner(
            add_samples, remove_samples, out_path, chrom, ctx
        )

        if _orig_s:
            self.samples = [Path(s) / f"{chrom}-{ctx}.h5" for s in _orig_s]
            self._original_samples = list(_orig_s)
            self.add_samples = [Path(s) / f"{chrom}-{ctx}.h5" for s in _orig_a]
            self._original_add_samples = list(_orig_a)
        elif _orig_r:
            # Incremental update without on-disk baseline yet: empty cohort + explicit deltas.
            self._original_samples = []
            self.samples = []
            self._original_add_samples = list(_orig_a)
            self.add_samples = [Path(s) / f"{chrom}-{ctx}.h5" for s in _orig_a]
        else:
            self.samples = (
                [Path(s) / f"{chrom}-{ctx}.h5" for s in _orig_a] if _orig_a else []
            )
            self._original_samples = list(_orig_a) if _orig_a else []
            self.add_samples = []
            self._original_add_samples = []
            if self.samples:
                _seen = set()
                _new_s, _new_o = [], []
                for p, orig in zip(self.samples, self._original_samples):
                    name = p.parent.name
                    if name in _seen:
                        continue
                    _seen.add(name)
                    _new_s.append(p)
                    _new_o.append(orig)
                if len(_new_s) < len(self.samples):
                    self.samples = _new_s
                    self._original_samples = _new_o

        # Handle remove samples for incremental updates
        self.remove_samples = (
            [Path(s) / f"{chrom}-{ctx}.h5" for s in _orig_r] if _orig_r else []
        )
        self._original_remove_samples = list(_orig_r)

        # Ensure no sample in add_samples is already in the centroid (samples / samples_used).
        # Match by directory basename so the same sample under different paths is not added twice.
        self._deduplicate_add_samples()

        self.min_coverage = max(1, min_coverage)
        self.min_samples = max(1, int(min_samples))
        self.max_sample_workers = max_sample_workers
        self._cap_coverage = cap_coverage and cap_coverage_n_cap is not None and cap_coverage_n_cap >= 1
        self._cap_coverage_n_cap = cap_coverage_n_cap if self._cap_coverage else None
        self._cap_coverage_seed = cap_coverage_seed
        self._cap_coverage_auto_n_cap = cap_coverage_auto_n_cap
        self._cap_coverage_n_cap_method = cap_coverage_n_cap_method
        self._cap_coverage_n_cap_iqr_multiplier = cap_coverage_n_cap_iqr_multiplier
        self._cap_coverage_n_cap_max_positions = cap_coverage_n_cap_max_positions
        if cap_coverage and not self._cap_coverage and not cap_coverage_auto_n_cap:
            self.logger.warning(
                "cap_coverage=True but cap_coverage_n_cap is missing or < 1; coverage capping disabled. "
                "Set cap_coverage_n_cap (e.g. 50) in base_config, or cap_coverage_auto_n_cap=True to estimate from IQR on sampled coverage."
            )
        self.chrom = chrom
        self.ctx = ctx
        self.output_dir = (
            Path(output_dir) if isinstance(output_dir, str) else output_dir
        )

        # ECDF bins are mandatory for supported centroids.
        self.binned_stats_bins = int(binned_stats_bins)
        if self.binned_stats_bins < 1:
            raise ValueError(
                f"binned_stats_bins must be >= 1 for ECDF centroids, got {binned_stats_bins}"
            )
        self._binned_stats = None
        self.residualize_coef_dir = str(residualize_coef_dir) if residualize_coef_dir else None
        self._residualize_apply_fn = None

        # Ensure output directory exists from the start
        try:
            # First ensure parent directories exist
            self.output_dir.parent.mkdir(parents=True, exist_ok=True)
            # Then create the target directory
            self.output_dir.mkdir(exist_ok=True)
            # Verify directory was actually created
            if not self.output_dir.exists():
                raise OSError(f"Failed to create output directory: {self.output_dir}")
            # Note: os.access may not work correctly in container environments
        except PermissionError as e:
            raise OSError(
                f"Permission denied creating output directory {self.output_dir}. "
                f"Please ensure the parent directory is writable: {self.output_dir.parent}. "
                f"Original error: {e}"
            )
        except Exception as e:
            raise OSError(f"Cannot create output directory {self.output_dir}: {e}")

        # Derive centroid path from output directory and chromosome/context
        self.centroid_path = self.output_dir / f"{chrom}-{ctx}.h5"

        # Store metadata fields
        self.laboratory = laboratory
        self.disease = disease
        self.group = group
        self.batch = batch

        self.centroid: Optional[Path] = None

        # Detect GPU availability (honors METHYL_DISABLE_GPU) and respect explicit user choice
        from methyl_utils.array_backend import (
            gpu_disabled_by_env,
            normalize_gpu_backend,
        )

        gpu_available = prefer_gpu_default()
        self.logger.info(f"GPU available: {gpu_available}")

        self._gpu_enabled = True if use_gpu is None else bool(use_gpu)
        self.gpu_backend = normalize_gpu_backend(gpu_backend)
        if not self._gpu_enabled:
            self.logger.info("GPU explicitly disabled by configuration; forcing CPU mode")

        if self.gpu_backend == "mojo":
            self.use_gpu = self._gpu_enabled and not gpu_disabled_by_env()
        elif self.gpu_backend == "numpy":
            self.use_gpu = False
        elif self.gpu_backend == "cupy":
            if not gpu_available:
                raise RuntimeError("gpu_backend=cupy but CuPy/CUDA is not available")
            self.use_gpu = self._gpu_enabled
        else:
            self.use_gpu = gpu_available and self._gpu_enabled
        if self.use_gpu:
            self.logger.info(
                "Using GPU acceleration backend=%s",
                self.gpu_backend or "auto",
            )
        else:
            self.logger.warning("GPU disabled or unavailable; using CPU processing")

        # Initialize centroid accumulator (will be created when first sample is added)
        self._centroid: Optional[MethylCentroidData] = None
        self._min_coverage = min_coverage

        # Initialize memory manager from MethylUtils
        self.memory_manager = get_memory_manager()

        # Initialize performance profiler from MethylUtils
        self.performance_profiler = get_performance_profiler()

        # Initialize chunked processor with dynamic memory-aware parameters
        chunked_params = self._calculate_chunked_processor_params(self.use_gpu)
        self.chunked_processor = ChunkedGenomicProcessor(
            chunk_size_positions=chunked_params["chunk_size_positions"],
            max_workers=chunked_params["max_workers"],
            use_gpu=self.use_gpu,  # Use GPU when available for large datasets
            memory_limit_gb=chunked_params["memory_limit_gb"],
        )

        # Track active samples (those currently included in centroid calculation).
        # Once the centroid is built, samples are only used to know which were added;
        # centroid data is loaded from the centroid H5 (with slicing by pos index) when needed.
        self.active_samples: set = set()

        self.sample_cache = SmartSampleCache(self.memory_manager)
        self._cache_enabled = True  # Flag to control caching behavior

        # GPU usage tracking and fallback management
        self._gpu_available = gpu_available
        self._using_gpu = self.use_gpu
        self._gpu_memory_pressure_detected = False

        for sample in self.samples + self.add_samples:
            if not sample.exists():
                print(f"Warning: Sample {sample} does not exist")

    def _deduplicate_add_samples(self) -> None:
        """
        Remove from add_samples any sample already in the centroid (samples / samples_used).
        Match by directory basename so the same sample under different paths is not added twice.
        Also deduplicate within add_samples (keep first occurrence per basename).
        """
        if not self.add_samples:
            return
        # Existing sample IDs (directory basename) already in the centroid
        existing_basenames = {p.parent.name for p in self.samples}
        # Filter add_samples: drop if already in centroid, and drop duplicates within add_samples
        seen_basename = set(existing_basenames)
        new_add_paths = []
        new_original = []
        skipped_in_centroid = []
        skipped_duplicate = []
        for i, p in enumerate(self.add_samples):
            name = p.parent.name
            if name in existing_basenames:
                skipped_in_centroid.append(name)
                continue
            if name in seen_basename:
                skipped_duplicate.append(name)
                continue
            seen_basename.add(name)
            new_add_paths.append(p)
            new_original.append(self._original_add_samples[i])
        n_removed_centroid = len(skipped_in_centroid)
        n_removed_dup = len(skipped_duplicate)
        if n_removed_centroid or n_removed_dup:
            self.logger.info(
                "Deduplicated add_samples: %s already in centroid (skipped), %s duplicate in add list (skipped), %s to add",
                n_removed_centroid,
                n_removed_dup,
                len(new_add_paths),
            )
            if n_removed_centroid and self.verbose:
                preview = list(dict.fromkeys(skipped_in_centroid))[:5]
                self.logger.info("  Already in centroid (sample ID): %s%s", preview, " ..." if n_removed_centroid > 5 else "")
        self.add_samples = new_add_paths
        self._original_add_samples = new_original

    def _sample_dir_string(self, sample: Union[str, Path]) -> str:
        """Normalize a sample directory or {chrom}-{ctx}.h5 path to the sample directory."""
        path = Path(sample)
        if path.suffix == ".h5":
            path = path.parent
        return str(path)

    def _sample_key(self, sample: Union[str, Path]) -> str:
        """Stable sample identity used for cohort deduplication and removal matching."""
        return Path(self._sample_dir_string(sample)).name

    def _resolve_effective_sample_dirs(self) -> List[str]:
        """
        Resolve the final cohort from _original_samples, _original_add_samples, and _original_remove_samples.

        Semantics are deterministic and do not require an existing centroid file:
        start from `_original_samples`, remove `_original_remove_samples`, then append `_original_add_samples`.
        Matching prefers exact directory paths and falls back to sample directory basename.
        """
        effective: List[str] = []
        exact_index: Dict[str, int] = {}
        key_index: Dict[str, int] = {}

        def rebuild_index() -> None:
            exact_index.clear()
            key_index.clear()
            for idx, sample_dir in enumerate(effective):
                exact_index[sample_dir] = idx
                key_index[self._sample_key(sample_dir)] = idx

        def append_unique(sample: Union[str, Path]) -> bool:
            sample_dir = self._sample_dir_string(sample)
            sample_key = self._sample_key(sample_dir)
            if sample_dir in exact_index or sample_key in key_index:
                return False
            exact_index[sample_dir] = len(effective)
            key_index[sample_key] = len(effective)
            effective.append(sample_dir)
            return True

        for sample in self._original_samples:
            append_unique(sample)

        removed_count = 0
        unmatched_removals: List[str] = []
        for sample in self._original_remove_samples:
            sample_dir = self._sample_dir_string(sample)
            sample_key = self._sample_key(sample_dir)
            idx = exact_index.get(sample_dir)
            if idx is None:
                idx = key_index.get(sample_key)
            if idx is None:
                unmatched_removals.append(sample_dir)
                continue
            effective.pop(idx)
            removed_count += 1
            rebuild_index()

        added_count = 0
        skipped_adds = 0
        for sample in self._original_add_samples:
            if append_unique(sample):
                added_count += 1
            else:
                skipped_adds += 1

        if removed_count or added_count or skipped_adds or unmatched_removals:
            self.logger.info(
                "Resolved sample deltas: start=%s, removed=%s, added=%s, skipped_duplicate_adds=%s, unmatched_removals=%s, final=%s",
                len(self._original_samples),
                removed_count,
                added_count,
                skipped_adds,
                len(unmatched_removals),
                len(effective),
            )
            if unmatched_removals and self.verbose:
                preview = unmatched_removals[:5]
                suffix = " ..." if len(unmatched_removals) > 5 else ""
                self.logger.warning(
                    "remove_samples not present in current cohort: %s%s",
                    preview,
                    suffix,
                )

        return effective

    def _apply_effective_sample_set(self) -> List[str]:
        """Convert the resolved final cohort into the runtime sample lists used for building."""
        effective_dirs = self._resolve_effective_sample_dirs()
        self.samples = [
            Path(sample_dir) / f"{self.chrom}-{self.ctx}.h5" for sample_dir in effective_dirs
        ]
        self.add_samples = []
        self.remove_samples = []
        return effective_dirs

    def _apply_centroid_filters(self) -> None:
        """
        Reapply cohort-level filters after any centroid mutation.

        This keeps the in-memory update path aligned with the full build path.
        """
        if self._centroid is None:
            return
        centroid_cpu = self._centroid.to_cpu()
        if len(centroid_cpu) == 0:
            self._centroid = centroid_cpu
            return

        coverage_vals = np.asarray(centroid_cpu.coverage.values, dtype=np.uint64)
        N_vals = np.asarray(centroid_cpu.N.values, dtype=np.uint32)
        valid_mask = (coverage_vals >= self.min_coverage) & (N_vals >= self.min_samples)
        if valid_mask.all():
            self._centroid = centroid_cpu
            return

        valid_indices = np.where(valid_mask)[0]
        if len(valid_indices) == 0:
            import pandas as pd

            empty_df = pd.DataFrame(
                {
                    "pos": [],
                    "tnc": [],
                    "N": [],
                    "Sx": [],
                    "Sx2": [],
                    "Sm": [],
                    "Su": [],
                    "Sc2": [],
                    "Swx2": [],
                }
            )
            empty_centroid = MethylCentroidData(empty_df, centroid_cpu.metadata)
            if getattr(centroid_cpu, "binned_stats", None) is not None:
                bin_edges = np.asarray(centroid_cpu.binned_stats["bin_edges"], dtype=np.float32)
                empty_centroid.set_binned_stats(
                    bin_edges,
                    np.zeros((0, len(bin_edges) - 1), dtype=np.float64),
                )
            self._centroid = empty_centroid
            return

        self._centroid = centroid_cpu.apply_mask(valid_indices)

    @classmethod
    def from_config(
        cls, config: MethylCentroidConfig, verbose: bool = None
    ) -> "MethylCentroid":
        # Use config.verbose if verbose parameter is not provided, otherwise use the parameter
        verbose_value = verbose if verbose is not None else config.verbose

        return cls(
            chrom=config.chrom,
            ctx=config.ctx,
            output_dir=config.output_dir,
            add_samples=config.add_samples,
            remove_samples=config.remove_samples,
            min_coverage=config.min_coverage,
            max_sample_workers=config.max_sample_workers,
            verbose=verbose_value,
            use_gpu=config.use_gpu,
            gpu_backend=getattr(config, "gpu_backend", None),
            # Metadata fields
            laboratory=config.laboratory,
            disease=config.disease,
            group=config.group,
            batch=config.batch,
            cap_coverage=config.cap_coverage,
            cap_coverage_n_cap=config.cap_coverage_n_cap,
            cap_coverage_seed=config.cap_coverage_seed,
            cap_coverage_auto_n_cap=config.cap_coverage_auto_n_cap,
            cap_coverage_n_cap_method=config.cap_coverage_n_cap_method,
            cap_coverage_n_cap_iqr_multiplier=config.cap_coverage_n_cap_iqr_multiplier,
            cap_coverage_n_cap_max_positions=config.cap_coverage_n_cap_max_positions,
            binned_stats_bins=config.binned_stats_bins,
            residualize_coef_dir=config.residualize_coef_dir,
        )

    @classmethod
    def from_json(
        cls, json_data: str | dict, verbose: bool = False
    ) -> "MethylCentroid":
        config = MethylCentroidConfig.model_validate(json_data)
        return cls.from_config(config, verbose=verbose)

    def get_config(self) -> MethylCentroidConfig:
        config_dict = {
            "chrom": self.chrom,
            "ctx": self.ctx,
            "output_dir": str(self.output_dir),
            "add_samples": self._original_add_samples,
            "remove_samples": self._original_remove_samples,
            "min_coverage": self.min_coverage,
            "use_gpu": self._gpu_enabled,
            "gpu_backend": getattr(self, "gpu_backend", None),
            "max_sample_workers": self.max_sample_workers,
            # Metadata fields
            "laboratory": self.laboratory,
            "disease": self.disease,
            "group": self.group,
            "batch": self.batch,
            "cap_coverage": self._cap_coverage,
            "cap_coverage_n_cap": self._cap_coverage_n_cap,
            "cap_coverage_seed": self._cap_coverage_seed,
            "cap_coverage_auto_n_cap": self._cap_coverage_auto_n_cap,
            "cap_coverage_n_cap_method": self._cap_coverage_n_cap_method,
            "cap_coverage_n_cap_iqr_multiplier": self._cap_coverage_n_cap_iqr_multiplier,
            "cap_coverage_n_cap_max_positions": self._cap_coverage_n_cap_max_positions,
            "binned_stats_bins": self.binned_stats_bins,
            "residualize_coef_dir": self.residualize_coef_dir,
        }

        return MethylCentroidConfig(**config_dict)

    def _print_progress(self):
        if self.progress_bar is None:
            total = len(self.samples) + len(self.add_samples)
            self.progress_bar = tqdm(total=total, desc="Adding samples", unit="sample")

        # Simply increment by 1 for each sample processed
        self.progress_bar.update(1)

        # Close progress bar if complete
        if self.progress_bar.n >= self.progress_bar.total:
            self.progress_bar.close()
            self.progress_bar = None

    def _load_existing_centroid_state(self, centroid_path: Path) -> bool:
        if not centroid_path.exists():
            print(f"Centroid file not found: {centroid_path}")
            return False
        try:
            # Load the centroid as MethylCentroid (data)
            loaded_centroid = MethylSample.load_from_h5(centroid_path)

            # Ensure it's a centroid (full schema with Sm, Su, Sc2, Swx2)
            if not isinstance(loaded_centroid, MethylCentroidData):
                print(
                    f"Loaded file is not a centroid: {type(loaded_centroid)}"
                )
                return False

            self._centroid = loaded_centroid

            # Mark original samples as active
            for i in range(len(self.samples)):
                self.active_samples.add((False, i))

            # Store centroid reference
            self.centroid = centroid_path

            print(f"Loaded existing centroid state from {centroid_path}")
            return True
        except Exception as e:
            print(f"Error loading existing centroid state: {e}")
            import traceback

            traceback.print_exc()
            return False

    def add_sample(
        self, sample_index: int, is_new_sample: bool = False, sample_path: Path = None
    ) -> bool:
        # Determine which sample list to use and get the actual sample
        if sample_path is not None:
            sample = sample_path
        elif is_new_sample:
            if sample_index >= len(self.add_samples):
                print(f"Add sample index {sample_index} out of range")
                return False
            sample = self.add_samples[sample_index]
        else:
            if sample_index >= len(self.samples):
                print(f"Sample index {sample_index} out of range")
                return False
            sample = self.samples[sample_index]

        # Create a unique identifier for tracking active samples
        if sample_path is not None:
            sample_id = ("path", str(sample_path))
        else:
            sample_id = (is_new_sample, sample_index)

        if sample_id in self.active_samples:
            print(f"Sample {sample_index} (new={is_new_sample}) is already included.")
            return False

        if not sample.exists():
            print(f"Sample {sample} does not exist")
            return False

        methyl_sample = None
        try:
            methyl_sample = self.load_sample(sample)

            if len(methyl_sample.pos) == 0:
                print(f"Sample {sample} has no valid positions")
                return False

            # Add the sample to centroid using new method
            if self._centroid is None:
                # Create initial centroid from first sample
                builder = self._new_centroid_builder(
                    self._min_coverage,
                    self.use_gpu,
                    binned_stats_bins=getattr(self, "binned_stats_bins", 20),
                )
                builder.add_sample(sample)
                self._centroid = builder.finalize()
            else:
                # Add sample to existing centroid (same residualize hook as the builder)
                self._centroid = self._centroid.add_sample(
                    methyl_sample,
                    residualize_apply=self._get_residualize_apply(),
                    sample_path=str(sample),
                )

            self._apply_centroid_filters()
            self.active_samples.add(sample_id)
            self._print_progress()

        except Exception as e:
            print(f"Error processing {sample}: {e}")
            return False
        finally:
            if methyl_sample is not None:
                try:
                    methyl_sample.close()
                except Exception as e:
                    self.logger.debug(f"Sample cleanup failed: {e}")
            self._cleanup_gpu_after_sample()

        return True

    def remove_sample(self, sample_index: int, is_new_sample: bool = False):
        index = (is_new_sample, sample_index)
        if index not in self.active_samples:
            return

        sample_path = (
            self.add_samples[sample_index]
            if is_new_sample
            else self.samples[sample_index]
        )

        methyl_sample = self.load_sample(sample_path)

        # Remove sample from centroid
        if self._centroid is None:
            raise RuntimeError("Cannot remove sample: no centroid exists")

        try:
            self._centroid = self._centroid.remove_sample(
                methyl_sample,
                residualize_apply=self._get_residualize_apply(),
                sample_path=str(sample_path),
            )
            self._apply_centroid_filters()
        except ValueError as e:
            raise RuntimeError(f"Failed to remove sample: {e}")
        finally:
            try:
                methyl_sample.close()
            except Exception as e:
                self.logger.debug(f"Sample cleanup failed: {e}")
            self._cleanup_gpu_after_sample()

        self.active_samples.remove(index)

    def _get_active_sample_paths(self) -> list:
        """
        Get the sample directory paths currently active in the centroid.

        Returns:
            List of sample directory paths.
        """
        active_paths = []
        for is_new_sample, sample_index in sorted(self.active_samples):
            if is_new_sample:
                # Sample from add_samples list
                if sample_index < len(self.add_samples):
                    sample_path = self.add_samples[sample_index]
                    active_paths.append(str(sample_path.parent))
            else:
                # Sample from original samples list
                if sample_index < len(self.samples):
                    sample_path = self.samples[sample_index]
                    active_paths.append(str(sample_path.parent))
        return active_paths

    def _filter_missing_sample_files(self) -> List[Path]:
        """
        Filter out samples whose expected {chrom}-{ctx}.h5 files are missing.

        Returns:
            List of missing sample file paths.
        """
        missing_samples = [p for p in self.samples if not p.is_file()]
        missing_add_samples = [p for p in self.add_samples if not p.is_file()]
        missing = missing_samples + missing_add_samples

        if missing:
            preview = ", ".join(str(p) for p in missing[:5])
            suffix = " ..." if len(missing) > 5 else ""
            self.logger.warning(
                f"Skipping {len(missing)} missing sample files. "
                f"Examples: {preview}{suffix}"
            )

        # Keep only existing files
        self.samples = [p for p in self.samples if p.is_file()]
        self.add_samples = [p for p in self.add_samples if p.is_file()]

        return missing

    def add_samples_parallel(self):
        def load_sample_data(
            sample_path: Path,
        ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
            try:
                if not sample_path.is_file():
                    self.logger.warning(f"Sample file missing: {sample_path}")
                    return None

                from methyl_utils import MethylSample

                methyl_sample = MethylSample.load_from_h5(sample_path)
                original_sample = methyl_sample
                sample_cpu = None
                try:
                    # Ensure sample is on CPU (converts GPU arrays if needed)
                    sample_cpu = methyl_sample.to_cpu()
                    methyl_sample = sample_cpu

                    # Get underlying numpy arrays from Series properties
                    # Ensure we get actual numpy arrays, not cupy arrays or Series
                    pos_series = methyl_sample.pos
                    mC_series = methyl_sample.mC
                    uC_series = methyl_sample.uC
                    # Access tnc from DataFrame directly (no property defined)
                    tnc_series = methyl_sample._df["tnc"]

                    # Extract numpy arrays from Series
                    if hasattr(pos_series, "values"):
                        pos = np.asarray(pos_series.values, dtype=np.uint32)
                    else:
                        pos = np.asarray(pos_series, dtype=np.uint32)

                    if hasattr(mC_series, "values"):
                        mC = np.asarray(mC_series.values, dtype=np.uint32)
                    else:
                        mC = np.asarray(mC_series, dtype=np.uint32)

                    if hasattr(uC_series, "values"):
                        uC = np.asarray(uC_series.values, dtype=np.uint32)
                    else:
                        uC = np.asarray(uC_series, dtype=np.uint32)

                    if hasattr(tnc_series, "values"):
                        tnc = np.asarray(tnc_series.values, dtype=np.uint8)
                    else:
                        tnc = np.asarray(tnc_series, dtype=np.uint8)
                finally:
                    for sample_obj in (sample_cpu, original_sample):
                        if sample_obj is not None:
                            try:
                                sample_obj.close()
                            except Exception:
                                pass

                # Filter out positions with no coverage to reduce memory usage
                coverage = mC + uC
                valid_mask = coverage > 0

                if np.any(valid_mask):
                    # Filter arrays (already numpy arrays from .values above)
                    filtered_pos = pos[valid_mask]
                    filtered_mC = mC[valid_mask]
                    filtered_uC = uC[valid_mask]
                    filtered_tnc = tnc[valid_mask]

                    return (
                        filtered_pos,
                        filtered_mC,
                        filtered_uC,
                        filtered_tnc,
                    )
                else:
                    return (
                        np.array([], dtype=np.uint32),
                        np.array([], dtype=np.uint32),
                        np.array([], dtype=np.uint32),
                        np.array([], dtype=np.uint8),
                    )
            except Exception as e:
                import traceback

                print(f"Error loading sample {sample_path}: {e}")
                print(f"Traceback: {traceback.format_exc()}")
                return None

        all_samples = self.samples + self.add_samples

        # Streaming builder applies residualize to every sample. Use it whenever
        # residualize is bound, including CPU, so later samples do not mix raw Sx.
        if self.use_gpu or self.residualize_coef_dir:
            self.logger.info(
                f"Using streaming centroid builder for {self.ctx} (GPU enabled)"
            )
            builder = self._create_streaming_builder()

            for sample_idx, sample_path in tqdm(
                enumerate(all_samples),
                total=len(all_samples),
                desc="Adding samples",
                unit="sample",
            ):
                if not sample_path.is_file():
                    self.logger.warning(f"Sample file missing: {sample_path}")
                    continue
                try:
                    sample_added = False
                    for attempt in range(2):
                        try:
                            builder.add_sample(sample_path)
                            sample_added = True
                            break
                        except (RuntimeError, MemoryError) as e:
                            if (
                                attempt == 0
                                and getattr(builder, "use_gpu", False)
                                and _is_gpu_oom_error(e)
                            ):
                                self.logger.warning(
                                    "GPU OOM while processing %s for %s-%s; "
                                    "switching the streaming centroid builder to CPU and retrying.",
                                    sample_path.name,
                                    self.chrom,
                                    self.ctx,
                                )
                                try:
                                    builder.release_gpu()
                                except Exception as cleanup_error:
                                    self.logger.debug(
                                        "GPU builder release before CPU fallback failed: %s",
                                        cleanup_error,
                                    )
                                self._cleanup_gpu_after_sample()
                                builder, rebuilt_indices = self._rebuild_streaming_builder_on_cpu(
                                    all_samples,
                                    sample_idx,
                                )
                                for rebuilt_idx in rebuilt_indices:
                                    self.active_samples.add(
                                        self._sample_id_for_index(rebuilt_idx)
                                    )
                                continue
                            raise

                    if not sample_added:
                        continue

                    self.active_samples.add(self._sample_id_for_index(sample_idx))
                except Exception as e:
                    self.logger.error(
                        f"Failed to process sample {sample_path.name}: {e}"
                    )
                    self.logger.exception("Full traceback:")
                finally:
                    self._cleanup_gpu_after_sample()

            if builder.samples_processed == 0:
                return

            self._centroid = builder.finalize()
            self._apply_centroid_filters()
            self.logger.info(
                f"Sample addition completed: {len(self.active_samples)} samples added"
            )
            return

        # Dynamic worker calculation based on memory and CPU
        memory_info = self.memory_manager.get_memory_usage()
        total_memory_gb = memory_info.get("total_gb", 400)
        available_memory_gb = memory_info.get("available_gb", 350)
        estimated_memory_per_sample_gb = 0.8  # Conservative estimate: 800MB per sample

        # Memory-based limit: reserve 30% of memory for processing
        memory_reserved_gb = total_memory_gb * 0.3
        max_workers_by_memory = max(
            1,
            int(
                (available_memory_gb - memory_reserved_gb)
                / estimated_memory_per_sample_gb
            ),
        )

        # CPU-based limit: use 75% of available CPUs
        cpu_count = psutil.cpu_count()
        max_workers_by_cpu = max(1, int(cpu_count * 0.75))

        # Context-based adjustment (CHH has more positions, needs more memory)
        context_multiplier = {"CG": 1.0, "CHG": 4.0, "CHH": 16.0}.get(self.ctx, 1.0)
        max_workers_by_memory = max(1, int(max_workers_by_memory / context_multiplier))

        # Final worker count
        actual_batch_size = min(
            max_workers_by_memory, max_workers_by_cpu, len(all_samples)
        )

        uncapped_batch_size = actual_batch_size
        cap = None
        cap_reason = None
        if self.max_sample_workers is not None:
            cap = self.max_sample_workers
            cap_reason = "config"
        elif self.use_gpu:
            # GPU: only one builder at a time to avoid OOM (each builder allocates large GPU buffers)
            cap = 1
            cap_reason = "GPU (single worker to avoid CUDA OOM)"
        elif self.ctx == "CHH":
            cap = 2
            cap_reason = "default for CHH"

        if cap is not None:
            actual_batch_size = min(actual_batch_size, cap)

        cap_note = ""
        if cap is not None and actual_batch_size < uncapped_batch_size:
            cap_note = f", capped at {cap}"
            if cap_reason == "default for CHH":
                cap_note += " (default for CHH)"
            elif cap_reason and "GPU" in cap_reason:
                cap_note += " (GPU)"

        self.logger.info(
            f"Using {actual_batch_size} parallel workers "
            f"(memory: {available_memory_gb:.1f}GB available, "
            f"context: {self.ctx}, multiplier: {context_multiplier:.1f}{cap_note})"
        )

        total_samples = len(all_samples)
        progress_lock = threading.Lock()
        # Load samples in parallel batches; executor is explicitly shut down in finally so all threads close
        executor = ThreadPoolExecutor(max_workers=actual_batch_size)
        try:
            with tqdm(
                total=total_samples,
                desc="Adding samples",
                unit="sample",
            ) as progress_bar:
                # Submit all sample loading tasks
                future_to_sample = {}
                for i, sample_path in enumerate(all_samples):
                    future = executor.submit(load_sample_data, sample_path)
                    future_to_sample[future] = (i, sample_path)

                # Process completed tasks and add samples to position aligner
                for future in as_completed(future_to_sample):
                    sample_idx, sample_path = future_to_sample[future]
                    methyl_sample = None
                    builder = None
                    try:
                        result = future.result()

                        if result is None:
                            continue

                        pos, mC, uC, tnc = result

                        # Skip empty samples
                        if len(pos) == 0:
                            # Skip debug logging during tqdm operations to avoid progress bar interference
                            continue

                        # Create MethylSample-like object for position aligner
                        # Add sample using new method
                        if self._centroid is None:
                            # Smaller initial chunk on GPU to avoid OOM (builder will grow as needed)
                            initial_chunk = 10_000_000 if self.use_gpu else 50_000_000
                            use_gpu_builder = self.use_gpu
                            last_error = None
                            for attempt in range(2):
                                try:
                                    builder = self._new_centroid_builder(
                                        self._min_coverage,
                                        use_gpu_builder,
                                        binned_stats_bins=getattr(self, "binned_stats_bins", 20),
                                        chunk_size=initial_chunk,
                                    )
                                    builder.add_sample(sample_path)
                                    self._centroid = builder.finalize(log_finalize=False)
                                    self.logger.info(
                                        "Initial centroid built from first sample; merging remaining samples"
                                    )
                                    break
                                except (RuntimeError, MemoryError) as e:
                                    last_error = e
                                    err_msg = str(e).lower()
                                    if (
                                        attempt == 0
                                        and use_gpu_builder
                                        and (
                                            "out of memory" in err_msg
                                            or "out_of_memory" in err_msg
                                            or "memoryallocation" in err_msg
                                            or "cuda error" in err_msg
                                            or "bad_alloc" in err_msg
                                        )
                                    ):
                                        self.logger.warning(
                                            "GPU allocation failed (%s), retrying with CPU for this centroid: %s",
                                            type(e).__name__,
                                            str(e)[:200],
                                        )
                                        use_gpu_builder = False
                                        initial_chunk = 50_000_000
                                        if builder is not None:
                                            try:
                                                builder.release_gpu()
                                            except Exception:
                                                pass
                                            try:
                                                del builder
                                            except Exception:
                                                pass
                                            builder = None
                                    else:
                                        raise
                            else:
                                if last_error is not None:
                                    raise last_error
                            # Apply min_samples filter after builder finalizes
                            if hasattr(self._centroid, "N") and len(self._centroid) > 0:
                                N_vals = (
                                    np.asarray(self._centroid.N.values)
                                    if hasattr(self._centroid.N, "values")
                                    else np.asarray(self._centroid.N)
                                )
                                valid_mask = N_vals >= self.min_samples
                                if not valid_mask.all():
                                    # Filter out positions with N < min_samples
                                    # Use integer indices instead of boolean mask to avoid pandas indexing issues
                                    valid_indices = np.where(valid_mask)[0]
                                    if len(valid_indices) > 0:
                                        self._centroid = self._centroid.apply_mask(
                                            valid_indices
                                        )
                                    else:
                                        # No valid positions, create empty centroid
                                        import pandas as pd

                                        empty_df = pd.DataFrame(
                                            {
                                                "pos": [],
                                                "tnc": [],
                                                "N": [],
                                                "Sx": [],
                                                "Sx2": [],
                                                "Sm": [],
                                                "Su": [],
                                                "Sc2": [],
                                                "Swx2": [],
                                            }
                                        )
                                        self._centroid = MethylCentroidData(
                                            empty_df, self._centroid.metadata
                                        )
                        else:
                            # Load the actual MethylSample and add it
                            methyl_sample = self.load_sample(sample_path)
                            self._centroid = self._centroid.add_sample(
                                methyl_sample,
                                residualize_apply=self._get_residualize_apply(),
                                sample_path=str(sample_path),
                            )
                        success = True

                        if success:
                            # Mark as active sample
                            is_new_sample = sample_idx >= len(self.samples)
                            actual_sample_idx = (
                                sample_idx - len(self.samples)
                                if is_new_sample
                                else sample_idx
                            )
                            sample_id = (is_new_sample, actual_sample_idx)
                            self.active_samples.add(sample_id)
                            with progress_lock:
                                progress_bar.update(1)
                                progress_bar.set_postfix_str(sample_path.name, refresh=True)
                        else:
                            self.logger.warning(
                                f"Failed to add sample {sample_path.name} to position aligner"
                            )

                    except Exception as e:
                        self.logger.error(
                            f"Failed to process sample {sample_path.name}: {e}"
                        )
                        self.logger.exception("Full traceback:")
                        continue
                    finally:
                        if methyl_sample is not None:
                            try:
                                methyl_sample.close()
                            except Exception as e:
                                self.logger.debug(f"Sample cleanup failed: {e}")
                        if builder is not None:
                            try:
                                builder.release_gpu()
                            except Exception as e:
                                self.logger.debug("Builder GPU release failed: %s", e)
                            del builder
                        self._cleanup_gpu_after_sample()

        finally:
            executor.shutdown(wait=True)

        self.logger.info(
            f"Parallel sample addition completed: {len(self.active_samples)} samples added"
        )

    def _calculate_chunked_processor_params(
        self, use_gpu: bool
    ) -> Dict[str, Union[int, float]]:
        """
        Dynamically calculate optimal ChunkedGenomicProcessor parameters based on available resources.
        Uses latest MethylUtils GPU optimizations for maximum performance on GH200.

        Args:
            use_gpu: Whether GPU acceleration is enabled

        Returns:
            Dictionary with chunk_size_positions, max_workers, and memory_limit_gb
        """
        from methyl_utils import get_memory_usage, is_gpu_available

        # Get current memory usage from MethylUtils
        memory_info = get_memory_usage()

        # Extract available memory (accounting for current usage)
        system_memory_total_gb = self.memory_manager.system_memory_limit_gb
        system_memory_used_gb = memory_info.get("system_memory_mb", 0) / 1024
        system_memory_available_gb = system_memory_total_gb - system_memory_used_gb

        # GPU memory information
        gpu_available_gb = 0.0
        gpu_free_gb = 0.0
        if use_gpu and is_gpu_available():
            gpu_available_gb = memory_info.get("gpu_memory_gb", 0)
            gpu_free_gb = memory_info.get("gpu_free_gb", 0)

        self.logger.debug(
            f"Memory status - System: {system_memory_available_gb:.1f}GB available "
            f"({system_memory_total_gb:.1f}GB total), "
            f"GPU: {gpu_free_gb:.1f}GB free ({gpu_available_gb:.1f}GB used)"
        )

        if use_gpu and gpu_free_gb > 0.0:
            free_bytes = self._get_gpu_free_bytes()
            sample_count_estimate = max(1, len(self.samples) + len(self.add_samples))
            bin_counter_bytes = 2 if sample_count_estimate < 60000 else 4
            # Chunked centroid accumulation footprint per position:
            # Sm,Su,Sc2,N(uint32)=16, Swx2,Sx,Sx2(float32)=12, positions(uint32)=4,
            # bin_counts(bins*uint16/uint32)=bins*bin_counter_bytes.
            bytes_per_position = 32 + (self.binned_stats_bins * bin_counter_bytes)
            reserve_bytes = 512 * 1024**2
            target_bytes = int(free_bytes * 0.98)
            usable_bytes = max(0, target_bytes - reserve_bytes)
            chunk_size_positions = usable_bytes // max(1, bytes_per_position)
            chunk_size_positions = max(
                1_000_000, min(int(chunk_size_positions), 500_000_000)
            )
            memory_limit_gb = max(
                10.0,
                min(
                    system_memory_available_gb * 0.9,
                    self.memory_manager.system_memory_limit_gb,
                ),
            )
            max_workers = 1

            self.logger.info(
                "Using VRAM-derived GPU chunking: free_vram=%.2fGB, bytes/pos=%s, chunk_size=%s",
                gpu_free_gb,
                bytes_per_position,
                f"{chunk_size_positions:,}",
            )
        else:
            # Fallback to memory-optimized chunking for CPU or limited GPU
            # Dynamic chunk size calculation based on context and available memory
            context_base_chunks = {
                "CG": 50_000_000,  # 50M positions - leverage MethylUtils memory efficiency
                "CHG": 25_000_000,  # 25M positions - medium density
                "CHH": 10_000_000,  # 10M positions - high density, moderate chunks
            }

            base_chunk_size = context_base_chunks.get(
                self.ctx, 20_000_000
            )  # Default 20M

            # Memory-based chunk size adjustment using MethylUtils memory calculations
            memory_limit_gb = (
                system_memory_available_gb * 0.6
            )  # Reserve 40% for other operations

            # Calculate optimal chunk size based on memory per position estimates
            # Use estimated bytes per position from loaded samples if available, otherwise conservative default
            if (
                hasattr(self, "sample_cache")
                and self.sample_cache._estimated_bytes_per_sample is not None
            ):
                memory_per_position_kb = (
                    self.sample_cache._estimated_bytes_per_sample / 1024
                )  # Convert bytes to KB
            else:
                memory_per_position_kb = (
                    0.15  # Conservative default estimate including overhead
                )

            max_positions_by_memory = int(
                (memory_limit_gb * 1024 * 1024) / memory_per_position_kb
            )

            chunk_size_positions = min(base_chunk_size, max_positions_by_memory)
            max_workers = max(
                1, int(system_memory_available_gb / 50)
            )  # 1 worker per 50GB RAM

        # Ensure reasonable minimums and maximums
        chunk_size_positions = max(
            1_000_000, min(chunk_size_positions, 500_000_000)
        )  # 1M to 500M
        memory_limit_gb = max(
            10.0, min(memory_limit_gb, system_memory_available_gb * 0.9)
        )
        max_workers = max(1, min(max_workers, 8))  # 1-8 workers

        return {
            "chunk_size_positions": chunk_size_positions,
            "max_workers": max_workers,
            "memory_limit_gb": memory_limit_gb,
        }

    def _load_sample_for_alignment_memory_aware(
        self, sample_path: Path, sample_idx: int
    ) -> Optional["MethylSample"]:
        """
        Load a sample for alignment with memory-aware caching.

        Args:
            sample_path: Path to the sample file
            sample_idx: Index of the sample for tracking

        Returns:
            MethylSample instance or None if loading fails
        """
        try:
            # Check memory before loading
            memory_info = self.memory_manager.get_memory_usage()
            available_gb = memory_info.get("available_gb", 350)

            # If memory is getting low, clear cache to free up space
            if available_gb < 50:  # Less than 50GB available
                self.logger.debug(
                    f"Low memory detected ({available_gb:.1f}GB), clearing cache"
                )
                self._clear_sample_cache()

            # Load sample with memory mapping for large files
            sample = self.load_sample(sample_path, memory_map=True)

            if sample is None or len(sample.pos) == 0:
                self.logger.warning(f"Sample {sample_path.name} has no valid data")
                return None

            # Filter positions with coverage to reduce memory usage
            coverage = sample.get_coverage()
            valid_mask = coverage > 0

            if np.any(valid_mask):
                # Create filtered sample
                filtered_sample = sample.create_aligned_sample(valid_mask.astype(bool))
                sample = filtered_sample

                # Log sample statistics for first few samples (only in verbose mode to avoid tqdm interference)
                if sample_idx < 5 and self.verbose:
                    self.logger.debug(
                        f"Sample {sample_idx}: {len(sample.pos):,} positions, "
                        f"coverage: {coverage[valid_mask].mean():.1f}"
                    )

                return sample
            else:
                self.logger.warning(
                    f"Sample {sample_path.name} has no positions with coverage"
                )
                return None

        except Exception as e:
            self.logger.error(f"Failed to load sample {sample_path}: {e}")
            return None

    @property
    def cache_enabled(self) -> bool:
        return self._cache_enabled

    @cache_enabled.setter
    def cache_enabled(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise TypeError("cache_enabled must be a boolean")

        self._cache_enabled = value

        if not value:
            # Clear cache when disabling
            self._clear_sample_cache()
            self.logger.info("Sample caching disabled")
        else:
            self.logger.info("Sample caching enabled")

    def _load_sample_for_alignment_memory_aware(
        self, sample_path: Path, sample_idx: int
    ) -> Optional["MethylSample"]:
        """
        Load a sample for alignment with memory-aware caching.

        Args:
            sample_path: Path to the sample file
            sample_idx: Index of the sample for tracking

        Returns:
            MethylSample instance or None if loading fails
        """
        try:
            # Check memory before loading
            memory_info = self.memory_manager.get_memory_usage()
            available_gb = memory_info.get("available_gb", 350)

            # If memory is getting low, clear cache to free up space
            if available_gb < 50:  # Less than 50GB available
                self.logger.debug(
                    f"Low memory detected ({available_gb:.1f}GB), clearing cache"
                )
                self._clear_sample_cache()

            # Load sample with memory mapping for large files
            sample = self.load_sample(sample_path, memory_map=True)

            if sample is None or len(sample.pos) == 0:
                self.logger.warning(f"Sample {sample_path.name} has no valid data")
                return None

            # Filter positions with coverage to reduce memory usage
            coverage = sample.get_coverage()
            valid_mask = coverage > 0

            if np.any(valid_mask):
                # Create filtered sample
                filtered_sample = sample.create_aligned_sample(valid_mask.astype(bool))
                sample = filtered_sample

                # Log sample statistics for first few samples (only in verbose mode to avoid tqdm interference)
                if sample_idx < 5 and self.verbose:
                    self.logger.debug(
                        f"Sample {sample_idx}: {len(sample.pos):,} positions, "
                        f"coverage: {coverage[valid_mask].mean():.1f}"
                    )

                return sample
            else:
                self.logger.warning(
                    f"Sample {sample_path.name} has no positions with coverage"
                )
                return None

        except Exception as e:
            self.logger.error(f"Failed to load sample {sample_path}: {e}")
            return None

    def _monitor_memory_during_loading(self):
        """Monitor memory usage during sample loading and take corrective actions."""
        memory_info = self.memory_manager.get_memory_usage()
        used_percent = memory_info.get("percent_used", 0)

        # If memory usage is too high, clear cache
        if used_percent > 85:
            self.logger.warning(
                f"High memory usage detected ({used_percent:.1f}%), clearing cache"
            )
            self._clear_sample_cache()

        # Force garbage collection periodically
        import gc

        collected = gc.collect()
        if collected > 0:
            self.logger.debug(f"Garbage collection freed {collected} objects")

    def compute_centroid(self, extended: bool = False):
        if self._centroid is None or len(self._centroid) == 0:
            print("No samples added")
            return np.array([], dtype=get_methyl_dtype(extended))

        if extended:
            # Get centroid as MethylCentroidData
            centroid_sample = self._centroid

            # Convert to numpy array format for saving
            centroid_data = centroid_sample.to_numpy(extended=True)
            return centroid_data
        else:
            # Get centroid and convert to basic format
            centroid_sample = self._centroid

            # Convert to numpy array format for saving (basic format)
            centroid_data = centroid_sample.to_numpy(extended=False)
            return centroid_data

    def save_centroid(
        self, output_dir: str, centroid_data=None, extended: bool = False
    ) -> Path:
        if centroid_data is None:
            centroid_data = self.compute_centroid(extended=extended)

        if centroid_data is None or len(centroid_data) == 0:
            print("No valid centroid data to save")
            return None

        # Get active sample paths (those currently in the centroid)
        active_sample_paths = self._get_active_sample_paths()
        samples_basenames = [Path(p).name for p in active_sample_paths]
        common_parent = _common_parent_directory(active_sample_paths)

        # Prepare metadata for H5 file
        from datetime import datetime

        metadata = {
            "laboratory": self.laboratory,
            "disease": self.disease,
            "group": self.group,
            "batch": self.batch,
            "chromosome": self.chrom,
            "context": self.ctx,
            "samples_used": samples_basenames,
            "creation_date": datetime.now().isoformat(),  # NEW: Creation timestamp
            "min_coverage": self.min_coverage,
        }
        if common_parent is not None:
            metadata["samples_base_path"] = str(common_parent)
        if self._binned_stats is None:
            raise ValueError(
                "ECDF centroids require binned_stats before saving. "
                "Build with binned_stats_bins >= 1."
            )
        metadata["binned_stats_enabled"] = True
        metadata["binned_stats_bins"] = int(self.binned_stats_bins)

        # Create MethylSample from centroid data with metadata
        # centroid_data is a structured numpy array, convert to DataFrame
        import pandas as pd

        # Convert structured array to DataFrame
        if isinstance(centroid_data, np.ndarray) and centroid_data.dtype.names:
            # Structured array - convert field by field
            if len(centroid_data) > 0:
                df = pd.DataFrame(
                    {name: centroid_data[name] for name in centroid_data.dtype.names}
                )
            else:
                # Empty structured array - create empty DataFrame with expected columns
                df = pd.DataFrame({name: [] for name in centroid_data.dtype.names})
        else:
            # Already a DataFrame or regular array
            df = (
                pd.DataFrame(centroid_data)
                if not isinstance(centroid_data, pd.DataFrame)
                else centroid_data
            )

        # Single centroid type: MethylCentroidData when full schema (Sm, Su, Sc2, Swx2) present
        if "Sm" in df.columns and "Su" in df.columns and "Sc2" in df.columns and "Swx2" in df.columns:
            methyl_sample = MethylCentroidData(df, metadata=metadata)
        else:
            methyl_sample = MethylSample(df, metadata=metadata)

        # Set metadata on the sample before saving
        methyl_sample.metadata = metadata

        # Attach binned stats if available
        try:
            methyl_sample.set_binned_stats(
                self._binned_stats["bin_edges"], self._binned_stats["bin_counts"]
            )
        except Exception as e:
            raise ValueError(f"Failed to attach required ECDF binned stats: {e}") from e

        # Save using MethylSample
        output_path = Path(output_dir)

        # Ensure output directory exists with robust error handling
        try:
            # First ensure parent directories exist
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Then create the target directory
            output_path.mkdir(exist_ok=True)
            # Verify directory was actually created and is writable
            if not output_path.exists():
                raise OSError(f"Failed to create output directory: {output_path}")
            # Note: os.access may not work correctly in container environments,
            # so we'll rely on the mkdir success and existence check
        except PermissionError as e:
            # Provide more specific error for permission issues
            raise OSError(
                f"Permission denied creating output directory {output_path}. "
                f"Please ensure the parent directory is writable: {output_path.parent}. "
                f"Original error: {e}"
            )
        except Exception as e:
            raise OSError(f"Cannot create output directory {output_path}: {e}")

        filename = f"{self.chrom}-{self.ctx}.h5"
        centroid_path = output_path / filename

        # Save centroid (metadata already embedded in MethylSample)
        # Metadata is already set on methyl_sample.metadata, just save
        methyl_sample.save_to_h5(centroid_path, compressed=True)

        # Store reference to centroid
        self.centroid = centroid_path

        return self.centroid

    def load_centroid(self, centroid_path: Union[str, Path]) -> "MethylSample":
        from methyl_utils import MethylSample

        methyl_sample = MethylSample.load_from_h5(centroid_path)

        # Ensure arrays are NumPy arrays
        # Use to_cpu() method instead of manual .get() calls
        return methyl_sample.to_cpu()

    def get_centroid(
        self,
        positions: Optional[np.ndarray] = None,
        indices: Optional[np.ndarray] = None,
    ):
        """
        Return the centroid, loading from the centroid H5 with slicing when not cached.

        Once the centroid is built, its data is read from the centroid H5 file (using
        position or row-index slicing). The samples list is only used to know which
        samples were added to the centroid; it is not used to load centroid data.

        Args:
            positions: If set, load only rows for these genomic positions (H5 pos index).
            indices: If set, load only these row indices from the H5 file.

        Returns:
            MethylCentroidData (or MethylSample) instance, or None if no centroid.
        """
        path = getattr(self, "centroid_path", None) or getattr(self, "centroid", None)
        if path is not None:
            path = Path(path)
        if path is None or not path.exists():
            return self._centroid if (positions is None and indices is None) else None

        if positions is not None or indices is not None:
            loaded = MethylSample.load_from_h5(
                path,
                positions=positions,
                indices=indices,
                align_positions=False,
            )
            return loaded.to_cpu() if hasattr(loaded, "to_cpu") else loaded

        if self._centroid is not None:
            return self._centroid
        loaded = MethylSample.load_from_h5(path)
        return loaded.to_cpu() if hasattr(loaded, "to_cpu") else loaded

    def load_sample(
        self, sample_path: Union[str, Path], memory_map: bool = True
    ) -> "MethylSample":
        """
        Load sample with optimized memory management.

        Args:
            sample_path: Path to the sample file
            memory_map: Whether to use memory mapping for large files

        Returns:
            MethylSample instance
        """
        from methyl_utils import MethylSample

        # Use memory mapping for large files to reduce memory footprint
        sample_path_obj = Path(sample_path)
        file_size_gb = sample_path_obj.stat().st_size / (1024**3)

        # Use memory mapping for files > 1GB or when explicitly requested
        use_memory_map = memory_map and file_size_gb > 1.0

        try:
            # Load sample
            methyl_sample = MethylSample.load_from_h5(sample_path)
            if use_memory_map:
                self.logger.debug(f"Loaded {sample_path} ({file_size_gb:.1f}GB)")

            # Convert from CuPy to NumPy if needed (MethylSample structure is trusted)
            methyl_sample = self._ensure_numpy_arrays(methyl_sample)

            # Optionally cap per-CpG coverage (binomial thinning) to correct high-coverage outliers
            if self._cap_coverage and self._cap_coverage_n_cap is not None:
                methyl_sample = methyl_sample.cap_coverage_binomial(
                    self._cap_coverage_n_cap,
                    seed=self._cap_coverage_seed,
                )
                self.logger.debug(
                    "Applied coverage cap n_cap=%s to sample %s",
                    self._cap_coverage_n_cap,
                    getattr(sample_path, "name", sample_path),
                )

            # Pre-compute and cache statistical properties if this is a centroid
            if methyl_sample.is_centroid and len(methyl_sample.pos) > 1000:
                # Cache statistical properties to avoid recomputation
                _ = methyl_sample.mean  # Trigger computation and caching
                _ = methyl_sample.variance
                _ = methyl_sample.precision

            return methyl_sample

        except Exception as e:
            self.logger.error(f"Failed to load sample {sample_path}: {e}")
            raise

    def _ensure_numpy_arrays(self, methyl_sample: "MethylSample") -> "MethylSample":
        """
        Convert MethylSample arrays from CuPy to NumPy if needed.

        MethylSample properties return Series (pandas/cudf), so we use to_cpu()
        to handle both GPU->CPU conversion and Series->numpy array conversion.

        Args:
            methyl_sample: MethylSample instance

        Returns:
            MethylSample with NumPy arrays (converted from CuPy if needed)
        """
        # Use to_cpu() method which handles both GPU->CPU and Series->numpy conversion
        return methyl_sample.to_cpu()

    def _cleanup_gpu_after_sample(self) -> None:
        if self.use_gpu:
            cleanup_gpu_memory()

    def _get_gpu_free_bytes(self) -> int:
        """Return currently available GPU bytes (0 when unavailable)."""
        if not self.use_gpu:
            return 0
        try:
            import cupy as cp

            free_bytes, _total_bytes = cp.cuda.runtime.memGetInfo()
            return int(free_bytes)
        except Exception:
            memory_info = self.memory_manager.get_memory_usage()
            gpu_free_gb = float(memory_info.get("gpu_free_gb", 0.0) or 0.0)
            return int(gpu_free_gb * (1024**3))

    def _derive_streaming_gpu_chunk_size(
        self,
        bins: int,
        utilization_fraction: float = 0.98,
        reserve_bytes: int = 256 * 1024**2,
    ) -> int:
        """
        Compute streaming builder chunk size from real-time free GPU memory.

        Per-position GPU footprint in `MethylCentroidBuilder`:
        pos(4) + mC_sum(4) + uC_sum(4) + Sc2(4) + Swx2(4) + N(4) + Sx(4) + Sx2(4) + tnc(1) + bin_counts(4*bins)
        """
        free_bytes = self._get_gpu_free_bytes()
        if free_bytes <= 0:
            return 1_000_000

        bytes_per_position = 33 + (4 * int(bins))
        target_bytes = int(free_bytes * float(utilization_fraction))
        usable_bytes = max(0, target_bytes - int(reserve_bytes))
        if usable_bytes <= 0:
            return 1_000_000

        chunk = usable_bytes // max(1, bytes_per_position)
        return int(max(1_000_000, min(chunk, 500_000_000)))

    def _get_residualize_apply(self):
        """Load the frozen M-value applier once per chrom×ctx; None = shipped-pack path."""
        if not self.residualize_coef_dir:
            return None
        if self._residualize_apply_fn is not None:
            return self._residualize_apply_fn
        from methyl_utils.mvalue_residualize import sample_id_from_path
        from methyl_utils.residualize_runtime import load_applier

        applier = load_applier(self.residualize_coef_dir, self.chrom, self.ctx)
        if applier is None:
            return None

        def _apply(sample_path, pos, mean):
            return applier.apply_for_sample(sample_id_from_path(sample_path), pos, mean)

        self._residualize_apply_fn = _apply
        return _apply

    def _new_centroid_builder(self, *args, **kwargs):
        kwargs.setdefault("gpu_backend", getattr(self, "gpu_backend", None))
        builder = _create_centroid_builder(*args, **kwargs)
        fn = self._get_residualize_apply()
        if fn is not None:
            builder.residualize_apply = fn
        return builder

    def _create_streaming_builder(self):
        """
        Create a streaming centroid builder with OOM-aware GPU fallback.

        Builder initialization allocates large GPU buffers up front. When CUDA
        memory is fragmented this can fail before per-sample fallback logic runs.
        """
        bins = int(getattr(self, "binned_stats_bins", 20))

        if getattr(self, "gpu_backend", None) == "mojo":
            chunk_size = {
                "CG": 50_000_000,
                "CHG": 25_000_000,
                "CHH": 10_000_000,
            }.get(self.ctx, 50_000_000)
            self.logger.info(
                "Initialized Mojo streaming builder with chunk_size=%s for %s-%s",
                f"{chunk_size:,}",
                self.chrom,
                self.ctx,
            )
            return self._new_centroid_builder(
                self._min_coverage,
                self.use_gpu,
                binned_stats_bins=bins,
                chunk_size=chunk_size,
            )

        if self.use_gpu:
            primary_chunk = self._derive_streaming_gpu_chunk_size(bins)
            gpu_chunk_candidates = []
            for scale in (1.0, 0.75, 0.5, 0.25):
                candidate = int(max(1_000_000, primary_chunk * scale))
                if candidate not in gpu_chunk_candidates:
                    gpu_chunk_candidates.append(candidate)
            for chunk_size in gpu_chunk_candidates:
                try:
                    builder = self._new_centroid_builder(
                        self._min_coverage,
                        True,
                        binned_stats_bins=bins,
                        chunk_size=chunk_size,
                    )
                    self.logger.info(
                        "Initialized GPU streaming builder with chunk_size=%s (free_vram=%.2fGB) for %s-%s",
                        f"{chunk_size:,}",
                        self._get_gpu_free_bytes() / (1024**3),
                        self.chrom,
                        self.ctx,
                    )
                    return builder
                except (RuntimeError, MemoryError) as e:
                    if _is_gpu_oom_error(e):
                        self.logger.warning(
                            "GPU builder init OOM at chunk_size=%s for %s-%s (%s). Retrying...",
                            f"{chunk_size:,}",
                            self.chrom,
                            self.ctx,
                            str(e)[:200],
                        )
                        self._cleanup_gpu_after_sample()
                        continue
                    raise

            self.logger.warning(
                "GPU builder initialization failed for %s-%s after reduced chunk retries; "
                "falling back to CPU streaming builder.",
                self.chrom,
                self.ctx,
            )

        return self._new_centroid_builder(
            self._min_coverage,
            False,
            binned_stats_bins=bins,
            chunk_size=50_000_000,
        )

    def _sample_id_for_index(self, sample_idx: int) -> Tuple[bool, int]:
        """Map a flat sample index back to the active_samples identifier."""
        is_new_sample = sample_idx >= len(self.samples)
        actual_sample_idx = (
            sample_idx - len(self.samples)
            if is_new_sample
            else sample_idx
        )
        return (is_new_sample, actual_sample_idx)

    def _rebuild_streaming_builder_on_cpu(
        self,
        sample_paths: List[Path],
        upto_index: int,
    ):
        """
        Rebuild the in-flight streaming centroid on CPU from the already-seen samples.

        This is used when a GPU builder hits a CUDA OOM. Replaying the successful
        prefix keeps the centroid correct instead of silently dropping samples after
        the failure.
        """
        cpu_builder = self._new_centroid_builder(
            self._min_coverage,
            False,
            binned_stats_bins=getattr(self, "binned_stats_bins", 20),
            chunk_size=50_000_000,
        )
        rebuilt_indices: List[int] = []

        for replay_idx, replay_path in enumerate(sample_paths[:upto_index]):
            if not replay_path.is_file():
                continue
            try:
                cpu_builder.add_sample(replay_path)
                rebuilt_indices.append(replay_idx)
            except Exception as replay_error:
                self.logger.warning(
                    "CPU fallback replay skipped sample %s: %s",
                    replay_path.name,
                    replay_error,
                )
            finally:
                self._cleanup_gpu_after_sample()

        self.logger.info(
            "Rebuilt centroid builder on CPU from %d prior sample(s) for %s-%s",
            len(rebuilt_indices),
            self.chrom,
            self.ctx,
        )
        return cpu_builder, rebuilt_indices

    def calculate_centroid(self, output_dir: str, extended: bool = False) -> Path:
        effective_sample_dirs = self._apply_effective_sample_set()
        self._centroid = None
        self._binned_stats = None
        self.active_samples.clear()
        print(
            f"Adding {len(effective_sample_dirs)} samples for {self.chrom}-{self.ctx}"
        )

        # Ensure output directory exists before processing
        output_path = Path(output_dir)
        try:
            # First ensure parent directories exist
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Then create the target directory
            output_path.mkdir(exist_ok=True)
            # Verify directory was actually created
            if not output_path.exists():
                raise OSError(f"Failed to create output directory: {output_path}")
            # Note: os.access may not work correctly in container environments
        except PermissionError as e:
            raise OSError(
                f"Permission denied creating output directory {output_path}. "
                f"Please ensure the parent directory is writable: {output_path.parent}. "
                f"Original error: {e}"
            )
        except Exception as e:
            raise OSError(f"Cannot create output directory {output_path}: {e}")

        # Add all samples in parallel
        missing_samples = self._filter_missing_sample_files()
        if not self.samples and not self.add_samples:
            expected_suffix = f"{self.chrom}-{self.ctx}.h5"
            if missing_samples:
                preview = ", ".join(str(p) for p in missing_samples[:5])
                suffix = " ..." if len(missing_samples) > 5 else ""
                raise FileNotFoundError(
                    "No valid sample files found. "
                    f"Expected files like {expected_suffix}. "
                    f"Missing examples: {preview}{suffix}"
                )
            raise FileNotFoundError(
                f"No valid sample files found. Expected files like {expected_suffix}."
            )

        # Auto-estimate n_cap from first sample (IQR: Q3 + 1.5*IQR on sampled positions)
        if (
            self._cap_coverage_auto_n_cap
            and getattr(self, "_cap_coverage_n_cap", None) is None
        ):
            first_path = self.samples[0] if self.samples else self.add_samples[0]
            try:
                from methyl_utils.core.io import estimate_n_cap_from_sample_path_with_log
                median, upper_fence, n_cap = estimate_n_cap_from_sample_path_with_log(
                    first_path,
                    max_positions=self._cap_coverage_n_cap_max_positions,
                    iqr_multiplier=self._cap_coverage_n_cap_iqr_multiplier,
                    seed=self._cap_coverage_seed,
                )
                self._cap_coverage_n_cap = n_cap
                self._cap_coverage = True
                self.logger.info(
                    "Estimated cap_coverage_n_cap=%s from IQR on sampled coverage (median=%.1f, upper_fence=%.1f, %s positions) on %s",
                    n_cap,
                    median,
                    upper_fence,
                    self._cap_coverage_n_cap_max_positions,
                    getattr(first_path, "name", first_path),
                )
            except Exception as e:
                self.logger.warning(
                    "Failed to auto-estimate n_cap from %s: %s; coverage capping disabled.",
                    getattr(first_path, "name", first_path),
                    e,
                )

        self.add_samples_parallel()

        # Check if any samples were successfully added
        if len(self.active_samples) == 0:
            raise RuntimeError(
                "Failed to compute centroid: no samples were successfully added"
            )

        # Compute and save centroid (chunk size from memory-derived params in chunked_processor).
        # When we already have a full centroid with binned_stats from add_samples_parallel (builder
        # + add_sample path), skip the redundant chunked recompute that re-reads all sample files.
        use_existing = (
            self._centroid is not None
            and isinstance(self._centroid, MethylCentroidData)
            and getattr(self._centroid, "binned_stats", None) is not None
        )
        if use_existing:
            self.logger.info(
                "Using centroid built during sample addition (skipping chunked recompute)"
            )
            centroid_cpu = self._centroid.to_cpu()
            centroid = centroid_cpu.df
            self._binned_stats = self._centroid.binned_stats
        else:
            centroid = self.compute_centroid_chunked(
                extended=extended,
                chunk_size_positions=self.chunked_processor.chunk_size_positions,
            )
        if centroid is None or len(centroid) == 0:
            raise RuntimeError(
                "Failed to compute centroid: no samples were successfully added"
            )

        centroid_path = self.save_centroid(output_dir, centroid, extended=extended)
        if centroid_path is None:
            raise RuntimeError("Failed to save centroid: no valid data to save")

        return centroid_path

    def compute_centroid_chunked(
        self, extended: bool = False, chunk_size_positions: Optional[int] = None
    ) -> Optional[np.ndarray]:
        """
        Compute centroid using chunked processing for memory efficiency.

        When the centroid is already built and saved, loads from the centroid H5
        using position-based slicing (H5 pos index); samples are not re-read.
        Samples are only used to know which samples were added to the centroid.

        This method processes the genome in chunks to handle very large datasets
        that exceed available memory. Chunk size is determined by MethylUtils
        memory management (GPU when available, else system RAM via get_memory_usage).

        Args:
            extended: Whether to compute extended centroid with statistics
            chunk_size_positions: Number of positions per chunk; if None, uses
                the instance's memory-derived value from chunked_processor.

        Returns:
            Centroid data as numpy array, or None if computation fails
        """
        if chunk_size_positions is None:
            chunk_size_positions = self.chunked_processor.chunk_size_positions
        with self.performance_profiler.profile_operation(
            "chunked_centroid_computation"
        ):
            memory_manager = get_memory_manager()
            path = getattr(self, "centroid_path", None)
            if path is not None:
                path = Path(path)

            if path is not None and path.exists():
                self.logger.info(
                    "Centroid already built: loading from H5 in chunks (using H5 pos index)"
                )
                from methyl_utils import load_pos_from_h5

                pos_arr = load_pos_from_h5(path)
                sorted_idx = np.argsort(pos_arr)
                sorted_positions = np.asarray(pos_arr[sorted_idx], dtype=np.uint32)
                total_positions = len(sorted_positions)
                if total_positions == 0:
                    return None
                total_chunks = (
                    total_positions + chunk_size_positions - 1
                ) // chunk_size_positions
                chunk_results = []
                chunk_bin_counts = []
                for chunk_idx in tqdm(range(total_chunks), desc="Loading chunks from H5"):
                    start_pos = chunk_idx * chunk_size_positions
                    end_pos = min(start_pos + chunk_size_positions, total_positions)
                    chunk_positions = sorted_positions[start_pos:end_pos]
                    idx = MethylSample.position_indices_from_h5(
                        path,
                        chunk_positions,
                        pos_cache=pos_arr,
                    )
                    if len(idx) == 0:
                        continue
                    loaded = MethylSample.load_from_h5(path, indices=idx, align_positions=False)
                    if loaded is None or len(loaded) == 0:
                        continue
                    arr = loaded.to_numpy(extended=extended) if hasattr(loaded, "to_numpy") else None
                    if arr is None:
                        df = getattr(loaded, "_df", None)
                        if df is not None:
                            arr = df.to_records(index=False)
                    if arr is not None:
                        chunk_results.append(arr)
                    if self.binned_stats_bins > 0 and hasattr(loaded, "binned_stats") and getattr(loaded, "binned_stats", None) is not None:
                        bc = loaded.binned_stats.get("bin_counts")
                        if bc is not None:
                            chunk_bin_counts.append(np.asarray(bc))
                if not chunk_results:
                    return None
                combined = np.concatenate(chunk_results)
                if self.binned_stats_bins > 0 and chunk_bin_counts:
                    combined_bins = np.concatenate(chunk_bin_counts)
                    bin_edges = np.linspace(0.0, 1.0, self.binned_stats_bins + 1, dtype=np.float32)
                    self._binned_stats = {"bin_edges": bin_edges, "bin_counts": combined_bins}
                else:
                    self._binned_stats = None
                sort_idx = np.argsort(combined["pos"])
                combined = combined[sort_idx]
                if self._binned_stats is not None and "bin_counts" in self._binned_stats:
                    self._binned_stats["bin_counts"] = self._binned_stats["bin_counts"][sort_idx]
                return combined

            self.logger.info(
                f"Computing centroid using chunked processing (chunk size: {chunk_size_positions:,} positions)"
            )

            if self._centroid is None or len(self._centroid) == 0:
                self.logger.warning("No samples available for centroid computation")
                return None

            centroid_positions = np.asarray(self._centroid.pos.values, dtype=np.uint32)
            all_positions = set(centroid_positions)
            total_positions = len(all_positions)
            self.logger.info(
                f"Found {total_positions:,} unique positions across all samples"
            )

            if total_positions == 0:
                self.logger.warning("No positions found in samples")
                return None

            sorted_positions = np.array(sorted(all_positions), dtype=np.uint32)

            chunk_results = []
            chunk_bin_counts = []
            total_chunks = (
                total_positions + chunk_size_positions - 1
            ) // chunk_size_positions

            self.logger.info(f"Processing {total_chunks} chunks...")
            pos_cache = {}

            for chunk_idx in tqdm(range(total_chunks), desc="Processing chunks"):
                start_pos = chunk_idx * chunk_size_positions
                end_pos = min(start_pos + chunk_size_positions, total_positions)
                chunk_positions = sorted_positions[start_pos:end_pos]

                with self.performance_profiler.profile_operation(
                    f"chunk_{chunk_idx}_processing"
                ):
                    chunk_centroid = self._compute_centroid_for_positions(
                        chunk_positions, extended, pos_cache=pos_cache
                    )
                    if chunk_centroid is not None:
                        if (self.binned_stats_bins > 0) and isinstance(
                            chunk_centroid, tuple
                        ):
                            centroid_part, bin_counts_part = chunk_centroid
                            if centroid_part is not None:
                                chunk_results.append(centroid_part)
                                chunk_bin_counts.append(bin_counts_part)
                        else:
                            chunk_results.append(chunk_centroid)

                # Release GPU memory after each chunk so it does not accumulate
                memory_manager.force_gpu_cleanup()

            if not chunk_results:
                self.logger.error("No chunks produced valid centroid data")
                return None

            # Combine chunk results
            self.logger.info(f"Combining {len(chunk_results)} chunk results...")
            if (self.binned_stats_bins > 0):
                combined = self._combine_chunked_centroids(
                    chunk_results, extended, bin_counts=chunk_bin_counts
                )
                if isinstance(combined, tuple):
                    final_centroid, combined_bins = combined
                    bin_edges = np.linspace(
                        0.0, 1.0, self.binned_stats_bins + 1, dtype=np.float32
                    )
                    self._binned_stats = {
                        "bin_edges": bin_edges,
                        "bin_counts": combined_bins,
                    }
                else:
                    final_centroid = combined
                    self._binned_stats = None
            else:
                final_centroid = self._combine_chunked_centroids(
                    chunk_results, extended
                )

            # Release chunk and position cache references to avoid holding memory
            chunk_results.clear()
            chunk_bin_counts.clear()
            pos_cache.clear()

            # Ensure GPU cleanup after chunked processing
            memory_manager.force_gpu_cleanup()

            self.logger.info("Chunked centroid computation completed")
            return final_centroid

    def _compute_centroid_for_positions(
        self,
        positions: np.ndarray,
        extended: bool = False,
        pos_cache: Optional[dict] = None,
    ) -> Optional[np.ndarray]:
        """
        Compute centroid for a specific set of positions.

        Args:
            positions: Array of genomic positions
            extended: Whether to compute extended statistics
            pos_cache: Optional dict path -> pos array; when provided, use indexed
                load (load_pos_from_h5 + load_from_h5(..., indices=)) to avoid
                reading each full file per chunk.

        Returns:
            Centroid data for these positions, or None if no data
        """
        from methyl_utils import load_pos_from_h5

        sample_count = len(self.active_samples) if self._centroid is not None else 0
        if sample_count == 0:
            return None

        # Initialize arrays for accumulation (single centroid schema: Sm, Su, Sc2, Swx2, N, Sx, Sx2)
        Sm_accum = np.zeros(len(positions), dtype=np.uint32)
        Su_accum = np.zeros(len(positions), dtype=np.uint32)
        Sc2_accum = np.zeros(len(positions), dtype=np.uint32)
        Swx2_accum = np.zeros(len(positions), dtype=np.float32)
        N_accum = np.zeros(len(positions), dtype=np.uint32)
        Sx_accum = np.zeros(len(positions), dtype=np.float32)
        Sx2_accum = np.zeros(len(positions), dtype=np.float32)

        bin_counts = None
        if (self.binned_stats_bins > 0):
            bin_dtype = np.uint16 if sample_count < 60000 else np.uint32
            bin_counts = np.zeros(
                (len(positions), self.binned_stats_bins), dtype=bin_dtype
            )

        use_indexed_load = pos_cache is not None

        # Process each sample that was actually added (iterate active_samples so indices match;
        # using range(sample_count) would load wrong paths when some samples failed to add)
        for sample_id in sorted(self.active_samples):
            sample_data_obj = None
            try:
                is_new_sample, sample_index = sample_id
                sample_path = (
                    self.add_samples[sample_index]
                    if is_new_sample
                    else self.samples[sample_index]
                )

                if use_indexed_load:
                    # Fast path: get pos from cache (or load once), then load only chunk rows
                    path_key = str(Path(sample_path))
                    if path_key not in pos_cache:
                        pos_cache[path_key] = load_pos_from_h5(sample_path)
                    pos_arr = pos_cache[path_key]
                    idx = MethylSample.position_indices_from_h5(
                        sample_path,
                        positions,
                        pos_cache=pos_arr,
                    )
                    if len(idx) == 0:
                        continue
                    sample_data_obj = MethylSample.load_from_h5(
                        sample_path,
                        indices=idx,
                        align_positions=False,
                    )
                    sample_data_obj = self._ensure_numpy_arrays(sample_data_obj)
                else:
                    sample_data_obj = self.load_sample(sample_path)

                if use_indexed_load:
                    aligned_sample_cpu = sample_data_obj.to_cpu() if hasattr(sample_data_obj, "to_cpu") else sample_data_obj
                    aligned_pos = np.asarray(aligned_sample_cpu.pos.values, dtype=np.uint32)
                    aligned_mC = np.asarray(aligned_sample_cpu.mC.values, dtype=np.uint32)
                    aligned_uC = np.asarray(aligned_sample_cpu.uC.values, dtype=np.uint32)
                else:
                    # Align sample to target positions (returns only common positions, length <= len(positions))
                    aligned_sample = sample_data_obj.align_to_positions(positions)
                    if len(aligned_sample) == 0:
                        continue
                    aligned_sample_cpu = aligned_sample.to_cpu()
                    aligned_pos = np.asarray(aligned_sample_cpu.pos.values, dtype=np.uint32)
                    aligned_mC = np.asarray(aligned_sample_cpu.mC.values, dtype=np.uint32)
                    aligned_uC = np.asarray(aligned_sample_cpu.uC.values, dtype=np.uint32)

                # Map aligned rows back to indices in the full positions array (positions is sorted)
                target_idx = np.searchsorted(positions, aligned_pos, side="left")
                # searchsorted can return len(positions) when value == positions[-1]; clip to valid range
                target_idx = np.minimum(target_idx, len(positions) - 1)
                if np.any(positions[target_idx] != aligned_pos):
                    # Should not happen if align_to_positions returns subset of positions
                    valid_map = positions[target_idx] == aligned_pos
                    target_idx = target_idx[valid_map]
                    aligned_mC = aligned_mC[valid_map]
                    aligned_uC = aligned_uC[valid_map]
                    aligned_pos = aligned_pos[valid_map]
                    if len(target_idx) == 0:
                        continue

                # Accumulate at the correct indices
                coverage = aligned_mC.astype(np.uint32) + aligned_uC.astype(np.uint32)
                np.add.at(Sm_accum, target_idx, aligned_mC)
                np.add.at(Su_accum, target_idx, aligned_uC)
                np.add.at(N_accum, target_idx, (coverage > 0).astype(np.uint32))
                # Sc2 += c_i^2, Swx2 += c_i*x_i^2 = mC^2/c
                np.add.at(Sc2_accum, target_idx, (coverage.astype(np.uint64) ** 2).astype(np.uint32))
                valid_in_aligned = coverage > 0
                swx2_inc = np.zeros(len(aligned_mC), dtype=np.float32)
                swx2_inc[valid_in_aligned] = (
                    (aligned_mC[valid_in_aligned].astype(np.float64) ** 2)
                    / coverage[valid_in_aligned].astype(np.float64)
                )
                np.add.at(Swx2_accum, target_idx, swx2_inc)

                if valid_in_aligned.any():
                    methylation_level = np.zeros(len(aligned_mC), dtype=np.float32)
                    methylation_level[valid_in_aligned] = (
                        aligned_mC[valid_in_aligned].astype(np.float32) / coverage[valid_in_aligned].astype(np.float32)
                    )
                    np.add.at(Sx_accum, target_idx, methylation_level)
                    np.add.at(Sx2_accum, target_idx, methylation_level ** 2)

                    if (self.binned_stats_bins > 0) and bin_counts is not None:
                        bin_idx = np.floor(
                            methylation_level * self.binned_stats_bins
                        ).astype(np.int32)
                        bin_idx = np.clip(bin_idx, 0, self.binned_stats_bins - 1)
                        idxs = np.where(valid_in_aligned)[0]
                        np.add.at(bin_counts, (target_idx[idxs], bin_idx[idxs]), 1)
            finally:
                if sample_data_obj is not None:
                    try:
                        if hasattr(sample_data_obj, "close"):
                            sample_data_obj.close()
                    except Exception as e:
                        self.logger.debug("Sample close failed: %s", e)
                    sample_data_obj = None

        # Filter positions with sufficient coverage (c.coverage >= min_coverage)
        total_coverage = Sm_accum.astype(np.uint64) + Su_accum.astype(np.uint64)
        valid_positions = total_coverage >= self.min_coverage
        valid_positions = valid_positions & (N_accum >= self.min_samples)

        if not valid_positions.any():
            return None

        from methyl_utils import get_methyl_dtype
        dtype = get_methyl_dtype(extended=True)
        centroid_data = np.empty(np.sum(valid_positions), dtype=dtype)
        centroid_data["pos"] = positions[valid_positions]
        centroid_data["tnc"] = np.zeros(np.sum(valid_positions), dtype=np.uint8)
        centroid_data["N"] = N_accum[valid_positions]
        centroid_data["Sx"] = Sx_accum[valid_positions]
        centroid_data["Sx2"] = Sx2_accum[valid_positions]
        centroid_data["Sm"] = Sm_accum[valid_positions]
        centroid_data["Su"] = Su_accum[valid_positions]
        centroid_data["Sc2"] = Sc2_accum[valid_positions]
        centroid_data["Swx2"] = Swx2_accum[valid_positions]

        if (self.binned_stats_bins > 0) and bin_counts is not None:
            bin_counts = bin_counts[valid_positions]
            return centroid_data, bin_counts

        return centroid_data

    def _combine_chunked_centroids(
        self,
        chunk_results: List[np.ndarray],
        extended: bool = False,
        bin_counts: Optional[List[np.ndarray]] = None,
    ) -> Optional[np.ndarray]:
        """
        Combine centroid results from multiple chunks.

        Args:
            chunk_results: List of centroid arrays from chunks
            extended: Whether the centroids are extended

        Returns:
            Combined centroid array
        """
        if not chunk_results:
            return None
        # Concatenate all chunks
        try:
            combined_centroid = np.concatenate(chunk_results)
            combined_bins = None
            if bin_counts is not None and len(bin_counts) == len(chunk_results):
                combined_bins = np.concatenate(bin_counts)

            # Sort by position
            sort_idx = np.argsort(combined_centroid["pos"])
            combined_centroid = combined_centroid[sort_idx]
            if combined_bins is not None:
                combined_bins = combined_bins[sort_idx]

            self.logger.info(f"Combined centroid: {len(combined_centroid):,} positions")
            if combined_bins is not None:
                return combined_centroid, combined_bins
            return combined_centroid

        except Exception as e:
            self.logger.error(f"Failed to combine chunked centroids: {e}")
            return None

    def _load_sample(
        self, sample_path: Path
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Load sample with intelligent caching and memory management.

        Args:
            sample_path: Path to the sample file

        Returns:
            Tuple of (pos, mC, uC) arrays
        """
        # Use intelligent cache if enabled
        if self.cache_enabled:
            # Define loader function for cache
            def sample_loader(path: Path) -> "MethylSample":
                return self.load_sample(path, memory_map=True)

            cached_sample = self.sample_cache.get(sample_path, sample_loader)
            if cached_sample is not None:
                # Ensure sample is on CPU and get numpy arrays
                cached_sample = cached_sample.to_cpu()
                # Extract sorted arrays from cached MethylSample
                pos_vals = (
                    cached_sample.pos.values
                    if hasattr(cached_sample.pos, "values")
                    else np.asarray(cached_sample.pos)
                )
                mC_vals = (
                    cached_sample.mC.values
                    if hasattr(cached_sample.mC, "values")
                    else np.asarray(cached_sample.mC)
                )
                uC_vals = (
                    cached_sample.uC.values
                    if hasattr(cached_sample.uC, "values")
                    else np.asarray(cached_sample.uC)
                )
                sort_idx = np.argsort(pos_vals)
                return (pos_vals[sort_idx], mC_vals[sort_idx], uC_vals[sort_idx])

        # Fallback: load without caching
        methyl_sample = None
        try:
            methyl_sample = self.load_sample(sample_path, memory_map=True)

            # Ensure sample is on CPU and get numpy arrays
            methyl_sample = methyl_sample.to_cpu()

            # Get underlying numpy arrays from Series properties
            pos_vals = (
                methyl_sample.pos.values
                if hasattr(methyl_sample.pos, "values")
                else np.asarray(methyl_sample.pos)
            )
            mC_vals = (
                methyl_sample.mC.values
                if hasattr(methyl_sample.mC, "values")
                else np.asarray(methyl_sample.mC)
            )
            uC_vals = (
                methyl_sample.uC.values
                if hasattr(methyl_sample.uC, "values")
                else np.asarray(methyl_sample.uC)
            )

            # Sort by position (copies; safe to close sample after)
            sort_idx = np.argsort(pos_vals)
            sorted_pos = pos_vals[sort_idx].copy()
            sorted_mC = mC_vals[sort_idx].copy()
            sorted_uC = uC_vals[sort_idx].copy()

            return sorted_pos, sorted_mC, sorted_uC

        except Exception as e:
            self.logger.error(f"Error loading sample {sample_path}: {e}")
            # Return empty arrays on error
            return (
                np.array([], dtype=np.uint32),
                np.array([], dtype=np.uint32),
                np.array([], dtype=np.uint32),
            )
        finally:
            if methyl_sample is not None:
                try:
                    if hasattr(methyl_sample, "close"):
                        methyl_sample.close()
                except Exception:
                    pass

    def process_large_sample_chunked(
        self, sample_path: Path, chunk_size: int = 1_000_000
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        self.logger.info(f"Processing large sample {sample_path.name} in chunks")

        # Create a processing function for chunks
        def process_sample_chunk(chunk_data, **kwargs):
            pos = chunk_data.get("pos", np.array([]))
            mC = chunk_data.get("mC", np.array([]))
            uC = chunk_data.get("uC", np.array([]))

            # Filter for valid positions (some coverage)
            if len(mC) > 0 and len(uC) > 0:
                coverage = mC + uC
                valid_mask = coverage >= self.min_coverage
                return {
                    "pos": pos[valid_mask],
                    "mC": mC[valid_mask],
                    "uC": uC[valid_mask],
                    "positions_processed": np.sum(valid_mask),
                }
            else:
                return {
                    "pos": np.array([], dtype=np.uint32),
                    "mC": np.array([], dtype=np.uint32),
                    "uC": np.array([], dtype=np.uint32),
                    "positions_processed": 0,
                }

        # Configure chunked processor for this sample
        self.chunked_processor.chunk_size_positions = chunk_size

        # Process the file in chunks
        results = self.chunked_processor.process_file_chunked(
            sample_path,
            process_sample_chunk,
            self.output_dir / "temp_chunks",
            cleanup_temp=True,
        )

        # Aggregate results from all chunks
        all_pos = []
        all_mC = []
        all_uC = []

        for result in results.get("chunk_results", []):
            if result.success and result.data["positions_processed"] > 0:
                all_pos.append(result.data["pos"])
                all_mC.append(result.data["mC"])
                all_uC.append(result.data["uC"])

        if all_pos:
            # Concatenate all chunks
            return (
                np.concatenate(all_pos),
                np.concatenate(all_mC),
                np.concatenate(all_uC),
            )
        else:
            # Return empty arrays if no valid data
            return (
                np.array([], dtype=np.uint32),
                np.array([], dtype=np.uint32),
                np.array([], dtype=np.uint32),
            )

    def _backup_centroid(self, centroid_path: Path) -> Path:
        """Helper for atomic operations - preserved for future use."""
        backup_path = centroid_path.with_suffix(".h5.bak")
        if centroid_path.exists():
            import shutil

            shutil.copy2(centroid_path, backup_path)
        return backup_path

    def _restore_centroid(self, centroid_path: Path, backup_path: Path):
        """Helper for atomic operations - preserved for future use."""
        if backup_path.exists():
            import shutil

            if centroid_path.exists():
                centroid_path.unlink()
            shutil.move(backup_path, centroid_path)

    def _cleanup_backup(self, backup_path: Path):
        """Helper for atomic operations - preserved for future use."""
        if backup_path.exists():
            backup_path.unlink()

    def _backup_centroid(self, centroid_path: Path) -> Path:
        backup_path = centroid_path.with_suffix(".h5.bak")
        if centroid_path.exists():
            import shutil

            shutil.copy2(centroid_path, backup_path)
        return backup_path

    def _restore_centroid(self, centroid_path: Path, backup_path: Path):
        if backup_path.exists():
            import shutil

            if centroid_path.exists():
                centroid_path.unlink()
            shutil.move(backup_path, centroid_path)

    def _cleanup_backup(self, backup_path: Path):
        if backup_path.exists():
            backup_path.unlink()

    def _clear_sample_cache(self):
        """Clear the intelligent sample cache."""
        if hasattr(self.sample_cache, "clear"):
            cache_stats = self.sample_cache.get_stats()
            self.logger.info(
                f"Clearing sample cache: {cache_stats['cached_samples']} samples, "
                f"{cache_stats['memory_usage_mb']:.1f}MB memory"
            )
            self.sample_cache.clear()

            # Force garbage collection
            import gc

            gc.collect()

    def _get_memory_usage_info(self) -> dict:
        memory_info = self.memory_manager.get_memory_usage()
        return {
            "total_gb": memory_info.get("total_gb", 0),
            "available_gb": memory_info.get("available_gb", 0),
            "used_gb": memory_info.get("used_gb", 0),
            "percent_used": memory_info.get("percent_used", 0),
            "cache_stats": self.sample_cache.get_stats()
            if hasattr(self.sample_cache, "get_stats")
            else {"cached_samples": 0},
        }

    def _prepare_for_gpu_processing(self):
        memory_info = self._get_memory_usage_info()

        # If memory usage is high (>80%), clear the sample cache
        if memory_info["percent_used"] > 80:
            self.logger.info(
                "High memory usage detected, clearing sample cache for processing..."
            )
            self._clear_sample_cache()

        # GPU memory pressure handling with automatic CPU fallback
        if self._using_gpu:
            try:
                from scripts.gpu_memory_utils import GPUMemoryUtils

                if GPUMemoryUtils.is_memory_pressure_high(threshold_percent=85.0):
                    self.logger.warning(
                        "GPU memory pressure detected, attempting cleanup..."
                    )
                    GPUMemoryUtils.cleanup_gpu_memory(aggressive=True)

                    # Check if cleanup helped
                    if GPUMemoryUtils.is_memory_pressure_high(threshold_percent=90.0):
                        self.logger.warning(
                            "GPU memory pressure persists, falling back to CPU processing"
                        )
                        self._fallback_to_cpu_processing()
                        self._gpu_memory_pressure_detected = True
                    else:
                        self.logger.info("GPU memory pressure alleviated after cleanup")
            except ImportError:
                # GPU memory utilities not available, check for basic GPU memory issues
                self.logger.debug(
                    "GPU memory utilities not available, monitoring basic GPU usage"
                )
                self._check_gpu_memory_fallback()
        else:
            self.logger.debug("Using CPU processing mode")

    def _check_gpu_memory_fallback(self):
        """Basic GPU memory monitoring when advanced utilities aren't available"""
        try:
            import cupy as cp

            # Check if we can allocate a small test array
            test_array = cp.zeros((1000, 1000), dtype=cp.float32)
            del test_array
            cp.cuda.Device(0).synchronize()  # Force sync to catch any pending errors
        except Exception as e:
            self.logger.warning(f"GPU memory test failed: {e}")
            self._fallback_to_cpu_processing()
            self._gpu_memory_pressure_detected = True

    def _fallback_to_cpu_processing(self):
        """Switch from GPU to CPU processing mode"""
        if not self._using_gpu:
            return  # Already using CPU

        self.logger.info(
            "Switching to CPU processing mode for reliability with large datasets"
        )
        self._using_gpu = False

        # Reinitialize chunked processor with dynamic CPU parameters
        try:
            cpu_params = self._calculate_chunked_processor_params(use_gpu=False)
            self.chunked_processor = ChunkedGenomicProcessor(
                chunk_size_positions=cpu_params["chunk_size_positions"],
                max_workers=cpu_params["max_workers"],
                use_gpu=False,
                memory_limit_gb=cpu_params["memory_limit_gb"],
            )

            # Clear any GPU-based caches that might cause issues
            self._clear_sample_cache()

            self.logger.info("Successfully switched chunked processing to CPU mode")
        except Exception as e:
            self.logger.error(f"Failed to switch chunked processing to CPU: {e}")
            raise RuntimeError("Cannot fallback to CPU processing") from e

    def _attempt_gpu_recovery(self):
        """Attempt to recover GPU usage if memory pressure has eased"""
        if not self._gpu_enabled or not self._gpu_available or self._using_gpu:
            return  # GPU not available or already using GPU

        try:
            # Test if GPU is available again
            import cupy as cp

            test_array = cp.zeros((100, 100), dtype=cp.float32)
            del test_array
            cp.cuda.Device(0).synchronize()

            self.logger.info(
                "GPU memory pressure appears resolved, attempting to recover GPU usage"
            )
            self._using_gpu = True

            # Reinitialize chunked processor with dynamic GPU parameters
            gpu_params = self._calculate_chunked_processor_params(use_gpu=True)
            self.chunked_processor = ChunkedGenomicProcessor(
                chunk_size_positions=gpu_params["chunk_size_positions"],
                max_workers=gpu_params["max_workers"],
                use_gpu=True,
                memory_limit_gb=gpu_params["memory_limit_gb"],
            )

            self._gpu_memory_pressure_detected = False
            self.logger.info("Successfully recovered GPU processing mode")

        except Exception as e:
            self.logger.debug(f"GPU recovery test failed, staying with CPU: {e}")
            self._using_gpu = False

    def build_centroid(self) -> CentroidResults:
        """Build centroid with comprehensive performance profiling."""
        with self.performance_profiler.profile_operation("build_centroid"):
            # Check if centroid exists
            centroid_exists = self.centroid_path.exists()

            if centroid_exists:
                print(
                    f"Centroid file exists at {self.centroid_path}, rebuilding from samples"
                )

        # Calculate extended centroid
        try:
            # Note: extended=True computes stats like standard deviation which are useful

            centroid_path = self.calculate_centroid(str(self.output_dir), extended=True)
            if centroid_path is None:
                raise RuntimeError(
                    "Failed to compute centroid: calculate_centroid returned None"
                )
        except RuntimeError as e:
            error_msg = str(e).lower()
            if (
                "no samples" in error_msg
                or "failed to compute" in error_msg
                or "failed to save" in error_msg
            ):
                # Return empty results if no samples were added
                return CentroidResults(
                    final_centroid_path="", total_samples_processed=0
                )
            raise

        # Update config with final state
        self.save_final_config()

        # Release GPU and CPU memory after this chromosome/context so the next
        # combination starts with a clean slate (MethylUtils GPU cleanup is used
        # inside compute_centroid_chunked; non-chunked path and batch loop need this too).
        try:
            memory_manager = get_memory_manager()
            memory_manager.force_gpu_cleanup()
            if hasattr(self, "sample_cache") and self.sample_cache is not None:
                self.sample_cache.clear()
            import gc
            gc.collect()
        except Exception as e:
            self.logger.debug(f"Post-build cleanup: {e}")

        # Return results
        return CentroidResults(
            final_centroid_path=str(centroid_path),
            total_samples_processed=len(self.active_samples),
        )

    def save_final_config(self) -> None:
        """
        Save the configuration file with current samples.
        """
        # Get current active samples
        current_samples = []

        # Collect remaining samples (those still active)
        for sample_id in sorted(self.active_samples):
            if isinstance(sample_id, tuple) and len(sample_id) == 2:
                is_new_sample, sample_index = sample_id
                if is_new_sample:
                    sample_path = self.add_samples[sample_index]
                else:
                    sample_path = self.samples[sample_index]
                # Convert to directory path (remove chrom-ctx.h5 suffix)
                sample_dir = str(sample_path.parent)
                current_samples.append(sample_dir)

        # Update the original tracking lists
        self._original_samples = current_samples
        self._original_add_samples = []  # Clear add_samples after processing
        self._original_remove_samples = []  # Removals have been applied
        self.remove_samples = []

        # Save updated config
        config = self.get_config()
        config_path = self.output_dir / f"{self.chrom}-{self.ctx}_config.json"
        with open(config_path, "w") as f:
            json.dump(config.model_dump(), f, indent=2)

        print(f"Updated configuration saved to {config_path}")
        print(f"Total samples: {len(current_samples)}")

    def validate_centroid_calculation(self) -> bool:
        centroid_obj = self.get_centroid()
        if centroid_obj is None or len(centroid_obj) == 0:
            print("No centroid data available for validation")
            return False

        # Get valid positions from centroid (coverage >= min_coverage); load from H5 if not cached
        centroid_cpu = centroid_obj.to_cpu()
        coverage = np.asarray(centroid_cpu.coverage.values, dtype=np.uint32)
        valid_mask = coverage >= self._min_coverage
        # Convert to numpy arrays before indexing to avoid pandas indexing issues
        pos_values = np.asarray(centroid_cpu.pos.values, dtype=np.uint32)
        mC_values = np.asarray(centroid_cpu.mC.values, dtype=np.uint32)
        uC_values = np.asarray(centroid_cpu.uC.values, dtype=np.uint32)
        N_values = np.asarray(centroid_cpu.N.values, dtype=np.uint32)

        valid_pos = pos_values[valid_mask]
        centroid_mC = mC_values[valid_mask]
        centroid_uC = uC_values[valid_mask]
        centroid_N = N_values[valid_mask]

        if len(valid_pos) == 0:
            print("No valid positions for validation")
            return False

        # Calculate direct average for comparison using N per position
        direct_mC = np.zeros_like(centroid_mC, dtype=float)
        direct_uC = np.zeros_like(centroid_uC, dtype=float)
        direct_N = np.zeros_like(centroid_N, dtype=int)

        for sample_id in self.active_samples:
            is_new_sample, sample_index = sample_id
            sample_path = (
                self.add_samples[sample_index]
                if is_new_sample
                else self.samples[sample_index]
            )

            if sample_path.exists():
                try:
                    from methyl_utils import MethylSample

                    methyl_sample = MethylSample.load_from_h5(sample_path)

                    # Ensure arrays are NumPy arrays for validation operations
                    # Convert to CPU first to ensure numpy arrays
                    methyl_sample_cpu = methyl_sample.to_cpu()
                    sample_pos = np.asarray(
                        methyl_sample_cpu.pos.values, dtype=np.uint32
                    )
                    sample_mC = np.asarray(methyl_sample_cpu.mC.values, dtype=np.uint32)
                    sample_uC = np.asarray(methyl_sample_cpu.uC.values, dtype=np.uint32)

                    # Find common positions
                    common_pos, sample_idx, valid_idx = np.intersect1d(
                        sample_pos, valid_pos, return_indices=True
                    )

                    if len(common_pos) > 0:
                        direct_mC[valid_idx] += sample_mC[sample_idx]
                        direct_uC[valid_idx] += sample_uC[sample_idx]
                        direct_N[valid_idx] += 1

                except Exception as e:
                    print(f"Warning: Could not read sample {sample_path}: {e}")
                    continue

        # Calculate averages using N per position
        valid_N = direct_N > 0
        if np.any(valid_N):
            # Use np.where instead of np.divide with where parameter for CuPy compatibility
            valid_N_mask = direct_N[valid_N] > 0
            with np.errstate(divide="ignore", invalid="ignore"):
                direct_mC[valid_N] = np.where(
                    valid_N_mask,
                    direct_mC[valid_N].astype(np.float64)
                    / direct_N[valid_N].astype(np.float64),
                    0.0,
                ).astype(np.uint32)
                direct_uC[valid_N] = np.where(
                    valid_N_mask,
                    direct_uC[valid_N].astype(np.float64)
                    / direct_N[valid_N].astype(np.float64),
                    0.0,
                ).astype(np.uint32)

            # Compare with centroid calculation
            mC_diff = np.abs(centroid_mC[valid_N] - direct_mC[valid_N])
            uC_diff = np.abs(centroid_uC[valid_N] - direct_uC[valid_N])
            N_diff = np.abs(centroid_N[valid_N] - direct_N[valid_N])

            max_mC_diff = np.max(mC_diff)
            max_uC_diff = np.max(uC_diff)
            max_N_diff = np.max(N_diff)

            print(
                f"Centroid validation - Max mC difference: {max_mC_diff}, Max uC difference: {max_uC_diff}, Max N difference: {max_N_diff}"
            )

            # Allow small floating point differences (increased tolerance for integer division rounding)
            tolerance = 1.0  # Allow 1 unit difference due to integer division rounding
            if max_mC_diff < tolerance and max_uC_diff < tolerance and max_N_diff == 0:
                print("Centroid calculation validation PASSED")
                return True
            else:
                print("Centroid calculation validation FAILED")
                return False
        else:
            print("No valid samples found for validation")
            return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Calculate methylation centroids")
    parser.add_argument(
        "--data-dir", type=str, help="Data directory containing sample files"
    )
    parser.add_argument("--csv-file", type=str, help="CSV file with sample paths")
    parser.add_argument("--output-dir", type=str, help="Output directory for centroids")
    parser.add_argument("--chrom", type=str, default="1", help="Chromosome to process")
    parser.add_argument(
        "--ctx", type=str, default="CG", help="Context to process (CG, CHG, CHH)"
    )
    parser.add_argument(
        "--min-coverage", type=int, default=4, help="Minimum coverage threshold"
    )

    args = parser.parse_args()

    # Use command line args if provided, otherwise use original defaults
    if args.data_dir and args.csv_file and args.output_dir:
        # Command line mode
        data_dir = Path(args.data_dir)
        csv_file = Path(args.csv_file)
        out_dir = Path(args.output_dir)

        with open(csv_file, "r") as f:
            samples = [data_dir / line.strip() for line in f if line.strip()]

        print(f"Processing {args.chrom}-{args.ctx}...")
        print(f"Found {len(samples)} samples")

        mc = MethylCentroid(
            chrom=args.chrom,
            ctx=args.ctx,
            output_dir=out_dir,
            add_samples=[str(p) for p in samples],
            min_coverage=args.min_coverage,
        )

        # Build extended centroid with methylation statistics
        results = mc.build_centroid()
        print(f"Completed {args.chrom}-{args.ctx}")

        # Save results
        json_file_path = out_dir / f"{args.chrom}-{args.ctx}.json"
        with open(json_file_path, "w") as json_file:
            json.dump(results.model_dump(mode="json"), json_file)

        print(f"Results saved to {json_file_path}")

    else:
        # Original default mode
        data_dir = Path("/home/ubuntu/Work/output_workflows/arabidopsis")
        chroms = [str(i) for i in range(1, 6)]  # + ["X"]
        ctxs = ["CG", "CHG", "CHH"]
        csv_file = Path("/home/ubuntu/MethylCentroid/data/msh1drm2.csv")
        out_dir = data_dir / "centroids" / csv_file.stem

        with open(csv_file, "r") as f:
            samples = [data_dir / line.strip() for line in f if line.strip()]

        # Collect all batch statistics
        batch_stats = {
            "timestamp": datetime.now().isoformat(),
            "batch_id": f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "total_combinations": len(chroms) * len(ctxs),
            "combinations_processed": 0,
            "summary": {
                "total_samples": len(samples),
                "data_directory": str(data_dir),
                "csv_file": str(csv_file),
                "output_directory": str(out_dir),
            },
        }

        # Process all chromosome/context combinations
        for chrom in chroms:
            for ctx in ctxs:
                print(f"\nProcessing {chrom}-{ctx}...")
                mc = MethylCentroid(
                    chrom=chrom,
                    ctx=ctx,
                    output_dir=out_dir,
                    add_samples=[str(p) for p in samples],
                    min_coverage=4,
                )
                # Build extended centroid with methylation statistics
                results = mc.build_centroid()
                print(f"Completed {chrom}-{ctx}")

                # Save configuration
                config = mc.get_config()
                with open(out_dir / f"{chrom}-{ctx}_config.json", "w") as f:
                    json.dump(config.model_dump(), f)

                combination_stats = {
                    "chromosome": chrom,
                    "context": ctx,
                    "iterations": 0,
                    "timestamp": datetime.now().isoformat(),
                }

                # Add methylation statistics if available
                # Check if centroid has methylation stats (extended centroid always has them)
                if mc._centroid is not None and isinstance(
                    mc._centroid, MethylCentroidData
                ):
                    # Create alignment stats from centroid
                    # Note: These methods may need to be implemented differently
                    # For now, skip stats collection as it's not critical
                    alignment_stats = None
                    group_stats = None

                    combination_stats["methylation_stats"] = {
                        "alignment_stats": alignment_stats.model_dump(),
                        "group_stats": group_stats.model_dump()
                        if group_stats
                        else None,
                    }

                    # Save individual group-level methylation statistics
                    stats_file_path = out_dir / f"{chrom}-{ctx}_methylation_stats.json"

                    simplified_stats = {
                        "timestamp": datetime.now().isoformat(),
                        "analysis_id": f"{chrom}-{ctx}_analysis",
                        "chromosome": chrom,
                        "context": ctx,
                        "alignment_stats": alignment_stats.model_dump(),
                        "group_stats": group_stats.model_dump()
                        if group_stats
                        else None,
                        "metadata": {
                            "total_samples": len(samples),
                            "final_samples": len(mc.active_samples),
                            "centroid_type": "extended",
                        },
                    }

                    with open(stats_file_path, "w") as f:
                        json.dump(simplified_stats, f, indent=2)
                    print(
                        f"Saved group-level methylation statistics to {stats_file_path}"
                    )

                batch_stats["individual_results"].append(combination_stats)
                batch_stats["combinations_processed"] += 1

        # Save batch statistics
        batch_stats_file_path = out_dir / "batch_methylation_statistics.json"
        with open(batch_stats_file_path, "w") as f:
            json.dump(batch_stats, f, indent=2)
        print(f"\nSaved batch statistics to {batch_stats_file_path}")
        print(
            f"Batch summary: {batch_stats['combinations_processed']} combinations processed"
        )


def attach_binned_stats_to_centroid(
    centroid_path: Union[str, Path],
    bins: int = 20,
    output_dir: Optional[Union[str, Path]] = None,
    chunk_size_positions: Optional[int] = None,
    sample_dirs: Optional[List[str]] = None,
    verbose: bool = True,
) -> Path:
    """
    Attach centroid-level binned stats by reprocessing sample files.

    This reuses the existing centroid positions but recomputes statistics
    from the original samples to generate bin counts. It overwrites the
    centroid file unless output_dir is provided.

    Chunk size is memory-derived (GPU or system RAM via MethylUtils) when
    chunk_size_positions is None.
    """
    centroid_path = Path(centroid_path)
    from methyl_utils import MethylSample

    centroid = MethylSample.load_from_h5(centroid_path)
    meta = centroid.metadata or {}
    chrom = meta.get("chromosome")
    ctx = meta.get("context")
    if not chrom or not ctx:
        raise ValueError(
            "Centroid metadata missing chromosome/context; cannot attach binned stats"
        )

    if sample_dirs is None:
        raw = meta.get("sample_paths") or meta.get("samples_used", [])
        sample_dirs = MethylSample.resolve_samples_used_paths(
            [str(x) for x in raw],
            meta.get("samples_base_path"),
            None,
        )
    if not sample_dirs:
        raise ValueError("No sample directories available to rebuild binned stats")

    out_dir = Path(output_dir) if output_dir is not None else centroid_path.parent

    worker = MethylCentroid(
        chrom=chrom,
        ctx=ctx,
        output_dir=out_dir,
        add_samples=sample_dirs,
        min_coverage=int(meta.get("min_coverage", 4)),
        min_samples=int(meta.get("min_samples", 1)),
        verbose=verbose,
        binned_stats_bins=int(bins),
        laboratory=meta.get("laboratory"),
        disease=meta.get("disease"),
        group=meta.get("group"),
        batch=meta.get("batch"),
    )

    # Use existing centroid positions for chunking
    worker._centroid = centroid
    worker.active_samples = set((False, i) for i in range(len(worker.samples)))

    centroid_data = worker.compute_centroid_chunked(
        extended=True, chunk_size_positions=chunk_size_positions
    )
    if centroid_data is None or len(centroid_data) == 0:
        raise RuntimeError("Failed to compute centroid with binned stats")

    return worker.save_centroid(str(out_dir), centroid_data, extended=True)
