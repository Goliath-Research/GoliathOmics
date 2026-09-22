"""Core MethylDetector pipeline for DMP detection, filtering, and selection."""

import itertools
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd

# Import statistical functions from MethylUtils (required)
from methyl_utils import (
    cleanup_gpu_memory,
    prefer_gpu_default,
)
from methyl_utils.logging_utils import setup_module_logging

# Import sample handler for proper SRP compliance
from methyl_utils.core.methyl_frame import MethylSample

# Import MethylCentroidPair from MethylUtils for mathematical operations
from methyl_utils import MethylCentroidPair

from methyl_utils.ecdf_classifier import ECDFClassifier, _LOG_PDF_CAP as LOG_PDF_CAP

# Handle relative imports - try module import first, fall back to direct execution setup
try:
    from ..models.config import MethylDetectorConfig
    from ..models.results import (
        ComparisonStats,
        MethylDetectorResult,
        MethylDetectorSummary,
    )
    from ..utils.core import GPUConfig, save_csv, save_json, save_summary_txt
    from ..utils.file_utils import get_chromosome_context_from_filename
except ImportError:
    # For direct execution, these will be set up in __main__
    MethylDetectorConfig = None
    ComparisonStats = None
    MethylDetectorResult = None
    GPUConfig = None
    save_csv = None
    save_json = None
    save_summary_txt = None
    CentroidPairHandler = None
    create_centroid_from_arrays = None
    get_chromosome_context_from_filename = None
logger = setup_module_logging(__name__)


@dataclass
class ValidationPrefixCache:
    """Cached ECDF validation state for fast repeated top-k evaluation."""

    sorted_df: pd.DataFrame
    weights: np.ndarray
    y_val: np.ndarray
    splits: List[Tuple[np.ndarray, np.ndarray]]
    prefix_ll_c1: List[np.ndarray]
    prefix_ll_c2: List[np.ndarray]
    prefix_ll_c1_calib: List[np.ndarray]
    prefix_ll_c2_calib: List[np.ndarray]
    temperature: float

def _select_by_effect_coverage(df: pd.DataFrame, coverage: float) -> pd.DataFrame:
    """
    Biological filter: per-context ECDF cumulative mass selection on effect_size.

    For each context group independently, sort positions by effect_size descending
    and keep the minimum set whose effects sum to >= coverage fraction of total
    effect mass in that context. Applied per-context so CG/CHG/CHH are selected
    independently (their effect size distributions are not cross-comparable).

    coverage=1.0 keeps all positions. coverage=0.0 returns empty.
    """
    if len(df) == 0 or "effect_size" not in df.columns:
        return df.copy()
    if coverage >= 1.0:
        return df.copy()

    keep_idx: list = []
    ctx_col = "context" if "context" in df.columns else None

    if ctx_col is not None:
        groups = df.groupby(ctx_col, sort=False)
    else:
        groups = [("all", df)]

    for _ctx, grp in groups:
        effects = grp["effect_size"].astype(float).values
        S = float(effects.sum())
        if S <= 0.0:
            continue
        order = np.argsort(-effects)
        cumulative = np.cumsum(effects[order]) / S
        K = int(np.searchsorted(cumulative, coverage, side="left")) + 1
        K = min(K, len(order))
        keep_idx.extend(grp.index[order[:K]].tolist())

    return df.loc[keep_idx].copy()


def _validate_fixed_panel_recurrence_metadata(df: pd.DataFrame, panel_path: Path, eps: float = 1e-9) -> None:
    """
    Validate optional stability recurrence metadata in fixed DMP panels.

    If any recurrence columns are present, enforce:
      - finite numeric values
      - 0 <= frequency <= 1
      - 0 <= count <= n_runs and n_runs > 0
      - frequency ~= count / n_runs when all three are present
    """
    recurrence_cols = {"frequency", "count", "n_runs"}
    present = recurrence_cols.intersection(df.columns)
    if not present:
        return

    numeric: Dict[str, pd.Series] = {}
    for col in present:
        s = pd.to_numeric(df[col], errors="coerce")
        bad = ~np.isfinite(s.to_numpy(dtype=float))
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid recurrence metadata in fixed_dmp_panel {panel_path}: "
                f"column '{col}' must be finite numeric values. "
                f"Example row(s): {bad_rows}"
            )
        numeric[col] = s.astype(float)

    if "frequency" in numeric:
        freq = numeric["frequency"]
        bad = (freq < 0.0 - eps) | (freq > 1.0 + eps)
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid recurrence metadata in fixed_dmp_panel {panel_path}: "
                "column 'frequency' must be in [0, 1]. "
                f"Example row(s): {bad_rows}"
            )

    for col in ("count", "n_runs"):
        if col in numeric:
            bad = numeric[col] < 0.0 - eps
            if bad.any():
                bad_rows = (df.index[bad][:5] + 1).tolist()
                raise ValueError(
                    f"Invalid recurrence metadata in fixed_dmp_panel {panel_path}: "
                    f"column '{col}' must be >= 0. "
                    f"Example row(s): {bad_rows}"
                )

    if "n_runs" in numeric:
        bad = numeric["n_runs"] <= eps
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid recurrence metadata in fixed_dmp_panel {panel_path}: "
                "column 'n_runs' must be > 0. "
                f"Example row(s): {bad_rows}"
            )

    if {"count", "n_runs"}.issubset(numeric):
        bad = numeric["count"] > (numeric["n_runs"] + eps)
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid recurrence metadata in fixed_dmp_panel {panel_path}: "
                "column 'count' must be <= 'n_runs'. "
                f"Example row(s): {bad_rows}"
            )

    if {"frequency", "count", "n_runs"}.issubset(numeric):
        expected = numeric["count"] / numeric["n_runs"]
        bad = (numeric["frequency"] - expected).abs() > eps
        if bad.any():
            bad_rows = (df.index[bad][:5] + 1).tolist()
            raise ValueError(
                f"Invalid recurrence metadata in fixed_dmp_panel {panel_path}: "
                "'frequency' must equal count / n_runs. "
                f"Example row(s): {bad_rows}"
            )


class MethylDetector:
    """Main class for MethylDetector DMP detection and filtering."""

    def __init__(self, config: MethylDetectorConfig):
        """Initialize with configuration."""
        self.config = config
        np.random.seed(config.random_state)
        self.gpu_config = GPUConfig()  # From MethylUtils for memory management
        self._runtime_gpu_used = False
        self.df = None  # Current working dataframe
        self._exported_csv_path = None  # Path to exported CSV file
        self._current_chromosome = None  # Current chromosome being processed (for multi-chromosome mode)
        self._centroid_bin_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # Optional calibration artifacts are attached only when validation trains them.
        # Initialize to None so export paths can safely check these attributes.
        self._platt_calibrator_bytes: Optional[bytes] = None
        self._platt_calibrator_scaler_bytes: Optional[bytes] = None
        # One-shot warning guards for noisy validation diagnostics.
        self._low_overlap_warned_once = False
        self._no_variation_warned_once = False
        self._degenerate_probs_warned_once = False
        self._ba_05_warned_once = False
        logger.debug("Initialized MethylDetector")
    
    @property
    def chromosome(self) -> str:
        """Get the current chromosome being processed."""
        if self._current_chromosome is not None:
            return self._current_chromosome
        # Fallback: if config.chromosome is a list, return first; otherwise return as-is
        if isinstance(self.config.chromosome, list):
            return self.config.chromosome[0]
        return self.config.chromosome
    
    def run(self) -> Union[MethylDetectorResult, List[MethylDetectorResult]]:
        """
        Run the complete DMP detection and filtering pipeline.
        
        Returns:
            MethylDetectorResult if processing a single chromosome,
            List[MethylDetectorResult] if processing multiple chromosomes
        """
        logger.debug("Starting MethylDetector analysis pipeline...")
        
        # Check if we're processing multiple chromosomes
        chromosomes = self.config.chromosome
        if isinstance(chromosomes, str):
            chromosomes = [chromosomes]  # Normalize to list
        
        if len(chromosomes) == 1:
            # Single chromosome: process normally
            self._current_chromosome = chromosomes[0]
            try:
                result = self._run_multi_context()
                self._aggregate_filter_funnel_csvs(chromosomes)
                return result
            finally:
                self._release_iteration_resources()
        else:
            # Multiple chromosomes: require binned_stats once before any work (fail fast)
            self._current_chromosome = chromosomes[0]
            self._require_binned_stats_available()
            logger.info(f"🧬 Processing {len(chromosomes)} chromosomes: {', '.join(chromosomes)}")
            results = []
            failed_chromosomes = []
            
            for i, chrom in enumerate(chromosomes, 1):
                logger.info(f"\n{'='*80}")
                logger.info(f"Processing chromosome {chrom} ({i}/{len(chromosomes)})")
                logger.info(f"{'='*80}")
                
                try:
                    self._current_chromosome = chrom
                    result = self._run_multi_context()
                    results.append(result)
                    logger.info(f"✅ Successfully completed chromosome {chrom}")
                except Exception as e:
                    logger.error(f"❌ Failed to process chromosome {chrom}: {e}")
                    import traceback
                    traceback.print_exc()
                    failed_chromosomes.append((chrom, str(e)))
                finally:
                    self._release_iteration_resources()
            
            # Summary
            logger.info(f"\n{'='*80}")
            logger.info("Multi-chromosome processing complete:")
            logger.info(f"  ✅ Successful: {len(results)}/{len(chromosomes)}")
            if failed_chromosomes:
                logger.warning(f"  ❌ Failed: {len(failed_chromosomes)}")
                for chrom, error in failed_chromosomes:
                    logger.warning(f"    - {chrom}: {error}")
            logger.info(f"{'='*80}\n")

            self._aggregate_filter_funnel_csvs(chromosomes)
            return results

    def _release_iteration_resources(self) -> None:
        """
        Release per-iteration caches and best-effort GPU allocations.

        Monte Carlo and multi-chromosome runs execute many consecutive detector
        passes in one process; clearing cached centroid arrays avoids unbounded
        growth across iterations.
        """
        self._centroid_bin_cache.clear()
        try:
            cleanup_gpu_memory()
        except Exception:
            pass

        import gc
        gc.collect()
    
    def _run_multi_context(self) -> MethylDetectorResult:
        """Run multi-context analysis (new unified approach)."""
        logger.info(f"🧬 Starting multi-context analysis for chromosome {self.chromosome}")
        logger.info(f"📍 Contexts: {', '.join(self.config.contexts)}")

        # Check for fixed_dmp_panel to bypass discovery (for production freeze)
        if getattr(self.config, "fixed_dmp_panel", None):
            logger.info(f"🔒 Using fixed DMP panel: {self.config.fixed_dmp_panel} (bypassing statistical/biological discovery)")
            fixed_path = Path(str(self.config.fixed_dmp_panel))
            if not fixed_path.exists():
                raise FileNotFoundError(f"fixed_dmp_panel not found: {fixed_path}")
            # Load the fixed panel and short-circuit to export/classifier phase
            df_all = pd.read_csv(fixed_path)
            if not {"chromosome", "position"}.issubset(df_all.columns):
                raise ValueError("fixed_dmp_panel CSV must contain 'chromosome' and 'position' columns")
            _validate_fixed_panel_recurrence_metadata(df_all, fixed_path)

            # Filter to the current chromosome — the panel may cover the whole genome
            # but each _run_multi_context call handles exactly one chromosome.
            df_fixed = df_all[
                df_all["chromosome"].astype(str) == str(self.chromosome)
            ].copy()
            if len(df_fixed) == 0:
                logger.warning(
                    f"fixed_dmp_panel has no entries for chromosome {self.chromosome}; "
                    "producing empty result for this chromosome."
                )

            # Ensure required columns for classifier, export, and result compatibility
            if "context" not in df_fixed.columns:
                ctx = self.config.contexts[0] if self.config.contexts else "CG"
                df_fixed["context"] = ctx
            if "effect_size" not in df_fixed.columns:
                df_fixed["effect_size"] = 1.0
            if "statistical_dmp" not in df_fixed.columns:
                df_fixed["statistical_dmp"] = True
            if "biological_dmp" not in df_fixed.columns:
                df_fixed["biological_dmp"] = True

            n_loaded = len(df_fixed)
            logger.info(
                f"Loaded fixed panel for chromosome {self.chromosome}: "
                f"{n_loaded:,} DMPs (panel total: {len(df_all):,})"
            )

            # Stable / genomewide panels may include CpGs absent from these centroid H5s; classifier
            # needs the intersection only (same as discovery-defined DMPs).
            df_fixed = self._subset_dmps_to_both_centroids(df_fixed, log_drops=False)
            n_dmps = len(df_fixed)
            if n_loaded and n_dmps < n_loaded:
                logger.info(
                    "Fixed panel: omitted %s loci not in both centroids; exporting %s of %s loaded for chr %s",
                    f"{n_loaded - n_dmps:,}",
                    f"{n_dmps:,}",
                    f"{n_loaded:,}",
                    self.chromosome,
                )
            if n_dmps == 0 and n_loaded > 0:
                logger.error(
                    "Fixed DMP panel: no positions intersect current centroid1_dir/centroid2_dir "
                    "for chromosome %s; check centroid paths or panel provenance.",
                    self.chromosome,
                )

            # Build minimal comparison stats for result
            ctx = self.config.contexts[0] if self.config.contexts else "CG"
            stats = ComparisonStats(
                comparison_name=f"{self.chromosome}-{ctx}",
                total_positions=n_dmps,
                statistical_dmps=n_dmps,
                biological_dmps=n_dmps,
                processing_time_seconds=0.0,
                gpu_used=self._runtime_gpu_used,
            )
            comparison_stats = [stats]

            config_summary = getattr(self.config, "model_dump", lambda: {"fixed_panel_mode": True})()

            result = MethylDetectorResult(
                biologically_significant_dmps_df=df_fixed,
                total_statistical_dmps=n_dmps,
                total_biological_dmps=n_dmps,
                biological_retention_rate=1.0,
                comparison_stats=comparison_stats,
                timestamp=datetime.now().isoformat(),
                version="2.0.0-fixed-panel",
                config_summary=config_summary,
            )

            if getattr(self.config, "output_dir", None):
                self._export_unified_csv(df_fixed, suffix="")
                self._save_unified_model(None, df_fixed)
            logger.info(f"✅ Fixed panel mode complete: {n_dmps} DMPs")
            return result

        # Require binned_stats on centroids (ECDF-based metrics); fail fast before any context
        self._require_binned_stats_available()

        # Validate centroid parameters before analysis
        self._validate_centroid_parameters()

        all_dmps = []  # List to collect DataFrames from each context
        total_positions_tested = 0  # Positions that entered the statistical test (for funnel)

        # Loop over all contexts
        for context in self.config.contexts:
            logger.info(f"🔬 Processing context: {context}")
            
            # Build paths to centroid files
            c1_path = Path(self.config.centroid1_dir) / f"{self.chromosome}-{context}.h5"
            c2_path = Path(self.config.centroid2_dir) / f"{self.chromosome}-{context}.h5"
            
            # Check if files exist
            if not c1_path.exists():
                logger.warning(f"Centroid1 file not found: {c1_path}, skipping context {context}")
                continue
            if not c2_path.exists():
                logger.warning(f"Centroid2 file not found: {c2_path}, skipping context {context}")
                continue
            
            # Detect DMPs for this context
            try:
                dmp_df, n_tested = self._detect_statistical_dmps_for_context(c1_path, c2_path, context)
                total_positions_tested += n_tested
                n_confirmed = int(dmp_df["statistical_dmp"].sum()) if "statistical_dmp" in dmp_df.columns else len(dmp_df)
                n_rescue = max(0, len(dmp_df) - n_confirmed)
                logger.info(
                    "✅ Context %s: %s confirmed statistical DMPs + %s biological-only rescue candidates",
                    context,
                    f"{n_confirmed:,}",
                    f"{n_rescue:,}",
                )
                all_dmps.append(dmp_df)
            except Exception as e:
                logger.error(f"❌ Context {context} failed: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        if not all_dmps:
            raise ValueError("No DMPs detected in any context")
        
        # Combine all contexts into single DataFrame
        logger.info("📊 Combining all contexts into unified DataFrame...")
        dmps_df = pd.concat(all_dmps, ignore_index=True)
        total_confirmed = int(dmps_df["statistical_dmp"].sum()) if "statistical_dmp" in dmps_df.columns else len(dmps_df)
        total_rescue = max(0, len(dmps_df) - total_confirmed)
        logger.info(
            "✅ Combined DataFrame: %s confirmed statistical DMPs + %s biological-only rescue candidates across %s contexts",
            f"{total_confirmed:,}",
            f"{total_rescue:,}",
            len(all_dmps),
        )
        
        # Compute context weights and add to DataFrame
        if self.config.use_context_weights:
            logger.info("⚖️  Computing context weights using trimmed-mean normalization...")
            dmps_df = self._compute_context_weights(dmps_df)
            
            # Log weights
            weight_summary = dmps_df.groupby('context')['context_weight'].first().to_dict()
            for ctx, w in sorted(weight_summary.items()):
                logger.info(f"  Context {ctx}: weight = {w:.4f}")
        else:
            # Equal weights
            dmps_df['context_weight'] = 1.0 / len(self.config.contexts)
            logger.info("Using equal context weights")

        # Optional: filter funnel sweep (range/step per biological filter → filter_funnel-{chrom}.csv)
        self._run_filter_funnel_sweep(dmps_df)

        # Filter biological DMPs (apply biological filters)
        logger.info("🔬 Filtering biologically significant DMPs...")
        bio_dmps_df = self._filter_biological_dmps(dmps_df)
        n_bio = len(bio_dmps_df)
        n_stat = total_confirmed
        n_bio_confirmed = int(bio_dmps_df["statistical_dmp"].sum()) if "statistical_dmp" in bio_dmps_df.columns else n_bio
        n_bio_rescue = max(0, n_bio - n_bio_confirmed)
        pct = f"{n_bio_confirmed / n_stat * 100:.1f}%" if n_stat and n_stat > 0 else "N/A"
        logger.info(
            "✅ Biological DMPs: %s total (%s confirmed statistical + %s biological-only rescue; confirmed retention: %s)",
            f"{n_bio:,}",
            f"{n_bio_confirmed:,}",
            f"{n_bio_rescue:,}",
            pct,
        )

        # Log DMP filter funnel with enter / pass / retention for each filter (after biological so we have all counts)
        self._log_dmp_filter_funnel(
            total_positions_tested=total_positions_tested,
            total_statistical=total_confirmed,
            total_biological=n_bio,
        )

        bio_dmps_df = bio_dmps_df.sort_values("effect_size", ascending=False).reset_index(drop=True)
        logger.info("📊 Biological DMPs sorted by effect_size")
       
        # Compute biological importance and sort (funnel output = all biological DMPs; no optimization)
        logger.info("📋 Sorting DMPs by biological importance...")
        sorted_by_importance_df = self._compute_biological_importance(bio_dmps_df)
        self._featurecuts_last_result = None
        self._final_validation_results = None
        self._classifier_panel_audit = None
        export_mode = getattr(self.config, "dmp_export_mode", "unified")
        detection_mode = str(getattr(self.config, "detection_mode", "legacy") or "legacy")
        discovery_only = detection_mode == "discovery_only"

        if discovery_only:
            selected_dmps_df = sorted_by_importance_df
            if self.config.output_dir:
                logger.info("💾 discovery_only: exporting discovery branch only (no classifier selection)")
                discovery_dmps_df = self._discovery_dmps_from_sorted(sorted_by_importance_df)
                self._export_unified_csv(discovery_dmps_df, suffix="-discovery")
                self._write_dmp_branch_metadata(
                    discovery_dmps_df,
                    discovery_dmps_df.iloc[0:0].copy(),
                    discovery_dmps_df.iloc[0:0].copy(),
                )
                self._save_validation_results(
                    n_dmps_exported=len(discovery_dmps_df),
                    total_statistical_dmps=total_confirmed,
                    total_biological_dmps=len(bio_dmps_df),
                )
            result = self._create_multi_context_result(dmps_df, selected_dmps_df)
            logger.info("✅ discovery_only complete for chromosome %s", self.chromosome)
            return result

        classifier_dmps_df = self._classifier_dmps_from_sorted(sorted_by_importance_df)
        selected_dmps_df = classifier_dmps_df

        # Export: discovery vs classifier branches (dual) or unified CSV + classifier
        if self.config.output_dir:
            logger.info("💾 Exporting DMPs and classifier (mode=%s)...", export_mode)
            if export_mode == "dual":
                discovery_dmps_df = self._discovery_dmps_from_sorted(sorted_by_importance_df)
                extended_dmps_df = self._classifier_extended_dmps_from_core(
                    sorted_by_importance_df,
                    classifier_dmps_df,
                )
                if len(discovery_dmps_df) < int(self.config.min_dmps_for_export):
                    logger.warning(
                        "Discovery DMP count %s is below min_dmps_for_export=%s (mapper/enricher may be sparse)",
                        len(discovery_dmps_df),
                        self.config.min_dmps_for_export,
                    )
                self._export_unified_csv(discovery_dmps_df, suffix="-discovery")
                self._export_unified_csv(classifier_dmps_df, suffix="-classifier")
                self._export_unified_csv(extended_dmps_df, suffix="-classifier-extended")
                self._write_dmp_branch_metadata(
                    discovery_dmps_df,
                    classifier_dmps_df,
                    extended_dmps_df,
                )
            else:
                export_df = classifier_dmps_df
                if (
                    len(export_df) < int(self.config.min_dmps_for_export)
                    and len(sorted_by_importance_df) >= int(self.config.min_dmps_for_export)
                ):
                    export_df = sorted_by_importance_df.iloc[: int(self.config.min_dmps_for_export)].copy()
                    logger.info(
                        "Unified export: CSV has %s rows (min_dmps_for_export) while classifier uses %s DMPs",
                        len(export_df),
                        len(classifier_dmps_df),
                    )
                elif len(export_df) < int(self.config.min_dmps_for_export):
                    logger.warning(
                        "Fewer biological DMPs (%s) than min_dmps_for_export=%s",
                        len(export_df),
                        self.config.min_dmps_for_export,
                    )
                self._export_unified_csv(export_df, suffix="")
            if bool(getattr(self.config, "export_classifier", True)):
                self._save_unified_model(None, classifier_dmps_df)
            else:
                logger.info("export_classifier=False; skipping inline classifier pickle (use pipeline.classifier)")
            if getattr(self, '_featurecuts_last_result', None) is not None:
                self._final_validation_results = self._featurecuts_last_result
            else:
                merged_val = self._validate_selected_dmps(classifier_dmps_df)
                if merged_val:
                    self._final_validation_results = merged_val
            self._save_validation_results(
                n_dmps_exported=len(classifier_dmps_df),
                total_statistical_dmps=total_confirmed,
                total_biological_dmps=len(bio_dmps_df),
            )
        
        # Create result (use selected DMPs for result stats)
        result = self._create_multi_context_result(dmps_df, selected_dmps_df)
        logger.info(f"✅ Multi-context analysis complete for chromosome {self.chromosome}!")
        
        return result
   
    def timer(func):
        """Decorator to time and log function execution."""
        import time
        import functools
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            logger.info(f"⏱️ Entering {func.__name__} at {start_time:.2f}")
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                logger.info(f"⏱️ Exiting {func.__name__} after {duration:.2f}s")
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"⏱️ {func.__name__} failed after {duration:.2f}s: {e}")
                raise
        return wrapper

    def _require_binned_stats_available(self) -> None:
        """Require that centroids have binned_stats (ECDF-based metrics). Raise at start of run if missing."""
        context0 = self.config.contexts[0] if self.config.contexts else "CG"
        c1_path = Path(self.config.centroid1_dir) / f"{self.chromosome}-{context0}.h5"
        c2_path = Path(self.config.centroid2_dir) / f"{self.chromosome}-{context0}.h5"
        if not c1_path.exists():
            raise FileNotFoundError(
                f"Centroid1 file not found: {c1_path}. "
                "MethylDetector requires centroids with binned_stats; build them first (methyl-centroid with binned_stats_bins, default 20)."
            )
        if not c2_path.exists():
            raise FileNotFoundError(
                f"Centroid2 file not found: {c2_path}. "
                "MethylDetector requires centroids with binned_stats; build them first (methyl-centroid with binned_stats_bins, default 20)."
            )
        centroid = MethylSample.load_from_h5(str(c1_path))
        try:
            binned = centroid.binned_stats if centroid else None
            if not binned or "bin_edges" not in binned or "bin_counts" not in binned:
                raise ValueError(
                    "Centroids must have binned_stats for MethylDetector (continuous ECDF overlap and effect_size). "
                    "Re-run methyl-centroid with binned_stats_bins (e.g. base_config.binned_stats_bins=20, or use default 20). "
                    "If you already did, check that the H5 files at "
                    "centroid1_dir/centroid2_dir are the ones just built (e.g. Python: MethylSample.load_from_h5(path).binned_stats)."
                )
        finally:
            if centroid is not None and hasattr(centroid, "close"):
                centroid.close()

    def _detect_statistical_dmps_for_context(
        self, 
        centroid1_path: Path, 
        centroid2_path: Path, 
        context: str
    ) -> pd.DataFrame:
        """
        Detect statistical DMPs for a specific context.
        
        Args:
            centroid1_path: Path to centroid1 H5 file
            centroid2_path: Path to centroid2 H5 file
            context: Context string (e.g., "CG", "CHG", "CHH")
            
        Returns:
            DataFrame with DMPs including chromosome and context columns
        """
        logger.debug(f"Processing centroids for context {context}...")
        
        # Load and align centroids (all common positions; per-centroid filters applied in the pair)
        centroid1, centroid2, common_positions = MethylCentroidPair.load_and_align(
            centroid1_path,
            centroid2_path,
            min_coverage=self.config.min_coverage,
        )
        n1_cohort = self._min_samples_cohort_size(
            centroid1, self.config.centroid1_dir, "centroid1"
        )
        n2_cohort = self._min_samples_cohort_size(
            centroid2, self.config.centroid2_dir, "centroid2"
        )
        min_s1 = self.config.effective_min_samples(n1_cohort)
        min_s2 = self.config.effective_min_samples(n2_cohort)
        logger.info(
            "Context %s: min_samples cohort sizes centroid1=%s centroid2=%s "
            "-> per-position thresholds N>=%s and N>=%s "
            "(min_samples_abs=%s min_samples_pct=%s)",
            context,
            n1_cohort,
            n2_cohort,
            min_s1,
            min_s2,
            self.config.min_samples_abs,
            self.config.min_samples_pct,
        )

        # Pre-filter aligned positions by delta_mean before the expensive statistical
        # comparison. This uses only the centroid means (N, Sx), which are cheap to
        # evaluate, to reduce the tested set from millions of positions down to those
        # that could ever have any biological signal. Only active when
        # delta_mean_reduction is set.
        delta_gate = self.config.delta_mean_reduction

        import time
        pre_filter_position_subset = None
        if delta_gate is not None:
            pos1 = np.asarray(centroid1.pos.values, dtype=np.uint32)
            pos2 = np.asarray(centroid2.pos.values, dtype=np.uint32)
            common_pre = np.intersect1d(pos1, pos2)
            idx1_pre = np.searchsorted(pos1, common_pre, side="left")
            idx2_pre = np.searchsorted(pos2, common_pre, side="left")
            m1 = np.asarray(centroid1.mean)[idx1_pre]
            m2 = np.asarray(centroid2.mean)[idx2_pre]
            dm_mask = np.abs(m1 - m2) >= float(delta_gate)
            pre_filter_position_subset = common_pre[dm_mask]
            n_pre = int(dm_mask.sum())
            logger.info(
                "Context %s: pre-filter |delta_mean| >= %.3f reduces %s → %s positions "
                "before statistical test",
                context,
                delta_gate,
                f"{len(common_pre):,}",
                f"{n_pre:,}",
            )

        # Create centroid pair: per-centroid valid sets (coverage + N), candidate = intersection
        centroid_pair = MethylCentroidPair(
            min_coverage=self.config.min_coverage,
            min_samples=(min_s1, min_s2),
        )

        # Compare centroids (only at pre-filtered positions when the gate is active)
        start_time = time.time()
        comparison_results = centroid_pair.compare_centroids(
            centroid1, centroid2, position_subset=pre_filter_position_subset
        )
        self._runtime_gpu_used = self._runtime_gpu_used or centroid_pair.gpu_available
        processing_time = time.time() - start_time

        logger.info(f"Context {context}: Compared {len(comparison_results):,} positions in {processing_time:.2f}s")
        comparison_results["chromosome"] = self.chromosome
        comparison_results["context"] = context
        if "effect_size" in comparison_results.columns:
            comparison_results["effect_size_approx"] = comparison_results["effect_size"].astype(np.float32)

        # Optional: replace p_value/q_value with KS on precise ECDF (default path)
        ecdf_view_1_full = None
        ecdf_view_2_full = None
        if self.config.significance_test == "ks_ecdf" and len(comparison_results) > 0:
            from methyl_utils.core.distribution_views import ECDFView
            from methyl_utils.statistical_tests import ecdf_ks_pvalue
            dmp_positions = np.asarray(comparison_results["position"].values, dtype=np.uint32)
            pos1 = np.asarray(centroid1.pos.values, dtype=np.uint32)
            pos2 = np.asarray(centroid2.pos.values, dtype=np.uint32)
            idx_in_c1 = np.searchsorted(pos1, dmp_positions, side="left")
            idx_in_c2 = np.searchsorted(pos2, dmp_positions, side="left")
            bs1 = centroid1.binned_stats
            bs2 = centroid2.binned_stats
            bin_edges_arr = np.asarray(bs1["bin_edges"], dtype=np.float64)
            ecdf_view_1_full = ECDFView(
                bin_edges_arr,
                np.asarray(bs1["bin_counts"], dtype=np.float64)[idx_in_c1],
                np.asarray(centroid1.Sx.values, dtype=np.float64)[idx_in_c1],
                np.asarray(centroid1.N.values, dtype=np.float64)[idx_in_c1],
                np.asarray(centroid1.Sx2.values, dtype=np.float64)[idx_in_c1],
            )
            ecdf_view_2_full = ECDFView(
                bin_edges_arr,
                np.asarray(bs2["bin_counts"], dtype=np.float64)[idx_in_c2],
                np.asarray(centroid2.Sx.values, dtype=np.float64)[idx_in_c2],
                np.asarray(centroid2.N.values, dtype=np.float64)[idx_in_c2],
                np.asarray(centroid2.Sx2.values, dtype=np.float64)[idx_in_c2],
            )
            position_indices = np.arange(len(comparison_results), dtype=np.intp)
            n1 = comparison_results["n1"].values.astype(np.float64)
            n2 = comparison_results["n2"].values.astype(np.float64)
            _, ks_pvalues = ecdf_ks_pvalue(
                ecdf_view_1_full,
                ecdf_view_2_full,
                position_indices,
                n1=n1,
                n2=n2,
                grid_size=self.config.ecdf_grid_size,
            )
            comparison_results["p_value"] = ks_pvalues.astype(np.float32)
            from methyl_utils.statistical_tests import storey_qvalues
            q_values, _ = storey_qvalues(comparison_results["p_value"].values)
            comparison_results["q_value"] = q_values.astype(np.float32)
            logger.info(
                "Context %s: significance from KS on precise ECDF (grid_size=%s)",
                context,
                self.config.ecdf_grid_size,
            )

        comparison_results = self._apply_tau2_filter(comparison_results, context)

        # Apply statistical filtering (n_positions_compared = positions that entered the statistical step)
        total_positions = len(comparison_results)
        n_positions_compared = total_positions
        confirmed_results = comparison_results[comparison_results['q_value'] <= self.config.alpha].copy()
        statistical_dmps_count = len(confirmed_results)

        logger.info(
            f"Context {context}: {statistical_dmps_count:,} significant DMPs (q≤{self.config.alpha}) "
            f"out of {total_positions:,} ({(statistical_dmps_count / total_positions) * 100:.1f}% pass rate)"
            if total_positions > 0 else
            f"Context {context}: 0 significant DMPs (no positions tested)"
        )

        # The delta_mean gate was already applied before compare_centroids, so every
        # position in filtered_results already satisfies |delta_mean| >= gate.  The
        # post-filter below is kept as a safety guard for the case where the gate was
        # not active (delta_mean_reduction is None).
        _gate = self.config.delta_mean_reduction
        confirmed_results["statistical_dmp"] = True
        confirmed_results["biological_dmp"] = False
        if _gate is not None and "delta_mean" in confirmed_results.columns:
            confirmed_results = confirmed_results[
                np.abs(confirmed_results["delta_mean"].astype(float)) >= float(_gate)
            ].copy()
        logger.info(
            "Context %s: %s positions after statistical + delta_mean filter%s",
            context,
            f"{len(confirmed_results):,}",
            "" if _gate is None else f" (|delta_mean| >= {_gate})",
        )

        use_ks_views = (
            self.config.significance_test == "ks_ecdf"
            and ecdf_view_1_full is not None
            and ecdf_view_2_full is not None
        )
        frames = [
            self._finalize_candidate_metrics_df(
                confirmed_results,
                centroid1=centroid1,
                centroid2=centroid2,
                context=context,
                ecdf_view1=ecdf_view_1_full if use_ks_views else None,
                ecdf_view2=ecdf_view_2_full if use_ks_views else None,
                view_row_indices=confirmed_results.index.to_numpy(dtype=np.intp) if use_ks_views else None,
            )
        ]

        rescue_coverage = self.config.biological_only_effect_size_coverage
        if rescue_coverage is not None:
            rescue_candidates = comparison_results[comparison_results["q_value"] > self.config.alpha].copy()
            if _gate is not None and "delta_mean" in rescue_candidates.columns:
                rescue_candidates = rescue_candidates[
                    np.abs(rescue_candidates["delta_mean"].astype(float)) >= float(_gate)
                ].copy()
            max_candidates = self.config.biological_only_max_candidates
            if max_candidates is not None and len(rescue_candidates) > max_candidates:
                rescue_candidates = (
                    rescue_candidates
                    .sort_values("effect_size", ascending=False)
                    .head(int(max_candidates))
                    .copy()
                )
            rescue_candidates = _select_by_effect_coverage(rescue_candidates, float(rescue_coverage))
            rescue_candidates["statistical_dmp"] = False
            rescue_candidates["biological_dmp"] = True
            logger.info(
                "Context %s: selected %s biological-only rescue candidates at coverage=%.2f",
                context,
                f"{len(rescue_candidates):,}",
                float(rescue_coverage),
            )
            frames.append(
                self._finalize_candidate_metrics_df(
                    rescue_candidates,
                    centroid1=centroid1,
                    centroid2=centroid2,
                    context=context,
                    ecdf_view1=ecdf_view_1_full if use_ks_views else None,
                    ecdf_view2=ecdf_view_2_full if use_ks_views else None,
                    view_row_indices=rescue_candidates.index.to_numpy(dtype=np.intp) if use_ks_views else None,
                )
            )

        frames = [f for f in frames if f is not None and len(f) > 0]
        if not frames:
            return pd.DataFrame(), 0
        return pd.concat(frames, ignore_index=True), n_positions_compared

    def _apply_tau2_filter(self, comparison_results: pd.DataFrame, context: str) -> pd.DataFrame:
        """Optionally drop highly heterogeneous loci before any DMP selection."""
        tau2_threshold = self.config.max_tau2_for_dmp
        if tau2_threshold is None:
            return comparison_results
        required_cols = {"tau2_1", "tau2_2"}
        if not required_cols.issubset(comparison_results.columns):
            logger.warning("tau2 filter requested but tau2 columns are missing; skipping filter.")
            return comparison_results

        keep_mask = ~(
            (comparison_results["tau2_1"].astype(float) > float(tau2_threshold))
            & (comparison_results["tau2_2"].astype(float) > float(tau2_threshold))
        )
        dropped = int((~keep_mask).sum())
        if dropped > 0:
            logger.info(
                "Context %s: dropped %s positions with tau2_1 and tau2_2 both > %.4f",
                context,
                f"{dropped:,}",
                float(tau2_threshold),
            )
        return comparison_results.loc[keep_mask].copy()

    def _finalize_candidate_metrics_df(
        self,
        dmp_df: pd.DataFrame,
        centroid1: MethylSample,
        centroid2: MethylSample,
        context: str,
        ecdf_view1: Any = None,
        ecdf_view2: Any = None,
        view_row_indices: Optional[np.ndarray] = None,
    ) -> pd.DataFrame:
        """Compute continuous ECDF overlap/effect_size for the selected candidate set.

        When ecdf_view1, ecdf_view2 and view_row_indices are provided (ks_ecdf path),
        they are reused instead of building new views; view_row_indices gives the row
        indices into those views for each row of dmp_df.
        """
        if dmp_df is None or len(dmp_df) == 0:
            return dmp_df.copy() if isinstance(dmp_df, pd.DataFrame) else pd.DataFrame()

        dmp_df = dmp_df.copy()
        if ecdf_view1 is not None and ecdf_view2 is not None and view_row_indices is not None:
            logger.info(
                "Context %s: reusing ECDFViews for %s candidate positions (KS path)",
                context,
                f"{len(dmp_df):,}",
            )
        else:
            dmp_positions = np.asarray(dmp_df["position"].values, dtype=np.uint32)
            pos1 = np.asarray(centroid1.pos.values, dtype=np.uint32)
            pos2 = np.asarray(centroid2.pos.values, dtype=np.uint32)
            idx_in_c1 = np.searchsorted(pos1, dmp_positions, side="left")
            idx_in_c2 = np.searchsorted(pos2, dmp_positions, side="left")
            bs1 = centroid1.binned_stats
            bs2 = centroid2.binned_stats
            bin_edges_arr = np.asarray(bs1["bin_edges"], dtype=np.float64)
            from methyl_utils.core.distribution_views import ECDFView

            ecdf_view1 = ECDFView(
                bin_edges_arr,
                np.asarray(bs1["bin_counts"], dtype=np.float64)[idx_in_c1],
                np.asarray(centroid1.Sx.values, dtype=np.float64)[idx_in_c1],
                np.asarray(centroid1.N.values, dtype=np.float64)[idx_in_c1],
                np.asarray(centroid1.Sx2.values, dtype=np.float64)[idx_in_c1],
            )
            ecdf_view2 = ECDFView(
                bin_edges_arr,
                np.asarray(bs2["bin_counts"], dtype=np.float64)[idx_in_c2],
                np.asarray(centroid2.Sx.values, dtype=np.float64)[idx_in_c2],
                np.asarray(centroid2.N.values, dtype=np.float64)[idx_in_c2],
                np.asarray(centroid2.Sx2.values, dtype=np.float64)[idx_in_c2],
            )
            view_row_indices = None
            logger.info(
                "Context %s: built ECDFViews for %s candidate positions (lazy, not full centroid)",
                context,
                f"{len(dmp_positions):,}",
            )

        dmp_df = self._compute_missing_metrics_df(
            dmp_df,
            ecdf_view1=ecdf_view1,
            ecdf_view2=ecdf_view2,
            view_row_indices=view_row_indices,
        )
        if "effect_size" in dmp_df.columns and len(dmp_df) > 0:
            from scipy.stats import rankdata

            bes = dmp_df["effect_size"].values.astype(np.float64)
            ranks = rankdata(bes)
            dmp_df["effect_size_ecdf"] = (ranks - 0.5) / len(ranks)

        dmp_df["chromosome"] = self.chromosome
        dmp_df["context"] = context
        return dmp_df

    def _compute_context_weights(self, dmps_df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute trimmed-mean context weights and add to DataFrame.
        
        Uses trimmed mean (removing top and bottom percentiles) to compute
        robust average effect size per context, then normalizes to sum=1.
        
        Args:
            dmps_df: DataFrame with 'context' and 'effect_size' columns
            
        Returns:
            DataFrame with added 'context_weight' column
        """
        if 'effect_size' not in dmps_df.columns:
            raise ValueError("DataFrame must have 'effect_size' column for context weighting")
        weight_map = {}

        # Compute trimmed mean per context using effect_size
        for context, group in dmps_df.groupby('context'):
            S = group['effect_size'].values
            
            # Calculate asymmetric trimmed percentiles
            # Remove more from bottom (low effect sizes) and less from top (high effect sizes are important)
            qlo = self.config.trimmed_percentile_low
            qhi = 1.0 - self.config.trimmed_percentile_high
            q_low, q_high = np.quantile(S, [qlo, qhi])
            
            # Keep only trimmed values
            S_trimmed = S[(S >= q_low) & (S <= q_high)]
            
            # Compute mean (fallback to full mean if trimmed is empty)
            if len(S_trimmed) > 0:
                w_c = S_trimmed.mean()
            else:
                w_c = S.mean() if len(S) > 0 else 0.0
            
            weight_map[context] = w_c

        mean_effect_per_context = weight_map.copy()
        # Optional: inverse weighting so contexts with inflated effect_size (e.g. CHH) get lower weight
        if self.config.context_weight_direction == "inverse":
            eps = 1e-8
            raw_weights = {k: 1.0 / (v + eps) for k, v in weight_map.items()}
        else:
            raw_weights = weight_map.copy()

        # Normalize weights to sum=1
        total_weight = sum(raw_weights.values())
        if total_weight > 0:
            weight_map = {k: v / total_weight for k, v in raw_weights.items()}
        else:
            n_contexts = len(weight_map)
            weight_map = {k: 1.0 / n_contexts for k in weight_map.keys()}
            logger.warning("All context weights are zero, using equal weights")

        # Log summary table: Mean_EffectSize = trimmed mean effect_size; Weight = final context weight
        logger.info("")
        logger.info("="*60)
        logger.info("Context Weighting Summary (statistical DMPs, before biological filter):")
        logger.info("="*60)
        logger.info(f"{'Context':<10} {'N_stat':>10} {'Mean_EffectSize':>16} {'Weight':>10}")
        logger.info("-" * 50)
        for ctx in sorted(weight_map.keys()):
            n_dmps = len(dmps_df[dmps_df['context'] == ctx])
            logger.info(f"{ctx:<10} {n_dmps:>10,} {mean_effect_per_context[ctx]:>16.4f} {weight_map[ctx]:>10.4f}")
        logger.info("-" * 50)
        logger.info(
            "N_stat = statistical DMPs (q ≤ α). Weights from trimmed mean effect_size (bottom %.0f%%, top %.0f%%); direction=%s.",
            self.config.trimmed_percentile_low * 100, self.config.trimmed_percentile_high * 100,
            self.config.context_weight_direction,
        )
        logger.info("="*60)
        logger.info("")
        
        # Map weights to DataFrame
        dmps_df['context_weight'] = dmps_df['context'].map(weight_map)
        
        return dmps_df
    
    def _log_dmp_filter_funnel(
        self,
        total_positions_tested: int,
        total_statistical: int,
        total_biological: int,
    ) -> None:
        """Log the DMP filter funnel with enter / pass / retention for each filter step."""
        def _pct(n: int, d: int) -> str:
            return f"{100.0 * n / d:.1f}%" if d and d > 0 else "N/A"

        logger.info("")
        logger.info("")
        logger.info("################################################################################")
        logger.info("#            DMP FILTER FUNNEL — Enter / Pass / Retention by step             #")
        logger.info("################################################################################")
        logger.info("")
        if self.config.delta_mean_reduction is not None:
            logger.info("  Step 1  Pre-filter:   |delta_mean| >= %.2f  (see per-context logs for enter/pass)", self.config.delta_mean_reduction)
        else:
            logger.info("  Step 1  Pre-filter:   (none)")
        logger.info("")
        # Step 2: statistical
        ret2 = _pct(total_statistical, total_positions_tested)
        logger.info("  Step 2  Statistical:  Entered %s  →  Passed %s  →  Retention %s  (q ≤ α = %.2f)",
                    f"{total_positions_tested:,}", f"{total_statistical:,}", ret2, self.config.alpha)
        logger.info("")
        logger.info("  Step 3  Weights:      (no filter — trimmed mean of effect_size per context for downstream use)")
        logger.info("")
        # Step 4: biological
        ret4 = _pct(total_biological, total_statistical)
        logger.info("  Step 4  Biological:   Entered %s  →  Passed %s  →  Retention %s  (effect_size_coverage = %.2f)",
                    f"{total_statistical:,}", f"{total_biological:,}", ret4, self.config.effect_size_coverage)
        if total_biological == 0 and total_statistical > 0:
            logger.warning("  →  No biological DMPs retained. Consider lowering effect_size_coverage or relaxing the biological filter.")
        logger.info("")
        logger.info("################################################################################")
        logger.info("")
        logger.info("")

    def _filter_biological_dmps(self, dmps_df: pd.DataFrame) -> pd.DataFrame:
        """
        Biological filter: per-context ECDF cumulative mass selection.

        Within each context, sort by effect_size descending and keep the minimum
        set of positions whose effects sum to >= effect_size_coverage fraction of
        total effect mass for that context.
        """
        initial_count = len(dmps_df)
        initial_confirmed = int(dmps_df["statistical_dmp"].sum()) if "statistical_dmp" in dmps_df.columns else initial_count
        coverage = self.config.effect_size_coverage
        _pct = lambda n, d: f"{n / d * 100:.1f}%" if d and d > 0 else "N/A"

        bio_df = _select_by_effect_coverage(dmps_df, coverage)

        selected_confirmed = int(bio_df["statistical_dmp"].sum()) if "statistical_dmp" in bio_df.columns else len(bio_df)
        selected_rescue = max(0, len(bio_df) - selected_confirmed)

        # Per-context: statistical → biological (clear funnel table)
        logger.info("  Per-context: statistical DMPs → biological DMPs (effect_size_coverage=%.2f):", coverage)
        if "context" in dmps_df.columns:
            logger.info("  %-8s %12s %12s %10s", "Context", "Statistical", "Biological", "Retained")
            logger.info("  %s", "-" * 46)
            for ctx in sorted(dmps_df["context"].unique()):
                n_in = int((dmps_df["context"] == ctx).sum())
                n_out = int((bio_df["context"] == ctx).sum()) if len(bio_df) > 0 else 0
                logger.info("  %-8s %12s %12s %10s", ctx, f"{n_in:,}", f"{n_out:,}", _pct(n_out, n_in))
            logger.info("  %s", "-" * 46)
        logger.info(
            "  Total: %s biological DMPs (%s confirmed statistical + %s rescue; confirmed retention: %s)",
            f"{len(bio_df):,}", f"{selected_confirmed:,}", f"{selected_rescue:,}",
            _pct(selected_confirmed, initial_confirmed),
        )

        # Value ranges for retained DMPs
        value_ranges = {}
        for col in ("delta_mean", "overlap", "effect_size"):
            if col in bio_df.columns and len(bio_df) > 0:
                ser = bio_df[col].astype(float)
                value_ranges[col] = {"min": float(ser.min()), "max": float(ser.max())}
                logger.info(
                    "  Retained DMPs %s: min=%.4f, max=%.4f",
                    col, value_ranges[col]["min"], value_ranges[col]["max"],
                )

        self._biological_filter_summary = {
            "thresholds": {"effect_size_coverage": coverage},
            "value_ranges": value_ranges,
        }
        return bio_df

    def _range_step_values(self, spec) -> List[float]:
        """Build [min, min+step, ...] up to max from a FilterFunnelRangeSpec (inclusive max)."""
        vals: List[float] = []
        x = spec.min
        while x <= spec.max + 1e-12:
            vals.append(round(x, 10))
            x += spec.step
        return vals

    def _run_filter_funnel_sweep(self, dmps_df: pd.DataFrame) -> None:
        """
        If filter_funnel_explore is set, sweep effect_size_coverage over a range and write
        filter_funnel-{chromosome}.csv (same output_dir as results-{chromosome}.json).
        CSV columns: n_statistical_dmps, effect_size_coverage, n_biological_dmps.
        Uses statistical DMPs already in memory; one run, no large DMP CSV.
        """
        explore = self.config.filter_funnel_explore
        if explore is None or self.config.output_dir is None:
            return
        if explore.effect_size_coverage is None:
            return

        n_statistical = len(dmps_df)
        csv_rows: List[Dict[str, Any]] = []
        csv_columns = ["n_statistical_dmps", "effect_size_coverage", "n_biological_dmps"]

        for v in self._range_step_values(explore.effect_size_coverage):
            n = len(_select_by_effect_coverage(dmps_df, v))
            csv_rows.append({
                "n_statistical_dmps": n_statistical,
                "effect_size_coverage": v,
                "n_biological_dmps": n,
            })

        out_path = Path(self.config.output_dir) / f"filter_funnel-{self.chromosome}.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        save_csv(csv_rows, out_path, csv_columns)
        logger.info(f"Filter funnel: wrote {len(csv_rows)} rows to {out_path}")

    def _aggregate_filter_funnel_csvs(self, chromosomes: List[str]) -> None:
        """
        Combine per-chromosome filter_funnel-{chrom}.csv files into filter_funnel.csv.

        Adds a leading ``chromosome`` column. Skips missing per-chrom files (e.g. failed
        iterations). No-op when filter_funnel_explore is disabled or output_dir is unset.
        """
        if self.config.filter_funnel_explore is None or self.config.output_dir is None:
            return
        out_dir = Path(self.config.output_dir)
        frames: List[pd.DataFrame] = []
        for chrom in chromosomes:
            per_chrom = out_dir / f"filter_funnel-{chrom}.csv"
            if not per_chrom.is_file():
                continue
            try:
                df = pd.read_csv(per_chrom)
            except Exception as exc:
                logger.warning(f"Filter funnel: could not read {per_chrom}: {exc}")
                continue
            if df.empty:
                continue
            df.insert(0, "chromosome", str(chrom))
            frames.append(df)
        if not frames:
            return
        combined = pd.concat(frames, ignore_index=True)
        combined["chromosome"] = combined["chromosome"].astype(str)
        agg_path = out_dir / "filter_funnel.csv"
        combined.to_csv(agg_path, index=False)
        logger.info(
            f"Filter funnel: aggregated {len(frames)} chromosome file(s), "
            f"{len(combined)} rows -> {agg_path}"
        )

    def _load_binned_counts_from_centroids(
        self,
        dmps_df: pd.DataFrame
    ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """
        Load per-position bin counts from centroid H5 files for given DMPs.

        Returns:
            (bin_edges, counts1, counts2) or None if not available.
        """
        from methyl_utils import MethylCentroidPair
        return MethylCentroidPair.load_binned_counts_from_centroids(
            dmps_df,
            self.config.centroid1_dir,
            self.config.centroid2_dir,
            self.chromosome,
        )
  
    def _compute_biological_importance(self, dmps_df: pd.DataFrame) -> pd.DataFrame:
        """
        Sort DMPs by effect_size (single biological importance measure).
        No separate importance column; effect_size is used everywhere downstream.
        """
        df = dmps_df.copy()
        if "effect_size" not in df.columns:
            raise ValueError("DMPs DataFrame must have 'effect_size'")
        df = df.sort_values("effect_size", ascending=False).reset_index(drop=True)
        return df

    def _min_samples_cohort_size(
        self,
        centroid: MethylSample,
        centroid_dir: Optional[str],
        centroid_name: str,
    ) -> int:
        """
        Number of samples in the cohort used to interpret ``min_samples_pct`` / ``min_samples_abs``.

        Prefer the resolved validation-sample list (explicit config paths or centroid metadata),
        then centroid metadata ``n_samples`` or ``sample_paths`` / ``samples_used`` length.
        Fall back to ``max(N)`` across positions (legacy; can underestimate cohort size when
        coverage is sparse) and finally a small default.
        """
        cdir = (str(centroid_dir).strip() if centroid_dir else "") or None
        class_cfg = getattr(self.config, f"{centroid_name}_validation_samples", None)
        if class_cfg is None:
            class_cfg = "use_metadata"
        paths = self._get_validation_samples(class_cfg, cdir, centroid_name)
        if paths:
            return max(1, len(paths))

        meta = getattr(centroid, "metadata", None) or {}
        ns = meta.get("n_samples")
        if ns is not None:
            try:
                return max(1, int(ns))
            except (TypeError, ValueError):
                pass
        raw = meta.get("sample_paths") or meta.get("samples_used")
        if isinstance(raw, (list, tuple)) and len(raw) > 0:
            return max(1, len(raw))

        if centroid.N is not None:
            nmax = int(np.max(np.asarray(centroid.N)))
            return max(1, nmax)
        return 10

    def _get_validation_samples(
        self,
        config_samples: Optional[Union[str, List[str]]],
        centroid_dir: Optional[str],
        centroid_name: str
    ) -> List[str]:
        """
        Get validation sample paths from config or centroid metadata.
        
        Args:
            config_samples: Config value - can be "use_metadata", a list of paths, or None
            centroid_dir: Directory containing centroids
            centroid_name: Name of centroid for logging (e.g., "centroid1")
            
        Returns:
            List of validation sample paths
        """
        from methyl_utils import MethylCentroidPair
        contexts = self.config.contexts if hasattr(self.config, 'contexts') else None
        return MethylCentroidPair.resolve_validation_samples(
            config_samples,
            centroid_dir,
            self.chromosome,
            contexts=contexts,
            centroid_name=centroid_name,
            fallback_samples_base_path=getattr(
                self.config, "validation_samples_base_path", None
            ),
        )
    
    def _load_validation_samples_multicontext(
        self,
        dmps_df: pd.DataFrame
    ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """
        Load validation samples for multi-context optimization.
        
        Requires samples from BOTH centroid1 and centroid2 so that Balanced Accuracy
        (classifying between the two groups) can be computed. If only one group has
        samples, returns None and the caller skips validation-driven optimization.
        
        Returns:
            Tuple of (X, y, positions, contexts) or None if loading fails
            - X: methylation matrix (n_samples x n_positions)
            - y: labels (0 for class1, 1 for class2)
            - positions: genomic positions
            - contexts: methylation contexts
        """
        try:
            # Resolve validation sample paths: explicit config, else centroid metadata (samples_used), else none
            class1_config = getattr(self.config, "centroid1_validation_samples", None)
            if class1_config is None:
                class1_config = "use_metadata"
            class2_config = getattr(self.config, "centroid2_validation_samples", None)
            if class2_config is None:
                class2_config = "use_metadata"

            class1_paths = self._get_validation_samples(
                class1_config,
                self.config.centroid1_dir,
                "centroid1"
            )
            class2_paths = self._get_validation_samples(
                class2_config,
                self.config.centroid2_dir,
                "centroid2"
            )
            
            if not class1_paths and not class2_paths:
                logger.warning("No real validation samples (config and centroid metadata).")
                return None

            # Balanced Accuracy requires both groups; we cannot classify between groups with only one.
            if not class1_paths or not class2_paths:
                logger.warning(
                    "Validation requires samples from BOTH centroid1 and centroid2 to compute Balanced Accuracy. "
                    "Got centroid1=%s, centroid2=%s." % (len(class1_paths), len(class2_paths))
                )
                return None
            
            logger.info(f"Loading {len(class1_paths)} healthy + {len(class2_paths)} cancer validation samples...")
            
            # Use common implementation
            val_data = self._load_validation_samples_multicontext_impl(dmps_df, class1_paths, class2_paths)
            return val_data
            
        except Exception as e:
            logger.error(f"Failed to load validation samples: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _check_centroid_self_classification(
        self,
        dmps_df: pd.DataFrame,
        *,
        check_name: Optional[str] = None,
    ) -> None:
        """
        Sanity check: classify each centroid's methylation profile at the DMP positions.
        Each centroid should get probability ~1.0 for its own class. If not, positions
        or sample↔DMP alignment may be wrong.
        Optionally uses only the top K DMPs by effect_size (centroid_self_check_top_k) to
        avoid low-information loci that can confuse the check.
        """
        if dmps_df is None or len(dmps_df) == 0:
            return
        try:
            if 'mean1' not in dmps_df.columns or 'mean2' not in dmps_df.columns:
                logger.debug("Skipping centroid self-check (no mean1/mean2 in DMP table)")
                return
            # Optionally restrict to top K by effect_size for a more stable check
            top_k = getattr(self.config, "centroid_self_check_top_k", None)
            label_prefix = f"[{check_name}] " if check_name else ""
            if top_k is not None and top_k > 0 and "effect_size" in dmps_df.columns:
                # Already sorted by effect_size desc from _compute_biological_importance; take first K
                check_df = dmps_df.head(int(top_k)).copy()
                if len(check_df) < len(dmps_df):
                    logger.info(
                        "%sCentroid self-check using top K=%s DMPs by effect_size (of %s biological), effect_size weights",
                        label_prefix,
                        len(check_df), len(dmps_df),
                    )
            else:
                check_df = dmps_df
            check_df = self._subset_dmps_to_both_centroids(check_df)
            if check_df.empty:
                logger.debug("Skipping centroid self-check (no DMPs after centroid intersection)")
                return
            # Use same effect_size-based weights as production so high-effect positions dominate;
            # uniform weights let many weak (cap-hit) positions dilute the mean and misclassify centroid2.
            weights = self._get_classifier_weights(check_df)
            dmpDF = pd.DataFrame({
                'pos': check_df['position'].values.astype(np.uint32),
                'weight': weights,
                'context': check_df['context'].values if 'context' in check_df.columns else None,
                'delta_sign': check_df['delta_sign'].values if 'delta_sign' in check_df.columns else None,
                'mean1': check_df['mean1'].values if 'mean1' in check_df.columns else None,
                'mean2': check_df['mean2'].values if 'mean2' in check_df.columns else None,
            })
            dmpDF = dmpDF.dropna(axis=1, how='all')
            bin_edges_sc, bc1_sc, bc2_sc = self._extract_bin_counts_for_dmps(check_df)
            clf = ECDFClassifier.from_dataframe(
                dmpDF,
                bin_edges=bin_edges_sc,
                bin_counts_c1=bc1_sc,
                bin_counts_c2=bc2_sc,
                temperature=self.config.temperature,
            )
            # Centroid1 profile = mean1 at each DMP (class 0); centroid2 = mean2 (class 1)
            profile_c1 = check_df['mean1'].values.astype(np.float64).reshape(1, -1)
            profile_c2 = check_df['mean2'].values.astype(np.float64).reshape(1, -1)
            profile_c1 = np.clip(profile_c1, 1e-6, 1.0 - 1e-6)
            profile_c2 = np.clip(profile_c2, 1e-6, 1.0 - 1e-6)
            avail = np.ones((1, len(check_df)), dtype=bool)
            debug = self.config.debug
            proba_c1 = clf.predict_proba(profile_c1, avail, debug=debug)[0]
            proba_c2 = clf.predict_proba(profile_c2, avail, debug=debug)[0]
            # P(class1): usually column 1; if classifier returns inverted (centroid1→high, centroid2→low), use column 0
            if proba_c1[1] > 0.5 and proba_c2[1] < 0.5:
                p_c1, p_c2 = proba_c1[0], proba_c2[0]
            else:
                p_c1, p_c2 = proba_c1[1], proba_c2[1]
            # Format probabilities with enough precision to distinguish true zeros from underflow (e.g. 1e-10)
            def _fmt_p(p: float) -> str:
                if p <= 0.0 or p >= 1.0:
                    return f"{p:.6g}"
                if p < 1e-4 or p > 1.0 - 1e-4:
                    return f"{p:.4e}"
                return f"{p:.4f}"
            logger.info(
                "%sCentroid self-check (DMP positions): centroid1 → P(class1)=%s, centroid2 → P(class1)=%s "
                "(expect ~0 and ~1)",
                label_prefix,
                _fmt_p(float(p_c1)), _fmt_p(float(p_c2))
            )
            # Fail if either centroid is on the wrong side of the decision boundary (0.5)
            if p_c1 >= 0.5 or p_c2 < 0.5:
                # Diagnostic: log weighted-mean log-likelihoods so we can see why class 2 loses
                try:
                    log_p1_c1, log_p1_c2, _ = clf.compute_log_pdf_matrices(profile_c1, avail)
                    log_p2_c1, log_p2_c2, _ = clf.compute_log_pdf_matrices(profile_c2, avail)
                    # Use same per-position cap as predict_proba so diagnostic matches classifier behaviour
                    cap = float(LOG_PDF_CAP)
                    log_p1_c1_cap = np.maximum(log_p1_c1[0], cap)
                    log_p1_c2_cap = np.maximum(log_p1_c2[0], cap)
                    log_p2_c1_cap = np.maximum(log_p2_c1[0], cap)
                    log_p2_c2_cap = np.maximum(log_p2_c2[0], cap)
                    w = clf.weights
                    n_dmps = len(w)
                    w_sum = np.maximum(np.sum(w), 1e-12)
                    mean_ll_c1_prof1 = float(np.sum(w * log_p1_c1_cap) / w_sum)
                    mean_ll_c2_prof1 = float(np.sum(w * log_p1_c2_cap) / w_sum)
                    mean_ll_c1_prof2 = float(np.sum(w * log_p2_c1_cap) / w_sum)
                    mean_ll_c2_prof2 = float(np.sum(w * log_p2_c2_cap) / w_sum)
                    logger.warning(
                        "%sCentroid self-check diagnostic (weighted-mean log-likelihood, %d DMPs, cap=%.1f): "
                        "centroid1 profile: class0=%.4f class1=%.4f; centroid2 profile: class0=%.4f class1=%.4f "
                        "(expect centroid1→class0>class1, centroid2→class1>class0).",
                        label_prefix,
                        n_dmps, cap, mean_ll_c1_prof1, mean_ll_c2_prof1, mean_ll_c1_prof2, mean_ll_c2_prof2,
                    )
                    # When centroid2 loses: log a few per-position log-PDFs (capped, as in classifier)
                    if mean_ll_c1_prof2 > mean_ll_c2_prof2:
                        try:
                            n_show = min(3, n_dmps)
                            idx_show = np.linspace(0, n_dmps - 1, n_show, dtype=int)
                            for i in idx_show:
                                m2 = float(profile_c2[0, i])
                                lp_c1 = float(np.maximum(log_p2_c1[0, i], cap))
                                lp_c2 = float(np.maximum(log_p2_c2[0, i], cap))
                                logger.warning(
                                    "  sample DMP %d: mean2=%.4f logPDF_c1(mean2)=%.4f logPDF_c2(mean2)=%.4f "
                                    "(centroid2 should win at its own mean if histograms are correct).",
                                    i, m2, lp_c1, lp_c2,
                                )
                        except Exception as _:
                            pass
                except Exception as diag_e:
                    logger.debug("Centroid self-check diagnostic failed: %s", diag_e)
                if p_c1 >= 0.5 and p_c2 >= 0.5:
                    logger.warning(
                        "%sCentroid self-check FAILED: both centroids classify as class1 (centroid1→%s, centroid2→%s). "
                        "Often due to poor centroid separation on this chromosome (see earlier 'Poor separation' / small delta_mean). "
                        "Validation BA may be low or meaningless.",
                        label_prefix,
                        _fmt_p(float(p_c1)), _fmt_p(float(p_c2))
                    )
                elif p_c1 < 0.5 and p_c2 < 0.5:
                    logger.warning(
                        "%sCentroid self-check FAILED: both centroids classify as class0 (centroid1→P(class1)=%s, centroid2→%s). "
                        "Context/position merging matches the DMP list, so this usually indicates weak centroid separation or "
                        "a DMP set dominated by low-information loci. Check delta_mean/effect_size and held-out BA.",
                        label_prefix,
                        _fmt_p(float(p_c1)), _fmt_p(float(p_c2))
                    )
                else:
                    logger.warning(
                        "%sCentroid self-check FAILED: centroid1 → P(class1)=%s, centroid2 → P(class1)=%s (expect ~0 and ~1). "
                        "Possible position/order mismatch between samples and DMP list. Validation BA may be meaningless.",
                        label_prefix,
                        _fmt_p(float(p_c1)), _fmt_p(float(p_c2))
                    )
        except Exception as e:
            logger.warning("Centroid self-check failed: %s", e)

    def _default_validation_result(self) -> dict:
        empty_tm = {
            'balanced_accuracy': 0.5,
            'confusion_matrix': {'tp': 0, 'tn': 0, 'fp': 0, 'fn': 0},
            'metrics': {'sensitivity': 0.0, 'specificity': 0.0, 'accuracy': 0.0, 'precision': 0.0},
            'counts': {'n_positive': 0, 'n_negative': 0, 'n_total': 0},
            'split_balanced_accuracy_std': 0.0,
            'n_splits': 0,
        }
        return {
            'balanced_accuracy': 0.5,
            'confusion_matrix': {'tp': 0, 'tn': 0, 'fp': 0, 'fn': 0},
            'metrics': {'sensitivity': 0.0, 'specificity': 0.0, 'accuracy': 0.0, 'precision': 0.0},
            'counts': {'n_positive': 0, 'n_negative': 0, 'n_total': 0},
            'split_balanced_accuracy_std': 0.0,
            'n_splits': 0,
            'training_metrics': dict(empty_tm),
        }

    @staticmethod
    def _binary_metrics_package(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, Any]:
        """Confusion-derived metrics for binary classification (rows = validation samples)."""
        y_true = np.asarray(y_true, dtype=int).ravel()
        y_pred = np.asarray(y_pred, dtype=int).ravel()
        tp = int(np.sum((y_pred == 1) & (y_true == 1)))
        tn = int(np.sum((y_pred == 0) & (y_true == 0)))
        fp = int(np.sum((y_pred == 1) & (y_true == 0)))
        fn = int(np.sum((y_pred == 0) & (y_true == 1)))
        n_pos = tp + fn
        n_neg = tn + fp
        sensitivity = tp / n_pos if n_pos > 0 else 0.0
        specificity = tn / n_neg if n_neg > 0 else 0.0
        total = tp + tn + fp + fn
        return {
            'balanced_accuracy': float((sensitivity + specificity) / 2.0),
            'confusion_matrix': {'tp': tp, 'tn': tn, 'fp': fp, 'fn': fn},
            'metrics': {
                'sensitivity': float(sensitivity),
                'specificity': float(specificity),
                'accuracy': float((tp + tn) / total) if total > 0 else 0.0,
                'precision': float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0,
            },
            'counts': {
                'n_positive': int(n_pos),
                'n_negative': int(n_neg),
                'n_total': int(total),
            },
        }

    def _merge_training_metrics_results(self, training_payloads: List[dict]) -> dict:
        """Aggregate training-fold metrics across repeated splits (same pattern as holdout merge)."""
        if not training_payloads:
            d = self._default_validation_result()
            return d['training_metrics']
        ba_values = [float(t.get('balanced_accuracy', 0.5)) for t in training_payloads]
        tp = sum(int(t['confusion_matrix']['tp']) for t in training_payloads)
        tn = sum(int(t['confusion_matrix']['tn']) for t in training_payloads)
        fp = sum(int(t['confusion_matrix']['fp']) for t in training_payloads)
        fn = sum(int(t['confusion_matrix']['fn']) for t in training_payloads)
        n_pos = tp + fn
        n_neg = tn + fp
        total = n_pos + n_neg
        sensitivity = tp / n_pos if n_pos > 0 else 0.0
        specificity = tn / n_neg if n_neg > 0 else 0.0
        return {
            'balanced_accuracy': float(np.mean(ba_values)),
            'confusion_matrix': {'tp': int(tp), 'tn': int(tn), 'fp': int(fp), 'fn': int(fn)},
            'metrics': {
                'sensitivity': float(sensitivity),
                'specificity': float(specificity),
                'accuracy': float((tp + tn) / total) if total > 0 else 0.0,
                'precision': float(tp / (tp + fp)) if (tp + fp) > 0 else 0.0,
            },
            'counts': {'n_positive': int(n_pos), 'n_negative': int(n_neg), 'n_total': int(total)},
            'split_balanced_accuracy_std': float(np.std(ba_values)) if len(ba_values) > 1 else 0.0,
            'n_splits': len(ba_values),
        }

    def _get_classifier_weights(self, dmps_df: pd.DataFrame) -> np.ndarray:
        """Extract bounded, positive feature weights from the selected DMP table."""
        if 'effect_size' in dmps_df.columns:
            weights = dmps_df['effect_size'].values.copy()
        else:
            weights = np.ones(len(dmps_df), dtype=np.float64)

        weights = np.asarray(weights, dtype=np.float64)
        if weights.size == 0:
            return weights
        if np.any(~np.isfinite(weights)) or np.any(weights <= 0):
            weights = np.where(np.isfinite(weights) & (weights > 0), weights, 1.0)
        w_max = float(np.max(weights))
        if w_max > 1e-6:
            weights = np.clip(weights / w_max, 1e-6, 1.0)
        else:
            weights = np.full(weights.shape, 1e-6, dtype=np.float64)
        # Weight concentration: raise to power so weak positions contribute less (resilient to 40K+ DMPs)
        power = float(getattr(self.config, "effect_size_weight_power", 1.0))
        if power != 1.0:
            weights = np.power(weights, power)
            weights = np.maximum(weights, 1e-6)
        return weights.astype(np.float64)

    def _build_ecdf_classifier(self, dmps_df: pd.DataFrame) -> Tuple[ECDFClassifier, pd.DataFrame]:
        """Build an ECDFClassifier and typed DMP frame for the given subset."""
        dmps_df = self._subset_dmps_to_both_centroids(dmps_df)
        if dmps_df.empty:
            raise ValueError(
                "No DMP rows remain after intersecting with centroid positions; "
                "check centroid1_dir/centroid2_dir and DMP coordinates."
            )
        weights = self._get_classifier_weights(dmps_df)
        dmpDF = pd.DataFrame({
            'pos': dmps_df['position'].values.astype(np.uint32),
            'weight': weights.astype(np.float64),
            'context': dmps_df['context'].values if 'context' in dmps_df.columns else None,
            'delta_sign': dmps_df['delta_sign'].values if 'delta_sign' in dmps_df.columns else None,
            'mean1': dmps_df['mean1'].values if 'mean1' in dmps_df.columns else None,
            'mean2': dmps_df['mean2'].values if 'mean2' in dmps_df.columns else None,
        }).dropna(axis=1, how='all')
        bin_edges, bc1, bc2 = self._extract_bin_counts_for_dmps(dmps_df)
        classifier = ECDFClassifier.from_dataframe(
            dmpDF,
            bin_edges=bin_edges,
            bin_counts_c1=bc1,
            bin_counts_c2=bc2,
            temperature=self.config.temperature,
        )
        return classifier, dmpDF

    def _prepare_validation_splits(
        self,
        y: np.ndarray,
        require_holdout: bool = True,
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Build repeated stratified holdout splits for balanced-accuracy evaluation.

        When validation_split_ratio is 0, use all data (no holdout): one split (idx_all, idx_all).
        When > 0, hold out that fraction for test in each repeat.

        Returns a list of `(calibration_indices, test_indices)` tuples.
        """
        y = np.asarray(y, dtype=int).ravel()
        n_total = len(y)
        if n_total == 0:
            return []

        split_ratio = float(self.config.validation_split_ratio or 0.0)
        idx_all = np.arange(n_total, dtype=np.int64)
        class0 = idx_all[y == 0]
        class1 = idx_all[y == 1]

        # Use all data when split_ratio is 0 (no holdout)
        if split_ratio <= 0.0:
            logger.info("Using all %s validation samples for optimization (validation_split_ratio=0, no holdout).", n_total)
            return [(idx_all, idx_all)]
        if len(class0) < 2 or len(class1) < 2:
            logger.warning(
                "Not enough samples for stratified holdout (class0=%s, class1=%s); using full cohort.",
                len(class0),
                len(class1),
            )
            return [(idx_all, idx_all)]

        n_test0 = min(len(class0) - 1, max(1, int(round(len(class0) * split_ratio))))
        n_test1 = min(len(class1) - 1, max(1, int(round(len(class1) * split_ratio))))
        if n_test0 <= 0 or n_test1 <= 0:
            return [(idx_all, idx_all)]

        n_repeats = max(int(self.config.validation_n_repeats or 1), 1)
        splits: List[Tuple[np.ndarray, np.ndarray]] = []
        base_seed = int(self.config.random_state or 42)
        for repeat_idx in range(n_repeats):
            rng = np.random.default_rng(base_seed + repeat_idx * 9973)
            test0 = rng.choice(class0, size=n_test0, replace=False)
            test1 = rng.choice(class1, size=n_test1, replace=False)
            test_idx = np.sort(np.concatenate([test0, test1]).astype(np.int64))
            calib_mask = np.ones(n_total, dtype=bool)
            calib_mask[test_idx] = False
            calib_idx = idx_all[calib_mask]
            splits.append((calib_idx, test_idx))
        return splits

    def _build_validation_prefix_cache(
        self,
        sorted_df: pd.DataFrame,
        X_val: np.ndarray,
        y_val: np.ndarray,
        splits: List[Tuple[np.ndarray, np.ndarray]],
    ) -> ValidationPrefixCache:
        """Cache weighted prefix log-likelihoods for very fast top-k evaluation."""
        weights = self._get_classifier_weights(sorted_df)
        classifier, _ = self._build_ecdf_classifier(sorted_df)
        availability_mask = ~np.isnan(X_val)
        log_p_c1, log_p_c2, _ = classifier.compute_log_pdf_matrices(
            X_val,
            availability_mask=availability_mask,
        )
        weighted_c1 = log_p_c1 * weights[np.newaxis, :]
        weighted_c2 = log_p_c2 * weights[np.newaxis, :]
        prefix_ll_c1: List[np.ndarray] = []
        prefix_ll_c2: List[np.ndarray] = []
        prefix_ll_c1_calib: List[np.ndarray] = []
        prefix_ll_c2_calib: List[np.ndarray] = []
        for calib_idx, test_idx in splits:
            prefix_ll_c1.append(np.cumsum(weighted_c1[test_idx], axis=1))
            prefix_ll_c2.append(np.cumsum(weighted_c2[test_idx], axis=1))
            prefix_ll_c1_calib.append(np.cumsum(weighted_c1[calib_idx], axis=1))
            prefix_ll_c2_calib.append(np.cumsum(weighted_c2[calib_idx], axis=1))
        return ValidationPrefixCache(
            sorted_df=sorted_df.copy(),
            weights=weights,
            y_val=np.asarray(y_val, dtype=int),
            splits=splits,
            prefix_ll_c1=prefix_ll_c1,
            prefix_ll_c2=prefix_ll_c2,
            prefix_ll_c1_calib=prefix_ll_c1_calib,
            prefix_ll_c2_calib=prefix_ll_c2_calib,
            temperature=self.config.temperature,
        )

    def _evaluate_prefix_subset(
        self,
        cache: ValidationPrefixCache,
        k: int,
    ) -> dict:
        """Evaluate the first `k` DMPs from a cached sorted table."""
        if cache is None or cache.sorted_df is None or len(cache.sorted_df) == 0 or k <= 0:
            return self._default_validation_result()

        k = min(int(k), len(cache.sorted_df))
        split_bas: List[float] = []
        tp = tn = fp = fn = 0
        split_bas_train: List[float] = []
        tp_tr = tn_tr = fp_tr = fn_tr = 0

        for (calib_idx, test_idx), prefix_c1, prefix_c2, pc1_cal, pc2_cal in zip(
            cache.splits,
            cache.prefix_ll_c1,
            cache.prefix_ll_c2,
            cache.prefix_ll_c1_calib,
            cache.prefix_ll_c2_calib,
        ):
            if prefix_c1.shape[1] < k or len(test_idx) == 0:
                continue
            log_likes = np.stack(
                [prefix_c1[:, k - 1], prefix_c2[:, k - 1]],
                axis=1,
            ) / max(cache.temperature, 0.1)
            log_likes -= log_likes.max(axis=1, keepdims=True)
            probs = np.exp(log_likes)
            probs /= probs.sum(axis=1, keepdims=True)
            y_test = cache.y_val[test_idx]
            y_pred = np.argmax(probs, axis=1)

            tp_i = int(np.sum((y_pred == 1) & (y_test == 1)))
            tn_i = int(np.sum((y_pred == 0) & (y_test == 0)))
            fp_i = int(np.sum((y_pred == 1) & (y_test == 0)))
            fn_i = int(np.sum((y_pred == 0) & (y_test == 1)))
            n_pos = tp_i + fn_i
            n_neg = tn_i + fp_i
            sensitivity = tp_i / n_pos if n_pos > 0 else 0.0
            specificity = tn_i / n_neg if n_neg > 0 else 0.0
            split_bas.append((sensitivity + specificity) / 2.0)

            tp += tp_i
            tn += tn_i
            fp += fp_i
            fn += fn_i

            same_fold = calib_idx.shape == test_idx.shape and (
                calib_idx is test_idx or np.array_equal(calib_idx, test_idx)
            )
            if same_fold:
                y_calib = y_test
                y_pred_calib = y_pred
            elif len(calib_idx) == 0:
                y_calib = None
            else:
                log_c = np.stack(
                    [pc1_cal[:, k - 1], pc2_cal[:, k - 1]],
                    axis=1,
                ) / max(cache.temperature, 0.1)
                log_c -= log_c.max(axis=1, keepdims=True)
                pr_c = np.exp(log_c)
                pr_c /= pr_c.sum(axis=1, keepdims=True)
                y_calib = cache.y_val[calib_idx]
                y_pred_calib = np.argmax(pr_c, axis=1)

            if y_calib is not None:
                ttp = int(np.sum((y_pred_calib == 1) & (y_calib == 1)))
                ttn = int(np.sum((y_pred_calib == 0) & (y_calib == 0)))
                tfp = int(np.sum((y_pred_calib == 1) & (y_calib == 0)))
                tfn = int(np.sum((y_pred_calib == 0) & (y_calib == 1)))
                np_tr = ttp + tfn
                nn_tr = ttn + tfp
                sen_tr = ttp / np_tr if np_tr > 0 else 0.0
                spe_tr = ttn / nn_tr if nn_tr > 0 else 0.0
                split_bas_train.append((sen_tr + spe_tr) / 2.0)
                tp_tr += ttp
                tn_tr += ttn
                fp_tr += tfp
                fn_tr += tfn

        if not split_bas:
            return self._default_validation_result()

        n_pos = tp + fn
        n_neg = tn + fp
        sensitivity = tp / n_pos if n_pos > 0 else 0.0
        specificity = tn / n_neg if n_neg > 0 else 0.0
        total = n_pos + n_neg

        if not split_bas_train:
            training_metrics = dict(self._default_validation_result()['training_metrics'])
        else:
            n_pos_tr = tp_tr + fn_tr
            n_neg_tr = tn_tr + fp_tr
            sensitivity_tr = tp_tr / n_pos_tr if n_pos_tr > 0 else 0.0
            specificity_tr = tn_tr / n_neg_tr if n_neg_tr > 0 else 0.0
            total_tr = n_pos_tr + n_neg_tr
            training_metrics = {
                'balanced_accuracy': float(np.mean(split_bas_train)),
                'confusion_matrix': {'tp': int(tp_tr), 'tn': int(tn_tr), 'fp': int(fp_tr), 'fn': int(fn_tr)},
                'metrics': {
                    'sensitivity': sensitivity_tr,
                    'specificity': specificity_tr,
                    'accuracy': (tp_tr + tn_tr) / total_tr if total_tr > 0 else 0.0,
                    'precision': tp_tr / (tp_tr + fp_tr) if (tp_tr + fp_tr) > 0 else 0.0,
                },
                'counts': {'n_positive': int(n_pos_tr), 'n_negative': int(n_neg_tr), 'n_total': int(total_tr)},
                'split_balanced_accuracy_std': float(np.std(split_bas_train)) if len(split_bas_train) > 1 else 0.0,
                'n_splits': len(split_bas_train),
            }
        return {
            'balanced_accuracy': float(np.mean(split_bas)),
            'confusion_matrix': {'tp': int(tp), 'tn': int(tn), 'fp': int(fp), 'fn': int(fn)},
            'metrics': {
                'sensitivity': sensitivity,
                'specificity': specificity,
                'accuracy': (tp + tn) / total if total > 0 else 0.0,
                'precision': tp / (tp + fp) if (tp + fp) > 0 else 0.0,
            },
            'counts': {'n_positive': int(n_pos), 'n_negative': int(n_neg), 'n_total': int(total)},
            'split_balanced_accuracy_std': float(np.std(split_bas)) if len(split_bas) > 1 else 0.0,
            'n_splits': len(split_bas),
            'training_metrics': training_metrics,
        }

    def _merge_validation_results(self, results: List[dict]) -> dict:
        """Aggregate repeated validation runs into a single summary dict."""
        valid_results = [r for r in results if r is not None]
        if not valid_results:
            return self._default_validation_result()

        ba_values = [float(r.get('balanced_accuracy', 0.5)) for r in valid_results]
        tp = sum(int(r['confusion_matrix']['tp']) for r in valid_results)
        tn = sum(int(r['confusion_matrix']['tn']) for r in valid_results)
        fp = sum(int(r['confusion_matrix']['fp']) for r in valid_results)
        fn = sum(int(r['confusion_matrix']['fn']) for r in valid_results)
        n_pos = tp + fn
        n_neg = tn + fp
        total = n_pos + n_neg
        merged = {
            'balanced_accuracy': float(np.mean(ba_values)),
            'confusion_matrix': {'tp': int(tp), 'tn': int(tn), 'fp': int(fp), 'fn': int(fn)},
            'metrics': {
                'sensitivity': tp / n_pos if n_pos > 0 else 0.0,
                'specificity': tn / n_neg if n_neg > 0 else 0.0,
                'accuracy': (tp + tn) / total if total > 0 else 0.0,
                'precision': tp / (tp + fp) if (tp + fp) > 0 else 0.0,
            },
            'counts': {'n_positive': int(n_pos), 'n_negative': int(n_neg), 'n_total': int(total)},
            'split_balanced_accuracy_std': float(np.std(ba_values)) if len(ba_values) > 1 else 0.0,
            'n_splits': len(ba_values),
        }
        for key in ('platt_calibrator', 'platt_calibrator_scaler'):
            for result in valid_results:
                if key in result:
                    merged[key] = result[key]
                    break
        training_parts = [
            r['training_metrics']
            for r in valid_results
            if isinstance(r.get('training_metrics'), dict)
        ]
        merged['training_metrics'] = self._merge_training_metrics_results(training_parts)
        return merged

    def _full_validation_merge_for_selected_panel(
        self,
        dmps_df: pd.DataFrame,
        X_val: np.ndarray,
        y_val: np.ndarray,
        val_positions: np.ndarray,
        val_contexts: np.ndarray,
        splits: List[Tuple[np.ndarray, np.ndarray]],
    ) -> Optional[dict]:
        """Run ECDF + optional Platt per split and merge (authoritative vs prefix-only FeatureCuts search)."""
        split_results: List[dict] = []
        for calib_indices, test_indices in splits:
            split_results.append(
                self._validate_classifier_subset(
                    dmps_df,
                    X_val[calib_indices],
                    y_val[calib_indices],
                    X_val[test_indices],
                    y_val[test_indices],
                    val_positions,
                    val_contexts,
                )
            )
        return self._merge_validation_results(split_results)

    def _validate_classifier_subset(
        self,
        dmps_subset: pd.DataFrame,
        X_calib: np.ndarray,
        y_calib: np.ndarray,
        X_test: np.ndarray,
        y_test: np.ndarray,
        all_positions: np.ndarray,
        all_contexts: np.ndarray
    ) -> dict:
        """
        Validate a DMP subset using the ECDFClassifier on calibration/test splits.
        
        Args:
            dmps_subset: Subset of DMPs to validate
            X_calib: Calibration methylation matrix (for Platt fitting)
            y_calib: Calibration labels
            X_test: Test methylation matrix (for evaluation)
            y_test: Test labels
            all_positions: All validation positions
            all_contexts: All validation contexts
            
        Returns:
            Dictionary with balanced accuracy and metrics
        """
        try:
            subset_positions = dmps_subset['position'].values
            subset_contexts = dmps_subset['context'].values

            feature_index_map = {
                (int(pos), str(ctx)): idx
                for idx, (pos, ctx) in enumerate(zip(all_positions, all_contexts))
            }
            subset_indices = np.asarray(
                [feature_index_map.get((int(pos), str(ctx)), -1) for pos, ctx in zip(subset_positions, subset_contexts)],
                dtype=np.int64,
            )
            matched_mask = subset_indices >= 0
            subset_indices = subset_indices[matched_mask]

            if len(subset_indices) == 0:
                logger.warning("No matching positions found in validation data")
                return self._default_validation_result()

            X_calib_subset = X_calib[:, subset_indices]
            X_test_subset = X_test[:, subset_indices]
            dmps_for_classifier = dmps_subset.loc[matched_mask].reset_index(drop=True)
            temp_classifier, _ = self._build_ecdf_classifier(dmps_for_classifier)
            logger.debug("ECDFClassifier built for validation: %s", temp_classifier)

            # PHASE 1: Fit Platt calibration using calibration set (if supported)
            X_calib_subset_clean = X_calib_subset.copy()
            X_calib_subset_clean = np.nan_to_num(X_calib_subset_clean, nan=0.5)
            X_calib_subset_clean = np.clip(X_calib_subset_clean, 1e-6, 1-1e-6)

            # Create availability mask
            calib_availability = ~np.isnan(X_calib_subset)

            use_calibration = False
            has_holdout = (
                X_calib_subset.shape[0] > 0
                and X_test_subset.shape[0] > 0
                and (
                    X_calib_subset.shape != X_test_subset.shape
                    or not np.array_equal(y_calib, y_test)
                )
            )
            if hasattr(temp_classifier, 'calibrate_platt') and self.config.enable_platt_calibration and has_holdout:
                try:
                    temp_classifier.calibrate_platt(X_calib_subset_clean, y_calib, calib_availability)
                    use_calibration = True
                except Exception as e:
                    logger.warning(f"Platt calibration failed: {e}, using uncalibrated predictions")
            
            # PHASE 2: Evaluate on held-out test set
            X_test_subset_clean = X_test_subset.copy()
            X_test_subset_clean = np.nan_to_num(X_test_subset_clean, nan=0.5)
            X_test_subset_clean = np.clip(X_test_subset_clean, 1e-6, 1-1e-6)
            test_availability = ~np.isnan(X_test_subset)

            # Detect no per-sample variation (causes degenerate probabilities and BA=0.5)
            n_test, n_feat = X_test_subset.shape
            frac_valid = np.sum(~np.isnan(X_test_subset)) / max(1, X_test_subset.size)
            # Rows identical after NaN->0.5 fill => classifier gets same input => same probability for all
            row_var_clean = np.var(X_test_subset_clean, axis=1)
            all_rows_same = n_feat > 0 and n_test > 1 and np.all(row_var_clean < 1e-9)
            if frac_valid < 0.05 and not self._low_overlap_warned_once:
                self._low_overlap_warned_once = True
                logger.warning(
                    "Validation data has very low overlap with DMP positions: %.1f%% non-NaN. "
                    "Samples may not share positions with the DMP list (wrong chromosome/assay?). "
                    "Filled NaNs with 0.5 -> no discrimination -> BA≈0.5.",
                    frac_valid * 100
                )
            if all_rows_same and n_test > 1 and not self._no_variation_warned_once:
                self._no_variation_warned_once = True
                logger.warning(
                    "Validation test matrix has no per-sample variation (all rows nearly identical). "
                    "Classifier will output the same probability for every sample -> BA≈0.5. "
                    "Check that validation sample files contain the DMP positions for this chromosome/context."
                )

            logger.debug(
                f"Testing classifier with {len(dmps_for_classifier)} DMPs on {X_test_subset_clean.shape[0]} samples"
            )
            if use_calibration and hasattr(temp_classifier, 'predict_proba_calibrated') and temp_classifier.calibrator is not None:
                test_probas = temp_classifier.predict_proba_calibrated(X_test_subset_clean, test_availability)
            else:
                test_probas = temp_classifier.predict_proba(X_test_subset_clean, test_availability, debug=False)

            # Check if all probabilities are exactly 0.5
            all_class0_05 = np.allclose(test_probas[:, 0], 0.5, atol=1e-10)
            all_class1_05 = np.allclose(test_probas[:, 1], 0.5, atol=1e-10)
            if all_class0_05 and all_class1_05:
                logger.error("CRITICAL: All probabilities are exactly 0.500000 - classifier is not working!")
                # Try to get intermediate values from classifier
                try:
                    # Try to access internal state if possible
                    logger.error(f"Classifier temperature: {temp_classifier.temperature if hasattr(temp_classifier, 'temperature') else 'unknown'}")
                    logger.error(f"Classifier has calibrator: {temp_classifier.calibrator is not None}")
                except Exception:
                    pass
            
            # Debug: Log prediction statistics for first few k values
            if len(dmps_subset) <= 100:
                mean_prob = test_probas[:, 1].mean()
                std_prob = test_probas[:, 1].std()
                prob_range = test_probas[:, 1].max() - test_probas[:, 1].min()
                logger.debug(f"    k={len(dmps_subset):,}: probs mean={mean_prob:.4f}, std={std_prob:.4f}, range={prob_range:.4f}")
                
            # Warn if probabilities are completely degenerate (once per run)
            prob_range = test_probas[:, 1].max() - test_probas[:, 1].min()
            if prob_range < 0.01 and not self._degenerate_probs_warned_once:
                self._degenerate_probs_warned_once = True
                logger.warning(
                    "Degenerate probabilities (range=%.6f, mean=%.6f) -> BA≈0.5. See 'low overlap' / 'no per-sample variation' above.",
                    prob_range, test_probas[:, 1].mean()
                )
            
            y_pred = np.argmax(test_probas, axis=1)
            test_pkg = self._binary_metrics_package(y_test, y_pred)
            balanced_accuracy = test_pkg['balanced_accuracy']
            n_pos = test_pkg['counts']['n_positive']
            n_neg = test_pkg['counts']['n_negative']

            xc = np.nan_to_num(X_calib_subset, nan=0.5)
            xt = np.nan_to_num(X_test_subset, nan=0.5)
            same_fold = (
                xc.shape == xt.shape
                and np.array_equal(y_calib, y_test)
                and np.array_equal(xc, xt)
            )
            if same_fold:
                train_pkg = dict(test_pkg)
                y_pred_calib = y_pred
            else:
                if use_calibration and hasattr(temp_classifier, 'predict_proba_calibrated') and temp_classifier.calibrator is not None:
                    calib_probas = temp_classifier.predict_proba_calibrated(X_calib_subset_clean, calib_availability)
                else:
                    calib_probas = temp_classifier.predict_proba(X_calib_subset_clean, calib_availability, debug=False)
                y_pred_calib = np.argmax(calib_probas, axis=1)
                train_pkg = self._binary_metrics_package(y_calib, y_pred_calib)

            # Diagnose BA≈0.5 (coin toss): usually means classifier predicts one class only
            n_pred_0 = int(np.sum(y_pred == 0))
            n_pred_1 = int(np.sum(y_pred == 1))
            if 0.48 <= balanced_accuracy <= 0.52 and (n_pos > 0 and n_neg > 0):
                if not self._ba_05_warned_once:
                    self._ba_05_warned_once = True
                    logger.warning(
                        "BA≈0.5 (coin toss): classifier is predicting only one class. "
                        "Predicted: %s class0, %s class1 | True: %s class0, %s class1. "
                        "Possible causes: (1) validation data has no overlap with DMP positions (all NaN -> 0.5), "
                        "(2) validation columns vs DMP order mismatch, "
                        "(3) one dominant weight or low-information DMPs. See earlier 'no per-sample variation' / 'low overlap' warnings.",
                        n_pred_0, n_pred_1, n_neg, n_pos
                    )
                else:
                    logger.debug("BA≈0.5 again: pred %s/%s, true %s/%s", n_pred_0, n_pred_1, n_neg, n_pos)

            if len(dmps_subset) <= 100 and not same_fold:
                logger.debug(
                    "    training fold: k=%s, calib BA=%.4f (pred class0/class1=%s/%s)",
                    f"{len(dmps_subset):,}",
                    float(train_pkg['balanced_accuracy']),
                    int(np.sum(y_pred_calib == 0)),
                    int(np.sum(y_pred_calib == 1)),
                )

            # Holdout = generalization; training_metrics = calibration-fold (in-sample) fit
            result = {
                'balanced_accuracy': test_pkg['balanced_accuracy'],
                'confusion_matrix': test_pkg['confusion_matrix'],
                'metrics': test_pkg['metrics'],
                'counts': test_pkg['counts'],
                'split_balanced_accuracy_std': 0.0,
                'n_splits': 1,
                'training_metrics': {
                    'balanced_accuracy': train_pkg['balanced_accuracy'],
                    'confusion_matrix': train_pkg['confusion_matrix'],
                    'metrics': train_pkg['metrics'],
                    'counts': train_pkg['counts'],
                    'split_balanced_accuracy_std': 0.0,
                    'n_splits': 1,
                },
            }
            # Include fitted Platt calibrator for export when enabled (so MethylClassifier can use it)
            if use_calibration and self.config.enable_platt_calibration and temp_classifier.calibrator is not None:
                import pickle
                result['platt_calibrator'] = pickle.dumps(temp_classifier.calibrator)
                if temp_classifier.calibrator_scaler is not None:
                    result['platt_calibrator_scaler'] = pickle.dumps(temp_classifier.calibrator_scaler)
            return result
            
        except Exception as e:
            logger.error(f"❌ Validation failed: {e}")
            import traceback
            traceback.print_exc()
            return self._default_validation_result()
    
    def _validate_centroid_parameters(self) -> None:
        """
        Log centroid summary: mean, variance, ECDF availability, and KS between ECDFs (sampled).
        Correct group classification is checked by the centroid self-check when building the classifier.
        """
        from pathlib import Path

        logger.info("🔍 Centroid summary (CG context)...")

        try:
            context = "CG"
            centroid1_path = Path(self.config.centroid1_dir) / f"{self.chromosome}-{context}.h5"
            centroid2_path = Path(self.config.centroid2_dir) / f"{self.chromosome}-{context}.h5"

            if not centroid1_path.exists() or not centroid2_path.exists():
                logger.debug("Skipping centroid summary: CG files not found")
                return

            centroid1 = MethylSample.load_from_h5(str(centroid1_path))
            centroid2 = MethylSample.load_from_h5(str(centroid2_path))
            try:
                results = MethylCentroidPair.validate_centroid_parameters(centroid1, centroid2)
            finally:
                if centroid1 is not None and hasattr(centroid1, "close"):
                    centroid1.close()
                if centroid2 is not None and hasattr(centroid2, "close"):
                    centroid2.close()

            if "error" in results:
                logger.warning(f"Centroid summary failed: {results['error']}")
                return

            c1, c2 = results["centroid1"], results["centroid2"]
            logger.info("Centroid1: %s positions, N = %.1f ± %.1f", c1["n_positions"], c1["sample_stats"]["mean_N"], c1["sample_stats"]["std_N"])
            logger.info("  mean (median) = %.4f, variance (median) = %.6f, ECDF = %s", c1["mean_median"], c1["variance_median"], "yes" if c1["has_ecdf"] else "no")
            logger.info("Centroid2: %s positions, N = %.1f ± %.1f", c2["n_positions"], c2["sample_stats"]["mean_N"], c2["sample_stats"]["std_N"])
            logger.info("  mean (median) = %.4f, variance (median) = %.6f, ECDF = %s", c2["mean_median"], c2["variance_median"], "yes" if c2["has_ecdf"] else "no")
            logger.info("Trimmed mean |Δβ| (group separation) = %.4f", results["group_separation"])

            # KS between ECDFs on a sample of positions (low performance hit); use aligned centroids
            if c1["has_ecdf"] and c2["has_ecdf"]:
                try:
                    from methyl_utils.core.distribution_views import get_distribution_view
                    from methyl_utils.statistical_tests import ecdf_ks_statistic
                    cent1, cent2, _ = MethylCentroidPair.load_and_align(str(centroid1_path), str(centroid2_path), min_coverage=1)
                    n_pos = len(cent1)
                    sample_size = min(500, max(100, n_pos // 1000))
                    rng = np.random.default_rng(42)
                    position_indices = rng.choice(n_pos, size=sample_size, replace=False).astype(np.intp)
                    ecdf1 = get_distribution_view(cent1, "ecdf")
                    ecdf2 = get_distribution_view(cent2, "ecdf")
                    ks_sample = ecdf_ks_statistic(ecdf1, ecdf2, position_indices, grid_size=128)
                    logger.info("ECDF KS (sample of %s positions): median = %.4f, mean = %.4f", sample_size, float(np.median(ks_sample)), float(np.mean(ks_sample)))
                except Exception as e:
                    logger.debug("ECDF KS summary skipped: %s", e)

        except Exception as e:
            logger.debug("Centroid summary failed: %s", e)

    def _validate_selected_dmps(self, selected_dmps_df: pd.DataFrame, sorted_df: Optional[pd.DataFrame] = None) -> Optional[dict]:
        """
        Validate selected DMPs without optimization.
        
        Optionally validate classifier performance on the selected DMP set (e.g. for reporting).
        
        Args:
            selected_dmps_df: DataFrame with selected DMPs to validate
            sorted_df: Optional sorted DataFrame (for consistency, not used here)
            
        Returns:
            Validation results dict or None if validation fails
        """
        try:
            n_dmps = len(selected_dmps_df)
            if n_dmps == 0:
                logger.warning("Cannot validate: no DMPs selected")
                return None

            logger.info("📊 Loading validation samples...")
            validation_data = self._load_validation_samples_multicontext(selected_dmps_df)
            if validation_data is None:
                logger.warning("Failed to load validation samples")
                return None

            X_val, y_val, val_positions, val_contexts = validation_data
            logger.info(f"✅ Loaded {len(X_val)} validation samples with {len(val_positions)} positions")
            splits = self._prepare_validation_splits(y_val, require_holdout=True)
            split_results = []
            for calib_indices, test_indices in splits:
                split_results.append(
                    self._validate_classifier_subset(
                        selected_dmps_df,
                        X_val[calib_indices],
                        y_val[calib_indices],
                        X_val[test_indices],
                        y_val[test_indices],
                        val_positions,
                        val_contexts,
                    )
                )

            return self._merge_validation_results(split_results)
            
        except Exception as e:
            logger.error(f"Validation failed: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _effect_size_elbow_trim(self, sorted_df: pd.DataFrame, enabled: Optional[bool] = None) -> pd.DataFrame:
        """
        Trim sorted DMPs by effect_size distribution (dynamic elbow). Operates on a copy of rows.
        """
        if len(sorted_df) == 0:
            return sorted_df.copy()
        if enabled is None:
            enabled = bool(getattr(self.config, "dynamic_dmp_cutoff_enabled", True))
        sorted_df = sorted_df.copy()
        n_before = len(sorted_df)
        if not enabled or "effect_size" not in sorted_df.columns or n_before == 0:
            return sorted_df.reset_index(drop=True)

        es = sorted_df["effect_size"].values.astype(np.float64)
        finite = np.isfinite(es)
        if not np.any(finite):
            logger.warning("effect_size has no finite values; skipping dynamic distribution trim")
            return sorted_df.reset_index(drop=True)
        es_finite = es[finite]
        if len(es_finite) <= 10:
            return sorted_df.reset_index(drop=True)
        es_min = float(es_finite.min())
        es_max = float(es_finite.max())
        if es_max <= es_min:
            return sorted_df.reset_index(drop=True)
        y = (es_finite - es_min) / (es_max - es_min)
        x = np.linspace(0, 1, len(es_finite))
        distances = x**2 + y**2
        elbow_idx = int(np.argmin(distances))
        base_thresh = float(es_finite[elbow_idx])
        relaxation = float(getattr(self.config, "dynamic_dmp_cutoff_relaxation", 1.0))
        thresh = base_thresh * relaxation
        keep = (es >= thresh) & finite
        n_keep = int(np.sum(keep))
        if n_keep < n_before:
            sorted_df = sorted_df.loc[keep].copy().reset_index(drop=True)
            logger.info(
                "📋 Dynamic distribution trim (elbow=%.6g, rel=%.2g): kept %s DMPs (effect_size >= %.6g), dropped %s weak tail",
                base_thresh, relaxation, n_keep, thresh, n_before - n_keep,
            )
        else:
            sorted_df = sorted_df.copy().reset_index(drop=True)
        return sorted_df

    def _featurecuts_select_k(self, sorted_pool: pd.DataFrame) -> Tuple[Optional[pd.DataFrame], Optional[dict]]:
        """Pick top-k DMPs by validation balanced accuracy. Returns (subset_df, best_result) or (None, None)."""
        if sorted_pool is None or len(sorted_pool) == 0:
            return None, None
        try:
            validation_data = self._load_validation_samples_multicontext(sorted_pool)
            if validation_data is None:
                logger.warning("FeatureCuts: no validation samples; cannot optimize k by balanced accuracy")
                return None, None
            X_val, y_val, _val_positions, _val_contexts = validation_data
            splits = self._prepare_validation_splits(y_val, require_holdout=True)
            if not splits:
                logger.warning("FeatureCuts: no validation splits; cannot optimize k")
                return None, None
            cache = self._build_validation_prefix_cache(sorted_pool, X_val, y_val, splits)
            target_ba = getattr(self.config, "target_balanced_accuracy", None)
            if target_ba is not None:
                best_k = self._optimize_dmps_binary_search(
                    sorted_pool,
                    float(target_ba),
                    len(sorted_pool),
                    cache,
                )
                best_result = self._evaluate_prefix_subset(cache, best_k)
            else:
                best_k, best_result = self._optimize_dmps_featurecuts(sorted_pool, len(sorted_pool), cache)
            if best_k <= 0:
                return None, None
            sel = sorted_pool.iloc[:best_k].copy().reset_index(drop=True)
            try:
                refined = self._full_validation_merge_for_selected_panel(
                    sel, X_val, y_val, _val_positions, _val_contexts, splits
                )
                if refined is not None:
                    best_result = refined
            except Exception as e:
                logger.warning(
                    "FeatureCuts: full ECDF validation merge failed (%s); using prefix log-likelihood metrics",
                    e,
                )
            if target_ba is not None and bool(getattr(self.config, "fail_if_below_target", False)):
                try:
                    achieved = float((best_result or {}).get("balanced_accuracy", 0.0))
                except (TypeError, ValueError):
                    achieved = 0.0
                if achieved < float(target_ba):
                    logger.warning(
                        "FeatureCuts strict mode: BA %.4f below target %.4f — rejecting panel",
                        achieved,
                        float(target_ba),
                    )
                    raise ValueError(
                        f"FeatureCuts balanced accuracy {achieved:.4f} below target "
                        f"{float(target_ba):.4f}"
                    )
            return sel, best_result
        except ValueError:
            raise
        except Exception as e:
            logger.warning("FeatureCuts error: %s", e)
            return None, None

    def _classifier_dmps_from_sorted(self, sorted_by_importance_df: pd.DataFrame) -> pd.DataFrame:
        """
        Build the prediction-panel DMP table via FeatureCuts validation BA or legacy elbow trim.
        """
        selection = getattr(self.config, "classifier_dmp_selection", "elbow")
        if selection == "featurecuts_validation":
            pool = sorted_by_importance_df.copy().reset_index(drop=True)
            cap = getattr(self.config, "featurecuts_max_k_cap", None)
            if cap is not None and len(pool) > int(cap):
                pool = pool.iloc[: int(cap)].copy().reset_index(drop=True)
            selected, res = self._featurecuts_select_k(pool)
            if selected is None or len(selected) == 0:
                raise ValueError(
                    "FeatureCuts did not produce a classifier panel; "
                    "elbow fallback is disabled for classifier_dmp_selection=featurecuts_validation"
                )

            self._featurecuts_last_result = res
            selected_k_target = int(len(selected))
            final_k = selected_k_target

            min_core = self._resolve_min_core_dmps()
            strict = bool(getattr(self.config, "fail_if_below_target", False))
            if min_core is not None and len(selected) < int(min_core):
                if strict:
                    logger.warning(
                        "FeatureCuts strict mode: selected %s DMPs below min_core_dmps=%s — rejecting panel",
                        len(selected),
                        min_core,
                    )
                    raise ValueError(
                        f"FeatureCuts selected {len(selected)} DMPs below min_core_dmps={min_core}"
                    )
                logger.info(
                    "FeatureCuts selected %s but min_core_dmps=%s — expanding core classifier panel",
                    len(selected),
                    min_core,
                )
                selected = sorted_by_importance_df.iloc[: int(min_core)].copy().reset_index(drop=True)

            final_k = int(len(selected))
            ba_text = "n/a"
            if isinstance(res, dict):
                try:
                    ba_text = f"{float(res.get('balanced_accuracy')):.4f}"
                except (TypeError, ValueError):
                    ba_text = "n/a"
            self._classifier_panel_audit = {
                "k_target_ba": int(selected_k_target),
                "k_core": int(final_k),
                "balanced_accuracy_at_k_target": ba_text,
                "min_core_dmps": (int(min_core) if min_core is not None else None),
            }
            logger.info(
                "📋 Classifier panel audit (FeatureCuts): k_target_ba=%s (BA=%s), "
                "min_core_dmps=%s, k_core=%s",
                selected_k_target,
                ba_text,
                (str(min_core) if min_core is not None else "none"),
                final_k,
            )
            logger.info("📋 Classifier panel: FeatureCuts selected k=%s DMPs", final_k)

            selected_target_panel = pool.iloc[:selected_k_target].copy().reset_index(drop=True)
            self._check_centroid_self_classification(
                selected_target_panel,
                check_name=f"featurecuts-target-k={selected_k_target}",
            )
            self._check_centroid_self_classification(
                selected,
                check_name=f"featurecuts-final-k={final_k}",
            )
            return selected

        out = self._effect_size_elbow_trim(sorted_by_importance_df.copy(), enabled=True)
        self._classifier_panel_audit = {
            "k_target_ba": None,
            "k_effect_size": int(len(out)),
            "k_core": int(len(out)),
            "balanced_accuracy_at_k_target": None,
            "min_core_dmps": self._resolve_min_core_dmps(),
        }
        logger.info("📋 Classifier panel: %s DMPs after elbow trim", len(out))
        self._check_centroid_self_classification(out)
        return out

    def _resolve_min_core_dmps(self) -> Optional[int]:
        min_core = getattr(self.config, "min_core_dmps", None)
        if min_core is not None:
            return int(min_core)
        legacy = getattr(self.config, "min_selected_dmps", None)
        return int(legacy) if legacy is not None else None

    def _compute_extended_panel_k(self, k_core: int, pool_len: int) -> int:
        import math

        k_core = max(0, int(k_core))
        if k_core <= 0 or pool_len <= 0:
            return k_core
        k_extended = k_core
        margin_pct = float(getattr(self.config, "classifier_export_margin_pct", 0.0) or 0.0)
        margin_abs = int(getattr(self.config, "classifier_export_margin_abs", 0) or 0)
        if margin_pct > 0.0:
            k_from_pct = k_core + int(math.ceil(k_core * float(margin_pct)))
            k_extended = max(k_extended, k_from_pct)
        if margin_abs > 0:
            k_extended = max(k_extended, k_core + margin_abs)
        max_dmps = getattr(self.config, "classifier_export_max_dmps", None)
        if max_dmps is not None:
            k_extended = min(k_extended, int(max_dmps))
        k_extended = min(k_extended, int(pool_len))
        return max(k_extended, k_core)

    def _classifier_extended_dmps_from_core(
        self,
        sorted_by_importance_df: pd.DataFrame,
        core_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Annotation panel for mapper/gene stability: k_core plus a modest ranked margin."""
        k_core = int(len(core_df))
        if k_core <= 0:
            return core_df.copy()
        pool = sorted_by_importance_df.reset_index(drop=True)
        k_extended = self._compute_extended_panel_k(k_core, len(pool))
        if k_extended <= k_core:
            return core_df.copy().reset_index(drop=True)
        extended = pool.iloc[:k_extended].copy().reset_index(drop=True)
        audit = getattr(self, "_classifier_panel_audit", None) or {}
        audit = dict(audit)
        audit["k_extended"] = int(k_extended)
        self._classifier_panel_audit = audit
        logger.info(
            "📋 Classifier extended panel: k_core=%s → k_extended=%s (margin_pct=%s, margin_abs=%s, max=%s)",
            k_core,
            k_extended,
            getattr(self.config, "classifier_export_margin_pct", None),
            getattr(self.config, "classifier_export_margin_abs", None),
            getattr(self.config, "classifier_export_max_dmps", None),
        )
        return extended

    def run_dmp_panel_selection_from_discovery(
        self,
        sorted_by_importance_df: pd.DataFrame,
        *,
        export_classifier_pickle: bool = False,
    ) -> Dict[str, Any]:
        """
        Select classifier / extended DMP panels from a pre-ranked discovery pool.

        Used by methyl-dmp-select when detection runs in discovery_only mode.
        """
        sorted_by_importance_df = sorted_by_importance_df.copy().reset_index(drop=True)
        self._featurecuts_last_result = None
        self._classifier_panel_audit = None
        classifier_dmps_df = self._classifier_dmps_from_sorted(sorted_by_importance_df)
        extended_dmps_df = self._classifier_extended_dmps_from_core(
            sorted_by_importance_df,
            classifier_dmps_df,
        )
        if self.config.output_dir:
            self._export_unified_csv(classifier_dmps_df, suffix="-classifier")
            self._export_unified_csv(classifier_dmps_df, suffix="-selected")
            self._export_unified_csv(extended_dmps_df, suffix="-classifier-extended")
            discovery_dmps_df = self._discovery_dmps_from_sorted(sorted_by_importance_df)
            self._write_dmp_branch_metadata(
                discovery_dmps_df,
                classifier_dmps_df,
                extended_dmps_df,
            )
            if export_classifier_pickle:
                self._save_unified_model(None, classifier_dmps_df)
        fc_summary = None
        if getattr(self, "_featurecuts_last_result", None) is not None:
            r = self._featurecuts_last_result
            try:
                fc_summary = {
                    "balanced_accuracy": float(r.get("balanced_accuracy", 0.0)),
                    "split_balanced_accuracy_std": float(r.get("split_balanced_accuracy_std", 0.0)),
                }
            except (TypeError, ValueError):
                fc_summary = None
        return {
            "n_classifier": int(len(classifier_dmps_df)),
            "n_extended": int(len(extended_dmps_df)),
            "classifier_panel_audit": getattr(self, "_classifier_panel_audit", None),
            "featurecuts_summary": fc_summary,
        }

    def _discovery_dmps_from_sorted(self, sorted_by_importance_df: pd.DataFrame) -> pd.DataFrame:
        """
        Discovery branch: broad list for mapper/enricher (no centroid self-check on full set).
        """
        use_elbow = bool(getattr(self.config, "discovery_dynamic_dmp_cutoff_enabled", False))
        out = self._effect_size_elbow_trim(sorted_by_importance_df.copy(), enabled=use_elbow)
        logger.info("📋 Discovery export: %s DMPs (elbow=%s)", len(out), use_elbow)
        return out

    def _write_dmp_branch_metadata(
        self,
        discovery_dmps_df: pd.DataFrame,
        classifier_dmps_df: pd.DataFrame,
        extended_dmps_df: Optional[pd.DataFrame] = None,
    ) -> None:
        """Sidecar JSON describing dual-branch exports (methods / reproducibility)."""
        import json
        from datetime import datetime

        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        meta_path = output_dir / f"dmp-export-{self.chromosome}.meta.json"
        ctx_str = ",".join(sorted(self.config.contexts)) if self.config.contexts else "unknown"
        fc_summary = None
        if getattr(self, "_featurecuts_last_result", None) is not None:
            r = self._featurecuts_last_result
            try:
                fc_summary = {
                    "balanced_accuracy": float(r.get("balanced_accuracy", 0.0)),
                    "split_balanced_accuracy_std": float(r.get("split_balanced_accuracy_std", 0.0)),
                }
                tm = r.get("training_metrics")
                if isinstance(tm, dict) and int(tm.get("n_splits") or 0) > 0:
                    fc_summary["training_balanced_accuracy"] = float(tm.get("balanced_accuracy", 0.0))
            except (TypeError, ValueError):
                fc_summary = {"note": "featurecuts ran; see results-{chrom}.json for full metrics"}

        payload = {
            "chromosome": self.chromosome,
            "contexts": ctx_str,
            "timestamp": datetime.now().isoformat(),
            "dmp_export_mode": getattr(self.config, "dmp_export_mode", "dual"),
            "classifier_dmp_selection": getattr(self.config, "classifier_dmp_selection", "elbow"),
            "n_dmps_discovery": int(len(discovery_dmps_df)),
            "n_dmps_classifier": int(len(classifier_dmps_df)),
            "n_dmps_classifier_extended": int(len(extended_dmps_df))
            if extended_dmps_df is not None
            else int(len(classifier_dmps_df)),
            "classifier_panel_audit": getattr(self, "_classifier_panel_audit", None),
            "classifier_export_margin_pct": float(
                getattr(self.config, "classifier_export_margin_pct", 0.0) or 0.0
            ),
            "classifier_export_margin_abs": int(
                getattr(self.config, "classifier_export_margin_abs", 0) or 0
            ),
            "classifier_export_max_dmps": getattr(self.config, "classifier_export_max_dmps", None),
            "min_core_dmps": self._resolve_min_core_dmps(),
            "min_dmps_for_export": int(self.config.min_dmps_for_export),
            "discovery_dynamic_dmp_cutoff_enabled": bool(
                getattr(self.config, "discovery_dynamic_dmp_cutoff_enabled", False)
            ),
            "classifier_dynamic_dmp_cutoff_enabled": bool(getattr(self.config, "dynamic_dmp_cutoff_enabled", True)),
            "files": {
                "discovery_csv": f"dmps-{self.chromosome}-discovery.csv",
                "classifier_csv": f"dmps-{self.chromosome}-classifier.csv",
                "classifier_extended_csv": f"dmps-{self.chromosome}-classifier-extended.csv",
                "classifier_pickle": f"classifier-{self.chromosome}-{ctx_str}.pkl",
            },
            "featurecuts_validation_summary": fc_summary,
            "note": (
                "Use classifier-extended CSV for mapper/gene annotation; core classifier CSV + pickle for prediction; "
                "discovery CSV for broad biology."
            ),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        logger.info("💾 Wrote DMP branch metadata to %s", meta_path)

    def _select_dmps_multicontext(self, bio_dmps_df: pd.DataFrame, sorted_df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        Return the classifier (prediction) DMP panel: elbow and/or FeatureCuts.
        For dual exports, use _discovery_dmps_from_sorted on the same sorted table for mapper input.
        """
        if len(bio_dmps_df) == 0:
            return bio_dmps_df
        if sorted_df is None:
            sorted_df = self._compute_biological_importance(bio_dmps_df)
        return self._classifier_dmps_from_sorted(sorted_df)
    
    def _validate_on_real_samples(self, selected_dmps_df: pd.DataFrame) -> Optional[dict]:
        """
        Validate final model on real samples from centroid metadata.
        Used after synthetic optimization to verify real-world performance.
        
        Args:
            selected_dmps_df: Final selected DMPs
            
        Returns:
            Validation results dict or None if real samples not available
        """
        try:
            # Resolve real sample paths: config, else centroid metadata (samples_used)
            c1 = getattr(self.config, "centroid1_validation_samples", None)
            if c1 is None:
                c1 = "use_metadata"
            c2 = getattr(self.config, "centroid2_validation_samples", None)
            if c2 is None:
                c2 = "use_metadata"
            real_class1_paths = self._get_validation_samples(c1, self.config.centroid1_dir, "centroid1")
            real_class2_paths = self._get_validation_samples(c2, self.config.centroid2_dir, "centroid2")
            
            if not real_class1_paths and not real_class2_paths:
                logger.warning("No real samples available (config or centroid metadata) for verification")
                return None
            if not real_class1_paths or not real_class2_paths:
                logger.warning(
                    "Verification requires samples from both groups (got %s healthy, %s cancer). Skipping real verification.",
                    len(real_class1_paths), len(real_class2_paths)
                )
                return None
            
            logger.info(f"   Loading {len(real_class1_paths)} healthy + {len(real_class2_paths)} cancer samples for verification...")
            
            # Load real samples
            real_val_data = self._load_validation_samples_multicontext_impl(
                selected_dmps_df,
                real_class1_paths,
                real_class2_paths
            )
            
            if real_val_data is None:
                return None
            
            X_val, y_val, val_positions, val_contexts = real_val_data
            splits = self._prepare_validation_splits(y_val, require_holdout=True)
            if not splits:
                return None

            split_results = []
            for calib_idx, test_idx in splits:
                split_results.append(
                    self._validate_classifier_subset(
                        selected_dmps_df,
                        X_val[calib_idx],
                        y_val[calib_idx],
                        X_val[test_idx],
                        y_val[test_idx],
                        val_positions,
                        val_contexts,
                    )
                )
            return self._merge_validation_results(split_results)
            
        except Exception as e:
            logger.warning(f"Failed to validate on real samples: {e}")
            return None
    
    def _load_validation_samples_multicontext_impl(
        self,
        dmps_df: pd.DataFrame,
        class1_paths: List[str],
        class2_paths: List[str],
        allow_mock: bool = False
    ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
        """
        Implementation of validation sample loading (extracted for reuse).

        Uses MethylCentroidPair to properly align validation samples to centroid positions.
        """
        from methyl_utils import MethylCentroidPair

        if not class1_paths and not class2_paths:
            return None

        # Balanced Accuracy requires both groups; reject single-class validation
        if not class1_paths or not class2_paths:
            logger.warning(
                "Validation data must include samples from both centroid1 and centroid2 (got %s and %s). Skipping.",
                len(class1_paths), len(class2_paths)
            )
            return None

        # Extract positions and contexts from DMPs
        dmp_positions = dmps_df['position'].values
        dmp_contexts = dmps_df['context'].values

        # Create methylation matrix
        all_sample_paths = [(p, 0) for p in class1_paths] + [(p, 1) for p in class2_paths]
        n_samples = len(all_sample_paths)
        n_positions = len(dmp_positions)
        logger.info(f"Loading validation data for {n_positions} DMP positions across {len(np.unique(dmp_contexts))} contexts")
        X = np.full((n_samples, n_positions), np.nan)  # Initialize with NaN
        y = np.zeros(n_samples, dtype=int)
        
        # Group DMP positions by context for efficient processing
        reference_positions = {}
        context_groups = {}
        for ctx in np.unique(dmp_contexts):
            ctx_mask = dmp_contexts == ctx
            ctx_positions = dmp_positions[ctx_mask]
            reference_positions[ctx] = ctx_positions.astype(np.uint32)
            context_groups[ctx] = {
                'indices': np.where(ctx_mask)[0],
                'positions': ctx_positions
            }

        # Use MethylCentroidPair to efficiently extract methylation fractions
        # Use validation_min_coverage (e.g. 4) so we get values at more positions when centroids were built with higher min_coverage (e.g. 10)
        extraction_min = self.config.validation_min_coverage
        logger.info("Extracting methylation fractions using MethylCentroidPair (min_coverage=%s)...", extraction_min)
        sample_paths_list = [p for p, _ in all_sample_paths]
        X_extracted, all_positions_extracted, all_contexts_extracted, context_indices_dict = MethylCentroidPair.extract_methylation_fractions(
            sample_paths=sample_paths_list,
            reference_positions=reference_positions,
            chromosome=self.chromosome,
            min_coverage=extraction_min
        )
        
        # Map extracted (position, context) back to DMP column index so X columns match DMP row order.
        # Extraction uses the same context order as reference_positions (→ config/DMP list order);
        # this (pos, ctx) key mapping guarantees alignment with the classifier's feature order.
        dmp_key_to_idx = {
            (int(dmp_positions[i]), str(dmp_contexts[i])): i
            for i in range(n_positions)
        }
        extracted_to_dmp = np.asarray(
            [
                dmp_key_to_idx.get((int(pos), str(ctx)), -1)
                for pos, ctx in zip(all_positions_extracted, all_contexts_extracted)
            ],
            dtype=np.int32,
        )
        valid_cols = extracted_to_dmp >= 0
        if np.any(valid_cols):
            X[:, extracted_to_dmp[valid_cols]] = X_extracted[:, valid_cols]
        
        # Set labels
        for i, (_, label) in enumerate(all_sample_paths):
            y[i] = label
        
        successful_samples = np.sum(~np.isnan(X).all(axis=1))
        logger.info(f"Successfully loaded {successful_samples} out of {len(all_sample_paths)} validation samples")
        
        # If no samples loaded successfully, create mock validation data for testing alignment
        if successful_samples == 0 and allow_mock:
            logger.warning("No validation samples found, creating mock data to test alignment")
            # Create 10 mock samples (5 healthy, 5 cancer) with different methylation distributions
            mock_n_samples = 10
            np.random.seed(42)  # For reproducible results

            # Initialize with NaN (same as original approach) - n_positions should be the total DMP count
            logger.info(f"Creating mock data for {n_positions} total DMP positions")
            X = np.full((mock_n_samples, n_positions), np.nan)
            y = np.zeros(mock_n_samples, dtype=int)

            # Set labels: first 5 healthy (0), last 5 cancer (1)
            y[5:] = 1

            # Generate mock methylation data for each context
            for ctx in np.unique(dmp_contexts):
                ctx_mask = dmp_contexts == ctx
                ctx_indices = np.where(ctx_mask)[0]  # These are the indices in the full DMP array
                n_ctx_positions = len(ctx_indices)

                if n_ctx_positions > 0:
                    # Debug: check indices are in valid range
                    logger.debug(f"Context {ctx}: {n_ctx_positions} positions, indices {ctx_indices.min()}-{ctx_indices.max()}")

                    # Healthy samples: lower methylation for this context
                    X_healthy_ctx = np.random.uniform(0.2, 0.5, (5, n_ctx_positions)).astype(np.float32)
                    # Cancer samples: higher methylation for this context
                    X_cancer_ctx = np.random.uniform(0.5, 0.9, (5, n_ctx_positions)).astype(np.float32)

                    # Assign to the appropriate positions in the full matrix
                    X[:5, ctx_indices] = X_healthy_ctx  # Healthy samples
                    X[5:, ctx_indices] = X_cancer_ctx   # Cancer samples
                    logger.debug(f"Assigned mock data for context {ctx}")

            n_samples = mock_n_samples
            successful_samples = mock_n_samples
            logger.info(f"Created mock validation data: {mock_n_samples} samples with {n_positions} positions each")

        if successful_samples == 0:
            logger.error("No validation samples could be loaded and mock data creation failed")
            return None

        # Debug: Check how many positions have valid data
        n_valid_positions = np.sum(~np.isnan(X), axis=0)  # Count non-NaN per position
        positions_with_data = np.sum(n_valid_positions > 0)
        logger.info(f"Loaded validation data: X shape={X.shape}, y shape={y.shape}")
        logger.info(f"Position coverage: {positions_with_data}/{n_positions} positions have data in ≥1 sample")

        # Check per-sample coverage
        samples_with_data = []
        for i in range(n_samples):
            valid_positions = np.sum(~np.isnan(X[i, :]))
            samples_with_data.append(valid_positions)
            if i < 3:  # Log first few samples
                logger.debug(f"Sample {i}: {valid_positions}/{n_positions} positions with data")

        logger.debug(f"Sample coverage summary: min={min(samples_with_data)}, max={max(samples_with_data)}, mean={np.mean(samples_with_data):.1f}")

        # Confirm alignment: X column j corresponds to DMP row j (position=dmp_positions[j], context=dmp_contexts[j])
        if n_positions > 0:
            logger.info(
                "Validation columns aligned to DMP (position, context) order (join by position+context). "
                "First column: (pos, ctx)=(%s, %s), last: (%s, %s).",
                int(dmp_positions[0]), str(dmp_contexts[0]), int(dmp_positions[-1]), str(dmp_contexts[-1])
            )
        logger.info(f"Returning validation data: X.shape={X.shape}, y.shape={y.shape}, successful_samples={successful_samples}")
        return X, y, dmp_positions, dmp_contexts
    
    def _optimize_dmps_featurecuts(
        self,
        sorted_df: pd.DataFrame,
        max_k: int,
        prefix_cache: ValidationPrefixCache,
        initial_k: Optional[int] = None
    ) -> Tuple[int, dict]:
        """
        Maximize balanced accuracy across candidate top-k cutoffs.

        When exhaustive_search=True, performs a comprehensive search:
        1. Coarse phase: Evaluates diverse k values across the range
        2. Refinement phase: Densely samples around top performers
        
        When exhaustive_search=False, uses fast logarithmic sampling (~20 candidates).

        Args:
            sorted_df: DMPs sorted by biological importance.
            max_k: Maximum number of DMPs available.
            prefix_cache: Cached repeated-holdout ECDF validation state.
            initial_k: Optional heuristic starting point for exploration.

        Returns:
            Tuple of (best_k, validation_result).
        """
        if max_k <= 0:
            logger.warning("FeatureCuts received empty candidate set; returning k=0")
            empty_result = self._default_validation_result()
            return 0, empty_result

        min_k = 1 if max_k > 0 else 0
        exhaustive = self.config.featurecuts_exhaustive_search
        max_candidates = self.config.featurecuts_max_candidates

        logger.info(f"  FeatureCuts search range: k ∈ [{min_k:,}, {max_k:,}]")
        logger.info(f"  Exhaustive search: {exhaustive}")

        # Determine search strategy
        if exhaustive:
            # Exhaustive search: evaluate more candidates
            if max_candidates is None:
                # Auto-determine: use linear sampling for small ranges, capped for large
                if max_k <= 1000:
                    # For small ranges, evaluate all or nearly all
                    max_candidates = min(500, max_k - min_k + 1)
                else:
                    # For large ranges, use more aggressive sampling
                    max_candidates = min(500, int(max_k * 0.1))
            else:
                max_candidates = min(max_candidates, max_k - min_k + 1)
            
            logger.info(f"  Exhaustive mode: evaluating up to {max_candidates} candidates")
        else:
            # Fast mode: logarithmic sampling (~20 candidates)
            max_candidates = min(20, max_k - min_k + 1)
            logger.info(f"  Fast mode: evaluating {max_candidates} candidates")

        # Phase 1: Coarse search
        if max_k <= 50:
            # Small range: evaluate all
            candidate_k = np.arange(min_k, max_k + 1, dtype=np.int64)
        elif not exhaustive:
            # Fast mode: logarithmic sampling
            candidate_k = np.array([min_k, max_k], dtype=np.int64)
            if initial_k is not None:
                heuristic_k = np.clip(int(initial_k), min_k, max_k)
                candidate_k = np.append(candidate_k, heuristic_k)
            
            if max_k > min_k + 2:
                n_geom = max_candidates - candidate_k.size
                if n_geom > 0:
                    geom = np.geomspace(max(min_k, 1), max_k, n_geom)
                    candidate_k = np.append(candidate_k, geom.astype(np.int64))
        else:
            # Exhaustive mode: more comprehensive initial sampling
            # Start with boundary points and heuristic
            candidate_k = np.array([min_k, max_k], dtype=np.int64)
            if initial_k is not None:
                heuristic_k = np.clip(int(initial_k), min_k, max_k)
                candidate_k = np.append(candidate_k, heuristic_k)
            
            # Add logarithmic sampling for broad coverage
            n_log = min(50, max_candidates // 4)
            if max_k > min_k + 2 and n_log > 0:
                geom = np.geomspace(max(min_k, 1), max_k, n_log)
                candidate_k = np.append(candidate_k, geom.astype(np.int64))
            
            # Add linear sampling in the lower range (often where optimal k is)
            # Sample more densely in first 30% of range
            lower_bound = min_k
            upper_bound = int(min_k + (max_k - min_k) * 0.3)
            if upper_bound > lower_bound:
                n_linear = min(100, max_candidates // 2)
                linear_k = np.linspace(lower_bound, upper_bound, n_linear, dtype=np.int64)
                candidate_k = np.append(candidate_k, linear_k)
            
            # Add some linear sampling in mid-range
            mid_lower = int(min_k + (max_k - min_k) * 0.3)
            mid_upper = int(min_k + (max_k - min_k) * 0.7)
            if mid_upper > mid_lower:
                n_mid = min(50, max_candidates // 4)
                mid_k = np.linspace(mid_lower, mid_upper, n_mid, dtype=np.int64)
                candidate_k = np.append(candidate_k, mid_k)

        candidate_k = np.unique(np.clip(candidate_k, min_k, max_k))
        # Limit to max_candidates if we exceeded it
        if len(candidate_k) > max_candidates:
            # Keep boundaries and heuristic, then evenly sample the rest
            important = np.array([min_k, max_k])
            if initial_k is not None:
                important = np.append(important, np.clip(int(initial_k), min_k, max_k))
            important = np.unique(important)
            
            remaining_slots = max_candidates - len(important)
            if remaining_slots > 0:
                other_k = np.setdiff1d(candidate_k, important)
                if len(other_k) > remaining_slots:
                    # Evenly sample from remaining
                    indices = np.linspace(0, len(other_k) - 1, remaining_slots, dtype=np.int64)
                    sampled = other_k[indices]
                else:
                    sampled = other_k
                candidate_k = np.unique(np.concatenate([important, sampled]))
        
        candidate_k = np.sort(candidate_k)
        n_candidates = candidate_k.size
        
        ba_results = np.empty(n_candidates, dtype=np.float64)
        detailed_results = []
        
        logger.info(f"  Phase 1 (coarse): Evaluating {n_candidates} candidate k values...")
        
        # Track when BA=1.0 is achieved to optimize search
        ba_1_0_achieved_at_k = None  # Minimum k where BA=1.0 was achieved
        
        for i in range(n_candidates):
            k = int(candidate_k[i])
            
            # Optimization: Skip candidates >= k where BA=1.0 was already achieved
            # Since we want minimum k with BA=1.0, testing larger k values is wasteful
            if ba_1_0_achieved_at_k is not None and k >= ba_1_0_achieved_at_k:
                logger.info(f"    [{i+1}/{n_candidates}] k={k:,} → SKIPPED (BA=1.0 already achieved at k={ba_1_0_achieved_at_k:,})")
                # Fill with NaN or skip - we'll handle this later
                ba_results[i] = np.nan
                detailed_results.append(None)
                continue
            
            result = self._evaluate_prefix_subset(prefix_cache, k)
            ba_results[i] = result['balanced_accuracy']
            detailed_results.append(result)
            
            # Track first k where BA=1.0 is achieved
            if ba_1_0_achieved_at_k is None and np.isclose(result['balanced_accuracy'], 1.0, atol=1e-6):
                ba_1_0_achieved_at_k = k
                logger.info(f"    [{i+1}/{n_candidates}] k={k:,} → BA={ba_results[i]:.6f} ⭐ BA=1.0 achieved! Will skip larger k values.")
            
            if i % max(1, n_candidates // 5) == 0 or i == n_candidates - 1 or np.isclose(ba_results[i], 1.0, atol=1e-6):
                logger.info(f"    [{i+1}/{n_candidates}] k={k:,} → BA={ba_results[i]:.6f}")

        # Filter out skipped (NaN) results before Phase 2
        valid_mask = ~np.isnan(ba_results)
        if not np.all(valid_mask):
            # Remove skipped evaluations
            candidate_k = candidate_k[valid_mask]
            ba_results = ba_results[valid_mask]
            detailed_results = [r for r, valid in zip(detailed_results, valid_mask) if valid]
            n_candidates = len(candidate_k)
            logger.info(f"  Phase 1 complete: {n_candidates} valid evaluations (skipped {np.sum(~valid_mask)} redundant candidates)")
        
        # Phase 2: Refinement around top candidates (only if exhaustive)
        if exhaustive and n_candidates > 5:
            # Find top 5 candidates
            top_indices = np.argsort(-ba_results)[:5]
            top_k_values = candidate_k[top_indices]
            
            # Optimization: If BA=1.0 was achieved, only refine around candidates with BA=1.0
            # and only look at values <= the minimum k that achieved BA=1.0
            if ba_1_0_achieved_at_k is not None:
                # Find all candidates with BA=1.0
                ba_1_0_mask = np.isclose(ba_results, 1.0, atol=1e-6)
                ba_1_0_k_values = candidate_k[ba_1_0_mask]
                
                if len(ba_1_0_k_values) > 0:
                    # Only refine around candidates with BA=1.0, and only below/at the minimum k
                    min_ba_1_0_k = int(ba_1_0_k_values.min())
                    logger.info(f"  Refinement: BA=1.0 achieved at k={min_ba_1_0_k:,}, only refining k <= {min_ba_1_0_k:,}")
                    # Get smallest k values with BA=1.0 (up to 5) for refinement
                    sorted_ba_1_0_k = np.sort(ba_1_0_k_values)[:5]
                    top_k_values = sorted_ba_1_0_k
                    max_refinement_k = min_ba_1_0_k  # Cap refinement range
                else:
                    max_refinement_k = max_k
            else:
                max_refinement_k = max_k
            
            # Refine around each top candidate
            refinement_candidates = []
            for top_k in top_k_values:
                # Sample densely in a window around this top candidate
                window_size = max(10, int((max_refinement_k - min_k) * 0.05))
                window_min = max(min_k, int(top_k - window_size))
                window_max = min(max_refinement_k, int(top_k + window_size))  # Cap at max_refinement_k
                
                # Add 20 points in this window
                if window_max > window_min:
                    refined = np.linspace(window_min, window_max, 20, dtype=np.int64)
                    refinement_candidates.extend(refined.tolist())
            
            # Remove duplicates and values already evaluated
            refinement_candidates = np.array(refinement_candidates, dtype=np.int64)
            refinement_candidates = np.unique(np.clip(refinement_candidates, min_k, max_refinement_k))  # Use max_refinement_k instead of max_k
            refinement_candidates = refinement_candidates[~np.isin(refinement_candidates, candidate_k)]
            
            if len(refinement_candidates) > 0:
                logger.info(f"  Phase 2 (refinement): Evaluating {len(refinement_candidates)} additional candidates around top performers...")
                
                # Evaluate refinement candidates
                refinement_ba = np.empty(len(refinement_candidates), dtype=np.float64)
                refinement_results = []
                
                for i, k in enumerate(refinement_candidates):
                    # Optimization: Skip if BA=1.0 was achieved and k >= minimum k with BA=1.0
                    if ba_1_0_achieved_at_k is not None and k >= ba_1_0_achieved_at_k:
                        logger.info(f"    [{i+1}/{len(refinement_candidates)}] k={k:,} → SKIPPED (BA=1.0 already achieved at k={ba_1_0_achieved_at_k:,})")
                        refinement_ba[i] = np.nan
                        refinement_results.append(None)
                        continue
                    
                    result = self._evaluate_prefix_subset(prefix_cache, k)
                    refinement_ba[i] = result['balanced_accuracy']
                    refinement_results.append(result)
                    
                    # Update minimum k with BA=1.0 if we find a smaller one
                    if ba_1_0_achieved_at_k is None and np.isclose(result['balanced_accuracy'], 1.0, atol=1e-6):
                        ba_1_0_achieved_at_k = k
                        logger.info(f"    [{i+1}/{len(refinement_candidates)}] k={k:,} → BA={refinement_ba[i]:.6f} ⭐ BA=1.0 achieved!")
                    elif ba_1_0_achieved_at_k is not None and np.isclose(result['balanced_accuracy'], 1.0, atol=1e-6) and k < ba_1_0_achieved_at_k:
                        ba_1_0_achieved_at_k = k
                        logger.info(f"    [{i+1}/{len(refinement_candidates)}] k={k:,} → BA={refinement_ba[i]:.6f} ⭐ Found smaller k with BA=1.0!")
                    
                    if i % max(1, len(refinement_candidates) // 5) == 0 or i == len(refinement_candidates) - 1 or np.isclose(refinement_ba[i], 1.0, atol=1e-6):
                        logger.info(f"    [{i+1}/{len(refinement_candidates)}] k={k:,} → BA={refinement_ba[i]:.6f}")
                
                # Filter out skipped (NaN) refinement results
                valid_refinement_mask = ~np.isnan(refinement_ba)
                if not np.all(valid_refinement_mask):
                    refinement_candidates = refinement_candidates[valid_refinement_mask]
                    refinement_ba = refinement_ba[valid_refinement_mask]
                    refinement_results = [r for r, valid in zip(refinement_results, valid_refinement_mask) if valid]
                    logger.info(f"  Refinement: {len(refinement_candidates)} valid evaluations (skipped {np.sum(~valid_refinement_mask)} redundant candidates)")
                
                # Combine results
                candidate_k = np.concatenate([candidate_k, refinement_candidates])
                ba_results = np.concatenate([ba_results, refinement_ba])
                detailed_results.extend(refinement_results)
                
                # Re-sort by k for consistency
                sort_idx = np.argsort(candidate_k)
                candidate_k = candidate_k[sort_idx]
                ba_results = ba_results[sort_idx]
                detailed_results = [detailed_results[i] for i in sort_idx]
                
                n_candidates = len(candidate_k)
                logger.info(f"  Total evaluations: {n_candidates} (coarse + refinement)")

        # Find best k (minimal k achieving maximum BA)
        max_ba = ba_results.max()
        best_mask = np.isclose(ba_results, max_ba, atol=1e-6) | (ba_results == max_ba)
        best_indices = np.where(best_mask)[0]
        best_k_values = candidate_k[best_indices]
        best_k = int(best_k_values.min())
        best_result_idx = int(best_indices[best_k_values == best_k][0])
        best_result = detailed_results[best_result_idx]
        
        logger.info(f"  ✅ FeatureCuts complete: {len(candidate_k)} evaluations, max BA={max_ba:.6f} at k={best_k:,}")
        if np.isclose(max_ba, 0.5, atol=1e-4):
            logger.warning(
                "Optimization achieved BA≈0.5 (coin toss). Check that validation data has both classes in test set "
                "and that held-out samples overlap the DMP positions; consider trying optimization_method='binary_search' or 'bayesian_optimization'."
            )
        top5_indices = np.argsort(-ba_results)[:5]
        if top5_indices.size > 0:
            logger.debug("  Top 5 k candidates: " + ", ".join(f"k={int(candidate_k[i]):,}(BA={ba_results[i]:.3f})" for i in top5_indices))
        return best_k, best_result

    def _optimize_dmps_binary_search(
        self,
        sorted_df: pd.DataFrame,
        target_ba: float,
        max_k: int,
        prefix_cache: ValidationPrefixCache,
    ) -> int:
        """
        Logarithmic binary search to find minimal k achieving target balanced accuracy.
        Uses geometric spacing to efficiently explore the k-space, starting with small k values.
        Assumes monotonic BA increase with k (validated by our weighting fixes).
        """
        if max_k <= 1:
            return max_k

        # First, do a logarithmic exploration to find a reasonable starting point
        # Try geometric spacing: 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, etc.
        logger.info(f"🔍 Logarithmic binary search: exploring k-space up to {max_k:,} DMPs")

        # Find the largest power of 2 that's reasonable to start with
        max_power = int(np.log2(min(max_k, 8192)))  # Cap at 8192 to avoid too many evaluations
        geometric_k = [2**i for i in range(max_power + 1) if 2**i <= max_k]

        # Add some intermediate points for better coverage
        if max_k > 100:
            geometric_k.extend([int(max_k * 0.1), int(max_k * 0.25), int(max_k * 0.5)])
        geometric_k = sorted(list(set(geometric_k)))  # Remove duplicates and sort

        logger.info(f"📊 Testing geometric sequence: {geometric_k[:10]}{'...' if len(geometric_k) > 10 else ''}")

        best_k = max_k
        min_achieved_ba = 0.0

        # Test geometric points to find where BA starts to stabilize
        for k in geometric_k:
            result = self._evaluate_prefix_subset(prefix_cache, k)

            current_ba = result['balanced_accuracy']
            logger.info(f"  [geom] k={k:,} → BA={current_ba:.4f}")

            if current_ba >= target_ba:
                best_k = k
                break  # Found a k that achieves target - can refine from here
            elif current_ba > min_achieved_ba:
                min_achieved_ba = current_ba

        # If we didn't find a k that achieves target_ba, we need more DMPs
        # Do a focused binary search in the upper range
        if best_k == max_k:
            logger.info(f"⚠️  Target BA {target_ba:.3f} not achieved with geometric search, doing full binary search")
            left, right = geometric_k[-1] if geometric_k else 1, max_k
        else:
            # Found a k that works - search for minimal k in the lower range
            # Find the largest k in geometric_k that didn't achieve target
            failed_k = [k for k in geometric_k if k < best_k]
            left = failed_k[-1] if failed_k else 1
            right = best_k

        logger.info(f"🔍 Focused binary search: k ∈ [{left}, {right}], target BA ≥ {target_ba:.3f}")

        iterations = 0
        max_iterations = 8  # Limit iterations for focused search

        while left <= right and iterations < max_iterations:
            iterations += 1
            mid = (left + right) // 2

            # Test current k
            result = self._evaluate_prefix_subset(prefix_cache, mid)

            current_ba = result['balanced_accuracy']
            logger.info(f"  [{iterations}] k={mid:,} → BA={current_ba:.4f}")

            if current_ba >= target_ba:
                # Achieved target - try smaller k
                best_k = mid
                right = mid - 1
            else:
                # Need more DMPs
                left = mid + 1

        # Check if we achieved the target with the final best_k
        if best_k == max_k:
            # Test the maximum k to see what BA we actually achieve
            final_result = self._evaluate_prefix_subset(prefix_cache, max_k)
            actual_ba = final_result['balanced_accuracy']

            if actual_ba < target_ba:
                logger.warning(f"⚠️  Target BA {target_ba:.3f} not achievable (maximum BA = {actual_ba:.4f} with {max_k:,} DMPs)")
                logger.info("💡 Consider lowering target_balanced_accuracy in config for this dataset")

        logger.info(f"🔍 Binary search converged after {iterations + len(geometric_k)} total evaluations")
        return best_k

    def _optimize_dmps_bayesian(
        self,
        sorted_df: pd.DataFrame,
        max_k: int,
        prefix_cache: ValidationPrefixCache,
        initial_k: Optional[int] = None
    ) -> int:
        """
        Optimize DMP count using Bayesian Optimization with Gaussian Process surrogate.

        This implements BO using sklearn's GP regressor and Expected Improvement acquisition.
        More efficient than DE for non-monotonic functions, typically requiring 20-50 evaluations.

        Args:
            sorted_df: Sorted DMPs by importance
            max_k: Maximum k to consider
            prefix_cache: Cached repeated-holdout ECDF validation state
            initial_k: Optional heuristic starting point

        Returns:
            Optimal k value
        """
        import warnings
        from scipy.stats import norm
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C

        if max_k <= 0:
            logger.warning("Bayesian optimization received empty candidate set; returning k=0")
            return 0

        min_k = 1 if max_k > 0 else 0
        logger.info(f"  BO search range: k ∈ [{min_k:,}, {max_k:,}]")

        # Cache for performance evaluations
        evaluation_cache: Dict[int, float] = {}

        def objective_function(k: int) -> float:
            """Evaluate BA for given k (return negative BA for minimization)"""
            k = int(round(k))
            k = max(min_k, min(k, max_k))

            if k in evaluation_cache:
                return -evaluation_cache[k]

            result = self._evaluate_prefix_subset(prefix_cache, k)
            ba = result['balanced_accuracy']
            evaluation_cache[k] = ba

            return -ba  # Minimize negative BA = maximize BA

        # Initial evaluations: 8-12 diverse points (limited by available range)
        n_initial = min(10, max_k - min_k + 1)

        # Generate initial points
        initial_points: List[int] = []
        if initial_k is not None:
            heuristic = int(np.clip(initial_k, min_k, max_k))
            initial_points.append(heuristic)
            for offset in [-25, -10, 10, 25]:
                candidate = heuristic + offset
                if min_k <= candidate <= max_k:
                    initial_points.append(candidate)

        if not initial_points:
            initial_points.append(min_k)
        if max_k != min_k and max_k not in initial_points:
            initial_points.append(max_k)

        # Fill with random points for diversity where range allows
        while len(initial_points) < n_initial and max_k > min_k:
            candidate = np.random.randint(min_k, max_k + 1)
            if candidate not in initial_points:
                initial_points.append(candidate)

        initial_points = sorted(list(set(initial_points)))[:n_initial]

        # Evaluate initial points
        X_observed = np.array([[k] for k in initial_points])
        y_observed = np.array([objective_function(k) for k in initial_points])

        logger.info(f"  Initial BO evaluations: {len(initial_points)} points, best BA: {-np.min(y_observed):.6f}")

        # Bayesian Optimization loop: 15-25 iterations
        n_iterations = min(20, max_k - min_k)

        # Suppress GP kernel convergence warnings (bounds hitting is normal for BO)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning,
                                  message=".*optimal value found.*close to the specified.*bound.*")

            for iteration in range(n_iterations):
                # Fit GP surrogate model
                kernel = C(1.0, (1e-6, 1e6)) * RBF(10, (1e-3, 1e6))
                gp = GaussianProcessRegressor(
                    kernel=kernel,
                    alpha=1e-6,
                    normalize_y=True,
                    n_restarts_optimizer=3
                )
                gp.fit(X_observed, y_observed)

                # Expected Improvement acquisition function
                def expected_improvement(X):
                    X = X.reshape(-1, 1)
                    mu, sigma = gp.predict(X, return_std=True)
                    best_y = np.min(y_observed)

                    with np.errstate(divide='ignore', invalid='ignore'):
                        Z = (best_y - mu) / sigma
                        ei = (best_y - mu) * norm.cdf(Z) + sigma * norm.pdf(Z)
                        ei[sigma == 0.0] = 0.0

                    return ei

                # Evaluate EI at candidate points and select next evaluation
                candidate_points = np.linspace(min_k, max_k, min(100, max_k - min_k + 1)).reshape(-1, 1)
                ei_values = expected_improvement(candidate_points)

                best_idx = np.argmax(ei_values)
                next_k = int(candidate_points[best_idx, 0])

                # Evaluate objective and update observations
                next_y = objective_function(next_k)
                X_observed = np.vstack([X_observed, [[next_k]]])
                y_observed = np.append(y_observed, next_y)

                # Log progress every 5 iterations or when improvement found
                current_best_k = X_observed[np.argmin(y_observed), 0]
                current_best_ba = -np.min(y_observed)
                if iteration % 5 == 0 or -next_y > current_best_ba - 0.001:
                    logger.info(f"    BO iter {iteration+1}: k={next_k:,} → BA={-next_y:.6f}, best: k={int(current_best_k):,} BA={current_best_ba:.6f}")

        # Return best result
        best_idx = np.argmin(y_observed)
        optimal_k = int(X_observed[best_idx, 0])
        optimal_ba = -y_observed[best_idx]

        logger.info(f"  ✅ BO complete: k={optimal_k:,}, BA={optimal_ba:.6f}")
        logger.info(f"  BO stats: {len(y_observed)} evaluations ({n_initial} initial + {n_iterations} BO)")

        return optimal_k

    
    def _validation_block_to_pydantic(self, block: dict, type_label: str):
        """Map a merged validation dict (holdout or training_metrics) to ValidationResults."""
        from ..models import ValidationResults, PerformanceMetrics, ConfusionMatrix, SampleCounts

        return ValidationResults(
            type=type_label,
            performance=PerformanceMetrics(
                balanced_accuracy=float(block['balanced_accuracy']),
                sensitivity=float(block['metrics']['sensitivity']),
                specificity=float(block['metrics']['specificity']),
                precision=float(block['metrics']['precision']),
                accuracy=float(block['metrics']['accuracy']),
            ),
            confusion_matrix=ConfusionMatrix(**block['confusion_matrix']),
            sample_counts=SampleCounts(**block['counts']),
        )

    def _save_validation_results(
        self,
        n_dmps_exported: Optional[int] = None,
        total_statistical_dmps: Optional[int] = None,
        total_biological_dmps: Optional[int] = None,
    ):
        """Save validation results to JSON file using Pydantic model."""
        from datetime import datetime

        from ..models import (
            MethylDetectorValidationResults,
            ValidationResults,
            PerformanceMetrics,
            ConfusionMatrix,
            SampleCounts
        )

        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results_path = output_dir / f"results-{self.chromosome}.json"

        # Get config dict (Pydantic v2 mode='json' handles Path conversion)
        try:
            config_dict = self.config.model_dump(mode='json')
        except (TypeError, ValueError):
            # Fallback for older Pydantic versions
            config_dict = self.config.model_dump()

        # Create validation results objects
        optimization_validation = None
        training_fold_validation = None
        vtype = "real"
        if hasattr(self, '_final_validation_results') and self._final_validation_results:
            result = self._final_validation_results
            optimization_validation = self._validation_block_to_pydantic(result, vtype)
            tm = result.get('training_metrics')
            if isinstance(tm, dict) and int(tm.get('n_splits') or 0) > 0:
                training_fold_validation = self._validation_block_to_pydantic(tm, vtype)

        real_validation = None
        if hasattr(self, '_real_validation_results') and self._real_validation_results:
            result = self._real_validation_results
            real_validation = ValidationResults(
                type='real',
                performance=PerformanceMetrics(
                    balanced_accuracy=result['balanced_accuracy'],
                    sensitivity=result['metrics']['sensitivity'],
                    specificity=result['metrics']['specificity'],
                    precision=result['metrics']['precision'],
                    accuracy=result['metrics']['accuracy']
                ),
                confusion_matrix=ConfusionMatrix(**result['confusion_matrix']),
                sample_counts=SampleCounts(**result['counts'])
            )

        biological_filter = self._biological_filter_summary if hasattr(self, '_biological_filter_summary') else None

        # Create the main results object
        vsr = float(self.config.validation_split_ratio or 0.0)
        row_split_sem = (
            "stratified_holdout" if vsr > 0.0 else "no_row_holdout"
        )
        results = MethylDetectorValidationResults(
            chromosome=self.chromosome,
            timestamp=datetime.now().isoformat(),
            config=config_dict,
            optimization_validation=optimization_validation,
            training_fold_validation=training_fold_validation,
            real_validation=real_validation,
            n_dmps_exported=n_dmps_exported,
            total_statistical_dmps=total_statistical_dmps,
            total_biological_dmps=total_biological_dmps,
            biological_filter=biological_filter,
            validation_row_split_semantics=row_split_sem,
        )

        # Save to JSON using Pydantic's model_dump_json for proper serialization
        with open(results_path, 'w') as f:
            f.write(results.model_dump_json(indent=2))

        logger.info(f"💾 Saved results to {results_path}")

    def _compute_sample_size_estimate(self, df: pd.DataFrame):
        """Compute n per group to achieve target_power (two-sample t-test, Cohen's d). Returns Series, NaN where not computable."""
        import warnings
        need = ['mean1', 'mean2']
        if not all(c in df.columns for c in need):
            return pd.Series(index=df.index, dtype=np.float64)
        if 'combined_variance' in df.columns:
            combined_var = df['combined_variance'].values.astype(np.float64)
        elif 'variance1' in df.columns and 'variance2' in df.columns:
            combined_var = df['variance1'].values.astype(np.float64) + df['variance2'].values.astype(np.float64)
        else:
            return pd.Series(index=df.index, dtype=np.float64)
        delta = np.abs(df['mean1'].values.astype(np.float64) - df['mean2'].values.astype(np.float64))
        eps = 1e-12
        pooled_std = np.sqrt(np.maximum(combined_var, eps))
        cohens_d = np.where(pooled_std > 0, delta / pooled_std, np.nan)
        # Clip to range where statsmodels solve_power typically converges (avoids ConvergenceWarning)
        cohens_d = np.clip(cohens_d, 0.02, 10.0)
        alpha = self.config.alpha
        power = self.config.target_power
        try:
            from statsmodels.stats.power import TTestIndPower
            from statsmodels.tools.sm_exceptions import ConvergenceWarning as StatsmodelsConvergenceWarning
            tt = TTestIndPower()
            n_est = np.full(len(df), np.nan, dtype=np.float64)
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=StatsmodelsConvergenceWarning, module="statsmodels")
                for i in range(len(df)):
                    d = cohens_d[i]
                    if np.isfinite(d) and d >= 0.02:
                        try:
                            n_est[i] = tt.solve_power(effect_size=d, alpha=alpha, power=power, nobs1=None, ratio=1.0)
                        except Exception:
                            pass
            return pd.Series(n_est, index=df.index)
        except ImportError:
            logger.warning("statsmodels not available; skipping n_estimated_per_group. Install with: pip install statsmodels")
            return pd.Series(index=df.index, dtype=np.float64)
    
    def _export_unified_csv(self, bio_dmps_df: pd.DataFrame, suffix: str = "") -> Path:
        """
        Export unified CSV with all contexts combined.
        
        Args:
            bio_dmps_df: DataFrame with biological DMPs
            suffix: Optional suffix to add to filename (e.g., "-1-biological")
            
        Returns:
            Path to exported CSV file
        """
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        if suffix:
            csv_path = output_dir / f"dmps-{self.chromosome}{suffix}.csv"
        else:
            csv_path = output_dir / f"dmps-{self.chromosome}.csv"
        
        # Standard column order for biologists: identity, sample counts, means/variances, overlap, effect, stats, distribution
        STANDARD_EXPORT_COLS = [
            'chromosome', 'context', 'position',
            'n1', 'n2', 'mean1', 'mean2', 'variance1', 'variance2',
            'overlap', 'delta_mean', 'delta_sign', 'effect_size',
            'p_value', 'q_value', 'dist', 'dist_name',
        ]
        DIST_NAMES = {5: 'ECDF'}
        # Optional / distribution-specific columns
        EXTRA_EXPORT_COLS = [
            'context_weight',
            'combined_variance', 'n_estimated_per_group',
            # Preserve stability-tier recurrence metadata when fixed_dmp_panel
            # rows provide it, so downstream mappers can weight by frequency.
            'frequency', 'count', 'n_runs', 'combined_score', 'log_combined_score',
        ]
        export_cols = STANDARD_EXPORT_COLS + [c for c in EXTRA_EXPORT_COLS if c not in STANDARD_EXPORT_COLS]

        export_df = bio_dmps_df.copy()
        if 'dist' in export_df.columns:
            export_df['dist_name'] = export_df['dist'].map(DIST_NAMES).fillna('Unknown').astype(str)
        if 'delta_sign' not in export_df.columns and 'mean1' in export_df.columns and 'mean2' in export_df.columns:
            export_df['delta_sign'] = np.sign(export_df['mean1'] - export_df['mean2']).astype(np.int8)
        if self.config.export_sample_size_estimate:
            export_df['n_estimated_per_group'] = self._compute_sample_size_estimate(export_df)
        available_cols = [c for c in export_cols if c in export_df.columns]
        
        # Export to CSV
        export_df[available_cols].to_csv(csv_path, index=False)
        
        logger.info(f"📁 Exported {len(bio_dmps_df):,} DMPs to {csv_path}")
        logger.info(f"📊 Columns: {', '.join(available_cols)}")
        
        # Log per-context counts
        if 'context' in bio_dmps_df.columns:
            context_counts = bio_dmps_df.groupby('context').size()
            for ctx, count in context_counts.items():
                logger.info(f"  {ctx}: {count:,} DMPs")
        
        return csv_path
    
    def _save_unified_model(self, classifier, selected_dmps_df: pd.DataFrame):
        """
        Save a unified ECDFClassifier model with strongly-typed dmpDF.
        
        Args:
            classifier: Classifier instance (ignored, we rebuild the ECDFClassifier from dmpDF)
            selected_dmps_df: DataFrame with selected DMPs (final DMPs used by classifier)
        """
        if selected_dmps_df is None or len(selected_dmps_df) == 0:
            logger.warning("No selected DMPs; skipping classifier model save")
            return
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        ctx_str = ",".join(sorted(self.config.contexts)) if self.config.contexts else "unknown"
        model_path = output_dir / f"classifier-{self.chromosome}-{ctx_str}.pkl"
        
        try:
            the_classifier, dmpDF = self._build_ecdf_classifier(selected_dmps_df)
        except Exception as exc:
            logger.error("Failed to build ECDFClassifier for model save: %s", exc)
            return
        classifier_label = "ECDFClassifier"

        # Create model package
        import pickle
        context_weights_summary: Dict[str, float] = {}
        if "context" in selected_dmps_df.columns:
            if "context_weight" in selected_dmps_df.columns:
                context_weights_summary = (
                    selected_dmps_df.groupby("context")["context_weight"].first().to_dict()
                )
            else:
                unique_contexts = [str(c) for c in selected_dmps_df["context"].dropna().unique()]
                if unique_contexts:
                    default_w = 1.0 / float(len(unique_contexts))
                    context_weights_summary = {ctx: default_w for ctx in unique_contexts}
        model_package = {
            'classifier': the_classifier,
            'dmpDF': dmpDF,  # Strongly typed DataFrame
            'context_weights_summary': context_weights_summary,
            'chromosome': self.chromosome,
            'n_dmps': len(selected_dmps_df),
            'n_dmps_per_context': selected_dmps_df.groupby('context').size().to_dict() if 'context' in selected_dmps_df.columns else {},
            'metadata': {
                'version': '2.0.0',
                'classifier_type': classifier_label,
                'context': ctx_str,
                'config': self.config.model_dump(),
                'trimmed_percentile_low': self.config.trimmed_percentile_low,
                'trimmed_percentile_high': self.config.trimmed_percentile_high,
            }
        }

        # Include fitted Platt calibrator when enabled (MethylClassifier can use it for better-calibrated probabilities)
        if self.config.enable_platt_calibration and self._platt_calibrator_bytes is not None:
            model_package["metadata"]["platt_calibrator"] = self._platt_calibrator_bytes
            if self._platt_calibrator_scaler_bytes is not None:
                model_package["metadata"]["platt_calibrator_scaler"] = self._platt_calibrator_scaler_bytes
            logger.info("  - Platt calibrator included (enable_platt_calibration=True)")

        derived_schema = self._build_derived_measures_schema(selected_dmps_df, dmpDF)
        if derived_schema:
            model_package["derived_measures_schema"] = derived_schema
            logger.info(
                "  - Derived measures schema: %s features (fingerprint=%s)",
                len(derived_schema.get("feature_names") or []),
                derived_schema.get("feature_order_fingerprint"),
            )
        
        # Save to pickle
        with open(model_path, 'wb') as f:
            pickle.dump(model_package, f)
        
        logger.info(f"💾 Saved model to {model_path}")
        logger.info("📦 Model package includes:")
        logger.info(f"  - Classifier: {the_classifier}")
        logger.info(f"  - dmpDF: {len(dmpDF)} DMPs (strongly typed)")
        logger.info(f"  - Context weights: {model_package['context_weights_summary']}")
        logger.info(f"  - Total DMPs: {model_package['n_dmps']}")
        logger.info(f"  - DMPs per context: {model_package['n_dmps_per_context']}")
        if 'effect_size' in selected_dmps_df.columns:
            logger.info(f"  - Effect size range: {selected_dmps_df['effect_size'].min():.4f} to {selected_dmps_df['effect_size'].max():.4f}")

    def _build_derived_measures_schema(
        self,
        selected_dmps_df: pd.DataFrame,
        dmpDF: pd.DataFrame,
    ) -> Optional[Dict[str, Any]]:
        """Persist effect_size-weighted panel derived-measures schema for blind predict."""
        try:
            from methyl_validation.chromosome_features import build_ecdf_derived_measures_schema
        except ImportError:
            logger.debug("methyl_validation not available; skipping derived_measures_schema")
            return None
        weights = self._get_classifier_weights(selected_dmps_df)
        locus_df = selected_dmps_df.copy()
        if "chromosome" not in locus_df.columns:
            locus_df["chromosome"] = str(self.chromosome)
        healthy_label = str(getattr(self.config, "control_group", None) or "healthy")
        cancer_label = str(getattr(self.config, "disease_group", None) or "cancer")
        centroid_dirs = {
            healthy_label: str(self.config.centroid1_dir),
            cancer_label: str(self.config.centroid2_dir),
        }
        return build_ecdf_derived_measures_schema(
            locus_df,
            effect_size_weights=weights,
            class_labels=[healthy_label, cancer_label],
            healthy_class_label=healthy_label,
            centroid_dir_by_class_label=centroid_dirs,
        )

    def _get_or_load_centroid_bin_cache(self, chrom: Any, ctx: Any) -> Optional[dict]:
        """Load and cache binned_stats + positions for one chromosome×context pair, or return None."""
        cache_key = (str(chrom), str(ctx))
        cached = self._centroid_bin_cache.get(cache_key)
        if cached is not None:
            return cached

        c1_path = Path(self.config.centroid1_dir) / f"{chrom}-{ctx}.h5"
        c2_path = Path(self.config.centroid2_dir) / f"{chrom}-{ctx}.h5"

        if not c1_path.exists() or not c2_path.exists():
            logger.warning(
                "_get_or_load_centroid_bin_cache: centroid files not found for %s-%s",
                chrom,
                ctx,
            )
            return None

        c1 = MethylSample.load_from_h5(c1_path)
        c2 = MethylSample.load_from_h5(c2_path)
        try:
            bs1 = c1.binned_stats if c1 else None
            bs2 = c2.binned_stats if c2 else None
            if not bs1 or "bin_counts" not in bs1 or not bs2 or "bin_counts" not in bs2:
                logger.warning(
                    "_get_or_load_centroid_bin_cache: binned_stats missing for %s-%s",
                    chrom,
                    ctx,
                )
                return None

            be1 = np.asarray(bs1["bin_edges"], dtype=np.float64)
            be2 = np.asarray(bs2["bin_edges"], dtype=np.float64) if "bin_edges" in bs2 else None
            if be2 is not None and (be2.shape != be1.shape or not np.allclose(be2, be1)):
                logger.warning(
                    "_get_or_load_centroid_bin_cache: %s-%s centroid2 bin_edges differ from centroid1; "
                    "class 2 PDFs may be wrong (centroid self-check can fail). Build both centroids with the same bins.",
                    chrom,
                    ctx,
                )
            cached = {
                "bin_edges": be1,
                "pos1": np.asarray(c1.pos.values, dtype=np.uint32),
                "pos2": np.asarray(c2.pos.values, dtype=np.uint32),
                "bc1": np.asarray(bs1["bin_counts"], dtype=np.float64),
                "bc2": np.asarray(bs2["bin_counts"], dtype=np.float64),
            }
            self._centroid_bin_cache[cache_key] = cached
            return cached
        finally:
            if c1 is not None and hasattr(c1, "close"):
                c1.close()
            if c2 is not None and hasattr(c2, "close"):
                c2.close()

    def _subset_dmps_to_both_centroids(
        self,
        dmps_df: pd.DataFrame,
        *,
        log_drops: bool = True,
    ) -> pd.DataFrame:
        """
        Keep rows whose (chromosome, context, position) exists in both centroid H5 position arrays.

        External fixed panels (e.g. stability union) can include CpGs that were masked or absent
        when the current centroids were built; the ECDF classifier is only defined on the
        intersection.
        """
        if dmps_df is None or dmps_df.empty:
            return dmps_df

        work = dmps_df.copy()
        dmp_positions = np.asarray(work["position"].values, dtype=np.uint32)
        chroms = (
            work["chromosome"].values
            if "chromosome" in work.columns
            else np.full(len(work), self.chromosome, dtype=object)
        )
        contexts = (
            work["context"].values
            if "context" in work.columns
            else np.full(len(work), self.config.contexts[0], dtype=object)
        )

        keep = np.zeros(len(work), dtype=bool)
        unique_pairs = list(dict.fromkeys(zip(chroms, contexts)))
        for chrom, ctx in unique_pairs:
            mask = (chroms == chrom) & (contexts == ctx)
            if not np.any(mask):
                continue
            cached = self._get_or_load_centroid_bin_cache(chrom, ctx)
            if cached is None:
                continue

            pos1 = cached["pos1"]
            pos2 = cached["pos2"]
            gp = np.asarray(dmp_positions[mask], dtype=np.uint32)

            idx1 = np.searchsorted(pos1, gp, side="left")
            idx2 = np.searchsorted(pos2, gp, side="left")
            in_range1 = idx1 < len(pos1)
            in_range2 = idx2 < len(pos2)
            match1 = in_range1 & (pos1[np.minimum(idx1, len(pos1) - 1)] == gp)
            match2 = in_range2 & (pos2[np.minimum(idx2, len(pos2) - 1)] == gp)
            both_match = match1 & match2

            global_idx = np.where(mask)[0]
            keep[global_idx[both_match]] = True

        n_before = len(work)
        out = work.loc[keep].reset_index(drop=True)
        n_drop = n_before - len(out)
        if n_drop and log_drops:
            logger.warning(
                "Dropped %s / %s DMP rows not present in both centroids (fixed panel or centroid coverage mismatch). "
                "Classifier uses the intersecting subset only.",
                n_drop,
                n_before,
            )
        return out

    def _extract_bin_counts_for_dmps(
        self,
        dmps_df: pd.DataFrame,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Load bin_counts from centroid H5 files at the positions listed in *dmps_df*.

        DMP rows should already lie on the intersection of both centroids (see
        :meth:`_subset_dmps_to_both_centroids`). A remaining mismatch raises ValueError
        (wrong paths, wrong files, or inconsistent inputs).

        The DMP DataFrame must have ``position``, ``context``, and ``chromosome``
        columns (or fall back to ``self.chromosome`` for the single-chromosome case).
        The method groups by chromosome × context so each H5 file is loaded at most
        once per invocation.

        Returns
        -------
        bin_edges : ndarray, shape (n_bins+1,)
            Shared bin edges (must be identical across all contexts and centroids).
        bc1 : ndarray, shape (n_dmps, n_bins)
            Histograms for centroid 1 (class 0), one row per row in *dmps_df*.
        bc2 : ndarray, shape (n_dmps, n_bins)
            Histograms for centroid 2 (class 1), one row per row in *dmps_df*.
        """
        dmp_positions = np.asarray(dmps_df["position"].values, dtype=np.uint32)
        chroms = (
            dmps_df["chromosome"].values
            if "chromosome" in dmps_df.columns
            else np.full(len(dmps_df), self.chromosome, dtype=object)
        )
        contexts = (
            dmps_df["context"].values
            if "context" in dmps_df.columns
            else np.full(len(dmps_df), self.config.contexts[0], dtype=object)
        )

        # Allocate output arrays (n_bins determined on first load)
        n_dmps = len(dmps_df)
        bin_edges_ref: Optional[np.ndarray] = None
        bc1_rows: Optional[np.ndarray] = None
        bc2_rows: Optional[np.ndarray] = None

        # Group by (chromosome, context) and cache centroid histograms across calls.
        unique_pairs = list(dict.fromkeys(zip(chroms, contexts)))
        for chrom, ctx in unique_pairs:
            mask = (chroms == chrom) & (contexts == ctx)
            if not np.any(mask):
                continue

            cached = self._get_or_load_centroid_bin_cache(chrom, ctx)
            if cached is None:
                logger.warning(
                    "_extract_bin_counts_for_dmps: skipping %s-%s (no centroid cache); rows will use zero histograms",
                    chrom,
                    ctx,
                )
                continue

            be1 = cached["bin_edges"]
            if bin_edges_ref is None:
                n_bins = int(be1.shape[0]) - 1
                bin_edges_ref = be1
                bc1_rows = np.zeros((n_dmps, n_bins), dtype=np.float64)
                bc2_rows = np.zeros((n_dmps, n_bins), dtype=np.float64)
            elif be1.shape != bin_edges_ref.shape or not np.allclose(be1, bin_edges_ref):
                raise ValueError(
                    "_extract_bin_counts_for_dmps: inconsistent centroid bin_edges across contexts."
                )

            pos1 = cached["pos1"]
            pos2 = cached["pos2"]
            group_positions = np.asarray(dmp_positions[mask], dtype=np.uint32)

            idx1 = np.searchsorted(pos1, group_positions, side="left")
            idx2 = np.searchsorted(pos2, group_positions, side="left")

            # DMPs are defined over the intersection of valid positions on both centroids, so every
            # DMP must be present in both centroid files. Use exact match to index; any mismatch
            # indicates wrong centroid paths, wrong chromosome/context, or corrupted data.
            in_range1 = idx1 < len(pos1)
            in_range2 = idx2 < len(pos2)
            match1 = in_range1 & (pos1[np.minimum(idx1, len(pos1) - 1)] == group_positions)
            match2 = in_range2 & (pos2[np.minimum(idx2, len(pos2) - 1)] == group_positions)
            both_match = match1 & match2

            n_match = int(np.sum(both_match))
            n_group = int(np.sum(mask))
            if n_match != n_group:
                raise ValueError(
                    f"_extract_bin_counts_for_dmps: {chrom}-{ctx}: {n_match}/{n_group} DMP positions "
                    "found in both centroids. DMPs are defined only on positions present in both centroids; "
                    "this mismatch indicates wrong centroid1_dir/centroid2_dir, wrong chromosome/context, or "
                    "centroid files from a different run."
                )
            bc1_rows[mask] = cached["bc1"][idx1]
            bc2_rows[mask] = cached["bc2"][idx2]

        if bin_edges_ref is None:
            raise ValueError(
                "_extract_bin_counts_for_dmps: no valid centroid files found. "
                "Ensure centroid1_dir/centroid2_dir contain H5 files with binned_stats."
            )

        # Sanity: centroid2 (class 1) histograms must be non-zero. DMPs exist only on positions in both
        # centroids; samples may miss positions, but the centroid files used here are the same ones
        # that produced the DMPs, so bc2 should never be all zeros unless paths or files are wrong.
        bc2_sum = float(np.sum(bc2_rows))
        if bc2_sum < 1e-6:
            raise ValueError(
                "centroid2 (class 1) histograms are zero. DMPs are defined on positions present in both "
                "centroids; check centroid2_dir and that centroid2 H5 files have binned_stats with "
                "non-zero bin_counts (and are the same centroids used for detection)."
            )
        c1_dir = getattr(self.config, "centroid1_dir", None)
        c2_dir = getattr(self.config, "centroid2_dir", None)
        c1_dir = str(c1_dir).strip() if c1_dir else ""
        c2_dir = str(c2_dir).strip() if c2_dir else ""
        if c1_dir and c2_dir and Path(c1_dir).resolve() == Path(c2_dir).resolve():
            raise ValueError(
                "centroid1_dir and centroid2_dir must point to different directories (class 0 vs class 1)."
            )

        return bin_edges_ref, bc1_rows, bc2_rows

    def _create_multi_context_result(
        self, 
        dmps_df: pd.DataFrame, 
        bio_dmps_df: pd.DataFrame
    ) -> MethylDetectorResult:
        """
        Create result object for multi-context analysis.
        
        Args:
            dmps_df: DataFrame with all statistical DMPs
            bio_dmps_df: DataFrame with biological DMPs
            
        Returns:
            MethylDetectorResult
        """
        # Compute per-context statistics
        comparison_stats = []
        total_statistical_dmps = int(dmps_df["statistical_dmp"].sum()) if "statistical_dmp" in dmps_df.columns else len(dmps_df)
        selected_confirmed_dmps = int(bio_dmps_df["statistical_dmp"].sum()) if "statistical_dmp" in bio_dmps_df.columns else len(bio_dmps_df)
        for context in self.config.contexts:
            ctx_dmps = dmps_df[dmps_df['context'] == context]
            ctx_bio = bio_dmps_df[bio_dmps_df['context'] == context]
            
            if len(ctx_dmps) > 0:
                ctx_statistical = int(ctx_dmps["statistical_dmp"].sum()) if "statistical_dmp" in ctx_dmps.columns else len(ctx_dmps)
                stats = ComparisonStats(
                    comparison_name=f"{self.chromosome}-{context}",
                    total_positions=len(ctx_dmps),
                    statistical_dmps=ctx_statistical,
                    biological_dmps=len(ctx_bio),
                    processing_time_seconds=0.0,  # TODO: track per-context timing
                    gpu_used=self._runtime_gpu_used
                )
                comparison_stats.append(stats)
        
        # Create result
        config_summary = self.config.model_dump()

        balanced_accuracy = None
        if hasattr(self, '_final_validation_results') and self._final_validation_results:
            balanced_accuracy = self._final_validation_results.get('balanced_accuracy')

        result = MethylDetectorResult(
            biologically_significant_dmps_df=bio_dmps_df,
            total_statistical_dmps=total_statistical_dmps,
            total_biological_dmps=len(bio_dmps_df),
            biological_retention_rate=selected_confirmed_dmps / max(1, total_statistical_dmps),
            comparison_stats=comparison_stats,
            timestamp=datetime.now().isoformat(),
            version="2.0.0-multi-context",
            config_summary=config_summary,
            balanced_accuracy=balanced_accuracy
        )
        
        return result


    def _structured_array_to_dmp_df(self, structured_array: np.ndarray) -> pd.DataFrame:
        """Convert structured array to DataFrame with basic fields (fast, no objects)."""
        df = pd.DataFrame(structured_array)
        # Ensure 'position' column exists and is properly typed
        if 'position' not in df.columns:
            logger.warning("Structured array missing 'position' field; adding dummy positions")
            df['position'] = np.arange(len(df), dtype=np.uint32)
        # Ensure dtypes (include n1, n2, variance1, variance2 from centroid comparison). No bhattacharyya (ECDF overlap only).
        dtype_map = {
            "position": "uint32",
            "chromosome": "str",
            "context": "str",
            "p_value": "float32",
            "q_value": "float32",
            "delta_mean": "float32",
            "weight": "float32",
            "mean1": "float32",
            "mean2": "float32",
            "selected": "bool",
            "n1": "uint32",
            "n2": "uint32",
            "variance1": "float32",
            "variance2": "float32",
        }
        for col, dtype in dtype_map.items():
            if col in df:
                df[col] = df[col].astype(dtype)
        # Drop legacy columns; overlap and effect_size come from the ECDF scoring path
        # in _compute_chunk_metrics_df.
        for legacy in ("bhattacharyya", "bhattacharyya_coefficient"):
            if legacy in df.columns:
                df.drop(columns=[legacy], inplace=True)
        return df

    def _compute_missing_metrics_df(
        self,
        df: pd.DataFrame,
        ecdf_view1: Any,
        ecdf_view2: Any,
        view_row_indices: Optional[np.ndarray] = None,
    ) -> pd.DataFrame:
        """Compute continuous-ECDF overlap and final effect_size for the reduced set.

        When view_row_indices is None, both ECDFViews are pre-sliced to exactly the
        positions in df (row i of the view corresponds to row i of df). When
        view_row_indices is provided, the views cover a superset and view_row_indices[i]
        is the row index into the views for df row i.
        """
        if ecdf_view1 is None or ecdf_view2 is None or len(df) == 0:
            return df
        # Drop legacy columns if present (from MethylCentroidPair output); we use ECDF overlap only
        legacy_cols = [c for c in ("bhattacharyya", "bhattacharyya_coefficient") if c in df.columns]
        if legacy_cols:
            df = df.drop(columns=legacy_cols)
        n_rows = len(df)
        # Estimate memory for the (n_positions × grid_size) PDF matrices used in ecdf_overlap_integral.
        grid_size = self.config.ecdf_grid_size
        mem_per_row_mb = (2 * grid_size * 8) / (1024 ** 2)  # two float64 arrays of shape (n, grid)
        from methyl_utils import get_memory_usage
        available_gb = get_memory_usage().get('gpu_free_gb', 80.0)
        available_mb = available_gb * 1024
        chunk_size = max(50000, int(available_mb * 0.9 / max(mem_per_row_mb, 1e-6)))
        logger.debug(f"ECDF chunking: {available_mb:.0f} MB available, chunk_size={chunk_size:,} rows")

        if n_rows > chunk_size:
            logger.info(f"🔄 Chunking ECDF metrics for {n_rows:,} rows (batches of {chunk_size:,})")
            chunks = []
            for i in range(0, n_rows, chunk_size):
                end_i = min(i + chunk_size, n_rows)
                chunk_df = df.iloc[i:end_i].copy()
                chunk_view_indices = view_row_indices[i:end_i] if view_row_indices is not None else None
                chunk_result = self._compute_chunk_metrics_df(
                    chunk_df,
                    ecdf_view1=ecdf_view1,
                    ecdf_view2=ecdf_view2,
                    start_row=i,
                    view_row_indices=chunk_view_indices,
                )
                chunks.append(chunk_result)
            result_df = pd.concat(chunks, ignore_index=True)
            logger.info(f"📊 Chunked ECDF metrics complete for {n_rows:,} rows")
            return result_df
        else:
            return self._compute_chunk_metrics_df(
                df,
                ecdf_view1=ecdf_view1,
                ecdf_view2=ecdf_view2,
                start_row=0,
                view_row_indices=view_row_indices,
            )

    def _compute_chunk_metrics_df(
        self,
        chunk_df: pd.DataFrame,
        ecdf_view1: Any,
        ecdf_view2: Any,
        start_row: int = 0,
        view_row_indices: Optional[np.ndarray] = None,
    ) -> pd.DataFrame:
        """Compute overlap and effect_size for one reduced chunk.

        When view_row_indices is None, ECDFView row i corresponds to chunk row i
        (start_row + i). When view_row_indices is provided, it gives the row indices
        into the (superset) views for this chunk.
        """
        import time
        start_time = time.time()

        dm = chunk_df['delta_mean'].values.astype(np.float64)
        var1 = chunk_df['variance1'].values.astype(np.float64)
        var2 = chunk_df['variance2'].values.astype(np.float64)

        mean_level = None
        if self.config.effect_size_use_mean_level and 'mean1' in chunk_df.columns and 'mean2' in chunk_df.columns:
            mu1 = chunk_df['mean1'].values.astype(np.float64)
            mu2 = chunk_df['mean2'].values.astype(np.float64)
            use_max = self.config.effect_size_mean_level_use_max
            mean_level = np.maximum(mu1, mu2) if use_max else (mu1 + mu2) / 2.0
        else:
            if start_row == 0 and self.config.effect_size_use_mean_level:
                missing = []
                if 'mean1' not in chunk_df.columns:
                    missing.append('mean1')
                if 'mean2' not in chunk_df.columns:
                    missing.append('mean2')
                if missing:
                    logger.warning(
                        "effect_size mean-level weight disabled: chunk missing %s (effect_size = raw formula).",
                        ", ".join(missing),
                    )

        from methyl_utils.statistical_tests import ecdf_effect_size
        position_indices = (
            view_row_indices
            if view_row_indices is not None
            else np.arange(start_row, start_row + len(chunk_df), dtype=np.intp)
        )
        grid_size = self.config.ecdf_grid_size
        lambda_var = self.config.lambda_var
        mean_level_weight = self.config.effect_size_mean_level_weight
        mean_level_k = self.config.effect_size_mean_level_k
        if self.config.effect_size_mean_level_k_by_context and "context" in chunk_df.columns:
            ctx = chunk_df["context"].iloc[0]
            mean_level_k = self.config.effect_size_mean_level_k_by_context.get(ctx, mean_level_k)
        results = ecdf_effect_size(
            delta_mean=dm,
            var1=var1,
            var2=var2,
            ecdf_view1=ecdf_view1,
            ecdf_view2=ecdf_view2,
            position_indices=position_indices,
            grid_size=grid_size,
            lambda_var=lambda_var,
            mean_level=mean_level,
            mean_level_weight=mean_level_weight,
            mean_level_k=mean_level_k,
        )
        chunk_df['overlap'] = results['overlap'].astype(np.float32)
        chunk_df['effect_size'] = results['effect_size'].astype(np.float32)
        chunk_df['effect_size_reliability'] = results['reliability'].astype(np.float32)

        chunk_df['combined_variance'] = (var1 + var2).astype(np.float32)

        total_time = time.time() - start_time
        logger.debug(f"Chunk metrics completed in {total_time:.2f}s for {len(chunk_df):,} rows")
        return chunk_df


    def _generate_filter_histograms(self, df: pd.DataFrame, chromosome: str, context: str) -> None:
        """Generate interactive HTML histograms for filter statistics (debugging only)."""
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        # Create output directory
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        # Generate histograms for key statistics
        fig = make_subplots(
            rows=2,
            cols=2,
            subplot_titles=[
                "Q-value Distribution",
                "Delta Mean Distribution",
                "P-value Distribution",
                "Biological Filters Summary",
            ],
        )
        # Q-value histogram
        fig.add_trace(
            go.Histogram(x=df["q_value"], nbinsx=50, name="Q-values"), row=1, col=1
        )
        # Delta mean histogram
        fig.add_trace(
            go.Histogram(x=df["delta_mean"].abs(), nbinsx=50, name="|Delta Mean|"),
            row=1,
            col=2,
        )
        # P-value histogram
        fig.add_trace(
            go.Histogram(x=df["p_value"], nbinsx=50, name="P-values"), row=2, col=1
        )
        # Summary statistics text
        stats_text = f"""
        Total DMPs: {len(df):,}
        Statistically significant (q ≤ {self.config.alpha}): {len(df[df['q_value'] <= self.config.alpha]):,}
        DMPs with effect_size (BD) column: {len(df) if 'effect_size' in df.columns else 0:,}
        """
        fig.add_annotation(
            text=stats_text,
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(size=12),
            align="left",
        )
        fig.update_layout(
            height=800, title_text="DMP Filter Statistics Overview", showlegend=False
        )
        # Save HTML file with suffix
        html_file = output_dir / f"filter_statistics_overview-{chromosome}-{context}.html"
        fig.write_html(html_file)
        logger.info(f"📊 Interactive filter statistics saved: {html_file}")
        # Generate chromosome/context breakdown histograms (but single, so simple)
        chrom_fig = go.Figure()
        chrom_counts = (
            df.groupby(["chromosome", "context"]).size().reset_index(name="count")
        )
        for ctx in df["context"].unique():
            context_data = chrom_counts[chrom_counts["context"] == ctx]
            chrom_fig.add_trace(
                go.Bar(
                    x=context_data["chromosome"],
                    y=context_data["count"],
            name=f"{ctx} context",
                )
            )
        chrom_fig.update_layout(
            title="DMPs by Chromosome and Context",
            xaxis_title="Chromosome",
            yaxis_title="Number of DMPs",
            barmode="group",
        )
        chrom_html = output_dir / f"dmps_by_chromosome_context-{chromosome}-{context}.html"
        chrom_fig.write_html(chrom_html)
        logger.info(f"📊 Chromosome/context breakdown saved: {chrom_html}")

    def _export_selected_dmps_csv(self, biological_dmps_df: pd.DataFrame) -> None:
        """Export selected DMPs to CSV with standard columns (n1, n2, variances, overlap, effect_size, ECDF distribution)."""
        if biological_dmps_df.empty:
            logger.warning("No biological DMPs to export")
            return

        export_df = biological_dmps_df.copy()
        DIST_NAMES = {5: 'ECDF'}
        if 'dist' in export_df.columns:
            export_df['dist_name'] = export_df['dist'].map(DIST_NAMES).fillna('Unknown').astype(str)
        if 'delta_sign' not in export_df.columns and 'mean1' in export_df.columns and 'mean2' in export_df.columns:
            export_df['delta_sign'] = np.sign(export_df['mean1'] - export_df['mean2']).astype(np.int8)
        if 'effect_size' in export_df.columns:
            export_df['weight'] = export_df['effect_size']

        standard_cols = [
            'chromosome', 'context', 'position', 'n1', 'n2', 'mean1', 'mean2', 'variance1', 'variance2',
            'overlap', 'delta_mean', 'delta_sign', 'effect_size', 'p_value', 'q_value', 'dist', 'dist_name', 'weight'
        ]
        if self.config.export_sample_size_estimate:
            export_df['n_estimated_per_group'] = self._compute_sample_size_estimate(export_df)
            standard_cols.append('n_estimated_per_group')
        export_cols = [c for c in standard_cols if c in export_df.columns]
        missing_cols = [c for c in standard_cols if c not in export_df.columns]
        if missing_cols:
            logger.warning(f"Missing columns in selected DMPs: {missing_cols}")
        
        # Create output directory and CSV path
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = output_dir / f"dmps-{self.chrom}-{self.ctx}.csv"
        
        # Export selected columns
        export_df[export_cols].to_csv(csv_path, index=False)
        logger.info(f"✅ Exported {len(export_df):,} selected DMPs to {csv_path} with columns: {export_cols}")
        
        # Store CSV path for result summary
        self._exported_csv_path = csv_path

    def _save_single_chrom_context_results(self, df: pd.DataFrame, output_dir: Path, chromosome: str, context: str) -> None:
        """Save biological DMPs CSV with all columns."""
        if df.empty:
            logger.warning(f"No biological DMPs to save for {chromosome}-{context}")
            return

        csv_path = output_dir / f"biological_dmps-{chromosome}-{context}.csv"
        # Save all DataFrame columns for comprehensive analysis
        df.to_csv(csv_path, index=False)
        logger.info(f"Saved {len(df):,} biological DMPs to {csv_path} with {len(df.columns)} columns")

    def _create_final_result(self, dmp_df: pd.DataFrame,
                             biological_dmps_df: Optional[pd.DataFrame] = None) -> MethylDetectorResult:
        """Create final result object from DataFrames."""
        # Log biological importance range
        if biological_dmps_df is not None and not biological_dmps_df.empty:
            if 'effect_size' in biological_dmps_df.columns:
                bio_scores = biological_dmps_df['effect_size'].dropna()
                if len(bio_scores) > 0:
                    max_score = bio_scores.max()
                    min_score = bio_scores.min()
                    logger.info(f"Biological importance range: min={min_score:.6f}, max={max_score:.6f}, count={len(bio_scores)}")

        # Build comparison stats from stored metadata
        chromosome = self.chrom if hasattr(self, 'chrom') else 'unknown'
        context = self.ctx if hasattr(self, 'ctx') else 'unknown'
        comp_name = f"{chromosome}-{context}" if chromosome != 'unknown' else "single_comparison"
        logger.debug(f"Processing {len(dmp_df)} DMPs for {comp_name}")

        stats = ComparisonStats(
            comparison_name=comp_name,
                total_positions=self.total_positions if hasattr(self, 'total_positions') else len(dmp_df),
            statistical_dmps=self.statistical_dmps_count if hasattr(self, 'statistical_dmps_count') else int(dmp_df["statistical_dmp"].sum()) if isinstance(dmp_df, pd.DataFrame) and "statistical_dmp" in dmp_df.columns else len(dmp_df),
            biological_dmps=len(biological_dmps_df) if biological_dmps_df is not None else 0,
            processing_time_seconds=self.processing_time_seconds if hasattr(self, 'processing_time_seconds') else 0.0,
            gpu_used=self._runtime_gpu_used
        )
        comparison_stats = [stats]
        total_statistical_dmps = self.statistical_dmps_count if hasattr(self, 'statistical_dmps_count') else int(dmp_df["statistical_dmp"].sum()) if isinstance(dmp_df, pd.DataFrame) and "statistical_dmp" in dmp_df.columns else len(dmp_df)
        selected_confirmed_dmps = (
            int(biological_dmps_df["statistical_dmp"].sum())
            if biological_dmps_df is not None and "statistical_dmp" in biological_dmps_df.columns
            else (len(biological_dmps_df) if biological_dmps_df is not None else 0)
        )

        config_summary = self.config.model_dump()

        result = MethylDetectorResult(
            biologically_significant_dmps_df=biological_dmps_df,
            total_statistical_dmps=total_statistical_dmps,
            total_biological_dmps=len(biological_dmps_df) if biological_dmps_df is not None else 0,
            biological_retention_rate=selected_confirmed_dmps / max(1, total_statistical_dmps) if biological_dmps_df is not None else 0.0,
            comparison_stats=comparison_stats,
            timestamp=datetime.now().isoformat(),
            version="2.0.0",
            config_summary=config_summary
        )
        return result

    def _save_results(self, result: MethylDetectorResult) -> None:
        """Save results for single mode."""
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        # Global CSV already saved in filtering for single
        # Always save summaries (JSON, TXT)
        chrom = self.chrom if hasattr(self, 'chrom') else 'unknown'
        ctx = self.ctx if hasattr(self, 'ctx') else 'unknown'
        suffix = f"-{chrom}-{ctx}"
        prefix = f"dmps{suffix}"
        save_json(result.model_dump(), output_dir / f"result{suffix}.json")
        # Create and save analysis summary
        import uuid
        from datetime import datetime
        # Extract key parameters (only relevant ones)
        key_params = {
            "alpha": self.config.alpha,
            "effect_size_coverage": self.config.effect_size_coverage,
            "delta_mean_reduction": self.config.delta_mean_reduction,
            "target_balanced_accuracy": self.config.target_balanced_accuracy,
            "min_selected_dmps": self.config.min_selected_dmps,
        }
        # Input files
        input_files = {
            "centroid1": self.config.centroid1_path,
            "centroid2": self.config.centroid2_path,
        }
        # CSV path for single - use exported CSV path if available, otherwise construct from prefix
        csv_path = self._exported_csv_path if hasattr(self, '_exported_csv_path') else None
        if csv_path is None and result.biologically_significant_dmps_df is not None and not result.biologically_significant_dmps_df.empty:
            csv_path = output_dir / f"{prefix}.csv"
        # Get top DMP importance
        top_dmp_importance = None
        if result.biologically_significant_dmps_df is not None and not result.biologically_significant_dmps_df.empty:
            if 'effect_size' in result.biologically_significant_dmps_df.columns:
                top_dmp_importance = result.biologically_significant_dmps_df['effect_size'].max()
        # Model info
        model_path = result.classifier_model_path if hasattr(result, 'classifier_model_path') else None
        training_accuracy = result.training_accuracy if hasattr(result, 'training_accuracy') else None
        summary = MethylDetectorSummary(
            analysis_id=str(uuid.uuid4()),
            timestamp=datetime.now().isoformat(),
            version="2.0.0",
            input_files=input_files,
            total_statistical_dmps=result.total_statistical_dmps,
            total_biological_dmps=result.total_biological_dmps,
            biological_retention_rate=result.biological_retention_rate,
            output_directory=output_dir,
            csv_file=csv_path,
            summary_json_file=output_dir / f"summary{suffix}.json",
            classifier_model_file=model_path,
            key_parameters=key_params,
            classifier_accuracy=training_accuracy,
            top_dmp_significance=top_dmp_importance, 
        )
        save_json(
            summary.model_dump(),
            output_dir / f"analysis_summary{suffix}.json"
        )
        summary_lines = [
            "MethylDetector Analysis Summary",
            "=" * 40,
            f"Analysis Date: {result.timestamp}",
            f"Version: {result.version}",
            "",
            "Configuration:",
            f"  Alpha (q-value threshold): {self.config.alpha}",
            f"  Effect-size coverage (biological filter): {self.config.effect_size_coverage}",
            f"  Delta-mean reduction gate (pre-statistical): {self.config.delta_mean_reduction}",
            f"  Target Balanced Accuracy: {self.config.target_balanced_accuracy}",
            "",
            "Biological filter: ECDF cumulative mass selection (effect_size_coverage per context).",
            "",
            "Results:",
            f"  Statistical DMPs (q≤{self.config.alpha}): {result.total_statistical_dmps:,}",
            f"  Biological DMPs (selected): {result.total_biological_dmps:,}",
            f"  Retention Rate: {result.biological_retention_rate:.1%}"
        ]
        if model_path:
            summary_lines.append(f"Classifier Model: {model_path}")
            if training_accuracy is not None:
                summary_lines.append(f"Model Accuracy (centroids): {training_accuracy * 100:.1f}%")
        save_summary_txt(summary_lines, output_dir / f"summary{suffix}.txt")

        try:
            from methyl_domain.action_result import atomic_write_json, manifest_path_for

            discovery_csv = output_dir / f"dmps-{chrom}-discovery.csv"
            result_json_path = output_dir / f"result{suffix}.json"
            group = None
            if isinstance(result.config_summary, dict):
                group = result.config_summary.get("group") or result.config_summary.get("comparison")
            run_key_parts = [str(chrom), str(ctx)]
            if group:
                run_key_parts.append(str(group))
            run_key = "_".join(run_key_parts) or "default"
            manifest = manifest_path_for(output_dir, "pipeline.detector", run_key)
            atomic_write_json(
                manifest,
                {
                    "schema_version": "1.0",
                    "status": "ok",
                    "action_name": "pipeline.detector",
                    "group": str(group) if group else None,
                    "chromosome": str(chrom),
                    "context": str(ctx),
                    "output_dir": str(output_dir),
                    "n_statistical_dmps": result.total_statistical_dmps,
                    "n_biological_dmps": result.total_biological_dmps,
                    "discovery_csv": str(discovery_csv) if discovery_csv.is_file() else None,
                    "result_json_path": str(result_json_path) if result_json_path.is_file() else None,
                    "result_code": 0,
                },
            )
        except Exception:
            logger.debug("Could not write detector action manifest", exc_info=True)


def _setup_imports_for_direct_execution():
    """Set up imports when running this file directly."""
    import sys
    from pathlib import Path

    # When run directly, set up the package structure
    script_dir = Path(__file__).parent  # methyl_detector/core/
    package_root = script_dir.parent  # methyl_detector/
    # Add package root to sys.path for imports
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))
    # Also add methylutils from monorepo if available (for development)
    # In monorepo: packages/methyldetector/../methylutils/methyl_utils
    methyl_utils_path = package_root.parent.parent / "methylutils" / "methyl_utils"
    if methyl_utils_path.exists() and str(methyl_utils_path) not in sys.path:
        sys.path.insert(0, str(methyl_utils_path))
    # Now import the relative imports and update globals
    global MethylDetectorConfig, ComparisonStats, MethylDetectorResult, MethylDetectorSummary
    global GPUConfig, save_csv, save_json, save_summary_txt
    global CentroidPairHandler, create_centroid_from_arrays, get_chromosome_context_from_filename
    try:
        from models.config import MethylDetectorConfig as MDC  # type: ignore[import-not-found]
        from models.results import (  # type: ignore[import-not-found]
            ComparisonStats as CS,
            MethylDetectorResult as MDR,
            MethylDetectorSummary as MDS,
        )
        from utils.core import (  # type: ignore[import-not-found]
            GPUConfig as GC,
            save_csv as sc,
            save_json as sj,
            save_summary_txt as sst,
        )
        from utils.sample_handler import (  # type: ignore[import-not-found]
            CentroidPairHandler as CPH,
            create_centroid_from_arrays as cca,
        )
        from utils.file_utils import get_chromosome_context_from_filename as gccf  # type: ignore[import-not-found]

        # Update the global variables
        MethylDetectorConfig = MDC
        ComparisonStats = CS
        MethylDetectorResult = MDR
        MethylDetectorSummary = MDS
        GPUConfig = GC
        save_csv = sc
        save_json = sj
        save_summary_txt = sst
        CentroidPairHandler = CPH
        create_centroid_from_arrays = cca
        get_chromosome_context_from_filename = gccf
    except ImportError as e:
        raise RuntimeError(
            f"Failed to import required modules for direct execution: {e}"
        )


if __name__ == "__main__":
    """Allow direct execution of MethylDetector for development/testing."""
    _setup_imports_for_direct_execution()
    # Delegate to CLI main function
    try:
        from methyl_detector.cli.main import main  # type: ignore[import-not-found]

        main()
    except ImportError as e:
        import sys

        print(f"Error: {e}")
        print("Try running: python run_methyl_detector.py")
        print("Or: python -m methyl_detector.cli.main")
        sys.exit(1)
