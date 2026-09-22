"""
Command-line interface for MethylClassifier
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any, Union
import numpy as np
import pandas as pd

from ..core.classifier import MethylClassifier
from ..utils.data_loader import DataLoader
from ..utils.ecdf_derived_measures import extract_derived_measures_from_dmp_vector
from ..utils.prediction_abstention import apply_min_observed_dmp_abstention
from ..utils.calibration_split import stratified_calibration_fit_mask
from ..utils.utils import extract_chrom_context_from_classifier, setup_logging
from ..models.config_schema import ClassificationConfig
from ..models.config import ClassifierConfig
from ..project_resolver import (
    classifier_step_dict_has_ovr_sources,
    merge_project_classifier_step,
    resolve_classifier_config,
    resolve_classifier_config_per_cancer_group,
)

try:
    from methyl_utils import load_project
except ImportError:
    load_project = None  # type: ignore[misc, assignment]

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable=None, *args, **kwargs):
        return iterable if iterable is not None else []


def _chromosome_keys_to_str(chrom_samples: Dict[Any, Any]) -> Dict[str, Any]:
    """
    Merged H5 samples use chromosome keys from filenames (str '1', 'X', ...).
    Union ``dmp_positions_df`` may use int or str in the chromosome column; normalize so
    ``chrom in chrom_samples`` cannot fail spuriously (otherwise every DMP is filled with 0.5 /
    unavailable and OvR fusion ties break to class 0).
    """
    return {str(k): v for k, v in chrom_samples.items()}


def _calibration_fit_mask_for_classifier(
    classifier: MethylClassifier,
    expected_classes: np.ndarray,
) -> Optional[np.ndarray]:
    """Stratified holdout mask for isotonic / chromosome stacking fit; None = use all rows."""
    frac = getattr(classifier.config, "calibration_train_fraction", None)
    if frac is None or float(frac) >= 1.0:
        return None
    seed = getattr(classifier.config, "calibration_seed", None)
    return stratified_calibration_fit_mask(
        np.asarray(expected_classes, dtype=np.float64),
        float(frac),
        seed,
    )


def _remap_centroid_path(path: str, path_remap: Optional[Dict[str, str]], sample_root: Optional[Path]) -> str:
    """
    Apply path_remap (prefix replacement), then optionally resolve bare sample IDs via sample_root.

    Some centroid metadata stores ``samples_used`` as sample IDs (e.g. ``039377_21M_25_99``)
    rather than absolute paths; in that case, join with sample_root so downstream loading can
    find the sample directory.
    """
    if path_remap:
        # Longest matching prefix so /a/b/c matches /a/b before /a
        best_old: Optional[str] = None
        for old_prefix in path_remap:
            if path.startswith(old_prefix) and (best_old is None or len(old_prefix) > len(best_old)):
                best_old = old_prefix
        if best_old is not None:
            new_prefix = path_remap[best_old]
            rest = path[len(best_old):].lstrip("/")
            return f"{new_prefix.rstrip('/')}/{rest}" if rest else new_prefix.rstrip("/")
    # Fallback for bare IDs or relative paths in samples_used metadata.
    if not Path(path).is_absolute():
        if sample_root is not None:
            return str(sample_root / Path(path).name)
        # When sample_root is not available, infer from remap targets (prefer ".../samples").
        if path_remap:
            remap_targets = [v.rstrip("/") for v in path_remap.values() if isinstance(v, str)]
            samples_targets = [t for t in remap_targets if Path(t).name.lower() == "samples"]
            if samples_targets:
                # Longest target first for deterministic behavior when multiple candidates exist.
                best_target = sorted(samples_targets, key=len, reverse=True)[0]
                return f"{best_target}/{Path(path).name}"
    return path


def _read_samples_used_from_centroid_dir(
    centroid_dir: Path,
    sample_root: Optional[Union[str, Path]] = None,
    path_remap: Optional[Dict[str, str]] = None
) -> List[str]:
    """
    Read union of 'samples_used' from all H5 files in a centroid output directory.
    Each centroid file (e.g. per chromosome/context) may list a subset; we return
    the deduplicated union so all samples that contributed to any centroid are included.
    Remapping (when data moved to NAS): if path_remap is set, replace longest matching
    old_prefix with new_prefix; else if sample_root is set, use sample_root / basename(path).
    """
    import h5py
    seen = set()
    result = []
    root = Path(sample_root) if sample_root else None
    for h5_path in sorted(centroid_dir.glob("*.h5")):
        try:
            with h5py.File(h5_path, "r") as f:
                raw = f.attrs.get("samples_used")
                if raw is None:
                    continue
                if isinstance(raw, (bytes, str)):
                    raw = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                    raw = json.loads(raw)
                for p in raw:
                    path = p if isinstance(p, str) else str(p)
                    if not path:
                        continue
                    path = _remap_centroid_path(path, path_remap, root)
                    if path not in seen:
                        seen.add(path)
                        result.append(path)
        except Exception:
            continue
    return result


def classify_samples(classifier: MethylClassifier,
                    h5_path: Optional[Path] = None,
                    samples_list: Optional[List[str]] = None,
                    chrom: str = None,
                    context: str = None,
                    output_file: Optional[Path] = None,
                    debug: bool = False,
                    panel_spec: Optional[Dict[str, Any]] = None,
                    min_observed_dmp_fraction: float = 0.0) -> None:
    """
    Load samples from .h5 files and classify them using the trained classifier.
    
    Supports:
    - Single .h5 file via h5_path
    - Directory of .h5 files via h5_path
    - List of sample directories (each with {chrom}-CG.h5, {chrom}-CHG.h5, {chrom}-CHH.h5) via samples_list
    """
    # Handle samples list (multi-chromosome with merged contexts)
    if samples_list:
        return classify_samples_from_list(
            classifier,
            samples_list,
            output_file,
            debug,
            panel_spec=panel_spec,
            min_observed_dmp_fraction=min_observed_dmp_fraction,
        )
    
    # Legacy: single file or directory
    if h5_path is None:
        raise ValueError("Either h5_path or samples_list must be provided")
    if panel_spec:
        print(
            "⚠️ panel is ignored for legacy input_path / single-directory .h5 classification; "
            "use samples_list (e.g. centroid validation) for panel CSV columns.",
            flush=True,
        )
    
    filter_info = f" ({chrom}-{context})" if chrom and context else ""
    print(f"\n🔍 Loading samples from: {h5_path}{filter_info}")

    # Handle both single file and directory
    if h5_path.is_file():
        if h5_path.suffix.lower() != '.h5':
            raise ValueError(f"Not an .h5 file: {h5_path}")
        # Check if single file matches the expected chromosome/context
        if chrom and context:
            expected_pattern = f"{chrom}-{context}.h5"
            if h5_path.name != expected_pattern:
                raise ValueError(f"File {h5_path.name} does not match expected pattern {expected_pattern}")
        # Load single sample
        sample = DataLoader.load_sample(h5_path, debug)
        sample_name = h5_path.parent.name
        samples = [(sample_name, sample)]
    elif h5_path.is_dir():
        samples = DataLoader.load_samples_from_directory(h5_path, chrom, context, debug)
    else:
        raise FileNotFoundError(f"Path not found: {h5_path}")

    if not samples:
        raise ValueError("No samples loaded. Check that input path contains .h5 files and that paths are correct.")

    print(f"\n📊 Extracting features for {len(samples)} samples...")

    # Get classifier feature information
    feature_info = classifier.get_feature_info()
    n_dmps = feature_info['n_features']
    dmp_positions = feature_info['positions']

    # Extract features for all samples
    feature_matrix = []
    availability_mask = []
    sample_names = []
    sample_types = []
    stats_info = []

    for sample_name, sample in samples:
        features, mask, stats = DataLoader.extract_sample_features(sample, dmp_positions)

        feature_matrix.append(features)
        availability_mask.append(mask)
        sample_names.append(sample_name)
        sample_types.append(sample.sample_type)
        stats_info.append(stats)

        missing_positions = stats['missing_positions']
        if missing_positions > 0:
            print(f"  ⚠️ {sample_name} ({sample.sample_type}): {missing_positions}/{n_dmps} DMP positions missing")

    feature_matrix = np.array(feature_matrix)
    availability_mask = np.array(availability_mask)

    # Display prediction method
    print(f"\n🤖 Classifying samples using ECDF classifier...")
    
    predictions, probabilities, abstain_flags = classify_samples_batch(
        classifier,
        feature_matrix,
        availability_mask,
        debug,
        min_observed_dmp_fraction=min_observed_dmp_fraction,
    )

    # Print classification genes (DMPs) information
    print(f"\n🧬 Classification Genes (DMPs) Information:")
    print(f"Total DMPs in classifier: {len(dmp_positions)}")

    # Calculate how many DMPs are actually available for classification
    available_dmps_per_sample = np.sum(availability_mask, axis=1)
    print(f"Average DMPs available per sample: {np.mean(available_dmps_per_sample):.1f}")
    print(f"Min DMPs available: {np.min(available_dmps_per_sample)}")
    print(f"Max DMPs available: {np.max(available_dmps_per_sample)}")

    # Show DMP positions (first 20 and last 20)
    print(f"\nFirst 20 DMP positions: {dmp_positions[:20].tolist()}")
    print(f"Last 20 DMP positions: {dmp_positions[-20:].tolist()}")

    # Debug position alignment issue
    if debug:
        print(f"\n🔍 Position Alignment Debug:")
        print(f"Classifier DMP positions range: {dmp_positions.min()} to {dmp_positions.max()}")

        # Check a few individual samples to see their position ranges
        for i, (name, (_, sample)) in enumerate(zip(sample_names[:3], samples[:3])):
            positions = sample.pos
            if len(positions) > 0:
                print(f"  {name} ({sample.sample_type}): {len(positions)} positions, range {positions.min()} to {positions.max()}")
                # Check overlap with classifier DMPs
                overlap = np.intersect1d(positions, dmp_positions)
                print(f"    Overlap with classifier DMPs: {len(overlap)}/{len(dmp_positions)} ({len(overlap)/len(dmp_positions)*100:.1f}%)")

    # Save DMP positions to file for analysis
    dmp_file = output_file.parent / "classification_dmps.txt" if output_file else Path("classification_dmps.txt")
    with open(dmp_file, 'w') as f:
        f.write("# Classification DMPs (Differentially Methylated Positions)\n")
        f.write(f"# Total DMPs: {len(dmp_positions)}\n")
        f.write(f"# Chromosome: {chrom if chrom else 'unknown'}\n")
        f.write(f"# Context: {context if context else 'unknown'}\n")
        f.write("# Format: position\n")
        for pos in dmp_positions:
            f.write(f"{pos}\n")
    print(f"💾 DMP positions saved to: {dmp_file}")

    # Analyze centroid vs sample classification
    if debug:
        print(f"\n🔍 Classification Analysis by Sample Type:")
        for i, (name, sample_type, pred, prob) in enumerate(zip(sample_names, sample_types, predictions, probabilities)):
            if sample_type == 'centroid':
                available_dmps = np.sum(availability_mask[i])
                print(f"  {name} ({sample_type}): Class_{pred}, Available DMPs: {available_dmps}/{len(dmp_positions)}")

                # Debug centroid methylation levels
                sample_data = feature_matrix[i]
                available_data = sample_data[availability_mask[i]]
                print(f"    Methylation level stats: mean={np.mean(available_data):.3f}, std={np.std(available_data):.3f}")
                print(f"    Methylation level range: [{np.min(available_data):.3f}, {np.max(available_data):.3f}]")

                # Check if this centroid should be classified differently
                if 'pb-' in name or 'pp-' in name:
                    # Correct class assignments:
                    # pb-cancer = Class_0 (Prostate Cancer Buffy Coat)
                    # pb-healthy = Class_1 (Healthy Buffy Coat)
                    # pp-cancer = Class_0 (Prostate Cancer Plasma) - should be cancer but model wasn't trained on plasma
                    # pp-healthy = Class_1 (Healthy Plasma) - should be healthy but model wasn't trained on plasma
                    if 'pb-cancer' in name:
                        expected_class = 0  # Prostate Cancer Buffy Coat
                    elif 'pb-healthy' in name:
                        expected_class = 1  # Healthy Buffy Coat
                    elif 'pp-cancer' in name:
                        expected_class = 0  # Prostate Cancer Plasma (but model not trained on plasma)
                    elif 'pp-healthy' in name:
                        expected_class = 1  # Healthy Plasma (but model not trained on plasma)
                    else:
                        expected_class = None

                    if expected_class is not None:
                        print(f"    Expected class: {expected_class}, Predicted: {pred}, Match: {expected_class == pred}")

                        # Additional debugging for centroid classification issues
                        if expected_class != pred:
                            print(f"    ⚠️ MISCLASSIFICATION: {name} should be Class {expected_class} but predicted as Class {pred}")
                            print(f"    Probabilities: Class0={prob[0]:.6f}, Class1={prob[1]:.6f}")

                            # Check if this is a plasma sample (model not trained on plasma data)
                            if 'pp-' in name:
                                print(f"    Note: This is a plasma sample - model was trained on buffy coat data only")
                            elif 'pb-' in name:
                                print(f"    Note: This is a training centroid - classification may be unreliable")

    # Display results with enhanced information
    print("\n📋 Classification Results:")

    # Create dynamic header based on number of classes
    prob_headers = [f"Prob_Class{i}" for i in range(classifier.n_classes)]
    header_parts = ["Sample", "Type", "Predicted"] + prob_headers + ["Coverage", "DMPs"]
    col_widths = [35, 12, 10] + [12] * classifier.n_classes + [8, 6]  # Column widths

    header_line = ""
    for part, width in zip(header_parts, col_widths):
        header_line += f"{part:<{width}} "
    header_width = len(header_line)

    print("-" * header_width)
    print(header_line)
    print("-" * header_width)

    class_counts = {i: 0 for i in range(classifier.n_classes)}
    abstain_count = 0
    sample_type_counts = {}
    results_data = []

    for i, (name, sample_type, pred, prob, stats) in enumerate(zip(sample_names, sample_types, predictions, probabilities, stats_info)):
        if pred < 0:
            predicted_label = "abstain"
            abstain_count += 1
        elif classifier.class_names is not None and pred < len(classifier.class_names):
            predicted_label = str(classifier.class_names[pred])
        else:
            predicted_label = f"Class_{pred}"

        if pred >= 0:
            class_counts[pred] = class_counts.get(pred, 0) + 1
        sample_type_counts[sample_type] = sample_type_counts.get(sample_type, 0) + 1

        # Get coverage info for display
        coverage_str = f"{stats.get('avg_coverage', 0):.1f}" if 'avg_coverage' in stats else "N/A"

        # Get DMPs used for classification
        dmps_used = np.sum(availability_mask[i])
        dmps_str = f"{dmps_used}/{len(dmp_positions)}"

        # Format probabilities for all classes
        prob_strings = [f"{prob[j]:.4f}" for j in range(classifier.n_classes)]
        row_parts = [name, sample_type, predicted_label] + prob_strings + [coverage_str, dmps_str]

        row_line = ""
        for part, width in zip(row_parts, col_widths):
            row_line += f"{part:<{width}} "
        print(row_line)

        # Prepare results data with statistical information
        result_entry = {
            'sample': name,
            'sample_type': sample_type,
            'prediction': int(pred),
            'predicted_class': predicted_label,
            'avg_coverage': stats.get('avg_coverage', 0),
            'total_positions': len(samples[i][1].pos),
            'dmps_used': int(dmps_used),
            'dmps_total': len(dmp_positions),
            'dmp_coverage_pct': float(dmps_used / len(dmp_positions) * 100),
            'abstained': bool(abstain_flags[i]),
        }

        # Add probabilities for all classes
        for j in range(classifier.n_classes):
            result_entry[f'prob_class{j}'] = float(prob[j])

        # Add statistical properties if available
        if 'avg_alpha' in stats:
            result_entry.update({
                'avg_alpha': stats['avg_alpha'],
                'avg_beta': stats['avg_beta'],
                'avg_tau': stats['avg_tau'],
                'avg_variance': stats['avg_variance']
            })

        results_data.append(result_entry)

    print("-" * header_width)
    print(f"Total samples: {len(samples)}")
    for i in range(classifier.n_classes):
        class_name = classifier.class_names[i] if classifier.class_names is not None and i < len(classifier.class_names) else f"Class {i}"
        print(f"{class_name}: {class_counts[i]} samples")
    if abstain_count:
        print(f"abstain (low observed DMP fraction): {abstain_count} samples")

    # Display sample type breakdown if we have that information
    if sample_type_counts:
        print("\nSample type breakdown:")
        for sample_type, count in sample_type_counts.items():
            print(f"  {sample_type}: {count} samples")

    # Summary and recommendations
    print(f"\n📊 Classification Summary:")
    print(f"  • Total samples classified: {len(samples)}")
    print(f"  • DMPs used for classification: {len(dmp_positions)}")
    print(f"  • Average DMPs available per sample: {np.mean(available_dmps_per_sample):.1f} ({np.mean(available_dmps_per_sample)/len(dmp_positions)*100:.1f}%)")
    print(f"  • Min DMPs available: {np.min(available_dmps_per_sample)} ({np.min(available_dmps_per_sample)/len(dmp_positions)*100:.1f}%)")
    print(f"  • Max DMPs available: {np.max(available_dmps_per_sample)} ({np.max(available_dmps_per_sample)/len(dmp_positions)*100:.1f}%)")
    # Display classification results summary
    result_summary = []
    for i in range(classifier.n_classes):
        class_name = classifier.class_names[i] if classifier.class_names is not None and i < len(classifier.class_names) else f"Class {i}"
        result_summary.append(f"{class_counts[i]} {class_name}")
    if abstain_count:
        result_summary.append(f"{abstain_count} abstain")
    print(f"  • Classification results: {', '.join(result_summary)}")

    # DMP usage by sample type
    print(f"\n📈 DMP Usage by Sample Type:")
    for sample_type in set(sample_types):
        type_indices = [i for i, st in enumerate(sample_types) if st == sample_type]
        type_dmps = available_dmps_per_sample[type_indices]
        print(f"  • {sample_type}: {np.mean(type_dmps):.1f} ± {np.std(type_dmps):.1f} DMPs ({np.mean(type_dmps)/len(dmp_positions)*100:.1f}% ± {np.std(type_dmps)/len(dmp_positions)*100:.1f}%)")

    # Check for potential issues
    centroid_samples = [i for i, st in enumerate(sample_types) if st == 'centroid']
    if centroid_samples:
        print(f"\n⚠️  Note: {len(centroid_samples)} centroid samples detected.")
        print(f"  Centroids may represent training data and classification results should be interpreted carefully.")
        print(f"  Individual samples (not centroids) provide more reliable classification results.")

    # Critical issue check: Low DMP coverage
    low_coverage_samples = np.sum(available_dmps_per_sample < len(dmp_positions) * 0.5)  # Less than 50% coverage
    if low_coverage_samples > 0:
        print(f"\n🚨 CRITICAL ISSUE DETECTED:")
        print(f"  {low_coverage_samples} samples have less than 50% DMP coverage!")
        print(f"  This suggests a position alignment problem between samples and classifier.")
        print(f"  Expected: ~100% coverage for samples used to train the classifier.")
        print(f"  This could indicate:")
        print(f"    - Different chromosome/context filtering")
        print(f"    - Position range mismatches")
        print(f"    - Data loading issues")
        print(f"    - File structure problems")

    # Save results if output file specified
    if output_file:
        import csv

        # Create output directory if it doesn't exist
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, 'w', newline='') as csvfile:
            # Determine fieldnames based on available data
            fieldnames = (
                ['sample', 'sample_type', 'prediction', 'predicted_class']
                + [f'prob_class{i}' for i in range(classifier.n_classes)]
                + ['avg_coverage', 'total_positions', 'dmps_used', 'dmps_total', 'dmp_coverage_pct', 'abstained']
            )

            # Add statistical fields if any sample has them
            if any('avg_alpha' in result for result in results_data):
                fieldnames.extend(['avg_alpha', 'avg_beta', 'avg_tau', 'avg_variance'])

            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results_data)
        print(f"\n💾 Results saved to: {output_file}")


def _inner_classifier_for_features(classifier: MethylClassifier):
    """Resolve the object that holds ECDF ``positions`` / ``contexts`` (unwrap one level if needed)."""
    inner = getattr(classifier, "classifier", None)
    if inner is None:
        return None
    if hasattr(inner, "positions") and hasattr(inner, "get_feature_info"):
        return inner
    nested = getattr(inner, "classifier", None)
    if nested is not None and hasattr(nested, "positions") and hasattr(nested, "get_feature_info"):
        return nested
    return inner if hasattr(inner, "get_feature_info") else None


def _try_centroid_pair_single_chrom_fast_path(
    classifier: MethylClassifier,
    samples_list: List[str],
    multichrom_dmp_df: bool,
    debug: bool,
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, List[str], str]]:
    """
    Batch-load methylation fractions via MethylCentroidPair.extract_methylation_fractions
    (same as MethylDetector validation): CG-only or multi-context using per-context
    reference positions, then reorder columns to match ECDF DMP row order.

    Returns (feature_matrix, availability_mask, dmp_positions, sample_names, chrom_str) or None.
    """
    if classifier.is_multi_chromosome or multichrom_dmp_df:
        return None
    inner = _inner_classifier_for_features(classifier)
    if inner is None or classifier.chromosome in (None, "unknown"):
        return None
    raw_pos = getattr(inner, "positions", None)
    if raw_pos is None and hasattr(inner, "get_feature_info"):
        fi = inner.get_feature_info()
        raw_pos = fi.get("positions") if isinstance(fi, dict) else None
    if raw_pos is None:
        return None
    pos_arr = np.asarray(raw_pos, dtype=np.uint32).ravel()
    if pos_arr.size == 0:
        return None
    raw_ctx = getattr(inner, "contexts", None)
    if raw_ctx is not None:
        ctx_arr = np.asarray(raw_ctx, dtype=str)
        if ctx_arr.shape[0] != pos_arr.shape[0]:
            return None
    else:
        ctx_arr = np.array(["CG"] * pos_arr.shape[0], dtype=str)

    # Preserve training metadata order, then append any context present in dmpDF but missing from metadata
    meta_ctx = getattr(classifier, "model_contexts", None) or []
    ctx_order = [str(c) for c in meta_ctx]
    seen_meta = set(ctx_order)
    for c in np.unique(ctx_arr):
        s = str(c)
        if s not in seen_meta:
            ctx_order.append(s)
            seen_meta.add(s)
    if not ctx_order:
        ctx_order = sorted(np.unique(ctx_arr).tolist())

    reference_positions: Dict[str, np.ndarray] = {}
    for ctx in ctx_order:
        mask = ctx_arr == str(ctx)
        reference_positions[str(ctx)] = (
            np.asarray(pos_arr[mask], dtype=np.uint32).copy() if np.any(mask) else np.array([], dtype=np.uint32)
        )

    try:
        from methyl_utils.methyl_centroid_pair import MethylCentroidPair

        chrom_str = str(classifier.chromosome)
        X_ext, all_pos, all_ctx, _ = MethylCentroidPair.extract_methylation_fractions(
            sample_paths=samples_list,
            reference_positions=reference_positions,
            chromosome=chrom_str,
            min_coverage=1,
        )
        if X_ext.size == 0:
            return None

        col_map: Dict[Tuple[int, str], int] = {}
        for j in range(int(all_pos.shape[0])):
            key = (int(all_pos[j]), str(all_ctx[j]))
            col_map[key] = j

        n_dmps = pos_arr.shape[0]
        col_for_row = np.empty(n_dmps, dtype=np.uint32)
        for i in range(n_dmps):
            key = (int(pos_arr[i]), str(ctx_arr[i]))
            if key not in col_map:
                raise KeyError(f"No extraction column for DMP row {key}")
            col_for_row[i] = col_map[key]

        X_reord = np.ascontiguousarray(X_ext[:, col_for_row])
        valid_rows = ~np.isnan(X_reord).all(axis=1)
        if not valid_rows.all():
            return None

        feature_matrix = np.where(np.isnan(X_reord), 0.5, X_reord).astype(np.float64)
        feature_matrix = np.clip(feature_matrix, 0.0, 1.0)
        availability = ~np.isnan(X_reord)
        sample_names = [Path(p).name for p in samples_list]
        n_ctx = len([c for c in ctx_order if reference_positions.get(str(c), np.array([])).size > 0])
        label = (
            f"multi-context ({', '.join(ctx_order)})"
            if n_ctx > 1 or (raw_ctx is not None and len(np.unique(ctx_arr)) > 1)
            else "CG-only"
        )
        print(
            f"⚡ Fast path ({label}): MethylCentroidPair.extract_methylation_fractions "
            f"({len(samples_list)} samples × {n_dmps} DMPs), chromosome {chrom_str}",
            flush=True,
        )
        return feature_matrix, availability, pos_arr, sample_names, chrom_str
    except Exception as exc:
        if debug:
            print(f"⚠️ Centroid-pair fast path skipped, using standard loader: {exc}", flush=True)
        return None


def classify_samples_from_list(
    classifier: MethylClassifier,
    samples_list: List[str],
    output_file: Optional[Path] = None,
    debug: bool = False,
    required_chromosomes: Optional[List[str]] = None,
    positions: Optional[np.ndarray] = None,
    dmp_positions_by_chrom: Optional[Union[Dict[str, np.ndarray], pd.DataFrame]] = None,
    expected_classes: Optional[List[int]] = None,
    panel_spec: Optional[Dict[str, Any]] = None,
    min_observed_dmp_fraction: float = 0.0,
) -> None:
    """
    Classify samples from a list of directories, merging CG, CHG, CHH contexts.

    Each directory should contain {chrom}-CG.h5, {chrom}-CHG.h5, {chrom}-CHG.h5 files.
    Contexts are merged per chromosome before classification.

    Args:
        classifier: MethylClassifier instance (single or multi-chromosome)
        samples_list: List of sample directory paths
        output_file: Optional output CSV file
        debug: Enable debug output
        required_chromosomes: Only load these chromosomes (performance optimization)
        positions: Only load these positions (ultra-performance optimization)
        dmp_positions_by_chrom: DMP positions organized by chromosome (chromosome-specific optimization)
        expected_classes: Optional list of expected class (0/1) per sample; when set, CSV gets expected_class column and a summary is printed (centroid validation).
        panel_spec: Optional hierarchical panel readout (OvR + pairwise max-contrast only). Adds columns to CSV and writes ``panel_report.json`` next to the CSV when ``output_file`` is set. See ``methyl_classifier.core.panel_fusion``.
        min_observed_dmp_fraction: If > 0, require at least this fraction of panel DMPs to be observed; otherwise abstain (uniform probabilities, prediction -1).
    """
    print(f"\n🔍 Loading {len(samples_list)} samples from directories...")
    
    # Use DataFrame directly from classifier if available (faster than building dict)
    # Otherwise build from classifiers for backward compatibility
    if dmp_positions_by_chrom is None:
        if hasattr(classifier, 'dmp_positions_df') and len(classifier.dmp_positions_df) > 0:
            # Use DataFrame directly (optimized format)
            dmp_positions_by_chrom = classifier.dmp_positions_df
        else:
            # Fallback: build dictionary (legacy)
            dmp_positions_by_chrom = {}
            if classifier.is_multi_chromosome:
                for chrom, chrom_classifier in classifier.classifiers.items():
                    feature_info = chrom_classifier.get_feature_info()
                    dmp_positions_by_chrom[chrom] = feature_info['positions']
            else:
                if getattr(classifier, "_ovr_mode", False):
                    dmp_positions_by_chrom = classifier.dmp_positions_df
                elif classifier.classifier is not None:
                    feature_info = classifier.classifier.get_feature_info()
                    # For single chromosome, use classifier's chromosome
                    chrom = classifier.chromosome if classifier.chromosome != 'unknown' else '1'
                    dmp_positions_by_chrom[chrom] = feature_info['positions']
    
    # When model is CG-only (e.g. MethylDetector with contexts: ["CG"]), load only CG to match training
    contexts_to_load = getattr(classifier, 'model_contexts', None)
    if contexts_to_load == ['CG']:
        print("📌 Model is CG-only: loading only CG context from samples (no CHG/CHH merge)", flush=True)

    # Fast path: single-chrom (CG-only or multi-context ECDF) — batch extraction like MethylDetector validation
    _dmp_df = getattr(classifier, "dmp_positions_df", None)
    multichrom_dmp_df = (
        _dmp_df is not None
        and len(_dmp_df) > 0
        and hasattr(_dmp_df, "columns")
        and "chromosome" in _dmp_df.columns
        and _dmp_df["chromosome"].nunique() > 1
    )
    fast = _try_centroid_pair_single_chrom_fast_path(
        classifier, samples_list, multichrom_dmp_df, debug
    )
    if fast is not None:
        feature_matrix_fast, availability_fast, dmp_pos_fast, sample_names_fast, chrom_str = fast
        fc_exp = None
        if expected_classes is not None:
            fc_exp = [
                expected_classes[i]
                for i in range(len(samples_list))
                if i < len(expected_classes)
            ]
        predictions, probabilities, _abst = classify_samples_batch(
            classifier,
            feature_matrix_fast,
            availability_fast,
            debug,
            expected_classes=fc_exp,
            min_observed_dmp_fraction=min_observed_dmp_fraction,
        )
        extra_cols = None
        if panel_spec and getattr(classifier, "_ovr_mode", False):
            extra_cols, panel_json = _panel_spec_to_csv_columns(
                classifier,
                feature_matrix_fast,
                availability_fast,
                panel_spec,
                debug=debug,
            )
            if output_file is not None:
                pr = Path(output_file).parent / "panel_report.json"
                with open(pr, "w", encoding="utf-8") as f:
                    json.dump(panel_json, f, indent=2)
                print(f"📋 Panel report saved to: {pr}", flush=True)
        derived_cols = _derived_measures_extra_columns(classifier, feature_matrix_fast)
        if derived_cols:
            extra_cols = {**(extra_cols or {}), **derived_cols}
        _save_classification_results(
            classifier,
            sample_names_fast,
            predictions,
            probabilities,
            availability_fast,
            dmp_pos_fast,
            output_file,
            expected_classes=fc_exp,
            extra_columns=extra_cols,
        )
        if fc_exp is not None and len(fc_exp) == len(sample_names_fast):
            _print_validation_report(
                classifier, sample_names_fast, predictions, probabilities, fc_exp
            )
        return

    # Load samples (merged contexts per chromosome, or single context when model is CG-only)
    loaded_samples, loaded_indices = DataLoader.load_samples_from_list(
        samples_list, debug=debug, required_chromosomes=required_chromosomes,
        positions=positions, dmp_positions_by_chrom=dmp_positions_by_chrom,
        contexts_to_load=contexts_to_load
    )
    
    if not loaded_samples:
        raise ValueError("No samples loaded from provided paths")
    if expected_classes is not None:
        expected_classes = [
            expected_classes[i]
            for i in loaded_indices
            if i < len(expected_classes)
        ]
    
    if classifier.is_multi_chromosome:
        # Multi-chromosome mode: extract features per chromosome and combine
        _classify_multi_chromosome_samples(
            classifier,
            loaded_samples,
            output_file,
            debug,
            expected_classes=expected_classes,
            panel_spec=panel_spec,
            min_observed_dmp_fraction=min_observed_dmp_fraction,
        )
    elif (
        getattr(classifier, "dmp_positions_df", None) is not None
        and len(classifier.dmp_positions_df) > 0
        and hasattr(classifier.dmp_positions_df, "columns")
        and "chromosome" in classifier.dmp_positions_df.columns
        and (
            getattr(classifier, "_ovr_mode", False)
            or classifier.dmp_positions_df["chromosome"].nunique() > 1
        )
    ):
        # OvR union DMPs or single-file multiclass with DMPs spanning multiple chromosomes
        _classify_single_file_multichrom_dmps(
            classifier,
            loaded_samples,
            output_file,
            debug,
            expected_classes=expected_classes,
            panel_spec=panel_spec,
            min_observed_dmp_fraction=min_observed_dmp_fraction,
        )
    else:
        # Single chromosome mode: use first chromosome from merged samples
        # Extract chromosome from classifier (may be None for legacy saved wrappers)
        classifier_chrom = classifier.chromosome
        
        if classifier_chrom is None or classifier_chrom == 'unknown':
            # Try to infer from available chromosomes
            available_chroms = set()
            for _, chrom_samples in loaded_samples:
                available_chroms.update(chrom_samples.keys())
            
            if not available_chroms:
                raise ValueError("No chromosomes found in loaded samples")
            
            classifier_chrom = sorted(available_chroms)[0]
            print(f"⚠️ Classifier chromosome unknown, using first available: {classifier_chrom}")
        
        # Convert to single-sample format
        single_samples = []
        single_expected_classes = [] if expected_classes is not None else None
        for loaded_idx, (sample_name, chrom_samples) in enumerate(loaded_samples):
            if classifier_chrom in chrom_samples:
                single_samples.append((sample_name, chrom_samples[classifier_chrom]))
                if single_expected_classes is not None and loaded_idx < len(expected_classes):
                    single_expected_classes.append(expected_classes[loaded_idx])
            else:
                print(f"⚠️ Sample {sample_name}: chromosome {classifier_chrom} not found, skipping")
        
        if not single_samples:
            raise ValueError(f"No samples found with chromosome {classifier_chrom}")
        
        # Use existing single-chromosome classification
        feature_info = classifier.get_feature_info()
        dmp_positions = feature_info['positions']
        
        feature_matrix = []
        availability_mask = []
        sample_names = []
        pbar = tqdm(single_samples, desc="Extracting features", unit="sample")
        for sample_name, sample in pbar:
            if hasattr(pbar, "set_postfix_str"):
                pbar.set_postfix_str(sample_name, refresh=True)
            features, mask, stats = DataLoader.extract_sample_features(sample, dmp_positions)
            feature_matrix.append(features)
            availability_mask.append(mask)
            sample_names.append(sample_name)
        
        feature_matrix = np.array(feature_matrix)
        availability_mask = np.array(availability_mask)
        predictions, probabilities, _abst = classify_samples_batch(
            classifier,
            feature_matrix,
            availability_mask,
            debug,
            expected_classes=single_expected_classes,
            min_observed_dmp_fraction=min_observed_dmp_fraction,
        )
        # Save results
        extra_cols = None
        if panel_spec and getattr(classifier, "_ovr_mode", False):
            extra_cols, panel_json = _panel_spec_to_csv_columns(
                classifier, feature_matrix, availability_mask, panel_spec, debug=debug
            )
            if output_file is not None:
                pr = Path(output_file).parent / "panel_report.json"
                with open(pr, "w", encoding="utf-8") as f:
                    json.dump(panel_json, f, indent=2)
                print(f"📋 Panel report saved to: {pr}", flush=True)
        derived_cols = _derived_measures_extra_columns(classifier, feature_matrix)
        if derived_cols:
            extra_cols = {**(extra_cols or {}), **derived_cols}
        _save_classification_results(
            classifier, sample_names, predictions, probabilities,
            availability_mask, dmp_positions, output_file,
            expected_classes=single_expected_classes,
            extra_columns=extra_cols,
        )
        if single_expected_classes is not None and len(single_expected_classes) == len(sample_names):
            _print_validation_report(classifier, sample_names, predictions, probabilities, single_expected_classes)


def _classify_single_file_multichrom_dmps(
    classifier: MethylClassifier,
    loaded_samples: List[Tuple[str, Dict[str, Any]]],
    output_file: Optional[Path] = None,
    debug: bool = False,
    expected_classes: Optional[List[int]] = None,
    panel_spec: Optional[Dict[str, Any]] = None,
    min_observed_dmp_fraction: float = 0.0,
) -> None:
    """
    Classify samples using a single-file classifier whose DMPs span multiple chromosomes
    (e.g. multiclass-classifier.pkl). Builds a flat feature matrix in dmp_positions_df row order.
    """
    dmp_df = classifier.dmp_positions_df
    chrom_series = dmp_df["chromosome"].astype(str)
    chrom_order = chrom_series.drop_duplicates().tolist()
    n_dmps = len(dmp_df)
    n_samples = len(loaded_samples)
    feature_matrix = np.zeros((n_samples, n_dmps), dtype=np.float64)
    availability_mask = np.zeros((n_samples, n_dmps), dtype=bool)
    sample_names = []
    pbar = tqdm(enumerate(loaded_samples), total=n_samples, desc="Extracting features", unit="sample")
    for sample_idx, (sample_name, chrom_samples) in pbar:
        if hasattr(pbar, "set_postfix_str"):
            pbar.set_postfix_str(sample_name, refresh=True)
        sample_names.append(sample_name)
        norm_cs = _chromosome_keys_to_str(chrom_samples)
        offset = 0
        for chrom in chrom_order:
            pos_arr = dmp_df.loc[chrom_series == chrom, "position"].values.astype(np.uint32)
            if chrom in norm_cs and len(pos_arr) > 0:
                feats, mask, _ = DataLoader.extract_sample_features(norm_cs[chrom], pos_arr)
                feature_matrix[sample_idx, offset : offset + len(pos_arr)] = feats
                availability_mask[sample_idx, offset : offset + len(pos_arr)] = mask
            else:
                feature_matrix[sample_idx, offset : offset + len(pos_arr)] = 0.5
                availability_mask[sample_idx, offset : offset + len(pos_arr)] = False
            offset += len(pos_arr)

    if getattr(classifier, "_ovr_mode", False):
        print(f"\n🤖 OvR ECDF: scoring {n_samples} sample(s) (batched fused predict)...", flush=True)
        probabilities = classifier.predict_proba(
            feature_matrix, availability_mask, debug
        )
        if expected_classes is not None and len(expected_classes) == n_samples and n_samples >= 2:
            ec = np.array(expected_classes, dtype=np.float64)
            fm = _calibration_fit_mask_for_classifier(classifier, ec)
            probabilities = classifier.calibrate_probabilities(probabilities, ec, fit_mask=fm)
        else:
            probabilities = classifier.calibrate_probabilities(probabilities)
        probabilities, predictions, _abst = apply_min_observed_dmp_abstention(
            np.asarray(probabilities, dtype=np.float64),
            availability_mask,
            float(min_observed_dmp_fraction),
            int(classifier.n_classes),
        )
    else:
        predictions, probabilities, _abst = classify_samples_batch(
            classifier,
            feature_matrix,
            availability_mask,
            debug,
            expected_classes=expected_classes,
            min_observed_dmp_fraction=min_observed_dmp_fraction,
        )
    dmp_positions_flat = dmp_df["position"].values.astype(np.uint32)
    extra_cols = None
    if panel_spec:
        if not getattr(classifier, "_ovr_mode", False):
            print(
                "⚠️ panel_spec ignored: hierarchical panel readout requires OvR mode.",
                flush=True,
            )
        else:
            extra_cols, panel_json = _panel_spec_to_csv_columns(
                classifier, feature_matrix, availability_mask, panel_spec, debug=debug
            )
            if output_file is not None:
                pr = Path(output_file).parent / "panel_report.json"
                with open(pr, "w", encoding="utf-8") as f:
                    json.dump(panel_json, f, indent=2)
                print(f"📋 Panel report saved to: {pr}", flush=True)
    derived_cols = _derived_measures_extra_columns(classifier, feature_matrix)
    if derived_cols:
        extra_cols = {**(extra_cols or {}), **derived_cols}
    _save_classification_results(
        classifier, sample_names, predictions, probabilities,
        availability_mask, dmp_positions_flat, output_file,
        expected_classes=expected_classes,
        extra_columns=extra_cols,
    )
    if expected_classes is not None and len(expected_classes) == len(sample_names):
        _print_validation_report(classifier, sample_names, predictions, probabilities, expected_classes)


def _classify_multi_chromosome_samples(
    classifier: MethylClassifier,
    loaded_samples: List[Tuple[str, Dict[str, Any]]],
    output_file: Optional[Path] = None,
    debug: bool = False,
    expected_classes: Optional[List[int]] = None,
    panel_spec: Optional[Dict[str, Any]] = None,
    min_observed_dmp_fraction: float = 0.0,
) -> None:
    """
    Classify samples using multi-chromosome classifier.

    Extracts features per chromosome from merged samples and combines predictions.
    Optionally saves per-chromosome probabilities to a matrix file.
    When expected_classes is provided (centroid validation), adds expected_class column and prints summary.
    """
    if panel_spec:
        print(
            "⚠️ panel_spec is not supported for legacy multi-chromosome model_dir layout; "
            "use an OvR union PKL (single pass over dmp_positions_df).",
            flush=True,
        )
    print(f"\n📊 Extracting features per chromosome for {len(loaded_samples)} samples...")
    
    # Get all chromosomes from classifier
    classifier_chroms = sorted(classifier.classifiers.keys())
    
    # Collect features per chromosome
    chrom_features = {chrom: [] for chrom in classifier_chroms}
    chrom_masks = {chrom: [] for chrom in classifier_chroms}
    sample_names = []
    
    # Use DataFrame directly from classifier if available (faster)
    if hasattr(classifier, 'dmp_positions_df') and len(classifier.dmp_positions_df) > 0:
        dmp_positions_by_chrom = classifier.dmp_positions_df
    else:
        # Fallback: build dictionary (legacy)
        dmp_positions_by_chrom = {}
        for chrom in classifier_chroms:
            chrom_classifier = classifier.classifiers[chrom]
            feature_info = chrom_classifier.get_feature_info()
            dmp_positions_by_chrom[chrom] = feature_info['positions']
    
    pbar = tqdm(loaded_samples, desc="Extracting features", unit="sample")
    for sample_name, chrom_samples in pbar:
        if hasattr(pbar, "set_postfix_str"):
            pbar.set_postfix_str(sample_name, refresh=True)
        sample_names.append(sample_name)
        norm_cs = _chromosome_keys_to_str(chrom_samples)

        # Extract features for each chromosome
        for chrom in classifier_chroms:
            sk = str(chrom)
            if sk in norm_cs:
                # Get this chromosome's classifier feature info
                chrom_classifier = classifier.classifiers[chrom]
                feature_info = chrom_classifier.get_feature_info()
                dmp_positions = feature_info['positions']

                # Extract features from merged sample for this chromosome
                sample = norm_cs[sk]
                features, mask, _ = DataLoader.extract_sample_features(sample, dmp_positions)
                
                chrom_features[chrom].append(features)
                chrom_masks[chrom].append(mask)
            else:
                # Missing chromosome: use zeros with all unavailable
                feature_info = classifier.classifiers[chrom].get_feature_info()
                n_dmps = feature_info['n_features']
                chrom_features[chrom].append(np.zeros(n_dmps))
                chrom_masks[chrom].append(np.zeros(n_dmps, dtype=bool))
                if debug:
                    print(f"  ⚠️ Sample {sample_name}: missing chromosome {chrom}")
    
    # Convert to arrays per chromosome
    for chrom in classifier_chroms:
        chrom_features[chrom] = np.array(chrom_features[chrom])
        chrom_masks[chrom] = np.array(chrom_masks[chrom])
    
    # One predict_proba pass per chromosome (all samples batched); reuse for weight fitting and combine.
    print(
        f"\n🤖 Running {len(classifier_chroms)} chromosome classifier(s) on {len(loaded_samples)} loaded samples...",
        flush=True,
    )
    cached_chrom_probas = classifier._compute_per_chromosome_probas(
        chrom_features, chrom_masks, progress_bar=len(classifier_chroms) > 1
    )

    # If weight_method is a fitted method and we have validation labels, fit weights from per-chromosome probas
    weight_method = classifier.config.weight_method
    fitted_methods = ("linear_fitted", "logistic_fitted", "elasticnet_fitted")
    if (
        (weight_method in fitted_methods or classifier.config.use_elasticnet_stacking)
        and expected_classes is not None
        and len(expected_classes) == len(loaded_samples)
        and len(loaded_samples) >= 2
    ):
        n_samples = len(loaded_samples)
        chrom_proba_matrix = np.zeros((n_samples, len(classifier_chroms)), dtype=np.float64)
        for i, chrom in enumerate(classifier_chroms):
            chrom_probas = cached_chrom_probas[chrom]
            # P(class1) for binary; column index 1
            chrom_proba_matrix[:, i] = chrom_probas[:, 1] if chrom_probas.shape[1] > 1 else chrom_probas[:, 0]
        
        if classifier.config.use_elasticnet_stacking:
            method = "elasticnet"
        else:
            method = "linear" if weight_method == "linear_fitted" else ("logistic" if weight_method == "logistic_fitted" else "elasticnet")
            
        reg = classifier.config.weight_fit_regularization or "none"
        alpha = classifier.config.weight_fit_alpha
        l1_ratio = classifier.config.weight_fit_l1_ratio
        
        ec_stack = np.array(expected_classes, dtype=np.float64)
        cal_fm = _calibration_fit_mask_for_classifier(classifier, ec_stack)
        classifier.fit_chromosome_weights(
            chrom_proba_matrix,
            ec_stack,
            method=method,
            regularization=reg,
            alpha=alpha,
            l1_ratio=l1_ratio,
            fit_row_mask=cal_fm,
        )

    n_samples = len(sample_names)
    probabilities, per_chrom_probas = classifier._combine_chromosome_probabilities(
        chrom_features,
        chrom_masks,
        debug=debug,
        cached_per_chrom_probas=cached_chrom_probas,
    )
    if (
        expected_classes is not None
        and len(expected_classes) == len(sample_names)
        and len(sample_names) >= 2
        and getattr(classifier.config, "use_isotonic_calibration", False)
    ):
        ec_iso = np.array(expected_classes, dtype=np.float64)
        fm_iso = _calibration_fit_mask_for_classifier(classifier, ec_iso)
        probabilities = classifier.calibrate_probabilities(
            probabilities, ec_iso, fit_mask=fm_iso
        )
    dmp_positions = np.concatenate(
        [
            np.asarray(classifier.classifiers[chrom].get_feature_info()["positions"], dtype=np.uint32)
            for chrom in classifier_chroms
        ]
    )
    combined_mask = np.concatenate(
        [chrom_masks[chrom] for chrom in classifier_chroms],
        axis=1,
    )
    probabilities, predictions, _abst = apply_min_observed_dmp_abstention(
        np.asarray(probabilities, dtype=np.float64),
        combined_mask,
        float(min_observed_dmp_fraction),
        int(classifier.n_classes),
    )

    # Optional chromosome probability matrix (store class-0 probabilities for continuity with existing output)
    chrom_proba_matrix = None
    chromosome_matrix_file = None
    if classifier.config.chromosome_matrix_path is not None:
        chromosome_matrix_file = Path(classifier.config.chromosome_matrix_path)
        chrom_proba_matrix = np.zeros((n_samples, len(classifier_chroms)))
        for i, chrom in enumerate(classifier_chroms):
            chrom_probas = per_chrom_probas.get(chrom)
            if chrom_probas is not None:
                chrom_proba_matrix[:, i] = chrom_probas[:, 0]

    # Diagnostic summary (helps spot collapse to one class)
    prob_c0 = probabilities[:, 0]
    prob_c1 = probabilities[:, 1]
    n_uncertain = np.sum((prob_c1 > 0.05) & (prob_c1 < 0.95))
    print(f"\n📈 Probability summary: P(class0) mean={prob_c0.mean():.3f} min={prob_c0.min():.3f} max={prob_c0.max():.3f} | "
          f"P(class1) mean={prob_c1.mean():.3f} min={prob_c1.min():.3f} max={prob_c1.max():.3f} | "
          f"Samples with 0.05<P(class1)<0.95: {n_uncertain}/{n_samples}")
    if n_uncertain < 0.05 * n_samples and n_samples > 10:
        print("   ⚠️ Most predictions are near 0 or 1. If unexpected, check: DMP coverage (dmps_used in CSV), "
              "temperature in config, or run with --debug.")

    # Save results
    _save_classification_results(
        classifier, sample_names, predictions, probabilities,
        combined_mask, dmp_positions, output_file,
        multi_chromosome=True, chromosomes=classifier_chroms,
        expected_classes=expected_classes
    )

    # Centroid validation summary (binary or multiclass)
    if expected_classes is not None and len(expected_classes) == n_samples:
        _print_validation_report(classifier, sample_names, predictions, probabilities, expected_classes)
        # Multi-chromosome binary debug: mean methylation vs centroid means
        if debug and classifier.n_classes == 2 and hasattr(classifier, 'model_packages') and classifier_chroms:
            expected_classes_arr = np.array(expected_classes)
            first_chrom = classifier_chroms[0]
            pkg = classifier.model_packages.get(first_chrom, {})
            dmpDF = pkg.get('dmpDF')
            if dmpDF is not None and isinstance(dmpDF, pd.DataFrame):
                if 'mean1' in dmpDF.columns and 'mean2' in dmpDF.columns:
                    cent_mean0 = float(np.mean(np.clip(dmpDF['mean1'].values.astype(np.float64), 0, 1)))
                    cent_mean1 = float(np.mean(np.clip(dmpDF['mean2'].values.astype(np.float64), 0, 1)))
                elif 'alpha1' in dmpDF.columns:
                    a1, b1 = dmpDF['alpha1'].values, dmpDF['beta1'].values
                    a2, b2 = dmpDF['alpha2'].values, dmpDF['beta2'].values
                    cent_mean0 = np.mean(np.clip(a1 / (a1 + b1), 0, 1))
                    cent_mean1 = np.mean(np.clip(a2 / (a2 + b2), 0, 1))
                else:
                    cent_mean0 = cent_mean1 = float('nan')
                if np.isfinite(cent_mean0) and np.isfinite(cent_mean1):
                    idx0 = next((i for i in range(n_samples) if expected_classes_arr[i] == 0), None)
                    idx1 = next((i for i in range(n_samples) if expected_classes_arr[i] == 1), None)
                    for idx, label in [(idx0, "expected class 0"), (idx1, "expected class 1")]:
                        if idx is not None and first_chrom in chrom_features and first_chrom in chrom_masks:
                            feats = chrom_features[first_chrom][idx]
                            mask = chrom_masks[first_chrom][idx]
                            if np.any(mask):
                                sample_mean = np.mean(feats[mask])
                                print(f"   Diagnostic ({first_chrom}): first {label} sample mean methylation = {sample_mean:.3f}")
                    print(f"   Diagnostic ({first_chrom}): centroid1 (class0) mean = {cent_mean0:.3f}, centroid2 (class1) mean = {cent_mean1:.3f}")

    # Save chromosome probability matrix if requested
    if chrom_proba_matrix is not None:
        _save_chromosome_probability_matrix(
            sample_names, classifier_chroms, chrom_proba_matrix, chromosome_matrix_file
        )


def _print_validation_report(
    classifier: MethylClassifier,
    sample_names: List[str],
    predictions: np.ndarray,
    probabilities: np.ndarray,
    expected_classes: List[int]
) -> None:
    """Print centroid validation summary: per-class and overall accuracy (binary or multiclass)."""
    n_classes = classifier.n_classes
    class_names = getattr(classifier, 'class_names', None) or [f"Class_{i}" for i in range(n_classes)]
    expected_arr = np.array(expected_classes)
    n_samples = len(expected_arr)

    if n_classes == 2:
        prob_c1 = probabilities[:, 1]
        for exp in (0, 1):
            mask = expected_arr == exp
            n_exp = int(np.sum(mask))
            if n_exp == 0:
                continue
            pred_correct = int(np.sum((expected_arr == exp) & (predictions == exp)))
            mean_p1 = float(np.mean(prob_c1[mask]))
            label = class_names[exp] if exp < len(class_names) else f"class {exp}"
            print(f"\n📊 {label}: {n_exp} samples | mean P(class1)={mean_p1:.3f} | predicted correctly: {pred_correct}/{n_exp} ({100*pred_correct/n_exp:.1f}%)")
        n_correct_0 = int(np.sum((expected_arr == 0) & (predictions == 0)))
        n_correct_1 = int(np.sum((expected_arr == 1) & (predictions == 1)))
        n_exp_0 = int(np.sum(expected_arr == 0))
        n_exp_1 = int(np.sum(expected_arr == 1))
        if (n_exp_0 > 0 and n_correct_0 == 0) or (n_exp_1 > 0 and n_correct_1 == 0):
            print("\n⚠️ Centroid validation failed: at least one expected class has 0% correct predictions.")
            print("   Possible causes: (1) DMP/context mismatch (detector used CG-only; classifier now loads CG-only when model is CG).")
            print("   (2) Very low DMP coverage (check dmps_used in CSV). (3) Try --debug to inspect per-chromosome log-likelihoods.")
            print("   (4) Temperature or calibration: try temperature > 1 for softer probabilities.")
    else:
        # Multiclass: per-class accuracy, overall, balanced accuracy, confusion matrix
        per_class_correct = []
        per_class_total = []
        for k in range(n_classes):
            mask = expected_arr == k
            n_k = int(np.sum(mask))
            correct_k = int(np.sum((expected_arr == k) & (predictions == k)))
            per_class_total.append(n_k)
            per_class_correct.append(correct_k)
        overall_correct = int(np.sum(predictions == expected_arr))
        overall_acc = 100.0 * overall_correct / n_samples if n_samples else 0.0
        acc_per_class = [100.0 * c / t if t else 0.0 for c, t in zip(per_class_correct, per_class_total)]
        balanced_acc = float(np.mean(acc_per_class)) if per_class_total else 0.0
        print("\n📊 Centroid validation (multiclass)")
        for k in range(n_classes):
            label = class_names[k] if k < len(class_names) else f"Class_{k}"
            print(f"   {label}: {per_class_correct[k]}/{per_class_total[k]} correct ({acc_per_class[k]:.1f}%)")
        print(f"   Overall accuracy: {overall_correct}/{n_samples} ({overall_acc:.1f}%)")
        print(f"   Balanced accuracy: {balanced_acc:.1f}%")
        # Compact confusion matrix (rows = expected, cols = predicted)
        print("   Confusion matrix (rows=expected, cols=predicted):")
        for k in range(n_classes):
            row = []
            for j in range(n_classes):
                count = int(np.sum((expected_arr == k) & (predictions == j)))
                row.append(str(count))
            print("     " + " ".join(f"{x:>4}" for x in row))
        if n_classes > 2 and int(np.sum(predictions == 0)) == n_samples:
            print(
                "\n   ⚠️ All samples predicted as class 0. Causes to check: (1) **Low DMP coverage** "
                "on holdouts — missing chroms used to yield fake (0.5,0.5) per head (now NaN → "
                "uniform fusion if all heads lack data; partial missing reweights surviving heads). "
                "(2) **Ambiguous fusion** — near-flat probabilities or control head dominating. "
                "(3) **Batch/cohort shift** vs training."
            )
        if n_classes > 2 and probabilities.shape[0] == n_samples:
            print("   Diagnostic — mean fused P(class) by **expected** cohort (rows in confusion matrix):")
            for k in range(n_classes):
                mask = expected_arr == k
                if not np.any(mask):
                    continue
                sub = probabilities[mask]
                means = np.mean(sub, axis=0)
                label_k = class_names[k] if k < len(class_names) else f"class_{k}"
                parts = [
                    f"{class_names[j] if j < len(class_names) else j}={means[j]:.3f}"
                    for j in range(n_classes)
                ]
                print(f"      expected {label_k} (n={int(np.sum(mask))}): " + " ".join(parts))


def _panel_spec_to_csv_columns(
    classifier: MethylClassifier,
    feature_matrix: np.ndarray,
    availability_mask: np.ndarray,
    panel_spec: Dict[str, Any],
    debug: bool = False,
) -> Tuple[Dict[str, List[Any]], Dict[str, Any]]:
    """
    Build extra CSV columns and a JSON-serializable panel payload from OvR binary heads.
    """
    import re

    from ..core.panel_fusion import compute_panel_outputs

    names = list(classifier.class_names or [])
    bp = classifier.collect_ovr_binary_probas(feature_matrix, availability_mask, debug)
    pmc = classifier.ovr_pairwise_max_contrast_enabled()
    out = compute_panel_outputs(
        bp, names, pairwise_max_contrast=pmc, spec=panel_spec
    )
    fam_keys = list(out["family_keys_order"])
    extra: Dict[str, List[Any]] = {
        "panel_label": [str(x) for x in out["panel_label"].tolist()],
        "panel_code": [int(x) for x in out["panel_code"].tolist()],
        "panel_logit_control": [float(x) for x in out["logit_control"].tolist()],
    }
    for fk in fam_keys:
        safe = re.sub(r"[^0-9a-zA-Z_]+", "_", fk).strip("_") or "family"
        extra[f"panel_logit_family_{safe}"] = [
            float(x) for x in out["family_max_logit"][fk].tolist()
        ]
    json_payload = {
        "spec": dict(panel_spec),
        "primary_family": out["primary_family"],
        "families": out["families"],
        "per_sample": [
            {
                "panel_label": str(out["panel_label"][i]),
                "panel_code": int(out["panel_code"][i]),
                "logit_control": float(out["logit_control"][i]),
                "family_max_logit": {
                    fk: float(out["family_max_logit"][fk][i]) for fk in fam_keys
                },
            }
            for i in range(len(out["panel_label"]))
        ],
    }
    return extra, json_payload


def _derived_measures_extra_columns(
    classifier: MethylClassifier,
    feature_matrix: np.ndarray,
) -> Dict[str, List[Any]]:
    schema = getattr(classifier, "derived_measures_schema", None)
    if not schema and getattr(classifier, "model_packages", None):
        first = next(iter(classifier.model_packages.values()), {})
        if isinstance(first, dict):
            schema = first.get("derived_measures_schema")
    if not schema:
        return {}
    names = list(schema.get("feature_names") or [])
    if not names:
        return {}
    cols: Dict[str, List[Any]] = {n: [] for n in names}
    for i in range(feature_matrix.shape[0]):
        vec, out_names = extract_derived_measures_from_dmp_vector(feature_matrix[i], schema)
        for j, name in enumerate(out_names):
            val = float(vec[j]) if j < len(vec) and np.isfinite(vec[j]) else None
            cols[name].append(val)
    return cols


def _save_classification_results(
    classifier: MethylClassifier,
    sample_names: List[str],
    predictions: np.ndarray,
    probabilities: np.ndarray,
    availability_mask: np.ndarray,
    dmp_positions: np.ndarray,
    output_file: Optional[Path],
    multi_chromosome: bool = False,
    chromosomes: Optional[List[str]] = None,
    expected_classes: Optional[List[int]] = None,
    extra_columns: Optional[Dict[str, List[Any]]] = None,
) -> None:
    """Helper to save classification results to CSV."""
    if output_file is None:
        return

    import csv

    output_file.parent.mkdir(parents=True, exist_ok=True)

    extra_columns = extra_columns or {}
    for col, vals in extra_columns.items():
        if len(vals) != len(sample_names):
            raise ValueError(
                f"extra_columns[{col!r}] length {len(vals)} != sample count {len(sample_names)}"
            )

    # Prepare results data
    results_data = []
    for i, (name, pred, prob) in enumerate(zip(sample_names, predictions, probabilities)):
        pred_i = int(pred)
        if pred_i < 0:
            predicted_label = "abstain"
        elif classifier.class_names is not None and pred_i < len(classifier.class_names):
            predicted_label = str(classifier.class_names[pred_i])
        else:
            predicted_label = f"Class_{pred_i}"

        dmps_used = np.sum(availability_mask[i]) if i < len(availability_mask) else 0

        result_entry = {
            'sample': name,
            'prediction': pred_i,
            'predicted_class': predicted_label,
            'abstained': bool(pred_i < 0),
        }

        # Add probabilities for all classes
        for j in range(classifier.n_classes):
            result_entry[f'prob_class{j}'] = float(prob[j])

        result_entry.update({
            'dmps_used': int(dmps_used),
            'dmps_total': len(dmp_positions)
        })

        if multi_chromosome and chromosomes:
            result_entry['chromosomes'] = ','.join(chromosomes)

        if expected_classes is not None and i < len(expected_classes):
            exp = expected_classes[i]
            result_entry['expected_class'] = int(exp)
            result_entry['agrees'] = bool(pred_i == exp)

        for col, vals in extra_columns.items():
            result_entry[col] = vals[i]

        results_data.append(result_entry)

    # Write CSV
    fieldnames = ['sample', 'prediction', 'predicted_class', 'abstained'] + \
                 [f'prob_class{i}' for i in range(classifier.n_classes)] + \
                 ['dmps_used', 'dmps_total']

    if multi_chromosome and chromosomes:
        fieldnames.append('chromosomes')
    if expected_classes is not None:
        fieldnames.extend(['expected_class', 'agrees'])
    fieldnames.extend(sorted(extra_columns.keys()))

    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results_data)

    print(f"\n💾 Results saved to: {output_file}")


def _save_classifier_and_sample_list(
    classifier: MethylClassifier,
    classifier_config: ClassifierConfig,
    samples_list: Optional[List[str]],
    output_dir: Optional[Path] = None,
) -> None:
    """
    After classification, save the classifier when ``save_classifier_path`` and/or ``project_name``
    is set. ``save_classifier_path`` is typically defaulted by ``resolve_classifier_config`` to
    ``<project>/classifiers/<project_name>-classifier.pkl``.

    output_dir: Parent of classification CSV; used with ``project_name`` when ``save_classifier_path`` is unset.
    """
    project_name = classifier_config.project_name
    save_classifier_path = classifier_config.save_classifier_path
    samples_list_export_path = classifier_config.samples_list_export_path
    if output_dir is None:
        output_dir = Path.cwd()

    do_save_classifier = bool(save_classifier_path or project_name)
    do_export_samples = bool(samples_list_export_path or project_name) and samples_list and len(samples_list) > 0

    if do_save_classifier:
        if save_classifier_path:
            pkl_path = Path(save_classifier_path)
        else:
            pkl_path = output_dir / f"{project_name}-classifier.pkl"
        pkl_path.parent.mkdir(parents=True, exist_ok=True)
        classifier.save(pkl_path)
        print(f"\n💾 Classifier saved to: {pkl_path}")

    if do_export_samples:
        if samples_list_export_path:
            export_path = Path(samples_list_export_path)
        else:
            export_path = output_dir / f"{project_name}-samples.txt"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = export_path.suffix.lower()
        if suffix == ".csv":
            with open(export_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["sample_path"])
                for p in samples_list:
                    writer.writerow([p])
        else:
            with open(export_path, "w") as f:
                for p in samples_list:
                    f.write(p + "\n")
        print(f"\n💾 Sample list exported to: {export_path}")


def _save_chromosome_probability_matrix(
    sample_names: List[str],
    chromosomes: List[str],
    probability_matrix: np.ndarray,
    output_file: Path
) -> None:
    """
    Save chromosome probability matrix to CSV.

    Args:
        sample_names: List of sample names
        chromosomes: List of chromosome names
        probability_matrix: Matrix of shape (n_samples, n_chromosomes) with p1 probabilities
        output_file: Path to output CSV file
    """
    import csv

    # Create header: sample + chromosome names
    fieldnames = ['sample'] + chromosomes

    # Create data rows
    results_data = []
    for i, sample_name in enumerate(sample_names):
        row = {'sample': sample_name}
        for j, chrom in enumerate(chromosomes):
            row[chrom] = probability_matrix[i, j]
        results_data.append(row)

    # Write CSV
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results_data)

    print(f"🧬 Chromosome probability matrix saved to: {output_file}")


def classify_samples_batch(
    classifier: MethylClassifier,
    methylation_data: np.ndarray,
    availability_mask: Optional[np.ndarray] = None,
    debug: bool = False,
    expected_classes: Optional[List[int]] = None,
    min_observed_dmp_fraction: float = 0.0,
):
    """
    Classify a batch of samples using the trained classifier.

    Args:
        classifier: Trained MethylClassifier
        methylation_data: Array of methylation values for the DMP positions
                         Shape should be (n_samples, n_features)
        availability_mask: Boolean mask indicating which positions are available
        debug: If True, enable debug output
        expected_classes: Ground truth labels (used for fitting Isotonic calibration if requested via config)
        min_observed_dmp_fraction: If > 0, samples with fraction of available DMPs below this
            threshold get uniform probabilities and prediction -1 (abstain).

    Returns:
        predictions: Array of class predictions (0 .. n_classes-1, or -1 when abstaining)
        probabilities: Array of posterior probabilities for each class
        abstain_flags: Boolean array, True where prediction was abstained due to low DMP coverage
    """
    # Single predict_proba pass (predict() would call predict_proba again for multi-chromosome).
    probabilities = classifier.predict_proba(methylation_data, availability_mask, debug)

    if expected_classes is not None and len(expected_classes) == methylation_data.shape[0] and len(methylation_data) >= 2:
        ec = np.array(expected_classes, dtype=np.float64)
        fm = _calibration_fit_mask_for_classifier(classifier, ec)
        probabilities = classifier.calibrate_probabilities(probabilities, ec, fit_mask=fm)
    else:
        probabilities = classifier.calibrate_probabilities(probabilities)

    probabilities, predictions, abstain_flags = apply_min_observed_dmp_abstention(
        np.asarray(probabilities, dtype=np.float64),
        availability_mask,
        float(min_observed_dmp_fraction),
        int(classifier.n_classes),
    )

    return predictions, probabilities, abstain_flags


def _apply_cli_path_overrides(config: ClassificationConfig, args: argparse.Namespace) -> None:
    """Apply --model, --model-dir, --input, --output from argparse onto an existing config."""
    if args.model:
        config.model_path = str(args.model)
    if args.model_dir:
        config.model_dir = str(args.model_dir)
    if args.input:
        config.input_path = str(args.input)
    if args.output:
        config.output_path = str(args.output)
    if getattr(args, "min_observed_dmp_fraction", None) is not None:
        config.min_observed_dmp_fraction = float(args.min_observed_dmp_fraction)
    if getattr(args, "residualize_coef_dir", None) is not None:
        config.residualize_coef_dir = str(args.residualize_coef_dir)


def _resolve_ovr_export_output_path(
    config: ClassificationConfig,
    explicit: Optional[str],
) -> Path:
    """
    Output path for --export-ovr-pkl: CLI path wins, else save_classifier_path, else project_name in cwd.
    """
    if explicit:
        return Path(explicit).expanduser()
    if config.save_classifier_path:
        return Path(config.save_classifier_path).expanduser()
    if config.project_name:
        return Path.cwd() / f"{config.project_name}-classifier.pkl"
    raise ValueError(
        "Could not determine export path: pass a path after --export-ovr-pkl, or set "
        "save_classifier_path or project_name in the config / project resolver."
    )


def _export_ovr_pkl_from_config(config: ClassificationConfig, out_path: Path) -> None:
    """Load OvR sources from config and write the portable ecdf_one_vs_rest PKL (no classification)."""
    ovr_p = config.ovr_binary_model_paths or []
    ovr_d = config.ovr_detection_dirs or []
    agg = bool(config.ovr_pairwise_aggregate_control)
    if len(ovr_p) < 2 and len(ovr_d) < 2 and not (agg and len(ovr_d) >= 1):
        raise ValueError(
            "--export-ovr-pkl requires ovr_binary_model_paths or ovr_detection_dirs (length >= 2), "
            "or ovr_pairwise_aggregate_control with at least one ovr_detection_dir, in the resolved config."
        )
    ovr_names = config.ovr_class_names or config.multiclass_class_names
    classifier_config = ClassifierConfig(
        model_path=config.model_path,
        model_dir=config.model_dir,
        ovr_binary_model_paths=config.ovr_binary_model_paths,
        ovr_detection_dirs=config.ovr_detection_dirs,
        ovr_class_names=ovr_names,
        ovr_pairwise_aggregate_control=agg,
        ovr_fuse_mode=config.ovr_fuse_mode,
        temperature=config.temperature,
        enable_platt_calibration=config.enable_platt_calibration,
        class_priors=config.class_priors,
        dependence_block_size=config.dependence_block_size,
        dependence_block_shrinkage=config.dependence_block_shrinkage,
        trimmed_percentile_low=config.trimmed_percentile_low,
        trimmed_percentile_high=config.trimmed_percentile_high,
        chromosome_weights=config.chromosome_weights,
        chromosome_matrix_path=config.chromosome_matrix_path,
        weight_method=config.weight_method,
        weight_fit_regularization=config.weight_fit_regularization,
        weight_fit_alpha=config.weight_fit_alpha,
        weight_fit_l1_ratio=config.weight_fit_l1_ratio,
        use_isotonic_calibration=config.use_isotonic_calibration,
        use_elasticnet_stacking=config.use_elasticnet_stacking,
        calibration_train_fraction=config.calibration_train_fraction,
        calibration_seed=config.calibration_seed,
        project_name=config.project_name,
        save_classifier_path=config.save_classifier_path,
        samples_list_export_path=config.samples_list_export_path,
        panel=config.panel,
    )
    classifier = MethylClassifier(classifier_config)
    if not getattr(classifier, "_ovr_mode", False):
        raise RuntimeError("Internal error: expected OvR mode after loading OvR sources.")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    classifier.save(out_path)
    print(f"\n💾 OvR multiclass bundle exported to: {out_path}")


def _run_one_classification(config: ClassificationConfig, label: Optional[str] = None) -> None:
    """Run classification once with the given config (used for single run and per-cancer-group loop)."""
    DataLoader.residualize_coef_dir = config.residualize_coef_dir
    DataLoader.residualize_chrom = None
    DataLoader.residualize_ctx = None
    ovr_names = config.ovr_class_names or config.multiclass_class_names
    classifier_config = ClassifierConfig(
        model_path=config.model_path,
        model_dir=config.model_dir,
        ovr_binary_model_paths=config.ovr_binary_model_paths,
        ovr_detection_dirs=config.ovr_detection_dirs,
        ovr_class_names=ovr_names,
        ovr_pairwise_aggregate_control=bool(config.ovr_pairwise_aggregate_control),
        ovr_fuse_mode=config.ovr_fuse_mode,
        temperature=config.temperature,
        enable_platt_calibration=config.enable_platt_calibration,
        class_priors=config.class_priors,
        dependence_block_size=config.dependence_block_size,
        dependence_block_shrinkage=config.dependence_block_shrinkage,
        trimmed_percentile_low=config.trimmed_percentile_low,
        trimmed_percentile_high=config.trimmed_percentile_high,
        chromosome_weights=config.chromosome_weights,
        chromosome_matrix_path=config.chromosome_matrix_path,
        weight_method=config.weight_method,
        weight_fit_regularization=config.weight_fit_regularization,
        weight_fit_alpha=config.weight_fit_alpha,
        weight_fit_l1_ratio=config.weight_fit_l1_ratio,
        use_isotonic_calibration=config.use_isotonic_calibration,
        use_elasticnet_stacking=config.use_elasticnet_stacking,
        calibration_train_fraction=config.calibration_train_fraction,
        calibration_seed=config.calibration_seed,
        project_name=config.project_name,
        save_classifier_path=config.save_classifier_path,
        samples_list_export_path=config.samples_list_export_path,
        panel=config.panel,
    )
    classifier = MethylClassifier(classifier_config)
    chrom, context = None, None
    if not config.no_filter and not classifier.is_multi_chromosome:
        model_path_str = config.model_dir or config.model_path
        if model_path_str:
            try:
                chrom, context = extract_chrom_context_from_classifier(Path(model_path_str))
                DataLoader.residualize_chrom = chrom
                DataLoader.residualize_ctx = context
                print(f"📋 Classifier trained on chromosome {chrom}, context {context}")
            except ValueError as e:
                print(f"⚠️ {e}")
    elif classifier.is_multi_chromosome:
        print(f"📋 Multi-chromosome classifier mode: {len(classifier.classifiers)} chromosomes")

    expected_classes = None
    c1_paths = config.centroid1_sample_paths
    c2_paths = config.centroid2_sample_paths
    c1_dir = config.centroid1_dir
    c2_dir = config.centroid2_dir
    if (c1_paths and c2_paths) and (len(c1_paths) > 0 and len(c2_paths) > 0):
        config.samples = list(c1_paths) + list(c2_paths)
        expected_classes = [0] * len(c1_paths) + [1] * len(c2_paths)
        print(f"📂 Centroid validation: {len(c1_paths)} centroid1 + {len(c2_paths)} centroid2 samples")
    elif c1_dir and c2_dir:
        path_remap = config.centroid_path_remap
        sample_root = config.centroid_sample_root
        c1_resolved = _read_samples_used_from_centroid_dir(Path(c1_dir), sample_root=sample_root, path_remap=path_remap)
        c2_resolved = _read_samples_used_from_centroid_dir(Path(c2_dir), sample_root=sample_root, path_remap=path_remap)
        if not c1_resolved or not c2_resolved:
            raise ValueError(
                f"Centroid dirs yielded no samples_used: centroid1_dir={c1_dir} -> {len(c1_resolved)} paths, "
                f"centroid2_dir={c2_dir} -> {len(c2_resolved)} paths."
            )
        config.samples = c1_resolved + c2_resolved
        expected_classes = [0] * len(c1_resolved) + [1] * len(c2_resolved)
        print(f"📂 Centroid validation (from metadata): {len(c1_resolved)} + {len(c2_resolved)} samples")
    elif config.centroid_dirs and len(config.centroid_dirs) > 2:
        centroid_dirs = config.centroid_dirs
        path_remap = config.centroid_path_remap
        sample_root = config.centroid_sample_root
        all_samples = []
        expected_classes = []
        for class_idx, c_dir in enumerate(centroid_dirs):
            resolved = _read_samples_used_from_centroid_dir(Path(c_dir), sample_root=sample_root, path_remap=path_remap)
            if not resolved:
                raise ValueError(
                    f"Centroid dir for class {class_idx} yielded no samples_used: {c_dir}"
                )
            all_samples.extend(resolved)
            expected_classes.extend([class_idx] * len(resolved))
        config.samples = all_samples
        config.expected_classes = expected_classes
        print(f"📂 Multiclass centroid validation: {len(centroid_dirs)} classes, {len(all_samples)} samples")
    elif classifier.is_multi_chromosome and config.input_path and not config.samples:
        input_path = Path(config.input_path)
        if input_path.is_dir():
            sample_dirs = sorted([d for d in input_path.iterdir() if d.is_dir() and list(d.glob("*-CG.h5"))])
            if sample_dirs:
                config.samples = [str(d) for d in sample_dirs]
                print(f"📂 Using {len(sample_dirs)} sample directories under {config.input_path}")

    if config.output_path:
        Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
    if config.samples:
        required_chromosomes = None
        positions = None
        dmp_positions_by_chrom = None
        if classifier.is_multi_chromosome:
            required_chromosomes = list(classifier.classifiers.keys())
            positions = getattr(classifier, 'all_dmp_positions', None)
            dmp_positions_by_chrom = getattr(classifier, 'dmp_positions_by_chrom', None)
        classify_samples_from_list(
            classifier=classifier,
            samples_list=config.samples,
            output_file=Path(config.output_path) if config.output_path else None,
            debug=config.debug,
            required_chromosomes=required_chromosomes,
            positions=positions,
            dmp_positions_by_chrom=dmp_positions_by_chrom,
            expected_classes=expected_classes,
            panel_spec=config.panel,
            min_observed_dmp_fraction=float(config.min_observed_dmp_fraction),
        )
    else:
        classify_samples(
            classifier=classifier,
            h5_path=Path(config.input_path) if config.input_path else None,
            chrom=chrom,
            context=context,
            output_file=Path(config.output_path) if config.output_path else None,
            debug=config.debug,
            panel_spec=config.panel,
            min_observed_dmp_fraction=float(config.min_observed_dmp_fraction),
        )
    output_dir = Path(config.output_path).parent if config.output_path else Path.cwd()
    _save_classifier_and_sample_list(
        classifier, classifier_config, config.samples, output_dir=output_dir
    )


def main():
    """Main command-line interface."""
    parser = argparse.ArgumentParser(
        description="MethylClassifier - Bayesian classification of methylation samples",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using config file (recommended)
  methyl_classifier --config classification_config.json

  # Direct arguments
  methyl_classifier --model classifier.pkl --input samples/ --output results.csv

  # Export multiclass OvR PKL only (no samples / no CSV)
  methyl_classifier --project myproject.json --export-ovr-pkl
  methyl_classifier --config ovr_config.json --export-ovr-pkl /path/to/bundle.pkl

Config fields (in JSON):
  {
    "model_path": "models/classifier-1-CG.pkl",
    "model_dir": null,
    "input_path": "samples/",
    "output_path": "results.csv",
    "temperature": 1.0,
    "enable_platt_calibration": false,
    "trimmed_percentile_low": 0.10,
    "trimmed_percentile_high": 0.01,
    "chromosome_weights": null,
    "debug": false,
    "no_filter": false,
    "log_level": "INFO"
  }
        """
    )
    
    # Required/optional args (keep existing, remove new ones)
    parser.add_argument(
        '--config', '-c',
        type=Path,
        help='Path to configuration JSON file (includes all params like temperature/calibration)'
    )
    parser.add_argument(
        '--project', '-p',
        type=Path,
        metavar='JSON',
        help='Path to pipeline project config; resolves classifier models, inputs, and outputs from the shared project layout'
    )
    parser.add_argument(
        '--step-override',
        type=Path,
        metavar='JSON',
        help='Optional JSON overrides for classifier step when using --project'
    )
    parser.add_argument(
        '--per-cancer-group',
        action='store_true',
        help='With --project: run one classifier per comparison. '
             'Uses models from the matching comparison directory and writes to classifiers/<control>/<disease>/classification_results.csv. '
             'Auto-enabled when the project uses controls/diseases + comparisons.'
    )
    
    parser.add_argument(
        '--model', '-m',
        type=Path,
        help='Path to trained classifier model (.pkl file) - overrides config if provided'
    )
    
    parser.add_argument(
        '--model-dir', '-M',
        type=Path,
        help='Path to directory containing classifier-{chrom}.pkl files (multi-chromosome mode)'
    )
    
    parser.add_argument(
        '--input', '-i',
        type=Path,
        help='Path to input .h5 file or directory - overrides config if provided'
    )
    
    parser.add_argument(
        '--output', '-o',
        type=Path,
        help='Optional output CSV file - overrides config if provided'
    )
    
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        default=False,
        help='Enable debug output'
    )
    
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default='INFO',
        help='Set logging level (default: INFO)'
    )

    parser.add_argument(
        '--min-observed-dmp-fraction',
        type=float,
        default=None,
        metavar='F',
        help=(
            "Override config: require this fraction of panel DMPs observed per sample; "
            "otherwise abstain (uniform probabilities, prediction -1). Omit to use "
            "classification_config min_observed_dmp_fraction (default 0 = off)."
        ),
    )
    
    parser.add_argument(
        '--residualize-coef-dir',
        type=Path,
        default=None,
        help='Optional residualize_fit coefficient directory. When omitted, sample features are raw betas.',
    )

    # No new args for temperature/calibration - handled in config

    parser.add_argument(
        "--export-ovr-pkl",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=(
            "Export multiclass OvR portable PKL only (no per-comparison classification). "
            "With --project + control/disease, the same bundle is also written automatically "
            "after a normal run when step_config.classifier.ovr_binary_pickles_from_comparisons is true. "
            "Requires ovr_binary_model_paths or ovr_detection_dirs in --config or in "
            "step_config.classifier (--project). Optional PATH; if omitted, uses save_classifier_path "
            "or <cwd>/<project_name>-classifier.pkl."
        ),
    )

    args = parser.parse_args()

    # Export-only: single multiclass OvR bundle (not per-comparison control/disease loops)
    if args.export_ovr_pkl is not None:
        if args.project is not None and args.config is not None:
            raise ValueError("Use either --config or --project, not both.")
        if args.project is not None:
            if not args.project.exists():
                raise FileNotFoundError(f"Project config not found: {args.project}")
            use_per_comparison = args.per_cancer_group
            if load_project is not None:
                project = load_project(args.project)
                if project.uses_control_disease():
                    use_per_comparison = True
            if use_per_comparison:
                merged_step = merge_project_classifier_step(
                    args.project, args.step_override
                )
                if not classifier_step_dict_has_ovr_sources(merged_step):
                    raise ValueError(
                        "--export-ovr-pkl with a control/disease (or --per-cancer-group) project "
                        "needs project-wide OvR sources: set ovr_binary_model_paths or "
                        "ovr_detection_dirs (K>=2) in step_config.classifier or --step-override. "
                        "Pairwise per-comparison models are not a single multiclass PKL."
                    )
                # Single bundle from resolve_classifier_config (multiclass paths + merged step).
            config = resolve_classifier_config(args.project, args.step_override)
            _apply_cli_path_overrides(config, args)
        elif args.config is not None:
            with open(args.config, "r") as f:
                config = ClassificationConfig(**json.load(f))
            _apply_cli_path_overrides(config, args)
        else:
            raise ValueError(
                "--export-ovr-pkl requires --config or --project with OvR sources in JSON / step_config."
            )
        setup_logging(config.log_level)
        explicit = (args.export_ovr_pkl or "").strip()
        out_path = _resolve_ovr_export_output_path(
            config, explicit if explicit else None
        )
        try:
            _export_ovr_pkl_from_config(config, out_path)
        except Exception as e:
            import traceback

            print(f"❌ --export-ovr-pkl failed: {e}")
            print(f"   Error type: {type(e).__name__}")
            traceback.print_exc()
            sys.exit(1)
        return

    # Exactly one of --config or --project or (model + input) for config source
    if args.project is not None:
        if args.config is not None:
            raise ValueError("Use either --config or --project, not both.")
        if not args.project.exists():
            raise FileNotFoundError(f"Project config not found: {args.project}")
        # Use per-comparison folder pattern (detections/<control>/<disease>,
        # classifiers/<control>/<disease>) when the project uses control/disease.
        use_per_comparison = args.per_cancer_group
        if load_project is not None:
            project = load_project(args.project)
            if project.uses_control_disease():
                use_per_comparison = True
        if use_per_comparison:
            configs_and_labels = resolve_classifier_config_per_cancer_group(args.project, args.step_override)
            if not configs_and_labels:
                raise ValueError(
                    "Project has fewer than 2 groups; per-comparison mode requires at least one control and one disease group."
                )
            setup_logging("INFO")
            for config, label in configs_and_labels:
                print(f"\n{'='*60}\nClassifier: control vs {label}\n{'='*60}")
                try:
                    _run_one_classification(config, label=label)
                except Exception as e:
                    import traceback
                    print(f"❌ Classification failed for {label}: {e}")
                    traceback.print_exc()
                    sys.exit(1)
            print(f"\nPer-comparison classification complete: {len(configs_and_labels)} group(s)")
            for config, label in configs_and_labels:
                print(f"  {label}: {config.output_path}")
            merged_step = merge_project_classifier_step(args.project, args.step_override)
            if merged_step.get("ovr_binary_pickles_from_comparisons") and classifier_step_dict_has_ovr_sources(
                merged_step
            ):
                print(f"\n{'='*60}\nExporting multiclass OvR bundle for MethylPredictor\n{'='*60}")
                config_mc = resolve_classifier_config(args.project, args.step_override)
                _apply_cli_path_overrides(config_mc, args)
                out_path = _resolve_ovr_export_output_path(config_mc, None)
                try:
                    _export_ovr_pkl_from_config(config_mc, out_path)
                except Exception as e:
                    import traceback

                    print(f"❌ Multiclass OvR export failed: {e}", file=sys.stderr)
                    traceback.print_exc()
                    sys.exit(1)
            return
        config = resolve_classifier_config(args.project, args.step_override)
        _apply_cli_path_overrides(config, args)
    elif args.config:
        with open(args.config, 'r') as f:
            config_data = json.load(f)
        config = ClassificationConfig(**config_data)
        _apply_cli_path_overrides(config, args)
    else:
        # Construct config from CLI args (existing logic)
        config = ClassificationConfig(
            model_path=str(args.model),
            input_path=str(args.input),
            output_path=str(args.output) if args.output else None,
            debug=args.debug,
            log_level=args.log_level
        )
        config.temperature = 1.0
        config.enable_platt_calibration = False
    
    # Setup logging
    setup_logging(config.log_level)

    try:
        _run_one_classification(config)
    except Exception as e:
        import traceback
        print(f"❌ Classification failed: {e}")
        print(f"   Error type: {type(e).__name__}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    import sys
    import traceback
    
    try:
        main()
    except KeyboardInterrupt:
        print("\n❌ Interrupted by user (Ctrl+C)")
        sys.exit(130)
    except SystemExit:
        raise  # Let system exits through
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        print(f"   Error type: {type(e).__name__}")
        print("   Full traceback:")
        traceback.print_exc()
        sys.exit(1)
