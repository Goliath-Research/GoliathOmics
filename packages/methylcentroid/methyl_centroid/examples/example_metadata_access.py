#!/usr/bin/env python3
"""
Example: Accessing and modifying metadata from a centroid H5 file.

This demonstrates how to:
1. Load a centroid and access its metadata properties (read)
2. Modify metadata properties (write)
3. Save the centroid with updated metadata
"""

from pathlib import Path

def example_metadata_access(centroid_path: Path):
    """
    Load a centroid and display its metadata.
    
    Args:
        centroid_path: Path to a centroid H5 file
    """
    from methyl_utils.core.methyl_frame import MethylFrame
    
    print(f"Loading centroid from: {centroid_path}")
    print("=" * 60)
    
    # Load the centroid
    centroid = MethylFrame.load_from_h5(centroid_path)
    
    # Check if it's a centroid
    print(f"\nSample type: {centroid.sample_type}")
    print(f"Is centroid: {centroid.is_centroid}")
    print(f"Is centroid: {centroid.is_centroid}")
    
    # Access metadata properties
    print("\nMetadata:")
    print(f"  Laboratory: {centroid.laboratory}")
    print(f"  Disease: {centroid.disease}")
    print(f"  Group: {centroid.group}")
    print(f"  Batch: {centroid.batch}")
    print(f"  Chromosome: {centroid.chromosome}")
    print(f"  Context: {centroid.context}")
    
    # Access full metadata dictionary
    if centroid.metadata:
        print(f"\nAll metadata keys: {list(centroid.metadata.keys())}")
        
        # Show sample count if available (centroid cohort is recorded as samples_used)
        su = centroid.metadata.get("samples_used")
        if su:
            print(f"Number of samples (samples_used): {len(su)}")
            print(f"First sample id: {su[0]}")
            if len(su) > 1:
                print(f"Last sample id: {su[-1]}")
        
        # Show processing parameters
        if "min_coverage" in centroid.metadata:
            print(f"\nProcessing parameters:")
            print(f"  Min coverage: {centroid.metadata.get('min_coverage')}")
            print(f"  Alpha: {centroid.metadata.get('alpha')}")
            print(f"  Distance metrics: {centroid.metadata.get('distance_metrics')}")
    else:
        print("\nNo metadata available (this centroid was created before metadata support)")
    
    # Show data statistics
    print(f"\nData statistics:")
    print(f"  Positions: {len(centroid.pos):,}")
    print(f"  Mean coverage: {centroid.get_coverage().mean():.2f}")
    if centroid.is_centroid:
        print(f"  Mean sample count: {centroid.N.mean():.2f}")
    
    print("=" * 60)


def example_metadata_modification():
    """
    Demonstrate how to modify metadata properties.
    """
    from methyl_utils.core.methyl_frame import MethylFrame
    import numpy as np
    
    print("\nExample: Creating and modifying metadata")
    print("=" * 60)
    
    # Create a simple centroid
    centroid = MethylFrame(
        pos=np.array([100, 200, 300], dtype=np.uint32),
        mC=np.array([10, 20, 30], dtype=np.uint32),
        uC=np.array([5, 10, 15], dtype=np.uint32),
        tnc=np.array([1, 2, 3], dtype=np.uint8),
        N=np.array([5, 5, 5], dtype=np.uint32)
    )
    
    print("Created centroid without metadata")
    print(f"  Laboratory: {centroid.laboratory}")  # None
    
    # Set metadata using clean property assignment
    print("\nSetting metadata using properties:")
    centroid.laboratory = "psomagen"
    centroid.disease = "prostate cancer"
    centroid.group = "cancer"
    centroid.batch = "AN00025834"
    centroid.chromosome = "1"
    centroid.context_metadata = "CG"
    
    print(f"  Laboratory: {centroid.laboratory}")
    print(f"  Disease: {centroid.disease}")
    print(f"  Group: {centroid.group}")
    print(f"  Batch: {centroid.batch}")
    
    # You can also set all metadata at once
    print("\nOr set all metadata at once:")
    centroid.metadata = {
        "laboratory": "updated_lab",
        "disease": "updated_disease",
        "group": "updated_group",
        "batch": "updated_batch",
        "custom_field": "custom_value"
    }
    
    print(f"  Laboratory: {centroid.laboratory}")
    print(f"  Custom field: {centroid.metadata.get('custom_field')}")
    
    # Save to file with metadata
    # output_path = Path("/tmp/test_centroid.h5")
    # centroid.save_to_h5(output_path, metadata=centroid.metadata)
    # print(f"\nCentroid saved to {output_path}")
    
    print("=" * 60)


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python example_metadata_access.py <centroid.h5>")
        print("       python example_metadata_access.py --demo")
        print("\nExamples:")
        print("  # Read metadata from existing centroid")
        print("  python example_metadata_access.py /path/to/centroids/1-CG.h5")
        print()
        print("  # Show metadata modification demo")
        print("  python example_metadata_access.py --demo")
        sys.exit(1)
    
    if sys.argv[1] == '--demo':
        # Run the modification example
        example_metadata_modification()
    else:
        centroid_path = Path(sys.argv[1])
        
        if not centroid_path.exists():
            print(f"Error: File not found: {centroid_path}")
            sys.exit(1)
        
        example_metadata_access(centroid_path)

