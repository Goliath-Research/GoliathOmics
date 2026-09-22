"""
Pydantic models for genomic methylation analysis.

This module provides structured data models for methylation statistics
and alignment results, enabling easy serialization to JSON and
type-safe data handling.
"""

from typing import List, Optional, Tuple, Dict, Any
import numpy as np
from pathlib import Path
import json
from datetime import datetime

# Import pydantic if available (should be available in container)
try:
    from pydantic import BaseModel, Field
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    # Fallback for environments without pydantic
    class BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def model_dump(self) -> Dict[str, Any]:
            return self.__dict__

        @classmethod
        def model_validate(cls, data: Dict[str, Any]) -> 'BaseModel':
            return cls(**data)

    def Field(default=None, description: str = "", default_factory=None, **kwargs):
        if default_factory is not None:
            return default_factory()
        return default


class PositionMethylationStats(BaseModel):
    """
    Methylation statistics for a single genomic position.

    Attributes:
        position: Genomic position
        avg_methylation_level: Average methylation level (mC/(mC+uC))
        methylation_mean: Mean methylation level across samples
        methylation_stdev: Standard deviation of methylation levels
        sample_count: Number of samples contributing to this position
        total_coverage: Total coverage (mC + uC) at this position
        mC_sum: Sum of methylated cytosine counts
        uC_sum: Sum of unmethylated cytosine counts
    """
    position: int = Field(..., description="Genomic position")
    avg_methylation_level: float = Field(..., description="Average methylation level (mC/(mC+uC))")
    methylation_mean: float = Field(..., description="Mean methylation level across samples")
    methylation_stdev: float = Field(..., description="Standard deviation of methylation levels")
    sample_count: int = Field(..., description="Number of samples contributing to this position")
    total_coverage: int = Field(..., description="Total coverage (mC + uC) at this position")
    mC_sum: int = Field(..., description="Sum of methylated cytosine counts")
    uC_sum: int = Field(..., description="Sum of unmethylated cytosine counts")


class GroupMethylationStats(BaseModel):
    """
    Group-level methylation statistics across all valid positions.

    Attributes:
        group_avg_methylation: Overall group average methylation level
        group_methylation_mean: Overall group mean methylation level
        group_methylation_stdev: Overall group standard deviation
        total_positions: Number of valid positions
        total_samples: Number of samples in the group
        total_mC: Total methylated cytosine counts across all positions
        total_uC: Total unmethylated cytosine counts across all positions
        total_coverage: Total coverage across all positions
    """
    group_avg_methylation: float = Field(..., description="Overall group average methylation level")
    group_methylation_mean: float = Field(..., description="Overall group mean methylation level")
    group_methylation_stdev: float = Field(..., description="Overall group standard deviation")
    total_positions: int = Field(..., description="Number of valid positions")
    total_samples: int = Field(..., description="Number of samples in the group")
    total_mC: int = Field(..., description="Total methylated cytosine counts across all positions")
    total_uC: int = Field(..., description="Total unmethylated cytosine counts across all positions")
    total_coverage: int = Field(..., description="Total coverage across all positions")


class AlignmentStats(BaseModel):
    """
    Comprehensive alignment statistics for the centroid.

    Attributes:
        position_range: Centroid position range as (min_pos, max_pos) - only positions that meet coverage criteria
        total_positions: Total number of positions in the centroid (same as valid_positions for centroid)
        valid_positions: Number of positions meeting coverage criteria (same as total_positions for centroid)
        sample_count: Number of samples in the aligner
        min_coverage: Current minimum coverage threshold
        gpu_acceleration: Whether GPU acceleration is enabled
        memory_usage_mb: Estimated memory usage in megabytes
        is_initialized: Whether the aligner has been initialized
        has_data: Whether the aligner contains any sample data
        methylation_stats_available: Whether methylation statistics are available
    """
    position_range: Optional[Tuple[int, int]] = Field(None, description="Centroid position range as (min_pos, max_pos) - only positions that meet coverage criteria")
    total_positions: int = Field(..., description="Total number of positions in the centroid (same as valid_positions)")
    valid_positions: int = Field(..., description="Number of positions meeting coverage criteria (same as total_positions for centroid)")
    sample_count: int = Field(..., description="Number of samples in the aligner")
    min_coverage: int = Field(..., description="Current minimum coverage threshold")
    gpu_acceleration: bool = Field(..., description="Whether GPU acceleration is enabled")
    memory_usage_mb: float = Field(..., description="Estimated memory usage in megabytes")
    is_initialized: bool = Field(..., description="Whether the aligner has been initialized")
    has_data: bool = Field(..., description="Whether the aligner contains any sample data")
    methylation_stats_available: bool = Field(..., description="Whether methylation statistics are available")


class MethylationAnalysisResults(BaseModel):
    """
    Complete methylation analysis results.

    This model contains all the results from a methylation analysis,
    including per-position statistics, group statistics, and alignment
    information. It can be easily serialized to JSON for later analysis.

    Attributes:
        timestamp: When the analysis was performed
        analysis_id: Unique identifier for this analysis
        alignment_stats: General alignment statistics
        group_stats: Group-level methylation statistics
        position_stats: Per-position methylation statistics
        metadata: Additional metadata about the analysis
    """
    timestamp: datetime = Field(default_factory=datetime.now, description="When the analysis was performed")
    analysis_id: str = Field(..., description="Unique identifier for this analysis")
    alignment_stats: AlignmentStats = Field(..., description="General alignment statistics")
    group_stats: Optional[GroupMethylationStats] = Field(None, description="Group-level methylation statistics")
    position_stats: List[PositionMethylationStats] = Field(default_factory=list, description="Per-position methylation statistics")
    metadata: dict = Field(default_factory=dict, description="Additional metadata about the analysis")

    def save_to_json(self, filepath: Path) -> None:
        """
        Save the analysis results to a JSON file.

        Args:
            filepath: Path to save the JSON file
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, 'w') as f:
            json.dump(self.model_dump(), f, indent=2, default=str)

    @classmethod
    def load_from_json(cls, filepath: Path) -> 'MethylationAnalysisResults':
        """
        Load analysis results from a JSON file.

        Args:
            filepath: Path to the JSON file

        Returns:
            MethylationAnalysisResults instance

        Raises:
            FileNotFoundError: If the file doesn't exist
            ValidationError: If the JSON data is invalid
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"Analysis results file not found: {filepath}")

        with open(filepath, 'r') as f:
            data = json.load(f)

        return cls.model_validate(data)

    def get_summary(self) -> dict:
        """
        Get a summary of the analysis results.

        Returns:
            Dictionary containing key summary statistics
        """
        summary = {
            "analysis_id": self.analysis_id,
            "timestamp": self.timestamp.isoformat(),
            "total_positions": self.alignment_stats.total_positions,
            "valid_positions": self.alignment_stats.valid_positions,
            "sample_count": self.alignment_stats.sample_count,
            "memory_usage_mb": self.alignment_stats.memory_usage_mb,
            "gpu_acceleration": self.alignment_stats.gpu_acceleration
        }

        if self.group_stats:
            summary.update({
                "group_avg_methylation": self.group_stats.group_avg_methylation,
                "group_methylation_mean": self.group_stats.group_methylation_mean,
                "group_methylation_stdev": self.group_stats.group_methylation_stdev,
                "total_coverage": self.group_stats.total_coverage
            })

        return summary


def create_analysis_results(
    analysis_id: str,
    alignment_stats: dict,
    group_stats: Optional[dict] = None,
    position_data: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = None,
    metadata: Optional[dict] = None
) -> MethylationAnalysisResults:
    """
    Create a MethylationAnalysisResults instance from raw data.

    This function is provided for backward compatibility and for creating
    results from raw data arrays. For new code, prefer using the
    PositionAligner.create_analysis_results() method which returns
    proper Pydantic models directly.

    Args:
        analysis_id: Unique identifier for this analysis
        alignment_stats: Dictionary containing alignment statistics
        group_stats: Dictionary containing group methylation statistics (optional)
        position_data: Tuple of (valid_pos, avg_levels, means, stdevs, N, mC_sum, uC_sum) arrays (optional)
        metadata: Additional metadata dictionary (optional)

    Returns:
        MethylationAnalysisResults instance
    """
    # Create alignment stats - handle missing fields that come from properties
    alignment_stats_dict = alignment_stats.copy()

    # Convert position_range tuple to proper format if it exists
    if "position_range" in alignment_stats_dict and alignment_stats_dict["position_range"] is not None:
        min_pos, max_pos = alignment_stats_dict["position_range"]
        alignment_stats_dict["position_range"] = (int(min_pos), int(max_pos))

    # Add missing fields with default values if not present
    if "memory_usage_mb" not in alignment_stats_dict:
        alignment_stats_dict["memory_usage_mb"] = 0.0
    if "is_initialized" not in alignment_stats_dict:
        alignment_stats_dict["is_initialized"] = alignment_stats_dict.get("position_range") is not None
    if "has_data" not in alignment_stats_dict:
        alignment_stats_dict["has_data"] = alignment_stats_dict.get("sample_count", 0) > 0
    if "methylation_stats_available" not in alignment_stats_dict:
        alignment_stats_dict["methylation_stats_available"] = group_stats is not None

    alignment_stats_model = AlignmentStats(**alignment_stats_dict)

    # Create group stats if provided
    group_stats_model = None
    if group_stats:
        # Convert numpy arrays to Python types
        group_stats_dict = {}
        for key, value in group_stats.items():
            if hasattr(value, 'item'):  # numpy scalar
                group_stats_dict[key] = value.item()
            elif isinstance(value, (list, tuple)) and len(value) > 0 and hasattr(value[0], 'item'):
                # numpy array
                group_stats_dict[key] = [v.item() for v in value]
            else:
                group_stats_dict[key] = value

        group_stats_model = GroupMethylationStats(**group_stats_dict)

    # Create position stats if provided
    position_stats = []
    if position_data is not None:
        valid_pos, avg_levels, means, stdevs, N, mC_sum, uC_sum = position_data

        for i in range(len(valid_pos)):
            position_stat = PositionMethylationStats(
                position=int(valid_pos[i]),
                avg_methylation_level=float(avg_levels[i]),
                methylation_mean=float(means[i]),
                methylation_stdev=float(stdevs[i]),
                sample_count=int(N[i]),
                total_coverage=int(mC_sum[i] + uC_sum[i]),
                mC_sum=int(mC_sum[i]),
                uC_sum=int(uC_sum[i])
            )
            position_stats.append(position_stat)

    # Create metadata
    metadata = metadata or {}

    return MethylationAnalysisResults(
        analysis_id=analysis_id,
        alignment_stats=alignment_stats_model,
        group_stats=group_stats_model,
        position_stats=position_stats,
        metadata=metadata
    )
