from pathlib import Path
from typing import List, Literal, Optional, Union, Dict  # noqa: F401

DmpExportMode = Literal["unified", "dual"]
ClassifierDmpSelection = Literal["elbow", "featurecuts_validation"]
from math import ceil

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from enum import Enum

# Import utility for extracting chromosome from filenames
try:
    from methyl_detector.utils.file_utils import get_chromosome_context_from_filename  # type: ignore[import-not-found]
except ImportError:
    # Fallback if not available
    def get_chromosome_context_from_filename(filepath):
        filename = Path(filepath).stem
        if '-' in filename:
            parts = filename.split('-')
            if len(parts) >= 2:
                return {'chromosome': parts[0], 'context': parts[1]}
        return {'chromosome': 'unknown', 'context': 'unknown'}




class FilterFunnelRangeSpec(BaseModel):
    """Range and step for sweeping one biological filter (min, min+step, ... up to max)."""
    min: float = Field(..., description="Minimum value (inclusive)")
    max: float = Field(..., description="Maximum value (inclusive)")
    step: float = Field(..., gt=0, description="Step between values")

    @model_validator(mode="after")
    def min_le_max(self):
        if self.min > self.max:
            raise ValueError("min must be <= max")
        return self


class FilterFunnelExplore(BaseModel):
    """
    Optional exploration: sweep effect_size_coverage over a range and write filter_funnel-{chromosome}.csv.
    """
    effect_size_coverage: Optional[FilterFunnelRangeSpec] = Field(
        default=None,
        description="Range/step for effect_size_coverage (values in [0,1], e.g. min=0.80, max=0.99, step=0.05)"
    )


class MethylDetectorConfig(BaseModel):
    """Simplified configuration for MethylDetector analysis."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_distribution_keys(cls, data):
        """Reject legacy detector knobs that no longer map to runtime behavior."""
        if isinstance(data, dict):
            _REMOVED_DETECTOR_KEYS = {
                "max_dmps_for_classifier": (
                    "max_dmps_for_classifier was removed from detector config. "
                    "Use dynamic_dmp_cutoff_enabled / dynamic_dmp_cutoff_relaxation, "
                    "classifier_dmp_selection, featurecuts_max_k_cap, and min_selected_dmps instead."
                ),
                "use_gpu": (
                    "use_gpu was removed from detector config. "
                    "The detector uses GPU automatically when CUDA/CuPy is available and falls back to CPU otherwise."
                ),
                "validation_mode": (
                    "validation_mode was removed; MethylDetector always uses real validation samples "
                    "(centroid1_validation_samples / centroid2_validation_samples or centroid metadata)."
                ),
                "n_validation_samples": (
                    "n_validation_samples was removed; synthetic validation generation is no longer supported."
                ),
                "min_sample_coverage": (
                    "min_sample_coverage was removed from detector config. "
                    "Align inference coverage with centroid training via methylclassifier / centroid base_config.min_coverage."
                ),
                "min_validation_coverage_per_position": (
                    "min_validation_coverage_per_position was removed; validation matrices use all positions "
                    "with sufficient per-sample coverage (see validation_min_coverage)."
                ),
                "classifier_coverage_weighting": (
                    "classifier_coverage_weighting was removed; classifier weighting follows the current ECDF pipeline defaults."
                ),
                "synthetic_config": (
                    "synthetic_config was removed; synthetic validation generation is no longer supported."
                ),
                "classifier_type": (
                    "classifier_type was removed from detector config; exports always use the ECDF classifier."
                ),
                "eps": (
                    "eps was removed from detector config; numerical guards are fixed internally."
                ),
            }
            for key, message in _REMOVED_DETECTOR_KEYS.items():
                if key in data:
                    raise ValueError(message)

            if "ecdf_overlap_grid_size" in data or "ecdf_ks_grid_size" in data:
                raise ValueError(
                    "ecdf_overlap_grid_size and ecdf_ks_grid_size are no longer supported. "
                    "Set ecdf_grid_size (single grid for ECDF/KS and overlap integration)."
                )

            if "statistical_test" in data:
                raise ValueError(
                    "statistical_test is no longer supported. Use significance_test instead "
                    "(e.g. 'ks_ecdf' or 'mann_whitney')."
                )
            legacy_keys = {"distribution", "delta_mean_mode", "overlap_mode", "max_N_for_ecdf"}
            used = sorted(k for k in legacy_keys if k in data)
            if used:
                raise ValueError(
                    "Legacy distribution-specific detector options are no longer supported: "
                    f"{used}. MethylDetector now uses the ECDF-first comparison pipeline."
                )
        return data

    # ----------------
    # Input/Output
    # ----------------
    chromosome: Union[str, List[str]] = Field(
        ..., description="Chromosome(s) to process. Can be a single chromosome (e.g., '1', 'X', '22') or a list (e.g., ['1', '2', 'X'])"
    )
    contexts: List[str] = Field(
        default=["CG"],
        description="List of methylation contexts to process (e.g., ['CG'], ['CG', 'CHG'], or ['CG', 'CHG', 'CHH']). Default is ['CG'] as it typically provides the strongest signal for most analyses."
    )
    centroid1_dir: Union[str, Path] = Field(
        ..., description="Directory containing centroid1 .h5 files (format: {chrom}-{context}.h5)"
    )
    centroid2_dir: Union[str, Path] = Field(
        ..., description="Directory containing centroid2 .h5 files (format: {chrom}-{context}.h5)"
    )
    
    # Backward compatibility: allow old centroid paths (optional)
    centroid1_path: Optional[Union[str, Path]] = Field(
        default=None, description="[Deprecated] Path to single centroid .h5 file (for single-context mode)"
    )
    centroid2_path: Optional[Union[str, Path]] = Field(
        default=None, description="[Deprecated] Path to single centroid .h5 file (for single-context mode)"
    )
    
    output_dir: Optional[Union[str, Path]] = Field(
        default=None, description="Output directory for results"
    )

    fixed_dmp_panel: Optional[Union[str, Path]] = Field(
        default=None,
        description="Path to a fixed DMP panel CSV (chromosome,position,context,...). If provided, bypasses statistical/biological discovery and uses only these positions (for production freeze).",
    )
    detection_mode: Literal["legacy", "discovery_only"] = Field(
        default="legacy",
        description=(
            "legacy: run discovery plus inline classifier panel selection and optional pickle export. "
            "discovery_only: export dmps-{chr}-discovery.csv only; run methyl-dmp-select and pipeline.classifier separately."
        ),
    )
    export_classifier: bool = Field(
        default=True,
        description="When True in legacy mode, write classifier pickle after panel selection. Set False when pipeline.classifier trains the model.",
    )

    centroid1_validation_samples: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="Validation cohort paths for centroid1, or the string 'use_metadata' to read from centroid H5 (default when omitted: use_metadata).",
    )
    centroid2_validation_samples: Optional[Union[str, List[str]]] = Field(
        default=None,
        description="Validation cohort paths for centroid2, or 'use_metadata'.",
    )
    validation_samples_base_path: Optional[str] = Field(
        default=None,
        description="When centroid H5 stores sample directory basenames without samples_base_path, prepend this directory (typically project samples_base_path).",
    )
    validation_split_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Fraction of validation samples per class held out as a test row split for BA (repeated stratified). "
            "0 = no row holdout (calibration and evaluation indices are the same — optimistic). "
            "Use e.g. 0.2 for generalization-style BA within the validation matrix."
        ),
    )
    validation_n_repeats: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Number of repeated stratified holdout splits when validation_split_ratio > 0.",
    )

    # ----------------
    # Statistical Filter
    # ----------------
    alpha: float = Field(
        default=0.05, ge=0.0, le=1.0,
        description="Significance level for statistical tests (q-value threshold)"
    )

    random_state: int = Field(
        default=42,
        ge=0,
        description="Random seed for reproducibility"
    )

    # ----------------
    # Coverage and sample filters (per-centroid; candidate positions = intersection of valid sets)
    # ----------------
    min_coverage: int = Field(
        default=4, ge=1,
        description="Minimum total coverage (Sm+uC) per position in each centroid for a position to be a DMP candidate. Same default as MethylCentroid; use higher for stricter precision when comparing centroids."
    )
    min_samples_abs: int = Field(
        default=1, ge=1,
        description="Absolute minimum number of samples (N) per position in each centroid."
    )
    min_samples_pct: float = Field(
        default=0.05, ge=0.0, le=1.0,
        description=(
            "Minimum fraction of that centroid's cohort (samples) per position. "
            "Effective min N per centroid = max(min_samples_abs, ceil(min_samples_pct * cohort_size)). "
            "Cohort size is resolved from validation sample lists when set, else centroid metadata "
            "(n_samples or sample_paths/samples_used length), else max(N) across positions as a fallback."
        )
    )

    # ----------------
    # Biological Filter
    # ----------------
    effect_size_coverage: float = Field(
        default=0.95, ge=0.0, le=1.0,
        description=(
            "Biological filter: select the minimum set of statistical DMPs (per context) "
            "whose effect sizes sum to this fraction of total effect mass. "
            "1.0 = keep all statistical DMPs (no biological filter); "
            "0.95 = keep the minimum set covering 95%% of total effect mass. "
            "Applied per-context so CG/CHG/CHH are selected independently."
        )
    )
    biological_only_effect_size_coverage: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Optional rescue track for biologically strong but underpowered loci (q > alpha). When set, apply the same per-context effect-mass selection to non-significant positions and flag them as biological-only."
    )
    biological_only_max_candidates: Optional[int] = Field(
        default=5000, ge=1,
        description="Maximum number of non-significant candidates per context to consider for biological-only rescue before ECDF rescoring."
    )
    # New calibration parameters (for trained classifier metadata)
    temperature: float = Field(
        default=1.0, ge=0.1, le=10.0,
        description="Temperature for softmax in trained classifier (1.0 = original, higher softens probabilities)"
    )
    enable_platt_calibration: bool = Field(
        default=False,
        description="Enable Platt scaling calibration on validation data during classification"
    )

    @field_validator('contexts', mode='before')
    @classmethod
    def validate_contexts(cls, v):
        """Validate that contexts are valid methylation contexts."""
        valid_contexts = {"CG", "CHG", "CHH"}
        if not v:
            raise ValueError("At least one context must be specified")
        for ctx in v:
            if ctx not in valid_contexts:
                raise ValueError(f"Invalid context '{ctx}'. Valid contexts are: {valid_contexts}")
        return v

    @field_validator('temperature', mode='before')
    @classmethod
    def validate_temperature(cls, v):
        if v < 0.1:
            raise ValueError("Temperature must be >= 0.1")
        if v > 10.0:
            raise ValueError("Temperature must be <= 10.0")
        return v

    delta_mean_reduction: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Coarse pre-statistical gate: before running the statistical test, discard positions where |delta_mean| < delta_mean_reduction. Reduces the test set for expensive contexts (CHH). If null, no coarse gate is applied."
    )
    lambda_var: float = Field(
        default=2.0, ge=0.0, le=20.0,
        description="Variance penalty strength in effect_size = |delta_mean| * (1 - overlap) * exp(-lambda_var * (sqrt(variance1) + sqrt(variance2)))."
    )
    effect_size_use_mean_level: bool = Field(
        default=True,
        description="Multiply effect_size by max(μ₁,μ₂)/(max(μ₁,μ₂)+k) to reduce inflated scores at low methylation (e.g. CHH)."
    )
    effect_size_mean_level_use_max: bool = Field(
        default=True,
        description="If True, use max(mean1,mean2) for the mean-level factor; if False, use (mean1+mean2)/2. Formula uses max."
    )
    effect_size_mean_level_weight: str = Field(
        default="saturating",
        description="Mean-level weight: 'sqrt', 'linear', 'saturating' (μ/(μ+k)), or 'boundary' (min(1, μ/τ)); use max(μ1,μ2) so one group ≥ τ removes penalty."
    )
    effect_size_mean_level_k: float = Field(
        default=0.05, ge=0.01, le=0.5,
        description="For saturating: k in μ/(μ+k). For boundary: τ (tau), e.g. 0.1; effect_size × min(1, max(μ1,μ2)/τ) so signals below τ are down-weighted."
    )
    effect_size_mean_level_k_by_context: Optional[Dict[str, float]] = Field(
        default=None,
        description="Optional per-context k for mean-level weight (e.g. {\"CHH\": 0.20, \"CHG\": 0.10}). Use a larger k for CHH so effect_size is naturally lower there; CG uses effect_size_mean_level_k. None = same k for all contexts."
    )

    @field_validator("effect_size_mean_level_weight", mode="before")
    @classmethod
    def validate_effect_size_mean_level_weight(cls, v: str) -> str:
        if v not in ("sqrt", "linear", "saturating", "boundary"):
            raise ValueError("effect_size_mean_level_weight must be one of: sqrt, linear, saturating, boundary")
        return v

    max_tau2_for_dmp: Optional[float] = Field(
        default=None, ge=0.0,
        description="Optional heterogeneity filter. If set, drop positions where both groups exceed this between-sample variance estimate (tau2), because they are likely heterogeneous subpopulations rather than clean DMPs."
    )

    # ----------------
    # Filter funnel exploration (sweep effect_size_coverage, write CSV)
    # ----------------
    filter_funnel_explore: Optional[FilterFunnelExplore] = Field(
        default=None,
        description="Optional. Sweep effect_size_coverage over a range and write filter_funnel-{chromosome}.csv per chromosome, then aggregate to filter_funnel.csv with a leading chromosome column. Set to null to disable."
    )

    # ----------------
    # Context Weighting
    # ----------------
    use_context_weights: bool = Field(
        default=True,
        description="Enable trimmed-mean context weighting for multi-context analysis"
    )
    trimmed_percentile_low: float = Field(
        default=0.10, ge=0.0, le=0.5,
        description="Lower percentile for trimmed mean - removes bottom X% (default: 0.10 = remove bottom 10%). Low effect_size DMPs are less informative."
    )
    trimmed_percentile_high: float = Field(
        default=0.01, ge=0.0, le=0.5,
        description="Upper percentile for trimmed mean - removes top X% (default: 0.01 = remove top 1%). High effect_size DMPs are critical for classification, so only extreme outliers are removed."
    )
    context_weight_direction: str = Field(
        default="inverse",
        description="How to derive context weight from trimmed mean effect_size: 'inverse' = weight ∝ 1/mean_effect_size (down-weight contexts with inflated effect_size, e.g. CHH); 'proportional' = weight ∝ mean_effect_size (legacy)."
    )

    # ----------------
    # DMP Selection
    # ----------------
    dmp_export_mode: DmpExportMode = Field(
        default="dual",
        description="unified: single dmps-{chrom}.csv (classifier subset; may expand rows to min_dmps_for_export for mapper). "
        "dual: dmps-{chrom}-discovery.csv (broad, mapper/enricher) + dmps-{chrom}-classifier.csv (prediction panel) + metadata JSON.",
    )
    discovery_dynamic_dmp_cutoff_enabled: bool = Field(
        default=False,
        description="If True, apply the same effect_size elbow trim to the discovery export in dual mode. Default False: discovery keeps all biologically filtered DMPs sorted by importance.",
    )
    classifier_dmp_selection: ClassifierDmpSelection = Field(
        default="elbow",
        description="How to build the prediction DMP panel: elbow = effect_size distribution trim only; "
        "featurecuts_validation = choose top-k by balanced accuracy on configured validation samples (requires real validation data).",
    )
    featurecuts_exhaustive_search: bool = Field(
        default=False,
        description="When classifier_dmp_selection=featurecuts_validation: search more k candidates (slower, more thorough).",
    )
    featurecuts_max_candidates: Optional[int] = Field(
        default=50,
        ge=1,
        description="Cap on candidate k values evaluated in featurecuts mode (None = auto).",
    )
    featurecuts_max_k_cap: Optional[int] = Field(
        default=None,
        ge=1,
        description="Optional upper bound on k when running featurecuts (speed). None = use all rows after elbow prefilter.",
    )
    target_balanced_accuracy: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Optional target BA for FeatureCuts. When set with classifier_dmp_selection=featurecuts_validation, "
            "the selector chooses the minimum top-k (by effect_size rank) that reaches this BA."
        ),
    )
    min_selected_dmps: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Deprecated alias for min_core_dmps when min_core_dmps is unset. "
            "Does not widen mapper exports; use classifier_export_margin_* for annotation panels."
        ),
    )
    min_core_dmps: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Optional small guardrail on the core classifier panel (k_core) after FeatureCuts. "
            "Use only to avoid trivially tiny panels; does not apply a large fixed export floor."
        ),
    )
    fail_if_below_target: bool = Field(
        default=False,
        description=(
            "Strict FeatureCuts: fail when target BA is unmet or selected k is below min_core_dmps "
            "instead of expanding the panel into worse held-out performance."
        ),
    )
    classifier_export_margin_pct: float = Field(
        default=0.10,
        ge=0.0,
        description=(
            "Dual export: extend dmps-{chrom}-classifier-extended.csv to at least "
            "ceil(k_core * (1 + margin_pct)) for mapper/gene annotation."
        ),
    )
    classifier_export_margin_abs: int = Field(
        default=10,
        ge=0,
        description="Dual export: add this many top-ranked DMPs beyond k_core for the extended classifier CSV.",
    )
    classifier_export_max_dmps: Optional[int] = Field(
        default=200,
        ge=1,
        description="Dual export: cap k_extended per chromosome (annotation panel only; core pickle unchanged).",
    )
    min_dmps_for_export: int = Field(
        default=1000, ge=1,
        description="Unified mode: if the classifier/elbow subset is smaller than this but more biological DMPs exist, "
        "the exported dmps-{chrom}.csv is widened to this many top-importance rows while the saved classifier still uses the elbow subset. "
        "Dual mode: warn when the discovery table has fewer than this many rows."
    )
    effect_size_weight_power: float = Field(
        default=1.0, ge=0.1, le=5.0,
        description="Power applied to normalized effect_size when building classifier weights: weight_i = (effect_size_i / max)^power. Default 1.0 (no change). Use > 1 (e.g. 2.0–3.0) for large DMP sets so weak positions contribute less; higher values concentrate weight more on top positions and can improve centroid separation and class probability margins."
    )
    dynamic_dmp_cutoff_enabled: bool = Field(
        default=True,
        description="If True, dynamically analyze the effect_size distribution (elbow detection) to identify and drop the long tail of low-importance DMPs. Maximizes retained DMPs while removing weak signal."
    )
    dynamic_dmp_cutoff_relaxation: float = Field(
        default=1.0, gt=0.0,
        description="Multiplier for the dynamically calculated elbow threshold. 1.0 = exact elbow. <1.0 = relaxed (keeps more DMPs), >1.0 = stricter."
    )

    # ----------------
    # Debug / Logging
    # ----------------
    centroid_self_check_top_k: Optional[int] = Field(
        default=None, ge=1,
        description="If set, centroid self-check uses only the top K DMPs by effect_size (most biologically important). "
        "Reduces noise from low-effect loci that can make both centroids classify as class0. E.g. 5000."
    )
    debug: bool = Field(
        default=False,
        description="Enable debug output (e.g. classifier predict_proba in centroid self-check, extra validation logs)."
    )

    # ----------------
    # System Settings
    # ----------------
    significance_test: Literal["ks_ecdf", "mann_whitney"] = Field(
        default="ks_ecdf",
        description="Statistical test for DMP significance: 'ks_ecdf' (Kolmogorov-Smirnov on precise ECDF; recommended and default) or 'mann_whitney' (Mann-Whitney from bin counts; alternative)."
    )
    ecdf_grid_size: int = Field(
        default=256, ge=16, le=4096,
        description="Number of grid points for ECDF/KS and overlap integration (single grid; default 256)."
    )

    # ----------------
    # Output Options
    # ----------------
    export_sample_size_estimate: bool = Field(
        default=False,
        description="If True, add n_estimated_per_group column to DMP CSV: sample size per group needed for target_power (two-sample t-test, Cohen's d from delta_mean and combined variance)."
    )
    target_power: float = Field(
        default=0.8, ge=0.5, le=0.999,
        description="Target power (1 - type II error) for sample size estimation when export_sample_size_estimate is True (e.g. 0.8 = 80%% power)."
    )

    # -----------------
    # Validators / Utils
    # -----------------
    @field_validator('centroid1_dir', 'centroid2_dir', mode='before')
    @classmethod
    def validate_centroid_dirs(cls, v):
        if v is not None:
            path = Path(v)
            if not path.exists():
                raise ValueError(
                    f"Centroid directory does not exist: {path}. "
                    "If using a project JSON from another machine or path, run with "
                    "--output-base /path/to/local/output so centroid and detection paths are resolved under that directory."
                )
            if not path.is_dir():
                raise ValueError(f"Centroid path must be a directory: {path}")
        return path
    
    @field_validator('centroid1_path', 'centroid2_path', mode='before')
    @classmethod
    def validate_centroid_paths(cls, v):
        if v is not None:
            path = Path(v)
            if not path.exists():
                raise ValueError(f"Centroid file does not exist: {path}")
            if path.suffix != '.h5':
                raise ValueError(f"Centroid file must be .h5 format: {path}")
        return path
    
    @field_validator('chromosome', mode='before')
    @classmethod
    def validate_chromosome(cls, v):
        """Validate chromosome(s) - can be a single chromosome or a list."""
        valid_chroms = [str(i) for i in range(1, 23)] + ['X', 'Y', 'M', 'MT']
        
        # Normalize to list for internal use
        if isinstance(v, str):
            if v not in valid_chroms:
                raise ValueError(f"Chromosome must be one of: {valid_chroms}, got: {v}")
            return [v]  # Return as list for consistency
        elif isinstance(v, list):
            if not v:
                raise ValueError("Chromosome list cannot be empty")
            for chrom in v:
                if chrom not in valid_chroms:
                    raise ValueError(f"Chromosome must be one of: {valid_chroms}, got: {chrom}")
            return v
        else:
            raise ValueError(f"Chromosome must be a string or list of strings, got: {type(v)}")

    @field_validator('output_dir', mode='before')
    @classmethod
    def validate_output_dir(cls, v):
        if v is not None:
            path = Path(v)
            if not str(path):
                raise ValueError("Output directory cannot be empty")
        return v

    validation_min_coverage: int = Field(
        default=4, ge=1, le=100,
        description="Min coverage when extracting methylation from validation/centroid samples. Use a value lower than the centroid's min_coverage (e.g. 4 if centroid used 10) so validation samples contribute values at more positions and better match lower-coverage patient data."
    )

    @field_validator('trimmed_percentile_low', 'trimmed_percentile_high', mode='before')
    @classmethod
    def validate_trimmed_percentile(cls, v):
        if v < 0.0 or v > 0.5:
            raise ValueError("Trimmed percentiles must be between 0.0 and 0.5")
        return v

    @field_validator('context_weight_direction', mode='before')
    @classmethod
    def validate_context_weight_direction(cls, v: str) -> str:
        if v not in ('inverse', 'proportional'):
            raise ValueError("context_weight_direction must be 'inverse' or 'proportional'")
        return v

    @model_validator(mode='after')
    def _post_root_validate(self):
        """Post-validation: backward compatibility and additional checks."""
        # Backward compatibility for old configs using centroid1_path/centroid2_path
        if self.centroid1_path and self.centroid2_path:
            if not isinstance(self.chromosome, list) or len(self.chromosome) == 0:
                # Extract chromosome from filename (e.g., "1-CG.h5" -> "1")
                try:
                    chrom_info = get_chromosome_context_from_filename(self.centroid1_path)
                    extracted_chrom = chrom_info['chromosome']
                    if extracted_chrom != 'unknown':
                        self.chromosome = [extracted_chrom]  # Normalize to list
                except Exception:
                    if not isinstance(self.chromosome, list) or len(self.chromosome) == 0:
                        raise ValueError("Cannot infer chromosome from centroid1_path. Please specify 'chromosome' in config.")
            
            if not self.centroid1_dir:
                self.centroid1_dir = Path(self.centroid1_path).parent
            if not self.centroid2_dir:
                self.centroid2_dir = Path(self.centroid2_path).parent
            
            # Clear old paths to ensure new fields are used
            self.centroid1_path = None
            self.centroid2_path = None
        
        # Ensure chromosome is set if dirs are set but chrom is not
        if (not isinstance(self.chromosome, list) or len(self.chromosome) == 0) and self.centroid1_dir and self.contexts:
            # Try to infer from first centroid file in dir
            first_centroid_file = next(Path(self.centroid1_dir).glob(f"*-{self.contexts[0]}.h5"), None)
            if first_centroid_file:
                try:
                    chrom_info = get_chromosome_context_from_filename(first_centroid_file)
                    extracted_chrom = chrom_info['chromosome']
                    if extracted_chrom != 'unknown':
                        self.chromosome = [extracted_chrom]  # Normalize to list
                except Exception:
                    pass # Will be caught by chromosome validator if still None
        
        # Check that the sum of trimmed percentiles doesn't exceed 1.0
        if self.trimmed_percentile_low + self.trimmed_percentile_high >= 1.0:
            raise ValueError(f"Sum of trimmed_percentile_low ({self.trimmed_percentile_low}) and trimmed_percentile_high ({self.trimmed_percentile_high}) must be less than 1.0")
        
        # Ensure min_dmps_for_export is not less than 1
        if self.min_dmps_for_export < 1:
            self.min_dmps_for_export = 1
        
        return self

    # ---------------
    # Helper Methods
    # ---------------
    def effective_min_samples(self, cohort_size: int) -> int:
        """
        Effective minimum number of samples per position for a centroid with given cohort size.
        Different cohort sizes (e.g. centroid1=50, centroid2=30) yield different effective minimums.
        """
        pct = max(0.0, min(1.0, float(self.min_samples_pct)))
        n_from_pct = ceil(pct * cohort_size)
        return max(int(self.min_samples_abs), n_from_pct)
