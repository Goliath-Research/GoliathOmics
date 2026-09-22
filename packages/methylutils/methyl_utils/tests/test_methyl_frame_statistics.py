#!/usr/bin/env python3
"""
Test suite for MethylFrame statistics and histogram generation.

Tests MethylSample and MethylCentroid (single centroid type):
- Loading samples from CSV files or config.json
- Computing global statistics (averages, totals)
- Generating interactive Plotly HTML histograms

Can be run as pytest tests or as a standalone script.
"""

import tempfile
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
import json
import csv

# Add parent directory to path to find methyl_utils package
_script_dir = Path(__file__).resolve().parent
_packages_dir = _script_dir.parent.parent.parent.parent / "packages" / "methylutils"
if _packages_dir.exists() and str(_packages_dir) not in sys.path:
    sys.path.insert(0, str(_packages_dir))

import numpy as np
import pandas as pd

# pytest is optional - only needed when running pytest tests
try:
    import pytest
except ImportError:
    pytest = None

from methyl_utils.core.methyl_frame import MethylSample, MethylCentroid

# Import helper functions - handle both relative and absolute imports
try:
    from .methyl_frame_stats import (
        load_samples_from_csv,
        load_samples_from_config,
        compute_sample_statistics,
        generate_all_histograms,
        create_mock_sample
    )
except ImportError:
    # Fallback to absolute import when running as script
    from methyl_utils.tests.methyl_frame_stats import (
        load_samples_from_csv,
        load_samples_from_config,
        compute_sample_statistics,
        generate_all_histograms,
        create_mock_sample
    )


# ============================================================================
# Test Functions
# ============================================================================

def test_methyl_sample_statistics_from_csv(tmp_path):
    """Test loading samples from CSV and computing statistics."""
    # Create mock sample directories
    input_dir = tmp_path / "samples"
    input_dir.mkdir()
    
    # Create CSV file with sample folder names
    csv_file = tmp_path / "sample_list.csv"
    sample_folders = ["sample1", "sample2"]
    with open(csv_file, 'w', newline='') as f:
        writer = csv.writer(f)
        for folder in sample_folders:
            writer.writerow([folder])
    
    # Create mock samples
    for folder in sample_folders:
        sample_dir = input_dir / folder
        sample_dir.mkdir()
        
        # Create a few chromosome-context files
        for chrom in ['1', '2']:
            for ctx in ['CG']:
                seed_val = abs(hash(f"{folder}_{chrom}_{ctx}")) % (2**32)
                sample = create_mock_sample(chrom, ctx, n_positions=100, seed=seed_val)
                h5_file = sample_dir / f"{chrom}-{ctx}.h5"
                sample.save_to_h5(h5_file)
    
    # Load samples
    samples = load_samples_from_csv(csv_file, input_dir, chromosomes=['1', '2'], contexts=['CG'])
    
    assert len(samples) > 0, "Should load at least one sample"
    
    # Compute statistics for each sample
    all_stats = []
    for sample in samples:
        stats = compute_sample_statistics(sample)
        assert 'avg_mC' in stats
        assert 'avg_uC' in stats
        assert 'avg_coverage' in stats
        assert 'avg_methylation_level' in stats
        assert 'position_count' in stats
        assert stats['position_count'] > 0
        all_stats.append(stats)
    
    # Generate histograms
    output_dir = tmp_path / "histograms"
    output_dir.mkdir()
    
    for i, sample in enumerate(samples):
        sample_name = f"sample_{i}"
        hist_paths = generate_all_histograms(sample, output_dir, sample_name)
        
        # Verify histogram files were created
        assert 'mC' in hist_paths
        assert 'uC' in hist_paths
        assert 'coverage' in hist_paths
        assert 'methylation_level' in hist_paths
        
        for metric, path in hist_paths.items():
            assert path.exists(), f"Histogram file should exist: {path}"


def test_methyl_sample_statistics_from_config(tmp_path):
    """Test loading samples from config.json and computing statistics."""
    # Create mock sample directories
    sample_dir1 = tmp_path / "sample1"
    sample_dir1.mkdir()
    sample_dir2 = tmp_path / "sample2"
    sample_dir2.mkdir()
    
    # Create mock samples
    for sample_dir in [sample_dir1, sample_dir2]:
        seed_val = abs(hash(str(sample_dir))) % (2**32)
        sample = create_mock_sample('1', 'CG', n_positions=100, seed=seed_val)
        h5_file = sample_dir / "1-CG.h5"
        sample.save_to_h5(h5_file)
    
    # Create config.json
    config_file = tmp_path / "config.json"
    config = {
        "samples": [str(sample_dir1), str(sample_dir2)]
    }
    with open(config_file, 'w') as f:
        json.dump(config, f)
    
    # Load samples
    samples = load_samples_from_config(config_file, chromosomes=['1'], contexts=['CG'])
    
    assert len(samples) >= 2, "Should load at least 2 samples"
    
    # Compute statistics
    for sample in samples:
        stats = compute_sample_statistics(sample)
        assert stats['sample_type'] == 'sample'
        assert stats['position_count'] > 0
        assert stats['avg_coverage'] > 0
    
    # Generate histograms
    output_dir = tmp_path / "histograms"
    output_dir.mkdir()
    
    for i, sample in enumerate(samples):
        sample_name = f"config_sample_{i}"
        hist_paths = generate_all_histograms(sample, output_dir, sample_name)
        assert len(hist_paths) == 4  # mC, uC, coverage, methylation_level


def test_methyl_centroid_statistics(tmp_path):
    """Test statistics computation for MethylCentroid (single centroid type)."""
    # Create multiple samples and save to temp files
    sample_files = []
    for i in range(3):
        sample = create_mock_sample('1', 'CG', n_positions=100, seed=i)
        h5_file = tmp_path / f"sample_{i}.h5"
        sample.save_to_h5(h5_file)
        sample_files.append(h5_file)
    
    # Create basic centroid using MethylCentroidBuilder
    try:
        from methyl_utils.core.centroid_builder import MethylCentroidBuilder
        
        # Try GPU if available, fallback to CPU
        try:
            from methyl_utils.gpu_detection import is_gpu_available
            use_gpu_test = is_gpu_available()
        except ImportError:
            use_gpu_test = False
        builder = MethylCentroidBuilder(min_coverage=1, use_gpu=use_gpu_test)
        for h5_file in sample_files:
            builder.add_sample(h5_file)
        
        centroid = builder.finalize()
    except ImportError:
        import pandas as pd
        samples = [MethylSample.load_from_h5(f) for f in sample_files]
        all_positions = set()
        for sample in samples:
            all_positions.update(sample.pos.values)
        all_positions = np.sort(np.array(list(all_positions), dtype=np.uint32))
        mC_sum = np.zeros(len(all_positions), dtype=np.uint64)
        uC_sum = np.zeros(len(all_positions), dtype=np.uint64)
        N = np.zeros(len(all_positions), dtype=np.uint32)
        Sx = np.zeros(len(all_positions), dtype=np.float64)
        Sx2 = np.zeros(len(all_positions), dtype=np.float64)
        tnc = np.zeros(len(all_positions), dtype=np.uint8)
        for sample in samples:
            sample_pos = sample.pos.values
            sample_mC = sample.mC.values.astype(np.float64)
            sample_uC = sample.uC.values.astype(np.float64)
            cov = sample_mC + sample_uC
            mean = np.where(cov > 0, sample_mC / cov, 0.0)
            idx = np.searchsorted(all_positions, sample_pos)
            valid = (idx < len(all_positions)) & (all_positions[idx] == sample_pos)
            mC_sum[valid] += sample_mC[valid].astype(np.uint64)
            uC_sum[valid] += sample_uC[valid].astype(np.uint64)
            N[valid] += 1
            Sx[valid] += mean[valid]
            Sx2[valid] += mean[valid] ** 2
            tnc[valid] = sample._df["tnc"].values[valid]
        avg_mC = (mC_sum / np.maximum(N, 1)).astype(np.uint32)
        avg_uC = (uC_sum / np.maximum(N, 1)).astype(np.uint32)
        cov = avg_mC + avg_uC
        Sc2 = (cov.astype(np.uint64) ** 2).astype(np.uint32)
        with np.errstate(divide="ignore", invalid="ignore"):
            Swx2 = np.where(cov > 0, (avg_mC.astype(np.float64) ** 2) / cov.astype(np.float64), 0.0).astype(np.float32)
        df = pd.DataFrame({
            "pos": all_positions, "tnc": tnc,
            "N": N, "Sx": Sx.astype(np.float32), "Sx2": Sx2.astype(np.float32),
            "Sm": avg_mC, "Su": avg_uC, "Sc2": Sc2, "Swx2": Swx2,
        })
        centroid = MethylCentroid(df)

    stats = compute_sample_statistics(centroid)
    assert stats["sample_type"] == "centroid"
    assert 'avg_N' in stats
    assert stats['avg_N'] > 0
    assert 'total_samples' in stats
    
    # Generate histograms
    output_dir = tmp_path / "histograms"
    output_dir.mkdir()
    
    hist_paths = generate_all_histograms(centroid, output_dir, "centroid")
    assert len(hist_paths) == 4


def test_methyl_centroid_statistics(tmp_path):
    """Test statistics computation for MethylCentroid."""
    # Create multiple samples and save to temp files
    sample_files = []
    for i in range(3):
        sample = create_mock_sample('1', 'CG', n_positions=100, seed=i)
        h5_file = tmp_path / f"sample_{i}.h5"
        sample.save_to_h5(h5_file)
        sample_files.append(h5_file)
    
    # Create extended centroid using MethylCentroidBuilder
    try:
        from methyl_utils.core.centroid_builder import MethylCentroidBuilder
        
        # Try GPU if available, fallback to CPU
        try:
            from methyl_utils.gpu_detection import is_gpu_available
            use_gpu_test = is_gpu_available()
        except ImportError:
            use_gpu_test = False
        builder = MethylCentroidBuilder(min_coverage=1, use_gpu=use_gpu_test)
        for h5_file in sample_files:
            builder.add_sample(h5_file)
        
        centroid = builder.finalize()
        assert isinstance(centroid, MethylCentroid)
    except ImportError:
        # Fallback: try using add_sample method
        try:
            samples = [MethylSample.load_from_h5(f) for f in sample_files]
            # Create centroid by adding samples (first sample becomes base)
            import pandas as pd

            base_sample = samples[0]
            df = base_sample._df.copy()
            mC = np.asarray(df["mC"].values, dtype=np.uint32)
            uC = np.asarray(df["uC"].values, dtype=np.uint32)
            cov = mC + uC
            with np.errstate(divide="ignore", invalid="ignore"):
                mean = np.where(cov > 0, mC.astype(np.float32) / cov.astype(np.float32), 0.0)
            Sc2 = (cov.astype(np.uint64) ** 2).astype(np.uint32)
            Swx2 = np.where(cov > 0, (mC.astype(np.float64) ** 2) / cov.astype(np.float64), 0.0).astype(np.float32)
            df = pd.DataFrame({
                "pos": df["pos"], "tnc": df["tnc"],
                "N": np.ones(len(df), dtype=np.uint32),
                "Sx": mean.astype(np.float32), "Sx2": (mean ** 2).astype(np.float32),
                "Sm": mC, "Su": uC, "Sc2": Sc2, "Swx2": Swx2,
            })
            centroid = MethylCentroid(df)

            for sample in samples[1:]:
                centroid = centroid.add_sample(sample)
        except Exception as e:
            pytest.skip(f"Could not create centroid: {e}")
    
    # Compute statistics
    stats = compute_sample_statistics(centroid)
    
    assert stats['sample_type'] == 'centroid'
    assert 'avg_N' in stats
    assert 'avg_Sx' in stats
    assert 'avg_Sx2' in stats

    # Generate histograms
    output_dir = tmp_path / "histograms"
    output_dir.mkdir()
    hist_paths = generate_all_histograms(centroid, output_dir, "centroid")
    assert len(hist_paths) == 4


def test_mock_sample_creation():
    """Test that mock sample creation works correctly."""
    sample = create_mock_sample('1', 'CG', n_positions=50, seed=42)
    
    assert isinstance(sample, MethylSample)
    assert len(sample.pos) == 50
    assert len(sample.mC) == 50
    assert len(sample.uC) == 50
    assert sample.metadata['chromosome'] == '1'
    assert sample.metadata['context'] == 'CG'


def test_context_property_collision_fix():
    """Test that context property collision is resolved.

    Ensures that:
    1. frame.context returns DataFrame column (Series) for filtering
    2. frame.context_metadata returns/sets metadata context (str)
    3. cg/chg/chh methods work correctly on MethylCentroid
    """
    # Create test data with CG context (tnc=1). MethylCentroid uses pos, tnc, N, Sx, Sx2, Sm, Su, Sc2, Swx2.
    mC, uC = np.array([10, 20, 30], dtype=np.uint32), np.array([5, 15, 25], dtype=np.uint32)
    cov = mC + uC
    Sx = np.where(cov > 0, mC.astype(np.float32) / cov.astype(np.float32), 0.0)
    test_data = pd.DataFrame({
        'pos': [100, 200, 300],
        'tnc': [1, 1, 1],
        'N': [1, 1, 1],
        'Sx': Sx, 'Sx2': Sx ** 2,
        'Sm': mC, 'Su': uC,
        'Sc2': (cov.astype(np.uint64) ** 2).astype(np.uint32),
        'Swx2': np.where(cov > 0, (mC.astype(np.float64) ** 2) / cov.astype(np.float64), 0.0).astype(np.float32),
    })
    # Base MethylSample can have extra columns; use subset for frame to avoid validation issues
    frame_data = test_data[['pos', 'Sm', 'Su', 'tnc']].copy()
    frame_data = frame_data.rename(columns={'Sm': 'mC', 'Su': 'uC'})
    frame = MethylSample(frame_data, metadata={'context': 'CG'})

    # Test 1: DataFrame context column access works for filtering
    context_series = frame.context
    assert isinstance(context_series, pd.Series), "frame.context should return pandas Series"
    assert context_series.tolist() == ['CG', 'CG', 'CG'], "Context values should be decoded correctly"

    # Test filtering by context column (this was broken before fix)
    cg_filtered = frame[frame.context == "CG"]
    assert len(cg_filtered) == 3, "Should find all CG positions"

    # Test 2: Metadata context access works
    metadata_context = frame.context_metadata
    assert metadata_context == 'CG', "Should return metadata context"

    # Test setting metadata context
    frame.context_metadata = "CHG"
    assert frame.context_metadata == "CHG", "Should be able to set metadata context"

    # Test 3: Extended centroid context methods work
    centroid = MethylCentroid(test_data.copy(), metadata={'context': 'CG'})

    cg_result = centroid.cg()
    assert len(cg_result) == 3, "cg() should return all positions"

    chg_result = centroid.chg()
    assert len(chg_result) == 0, "chg() should return no positions (all are CG)"

    chh_result = centroid.chh()
    assert len(chh_result) == 0, "chh() should return no positions (all are CG)"


def test_statistics_computation():
    """Test that statistics computation works for a simple sample."""
    sample = create_mock_sample('1', 'CG', n_positions=100, seed=123)
    
    stats = compute_sample_statistics(sample)
    
    # Verify all expected keys are present
    required_keys = [
        'position_count', 'avg_mC', 'avg_uC', 'avg_coverage',
        'avg_methylation_level', 'total_mC', 'total_uC', 'total_coverage',
        'min_coverage', 'max_coverage', 'median_coverage', 'sample_type'
    ]
    
    for key in required_keys:
        assert key in stats, f"Missing key: {key}"
    
    # Verify values are reasonable
    assert stats['position_count'] == 100
    assert stats['avg_coverage'] > 0
    assert stats['total_coverage'] > 0


# ============================================================================
# Standalone Script Functionality
# ============================================================================

def main():
    """Main function for standalone execution."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Compute statistics and generate histograms for methylation samples",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Load from CSV file
  python test_methyl_frame_statistics.py --csv sample_list.csv --input-dir /path/to/samples --output-dir results
  
  # Load from config.json
  python test_methyl_frame_statistics.py --config config.json --output-dir results
  
  # Generate mock data for testing
  python test_methyl_frame_statistics.py --mock --output-dir results
        """
    )
    
    # Input options (mutually exclusive)
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--csv', type=Path, help='Path to CSV file with sample folder names')
    input_group.add_argument('--config', type=Path, help='Path to config.json file')
    input_group.add_argument('--mock', action='store_true', help='Generate mock data for testing')
    
    parser.add_argument('--input-dir', type=Path, help='Base directory for samples (required with --csv)')
    parser.add_argument('--output-dir', type=Path, default=Path('output'), help='Directory for histogram outputs')
    parser.add_argument('--chromosomes', nargs='+', help='Chromosomes to process (e.g., 1 2 X)')
    parser.add_argument('--contexts', nargs='+', default=['CG', 'CHG', 'CHH'], help='Contexts to process (default: CG CHG CHH)')
    parser.add_argument('--stats-output', type=Path, help='Path to save statistics summary (CSV or JSON)')
    parser.add_argument('--use-gpu', action='store_true', help='Use GPU acceleration (if available)')
    parser.add_argument('--no-gpu', action='store_true', help='Force CPU-only processing')
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.csv and not args.input_dir:
        parser.error("--input-dir is required when using --csv")
    
    if args.csv and not args.csv.exists():
        parser.error(f"CSV file not found: {args.csv}")
    
    if args.config and not args.config.exists():
        parser.error(f"Config file not found: {args.config}")
    
    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)
    
    # Determine GPU usage
    use_gpu = args.use_gpu and not args.no_gpu
    if use_gpu:
        try:
            from methyl_utils.gpu_detection import is_gpu_available, get_gpu_memory_gb, get_gpu_device_count
            gpu_available = is_gpu_available()
            if gpu_available:
                gpu_memory = get_gpu_memory_gb()
                gpu_count = get_gpu_device_count()
                print(f"\n🚀 GPU Acceleration Enabled")
                print(f"   GPU Devices: {gpu_count}")
                print(f"   GPU Memory: {gpu_memory:.1f} GB")
            else:
                print("\n⚠️  GPU requested but not available, falling back to CPU")
                use_gpu = False
        except ImportError:
            print("\n⚠️  GPU detection not available, using CPU")
            use_gpu = False
    else:
        print("\n💻 Using CPU processing")
    
    # Load samples
    samples = []
    sample_names = []
    
    if args.mock:
        print("Generating mock samples...")
        for chrom in (args.chromosomes or ['1', '2']):
            for ctx in args.contexts:
                # Ensure seed is in valid range [0, 2**32-1]
                seed = abs(hash(f"{chrom}_{ctx}")) % (2**32)
                sample = create_mock_sample(chrom, ctx, n_positions=1000, seed=seed)
                samples.append(sample)
                sample_names.append(f"mock_{chrom}_{ctx}")
    
    elif args.csv:
        print(f"Loading samples from CSV: {args.csv}")
        samples = load_samples_from_csv(args.csv, args.input_dir, args.chromosomes, args.contexts)
        sample_names = [f"sample_{i}" for i in range(len(samples))]
    
    elif args.config:
        print(f"Loading samples from config: {args.config}")
        samples = load_samples_from_config(args.config, args.chromosomes, args.contexts)
        sample_names = [f"sample_{i}" for i in range(len(samples))]
    
    if not samples:
        print("No samples loaded!")
        sys.exit(1)
    
    print(f"Loaded {len(samples)} samples")
    
    # Compute statistics for all samples
    print("\nComputing statistics...")
    all_stats = []
    for i, sample in enumerate(samples):
        stats = compute_sample_statistics(sample)
        stats['sample_index'] = i
        stats['sample_name'] = sample_names[i] if i < len(sample_names) else f"sample_{i}"
        all_stats.append(stats)
        print(f"  Sample {i+1}/{len(samples)}: {stats.get('position_count', 0)} positions")
    
    # Generate histograms
    print(f"\nGenerating histograms in {args.output_dir}...")
    for i, (sample, name) in enumerate(zip(samples, sample_names)):
        try:
            hist_paths = generate_all_histograms(sample, args.output_dir, name)
            print(f"  Generated histograms for {name}")
        except Exception as e:
            print(f"  Warning: Failed to generate histograms for {name}: {e}")
    
    # Save statistics summary
    if args.stats_output:
        print(f"\nSaving statistics summary to {args.stats_output}...")
        stats_df = pd.DataFrame(all_stats)
        
        if args.stats_output.suffix.lower() == '.json':
            stats_df.to_json(args.stats_output, orient='records', indent=2)
        else:
            stats_df.to_csv(args.stats_output, index=False)
        
        print(f"  Saved statistics for {len(all_stats)} samples")
    
    # Print summary table
    print("\n" + "="*80)
    print("Statistics Summary")
    print("="*80)
    stats_df = pd.DataFrame(all_stats)
    print(stats_df[['sample_name', 'position_count', 'avg_mC', 'avg_uC', 'avg_coverage', 'avg_methylation_level']].to_string(index=False))
    print("="*80)
    
    print(f"\n✓ Processing complete!")
    print(f"  Samples processed: {len(samples)}")
    print(f"  Histograms saved to: {args.output_dir}")


if __name__ == "__main__":
    main()

