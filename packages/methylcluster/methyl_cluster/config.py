"""
Configuration management for MethylCluster.

This module handles configuration parsing and validation for the supported
MethylCluster methods (centroid, HDBSCAN, and hierarchical).
"""

from pathlib import Path
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, field_validator, model_validator
import json
from enum import Enum


class ClusteringMethod(str, Enum):
    """Enumeration of available clustering methods."""
    HDBSCAN = "hdbscan"
    HIERARCHICAL = "hierarchical"
    CENTROID = "centroid"


class ClusterMetric(str, Enum):
    """Enumeration of available distance metrics for clustering."""
    JENSEN_SHANNON = "jensen_shannon"
    WEIGHTED_JENSEN_SHANNON = "weighted_jensen_shannon"
    HELLINGER = "hellinger"
    BHATTACHARYYA = "bhattacharyya"
    WASSERSTEIN = "wasserstein"
    JEFFREYS = "jeffreys"
    KL = "kl"
    
    def to_factory_name(self) -> str:
        """Convert ClusterMetric enum to MethylUtils factory metric name."""
        return self.value


class MethylClusterConfig(BaseModel):
    """
    Pydantic configuration model for MethylCluster parameters.
    
    This configuration defines all parameters for clustering methylation
    samples with the supported MethylCluster methods.
    """
    
    # Sample specification
    samples: List[str] = Field(
        ...,
        description="List of sample directory paths"
    )
    chrom: str = Field(
        ...,
        description="Chromosome identifier (e.g., '1', 'X')"
    )
    ctx: str = Field(
        ...,
        description="Context type (CG, CHG, or CHH)"
    )
    
    # Clustering parameters
    clustering_method: ClusteringMethod = Field(
        default=ClusteringMethod.CENTROID,
        description="Clustering algorithm to use (centroid recommended for methylation data)"
    )
    metric: ClusterMetric = Field(
        default=ClusterMetric.JENSEN_SHANNON,
        description="Distance metric for clustering"
    )
    min_cluster_size: int = Field(
        default=5,
        ge=2,
        description="Minimum number of samples per cluster"
    )
    min_samples: Optional[int] = Field(
        default=None,
        description="HDBSCAN min_samples parameter (defaults to min_cluster_size)"
    )
    cluster_selection_epsilon: float = Field(
        default=0.0,
        ge=0.0,
        description="Distance threshold for cluster merging"
    )
    cluster_selection_method: str = Field(
        default="eom",
        description="Cluster selection method (eom or leaf)"
    )
    allow_single_cluster: bool = Field(
        default=False,
        description="[HDBSCAN only] Allow HDBSCAN to form a single cluster if all samples are close"
    )
    
    # Hierarchical clustering parameters
    linkage_method: str = Field(
        default="average",
        description="[Hierarchical only] Linkage method: average, ward, complete, single"
    )
    max_k: Optional[int] = Field(
        default=None,
        description="Maximum K to test for hierarchical/k-means (defaults to sqrt(n_samples))"
    )
    force_k: Optional[int] = Field(
        default=None,
        ge=2,
        description="Force specific number of clusters (bypasses silhouette threshold). Use when K is known a priori."
    )

    silhouette_threshold: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Minimum silhouette score to accept clusters as meaningful (ignored if force_k is set)"
    )

    forced_groups: Optional[Dict[str, int]] = Field(
        default=None,
        description="Dictionary of forced group labels (keys) and sizes (values) for supervised/confirmation clustering. Sizes must sum to len(samples). Example: {\"Healthy\": 35, \"Cancer\": 15}. Requires clustering_method='centroid'."
    )

    # Deprecated: Use forced_groups dict instead
    # group_labels: Optional[List[str]] = Field(...)
    # group_sizes: Optional[List[int]] = Field(...)

    # K-medoids refinement parameters
    enable_medoid_refinement: bool = Field(
        default=True,
        description="Refine clusters using K-medoids with farthest-point initialization"
    )
    max_medoid_iterations: int = Field(
        default=50,
        ge=1,
        description="Maximum iterations for K-medoids refinement"
    )
    
    # Centroid-based clustering parameters
    max_em_iterations: int = Field(
        default=50,
        ge=1,
        description="[Centroid only] Maximum EM iterations for centroid-based clustering"
    )
    convergence_threshold: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="[Centroid only] Convergence threshold (fraction of samples changing clusters)"
    )
    num_restarts: int = Field(3, description="Number of EM restarts for centroid clustering (1 = no multiple starts)")

    # Soft assignment parameters (new)
    soft_assignment: bool = Field(
        default=True,
        description="Enable soft cluster assignments with membership probabilities (centroid method only)"
    )
    assignment_temperature: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Temperature parameter for softmax in soft assignments (higher values increase uncertainty)"
    )

    # New: Forced init validation
    validate_init: bool = Field(
        default=False,
        description="Validate forced init by checking own-sample fit and swapping bad seeds (centroid only)"
    )

    # New: Cluster balancing
    balance_clusters: bool = Field(
        default=True,
        description="Enable post-assignment balancing of cluster sizes (centroid only)"
    )

    # Legacy K-means fallback (deprecated, use hierarchical method instead)
    enable_kmeans_fallback: bool = Field(
        default=False,
        description="[Deprecated] Enable K-means fallback for HDBSCAN. Use clustering_method='hierarchical' instead."
    )
    
    # Output configuration
    output_dir: str = Field(
        ...,
        description="Output directory for results and visualizations"
    )
    cache_distance_matrix: bool = Field(
        default=True,
        description="Cache computed distance matrix to disk"
    )
    
    # GPU settings
    use_gpu: bool = Field(
        default=True,
        description="Use GPU acceleration for distance computation"
    )
    
    @field_validator('output_dir')
    @classmethod
    def normalize_output_dir(cls, v):
        """Normalize output directory path."""
        from pathlib import Path
        return str(Path(v).resolve())
    
    @field_validator('ctx')
    @classmethod
    def validate_context(cls, v):
        """Validate context is one of the allowed types."""
        valid_contexts = ['CG', 'CHG', 'CHH']
        if v not in valid_contexts:
            raise ValueError(f"Context must be one of {valid_contexts}, got '{v}'")
        return v
    
    @field_validator('cluster_selection_method')
    @classmethod
    def validate_selection_method(cls, v):
        """Validate cluster selection method."""
        valid_methods = ['eom', 'leaf']
        if v not in valid_methods:
            raise ValueError(f"Cluster selection method must be one of {valid_methods}, got '{v}'")
        return v
    
    @field_validator('samples')
    @classmethod
    def validate_samples(cls, v):
        """Validate that at least 2 samples are provided."""
        if len(v) < 2:
            raise ValueError("At least 2 samples are required for clustering")
        return v

    @field_validator('assignment_temperature')
    @classmethod
    def validate_temperature(cls, v):
        """Validate temperature is within reasonable bounds."""
        if v < 0.1:
            raise ValueError("assignment_temperature must be >= 0.1 to avoid numerical issues")
        if v > 10.0:
            raise ValueError("assignment_temperature must be <= 10.0 (excessive softening)")
        return v

    @model_validator(mode='after')
    def validate_forced_groups_after(self):
        """Validate forced groups configuration after all fields are parsed."""
        forced_groups = self.forced_groups
        
        if forced_groups is not None:
            if len(forced_groups) < 2:
                raise ValueError("At least 2 groups required for forced clustering")
            
            labels = list(forced_groups.keys())
            sizes = list(forced_groups.values())
            
            if any(size <= 0 for size in sizes):
                raise ValueError("All group sizes must be positive")
            
            if sum(sizes) != len(self.samples):
                raise ValueError(f"Sum of group_sizes ({sum(sizes)}) must equal number of samples ({len(self.samples)})")
            
            if self.clustering_method != 'centroid':
                raise ValueError("Forced groups only supported with clustering_method='centroid'")
            
            if self.force_k is not None and self.force_k != len(forced_groups):
                raise ValueError("force_k must equal number of forced groups")
            
            # Store parsed lists internally for backward compat or easy access
            self._parsed_group_labels = labels
            self._parsed_group_sizes = sizes
        
        # Fallback for deprecated fields (if you want to keep support)
        # if self.group_labels is not None or self.group_sizes is not None:
        #     ... (old validation)
        
        return self

    def to_file(self, file_path: Path) -> None:
        """Save configuration to JSON file."""
        file_path = Path(file_path)
        with open(file_path, 'w') as f:
            json.dump(self.model_dump(), f, indent=2)
    
    @classmethod
    def from_file(cls, file_path: Path) -> 'MethylClusterConfig':
        """Load configuration from JSON file."""
        file_path = Path(file_path)
        with open(file_path, 'r') as f:
            data = json.load(f)
        return cls(**data)


__all__ = ['MethylClusterConfig', 'ClusterMetric']

