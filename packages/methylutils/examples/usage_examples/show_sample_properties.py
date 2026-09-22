#!/usr/bin/env python3
"""
Display all properties of a methylation sample (.h5 file).

This script loads a MethylSample from an HDF5 file and displays all available
properties including core data, centroid data, metadata, and statistics.
"""

import sys
from pathlib import Path
import numpy as np

# Add the parent directory to the Python path to allow imports
# This allows the script to be run from anywhere
current_dir = Path(__file__).resolve().parent
methylutils_dir = current_dir.parent.parent
sys.path.insert(0, str(methylutils_dir))

# Import HDF5 dependencies - must import hdf5plugin before h5py
import hdf5plugin  # noqa: F401
import h5py


def format_array_summary(arr, max_items=5):
    """Format array summary for display."""
    if arr is None:
        return "N/A"
    
    if len(arr) == 0:
        return "Empty array"
    
    arr_str = f"Shape: {arr.shape}, dtype: {arr.dtype}"
    arr_str += f"\n    Min: {np.min(arr)}, Max: {np.max(arr)}, Mean: {np.mean(arr):.2f}"
    
    if len(arr) <= max_items:
        arr_str += f"\n    Values: {arr}"
    else:
        arr_str += f"\n    First {max_items} values: {arr[:max_items]}"
        arr_str += f"\n    Last {max_items} values: {arr[-max_items:]}"
    
    return arr_str


def display_sample_properties(h5_path: Path):
    """
    Display all properties of a MethylSample loaded from an HDF5 file.
    
    Args:
        h5_path: Path to the HDF5 file
    """
    try:
        from methyl_utils.core.methyl_frame import MethylSample
    except ImportError:
        print("Error: Could not import MethylSample from methyl_utils")
        print("Make sure the methylation pipeline is properly installed.")
        sys.exit(1)
    
    print("=" * 80)
    print("METHYLSAMPLE PROPERTIES")
    print("=" * 80)
    print(f"\nFile: {h5_path}")
    print(f"File exists: {h5_path.exists()}")
    print(f"File size: {h5_path.stat().st_size / (1024*1024):.2f} MB")
    print()
    
    # Load the sample
    try:
        print("Loading sample...")
        sample = MethylSample.load_from_h5(h5_path)
        print("Sample loaded successfully!")
        print()
    except Exception as e:
        print(f"Error loading sample: {e}")
        sys.exit(1)
    
    # ========================================================================
    # BASIC PROPERTIES
    # ========================================================================
    print("=" * 80)
    print("BASIC PROPERTIES")
    print("=" * 80)
    
    print(f"\nSample Type: {sample.sample_type}")
    print(f"Is Centroid: {sample.is_centroid}")
    print(f"Is Centroid: {sample.is_centroid}")
    print(f"Position Count: {sample.position_count:,}")
    print(f"Memory Usage: {sample.memory_usage_mb:.2f} MB")
    print(f"Bytes per Position: {sample.bytes_per_position:.2f}")
    
    # ========================================================================
    # CORE DATA ARRAYS
    # ========================================================================
    print("\n" + "=" * 80)
    print("CORE DATA ARRAYS")
    print("=" * 80)
    
    print("\n1. Positions (pos):")
    print(f"   {format_array_summary(sample.pos)}")
    
    print("\n2. Methylated Counts (mC):")
    print(f"   {format_array_summary(sample.mC)}")
    
    print("\n3. Unmethylated Counts (uC):")
    print(f"   {format_array_summary(sample.uC)}")
    
    print("\n4. Trinucleotide Context (tnc):")
    print(f"   {format_array_summary(sample.tnc)}")
    
    # ========================================================================
    # CENTROID-SPECIFIC DATA
    # ========================================================================
    if sample.is_centroid:
        print("\n" + "=" * 80)
        print("CENTROID-SPECIFIC DATA")
        print("=" * 80)
        
        print("\n5. Sample Count (N):")
        print(f"   {format_array_summary(sample.N)}")
        
        if sample.Sx is not None:
            print("\n6. Sum of Methylation Levels (Sx):")
            print(f"   {format_array_summary(sample.Sx)}")
        
        if sample.Sx2 is not None:
            print("\n7. Sum of Squared Methylation Levels (Sx2):")
            print(f"   {format_array_summary(sample.Sx2)}")

    # ========================================================================
    # METADATA
    # ========================================================================
    print("\n" + "=" * 80)
    print("METADATA")
    print("=" * 80)
    
    if sample.metadata:
        print("\nStandard Metadata Fields:")
        print(f"  Laboratory: {sample.laboratory}")
        print(f"  Disease: {sample.disease}")
        print(f"  Group: {sample.group}")
        print(f"  Batch: {sample.batch}")
        print(f"  Chromosome: {sample.chromosome}")
        print(f"  Context: {sample.context}")
        print(f"  Group Name: {sample.group_name}")
        
        # Display sample-related information (important for centroids)
        if sample.is_centroid:
            print("\n" + "-" * 80)
            print("CENTROID SAMPLES")
            print("-" * 80)
            
            samples_to_display = sample.samples           
            print(f"\nNumber of samples in this centroid: {len(samples_to_display)}")
            print("\nSample file paths:")
            for i, path in enumerate(samples_to_display, 1):
                print(f"  {i}. {path}")
            print(f"  (Total: {len(samples_to_display)} files)")
            
            # Also check for other sample-related keys in metadata
            sample_keys = [k for k in sample.metadata.keys() if 'sample' in k.lower()]
            if sample_keys:
                print(f"\nOther sample-related metadata keys: {sample_keys}")
                for key in sample_keys:
                    if key != 'sample_paths':  # Already displayed above
                        value = sample.metadata[key]
                        if isinstance(value, (list, tuple)) and len(value) > 5:
                            print(f"  {key}: (list with {len(value)} items)")
                        elif isinstance(value, (list, tuple)) and len(value) <= 5:
                            print(f"  {key}:")
                            for item in value:
                                print(f"      - {item}")
                        elif isinstance(value, str) and len(value) > 100:
                            print(f"  {key}: {value[:100]}...")
                        else:
                            print(f"  {key}: {value}")
    else:
        print("\nNo metadata available")
    
    # Display full metadata
    if sample.metadata:
        print("\n" + "-" * 80)
        print("ALL METADATA KEYS AND VALUES")
        print("-" * 80)
        print(f"\nAll Metadata Keys: {list(sample.metadata.keys())}")
        
        print("\nFull Metadata Dictionary:")
        for key, value in sample.metadata.items():
            # Skip sample-related keys if already shown in CENTROID SAMPLES section
            if key in ['sample_paths', 'samples_used'] and sample.is_centroid:
                continue
                
            if isinstance(value, (list, tuple)) and len(value) > 5:
                print(f"  {key}: (list with {len(value)} items)")
                # Show first few items for smaller lists
                if len(value) <= 10:
                    for item in value[:5]:
                        print(f"      - {item}")
                    if len(value) > 5:
                        print(f"      ... and {len(value) - 5} more items")
            elif isinstance(value, str) and len(value) > 100:
                print(f"  {key}: {value[:100]}...")
            else:
                print(f"  {key}: {value}")
    
    # ========================================================================
    # STATISTICAL PROPERTIES
    # ========================================================================
    print("\n" + "=" * 80)
    print("STATISTICAL PROPERTIES")
    print("=" * 80)
    
    print("\nComputed Statistics:")
    try:
        alpha = sample.alpha
        beta = sample.beta
        mean = sample.mean
        variance = sample.variance
        tau = sample.tau
        
        print(f"  Alpha (min/max/mean): {np.min(alpha):.2f} / {np.max(alpha):.2f} / {np.mean(alpha):.2f}")
        print(f"  Beta (min/max/mean): {np.min(beta):.2f} / {np.max(beta):.2f} / {np.mean(beta):.2f}")
        print(f"  Mean (min/max/mean): {np.min(mean):.2f} / {np.max(mean):.2f} / {np.mean(mean):.2f}")
        print(f"  Variance (min/max/mean): {np.min(variance):.2f} / {np.max(variance):.2f} / {np.mean(variance):.2f}")
        print(f"  Tau/Precision (min/max/mean): {np.min(tau):.2f} / {np.max(tau):.2f} / {np.mean(tau):.2f}")
    except Exception as e:
        print(f"  Error computing statistical properties: {e}")
    
    # Coverage statistics
    print("\nCoverage Statistics:")
    try:
        coverage_stats = sample.coverage_stats
        for key, value in coverage_stats.items():
            if isinstance(value, float):
                print(f"  {key}: {value:.2f}")
            else:
                print(f"  {key}: {value}")
    except Exception as e:
        print(f"  Error computing coverage statistics: {e}")
    
    # Methylation statistics
    print("\nMethylation Statistics:")
    try:
        meth_stats = sample.methylation_stats
        for key, value in meth_stats.items():
            if isinstance(value, (int, float)):
                print(f"  {key}: {value:.2f}" if isinstance(value, float) else f"  {key}: {value}")
            elif isinstance(value, np.ndarray):
                print(f"  {key}: array with {len(value)} values")
            else:
                print(f"  {key}: {value}")
    except Exception as e:
        print(f"  Error computing methylation statistics: {e}")
    
    # ========================================================================
    # COMPUTED PROPERTIES
    # ========================================================================
    print("\n" + "=" * 80)
    print("COMPUTED PROPERTIES")
    print("=" * 80)
    
    # Methylation levels
    print("\nMethylation Levels (computed):")
    try:
        meth_levels = sample.get_methylation_levels()
        print(f"  {format_array_summary(meth_levels)}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Coverage
    print("\nCoverage (computed):")
    try:
        coverage = sample.get_coverage()
        print(f"  {format_array_summary(coverage)}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Sample count (if centroid)
    if sample.is_centroid:
        print("\nSample Count per Position (computed):")
        try:
            sample_count = sample.get_sample_count()
            print(f"  {format_array_summary(sample_count)}")
        except Exception as e:
            print(f"  Error: {e}")
    
    # Position information
    print("\nPosition Information:")
    try:
        pos_info = sample.get_position_info()
        for key, value in pos_info.items():
            if isinstance(value, float):
                print(f"  {key}: {value:.2f}")
            else:
                print(f"  {key}: {value}")
    except Exception as e:
        print(f"  Error: {e}")
    
    # ========================================================================
    # SUMMARY
    # ========================================================================
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    print(f"\nSample loaded from: {h5_path}")
    print(f"Sample type: {sample.sample_type}")
    print(f"Total positions: {sample.position_count:,}")
    print(f"Memory usage: {sample.memory_usage_mb:.2f} MB")
    
    if sample.metadata:
        print(f"Metadata fields: {len(sample.metadata)}")
    
    print("=" * 80)


def main():
    """Main entry point for the script."""
    if len(sys.argv) < 2:
        print("Usage: python show_sample_properties.py <sample.h5>")
        print()
        print("Description:")
        print("  Display all properties of a methylation sample from an HDF5 file.")
        print()
        print("Examples:")
        print("  python show_sample_properties.py /path/to/sample.h5")
        print("  python show_sample_properties.py ./data/centroids/chr1-CG.h5")
        sys.exit(1)
    
    h5_path = Path(sys.argv[1])
    
    if not h5_path.exists():
        print(f"Error: File not found: {h5_path}")
        sys.exit(1)
    
    if not h5_path.suffix.lower() in ['.h5', '.hdf5']:
        print(f"Warning: File does not have .h5 or .hdf5 extension: {h5_path}")
    
    display_sample_properties(h5_path)


if __name__ == '__main__':
    main()

