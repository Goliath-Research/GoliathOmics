#!/usr/bin/env python3
"""
Example script showing how to use the MethylFrame statistics functions programmatically.

This demonstrates:
1. Loading samples from CSV or config.json
2. Computing statistics
3. Generating histograms
4. Working with centroids
"""

from pathlib import Path
from methyl_utils.tests.methyl_frame_stats import (
    load_samples_from_csv,
    load_samples_from_config,
    compute_sample_statistics,
    generate_all_histograms,
    create_mock_sample
)
from methyl_utils.core.methyl_frame import MethylSample, MethylCentroid
from methyl_utils.core.centroid_builder import MethylCentroidBuilder
import pandas as pd


def example_1_mock_data():
    """Example 1: Create mock samples and compute statistics."""
    print("=" * 80)
    print("Example 1: Mock Data")
    print("=" * 80)
    
    # Create mock samples
    samples = []
    for chrom in ['1', '2']:
        for ctx in ['CG']:
            sample = create_mock_sample(chrom, ctx, n_positions=1000, seed=42)
            samples.append(sample)
            print(f"Created mock sample: {chrom}-{ctx} ({len(sample.pos)} positions)")
    
    # Compute statistics
    print("\nStatistics:")
    for i, sample in enumerate(samples):
        stats = compute_sample_statistics(sample)
        print(f"\nSample {i+1}:")
        print(f"  Position count: {stats['position_count']}")
        print(f"  Avg mC: {stats['avg_mC']:.2f}")
        print(f"  Avg uC: {stats['avg_uC']:.2f}")
        print(f"  Avg coverage: {stats['avg_coverage']:.2f}")
        print(f"  Avg methylation level: {stats['avg_methylation_level']:.4f}")
    
    # Generate histograms
    output_dir = Path("example_output")
    output_dir.mkdir(exist_ok=True)
    
    print(f"\nGenerating histograms in {output_dir}...")
    for i, sample in enumerate(samples):
        hist_paths = generate_all_histograms(sample, output_dir, f"mock_sample_{i}")
        print(f"  Generated histograms for sample {i+1}")


def example_2_load_from_csv():
    """Example 2: Load samples from CSV file."""
    print("\n" + "=" * 80)
    print("Example 2: Load from CSV")
    print("=" * 80)
    
    csv_path = Path("example_sample_list.csv")
    input_dir = Path("/path/to/samples")  # Update this path
    
    if not csv_path.exists():
        print(f"CSV file not found: {csv_path}")
        print("Create example_sample_list.csv with sample folder names")
        return
    
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}")
        print("Update the input_dir path in this script")
        return
    
    # Load samples
    samples = load_samples_from_csv(
        csv_path=csv_path,
        input_dir=input_dir,
        chromosomes=['1', '2'],  # Only load chromosomes 1 and 2
        contexts=['CG']  # Only load CG context
    )
    
    print(f"Loaded {len(samples)} samples")
    
    # Compute and print statistics
    for sample in samples:
        stats = compute_sample_statistics(sample)
        print(f"\nSample: {stats.get('sample_name', 'unknown')}")
        print(f"  Chromosome: {stats.get('chromosome', 'N/A')}")
        print(f"  Context: {stats.get('context', 'N/A')}")
        print(f"  Positions: {stats['position_count']}")
        print(f"  Avg coverage: {stats['avg_coverage']:.2f}")


def example_3_load_from_config():
    """Example 3: Load samples from config.json."""
    print("\n" + "=" * 80)
    print("Example 3: Load from config.json")
    print("=" * 80)
    
    config_path = Path("example_config.json")
    
    if not config_path.exists():
        print(f"Config file not found: {config_path}")
        print("Create example_config.json with sample paths")
        return
    
    # Load samples
    samples = load_samples_from_config(
        config_path=config_path,
        chromosomes=['1'],  # Only chromosome 1
        contexts=['CG', 'CHG']  # CG and CHG contexts
    )
    
    print(f"Loaded {len(samples)} samples")
    
    # Compute statistics
    all_stats = []
    for sample in samples:
        stats = compute_sample_statistics(sample)
        all_stats.append(stats)
    
    # Create summary DataFrame
    df = pd.DataFrame(all_stats)
    print("\nSummary Statistics:")
    print(df[['sample_name', 'position_count', 'avg_coverage', 'avg_methylation_level']].to_string(index=False))


def example_4_create_centroid():
    """Example 4: Create centroid from samples and compute statistics."""
    print("\n" + "=" * 80)
    print("Example 4: Create Centroid")
    print("=" * 80)
    
    # Create multiple mock samples
    print("Creating mock samples...")
    sample_files = []
    temp_dir = Path("temp_samples")
    temp_dir.mkdir(exist_ok=True)
    
    for i in range(3):
        sample = create_mock_sample('1', 'CG', n_positions=500, seed=i)
        h5_file = temp_dir / f"sample_{i}.h5"
        sample.save_to_h5(h5_file)
        sample_files.append(h5_file)
        print(f"  Created sample {i+1}: {h5_file}")
    
    # Create extended centroid using MethylCentroidBuilder
    print("\nCreating extended centroid...")
    builder = MethylCentroidBuilder(min_coverage=1, use_gpu=False)
    for h5_file in sample_files:
        builder.add_sample(h5_file)
    
    centroid = builder.finalize()
    print(f"  Created centroid with {len(centroid.pos)} positions")
    print(f"  Sample type: {centroid.sample_type}")
    
    # Compute statistics
    stats = compute_sample_statistics(centroid)
    print("\nCentroid Statistics:")
    print(f"  Position count: {stats['position_count']}")
    print(f"  Avg N (samples per position): {stats['avg_N']:.2f}")
    print(f"  Avg coverage: {stats['avg_coverage']:.2f}")
    print(f"  Avg methylation level: {stats['avg_methylation_level']:.4f}")
    
    if 'avg_Sx' in stats:
        print(f"  Avg Sx: {stats['avg_Sx']:.4f}")
        print(f"  Avg Sx2: {stats['avg_Sx2']:.4f}")
    
    # Generate histograms
    output_dir = Path("example_output")
    output_dir.mkdir(exist_ok=True)
    hist_paths = generate_all_histograms(centroid, output_dir, "centroid_example")
    print(f"\nGenerated histograms: {list(hist_paths.keys())}")
    
    # Cleanup
    import shutil
    shutil.rmtree(temp_dir)
    print(f"\nCleaned up temporary files in {temp_dir}")


def main():
    """Run all examples."""
    print("MethylFrame Statistics Examples")
    print("=" * 80)
    
    # Example 1: Mock data (always works)
    example_1_mock_data()
    
    # Example 2: CSV (requires real data)
    # example_2_load_from_csv()
    
    # Example 3: Config.json (requires real data)
    # example_3_load_from_config()
    
    # Example 4: Create centroid
    example_4_create_centroid()
    
    print("\n" + "=" * 80)
    print("Examples complete!")
    print("=" * 80)
    print("\nCheck the 'example_output' directory for generated histograms.")


if __name__ == "__main__":
    main()

