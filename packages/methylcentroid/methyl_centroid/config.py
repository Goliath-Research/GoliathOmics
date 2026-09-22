"""
Configuration management for MethylCentroid.

This module handles configuration parsing, validation, and management
following SOLID principles with clear separation of concerns.
"""

from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator, model_validator
import json



class MethylCentroidConfig(BaseModel):
    """
    Pydantic configuration model for MethylCentroid parameters.

    Workflows:
    - Initial centroid creation: provide add_samples, output_dir for saving
    - Centroid updates: provide add_samples/remove_samples; current cohort is derived from
      existing centroid HDF5 metadata (samples_used) when applicable (see MethylCentroid).
    """

    # Metadata fields (saved to H5 file)
    laboratory: str = Field(..., description="Laboratory or institution name")
    disease: str = Field(..., description="Disease or condition being studied")
    group: str = Field(..., description="Sample group identifier (e.g., 'cancer', 'control')")
    batch: str = Field(..., description="Batch identifier for sample processing")
    
    # Core parameters
    chrom: str = Field(..., description="Chromosome identifier (e.g., '1', 'X')")
    ctx: str = Field(..., description="Context type (e.g., 'CG', 'CHG', 'CHH')")
    output_dir: str = Field(..., description="Directory to save centroid files")
    add_samples: List[str] = Field(
        default=[],
        description="Optional list of new sample paths to add incrementally"
    )
    remove_samples: List[str] = Field(
        default=[],
        description="Optional list of sample paths to remove"
    )
    min_coverage: int = Field(
        default=4,
        ge=1,
        description="Minimum sum of mC and uC for a position"
    )
    use_gpu: bool = Field(
        default=True,
        description=(
            "Prefer an accelerator when the selected gpu_backend can use one. "
            "Operator-set per profile/site."
        ),
    )
    gpu_backend: Optional[str] = Field(
        default=None,
        description=(
            "Numeric array backend: numpy, cupy (NVIDIA CUDA), or mojo "
            "(mojo-align numeric DeviceContext). Unset keeps CuPy-or-NumPy auto. "
            "Operator-set per profile/site; do not encode a package default."
        ),
    )
    max_sample_workers: Optional[int] = Field(
        default=None,
        ge=1,
        description="Maximum parallel workers for sample loading"
    )
    verbose: bool = Field(
        default=True,
        description="Enable verbose output"
    )
    # Coverage capping (binomial thinning) to correct high-coverage outliers before centroid
    cap_coverage: bool = Field(
        default=False,
        description="If True, cap per-CpG coverage on each sample with binomial thinning before adding to centroid",
    )
    cap_coverage_n_cap: Optional[int] = Field(
        default=None,
        ge=1,
        description="Max coverage per position; positions with coverage > this are thinned (requires cap_coverage=True)",
    )
    cap_coverage_seed: Optional[int] = Field(
        default=None,
        description="RNG seed for reproducible capping (optional)",
    )
    # Auto-estimate n_cap from first sample using IQR (median + 1.5*IQR upper fence)
    cap_coverage_auto_n_cap: bool = Field(
        default=False,
        description="If True and cap_coverage_n_cap is None, estimate n_cap from first sample using Q3+1.5*IQR on sampled positions (outlier limit)",
    )
    cap_coverage_n_cap_method: str = Field(
        default="iqr",
        description="Method for auto n_cap: 'iqr' (Q3+iqr_multiplier*IQR, default).",
    )
    cap_coverage_n_cap_iqr_multiplier: float = Field(
        default=1.5,
        ge=0.0,
        description="IQR multiplier for auto n_cap when method=iqr (standard boxplot fence=1.5)",
    )
    cap_coverage_n_cap_max_positions: int = Field(
        default=100_000,
        ge=1000,
        description="Max positions to sample when auto-estimating n_cap (keeps estimation fast)",
    )
    # Binned stats (bin_edges, bin_counts) for ECDF and distribution comparison
    binned_stats_bins: int = Field(
        default=20,
        ge=1,
        description="Number of bins for the required per-position ECDF histogram. Must be >= 1. Default 20.",
    )
    residualize_coef_dir: Optional[str] = Field(
        default=None,
        description=(
            "Optional coefficient directory from pipeline.residualize_fit. "
            "When unset, centroid uses raw betas (shipped-pack path)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def reject_removed_samples_field(cls, data: Any) -> Any:
        if isinstance(data, dict) and "samples" in data:
            raise ValueError(
                'The "samples" field was removed from MethylCentroidConfig. '
                "Use add_samples for a full cohort (typical pipeline runs), or add_samples/remove_samples "
                "for incremental updates; when a centroid HDF5 already exists, the baseline cohort is "
                "read from its metadata. Remove the \"samples\" key from JSON and put paths in add_samples."
            )
        return data

    @field_validator("gpu_backend")
    @classmethod
    def validate_gpu_backend(cls, v):
        from methyl_utils.array_backend import normalize_gpu_backend

        return normalize_gpu_backend(v)

    @field_validator('ctx')
    @classmethod
    def validate_context(cls, v):
        """Validate context is one of CG, CHG, CHH."""
        if v not in ['CG', 'CHG', 'CHH']:
            raise ValueError(f"Context must be one of CG, CHG, CHH, got {v}")
        return v

    @field_validator('chrom')
    @classmethod
    def validate_chromosome(cls, v):
        """Validate chromosome identifier."""
        if not v or not isinstance(v, str):
            raise ValueError("Chromosome must be a non-empty string")
        return v

    def get_metadata(self) -> Dict[str, Any]:
        """
        Extract metadata fields to be saved with the centroid H5 file.
        
        Returns:
            Dictionary containing metadata fields
        """
        from datetime import datetime
        return {
            "laboratory": self.laboratory,
            "disease": self.disease,
            "group": self.group,
            "batch": self.batch,
            "chromosome": self.chrom,
            "context": self.ctx,
            "samples_used": [],  # Will be populated during save_centroid
            "creation_date": datetime.now().isoformat(),
            "min_coverage": self.min_coverage,
        }

    def to_file(self, file_path: Path) -> None:
        """Save configuration to JSON file."""
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(self.model_dump(), f, indent=2)

    @classmethod
    def from_file(cls, file_path: Path) -> 'MethylCentroidConfig':
        """Load configuration from JSON file."""
        with open(file_path, 'r') as f:
            data = json.load(f)
        return cls.model_validate(data)


class CentroidResults(BaseModel):
    """Pydantic model for centroid creation results."""
    final_centroid_path: str = Field(..., description="Path to the final centroid file")
    total_samples_processed: int = Field(..., description="Total number of samples included")


class ProcessingConfig(BaseModel):
    """Configuration for processing parameters that may change dynamically."""

    # Memory management
    enable_caching: bool = Field(default=True, description="Enable sample caching")
    memory_limit_gb: Optional[float] = Field(default=None, description="Memory limit for operations")

    # Performance tuning
    max_workers: Optional[int] = Field(default=None, description="Maximum parallel workers")
    chunk_size_positions: Optional[int] = Field(default=None, description="Chunk size for processing")

    # GPU settings
    use_gpu: bool = Field(default=True, description="Enable GPU acceleration")
    gpu_memory_fraction: float = Field(default=0.8, description="GPU memory fraction to use")

    # Debugging and output
    enable_profiling: bool = Field(default=True, description="Enable performance profiling")
    save_intermediate: bool = Field(default=False, description="Save intermediate results")
    verbose_logging: bool = Field(default=False, description="Enable verbose logging")

    # Validation
    enable_validation: bool = Field(default=True, description="Enable centroid validation")
    enable_visualization: bool = Field(default=True, description="Enable result visualization")


class BatchProcessingConfig(BaseModel):
    """Configuration for batch processing multiple chromosome/context combinations."""

    chromosomes: List[str] = Field(default_factory=list, description="Chromosomes to process")
    contexts: List[str] = Field(default_factory=lambda: ['CG', 'CHG', 'CHH'], description="Contexts to process")
    base_config: MethylCentroidConfig = Field(..., description="Base configuration template")
    context_overrides: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-context overrides merged into base_config"
    )

    # Batch-specific settings
    continue_on_error: bool = Field(default=True, description="Continue processing if one combination fails")
    save_batch_summary: bool = Field(default=True, description="Save batch processing summary")

    @classmethod
    def from_file(cls, file_path: Path) -> 'BatchProcessingConfig':
        """Load batch configuration from JSON file."""
        with open(file_path, 'r') as f:
            data = json.load(f)
        return cls.model_validate(data)
