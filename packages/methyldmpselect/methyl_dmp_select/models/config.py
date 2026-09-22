from __future__ import annotations

from pathlib import Path
from typing import List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

ClassifierDmpSelection = Literal["elbow", "featurecuts_validation"]


class DmpSelectionConfig(BaseModel):
    """Configuration for DMP panel selection from discovery exports."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    chromosome: str = Field(..., description="Chromosome scope for this selection run")
    contexts: List[str] = Field(default_factory=lambda: ["CG"])
    centroid1_dir: str = Field(..., description="Control centroid HDF5 directory")
    centroid2_dir: str = Field(..., description="Disease centroid HDF5 directory")
    output_dir: str = Field(..., description="Directory for classifier CSV and audit JSON outputs")
    discovery_csv: Optional[str] = Field(
        default=None,
        description="Explicit path to dmps-{chr}-discovery.csv; default: output_dir/dmps-{chr}-discovery.csv",
    )

    selection_mode: ClassifierDmpSelection = Field(
        default="elbow",
        validation_alias=AliasChoices("selection_mode", "classifier_dmp_selection"),
        serialization_alias="classifier_dmp_selection",
        description="elbow = effect_size distribution trim; featurecuts_validation = validation BA k-search",
    )
    dynamic_dmp_cutoff_enabled: bool = Field(
        default=True,
        description="Apply effect_size elbow trim before FeatureCuts search pool",
    )
    dynamic_dmp_cutoff_relaxation: float = Field(default=1.0, ge=0.0)
    featurecuts_exhaustive_search: bool = Field(default=False)
    featurecuts_max_candidates: Optional[int] = Field(default=50, ge=1)
    featurecuts_max_k_cap: Optional[int] = Field(default=None, ge=1)
    target_balanced_accuracy: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    min_core_dmps: Optional[int] = Field(default=None, ge=1)
    min_selected_dmps: Optional[int] = Field(default=None, ge=1)
    fail_if_below_target: bool = Field(
        default=False,
        description="Strict mode: reject selection when BA target or min panel size is unmet.",
    )
    classifier_export_margin_pct: float = Field(default=0.10, ge=0.0)
    classifier_export_margin_abs: int = Field(default=0, ge=0)
    classifier_export_max_dmps: Optional[int] = Field(default=None, ge=1)
    min_dmps_for_export: int = Field(default=50, ge=0)

    validation_split_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    validation_n_repeats: int = Field(default=1, ge=1)
    random_state: int = Field(default=42)
    temperature: float = Field(default=1.0, gt=0.0)
    centroid1_validation_samples: Optional[List[str]] = Field(default=None)
    centroid2_validation_samples: Optional[List[str]] = Field(default=None)
    validation_samples_base_path: Optional[str] = Field(default=None)

    dmp_export_mode: Literal["dual", "unified"] = Field(default="dual")
    export_classifier_pickle: bool = Field(
        default=False,
        description="When True, also write classifier-{chr}-{contexts}.pkl (prefer pipeline.classifier)",
    )
