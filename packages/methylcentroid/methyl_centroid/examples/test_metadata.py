#!/usr/bin/env python3
"""
Test script to verify metadata is correctly saved to and loaded from H5 centroid files.
"""

import h5py
import json
from pathlib import Path
import sys

def test_metadata_in_h5(h5_file_path: Path):
    """
    Read and display metadata from an H5 centroid file.
    
    Args:
        h5_file_path: Path to the H5 file
    """
    if not h5_file_path.exists():
        print(f"Error: File {h5_file_path} does not exist")
        return False
    
    try:
        with h5py.File(h5_file_path, 'r') as f:
            print(f"\n{'='*60}")
            print(f"Metadata in: {h5_file_path.name}")
            print('='*60)
            
            # Display all file-level attributes
            if f.attrs:
                print("\nFile-level attributes:")
                for key, value in f.attrs.items():
                    # Try to parse JSON if it's a string
                    if isinstance(value, (str, bytes)):
                        try:
                            if isinstance(value, bytes):
                                value = value.decode('utf-8')
                            parsed = json.loads(value)
                            print(f"  {key}: {parsed}")
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            print(f"  {key}: {value}")
                    else:
                        print(f"  {key}: {value}")
            else:
                print("\nNo file-level attributes found")
            
            # Display groups
            print("\nGroups in file:")
            for group_name in f.keys():
                print(f"  - {group_name}")
            
            print('='*60)
            return True
            
    except Exception as e:
        print(f"Error reading file: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main function to test metadata functionality."""
    if len(sys.argv) < 2:
        print("Usage: python test_metadata.py <path_to_h5_file>")
        print("\nThis script reads metadata from a centroid H5 file.")
        sys.exit(1)
    
    h5_file = Path(sys.argv[1])
    test_metadata_in_h5(h5_file)


if __name__ == '__main__':
    main()

