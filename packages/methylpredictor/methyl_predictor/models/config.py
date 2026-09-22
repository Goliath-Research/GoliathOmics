"""Pydantic config for MethylPredictor."""

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


def _to_absolute_paths(paths: List[str], base_path: Optional[str]) -> List[str]:
    """Resolve each path to absolute; relative paths are resolved against base_path or cwd."""
    base = Path(base_path).resolve() if base_path else Path.cwd()
    result: List[str] = []
    for p in paths:
        if not p or not str(p).strip():
            continue
        path = Path(p.strip())
        if not path.is_absolute():
            path = base / path
        result.append(str(path.resolve()))
    return result


class PredictorConfig(BaseModel):
    """Configuration for running MethylPredictor on test sample sets."""

    model_path: Optional[str] = Field(
        default=None,
        description="Path to trained classifier .pkl file (single-file mode).",
    )
    model_dir: Optional[str] = Field(
        default=None,
        description="Path to directory containing classifier-{chrom}.pkl files (multi-chromosome mode).",
    )
    output_dir: str = Field(
        ...,
        description="Directory for validation_metrics.json and predictions CSV.",
    )
    test_control_paths: List[str] = Field(
        default_factory=list,
        description="Sample directory paths for control/class-0 test set.",
    )
    test_disease_paths: List[str] = Field(
        default_factory=list,
        description="Sample directory paths for disease/class-1 test set.",
    )
    test_blind_paths: List[str] = Field(
        default_factory=list,
        description="Unlabeled / blind test samples (no expected_class); mutually exclusive with labeled cohorts.",
    )
    test_group_paths: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="For multi-class: list of {label: str, paths: list} or {class_index: int, paths: list}. "
        "Order must match classifier class_names. Resolved to absolute paths.",
    )
    train_control_paths: List[str] = Field(
        default_factory=list,
        description="Training split: class-0 paths when using train/holdout dual evaluation (with holdout_*).",
    )
    train_disease_paths: List[str] = Field(
        default_factory=list,
        description="Training split: class-1 paths for dual evaluation.",
    )
    holdout_control_paths: List[str] = Field(
        default_factory=list,
        description="Holdout split: class-0 paths; when any holdout_* paths are set, metrics are split into training vs holdout.",
    )
    holdout_disease_paths: List[str] = Field(
        default_factory=list,
        description="Holdout split: class-1 paths for dual evaluation.",
    )
    train_group_paths: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Multiclass training split paths (same shape as test_group_paths). Used with holdout_group_paths.",
    )
    holdout_group_paths: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Multiclass holdout split paths per class.",
    )
    controls: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Nested control cohort: {label, groups[{label, sample_paths}]}. "
        "When test_control_paths is empty and diseases is set, run_prediction expands these.",
    )
    diseases: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Nested disease cohort (same shape as project diseases).",
    )
    blind: Optional[Dict[str, Any]] = Field(
        default=None,
        description='Optional blind cohort: {"groups": [{label, sample_paths}]}. Mutually exclusive with controls/diseases.',
    )
    path_remap: Optional[Dict[str, str]] = Field(
        default=None,
        description="Prefix replacement for sample paths: {\"old_prefix\": \"new_prefix\"}. Applied when paths come from config.",
    )
    samples_base_path: Optional[str] = Field(
        default=None,
        description="Base directory to resolve relative sample paths (project-level).",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug output.",
    )
    comparison_label: Optional[str] = Field(
        default=None,
        description="When running per-comparison, the comparison folder label (written to prediction_report.json).",
    )
    report_controls: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Controls block for prediction_report.json (label + groups with sample_paths), "
        "same shape as step_config.predictor.controls.",
    )
    report_diseases: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Diseases block for prediction_report.json.",
    )
    report_blind: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Blind cohort block for prediction_report.json (same shape as predictor.blind).",
    )
    sample_lineage: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Ordered rows parallel to classification input: absolute_path, side, group_label.",
    )
    cohort_hierarchy: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional tree metadata from project (disease families / stages); used for hierarchy summaries in reports.",
    )
    classifier_step_snapshot: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional copy of step_config.classifier fields merged into ClassifierConfig during "
        "run_prediction (temperature, use_isotonic_calibration, weight_method, etc.).",
    )
    panel: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional hierarchical panel readout for OvR ECDF bundles (pairwise max-contrast). "
        "Set under step_config.predictor.panel in the project JSON. See methyl_classifier.core.panel_fusion.",
    )
    decision_enabled: bool = Field(
        default=True,
        description="If true (binary only), derive final_decision in predictions.csv using "
        "decision_min_margin and decision_min_confidence thresholds.",
    )
    decision_min_margin: float = Field(
        default=0.20,
        description="Binary decision policy: minimum |prob_class1 - prob_class0| required to avoid indeterminate.",
    )
    decision_min_confidence: float = Field(
        default=0.65,
        description="Binary decision policy: minimum max(prob_class0, prob_class1) required to avoid indeterminate.",
    )

    @model_validator(mode="after")
    def ensure_absolute_test_paths(self) -> "PredictorConfig":
        """Normalize test_control_paths, test_disease_paths, and test_group_paths to absolute paths."""
        has_labeled = bool(
            self.test_control_paths
            or self.test_disease_paths
            or self.test_group_paths
            or self.train_control_paths
            or self.train_disease_paths
            or self.holdout_control_paths
            or self.holdout_disease_paths
            or self.train_group_paths
            or self.holdout_group_paths
        )
        has_blind = bool(self.test_blind_paths)
        if has_labeled and has_blind:
            raise ValueError(
                "PredictorConfig: cannot set test_blind_paths together with labeled "
                "test_control_paths/test_disease_paths, test_group_paths, or train/holdout path lists."
            )

        def _nested_groups_nonempty(d: Optional[Dict[str, Any]]) -> bool:
            if not isinstance(d, dict):
                return False
            g = d.get("groups")
            return isinstance(g, list) and len(g) > 0

        if _nested_groups_nonempty(self.blind) and (
            _nested_groups_nonempty(self.controls) or _nested_groups_nonempty(self.diseases)
        ):
            raise ValueError(
                "PredictorConfig: nested 'blind.groups' cannot be combined with nested "
                "'controls' / 'diseases' in the same config."
            )
        self.test_control_paths = _to_absolute_paths(
            self.test_control_paths, self.samples_base_path
        )
        self.test_disease_paths = _to_absolute_paths(
            self.test_disease_paths, self.samples_base_path
        )
        self.test_blind_paths = _to_absolute_paths(
            self.test_blind_paths, self.samples_base_path
        )
        self.train_control_paths = _to_absolute_paths(
            self.train_control_paths, self.samples_base_path
        )
        self.train_disease_paths = _to_absolute_paths(
            self.train_disease_paths, self.samples_base_path
        )
        self.holdout_control_paths = _to_absolute_paths(
            self.holdout_control_paths, self.samples_base_path
        )
        self.holdout_disease_paths = _to_absolute_paths(
            self.holdout_disease_paths, self.samples_base_path
        )
        if self.test_group_paths:
            base = self.samples_base_path
            for entry in self.test_group_paths:
                paths = entry.get("paths")
                if isinstance(paths, list):
                    entry["paths"] = _to_absolute_paths(paths, base)
        if self.train_group_paths:
            base = self.samples_base_path
            for entry in self.train_group_paths:
                paths = entry.get("paths")
                if isinstance(paths, list):
                    entry["paths"] = _to_absolute_paths(paths, base)
        if self.holdout_group_paths:
            base = self.samples_base_path
            for entry in self.holdout_group_paths:
                paths = entry.get("paths")
                if isinstance(paths, list):
                    entry["paths"] = _to_absolute_paths(paths, base)
        if self.sample_lineage:
            base = self.samples_base_path
            for row in self.sample_lineage:
                p = row.get("absolute_path")
                if p:
                    row["absolute_path"] = _to_absolute_paths([p], base)[0]
        return self

    class Config:
        """Pydantic config."""
        protected_namespaces = ()
