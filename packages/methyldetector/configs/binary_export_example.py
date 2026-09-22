#!/usr/bin/env python3
"""
Example script demonstrating binary export functionality for DMP and DMR data.

This script shows how to use MethylDetector to export significant
positions and regions in both Parquet and HDF5 formats with the naming convention:
- {chr}-{ctx}-dmp.ext for differentially methylated positions
- {chr}-{ctx}-dmr.ext for differentially methylated regions

The HDF5 files use Z-standard compression (level 9) for optimal file size.
"""

import sys
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from methyl_detector import MethylDetector
from methyl_detector.models.config import MethylDetectorConfig


def main():
    """Demonstrate binary export functionality."""
    print("MethylDetector Binary Export Example")
    print("=" * 50)
    
    # Example file paths (modify these to match your actual files)
    centroid1_path = Path("data/centroids/WT/1-CG.h5")
    centroid2_path = Path("data/centroids/msh1/1-CG.h5")
    output_dir = Path("results/binary_export_example")
    
    # Check if example files exist
    if not centroid1_path.exists():
        print(f"❌ Centroid 1 file not found: {centroid1_path}")
        print("Please modify the paths in this script to point to your actual centroid files.")
        print("Expected naming convention: {chromosome}-{context}.h5 (e.g., 1-CG.h5)")
        return
    
    if not centroid2_path.exists():
        print(f"❌ Centroid 2 file not found: {centroid2_path}")
        print("Please modify the paths in this script to point to your actual centroid files.")
        return
    
    print(f"📁 Centroid 1: {centroid1_path}")
    print(f"📁 Centroid 2: {centroid2_path}")
    print(f"📁 Output directory: {output_dir}")
    print()
    
    # Example configuration with DMP filtering enabled
    config = MethylDetectorConfig(
        centroid1_path=Path("/home/ubuntu/Work/output_workflows/arabidopsis/centroids/WT/1-CG.h5"),
        centroid2_path=Path("/home/ubuntu/Work/output_workflows/arabidopsis/centroids/msh1/1-CG.h5"),
        output_dir=Path("./example_results"),
        alpha=0.05,
        min_N=10,
        apply_fdr_correction=True,
        fdr_method="storey",
        global_significance_threshold=0.05,

        # DMP filtering parameters (effect_size from MethylCentroidPair; min_effect_size = minimum effect_size threshold)
        apply_dmp_filtering=True,
        dmp_filter_method="combined",
        min_effect_size=0.5,
        min_cohen_d=0.8,
        min_auc=0.7,
        max_selected_dmps=100
    )
    
    print("🔧 Configuration created successfully")
    print(f"   - Significance level: {config.alpha}")
    print(f"   - FDR correction: {config.apply_fdr_correction}")
    print(f"   - FDR method: {config.fdr_method}")
    print(f"   - Global significance threshold: {config.global_significance_threshold}")
    print()
    
    try:
        # Initialize detector
        print("🚀 Initializing MethylDetector...")
        detector = MethylDetector(config)
        
        # Run analysis
        print("🔬 Running DMP detection analysis...")
        result = detector.run()
        
        print("✅ Analysis completed successfully!")
        print()
        
        # Display results summary
        if result.comparisons:
            comparison = result.comparisons[0]
            print("📊 Results Summary:")
            print(f"   - Total positions: {comparison.total_positions:,}")
            print(f"   - Significant positions: {comparison.significant_count:,}")
            print(f"   - Significant fraction: {comparison.significant_fraction:.2%}")
            print(f"   - Processing time: {comparison.processing_time_seconds:.2f} seconds")
            print(f"   - GPU acceleration: {'Yes' if comparison.gpu_acceleration else 'No'}")
            print()
        
        # Check for binary export files
        print("📁 Checking for binary export files...")
        
        # Extract chromosome and context from filename
        chrom_info = detector.config.centroid1_path.stem.split('-')
        if len(chrom_info) == 2:
            chromosome, context = chrom_info
            print(f"   - Chromosome: {chromosome}")
            print(f"   - Context: {context}")
            print()
            
            # Expected binary export files
            expected_files = [
                f"{chromosome}-{context}-dmp.parquet",
                f"{chromosome}-{context}-dmp.h5",
                f"{chromosome}-{context}-dmr.parquet",
                f"{chromosome}-{context}-dmr.h5"
            ]
            
            print("📦 Binary Export Files:")
            for filename in expected_files:
                file_path = output_dir / filename
                if file_path.exists():
                    file_size = file_path.stat().st_size
                    file_size_mb = file_size / (1024 * 1024)
                    print(f"   ✅ {filename} ({file_size_mb:.2f} MB)")
                    
                    # Show additional info for HDF5 files
                    if filename.endswith('.h5'):
                        try:
                            import h5py
                            with h5py.File(file_path, 'r') as f:
                                if 'dmp_data' in f:
                                    dmp_group = f['dmp_data']
                                    total_positions = dmp_group.attrs.get('total_positions', 'N/A')
                                    print(f"      📊 Contains {total_positions} DMP positions")
                                elif 'dmr_data' in f:
                                    dmr_group = f['dmr_data']
                                    total_regions = dmr_group.attrs.get('total_regions', 'N/A')
                                    print(f"      📊 Contains {total_regions} DMR regions")
                        except Exception as e:
                            print(f"      ⚠️  Error reading HDF5 metadata: {e}")
                    
                    # Show additional info for Parquet files
                    elif filename.endswith('.parquet'):
                        try:
                            import pandas as pd
                            df = pd.read_parquet(file_path)
                            print(f"      📊 Contains {len(df)} rows")
                            print(f"      📋 Columns: {', '.join(df.columns)}")
                        except Exception as e:
                            print(f"      ⚠️  Error reading Parquet file: {e}")
                else:
                    print(f"   ❌ {filename} (not found)")
                    if "dmp" in filename and comparison.significant_count == 0:
                        print(f"      ℹ️  No significant positions found, DMP file not created")
                    elif "dmr" in filename and not hasattr(comparison, 'significant_regions'):
                        print(f"      ℹ️  No significant regions found, DMR file not created")
            
            print()
            
            # Show CSV files for comparison
            csv_files = list(output_dir.glob("*.csv"))
            if csv_files:
                print("📄 CSV Export Files (for comparison):")
                for csv_file in csv_files:
                    file_size = csv_file.stat().st_size
                    file_size_kb = file_size / 1024
                    print(f"   📄 {csv_file.name} ({file_size_kb:.1f} KB)")
                print()
            
            # Show file size comparison
            print("📊 File Size Comparison:")
            try:
                # Find DMP files
                dmp_parquet = output_dir / f"{chromosome}-{context}-dmp.parquet"
                dmp_h5 = output_dir / f"{chromosome}-{context}-dmp.h5"
                dmp_csv = output_dir / f"{chromosome}-{context}_vs_{chromosome}-{context}_significant_positions.csv"
                
                if dmp_parquet.exists() and dmp_h5.exists() and dmp_csv.exists():
                    parquet_size = dmp_parquet.stat().st_size / 1024  # KB
                    h5_size = dmp_h5.stat().st_size / 1024  # KB
                    csv_size = dmp_csv.stat().st_size / 1024  # KB
                    
                    print(f"   📊 DMP data sizes:")
                    print(f"      - CSV: {csv_size:.1f} KB")
                    print(f"      - Parquet: {parquet_size:.1f} KB")
                    print(f"      - HDF5 (Z-standard): {h5_size:.1f} KB")
                    
                    if h5_size < csv_size:
                        compression_ratio = csv_size / h5_size
                        print(f"      🎯 HDF5 compression ratio: {compression_ratio:.1f}x smaller than CSV")
                    
                    if parquet_size < csv_size:
                        compression_ratio = csv_size / parquet_size
                        print(f"      🎯 Parquet compression ratio: {compression_ratio:.1f}x smaller than CSV")
            except Exception as e:
                print(f"   ⚠️  Could not compare file sizes: {e}")
            
            print()
            print("🎉 Binary export demonstration completed!")
            print()
            print("💡 Next steps:")
            print("   1. Use DuckDB to load these binary files efficiently")
            print("   2. Parquet files are great for analytical queries")
            print("   3. HDF5 files with Z-standard compression offer the smallest file sizes")
            print("   4. Both formats maintain all the statistical information from the analysis")
            
        else:
            print("❌ Could not extract chromosome and context information from filename")
            print("   Expected format: {chromosome}-{context}.h5 (e.g., 1-CG.h5)")
    
    except Exception as e:
        print(f"❌ Error during analysis: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
