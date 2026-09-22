"""Simplified result models for MethylDetector analysis."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
from pydantic import BaseModel, Field, field_validator


def convert_numpy_types(obj):
    """Convert numpy types to Python native types for Pydantic compatibility."""
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [convert_numpy_types(item) for item in obj]
    else:
        return obj


class NumpyCompatibleModel(BaseModel):
    """Base model that handles numpy arrays automatically."""

    model_config = {
        "arbitrary_types_allowed": True,
    }

    @field_validator('*', mode='before')
    @classmethod
    def convert_numpy_arrays(cls, v):
        return convert_numpy_types(v)


class ComparisonStats(NumpyCompatibleModel):
    """Statistics for a single comparison."""

    comparison_name: str = Field(..., description="Name of comparison (e.g., '1-CG_WT_vs_msh1')")
    total_positions: int = Field(..., description="Total positions analyzed")
    statistical_dmps: int = Field(..., description="Statistically significant DMPs")
    biological_dmps: int = Field(..., description="Biologically significant DMPs after filtering")
    processing_time_seconds: float = Field(..., description="Processing time in seconds")
    gpu_used: bool = Field(default=False, description="Whether GPU was used")


class MethylDetectorResult(NumpyCompatibleModel):
    """Simplified result from MethylDetector analysis (DataFrame-centric)."""

    # Core results - DataFrame-based storage only
    biologically_significant_dmps_df: Optional[Any] = Field(
        default=None,
        description="DataFrame of biologically significant DMPs"
    )

    # Summary statistics
    total_statistical_dmps: int = Field(..., description="Total statistically significant DMPs")
    total_biological_dmps: int = Field(..., description="Total biologically significant DMPs")
    biological_retention_rate: float = Field(..., description="Fraction of statistical DMPs that are biologically significant")

    # Model performance (if classifier was trained / validated)
    training_accuracy: Optional[float] = Field(
        default=None,
        description="Accuracy of trained classifier on training data"
    )
    balanced_accuracy: Optional[float] = Field(
        default=None,
        description="Balanced accuracy from validation (optimization or real) when available"
    )

    # Comparison details
    comparison_stats: List[ComparisonStats] = Field(
        default_factory=list,
        description="Statistics for each comparison"
    )

    # Output files
    output_files: Dict[str, Union[str, Path]] = Field(
        default_factory=dict,
        description="Paths to generated output files"
    )

    # Classifier model (if trained)
    classifier_model_path: Optional[Union[str, Path]] = Field(
        default=None,
        description="Path to trained classifier model file"
    )


    # Metadata
    timestamp: str = Field(..., description="Analysis timestamp")
    version: str = Field(..., description="MethylDetector version")
    config_summary: Dict[str, Any] = Field(..., description="Key configuration parameters used")


    model_config = {
        "arbitrary_types_allowed": True,
    }


class MethylDetectorSummary(BaseModel):
    """Summary configuration for completed MethylDetector analysis."""

    # -----------------------
    # Analysis identification
    # -----------------------
    analysis_id: str = Field(..., description="Unique identifier for this analysis")
    timestamp: str = Field(..., description="When analysis was completed")
    version: str = Field(..., description="MethylDetector version used")

    # -----------------------
    # Input configuration
    # -----------------------
    input_files: Dict[str, Union[str, Path]] = Field(
        ..., description="Input centroid files used"
    )

    # -----------------------
    # Results summary
    # -----------------------
    total_statistical_dmps: int = Field(..., description="Total statistically significant DMPs found")
    total_biological_dmps: int = Field(..., description="Total biologically significant DMPs retained")
    biological_retention_rate: float = Field(..., description="Fraction retained after biological filtering")

    # -----------------------
    # Output files
    # -----------------------
    output_directory: Union[str, Path] = Field(..., description="Base output directory")

    csv_file: Optional[Union[str, Path]] = Field(
        default=None, description="Path to biological DMPs CSV file"
    )
    summary_json_file: Optional[Union[str, Path]] = Field(
        default=None, description="Path to analysis summary JSON file"
    )
    classifier_model_file: Optional[Union[str, Path]] = Field(
        default=None, description="Path to trained probabilistic classifier pickle file"
    )

    # -----------------------
    # Key parameters used
    # -----------------------
    key_parameters: Dict[str, Any] = Field(
        ..., description="Key configuration parameters that affected results"
    )

    # -----------------------
    # Quality metrics
    # -----------------------
    classifier_accuracy: Optional[float] = Field(
        default=None, description="Cross-validation accuracy of trained classifier"
    )
    top_dmp_significance: Optional[float] = Field(
        default=None, description="Biological significance score of top-ranked DMP"
    )

    model_config = {
        "arbitrary_types_allowed": True,
    }


class ConfusionMatrix(NumpyCompatibleModel):
    """Confusion matrix for classification results."""
    tp: int = Field(..., description="True positives")
    tn: int = Field(..., description="True negatives")
    fp: int = Field(..., description="False positives")
    fn: int = Field(..., description="False negatives")


class SampleCounts(NumpyCompatibleModel):
    """Sample counts for validation."""
    n_positive: int = Field(..., description="Number of positive samples")
    n_negative: int = Field(..., description="Number of negative samples")
    n_total: int = Field(..., description="Total number of samples")


class PerformanceMetrics(NumpyCompatibleModel):
    """Performance metrics for classification."""
    balanced_accuracy: float = Field(..., description="Balanced accuracy score")
    sensitivity: float = Field(..., description="Sensitivity (recall)")
    specificity: float = Field(..., description="Specificity")
    precision: float = Field(..., description="Precision")
    accuracy: float = Field(..., description="Overall accuracy")


class ValidationResults(NumpyCompatibleModel):
    """Results from a validation run."""
    type: str = Field(..., description="Validation cohort label (typically 'real'; stored for schema compatibility)")
    performance: PerformanceMetrics = Field(..., description="Performance metrics")
    confusion_matrix: ConfusionMatrix = Field(..., description="Confusion matrix")
    sample_counts: SampleCounts = Field(..., description="Sample counts")


class MethylDetectorValidationResults(NumpyCompatibleModel):
    """Final validation results from MethylDetector analysis."""

    # Core identification
    chromosome: str = Field(..., description="Chromosome analyzed")
    timestamp: str = Field(..., description="Analysis completion timestamp")

    # Configuration used
    config: Dict[str, Any] = Field(..., description="Complete configuration used for analysis")

    # Validation results
    optimization_validation: Optional[ValidationResults] = Field(
        default=None,
        description="Holdout / evaluation-fold results (generalization when validation_split_ratio>0)"
    )
    training_fold_validation: Optional[ValidationResults] = Field(
        default=None,
        description="Calibration / training-fold metrics (in-sample fit) per repeated split; same rows as holdout when validation_split_ratio=0",
    )
    real_validation: Optional[ValidationResults] = Field(
        default=None,
        description="Results from real validation (synthetic mode verification)"
    )

    # Export summary
    n_dmps_exported: Optional[int] = Field(
        default=None,
        description="Number of DMP rows exported to the final CSV (current runtime: full biological funnel output, optionally capped for classifier handoff)"
    )
    total_statistical_dmps: Optional[int] = Field(
        default=None,
        description="Total statistically significant DMPs (passing q-value filter)"
    )
    total_biological_dmps: Optional[int] = Field(
        default=None,
        description="Total biologically significant DMPs after the biological filter and optional rescue track"
    )
    biological_filter: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Biological filter thresholds used and value ranges (min/max) of delta_mean, overlap, effect_size in retained DMPs"
    )
    validation_row_split_semantics: Optional[str] = Field(
        default=None,
        description=(
            "How validation rows were split for reported BA: "
            "'no_row_holdout' when validation_split_ratio<=0 (same rows for calibration and test); "
            "'stratified_holdout' when validation_split_ratio>0."
        ),
    )

    model_config = {
        "arbitrary_types_allowed": True,
    }


# ✅ IMPLEMENTED: Full DataFrame Architecture
#
# The system uses pandas DataFrames throughout the entire pipeline:
# - Processing: DataFrame-based operations for filtering/selection
# - Results: DataFrame storage in MethylDetectorResult.biologically_significant_dmps_df
# - Export: Direct DataFrame-to-CSV without object conversion
#
# Benefits achieved:
# ✅ Zero-copy data pipeline from processing to output
# ✅ Faster vectorized operations with pandas/numpy
# ✅ Stronger type safety with DataFrame schemas
# ✅ Better memory efficiency for large datasets
# ✅ More expressive data manipulation
# ✅ Easier testing and debugging
# Architecture:
# - DataFrame operations handled directly in MethylDetector class
# - MethylDetectorResult stores DataFrame directly (no conversion)
# - Direct CSV export from DataFrames (no object serialization)
# - Pydantic models used only for config/results metadata 