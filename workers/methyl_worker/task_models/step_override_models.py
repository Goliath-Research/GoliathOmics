"""Typed --step-override payloads for pipeline workflow actions."""

from __future__ import annotations

from typing import List, Literal, Optional

from methyl_classifier.models.config_schema import ClassificationConfig
from methyl_disease_progression.config import ProgressionStepConfig
from methyl_enricher.config import EnricherStepConfig
from methyl_mapper.config import MapperStepConfig
from pydantic import BaseModel, ConfigDict, Field


class SampleCohortOverride(BaseModel):
    """Incremental sample cohort changes (synthesized by worker or passed in base_config)."""

    model_config = ConfigDict(extra="forbid")

    add_samples: Optional[List[str]] = None
    remove_samples: Optional[List[str]] = None


class CentroidBaseConfigOverride(SampleCohortOverride):
    """Optional centroid base_config fields merged by methyl-centroid --step-override."""

    model_config = ConfigDict(extra="forbid")

    min_coverage: Optional[int] = Field(default=None, ge=1)
    use_gpu: Optional[bool] = None
    gpu_backend: Optional[str] = Field(
        default=None,
        description=(
            "Numeric array backend: numpy, cupy, or mojo. Unset keeps CuPy-or-NumPy auto. "
            "Operator-set per site/profile."
        ),
    )
    max_sample_workers: Optional[int] = Field(default=None, ge=1)
    verbose: Optional[bool] = None
    cap_coverage: Optional[bool] = None
    cap_coverage_n_cap: Optional[int] = Field(default=None, ge=1)
    cap_coverage_seed: Optional[int] = None
    cap_coverage_auto_n_cap: Optional[bool] = None
    cap_coverage_n_cap_method: Optional[str] = None
    cap_coverage_n_cap_iqr_multiplier: Optional[float] = Field(default=None, ge=0.0)
    cap_coverage_n_cap_max_positions: Optional[int] = Field(default=None, ge=1000)
    binned_stats_bins: Optional[int] = Field(default=None, ge=1)


class CentroidStepOverride(BaseModel):
    """MC / workflow overrides for methyl-centroid."""

    model_config = ConfigDict(extra="forbid")

    base_config: Optional[CentroidBaseConfigOverride] = None
    chromosomes: Optional[List[str]] = None
    contexts: Optional[List[str]] = None
    continue_on_error: Optional[bool] = None
    save_batch_summary: Optional[bool] = None
    chromosome: Optional[str] = None
    context: Optional[str] = None
    comparison: Optional[str] = None
    output_dir: Optional[str] = None


_DETECTOR_WORKFLOW_ONLY_KEYS = frozenset({"context", "comparison", "base_config"})


class DetectorStepOverride(BaseModel):
    """Scope fields and detection-step overrides folded into methyl-detector --step-override."""

    model_config = ConfigDict(extra="forbid")

    chromosome: Optional[str] = None
    contexts: Optional[List[str]] = None
    context: Optional[str] = None
    fixed_dmp_panel: Optional[str] = None
    output_dir: Optional[str] = None
    comparison: Optional[str] = None
    base_config: Optional[SampleCohortOverride] = None
    detection_mode: Optional[Literal["legacy", "discovery_only"]] = None
    export_classifier: Optional[bool] = None
    dmp_export_mode: Optional[Literal["unified", "dual"]] = None
    classifier_dmp_selection: Optional[Literal["elbow", "featurecuts_validation"]] = None
    target_balanced_accuracy: Optional[float] = None
    min_core_dmps: Optional[int] = None
    classifier_export_margin_pct: Optional[float] = None
    classifier_export_margin_abs: Optional[int] = None
    classifier_export_max_dmps: Optional[int] = None

    def to_methyl_detector_payload(self) -> dict[str, object]:
        """Export only methyl-detector config keys; omit workflow scope fields and unset nulls."""
        from methyl_detector.models.config import MethylDetectorConfig

        allowed = set(MethylDetectorConfig.model_fields.keys())
        raw = self.model_dump(mode="json", exclude_none=True, exclude_unset=True)
        return {k: v for k, v in raw.items() if k in allowed and k not in _DETECTOR_WORKFLOW_ONLY_KEYS}


class DmpSelectStepOverride(BaseModel):
    """Per-invocation overrides for methyl-dmp-select --step-override."""

    model_config = ConfigDict(extra="forbid")

    classifier_dmp_selection: Optional[Literal["elbow", "featurecuts_validation"]] = None
    target_balanced_accuracy: Optional[float] = None
    min_core_dmps: Optional[int] = None
    classifier_export_margin_pct: Optional[float] = None
    classifier_export_margin_abs: Optional[int] = None
    classifier_export_max_dmps: Optional[int] = None
    fail_if_below_target: Optional[bool] = None
    output_dir: Optional[str] = None


class MapperStepOverride(MapperStepConfig):
    """Flat mapper overrides from program stepOverride or resolvedConfig."""

    model_config = ConfigDict(extra="forbid")


class EnricherStepOverride(EnricherStepConfig):
    model_config = ConfigDict(extra="forbid")


class ProgressionStepOverride(ProgressionStepConfig):
    model_config = ConfigDict(extra="forbid")


class ClassifierStepOverride(ClassificationConfig):
    model_config = ConfigDict(extra="forbid")


class PredictorStepOverride(BaseModel):
    """Optional predictor step overrides (--step-override)."""

    model_config = ConfigDict(extra="forbid")

    model_path: Optional[str] = None
    model_dir: Optional[str] = None
    debug: Optional[bool] = None
    decision_enabled: Optional[bool] = None
    decision_min_margin: Optional[float] = None
    decision_min_confidence: Optional[float] = None
