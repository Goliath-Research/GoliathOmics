from pydantic import Field, field_validator, model_validator
from typing import Any, Optional, Dict, Literal, List
from pathlib import Path
from pydantic import BaseModel

WeightMethod = Literal["config", "effect_size", "linear_fitted", "logistic_fitted", "elasticnet_fitted"]
WeightFitRegularization = Literal["none", "ridge", "lasso", "l1", "l2"]


class ClassifierConfig(BaseModel):
    # Model path (can be file or directory for multi-chromosome mode)
    model_path: Optional[str] = Field(
        default=None,
        description="Path to trained classifier model (.pkl file) or directory containing classifier-{chrom}.pkl files"
    )
    # Multi-chromosome parameters
    model_dir: Optional[str] = Field(
        default=None,
        description="Path to directory containing classifier-{chrom}.pkl files (alternative to model_path)"
    )
    ovr_binary_model_paths: Optional[List[str]] = Field(
        default=None,
        description="K>=2 MethylDetector pickle paths (one per class, OvR order). Builds ecdf_one_vs_rest in memory; no model_path/model_dir needed when set.",
    )
    ovr_detection_dirs: Optional[List[str]] = Field(
        default=None,
        description="K>=2 MethylDetector output directories (OvR order). One classifier*.pkl → single-chrom ECDF head; multiple classifier-*.pkl → multi-chromosome weighted expert per class. Alternative to ovr_binary_model_paths.",
    )
    ovr_class_names: Optional[List[str]] = Field(
        default=None,
        description="Class names matching OvR order (length K). Required when using ovr_binary_model_paths or ovr_detection_dirs unless passed via ClassificationConfig.multiclass_class_names.",
    )
    ovr_pairwise_aggregate_control: bool = Field(
        default=False,
        description="When true with ovr_detection_dirs: first class is control; dirs are K-1 pairwise control-vs-disease folders; control OvR head aggregates P(control) across those pairwises.",
    )
    ovr_fuse_mode: Optional[str] = Field(
        default=None,
        description=(
            "Optional OvR fusion mode override: 'flat' or 'pairwise_max_contrast'. "
            "None uses package metadata/default behavior."
        ),
    )
    ovr_bipartite_aggregate: bool = Field(
        default=False,
        description="When true with ovr_detection_dirs: multi-control × multi-disease bipartite. "
        "Dirs must be M×N in row-major order (each control × each disease). class_names = M controls + N diseases. "
        "Requires ovr_n_control_classes=M.",
    )
    ovr_n_control_classes: Optional[int] = Field(
        default=None,
        ge=1,
        description="With ovr_bipartite_aggregate: number of control strata (M); disease count N = len(ovr_class_names) - M.",
    )
    trimmed_percentile_low: float = Field(
        default=0.10,
        ge=0.0,
        le=0.5,
        description="Lower percentile for trimmed mean effect_size calculation - removes bottom X% (default: 0.10 = remove bottom 10%)"
    )
    trimmed_percentile_high: float = Field(
        default=0.01,
        ge=0.0,
        le=0.5,
        description="Upper percentile for trimmed mean effect_size calculation - removes top X% (default: 0.01 = remove top 1%). High effect_size DMPs are important for classification, so only remove outliers."
    )
    chromosome_weights: Optional[Dict[str, float]] = Field(
        default=None,
        description="Predefined chromosome weights (bypasses trimmed-mean calculation). Dict format: {'1': 0.5, '2': 0.3, ...}"
    )
    weight_method: Optional[WeightMethod] = Field(
        default=None,
        description="How to obtain chromosome weights: 'config', 'effect_size', 'linear_fitted', 'logistic_fitted', or 'elasticnet_fitted'. If None, inferred: config when chromosome_weights set, else effect_size."
    )
    weight_fit_regularization: Optional[WeightFitRegularization] = Field(
        default="none",
        description="For linear_fitted: 'none', 'ridge', 'lasso'. For logistic_fitted: 'none', 'l1', 'l2'. For elasticnet_fitted: ignored (uses l1_ratio)."
    )
    weight_fit_alpha: float = Field(
        default=1.0,
        ge=0.0,
        description="Regularization strength for fitted weights (inverse of C for logistic)."
    )
    weight_fit_l1_ratio: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="For weight_method='elasticnet_fitted': balance L1/L2 (0=ridge-like, 1=lasso-like)."
    )
    # Prediction parameters
    temperature: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Temperature for softmax in prediction"
    )
    enable_platt_calibration: bool = Field(
        default=False,
        description="Enable Platt scaling calibration on validation data"
    )
    class_priors: Optional[List[float]] = Field(
        default=None,
        description=(
            "Optional binary class priors [p(class0), p(class1)] used by ECDF posterior. "
            "If omitted, priors default to uniform."
        ),
    )
    dependence_block_size: int = Field(
        default=1,
        ge=1,
        description=(
            "Optional dependence-aware aggregation block size for ECDF features. "
            "1 keeps independent-loci aggregation."
        ),
    )
    dependence_block_shrinkage: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Optional shrinkage factor for block-level dependence-aware aggregation "
            "(0.0 = no shrinkage)."
        ),
    )
    use_elasticnet_stacking: bool = Field(
        default=False,
        description="Enable ElasticNet regression to learn optimal chromosome weights during training. When True, bypasses other weight methods."
    )
    use_isotonic_calibration: bool = Field(
        default=False,
        description="Enable Isotonic Regression calibration to map raw scores to calibrated probabilities."
    )
    calibration_train_fraction: Optional[float] = Field(
        default=None,
        description=(
            "If set in (0, 1), fit isotonic calibration and fitted chromosome weights on a stratified "
            "per-class fraction of samples (like MC train_fraction); calibrators apply to all rows. "
            "None or 1.0 uses the full labeled batch for fitting."
        ),
    )
    calibration_seed: Optional[int] = Field(
        default=None,
        description="RNG seed for the stratified calibration/stacking fit mask.",
    )

    # Output configuration
    chromosome_matrix_path: Optional[str] = Field(
        default=None,
        description="Optional output CSV file for chromosome probability matrix (samples × chromosomes)"
    )
    project_name: Optional[str] = Field(
        default=None,
        description="Project name for saving the classifier as <project_name>-classifier.pkl and sample list as <project_name>-samples.txt (or .csv). Used when save_classifier_path or samples_list_export_path are not set."
    )
    save_classifier_path: Optional[str] = Field(
        default=None,
        description="Path to save the final classifier as .pkl after classification. If null and project_name is set, saves to <output_dir>/<project_name>-classifier.pkl."
    )
    samples_list_export_path: Optional[str] = Field(
        default=None,
        description="Path to export the list of sample folders (.txt or .csv). If null and project_name is set, exports to <output_dir>/<project_name>-samples.txt."
    )
    panel: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Hierarchical panel readout for OvR + pairwise max-contrast: primary_family, families (partition of disease class names), optional indeterminate_delta. Passed through to classification CSV / panel_report.json.",
    )

    @field_validator('temperature')
    @classmethod
    def validate_temperature(cls, v):
        if v < 0.1:
            raise ValueError("Temperature must be >= 0.1")
        if v > 10.0:
            raise ValueError("Temperature must be <= 10.0")
        return v
    
    @field_validator('trimmed_percentile_low', 'trimmed_percentile_high')
    @classmethod
    def validate_trimmed_percentile(cls, v):
        if v < 0.0 or v > 0.5:
            raise ValueError("Trimmed percentiles must be between 0.0 and 0.5")
        return v
    
    @field_validator('weight_method')
    @classmethod
    def validate_weight_method(cls, v):
        if v is not None and v not in ("config", "effect_size", "linear_fitted", "logistic_fitted", "elasticnet_fitted"):
            raise ValueError("weight_method must be one of: config, effect_size, linear_fitted, logistic_fitted, elasticnet_fitted")
        return v

    @field_validator('weight_fit_regularization')
    @classmethod
    def validate_weight_fit_regularization(cls, v):
        if v is not None and v not in ("none", "ridge", "lasso", "l1", "l2"):
            raise ValueError("weight_fit_regularization must be one of: none, ridge, lasso, l1, l2")
        return v

    @field_validator("ovr_fuse_mode")
    @classmethod
    def validate_ovr_fuse_mode(cls, v):
        if v is None:
            return v
        vv = str(v).strip().lower()
        if vv not in {"flat", "pairwise_max_contrast"}:
            raise ValueError("ovr_fuse_mode must be one of: flat, pairwise_max_contrast")
        return vv

    @field_validator("calibration_train_fraction")
    @classmethod
    def validate_calibration_train_fraction(cls, v):
        if v is None:
            return v
        if v <= 0 or v > 1:
            raise ValueError("calibration_train_fraction must be None or in (0, 1]")
        return v

    @field_validator("class_priors")
    @classmethod
    def validate_class_priors(cls, v):
        if v is None:
            return v
        if len(v) != 2:
            raise ValueError("class_priors must have exactly two values: [p0, p1]")
        if any(float(x) <= 0 for x in v):
            raise ValueError("class_priors values must be > 0")
        s = float(sum(float(x) for x in v))
        if s <= 0:
            raise ValueError("class_priors must sum to > 0")
        return [float(x) / s for x in v]

    @model_validator(mode='after')
    def validate_trimmed_percentile_sum(self):
        # Check that the sum doesn't exceed 1.0 (would trim everything)
        if self.trimmed_percentile_low + self.trimmed_percentile_high >= 1.0:
            raise ValueError(f"Sum of trimmed_percentile_low ({self.trimmed_percentile_low}) and trimmed_percentile_high ({self.trimmed_percentile_high}) must be less than 1.0")
        return self

    @model_validator(mode="after")
    def validate_model_source(self) -> "ClassifierConfig":
        ovr_pkls = self.ovr_binary_model_paths or []
        ovr_dirs = self.ovr_detection_dirs or []
        if len(ovr_pkls) >= 2 and len(ovr_dirs) >= 2:
            raise ValueError(
                "Use either ovr_binary_model_paths or ovr_detection_dirs, not both."
            )
        agg = self.ovr_pairwise_aggregate_control
        bip = self.ovr_bipartite_aggregate
        if agg and bip:
            raise ValueError(
                "Use only one of ovr_pairwise_aggregate_control and ovr_bipartite_aggregate"
            )
        M = int(self.ovr_n_control_classes or 0)
        names_list = self.ovr_class_names or []
        bip_ok = False
        if bip and M >= 1 and names_list and len(ovr_dirs) == M * (len(names_list) - M):
            bip_ok = True
        has_ovr = (
            len(ovr_pkls) >= 2
            or len(ovr_dirs) >= 2
            or (
                agg
                and len(ovr_dirs) >= 1
                and self.ovr_class_names
                and len(self.ovr_class_names) == len(ovr_dirs) + 1
            )
            or bip_ok
        )
        has_path = bool(self.model_path or self.model_dir)
        if has_ovr and has_path:
            raise ValueError(
                "Use either (model_path or model_dir) or OvR sources "
                "(ovr_binary_model_paths / ovr_detection_dirs), not both."
            )
        if not has_ovr and not has_path:
            raise ValueError(
                "Provide model_path, model_dir, ovr_binary_model_paths (>=2), "
                "ovr_detection_dirs (>=2), ovr_pairwise_aggregate_control with "
                "ovr_detection_dirs and ovr_class_names of length len(dirs)+1, or "
                "ovr_bipartite_aggregate with ovr_n_control_classes and M×N detection dirs."
            )
        if has_ovr:
            names = self.ovr_class_names
            if len(ovr_pkls) >= 2:
                k = len(ovr_pkls)
                if not names or len(names) != k:
                    raise ValueError(
                        f"ovr_class_names must be a list of length {k} when using OvR auto-build"
                    )
            elif bip:
                if M < 1:
                    raise ValueError("ovr_bipartite_aggregate requires ovr_n_control_classes>=1")
                elif not names or len(names) <= M:
                    raise ValueError(
                        "ovr_bipartite_aggregate requires ovr_class_names with M+N names (N>=1 disease)"
                    )
                elif len(ovr_dirs) != M * (len(names) - M):
                    raise ValueError(
                        "ovr_bipartite_aggregate requires len(ovr_detection_dirs) == M * N "
                        f"(M={M}, N={len(names) - M}, dirs={len(ovr_dirs)})"
                    )
            elif agg:
                if (
                    len(ovr_dirs) < 1
                    or not names
                    or len(names) != len(ovr_dirs) + 1
                    or len(names) < 2
                ):
                    raise ValueError(
                        "ovr_pairwise_aggregate_control requires ovr_class_names of length "
                        "len(ovr_detection_dirs)+1 (>=2)"
                    )
            else:
                k = len(ovr_dirs)
                if not names or len(names) != k:
                    raise ValueError(
                        f"ovr_class_names must be a list of length {k} when using OvR auto-build"
                    )
        return self
    
    class Config:
        """Pydantic config."""
        protected_namespaces = ()  # Allow 'model_' prefix
