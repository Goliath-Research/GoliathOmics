#!/usr/bin/env python3
"""
Example usage of MethylDetector for genomics sample classification.

This example demonstrates how to use MethylDetector programmatically
to classify two groups of genomics samples using divergences.
"""

import logging
from pathlib import Path
from methyl_detector import MethylDetector
from methyl_detector.models.config import MethylDetectorConfig

# Setup logging
# Use MethylUtils logging if available
try:
    from methyl_utils.logging_utils import setup_logging
    setup_logging(verbose=False)
except ImportError:
    # Fallback to basic logging if MethylUtils not available
    logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    """Example usage of MethylDetector."""
    
    # Example file paths (replace with your actual data paths)
    centroid_path = Path("/home/ubuntu/Work/output_workflows/download_samples_azenta/4j03r7051525/1-CG.h5")
    control_paths = [
        Path("data/control_1.csv"),
        Path("data/control_2.csv"),
        Path("data/control_3.csv")
    ]
    treatment_paths = [
        Path("data/treatment_1.csv"),
        Path("data/treatment_2.csv"),
        Path("data/treatment_3.csv")
    ]
    
    # Create configuration
    config = MethylDetectorConfig(
        centroid_path=centroid_path,
        controls=control_paths,
        treatments=treatment_paths,
        alpha=0.05,
        output_dir=Path("results"),
        random_state=42
    )
    
    # Initialize detector
    detector = MethylDetector(config)
    
    # Run analysis
    result = detector.run()
    
    # Display results
    print("\n" + "="*60)
    print("MethylDetector Analysis Results")
    print("="*60)
    
    print("\nModel Parameters:")
    print(f"  Optimal Cutpoint: {result.model_params.cutpoint:.6f}")
    print(f"  Youden Index: {result.model_params.youden_index:.6f}")
    print(f"  Alpha: {result.model_params.alpha}")
    
    print("\nClassification Metrics:")
    print(f"  Accuracy: {result.metrics.accuracy:.4f}")
    print(f"  Sensitivity: {result.metrics.sensitivity:.4f}")
    print(f"  Specificity: {result.metrics.specificity:.4f}")
    print(f"  Precision: {result.metrics.precision:.4f}")
    print(f"  F1 Score: {result.metrics.f1_score:.4f}")
    print(f"  AUC-ROC: {result.metrics.auc_roc:.4f}")
    
    print("\nData Summary:")
    print(f"  Control Samples: {result.n_controls}")
    print(f"  Treatment Samples: {result.n_treatments}")
    print(f"  Total Positions: {result.n_positions}")
    print(f"  Significant Positions: {len(result.significant_positions)}")
    
    if result.output_dir:
        print(f"\nResults saved to: {result.output_dir}")
        print("Generated files:")
        for file_type, file_path in result.result_files.items():
            print(f"  {file_type}: {file_path}")
    
    print("\nAnalysis completed successfully!")


if __name__ == "__main__":
    main() 