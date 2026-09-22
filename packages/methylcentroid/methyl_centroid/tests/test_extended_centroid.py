#!/usr/bin/env python3
"""
Tests for centroid functionality with methylation level statistics (N, Sx, Sx2, Sm, Su, Sc2, Swx2).
"""

import pytest
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


class TestCentroid:
    """Test centroid functionality (full schema with Sm, Su, Sc2, Swx2)."""

    def test_basic_vs_centroid(self):
        """Centroid HDF5 uses the extended ECDF schema (Sm, Su, N, Sx, Sx2, …) on disk."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Create sample files
            sample1_file = temp_path / "sample1" / "1-CG.h5"
            sample1_file.parent.mkdir(exist_ok=True)
            positions1 = np.array([1000, 2000], dtype=np.uint32)
            mC1 = np.array([10, 20], dtype=np.uint32)
            uC1 = np.array([5, 15], dtype=np.uint32)
            tnc1 = np.array([1, 2], dtype=np.uint8)
            create_sample_file(sample1_file, positions1, mC1, uC1, tnc1)
            
            sample2_file = temp_path / "sample2" / "1-CG.h5"
            sample2_file.parent.mkdir(exist_ok=True)
            positions2 = np.array([1500, 2000], dtype=np.uint32)
            mC2 = np.array([15, 25], dtype=np.uint32)
            uC2 = np.array([8, 18], dtype=np.uint32)
            tnc2 = np.array([2, 3], dtype=np.uint8)
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
                laboratory="lab",
                disease="d",
                group="g",
                batch="b",
                min_samples=1,
                binned_stats_bins=20,
                use_gpu=False,
                verbose=False,
            )
            
            centroid_path = methyl_centroid.calculate_centroid(str(output_dir), extended=True)
            
            with h5py.File(centroid_path, "r") as f:
                data = f["methylation_data"]
                cols = list(data.keys())
                assert "pos" in cols
                assert "tnc" in cols
                assert "N" in cols
                assert "Sx" in cols
                assert "Sx2" in cols
                assert "Sm" in cols
                assert "Su" in cols
                assert "Sc2" in cols
                assert "Swx2" in cols
    
    def test_centroid_calculations(self):
        """Test that Sx and Sx2 (and full schema) calculations are correct."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Create sample files with known values
            sample1_file = temp_path / "sample1" / "1-CG.h5"
            sample1_file.parent.mkdir(exist_ok=True)
            positions1 = np.array([1000, 2000], dtype=np.uint32)
            mC1 = np.array([10, 20], dtype=np.uint32)
            uC1 = np.array([5, 15], dtype=np.uint32)
            tnc1 = np.array([1, 2], dtype=np.uint8)
            create_sample_file(sample1_file, positions1, mC1, uC1, tnc1)
            
            sample2_file = temp_path / "sample2" / "1-CG.h5"
            sample2_file.parent.mkdir(exist_ok=True)
            positions2 = np.array([1500, 2000], dtype=np.uint32)
            mC2 = np.array([15, 25], dtype=np.uint32)
            uC2 = np.array([8, 18], dtype=np.uint32)
            tnc2 = np.array([2, 3], dtype=np.uint8)
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
                laboratory="lab",
                disease="d",
                group="g",
                batch="b",
                min_samples=1,
                binned_stats_bins=20,
                use_gpu=False,
                verbose=False,
            )
            
            # Build centroid
            centroid_path = methyl_centroid.calculate_centroid(str(output_dir), extended=True)
            
            # Check calculations (extended schema: Sm, Su, N, Sx, Sx2)
            with h5py.File(centroid_path, "r") as f:
                data = f["methylation_data"]
                
                # For position 2000, we have data from both samples
                pos_2000_idx = np.where(data['pos'][:] == 2000)[0][0]
                
                N_2000 = data['N'][pos_2000_idx]
                Sx_2000 = data['Sx'][pos_2000_idx]
                Sx2_2000 = data['Sx2'][pos_2000_idx]
                
                # Expected values:
                # Sample 1: mC=20, uC=15, methylation_level = 20/(20+15) = 0.571
                # Sample 2: mC=25, uC=18, methylation_level = 25/(25+18) = 0.581
                # Sx should be the sum of individual methylation levels: 0.571 + 0.581 = 1.152
                # Sx2 should be the sum of squared methylation levels: 0.571^2 + 0.581^2 = 0.326 + 0.338 = 0.664
                expected_Sx = (20 / (20 + 15)) + (25 / (25 + 18))  # Sum of individual methylation levels
                expected_Sx2 = (20 / (20 + 15)) ** 2 + (25 / (25 + 18)) ** 2  # Sum of squared methylation levels
                
                assert N_2000 == 2
                assert abs(Sx_2000 - expected_Sx) < 1e-6
                assert abs(Sx2_2000 - expected_Sx2) < 1e-6
    
    def test_incremental_operations(self):
        """Incremental add/remove via rebuild (baseline from HDF5 + add_samples/remove_samples)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            sample1_file = temp_path / "sample1" / "1-CG.h5"
            sample1_file.parent.mkdir(exist_ok=True)
            positions1 = np.array([1000, 2000], dtype=np.uint32)
            mC1 = np.array([10, 20], dtype=np.uint32)
            uC1 = np.array([5, 15], dtype=np.uint32)
            tnc1 = np.array([1, 2], dtype=np.uint8)
            create_sample_file(sample1_file, positions1, mC1, uC1, tnc1)

            sample2_file = temp_path / "sample2" / "1-CG.h5"
            sample2_file.parent.mkdir(exist_ok=True)
            positions2 = np.array([2000, 3000], dtype=np.uint32)
            mC2 = np.array([15, 25], dtype=np.uint32)
            uC2 = np.array([8, 18], dtype=np.uint32)
            tnc2 = np.array([2, 3], dtype=np.uint8)
            create_sample_file(sample2_file, positions2, mC2, uC2, tnc2)

            sample3_file = temp_path / "sample3" / "1-CG.h5"
            sample3_file.parent.mkdir(exist_ok=True)
            positions3 = np.array([1500, 2000], dtype=np.uint32)
            mC3 = np.array([12, 22], dtype=np.uint32)
            uC3 = np.array([6, 16], dtype=np.uint32)
            tnc3 = np.array([3, 4], dtype=np.uint8)
            create_sample_file(sample3_file, positions3, mC3, uC3, tnc3)

            output_dir = temp_path / "output"
            output_dir.mkdir(exist_ok=True)

            common_kw = dict(
                laboratory="lab",
                disease="d",
                group="g",
                batch="b",
                chrom="1",
                ctx="CG",
                output_dir=output_dir,
                min_coverage=4,
                min_samples=1,
                use_gpu=False,
                binned_stats_bins=20,
                verbose=False,
            )

            original_samples = [str(sample1_file.parent), str(sample2_file.parent)]
            mc0 = MethylCentroid(add_samples=original_samples, **common_kw)
            mc0.build_centroid()

            with h5py.File(output_dir / "1-CG.h5", "r") as f:
                data = f["methylation_data"]
                pos_2000_idx = np.where(data["pos"][:] == 2000)[0][0]
                assert data["N"][pos_2000_idx] == 2

            mc1 = MethylCentroid(
                add_samples=[str(sample3_file.parent)],
                remove_samples=[],
                **common_kw,
            )
            mc1.build_centroid()

            with h5py.File(output_dir / "1-CG.h5", "r") as f:
                data = f["methylation_data"]
                pos_2000_idx = np.where(data["pos"][:] == 2000)[0][0]
                assert data["N"][pos_2000_idx] == 3

            mc2 = MethylCentroid(
                add_samples=[],
                remove_samples=[str(sample3_file.parent)],
                **common_kw,
            )
            mc2.build_centroid()

            with h5py.File(output_dir / "1-CG.h5", "r") as f:
                data = f["methylation_data"]
                pos_2000_idx = np.where(data["pos"][:] == 2000)[0][0]
                assert data["N"][pos_2000_idx] == 2

    def test_build_centroid(self):
        """Test the build_centroid method."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Create multiple sample files
            sample_files = []
            for i in range(3):
                sample_file = temp_path / f"sample{i+1}" / "1-CG.h5"
                sample_file.parent.mkdir(exist_ok=True)
                
                positions = np.array([1000, 2000], dtype=np.uint32)
                mC = np.array([10 + i, 20 + i], dtype=np.uint32)
                uC = np.array([5 + i, 15 + i], dtype=np.uint32)
                tnc = np.array([1, 2], dtype=np.uint8)
                
                create_sample_file(sample_file, positions, mC, uC, tnc)
                sample_files.append(str(sample_file.parent))
            
            # Create output directory
            output_dir = temp_path / "output"
            output_dir.mkdir(exist_ok=True)
            
            # Initialize MethylCentroid runner
            methyl_centroid = MethylCentroid(
                chrom="1",
                ctx="CG",
                output_dir=output_dir,
                add_samples=sample_files,
                min_coverage=4,
                laboratory="lab",
                disease="d",
                group="g",
                batch="b",
                min_samples=1,
                use_gpu=False,
                binned_stats_bins=20,
                verbose=False,
            )
            
            # Build centroid
            results = methyl_centroid.build_centroid()

            # Check results
            assert results.final_centroid_path is not None
            assert Path(results.final_centroid_path).exists()
            
            # Check final centroid has full schema (Sx, Sx2, Sm, Su, etc.)
            with h5py.File(results.final_centroid_path, "r") as f:
                data = f["methylation_data"]
                assert "Sx" in data
                assert "Sx2" in data
                assert len(data['pos']) > 0


    
    def test_position_aligner_centroid_methods(self):
        """Test PositionAligner methods for centroid output."""
        pytest.importorskip("genomic_position_aligner")
        from genomic_position_aligner import PositionAligner
        
        # Create a simple position aligner
        aligner = PositionAligner(max_samples=2, use_gpu=False)
        aligner.set_min_coverage(1)
        
        # Add sample data
        positions = np.array([1000, 2000], dtype=np.uint32)
        mC = np.array([10, 20], dtype=np.uint32)
        uC = np.array([5, 15], dtype=np.uint32)
        tnc = np.array([1, 2], dtype=np.uint8)
        
        success = aligner.add_sample_data(positions, mC, uC, tnc, sample_index=0)
        assert success
        
        # Test get_centroid_sample for extended data
        centroid_sample = aligner.get_centroid_sample()

        assert centroid_sample is not None
        assert len(centroid_sample.pos) == 2
        assert len(centroid_sample.Sx) == 2
        assert len(centroid_sample.Sx2) == 2

        # Verify calculations
        expected_methylation_1000 = 10 / (10 + 5)  # 0.667
        expected_methylation_2000 = 20 / (20 + 15)  # 0.571

        assert abs(centroid_sample.Sx[0] - expected_methylation_1000) < 1e-6
        assert abs(centroid_sample.Sx[1] - expected_methylation_2000) < 1e-6
        assert abs(centroid_sample.Sx2[0] - (expected_methylation_1000 ** 2)) < 1e-6
        assert abs(centroid_sample.Sx2[1] - (expected_methylation_2000 ** 2)) < 1e-6


def test_basic_import():
    """Test that MethylCentroid can be imported and basic functionality works."""
    from methyl_centroid.methyl_centroid import MethylCentroid
    from methyl_utils import is_gpu_available

    # Test basic instantiation
    mc = MethylCentroid(
        chrom="1",
        ctx="CG",
        output_dir="/tmp",
        add_samples=["dummy"],
        verbose=True,
        laboratory="l",
        disease="d",
        group="g",
        batch="b",
        min_samples=1,
        binned_stats_bins=20,
    )

    # Test that components are initialized
    assert mc.memory_manager is not None
    assert mc.performance_profiler is not None
    assert mc.chunked_processor is not None
    assert mc.logger is not None

    # Test GPU detection integration
    gpu_available = is_gpu_available()
    assert isinstance(gpu_available, bool)

    print("✅ Basic import and initialization test passed")


if __name__ == "__main__":
    # Run basic functionality test first
    test_basic_import()

    # Then run pytest if available
    try:
        pytest.main([__file__, "-v"])
    except NameError:
        print("⚠️  pytest not available, but basic import test passed") 