#!/usr/bin/env python3
"""
Example demonstrating centroid functionality with methylation level statistics.

This example shows how to build centroid files with full schema: N (sample count),
Sx, Sx2 (sample mean/variance), and Sm, Su, Sc2, Swx2 (coverage-weighted statistics).
"""

import numpy as np
import h5py
from pathlib import Path
import tempfile

from methyl_centroid.methyl_centroid import MethylCentroid


def create_sample_file(filepath: Path, positions: np.ndarray, mC: np.ndarray, 
                      uC: np.ndarray, tnc: np.ndarray):
    """Create a sample HDF5 file for testing."""
    with h5py.File(filepath, "w") as f:
        data_group = f.create_group("methylation_data")
        data_group.create_dataset("pos", data=positions, dtype=np.uint32)
        data_group.create_dataset("mC", data=mC, dtype=np.uint32)
        data_group.create_dataset("uC", data=uC, dtype=np.uint32)
        data_group.create_dataset("tnc", data=tnc, dtype=np.uint8)


def demonstrate_basic_vs_centroid():
    """Demonstrate the difference between basic output and full centroid schema."""
    print("=== Basic vs Centroid (full schema) Demonstration ===")
    
    # Create temporary directory for sample files
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create sample files
        sample1_file = temp_path / "sample1" / "1-CG.h5"
        sample1_file.parent.mkdir(exist_ok=True)
        positions1 = np.array([1000, 2000, 3000, 4000], dtype=np.uint32)
        mC1 = np.array([10, 20, 30, 40], dtype=np.uint32)
        uC1 = np.array([5, 15, 25, 35], dtype=np.uint32)
        tnc1 = np.array([1, 2, 3, 4], dtype=np.uint8)
        create_sample_file(sample1_file, positions1, mC1, uC1, tnc1)
        
        sample2_file = temp_path / "sample2" / "1-CG.h5"
        sample2_file.parent.mkdir(exist_ok=True)
        positions2 = np.array([1500, 2000, 2500, 3000], dtype=np.uint32)
        mC2 = np.array([15, 25, 35, 45], dtype=np.uint32)
        uC2 = np.array([8, 18, 28, 38], dtype=np.uint32)
        tnc2 = np.array([2, 3, 4, 5], dtype=np.uint8)
        create_sample_file(sample2_file, positions2, mC2, uC2, tnc2)
        
        # Create output directory
        output_dir = temp_path / "output"
        output_dir.mkdir(exist_ok=True)
        
        # Initialize MethylCentroid
        samples = [str(sample1_file.parent), str(sample2_file.parent)]
        methyl_centroid = MethylCentroid(
            chrom="1",
            ctx="CG",
            output_dir=output_dir,
            add_samples=samples,
            min_coverage=4,
            laboratory="example-lab",
            disease="example",
            group="g",
            batch="b",
            min_samples=1,
            binned_stats_bins=20,
            use_gpu=False,
            verbose=False,
        )
        
        print("1. Building Basic Centroid:")
        basic_centroid_path = methyl_centroid.calculate_centroid(str(output_dir), extended=False)
        print(f"   Basic centroid saved to: {basic_centroid_path}")
        
        # Examine basic centroid
        with h5py.File(basic_centroid_path, "r") as f:
            data = f["methylation_data"]
            print(f"   Basic centroid columns: {list(data.keys())}")
            print(f"   Number of positions: {len(data['pos'])}")
            print(f"   Positions: {data['pos'][:]}")
            print(f"   mC values: {data['mC'][:]}")
            print(f"   uC values: {data['uC'][:]}")
            print(f"   N values: {data['N'][:]}")
        
        print("\n2. Building Centroid (full schema):")
        centroid_path = methyl_centroid.calculate_centroid(str(output_dir), extended=True)
        print(f"   Centroid saved to: {centroid_path}")
        
        # Examine centroid
        with h5py.File(centroid_path, "r") as f:
            data = f["methylation_data"]
            print(f"   Centroid columns: {list(data.keys())}")
            print(f"   Number of positions: {len(data['pos'])}")
            print(f"   Positions: {data['pos'][:]}")
            print(f"   mC values: {data['mC'][:]}")
            print(f"   uC values: {data['uC'][:]}")
            print(f"   N values: {data['N'][:]}")
            print(f"   Sx values: {data['Sx'][:]}")
            print(f"   Sx2 values: {data['Sx2'][:]}")
            
            # Verify calculations
            print("\n3. Verification of Extended Data:")
            for i in range(len(data['pos'])):
                pos = data['pos'][i]
                mC = data['mC'][i]
                uC = data['uC'][i]
                N = data['N'][i]
                Sx = data['Sx'][i]
                Sx2 = data['Sx2'][i]
                
                # Calculate expected methylation level
                total_coverage = mC + uC
                if total_coverage > 0:
                    expected_methylation = mC / total_coverage
                    expected_Sx = expected_methylation * N
                    expected_Sx2 = (expected_methylation ** 2) * N
                    
                    print(f"   Position {pos}:")
                    print(f"     - mC={mC}, uC={uC}, N={N}")
                    print(f"     - Methylation level: {expected_methylation:.3f}")
                    print(f"     - Sx: expected={expected_Sx:.3f}, actual={Sx:.3f}")
                    print(f"     - Sx2: expected={expected_Sx2:.3f}, actual={Sx2:.3f}")


def demonstrate_incremental_operations():
    """Demonstrate adding and removing samples from an existing extended centroid."""
    print("\n=== Incremental Operations Demonstration ===")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create initial sample files
        sample1_file = temp_path / "sample1" / "1-CG.h5"
        sample1_file.parent.mkdir(exist_ok=True)
        positions1 = np.array([1000, 2000, 3000], dtype=np.uint32)
        mC1 = np.array([10, 20, 30], dtype=np.uint32)
        uC1 = np.array([5, 15, 25], dtype=np.uint32)
        tnc1 = np.array([1, 2, 3], dtype=np.uint8)
        create_sample_file(sample1_file, positions1, mC1, uC1, tnc1)
        
        sample2_file = temp_path / "sample2" / "1-CG.h5"
        sample2_file.parent.mkdir(exist_ok=True)
        positions2 = np.array([2000, 3000, 4000], dtype=np.uint32)
        mC2 = np.array([15, 25, 35], dtype=np.uint32)
        uC2 = np.array([8, 18, 28], dtype=np.uint32)
        tnc2 = np.array([2, 3, 4], dtype=np.uint8)
        create_sample_file(sample2_file, positions2, mC2, uC2, tnc2)
        
        # Create new sample for incremental addition
        sample3_file = temp_path / "sample3" / "1-CG.h5"
        sample3_file.parent.mkdir(exist_ok=True)
        positions3 = np.array([1500, 2000, 2500], dtype=np.uint32)
        mC3 = np.array([12, 22, 32], dtype=np.uint32)
        uC3 = np.array([6, 16, 26], dtype=np.uint32)
        tnc3 = np.array([3, 4, 5], dtype=np.uint8)
        create_sample_file(sample3_file, positions3, mC3, uC3, tnc3)
        
        # Create output directory
        output_dir = temp_path / "output"
        output_dir.mkdir(exist_ok=True)
        
        # Initialize MethylCentroid with original samples
        original_samples = [str(sample1_file.parent), str(sample2_file.parent)]
        new_samples = [str(sample3_file.parent)]
        
        common = dict(
            chrom="1",
            ctx="CG",
            output_dir=output_dir,
            min_coverage=4,
            laboratory="example-lab",
            disease="example",
            group="g",
            batch="b",
            min_samples=1,
            binned_stats_bins=20,
            use_gpu=False,
            verbose=False,
        )
        methyl_centroid = MethylCentroid(add_samples=original_samples, **common)

        print("1. Building Initial Extended Centroid:")
        initial_centroid_path = methyl_centroid.calculate_centroid(str(output_dir), extended=True)
        print(f"   Initial centroid saved to: {initial_centroid_path}")
        
        # Examine initial centroid
        with h5py.File(initial_centroid_path, "r") as f:
            data = f["methylation_data"]
            print(f"   Initial positions: {data['pos'][:]}")
            print(f"   Initial N values: {data['N'][:]}")
        
        print("\n2. Adding New Sample (rebuild with baseline from HDF5 + add_samples):")
        mc_add = MethylCentroid(
            add_samples=new_samples,
            remove_samples=[],
            **common,
        )
        updated_centroid_path = mc_add.calculate_centroid(str(output_dir), extended=True)
        print(f"   Updated centroid saved to: {updated_centroid_path}")

        with h5py.File(updated_centroid_path, "r") as f:
            data = f["methylation_data"]
            print(f"   Updated positions: {data['pos'][:]}")
            print(f"   Updated N values: {data['N'][:]}")
            print(f"   Updated Sx values: {data['Sx'][:]}")

        print("\n3. Removing Sample (rebuild with remove_samples):")
        mc_rem = MethylCentroid(
            add_samples=[],
            remove_samples=new_samples,
            **common,
        )
        final_centroid_path = mc_rem.calculate_centroid(str(output_dir), extended=True)
        print(f"   Final centroid saved to: {final_centroid_path}")

        with h5py.File(final_centroid_path, "r") as f:
            data = f["methylation_data"]
            print(f"   Final positions: {data['pos'][:]}")
            print(f"   Final N values: {data['N'][:]}")





def main():
    """Run the extended centroid demonstration."""
    print("MethylCentroid - Extended Centroid Example")
    print("=" * 60)
    
    try:
        # Demonstrate basic vs extended centroids
        demonstrate_basic_vs_centroid()
        
        # Demonstrate incremental operations
        demonstrate_incremental_operations()
        

        
        print("\n" + "=" * 60)
        print("Extended centroid demonstration completed successfully!")
        print("Key features demonstrated:")
        print("- Extended centroid files with N, Sx, Sx2 columns")
        print("- Incremental sample addition and removal")

        print("- Verification of methylation level calculations")
        
    except Exception as e:
        print(f"Error during demonstration: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main() 