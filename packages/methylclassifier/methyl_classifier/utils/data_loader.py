"""
Data loading functionality for MethylClassifier
"""

import time
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any, Union
import numpy as np
import pandas as pd
from collections import defaultdict

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable=None, *args, **kwargs):
        return iterable if iterable is not None else []


def _parse_chrom_ctx_from_h5_name(h5_path: Optional[Path]) -> Tuple[Optional[str], Optional[str]]:
    """Parse ``{chrom}-{ctx}.h5`` (e.g. ``1-CG.h5``). Chromosome may contain dashes."""
    if h5_path is None:
        return None, None
    stem = Path(h5_path).stem
    if "-" not in stem:
        return None, None
    chrom, ctx = stem.rsplit("-", 1)
    if ctx in {"CG", "CHG", "CHH"} and chrom:
        return chrom, ctx
    return None, None


def _unique_ctx_from_tnc(sample: Any) -> Optional[str]:
    """If packed tnc bytes share one methylation context, return CG/CHG/CHH."""
    df = getattr(sample, "_df", None)
    if df is None or "tnc" not in getattr(df, "columns", []):
        return None
    try:
        tnc = np.asarray(df["tnc"].values)[:4096]
    except Exception:
        return None
    if tnc.size == 0:
        return None
    from methyl_utils import CONTEXT_NAMES, TNCBits

    names = set()
    for byte in np.unique(tnc):
        name = CONTEXT_NAMES.get(TNCBits.from_byte(int(byte)).context, "UNKNOWN")
        if name != "UNKNOWN":
            names.add(name)
    if len(names) == 1:
        return next(iter(names))
    return None


def chrom_ctx_for_residualize(
    sample: Any,
    h5_path: Optional[Path] = None,
    chrom: Optional[str] = None,
    ctx: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve chrom×ctx without truth-testing the per-position ``context`` Series."""
    meta = getattr(sample, "_metadata", None) or {}
    chrom = (
        chrom
        or getattr(sample, "chrom", None)
        or getattr(sample, "chromosome", None)
        or meta.get("chromosome")
    )
    ctx = (
        ctx
        or getattr(sample, "ctx", None)
        or getattr(sample, "context_metadata", None)
        or meta.get("context")
    )
    file_chrom, file_ctx = _parse_chrom_ctx_from_h5_name(h5_path)
    chrom = chrom or file_chrom or DataLoader.residualize_chrom
    ctx = ctx or file_ctx or DataLoader.residualize_ctx or _unique_ctx_from_tnc(sample)
    return (str(chrom) if chrom else None, str(ctx) if ctx else None)


class DataLoader:
    """
    Data loader for methylation samples.
    
    Handles loading samples from various sources and formats.
    """

    residualize_coef_dir: Optional[str] = None
    residualize_chrom: Optional[str] = None
    residualize_ctx: Optional[str] = None

    @staticmethod
    def _attach_residualize(
        sample: Any,
        h5_path: Optional[Path] = None,
        chrom: Optional[str] = None,
        ctx: Optional[str] = None,
    ) -> Any:
        coef_dir = DataLoader.residualize_coef_dir
        if not coef_dir:
            return sample
        chrom, ctx = chrom_ctx_for_residualize(sample, h5_path, chrom=chrom, ctx=ctx)
        if not chrom or not ctx:
            return sample
        from methyl_utils.residualize_runtime import load_applier

        sample._residualize_applier = load_applier(coef_dir, str(chrom), str(ctx))
        if h5_path is not None and not getattr(sample, "sample_id", None):
            from methyl_utils.mvalue_residualize import sample_id_from_path

            try:
                sample.sample_id = sample_id_from_path(h5_path)
            except Exception:
                pass
        return sample

    @staticmethod
    def load_sample(h5_path: Path, debug: bool = False):
        """
        Load a single methylation sample from file.

        Args:
            h5_path: Path to the sample file
            debug: Enable debug output

        Returns:
            MethylSample instance
        """
        from methyl_utils import MethylSample

        if h5_path.suffix.lower() == '.h5':
            sample = MethylSample.load_from_h5(h5_path)
            return DataLoader._attach_residualize(sample, h5_path)
        else:
            raise ValueError(f"Unsupported file format: {h5_path.suffix}")

    @staticmethod
    def load_samples_from_directory(h5_dir: Path,
                                 chrom: str = None,
                                 context: str = None,
                                 debug: bool = False) -> List[Tuple[str, Any]]:
        """
        Load all methylation samples from a directory.

        Args:
            h5_dir: Directory containing sample files
            chrom: Optional chromosome filter
            context: Optional context filter
            debug: Enable debug output

        Returns:
            List of (sample_name, sample) tuples
        """
        samples = []
        all_h5_files = list(h5_dir.rglob("*.h5"))

        if not all_h5_files:
            raise FileNotFoundError(f"No .h5 files found in {h5_dir} or its subdirectories")

        # Filter by chromosome and context if specified
        if chrom and context:
            h5_files = DataLoader._filter_h5_files_by_chrom_context(all_h5_files, chrom, context)
            print(f"Found {len(all_h5_files)} total .h5 files, {len(h5_files)} match {chrom}-{context}")
        else:
            h5_files = all_h5_files
            print(f"Found {len(h5_files)} .h5 files")

        if not h5_files:
            filter_msg = f" matching {chrom}-{context}" if chrom and context else ""
            raise FileNotFoundError(f"No .h5 files{filter_msg} found in {h5_dir} or its subdirectories")

        for h5_file in sorted(h5_files):
            try:
                sample = DataLoader.load_sample(h5_file, debug=debug)
                sample_name = h5_file.parent.name  # Use parent directory name as sample identifier

                # Collect statistical information for enhanced analysis
                coverage = sample.get_coverage()
                avg_coverage = np.mean(coverage) if len(coverage) > 0 else 0

                samples.append((sample_name, sample))

                # Provide more informative loading message
                print(f"    ✅ Loaded {sample_name} ({sample.sample_type}, avg coverage: {avg_coverage:.1f})")

            except Exception as e:
                sample_name = h5_file.parent.name
                print(f"    ❌ Failed to load {sample_name}: {e}")

        return samples

    @staticmethod
    def _filter_h5_files_by_chrom_context(h5_files: List[Path], chrom: str, context: str) -> List[Path]:
        """
        Filter .h5 files to only include those matching the specified chromosome and context.
        Expects files to be named {prefix}-{chrom}-{context}.h5 (exact chromosome match).
        """
        expected_pattern = f"{chrom}-{context}.h5"
        filtered_files = []

        for h5_file in h5_files:
            # Check if filename exactly matches the pattern
            # This prevents "11-CG.h5" from matching when chrom="1"
            if h5_file.name == expected_pattern:
                filtered_files.append(h5_file)

        return filtered_files

    @staticmethod
    def _merge_context_samples(samples: List[Any]) -> Any:
        """
        Merge multiple MethylSample instances (e.g. CG, CHG, CHH) into one.
        Union of positions; for duplicates, the first sample's value is used (CG first).
        """
        from methyl_utils import MethylSample

        if len(samples) == 1:
            return samples[0]

        # Build position -> (mC, uC, tnc), first occurrence wins (CG then CHG then CHH)
        pos_to_data: Dict[int, Tuple[Any, Any, Any]] = {}
        for s in samples:
            pos_arr = np.asarray(s.pos.values if hasattr(s.pos, 'values') else s.pos)
            mC_arr = np.asarray(s.mC.values if hasattr(s.mC, 'values') else s.mC)
            uC_arr = np.asarray(s.uC.values if hasattr(s.uC, 'values') else s.uC)
            tnc_col = s.df["tnc"]
            tnc_arr = np.asarray(tnc_col.values if hasattr(tnc_col, 'values') else tnc_col, dtype=np.uint8)
            for i in range(len(pos_arr)):
                p = int(pos_arr[i])
                if p not in pos_to_data:
                    pos_to_data[p] = (mC_arr[i], uC_arr[i], tnc_arr[i])

        positions = np.array(sorted(pos_to_data.keys()), dtype=np.uint32)
        mC = np.array([pos_to_data[p][0] for p in positions], dtype=np.uint32)
        uC = np.array([pos_to_data[p][1] for p in positions], dtype=np.uint32)
        tnc = np.array([pos_to_data[p][2] for p in positions], dtype=np.uint8)
        return MethylSample.from_sample_data(positions, mC, uC, tnc, metadata=None)

    @staticmethod
    def load_sample_from_directory(
        sample_dir: Path,
        chromosomes: Optional[List[str]] = None,
        debug: bool = False,
        required_chromosomes: Optional[List[str]] = None,
        positions: Optional[np.ndarray] = None,
        dmp_positions_by_chrom: Optional[Union[Dict[str, np.ndarray], pd.DataFrame]] = None,
        contexts_to_load: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Load a sample from a directory, merging contexts (or single context when contexts_to_load has one).

        Only classifier DMP positions are loaded when dmp_positions_by_chrom is provided.
        When contexts_to_load is ['CG'] (model trained CG-only), only CG files are loaded—no merge.

        Args:
            sample_dir: Directory containing {chrom}-{context}.h5 files
            chromosomes: Optional list of chromosomes to load. If None, auto-detect from files.
            dmp_positions_by_chrom: When set, only these positions are read per chromosome (required for classification).
            contexts_to_load: When set to e.g. ['CG'], only those context files are loaded (ensures match to detector).

        Returns:
            Dictionary mapping chromosome to merged MethylSample (or single-context when contexts_to_load has one)
        """
        from methyl_utils import MethylSample
        
        sample_dir = Path(sample_dir)
        
        # Find all chromosome-context files
        h5_files = list(sample_dir.glob("*-CG.h5")) + list(sample_dir.glob("*-CHG.h5")) + list(sample_dir.glob("*-CHH.h5"))
        
        if not h5_files:
            raise FileNotFoundError(f"No .h5 files found in {sample_dir}")
        
        # Group files by chromosome
        chrom_files: Dict[str, Dict[str, Path]] = defaultdict(dict)
        
        for h5_file in h5_files:
            # Extract chromosome from filename (e.g., "1-CG.h5" -> chrom="1", context="CG")
            parts = h5_file.stem.split('-')
            if len(parts) >= 2:
                chrom = parts[0]
                context = parts[-1]  # Last part is context
                if context in ['CG', 'CHG', 'CHH']:
                    chrom_files[chrom][context] = h5_file
        
        # Filter chromosomes if specified
        if chromosomes:
            chrom_files = {chrom: files for chrom, files in chrom_files.items() if chrom in chromosomes}

        # Filter to required chromosomes if specified (for performance optimization)
        if required_chromosomes:
            chrom_files = {chrom: files for chrom, files in chrom_files.items() if chrom in required_chromosomes}

        if not chrom_files:
            raise FileNotFoundError(f"No valid chromosome files found in {sample_dir}")
        
        merged_samples = {}

        def _fallback_positions_for_chrom(chrom_key: str) -> Optional[np.ndarray]:
            """Use flat ``positions`` only when a single chromosome is in scope (safe hyperslice)."""
            if positions is None or len(positions) == 0:
                return None
            pos_arr = np.asarray(positions, dtype=np.uint32).ravel()
            if pos_arr.size == 0:
                return None
            if len(pos_arr) > 1 and not np.all(np.diff(pos_arr) >= 0):
                pos_arr = np.sort(pos_arr)
            single_chrom_scope = False
            if required_chromosomes is not None and len(required_chromosomes) == 1:
                single_chrom_scope = str(chrom_key) == str(required_chromosomes[0])
            elif chromosomes is not None and len(chromosomes) == 1:
                single_chrom_scope = str(chrom_key) == str(chromosomes[0])
            elif len(chrom_files) == 1:
                single_chrom_scope = True
            if not single_chrom_scope:
                if debug:
                    print(
                        f"      ⚠️ Chromosome {chrom_key}: flat ``positions`` ignored for multi-chrom "
                        f"load; use dmp_positions_by_chrom per chromosome.",
                        flush=True,
                    )
                return None
            return pos_arr
        
        # Merge contexts for each chromosome
        for chrom, context_files in chrom_files.items():
            # Load and merge contexts
            contexts_to_merge = []
            
            # Load CG (required as base) - use chromosome-specific positions for optimal performance
            if 'CG' in context_files:
                # Use chromosome-specific DMP positions if available (for hyperslice optimization)
                chrom_positions = None
                if dmp_positions_by_chrom is not None:
                    if isinstance(dmp_positions_by_chrom, dict):
                        # Dictionary format (legacy)
                        chrom_positions = dmp_positions_by_chrom.get(chrom)
                        if chrom_positions is not None:
                            chrom_positions = np.array(chrom_positions, dtype=np.uint32)
                            # Ensure sorted (required for hyperslice binary search optimization)
                            if len(chrom_positions) > 1 and not np.all(np.diff(chrom_positions) >= 0):
                                chrom_positions = np.sort(chrom_positions)
                    else:
                        # DataFrame format - query directly for speed
                        # Positions are already sorted from dmpDF (extracted from classifier)
                        chrom_positions = dmp_positions_by_chrom[
                            dmp_positions_by_chrom['chromosome'] == chrom
                        ]['position'].values.astype(np.uint32) if len(dmp_positions_by_chrom) > 0 else None
                    
                    if chrom_positions is not None and len(chrom_positions) > 0:
                        # Verify positions are sorted (critical for hyperslice optimization in methyl_utils)
                        if len(chrom_positions) > 1:
                            assert np.all(np.diff(chrom_positions) >= 0), \
                                f"Positions for {chrom} must be sorted for hyperslice binary search!"
                        if debug:
                            print(f"      🎯 Chromosome {chrom}: Using {len(chrom_positions):,} sorted DMP positions for hyperslice", flush=True)
                    else:
                        if debug:
                            print(f"      ⚠️ Chromosome {chrom}: No DMP positions found, will load ALL positions", flush=True)
                            chrom_positions = None
                else:
                    chrom_positions = _fallback_positions_for_chrom(chrom)
                    if chrom_positions is None and debug:
                        print(f"      ⚠️ Chromosome {chrom}: No DMP positions provided, will load ALL positions", flush=True)

                try:
                    cg_sample = MethylSample.load_from_h5(context_files['CG'], chrom_positions)
                    cg_sample = DataLoader._attach_residualize(
                        cg_sample, context_files['CG'], chrom=chrom, ctx='CG'
                    )
                    if debug:
                        print(f"      ✅ Chromosome {chrom}-CG: Loaded {len(cg_sample.pos):,} positions", flush=True)
                    contexts_to_merge.append(cg_sample)
                except Exception as e:
                    import traceback
                    print(f"❌ Failed to load {chrom}-CG.h5 (required): {e}")
                    print(f"   Error type: {type(e).__name__}")
                    print("   Traceback:")
                    traceback.print_exc()
                    # CG is required, so skip this chromosome
                    continue
            else:
                print(f"⚠️ Warning: {chrom}-CG.h5 not found in {sample_dir}, skipping chromosome {chrom}")
                continue

            # Load CHG and CHH only when not using single-context (e.g. CG-only model)
            if contexts_to_load is None or len(contexts_to_load) > 1:
                for context in ['CHG', 'CHH']:
                    if context in context_files:
                        try:
                            context_sample = MethylSample.load_from_h5(context_files[context], chrom_positions)
                            context_sample = DataLoader._attach_residualize(
                                context_sample,
                                context_files[context],
                                chrom=chrom,
                                ctx=context,
                            )
                            contexts_to_merge.append(context_sample)
                        except Exception as e:
                            print(f"⚠️ Warning: Failed to load {chrom}-{context}.h5: {e}")
            elif debug and contexts_to_load == ['CG']:
                print(f"      📌 Chromosome {chrom}: CG-only (model context), skipping CHG/CHH", flush=True)

            if len(contexts_to_merge) == 0:
                continue

            # Merge contexts: single context → use as-is; multiple → union of positions (CG preferred)
            merged_sample = None
            try:
                merged_sample = DataLoader._merge_context_samples(contexts_to_merge)
                if merged_sample is not None:
                    for src in contexts_to_merge:
                        applier = getattr(src, "_residualize_applier", None)
                        if applier is None:
                            continue
                        merged_sample._residualize_applier = applier
                        sid = getattr(src, "sample_id", None)
                        if sid:
                            merged_sample.sample_id = sid
                        break
            except Exception as e:
                import traceback
                print(f"❌ Failed to merge contexts for chromosome {chrom}: {e}")
                print(f"   Error type: {type(e).__name__}")
                print("   Traceback:")
                traceback.print_exc()
                continue
            finally:
                # Multi-context merge creates a new sample object; release the temporary
                # per-context samples immediately so large Monte Carlo loops do not retain
                # unnecessary dataframes.
                if len(contexts_to_merge) > 1:
                    for context_sample in contexts_to_merge:
                        if context_sample is not None and hasattr(context_sample, "close"):
                            try:
                                context_sample.close()
                            except Exception:
                                pass
            
            # Skip if merged sample is empty (no positions after merging)
            if len(merged_sample.pos) == 0:
                if debug:
                    print(f"⚠️ Warning: {chrom} merged sample is empty (no DMP positions), skipping")
                continue
            
            merged_samples[chrom] = merged_sample
        
        return merged_samples
    
    @staticmethod
    def load_samples_from_list(
        sample_paths: List[str],
        chromosomes: Optional[List[str]] = None,
        debug: bool = False,
        required_chromosomes: Optional[List[str]] = None,
        positions: Optional[np.ndarray] = None,
        dmp_positions_by_chrom: Optional[Union[Dict[str, np.ndarray], pd.DataFrame]] = None,
        contexts_to_load: Optional[List[str]] = None
    ) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[int]]:
        """
        Load multiple samples from a list of directory paths.

        Each directory should contain {chrom}-CG.h5, {chrom}-CHG.h5, {chrom}-CHH.h5 files.
        Contexts are merged per chromosome (union of positions, CG preferred).

        Args:
            sample_paths: List of sample directory paths
            chromosomes: Optional list of chromosomes to load. If None, auto-detect.
            debug: Enable debug output
            required_chromosomes: If provided, only load these specific chromosomes (for performance)
            positions: If provided, only load data for these positions (ultra-performance)
            dmp_positions_by_chrom: DMP positions organized by chromosome (chromosome-specific optimization)

        Returns:
            Tuple of:
            - loaded samples as (sample_name, {chrom: merged_MethylSample}) tuples
            - indices into the original sample_paths list for the successfully loaded samples
        """
        samples = []
        loaded_indices: List[int] = []
        total = len(sample_paths)
        pbar = tqdm(sample_paths, desc="Loading samples", unit="sample", total=total)

        for i, sample_path in enumerate(pbar, 1):
            sample_dir = Path(sample_path)
            sample_name = sample_dir.name
            if hasattr(pbar, "set_postfix_str"):
                pbar.set_postfix_str(sample_name, refresh=True)
            else:
                print(f"✅ Loading sample {i}/{total}: {sample_name}", flush=True)
            start_time = time.time()

            try:
                # Load and merge contexts for this sample
                merged_samples = DataLoader.load_sample_from_directory(
                    sample_dir, chromosomes, debug, required_chromosomes, positions, dmp_positions_by_chrom,
                    contexts_to_load=contexts_to_load
                )
                
                # Skip sample if no chromosomes were loaded (all were empty or missing)
                if len(merged_samples) == 0:
                    if hasattr(pbar, "set_postfix_str"):
                        pbar.set_postfix_str(f"skip: {sample_name}", refresh=True)
                    else:
                        print(f"⚠️ Warning: Sample {sample_name} has no valid chromosomes (all empty or missing), skipping")
                    continue
                
                load_time = time.time() - start_time
                samples.append((sample_name, merged_samples))
                loaded_indices.append(i - 1)
                if debug and not hasattr(pbar, "set_postfix_str"):
                    print(f"✅ Loaded sample: {sample_name} ({len(merged_samples)} chromosomes) in {load_time:.1f}s")
            except Exception as e:
                if hasattr(pbar, "set_postfix_str"):
                    pbar.set_postfix_str(f"failed: {sample_name}", refresh=True)
                else:
                    print(f"❌ Failed to load sample {sample_name}: {e}", flush=True)
                if debug:
                    import traceback
                    print(f"   Error type: {type(e).__name__}", flush=True)
                    traceback.print_exc()
                continue
        
        return samples, loaded_indices

    @staticmethod
    def extract_sample_features(sample: Any,
                              dmp_positions: np.ndarray) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
        """
        Extract DMP features from a methylation sample.

        Args:
            sample: MethylSample instance
            dmp_positions: Array of DMP positions used by the classifier

        Returns:
            Tuple of (feature_vector, availability_mask, stats_info)
        """
        dmp_arr = np.asarray(dmp_positions, dtype=np.uint32).ravel()
        if hasattr(sample, "lookup_at_positions"):
            values, availability_mask = sample.lookup_at_positions(
                dmp_arr,
                min_coverage=1,
                missing_value=np.nan,
            )
        else:
            # Backward-compatible fallback for test doubles / legacy sample adapters.
            methylation_levels = sample.get_methylation_levels()
            meth_vals = np.asarray(methylation_levels, dtype=np.float64).ravel()
            pos_vals = np.asarray(sample.pos, dtype=np.uint32).ravel()
            n_common = min(pos_vals.size, meth_vals.size)
            pos_vals = pos_vals[:n_common]
            meth_vals = meth_vals[:n_common]
            values = np.full(len(dmp_arr), np.nan, dtype=np.float64)
            availability_mask = np.zeros(len(dmp_arr), dtype=bool)
            if pos_vals.size:
                order = np.argsort(pos_vals, kind="mergesort")
                sp = pos_vals[order]
                sm = meth_vals[order]
                n = int(sp.size)
                idx = np.searchsorted(sp, dmp_arr, side="left").astype(np.int32, copy=False)
                safe_idx = np.minimum(np.maximum(idx, 0), max(n - 1, 0))
                match = (idx < n) & (sp[safe_idx] == dmp_arr)
                if np.any(match):
                    mloc = np.flatnonzero(match)
                    raw = sm[safe_idx[mloc]]
                    finite = np.isfinite(raw)
                    values[mloc[finite]] = raw[finite]
                    availability_mask[mloc[finite]] = True
        applier = getattr(sample, "_residualize_applier", None)
        if applier is None and DataLoader.residualize_coef_dir:
            sample = DataLoader._attach_residualize(sample)
            applier = getattr(sample, "_residualize_applier", None)
        if applier is not None:
            from methyl_utils.mvalue_residualize import sample_id_from_path

            sid = getattr(sample, "sample_id", None) or getattr(sample, "name", None)
            if not sid:
                sid = "unknown"
            filled = np.where(np.isfinite(values), values, 0.5)
            adjusted = np.asarray(
                applier.apply_for_sample(str(sid), dmp_arr, filled),
                dtype=np.float64,
            )
            values = np.where(np.isfinite(values), adjusted, np.nan)
        feature_vector = np.full(len(dmp_arr), 0.5, dtype=np.float64)
        if values.size:
            valid = np.isfinite(values)
            feature_vector[valid] = np.clip(values[valid], 0.0, 1.0)
        missing_positions = int(np.sum(~availability_mask))

        # Collect statistical information
        coverage = sample.get_coverage()
        avg_coverage = np.mean(coverage) if len(coverage) > 0 else 0
        stats_info = {
            'avg_coverage': avg_coverage,
            'total_positions': len(sample.pos),
            'sample_type': sample.sample_type,
            'missing_positions': missing_positions,
            'dmp_coverage_pct': (
                (len(dmp_arr) - missing_positions) / len(dmp_arr) * 100.0 if len(dmp_arr) > 0 else 0.0
            ),
        }

        # Add statistical properties if available (for centroids)
        if sample.is_centroid:
            try:
                alpha, beta = sample.get_beta_parameters()
                stats_info.update({
                    'avg_alpha': np.mean(alpha),
                    'avg_beta': np.mean(beta),
                    'avg_tau': np.mean(sample.tau),
                    'avg_variance': np.mean(sample.variance)
                })
            except Exception:
                # If statistical properties can't be computed, continue without them
                pass

        return np.array(feature_vector), np.array(availability_mask), stats_info
