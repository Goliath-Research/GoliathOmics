#!/usr/bin/env python3
"""
Comprehensive usage examples for MethylDetector.

This file demonstrates all the different ways to use MethylDetector:
1. Command line interface with parameters
2. Command line interface with JSON configuration
3. Python API with Pydantic configuration model
4. Python API with direct parameters
5. Multiple comparisons programmatically
6. Accessing different output formats
"""

import subprocess
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

from methyl_detector import MethylDetector
from methyl_detector.models.config import MethylDetectorConfig
from methyl_detector.models.results import MethylDetectorResult


def setup_logging() -> None:
    """Setup logging configuration using MethylUtils."""
    try:
        from methyl_utils.logging_utils import setup_logging as methyl_utils_setup_logging
        methyl_utils_setup_logging(verbose=False)
    except ImportError:
        # Fallback to basic logging if MethylUtils not available
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )


def example_1_command_line_with_parameters() -> None:
    """
    Example 1: Command line interface with parameters.
    
    This demonstrates how to run MethylDetector using command line parameters.
    """
    print("\n" + "="*60)
    print("Example 1: Command Line Interface with Parameters")
    print("="*60)
    
    # Example command (commented out as it requires actual data files)
    cmd: List[str] = [
        "python", "-m", "methyl_detector",
        "--centroid1", "/path/to/centroid1.h5",
        "--centroid2", "/path/to/centroid2.h5",
        "--alpha", "0.05",
        "--min-n", "10",
        "--output-dir", "./results",
        "--verbose"
    ]
    
    print("Command:")
    print(" ".join(cmd))
    
    print("\nTo run this example:")
    print("1. Replace /path/to/centroid1.h5 with your actual centroid file path")
    print("2. Replace /path/to/centroid2.h5 with your actual centroid file path")
    print("3. Run the command above")
    
    # Uncomment to actually run (requires real data files)
    result: subprocess.CompletedProcess = subprocess.run(cmd, capture_output=True, text=True)
    print(f"Return code: {result.returncode}")
    print(f"Output: {result.stdout}")


def example_2_command_line_with_json_config() -> None:
    """
    Example 2: Command line interface with JSON configuration.
    
    This demonstrates how to run MethylDetector using a JSON configuration file
    created from a Pydantic model for type safety.
    """
    print("\n" + "="*60)
    print("Example 2: Command Line Interface with JSON Configuration")
    print("="*60)
    
    try:
        # Create configuration using Pydantic model for type safety
        config: MethylDetectorConfig = MethylDetectorConfig(
            centroid1_path=Path("/path/to/centroid1.h5"),
            centroid2_path=Path("/path/to/centroid2.h5"),
            output_dir=Path("/home/ubuntu/Work/output_workflows/arabidopsis/detection/WT-msh1"),
            alpha=0.05,
            min_N_pct=0.1,
        )
        
        # Save config to file using Pydantic's built-in JSON export
        config_file: Path = Path("examples/temp_config.json")
        config_file.parent.mkdir(exist_ok=True)
        with open(config_file, 'w') as f:
            f.write(config.model_dump_json(indent=4))
        
        print("JSON Configuration (created from Pydantic model):")
        print(config.model_dump_json(indent=4))
        
        # Example command
        cmd: List[str] = [
            "python", "-m", "methyl_detector",
            "--config", str(config_file),
            "--verbose"
        ]
        
        print("\nCommand:")
        print(" ".join(cmd))
        
        print("\nBenefits of using Pydantic model:")
        print("- Type safety and validation")
        print("- Direct JSON export with model_dump_json()")
        print("- Automatic handling of Path objects and other types")
        print("- Consistent with the rest of the API")
        print("- IDE autocomplete and error detection")
        
        print("\nTo run this example:")
        print("1. Replace the file paths in the configuration")
        print("2. Run the command above")
        
        # Clean up
        if config_file.exists():
            config_file.unlink()
            
    except Exception as e:
        print(f"Error creating configuration: {e}")
        print("This is expected if the paths don't exist.")


def example_3_python_api_with_pydantic_config() -> None:
    """
    Example 3: Python API using Pydantic configuration model.
    
    This demonstrates how to use MethylDetector programmatically with
    type-safe Pydantic configuration.
    """
    print("\n" + "="*60)
    print("Example 3: Python API with Pydantic Configuration")
    print("="*60)
    
    try:
        # Create configuration using Pydantic model
        config: MethylDetectorConfig = MethylDetectorConfig(
            centroid1_path=Path("/path/to/centroid1.h5"),
            centroid2_path=Path("/path/to/centroid2.h5"),
            output_dir=Path("./results"),
            alpha=0.05,
            min_N=10,
            apply_fdr_correction=True,
            fdr_method="storey",
            global_significance_threshold=0.05,
        )
        
        print("Configuration created successfully:")
        print(f"  Centroid 1: {config.centroid1_path}")
        print(f"  Centroid 2: {config.centroid2_path}")
        print(f"  Output directory: {config.output_dir}")
        print(f"  Alpha: {config.alpha}")
        print(f"  Min N: {config.min_N}")
        print(f"  FDR correction: {config.apply_fdr_correction}")
        
        # Initialize detector
        detector = MethylDetector(config)
        print("\nDetector initialized successfully!")
        
        # Run analysis (commented out as it requires real data)
        result: MethylDetectorResult = detector.run()
        print(f"Analysis completed! Found {result.comparisons[0].significant_count} significant positions")
        
    except Exception as e:
        print(f"Error: {e}")
        print("This is expected if the data files don't exist.")


def example_4_python_api_with_direct_parameters() -> None:
    """
    Example 4: Python API using direct parameters.
    
    This demonstrates how to use MethylDetector programmatically with
    direct parameter passing (without Pydantic model).
    """
    print("\n" + "="*60)
    print("Example 4: Python API with Direct Parameters")
    print("="*60)
    
    try:
        # Initialize detector with direct parameters
        detector = MethylDetector(
            centroid1_path=Path("/path/to/centroid1.h5"),
            centroid2_path=Path("/path/to/centroid2.h5"),
            output_dir=Path("./results"),
            alpha=0.05,
            min_N=10,
            apply_fdr_correction=True,
            fdr_method="storey",
            global_significance_threshold=0.05,
        )
        
        print("Detector initialized successfully with direct parameters!")
        print("Parameters:")
        print(f"  Alpha: {detector.config.alpha}")
        print(f"  Min N: {detector.config.min_N}")
        print(f"  FDR correction: {detector.config.apply_fdr_correction}")
        
        # Run analysis (commented out as it requires real data)
        result: MethylDetectorResult = detector.run()
        print(f"Analysis completed! Found {result.comparisons[0].significant_count} significant positions")
        
    except Exception as e:
        print(f"Error: {e}")
        print("This is expected if the data files don't exist.")


def example_5_multiple_comparisons_programmatically() -> None:
    """
    Example 5: Multiple comparisons programmatically.
    
    This demonstrates how to run multiple comparisons across
    different chromosomes and contexts programmatically.
    """
    print("\n" + "="*60)
    print("Example 5: Multiple Comparisons Programmatically")
    print("="*60)
    
    try:
        # Create multiple comparison configuration
        config: MultipleComparisonConfig = MultipleComparisonConfig(
            centroid1_dir=Path("/path/to/control/centroids"),
            centroid2_dir=Path("/path/to/treatment/centroids"),
            chromosomes=["1", "2", "3", "4", "5"],
            contexts=["CG", "CHG", "CHH"],
            output_dir=Path("./results"),
            alpha=0.05,
            min_N=10,
            apply_fdr_correction=True,
            fdr_method="storey",
            global_significance_threshold=0.05,
        )
        
        print("Multiple comparison configuration created:")
        print(f"  Control directory: {config.centroid1_dir}")
        print(f"  Treatment directory: {config.centroid2_dir}")
        print(f"  Chromosomes: {config.chromosomes}")
        print(f"  Contexts: {config.contexts}")
        print(f"  Total comparisons: {len(config.chromosomes) * len(config.contexts)}")
        
        # Initialize detector
        detector = MethylDetector(config)
        print("\nDetector initialized successfully!")
        
        # Run multiple comparisons (commented out as it requires real data)
        result: MethylDetectorResult = detector.run()
        print("Multiple comparisons completed!")
        for comparison in result.comparisons:
            print(f"  {comparison.name}: {comparison.significant_count} significant positions")
        
    except Exception as e:
        print(f"Error: {e}")
        print("This is expected if the data files don't exist.")


def example_6_accessing_output_formats() -> None:
    """
    Example 6: Accessing different output formats.
    
    This demonstrates how to access results in different formats:
    - Command line output
    - Pydantic JSON models
    - File outputs
    """
    print("\n" + "="*60)
    print("Example 6: Accessing Different Output Formats")
    print("="*60)
    
    print("1. Command Line Output:")
    print("   The tool provides real-time progress tracking:")
    print("   GPU acceleration available with CuPy")
    print("   Filtering valid positions: 100%|█████████████████████████████| 1343836/1343836 [00:10<00:00, 125172.75pos/s]")
    print("   Processing positions: 100%|█████████████████████████████████████████████████████████████████████████████████| 1331806/1331806 [01:02<00:00, 21380.28pos/s]")
    print("   === SMART COMPARISON COMPLETED ===")
    print("   Total positions processed: 1331806")
    print("   Significant positions found: 80021")
    print("   Significant fraction: 6.01%")
    print("   Processing time: 63.78 seconds")
    print("   GPU acceleration: Yes")
    print("   FDR correction applied: pi0=0.942")
    print("   Global significance (Stouffer): p=0.000000, z-score=-1313.774982, significant=True")
    
    print("\n2. Pydantic JSON Models:")
    print("   Results are available as structured Pydantic models:")
    print("   ```python")
    print("   result = detector.run()")
    print("   comparison = result.comparisons[0]")
    print("   print(f'Total positions: {comparison.total_positions}')")
    print("   print(f'Significant positions: {comparison.significant_count}')")
    print("   print(f'Significant fraction: {comparison.significant_fraction:.2%}')")
    print("   print(f'Processing time: {comparison.processing_time_seconds:.2f} seconds')")
    print("   print(f'GPU used: {comparison.gpu_used}')")
    print("   print(f'FDR method: {comparison.fdr_method}')")
    print("   print(f'Pi0 estimate: {comparison.pi0_estimate:.3f}')")
    print("   print(f'Global p-value: {comparison.global_p_value:.6f}')")
    print("   print(f'Global significant: {comparison.global_significant}')")
    print("   ```")
    
    print("\n3. File Outputs:")
    print("   The tool generates comprehensive output files:")
    print("   - {prefix}_significant_positions.csv - Significant positions with p-values and q-values")
    print("   - {prefix}_summary.txt - Summary statistics")
    print("   - {prefix}_pi0_vs_lambda.html - FDR analysis plot")
    print("   - {prefix}_significant_regions.csv - Grouped significant regions")
    print("   - {prefix}_config.json - Input configuration")
    print("   - {prefix}_results.json - Complete results in JSON format")


def example_7_container_usage() -> None:
    """
    Example 7: Container usage.
    
    This demonstrates how to use MethylDetector within the Docker container.
    """
    print("\n" + "="*60)
    print("Example 7: Container Usage")
    print("="*60)
    
    print("1. Build and start the container:")
    print("   ```bash")
    print("   cd /path/to/project")
    print("   make docker-build")
    print("   make docker-run")
    print("   ```")
    
    print("\n2. Access the container shell:")
    print("   ```bash")
    print("   make docker-shell")
    print("   ```")
    
    print("\n3. Install the package:")
    print("   ```bash")
    print("   poetry install")
    print("   ```")
    
    print("\n4. Run analysis:")
    print("   ```bash")
    print("   python -m methyl_detector --config examples/config_WT-msh1.json")
    print("   ```")
    
    print("\n5. Container management:")
    print("   ```bash")
    print("   make docker-shell    # Access container shell")
    print("   make docker-stop     # Stop container")
    print("   make docker-run      # Start container")
    print("   ```")


def main() -> None:
    """Run all examples."""
    setup_logging()
    
    print("MethylDetector Comprehensive Usage Examples")
    print("="*60)
    print("This file demonstrates all the different ways to use MethylDetector:")
    print("1. Command line interface with parameters")
    print("2. Command line interface with JSON configuration")
    print("3. Python API with Pydantic configuration model")
    print("4. Python API with direct parameters")
    print("5. Multiple comparisons programmatically")
    print("6. Accessing different output formats")
    print("7. Container usage")
    
    # Run all examples
    example_1_command_line_with_parameters()
    example_2_command_line_with_json_config()
    example_3_python_api_with_pydantic_config()
    example_4_python_api_with_direct_parameters()
    example_5_multiple_comparisons_programmatically()
    example_6_accessing_output_formats()
    example_7_container_usage()
    
    print("\n" + "="*60)
    print("All examples completed!")
    print("="*60)
    print("\nTo run actual analyses:")
    print("1. Replace file paths with your actual data files")
    print("2. Uncomment the relevant code sections")
    print("3. Run the examples with real data")
    print("\nFor more information, see:")
    print("- README.modeler for comprehensive documentation")
    print("- QUICKSTART.modeler for quick setup guide")
    print("- examples/ directory for configuration files")


if __name__ == "__main__":
    main()
