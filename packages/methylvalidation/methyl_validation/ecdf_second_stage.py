"""
Optional ECDF second-stage scorer.

Stacks first-stage ECDF class probabilities with optional observed-hybrid features
and/or covariates (``fit_covariates``) into a logistic refiner.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

import joblib
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score

from methyl_utils import load_project

from .classification_metrics import compute_validation_metrics
from .covariate_preprocessor import (
    CompositionGroupSpec,
    exclusion_report,
    fit_covariates,
    normalize_composition_groups,
    resolve_covariate_sample_ids,
    transform_covariates,
)
from .eval_split_resolver import resolve_eval_paths_and_labels
from .model_datasets import (
    resolve_model_bundle_dir,
    write_model_datasets,
)
from .model_bundle import build_model_feature_bundle, load_bundle_dmp_index
from .observed_feature_builder import (
    apply_feature_fill_values,
    build_observed_hybrid_feature_table,
    derive_observed_hybrid_anchors,
    fit_feature_fill_values,
    select_training_feature_matrix,
    verify_feature_schema,
)


def _composition_groups_as_dicts(groups: Any) -> Optional[List[Dict[str, Any]]]:
    """Normalize configured composition groups (pydantic models or dicts) to dicts."""
    if not groups:
        return None
    out: List[Dict[str, Any]] = []
    for group in groups:
        if hasattr(group, "model_dump"):
            out.append(group.model_dump())
        elif isinstance(group, dict):
            out.append(dict(group))
        else:
            raise TypeError(f"Unsupported composition group entry: {type(group)!r}")
    return out or None


class EcdfSecondStageParams(BaseModel):
    """Typed knobs for the ECDF second-stage stacker (no invented science defaults)."""

    model_config = ConfigDict(extra="forbid")

    include_observed_hybrid: bool = Field(
        default=False,
        description=(
            "When true, include observed-hybrid methylation features restricted to "
            "the freeze-time stable/frozen gene panel. Driven by "
            "ecdf_second_stage_include_observed_hybrid once the stacker already runs "
            "(not by ecdf_second_stage_enabled alone)."
        ),
    )
    max_dmps: Optional[int] = Field(default=None, ge=0)
    quantiles: Optional[List[float]] = Field(default=None)
    min_coverage: int = Field(default=1, ge=1)
    include_dmp_features: bool = Field(default=True)
    include_chromosome_features: bool = Field(default=True)
    include_dmr_features: bool = Field(default=True)
    include_gene_features: bool = Field(default=True)
    dmr_window_bp: int = Field(default=100000, ge=1)
    max_dmr_features: int = Field(default=32, ge=0)
    max_gene_features: int = Field(default=32, ge=0)
    hist_eps: float = Field(default=1e-6)
    hist_alpha: float = Field(default=0.5)
    hist_evidence_clip_cap: float = Field(default=5.0)
    hist_tail_agreement_threshold: float = Field(default=0.10)
    chromosome_hypo_beta_threshold: Optional[float] = Field(default=None)
    chromosome_intermediate_beta_lo: Optional[float] = Field(default=None)
    chromosome_intermediate_beta_hi: Optional[float] = Field(default=None)
    chromosome_distance_metrics: Optional[List[str]] = Field(default=None)
    chromosome_list: Optional[List[str]] = Field(default=None)
    feature_family_set: str = Field(default="dmp_scored")

    covariates_path: Optional[Union[str, List[str]]] = Field(default=None)
    covariate_id_column: str = Field(default="sample_id")
    covariate_numeric_columns: Optional[List[str]] = Field(default=None)
    covariate_ordinal_columns: Optional[List[str]] = Field(default=None)
    covariate_ordinal_maps: Optional[Dict[str, Dict[str, float]]] = Field(default=None)
    covariate_ordinal_unknown_value: float = Field(default=0.0)
    covariate_categorical_columns: Optional[List[str]] = Field(default=None)
    covariate_missing_numeric_strategy: str = Field(default="mean")
    covariate_standardize_numeric: bool = Field(default=True)
    covariates_strict_join: bool = Field(default=False)
    covariates_missing_samples: Optional[Literal["fail", "drop"]] = Field(default=None)
    probability_transform: Optional[str] = Field(default=None)
    probability_epsilon: Optional[float] = Field(
        default=None,
        gt=0.0,
        lt=0.5,
        description=(
            "ALR pseudocount for multiclass (K>2) first-stage class probabilities. "
            "Not required for binary (K=2), which stacks closed prob_class1. Set via "
            "ecdf_second_stage_probability_epsilon in profile/site actionConfig "
            "(no code default)."
        ),
    )
    composition_transform: Optional[str] = Field(default=None)
    composition_columns: Optional[List[str]] = Field(default=None)
    composition_reference: Optional[str] = Field(default=None)
    composition_pseudocount: Optional[float] = Field(default=None, gt=0.0)
    composition_groups: Optional[List[Dict[str, Any]]] = Field(default=None)

    def resolved_composition_groups(self) -> List[CompositionGroupSpec]:
        """Typed composition groups plus the legacy single-group keys."""
        return normalize_composition_groups(
            self.composition_groups,
            legacy_transform=self.composition_transform,
            legacy_columns=self.composition_columns,
            legacy_reference=self.composition_reference,
            legacy_pseudocount=self.composition_pseudocount,
        )

    def has_covariates(self) -> bool:
        if self.covariates_path is None:
            return False
        if isinstance(self.covariates_path, (list, tuple)):
            return any(str(p).strip() for p in self.covariates_path)
        return bool(str(self.covariates_path).strip())

    def validate_stack_components(self) -> None:
        if not self.include_observed_hybrid and not self.has_covariates():
            raise ValueError(
                "ECDF second-stage requires include_observed_hybrid and/or covariates_path "
                "(probabilities alone are not a useful stacker)."
            )

    @classmethod
    def from_monte_carlo_config(
        cls,
        config: Any,
        *,
        feature_family_set: Optional[str] = None,
    ) -> "EcdfSecondStageParams":
        """Build params from synced MonteCarloConfig / backend profile fields."""
        if config is None:
            raise ValueError("MonteCarloConfig is required to build EcdfSecondStageParams")
        return cls(
            include_observed_hybrid=bool(
                getattr(config, "ecdf_second_stage_include_observed_hybrid", False)
            ),
            max_dmps=getattr(config, "tabular_max_dmps", None),
            quantiles=getattr(config, "observed_feature_quantiles", None),
            min_coverage=int(getattr(config, "observed_feature_min_coverage", 1) or 1),
            include_dmp_features=bool(getattr(config, "observed_feature_include_dmp", True)),
            include_chromosome_features=bool(
                getattr(config, "observed_feature_include_chromosome", True)
            ),
            include_dmr_features=bool(getattr(config, "observed_feature_include_dmr", True)),
            include_gene_features=bool(getattr(config, "observed_feature_include_gene", True)),
            dmr_window_bp=int(getattr(config, "observed_feature_dmr_window_bp", 100000) or 100000),
            max_dmr_features=int(getattr(config, "observed_feature_max_dmrs", 32) or 0),
            max_gene_features=int(getattr(config, "observed_feature_max_genes", 32) or 0),
            hist_eps=float(getattr(config, "observed_hist_eps", 1e-6) or 1e-6),
            hist_alpha=float(getattr(config, "observed_hist_alpha", 0.5) or 0.5),
            hist_evidence_clip_cap=float(
                getattr(config, "observed_hist_evidence_clip_cap", 5.0) or 5.0
            ),
            hist_tail_agreement_threshold=float(
                getattr(config, "observed_hist_tail_agreement_threshold", 0.10) or 0.10
            ),
            chromosome_hypo_beta_threshold=getattr(config, "chromosome_hypo_beta_threshold", None),
            chromosome_intermediate_beta_lo=getattr(
                config, "chromosome_intermediate_beta_lo", None
            ),
            chromosome_intermediate_beta_hi=getattr(
                config, "chromosome_intermediate_beta_hi", None
            ),
            chromosome_distance_metrics=getattr(config, "chromosome_distance_metrics", None),
            chromosome_list=getattr(config, "chromosome_list", None),
            feature_family_set=str(
                feature_family_set
                if feature_family_set is not None
                else getattr(config, "feature_family_set", "dmp_scored")
            ),
            covariates_path=getattr(config, "covariates_path", None),
            covariate_id_column=str(getattr(config, "covariate_id_column", "sample_id") or "sample_id"),
            covariate_numeric_columns=getattr(config, "covariate_numeric_columns", None),
            covariate_ordinal_columns=getattr(config, "covariate_ordinal_columns", None),
            covariate_ordinal_maps=getattr(config, "covariate_ordinal_maps", None),
            covariate_ordinal_unknown_value=float(
                getattr(config, "covariate_ordinal_unknown_value", 0.0) or 0.0
            ),
            covariate_categorical_columns=getattr(config, "covariate_categorical_columns", None),
            covariate_missing_numeric_strategy=str(
                getattr(config, "covariate_missing_numeric_strategy", "mean") or "mean"
            ),
            covariate_standardize_numeric=bool(
                getattr(config, "covariate_standardize_numeric", True)
            ),
            covariates_strict_join=bool(getattr(config, "covariates_strict_join", False)),
            covariates_missing_samples=getattr(config, "covariates_missing_samples", None),
            probability_transform=getattr(
                config, "ecdf_second_stage_probability_transform", None
            ),
            probability_epsilon=getattr(
                config, "ecdf_second_stage_probability_epsilon", None
            ),
            composition_transform=getattr(
                config, "covariate_composition_transform", None
            ),
            composition_columns=getattr(
                config, "covariate_composition_columns", None
            ),
            composition_reference=getattr(
                config, "covariate_composition_reference", None
            ),
            composition_pseudocount=getattr(
                config, "covariate_composition_pseudocount", None
            ),
            composition_groups=_composition_groups_as_dicts(
                getattr(config, "covariate_composition_groups", None)
            ),
        )


def ecdf_second_stage_should_run(config: Any) -> bool:
    """True when the second-stage stacker is configured to run.

    The sole gate is ``ecdf_second_stage_enabled``. Setting ``covariates_path``
    without the flag is coerced to enabled=true during EcdfBackendParams
    validation, so resolved MonteCarloConfig cannot claim the stacker is off
    while covariates are configured. ``ecdf_second_stage_include_observed_hybrid``
    only controls whether hybrid features are included once the stacker already
    runs; it does not start the stacker by itself.
    """
    if config is None:
        return False
    return bool(getattr(config, "ecdf_second_stage_enabled", False))


def _resolve_eval_paths_and_labels(
    project_json: str | Path,
    *,
    evaluation_partition: Optional[str] = None,
) -> Tuple[List[str], Optional[np.ndarray]]:
    """Delegate path resolution to the shared model-MC partition resolver."""
    project = load_project(project_json)
    class_names = [str(label) for label, _paths in project.get_resolved_groups()]
    if len(class_names) < 2:
        class_names = ["control", "disease"]
    return resolve_eval_paths_and_labels(
        project_json,
        class_names,
        project_loader=load_project,
        evaluation_partition=evaluation_partition,
    )


def _ensure_bundle_h5(project_json: Path, bundle_dir: Path) -> Path:
    bundle_h5 = bundle_dir / "model_feature_bundle.h5"
    if bundle_h5.is_file():
        return bundle_h5
    bundle_dir.mkdir(parents=True, exist_ok=True)
    build_model_feature_bundle(
        project_json=project_json,
        output_dir=bundle_dir,
        weight_column="weight",
        extra_metadata={"model_backend": "ecdf_second_stage"},
    )
    if not bundle_h5.is_file():
        raise FileNotFoundError(f"Model bundle not found at {bundle_h5}")
    return bundle_h5


def _resolve_class_centroid_dirs(project_json: Path) -> Dict[str, str]:
    label_to_dir: Dict[str, str] = {}
    try:
        project = load_project(project_json)
        resolved = project.get_resolved_groups()
        derived = project.get_derived_paths()
        centroid_dirs = list(getattr(derived, "centroid_dirs", []) or [])
        for idx, (label, _paths) in enumerate(resolved):
            if idx < len(centroid_dirs):
                label_to_dir[str(label)] = str(centroid_dirs[idx])
    except Exception:
        return {}
    return {k: v for k, v in label_to_dir.items() if str(v).strip()}


def _sample_paths_from_predictions(
    predictions_df: pd.DataFrame,
    project_json: Path,
) -> List[str]:
    """
    Build per-row sample paths directly from predictions rows.

    This avoids train/holdout/test group-resolution ambiguity and guarantees row
    parity between ECDF probabilities and observed-hybrid / covariate features.
    """
    with open(project_json, encoding="utf-8") as f:
        project_data = json.load(f)
    samples_base = str(project_data.get("samples_base_path") or "").strip()
    samples_base_path = Path(samples_base) if samples_base else None

    resolved_eval_paths: List[str] = []
    for part in ("train", "test"):
        try:
            paths, _ = _resolve_eval_paths_and_labels(
                project_json, evaluation_partition=part
            )
            resolved_eval_paths.extend(list(paths or []))
        except Exception:
            continue
    if not resolved_eval_paths:
        try:
            paths, _ = _resolve_eval_paths_and_labels(project_json)
            resolved_eval_paths = list(paths or [])
        except Exception:
            resolved_eval_paths = []
    eval_by_name: Dict[str, str] = {Path(str(p)).name: str(p) for p in resolved_eval_paths}
    eval_by_stem: Dict[str, str] = {Path(str(p)).stem: str(p) for p in resolved_eval_paths}

    out: List[str] = []
    for row in predictions_df.itertuples(index=False):
        sample = str(getattr(row, "sample", "") or "").strip()
        sample_path = str(getattr(row, "sample_path", "") or "").strip()
        chosen: Optional[str] = None

        if sample_path:
            p = Path(sample_path)
            if p.is_absolute():
                chosen = str(p)
            elif samples_base_path is not None:
                chosen = str((samples_base_path / sample_path).resolve())
            else:
                chosen = str(p)

        if not chosen and sample:
            if sample in eval_by_name:
                chosen = eval_by_name[sample]
            elif sample in eval_by_stem:
                chosen = eval_by_stem[sample]
            elif samples_base_path is not None:
                chosen = str((samples_base_path / sample).resolve())
            else:
                chosen = sample

        if not chosen:
            raise ValueError(
                "Encountered prediction row without sample identifier for second-stage scorer."
            )
        out.append(chosen)
    return out


def _prob_columns(df: pd.DataFrame) -> List[str]:
    cols = [c for c in df.columns if str(c).startswith("prob_class")]
    if "prob_class0" in cols and "prob_class1" in cols:
        # Stable binary order first, then any extra multiclass heads.
        ordered = ["prob_class0", "prob_class1"] + [
            c for c in sorted(cols) if c not in ("prob_class0", "prob_class1")
        ]
        return ordered
    if cols:
        return sorted(cols)
    raise ValueError(
        "Second-stage scorer requires ECDF probability columns (prob_class0/prob_class1 or prob_class*)."
    )


def _probability_design(
    predictions: pd.DataFrame,
    *,
    transform: Optional[str],
    epsilon: Optional[float],
) -> Tuple[np.ndarray, List[str]]:
    """Encode first-stage class probabilities for the second-stage stacker.

    ECDF class probabilities sum to 1. Encoding:

    - binary (K=2): closed non-reference probability ``prob_class1`` (reference
      ``prob_class0``); no ALR and no epsilon required
    - multiclass (K>2): ALR vs ``prob_class0``; requires
      ``ecdf_second_stage_probability_epsilon``

    Legacy ``logit_class1`` / ``None`` both mean this default simplex encode.
    """
    from methyl_validation.covariate_preprocessor import _simplex_encode

    if transform is not None and str(transform).strip().lower() != "logit_class1":
        raise ValueError(f"Unsupported ECDF probability transform: {transform!r}")
    probability_columns = _prob_columns(predictions)
    values = predictions[probability_columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=np.float64)).all():
        raise ValueError("ECDF probabilities contain non-finite values.")
    row_sums = values.to_numpy(dtype=np.float64).sum(axis=1)
    if not np.allclose(row_sums, 1.0, atol=1e-6):
        raise ValueError("ECDF class probabilities do not sum to one.")
    reference = probability_columns[0]
    if len(probability_columns) == 2:
        # Prefer readable probability name for binary ECDF (not p_prob_class1).
        closed = values.to_numpy(dtype=np.float64)
        closed = closed / closed.sum(axis=1, keepdims=True)
        non_ref = probability_columns[1]
        name = non_ref  # typically prob_class1
        encoded = pd.DataFrame(
            {name: closed[:, 1].astype(np.float32)},
            index=values.index,
        )
        encoded_names = [name]
    else:
        if epsilon is None:
            raise ValueError(
                "ecdf_second_stage_probability_epsilon is required for ALR-encoding "
                "multiclass probabilities (set in profile/site actionConfig; no code default)."
            )
        pseudocount = float(epsilon)
        if pseudocount <= 0.0:
            raise ValueError("ecdf_second_stage_probability_epsilon must be > 0.")
        encoded, encoded_names = _simplex_encode(
            values,
            probability_columns,
            reference,
            pseudocount,
        )
    for name in encoded_names:
        predictions[name] = encoded[name].to_numpy(dtype=np.float32)
    return encoded.to_numpy(dtype=np.float32), encoded_names


def _composition_alr_export_names(preprocessor: Any) -> set[str]:
    """Canonical composition feature names from frozen groups (binary p_* or ALR)."""
    from methyl_validation.covariate_preprocessor import _binary_probability_name

    names: set[str] = set()
    for group in getattr(preprocessor, "composition_groups", []) or []:
        columns = [str(c) for c in (group.get("columns") or [])]
        if not columns:
            continue
        reference = str(group.get("reference") or columns[-1])
        if len(columns) == 2:
            non_ref = next(c for c in columns if c != reference)
            names.add(_binary_probability_name(non_ref))
        else:
            names.update(
                f"alr_{column}_vs_{reference}"
                for column in columns
                if column != reference
            )
    return names


def _second_stage_dataset_frame(
    *,
    predictions: pd.DataFrame,
    probability_columns: List[str],
    sample_ids: List[str],
    labels: np.ndarray,
    valid_rows: np.ndarray,
    transformed_covariates: Optional[np.ndarray],
    preprocessor: Any,
    class_names: Optional[Sequence[str]] = None,
) -> pd.DataFrame:
    """Build the persisted matrix actually supplied to the covariate stacker."""
    if len(predictions) != len(sample_ids) or len(predictions) != len(labels):
        raise ValueError("Second-stage dataset row counts are inconsistent.")

    y = labels.astype(int)
    names = [str(x) for x in (class_names or ["class0", "class1"])]
    class_labels = [
        names[int(i)] if 0 <= int(i) < len(names) else str(int(i)) for i in y
    ]
    data: Dict[str, Any] = {
        "sample_id": sample_ids,
        "class_index": y,
        "class_label": class_labels,
    }
    for column in probability_columns:
        data[column] = pd.to_numeric(predictions[column], errors="coerce").to_numpy(
            dtype=np.float32
        )

    if transformed_covariates is not None:
        covariates = np.asarray(transformed_covariates, dtype=np.float32)
        if covariates.shape[0] != len(predictions):
            raise ValueError("Second-stage covariate and prediction row counts differ.")
        output_columns = list(getattr(preprocessor, "output_columns", []) or [])
        if len(output_columns) != covariates.shape[1]:
            raise ValueError(
                "Second-stage covariate schema mismatch: "
                f"names={len(output_columns)} matrix={covariates.shape[1]}"
            )
        numeric = set(getattr(preprocessor, "numeric_columns", []) or [])
        ordinal = set(getattr(preprocessor, "ordinal_columns", []) or [])
        no_standardize = set(
            getattr(preprocessor, "composition_no_standardize_columns", []) or []
        )
        composition_feats = _composition_alr_export_names(preprocessor)
        standardized = bool(getattr(preprocessor, "standardize_numeric", False))
        for index, name in enumerate(output_columns):
            # Keep composition names canonical (p_* or alr_*_vs_*) so they
            # match tabular_sklearn / covariate-preprocessor output_columns.
            if name in composition_feats:
                export_name = name
            else:
                is_std = (
                    standardized
                    and name in (numeric | ordinal)
                    and name not in no_standardize
                )
                prefix = "standardized_" if is_std else "transformed_"
                export_name = f"{prefix}{name}"
            data[export_name] = np.asarray(covariates[:, index], dtype=np.float32)

    return pd.DataFrame(data).loc[np.asarray(valid_rows, dtype=bool)].reset_index(drop=True)


def _write_second_stage_datasets(
    *,
    project_json: Path,
    train_predictions: pd.DataFrame,
    train_probability_columns: List[str],
    train_sample_ids: List[str],
    train_labels: np.ndarray,
    train_valid_rows: np.ndarray,
    train_covariates: Optional[np.ndarray],
    preprocessor: Any,
    test_predictions: Optional[pd.DataFrame],
    test_probability_columns: Optional[List[str]],
    test_sample_ids: Optional[List[str]],
    test_labels: Optional[np.ndarray],
    test_valid_rows: Optional[np.ndarray],
    test_covariates: Optional[np.ndarray],
    class_names: Optional[Sequence[str]] = None,
    bundle_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    output_dir = resolve_model_bundle_dir(bundle_dir, project_json=project_json)
    output_dir.mkdir(parents=True, exist_ok=True)
    names = [str(x) for x in (class_names or ["class0", "class1"])]

    train_dataset = _second_stage_dataset_frame(
        predictions=train_predictions,
        probability_columns=train_probability_columns,
        sample_ids=train_sample_ids,
        labels=train_labels,
        valid_rows=train_valid_rows,
        transformed_covariates=train_covariates,
        preprocessor=preprocessor,
        class_names=names,
    )

    test_dataset: Optional[pd.DataFrame] = None
    if (
        test_predictions is not None
        and test_probability_columns is not None
        and test_sample_ids is not None
        and test_labels is not None
        and test_valid_rows is not None
    ):
        test_dataset = _second_stage_dataset_frame(
            predictions=test_predictions,
            probability_columns=test_probability_columns,
            sample_ids=test_sample_ids,
            labels=test_labels,
            valid_rows=test_valid_rows,
            transformed_covariates=test_covariates,
            preprocessor=preprocessor,
            class_names=names,
        )

    covariates_exported = train_covariates is not None
    artifacts = write_model_datasets(
        output_dir,
        backend="ecdf",
        train_frame=train_dataset,
        test_frame=test_dataset,
        extra_manifest={
            "covariates_are_training_fitted_transforms": covariates_exported,
        },
    )
    overlap = list(artifacts.get("overlapping_sample_ids") or [])
    if overlap:
        raise ValueError(
            "Second-stage train/test datasets overlap; refusing model evaluation. "
            f"First overlap(s): {overlap[:10]}"
        )

    return {
        "train_dataset": artifacts["train_dataset"],
        "test_dataset": artifacts.get("test_dataset"),
        "dataset_manifest_json": artifacts["dataset_manifest_json"],
        "train_test_overlap_count": artifacts.get("train_test_overlap_count"),
        "covariates_exported": covariates_exported,
    }


def _resolve_second_stage_frozen_gene_panel(
    *,
    project_json: Path,
    bundle_dir: Path,
    bundle_h5: Path,
) -> pd.DataFrame:
    """Load freeze-time stable gene panel; required when second-stage hybrid is on."""
    from .model_bundle import load_bundle_frozen_gene_panel

    panel = load_bundle_frozen_gene_panel(bundle_h5, project_json=project_json)
    if panel is not None and not panel.empty:
        return panel
    for candidate in (
        bundle_dir / "frozen_genes_production.csv",
        bundle_dir / "stable_genes_from_stability.csv",
        project_json.parent / "model_bundle" / "frozen_genes_production.csv",
        project_json.parent / "model_bundle" / "stable_genes_from_stability.csv",
        project_json.parent.parent.parent / "production" / "model_bundle" / "frozen_genes_production.csv",
        project_json.parent.parent.parent / "production" / "model_bundle" / "stable_genes_from_stability.csv",
    ):
        if not candidate.is_file():
            continue
        try:
            df = pd.read_csv(candidate)
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            continue
        if df.empty or "gene_name" not in df.columns:
            continue
        out = df.copy()
        if "comparison_label" not in out.columns:
            out["comparison_label"] = "default"
        if "gene_support_n" not in out.columns:
            out["gene_support_n"] = 0
        if "gene_importance" not in out.columns:
            out["gene_importance"] = 0.0
        out["gene_name"] = out["gene_name"].astype(str)
        return out[["comparison_label", "gene_name", "gene_support_n", "gene_importance"]]
    raise ValueError(
        "ECDF second-stage observed-hybrid requires a freeze-time stable/frozen gene "
        "panel (frozen_genes_production.csv or stable_genes_from_stability.csv) under "
        f"model_bundle. Searched beside {bundle_dir}."
    )


def _filter_dmp_df_to_frozen_genes(dmp_df: pd.DataFrame, frozen_panel: pd.DataFrame) -> pd.DataFrame:
    """Keep only loci whose gene_name is in the frozen/stable panel."""
    allowed = {
        str(g).strip()
        for g in frozen_panel.get("gene_name", pd.Series(dtype=object)).tolist()
        if str(g).strip() and str(g).strip().lower() not in {"", "unknown", "nan", "none"}
    }
    if not allowed:
        raise ValueError("Frozen/stable gene panel has no usable gene_name values.")
    if "gene_name" not in dmp_df.columns:
        raise ValueError(
            "Bundle DMP index lacks gene_name; cannot restrict second-stage hybrid "
            "features to the frozen/stable gene panel."
        )
    gene_series = dmp_df["gene_name"].astype(str).str.strip()
    filtered = dmp_df[gene_series.isin(allowed)].copy()
    if filtered.empty:
        raise ValueError(
            "No bundle loci remain after restricting to the frozen/stable gene panel "
            f"({len(allowed)} genes)."
        )
    return filtered


def train_and_apply_ecdf_second_stage(
    *,
    project_json: str | Path,
    predictor_output_dir: str | Path,
    classifier_output_dir: str | Path,
    params: EcdfSecondStageParams,
) -> Dict[str, Any]:
    params.validate_stack_components()
    project_json = Path(project_json).resolve()
    predictor_output_dir = Path(predictor_output_dir).resolve()
    classifier_output_dir = Path(classifier_output_dir).resolve()
    classifier_output_dir.mkdir(parents=True, exist_ok=True)

    train_pred_csv = predictor_output_dir / "train_predictions.csv"
    test_pred_csv = predictor_output_dir / "test_predictions.csv"
    if not train_pred_csv.is_file():
        train_pred_csv = predictor_output_dir / "predictions.csv"
    if not train_pred_csv.is_file():
        raise FileNotFoundError(f"Missing train_predictions.csv under {predictor_output_dir}")
    df = pd.read_csv(train_pred_csv)
    if "expected_class" not in df.columns:
        raise ValueError("Second-stage scorer requires expected_class column in train_predictions.csv.")
    if "sample" not in df.columns:
        raise ValueError("Second-stage scorer requires sample column in train_predictions.csv.")

    sample_paths = _sample_paths_from_predictions(df, project_json)
    if not sample_paths:
        raise ValueError("No sample paths were derived from predictions.csv for ECDF second-stage scorer.")
    sample_ids = [Path(str(p)).name for p in sample_paths]
    dropped_train_ids: List[str] = []
    if params.has_covariates():
        kept_ids, dropped_train_ids = resolve_covariate_sample_ids(
            sample_ids,
            params.covariates_path,
            params.covariate_id_column,
            missing_samples=params.covariates_missing_samples,
            strict_join=params.covariates_strict_join,
        )
        if dropped_train_ids:
            keep = set(kept_ids)
            mask = [sid in keep for sid in sample_ids]
            df = df.loc[mask].reset_index(drop=True)
            sample_paths = [path for path, keep_row in zip(sample_paths, mask) if keep_row]
            sample_ids = list(kept_ids)

    X_prob, prob_cols = _probability_design(
        df,
        transform=params.probability_transform,
        epsilon=params.probability_epsilon,
    )

    blocks: List[np.ndarray] = [X_prob]
    block_names: List[str] = list(prob_cols)
    meta_extra: Dict[str, Any] = {
        "prob_feature_names": list(prob_cols),
        "n_prob_features": int(X_prob.shape[1]),
        "include_observed_hybrid": bool(params.include_observed_hybrid),
        "include_covariates": bool(params.has_covariates()),
    }

    bundle_h5: Optional[Path] = None
    feat = None
    anchors = None
    fill_values = None
    frozen_gene_panel_df: Optional[pd.DataFrame] = None
    hybrid_dmp_df: Optional[pd.DataFrame] = None
    if params.include_observed_hybrid:
        bundle_dir = project_json.parent / "model_bundle"
        bundle_h5 = _ensure_bundle_h5(project_json, bundle_dir)
        frozen_gene_panel_df = _resolve_second_stage_frozen_gene_panel(
            project_json=project_json,
            bundle_dir=bundle_dir,
            bundle_h5=bundle_h5,
        )
        dmp_df = _filter_dmp_df_to_frozen_genes(
            load_bundle_dmp_index(bundle_h5),
            frozen_gene_panel_df,
        )
        hybrid_dmp_df = dmp_df
        max_dmps_norm = (
            int(params.max_dmps) if (params.max_dmps is not None and int(params.max_dmps) > 0) else 0
        )
        if max_dmps_norm and len(dmp_df) > max_dmps_norm:
            dmp_df = dmp_df.sort_values(["effect_size"], ascending=[False]).head(max_dmps_norm).copy()
            hybrid_dmp_df = dmp_df

        y_for_anchor = pd.to_numeric(df["expected_class"], errors="coerce").fillna(0).astype(int).to_numpy()
        y_for_anchor = np.where(y_for_anchor > 0, 1, 0).astype(np.int32)
        centroid_dirs = _resolve_class_centroid_dirs(project_json)
        healthy_label = "healthy"
        cancer_labels = ["cancer"]
        if centroid_dirs:
            labels = list(centroid_dirs.keys())
            if labels:
                healthy_label = labels[0]
                if len(labels) > 1:
                    cancer_labels = [labels[1]]

        anchors = derive_observed_hybrid_anchors(
            sample_paths=sample_paths,
            sample_class_indices=y_for_anchor.tolist(),
            class_names=[healthy_label] + cancer_labels,
            dmp_df=dmp_df,
            min_coverage=int(max(1, params.min_coverage)),
        )
        feat = build_observed_hybrid_feature_table(
            sample_paths,
            dmp_df,
            quantiles=params.quantiles,
            min_coverage=int(max(1, params.min_coverage)),
            include_dmp_features=bool(params.include_dmp_features),
            include_chromosome_features=bool(params.include_chromosome_features),
            include_dmr_features=bool(params.include_dmr_features),
            include_gene_features=bool(params.include_gene_features),
            dmr_window_bp=int(max(1, params.dmr_window_bp)),
            max_dmr_features=int(max(0, params.max_dmr_features)),
            max_gene_features=int(max(0, params.max_gene_features)),
            healthy_reference_vector=anchors.healthy_reference_vector,
            cancer_reference_vector=anchors.cancer_reference_vector,
            per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
            healthy_class_label=anchors.healthy_class_label,
            cancer_class_labels=anchors.cancer_class_labels,
            all_class_labels=[healthy_label] + cancer_labels,
            anchor_strategy=anchors.anchor_strategy,
            expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
            centroid_dir_by_class_label=centroid_dirs,
            hist_eps=float(params.hist_eps),
            hist_alpha=float(params.hist_alpha),
            hist_evidence_clip_cap=float(params.hist_evidence_clip_cap),
            hist_tail_agreement_threshold=float(params.hist_tail_agreement_threshold),
            feature_family_set=str(params.feature_family_set),
            frozen_gene_panel_df=frozen_gene_panel_df,
            chromosome_hypo_beta_threshold=params.chromosome_hypo_beta_threshold,
            chromosome_intermediate_beta_lo=params.chromosome_intermediate_beta_lo,
            chromosome_intermediate_beta_hi=params.chromosome_intermediate_beta_hi,
            chromosome_distance_metrics=params.chromosome_distance_metrics,
            chromosome_list=params.chromosome_list,
        )
        X_obs_full = np.asarray(feat.X, dtype=np.float32)
        fill_values = fit_feature_fill_values(X_obs_full)
        X_obs_full = apply_feature_fill_values(X_obs_full, fill_values)
        X_obs = select_training_feature_matrix(
            X_obs_full,
            feat.feature_names,
            feat.training_feature_names,
        )
        blocks.append(X_obs)
        block_names.extend(list(feat.training_feature_names))
        verify_feature_schema(
            feat.feature_names,
            list(feat.feature_names),
            context="ecdf second-stage train/apply",
        )
        meta_extra.update(
            {
                "bundle_h5": str(bundle_h5),
                "n_observed_features": int(X_obs.shape[1]),
                "observed_feature_names": list(feat.feature_names),
                "training_feature_names": list(feat.training_feature_names),
                "quality_feature_names": list(feat.quality_feature_names),
                "observed_feature_report": dict(feat.report),
                "observed_feature_fill_values": [float(v) for v in fill_values.tolist()],
                "observed_healthy_reference_vector": [
                    float(v) for v in anchors.healthy_reference_vector.tolist()
                ],
                "observed_cancer_reference_vector": [
                    float(v) for v in anchors.cancer_reference_vector.tolist()
                ],
                "observed_healthy_class_label": str(anchors.healthy_class_label),
                "observed_cancer_class_labels": [str(x) for x in anchors.cancer_class_labels],
                "observed_anchor_strategy": str(anchors.anchor_strategy),
                "observed_feature_order_fingerprint": str(anchors.feature_order_fingerprint),
                "max_dmps": int(max_dmps_norm),
                "quantiles": [float(q) for q in (feat.report.get("quantiles") or [])],
                "min_coverage": int(max(1, params.min_coverage)),
                "observed_feature_include_dmp": bool(params.include_dmp_features),
                "observed_feature_include_chromosome": bool(params.include_chromosome_features),
                "observed_feature_include_dmr": bool(params.include_dmr_features),
                "observed_feature_include_gene": bool(params.include_gene_features),
                "observed_feature_dmr_window_bp": int(max(1, params.dmr_window_bp)),
                "observed_feature_max_dmrs": int(max(0, params.max_dmr_features)),
                "observed_feature_max_genes": int(max(0, params.max_gene_features)),
                "observed_hist_eps": float(params.hist_eps),
                "observed_hist_alpha": float(params.hist_alpha),
                "observed_hist_evidence_clip_cap": float(params.hist_evidence_clip_cap),
                "observed_hist_tail_agreement_threshold": float(params.hist_tail_agreement_threshold),
            }
        )
    else:
        meta_extra["n_observed_features"] = 0
        meta_extra["observed_feature_names"] = []
        meta_extra["training_feature_names"] = []

    cov_report: Dict[str, Any] = {"used": False}
    preprocessor = None
    cov: Optional[np.ndarray] = None
    if params.has_covariates():
        cov, preprocessor, cov_report = fit_covariates(
            params.covariates_path,
            sample_ids,
            covariate_id_column=params.covariate_id_column,
            strict_join=True if dropped_train_ids else params.covariates_strict_join,
            numeric_columns=params.covariate_numeric_columns,
            ordinal_columns=params.covariate_ordinal_columns,
            ordinal_maps=params.covariate_ordinal_maps,
            ordinal_unknown_value=params.covariate_ordinal_unknown_value,
            categorical_columns=params.covariate_categorical_columns,
            missing_numeric_strategy=params.covariate_missing_numeric_strategy,
            standardize_numeric=params.covariate_standardize_numeric,
            composition_groups=params.resolved_composition_groups(),
        )
        if cov is None:
            raise ValueError("covariates_path was set but fit_covariates returned no matrix")
        cov = np.asarray(cov, dtype=np.float32)
        blocks.append(cov)
        cov_names = list(preprocessor.output_columns) if preprocessor is not None else [
            f"cov_{i}" for i in range(cov.shape[1])
        ]
        block_names.extend(cov_names)
        meta_extra["n_covariate_features"] = int(cov.shape[1])
        meta_extra["covariate_feature_names"] = cov_names
        cov_report = dict(cov_report)
        cov_report.update(exclusion_report(dropped_train_ids))
        meta_extra["covariate_report"] = cov_report
        meta_extra["covariates_path"] = (
            [str(p) for p in params.covariates_path]
            if isinstance(params.covariates_path, (list, tuple))
            else str(params.covariates_path)
        )
        preprocessor_path = classifier_output_dir / "covariate-preprocessor.json"
        if preprocessor is not None:
            preprocessor_path.write_text(
                json.dumps(preprocessor.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            meta_extra["covariate_preprocessor_path"] = str(preprocessor_path)
    else:
        meta_extra["n_covariate_features"] = 0
        meta_extra["covariate_feature_names"] = []

    X_full = np.concatenate(blocks, axis=1)
    y = pd.to_numeric(df["expected_class"], errors="coerce").fillna(-1).astype(int).to_numpy()
    valid = np.isin(y, [0, 1])
    if int(np.sum(valid)) < 4:
        raise ValueError("Second-stage scorer requires at least 4 valid binary labeled rows.")
    X = X_full[valid, :]
    y_fit = y[valid]

    clf = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=13)
    clf.fit(X, y_fit)
    probs = clf.predict_proba(X_full)
    y_hat = np.asarray(np.argmax(probs, axis=1), dtype=np.int32)
    refined_balanced_accuracy = (
        float(balanced_accuracy_score(y_fit, y_hat[valid])) if int(np.sum(valid)) > 0 else None
    )

    df["prob_refined_class0"] = probs[:, 0].astype(float)
    df["prob_refined_class1"] = probs[:, 1].astype(float)
    df["prediction_refined"] = y_hat.astype(int)
    df.to_csv(train_pred_csv, index=False)

    model_path = classifier_output_dir / "ecdf-second-stage.joblib"
    joblib.dump(clf, model_path)

    train_metrics_path = predictor_output_dir / "train_metrics.json"
    train_refined = compute_validation_metrics(
        y_fit,
        y_hat[valid],
        ["class0", "class1"],
    )
    train_payload = (
        json.loads(train_metrics_path.read_text(encoding="utf-8"))
        if train_metrics_path.is_file()
        else {}
    )
    train_payload.update(train_refined)
    train_payload.update(
        {
            "evaluation_partition": "train",
            "metrics_source": "ecdf_second_stage_train",
            "n_train_samples": int(len(y_fit)),
            "n_samples_excluded_missing_covariates": int(len(dropped_train_ids)),
            "dropped_sample_ids": list(dropped_train_ids),
        }
    )
    train_metrics_path.write_text(json.dumps(train_payload, indent=2) + "\n", encoding="utf-8")

    test_metrics_path: Optional[Path] = None
    test_df_for_dataset: Optional[pd.DataFrame] = None
    test_prob_cols_for_dataset: Optional[List[str]] = None
    test_sample_ids_for_dataset: Optional[List[str]] = None
    test_y_for_dataset: Optional[np.ndarray] = None
    test_valid_for_dataset: Optional[np.ndarray] = None
    test_cov_for_dataset: Optional[np.ndarray] = None
    if test_pred_csv.is_file():
        test_df = pd.read_csv(test_pred_csv)
        if "sample" not in test_df.columns or "expected_class" not in test_df.columns:
            raise ValueError(
                "Second-stage test application requires sample and expected_class columns."
            )
        test_sample_paths = _sample_paths_from_predictions(test_df, project_json)
        test_sample_ids = [Path(str(path)).name for path in test_sample_paths]
        dropped_test_ids: List[str] = []
        if params.has_covariates() and preprocessor is not None:
            kept_test_ids, dropped_test_ids = resolve_covariate_sample_ids(
                test_sample_ids,
                params.covariates_path,
                params.covariate_id_column,
                missing_samples=params.covariates_missing_samples,
                strict_join=params.covariates_strict_join,
            )
            if dropped_test_ids:
                keep_test = set(kept_test_ids)
                test_mask = [sid in keep_test for sid in test_sample_ids]
                test_df = test_df.loc[test_mask].reset_index(drop=True)
                test_sample_paths = [
                    path for path, keep_row in zip(test_sample_paths, test_mask) if keep_row
                ]
                test_sample_ids = list(kept_test_ids)
        test_probability_matrix, test_prob_cols = _probability_design(
            test_df,
            transform=params.probability_transform,
            epsilon=params.probability_epsilon,
        )
        test_blocks: List[np.ndarray] = [test_probability_matrix]
        overlap = sorted(set(sample_ids) & set(test_sample_ids))
        if overlap:
            raise ValueError(
                "Second-stage train/test datasets overlap; refusing model evaluation. "
                f"First overlap(s): {overlap[:10]}"
            )

        if params.include_observed_hybrid:
            if (
                bundle_h5 is None
                or feat is None
                or anchors is None
                or fill_values is None
                or hybrid_dmp_df is None
                or frozen_gene_panel_df is None
            ):
                raise RuntimeError("Observed second-stage training state was not retained.")
            test_feat = build_observed_hybrid_feature_table(
                test_sample_paths,
                hybrid_dmp_df,
                min_coverage=int(max(1, params.min_coverage)),
                include_dmp_features=bool(params.include_dmp_features),
                include_chromosome_features=bool(params.include_chromosome_features),
                include_dmr_features=bool(params.include_dmr_features),
                include_gene_features=bool(params.include_gene_features),
                dmr_window_bp=int(max(1, params.dmr_window_bp)),
                max_dmr_features=int(max(0, params.max_dmr_features)),
                max_gene_features=int(max(0, params.max_gene_features)),
                healthy_reference_vector=anchors.healthy_reference_vector,
                cancer_reference_vector=anchors.cancer_reference_vector,
                per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
                healthy_class_label=anchors.healthy_class_label,
                cancer_class_labels=anchors.cancer_class_labels,
                all_class_labels=[healthy_label] + cancer_labels,
                anchor_strategy=anchors.anchor_strategy,
                expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
                centroid_dir_by_class_label=centroid_dirs,
                hist_eps=float(params.hist_eps),
                hist_alpha=float(params.hist_alpha),
                hist_evidence_clip_cap=float(params.hist_evidence_clip_cap),
                hist_tail_agreement_threshold=float(params.hist_tail_agreement_threshold),
                feature_family_set=str(params.feature_family_set),
                frozen_gene_panel_df=frozen_gene_panel_df,
                chromosome_hypo_beta_threshold=params.chromosome_hypo_beta_threshold,
                chromosome_intermediate_beta_lo=params.chromosome_intermediate_beta_lo,
                chromosome_intermediate_beta_hi=params.chromosome_intermediate_beta_hi,
                chromosome_distance_metrics=params.chromosome_distance_metrics,
                chromosome_list=params.chromosome_list,
            )
            verify_feature_schema(
                feat.feature_names,
                test_feat.feature_names,
                context="ecdf second-stage test apply",
            )
            test_obs_full = apply_feature_fill_values(
                np.asarray(test_feat.X, dtype=np.float32),
                fill_values,
            )
            test_blocks.append(
                select_training_feature_matrix(
                    test_obs_full,
                    test_feat.feature_names,
                    test_feat.training_feature_names,
                )
            )

        if preprocessor is not None:
            test_cov, _test_cov_report = transform_covariates(
                params.covariates_path,
                test_sample_ids,
                preprocessor,
                strict_join=True if dropped_test_ids else params.covariates_strict_join,
            )
            if test_cov is None:
                raise ValueError("Second-stage covariate transform returned no test matrix.")
            test_cov_for_dataset = np.asarray(test_cov, dtype=np.float32)
            test_blocks.append(test_cov_for_dataset)

        test_matrix = np.concatenate(test_blocks, axis=1)
        if int(test_matrix.shape[1]) != int(X.shape[1]):
            raise ValueError(
                "Second-stage train/test feature count mismatch: "
                f"train={X.shape[1]} test={test_matrix.shape[1]}"
            )
        test_probs = clf.predict_proba(test_matrix)
        test_hat = np.asarray(np.argmax(test_probs, axis=1), dtype=np.int32)
        test_df["prob_refined_class0"] = test_probs[:, 0].astype(float)
        test_df["prob_refined_class1"] = test_probs[:, 1].astype(float)
        test_df["prediction_refined"] = test_hat.astype(int)
        test_df.to_csv(test_pred_csv, index=False)

        test_y = (
            pd.to_numeric(test_df["expected_class"], errors="coerce")
            .fillna(-1)
            .astype(int)
            .to_numpy()
        )
        test_valid = np.isin(test_y, [0, 1])
        test_df_for_dataset = test_df
        test_prob_cols_for_dataset = test_prob_cols
        test_sample_ids_for_dataset = test_sample_ids
        test_y_for_dataset = test_y
        test_valid_for_dataset = test_valid
        test_scored = compute_validation_metrics(
            test_y[test_valid],
            test_hat[test_valid],
            ["class0", "class1"],
        )
        test_metrics_path = predictor_output_dir / "test_metrics.json"
        test_payload = (
            json.loads(test_metrics_path.read_text(encoding="utf-8"))
            if test_metrics_path.is_file()
            else {}
        )
        test_payload.update(test_scored)
        test_payload.update(
            {
                "evaluation_partition": "test",
                "metrics_source": "ecdf_second_stage_test",
                "n_train_samples": int(len(y_fit)),
                "n_test_samples": int(np.sum(test_valid)),
                "train_test_overlap_count": 0,
                "n_samples_excluded_missing_covariates": int(len(dropped_test_ids)),
                "dropped_sample_ids": list(dropped_test_ids),
            }
        )
        test_metrics_path.write_text(
            json.dumps(test_payload, indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.copy2(test_pred_csv, predictor_output_dir / "predictions.csv")
        shutil.copy2(test_metrics_path, predictor_output_dir / "validation_metrics.json")

    try:
        _proj = load_project(project_json)
        dataset_class_names = [str(label) for label, _paths in _proj.get_resolved_groups()]
        if len(dataset_class_names) < 2:
            dataset_class_names = ["class0", "class1"]
    except Exception:
        dataset_class_names = ["class0", "class1"]

    dataset_artifacts = _write_second_stage_datasets(
        project_json=project_json,
        train_predictions=df,
        train_probability_columns=prob_cols,
        train_sample_ids=sample_ids,
        train_labels=y,
        train_valid_rows=valid,
        train_covariates=cov,
        preprocessor=preprocessor,
        test_predictions=test_df_for_dataset,
        test_probability_columns=test_prob_cols_for_dataset,
        test_sample_ids=test_sample_ids_for_dataset,
        test_labels=test_y_for_dataset,
        test_valid_rows=test_valid_for_dataset,
        test_covariates=test_cov_for_dataset,
        class_names=dataset_class_names,
        bundle_dir=project_json.parent / "model_bundle",
    )
    dataset_manifest_path = Path(dataset_artifacts["dataset_manifest_json"])
    dataset_manifest = json.loads(
        dataset_manifest_path.read_text(encoding="utf-8")
    )
    dataset_manifest.update(
        {
            # Binary: closed non-reference probability; multiclass: ALR.
            "probability_transform": (
                "probability" if len(prob_cols) == 1 else "alr"
            ),
            "probability_epsilon": params.probability_epsilon,
            "composition_groups": [
                {
                    "name": spec.name,
                    "columns": list(spec.columns),
                    "reference": spec.reference,
                    "pseudocount": spec.pseudocount,
                    "standardize": spec.standardize,
                }
                for spec in params.resolved_composition_groups()
            ],
            "covariate_preprocessor": meta_extra.get(
                "covariate_preprocessor_path"
            ),
            "second_stage_model": str(model_path),
        }
    )
    dataset_manifest_path.write_text(
        json.dumps(dataset_manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    meta = {
        "model_backend": "ecdf",
        "second_stage_type": "logistic_regression",
        "project_json": str(project_json),
        "predictor_output_dir": str(predictor_output_dir),
        "train_predictions_csv": str(train_pred_csv),
        "test_predictions_csv": str(test_pred_csv) if test_pred_csv.is_file() else None,
        "train_metrics_json": str(train_metrics_path),
        "test_metrics_json": str(test_metrics_path) if test_metrics_path else None,
        **dataset_artifacts,
        "n_features": int(X.shape[1]),
        "stack_feature_names": block_names,
        "refined_balanced_accuracy_labeled_rows": refined_balanced_accuracy,
        **meta_extra,
    }
    meta_path = classifier_output_dir / "ecdf-second-stage-metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    ablation_report = {
        "backend": "ecdf",
        "stage": "second_stage",
        "balanced_accuracy_labeled_rows": refined_balanced_accuracy,
        "active_feature_families": {
            "dmp": bool(params.include_dmp_features) if params.include_observed_hybrid else False,
            "chromosome": bool(params.include_chromosome_features)
            if params.include_observed_hybrid
            else False,
            "dmr": bool(params.include_dmr_features) if params.include_observed_hybrid else False,
            "gene": bool(params.include_gene_features) if params.include_observed_hybrid else False,
            "covariates": bool(params.has_covariates()),
        },
        "recommended_ablation_matrix": [
            {"name": "baseline", "include_dmp": False, "include_dmr": False, "include_gene": False},
            {"name": "plus_dmp", "include_dmp": True, "include_dmr": False, "include_gene": False},
            {"name": "plus_dmr", "include_dmp": False, "include_dmr": True, "include_gene": False},
            {"name": "plus_gene", "include_dmp": False, "include_dmr": False, "include_gene": True},
            {"name": "all", "include_dmp": True, "include_dmr": True, "include_gene": True},
        ],
    }
    with open(classifier_output_dir / "feature_family_ablation.json", "w", encoding="utf-8") as f:
        json.dump(ablation_report, f, indent=2)

    return {
        "model_path": str(model_path),
        "metadata_path": str(meta_path),
        "train_predictions_csv": str(train_pred_csv),
        "test_predictions_csv": str(test_pred_csv) if test_pred_csv.is_file() else None,
        **dataset_artifacts,
        "n_rows": int(df.shape[0]),
        "n_features": int(X.shape[1]),
        "include_observed_hybrid": bool(params.include_observed_hybrid),
        "include_covariates": bool(params.has_covariates()),
    }
