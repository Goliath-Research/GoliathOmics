"""
Baselines and per-run override JSON (split from CLI to avoid import cycles with planner/queue).
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from .validator_metrics import metrics_schema_descriptor

if TYPE_CHECKING:
    from .config import MonteCarloConfig


def digest_sample_paths(paths: List[str]) -> str:
    h = hashlib.sha256()
    for p in sorted(str(x) for x in paths):
        h.update(p.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def write_baseline_manifest(
    *,
    output_root: Path,
    mode: str,
    config: "MonteCarloConfig",
    layout: str,
    cohort_paths_list: List[Tuple[str, List[str]]],
    split_reuse_source_root: Optional[Path] = None,
) -> Path:
    reg = getattr(config, "regulatory", None)
    vp = getattr(config, "validation_partitions", None)
    roles_present = []
    roles_missing = []
    if vp is not None:
        for role in (
            "development_train",
            "internal_validation",
            "locked_test",
            "pivotal_validation",
            "post_market_monitoring",
        ):
            vals = list(getattr(vp, role, []) or [])
            if vals:
                roles_present.append(role)
            else:
                roles_missing.append(role)
    payload: Dict[str, Any] = {
        "manifest_version": "probabilistic_v2_baseline_v1",
        "mode": str(mode),
        "layout": str(layout),
        "n_iterations": int(config.n_iterations),
        "train_fraction": float(config.train_fraction),
        "seed_policy": {
            "base_seed": int(config.seed) if config.seed is not None else None,
            "per_iteration_seed_rule": "seed_i = base_seed + iteration_index",
            "split_strategy": "stratified_per_cohort",
        },
        "cohorts": [
            {
                "label": str(label),
                "n_samples": int(len(paths)),
                "sample_digest_sha256": digest_sample_paths(paths),
            }
            for label, paths in cohort_paths_list
        ],
        "metrics_schema": metrics_schema_descriptor(),
        "regulatory": {
            "stage": getattr(reg, "stage", "feasibility"),
            "allow_clinical_performance_claims": bool(
                getattr(reg, "allow_clinical_performance_claims", False)
            ),
            "claim_boundary": getattr(reg, "claim_boundary", None),
            "intended_use_summary": getattr(reg, "intended_use_summary", None),
            "target_population": getattr(reg, "target_population", None),
            "sample_type": getattr(reg, "sample_type", None),
            "primary_analyte": getattr(reg, "primary_analyte", None),
            "model_training_analyte": getattr(reg, "model_training_analyte", None)
            or getattr(reg, "primary_analyte", None),
            "fragmentomics_schema_version": "fragmentomics_run_v1",
            "reference_standard": getattr(reg, "reference_standard", None),
        },
        "validation_partitions": {
            "configured": vp is not None,
            "roles_present": roles_present,
            "roles_missing": roles_missing,
            "independence_keys": list(getattr(vp, "independence_keys", []) or []),
        },
    }
    if split_reuse_source_root is not None:
        payload["split_reuse"] = {
            "policy": "auto_try_primary_runs_then_stratified",
            "candidate_source_root": str(Path(split_reuse_source_root).resolve()),
            "run_id_pattern": "run_{iteration:04d} for iteration 1..n_iterations",
        }
    output_root.mkdir(parents=True, exist_ok=True)
    out = output_root / "baseline_manifest.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return out


def build_detector_featurecuts_override(
    config: "MonteCarloConfig",
) -> Optional[Dict[str, Any]]:
    """
    Build per-run detector overrides when DMP FeatureCuts stability is enabled.

    Returns a dict suitable for workflow ``stepOverride`` / task ``resolvedConfig`` merge —
    not written to sidecar files beside iteration ``project.json``.
    """
    enable_featurecuts = bool(config.stability_featurecuts_enabled)
    target_ba = config.dmp_featurecuts_target_ba
    if target_ba is None:
        target_ba = config.stability_target_balanced_accuracy
    min_core_dmps = config.dmp_featurecuts_min_dmps
    if min_core_dmps is None:
        min_core_dmps = config.stability_min_core_dmps
    margin_pct = config.stability_classifier_export_margin_pct
    margin_abs = config.stability_classifier_export_margin_abs
    margin_max = config.dmp_featurecuts_max_dmps
    if margin_max is None:
        margin_max = config.stability_classifier_export_max_dmps
    fail_if_below = bool(getattr(config, "dmp_featurecuts_fail_if_below_target", False))
    if (
        not enable_featurecuts
        and target_ba is None
        and min_core_dmps is None
        and margin_pct is None
        and margin_abs is None
        and margin_max is None
        and not fail_if_below
    ):
        return None

    payload: Dict[str, Any] = {}
    if enable_featurecuts or target_ba is not None or min_core_dmps is not None:
        payload["classifier_dmp_selection"] = "featurecuts_validation"
    if target_ba is not None:
        payload["target_balanced_accuracy"] = float(target_ba)
    if min_core_dmps is not None:
        payload["min_core_dmps"] = int(min_core_dmps)
    if margin_pct is not None:
        payload["classifier_export_margin_pct"] = float(margin_pct)
    if margin_abs is not None:
        payload["classifier_export_margin_abs"] = int(margin_abs)
    if margin_max is not None:
        payload["classifier_export_max_dmps"] = int(margin_max)
    if fail_if_below:
        payload["fail_if_below_target"] = True
    if not payload:
        return None
    return payload


def write_detector_featurecuts_override(
    run_dir: Path,
    config: "MonteCarloConfig",
) -> Optional[Path]:
    """Write per-run detector step override JSON when FeatureCuts stability is enabled."""
    payload = build_detector_featurecuts_override(config)
    if payload is None:
        return None
    from .project_gen import prepare_path_for_write

    path = prepare_path_for_write(run_dir / "detector_step_override.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def resolve_detector_step_override_path(
    run_dir: Path,
    config: "MonteCarloConfig",
) -> Optional[str]:
    """Return absolute path to per-run detector override JSON, reusing or materializing sidecar."""
    sidecar = run_dir / "detector_step_override.json"
    if sidecar.is_file():
        return str(sidecar.parent.resolve() / sidecar.name)
    written = write_detector_featurecuts_override(run_dir, config)
    if written is None:
        return None
    return str(written.parent.resolve() / written.name)


CLASSIFIER_DMP_CSV_PATTERN = "dmps-*-classifier.csv"
CLASSIFIER_EXTENDED_DMP_CSV_PATTERN = "dmps-*-classifier-extended.csv"
SELECTED_DMP_CSV_PATTERN = "dmps-*-selected.csv"
DISCOVERY_DMP_CSV_PATTERN = "dmps-*-discovery.csv"
STABLE_DMP_CSV_PATTERN = "stable_dmps*.csv"


def build_mapper_classifier_override(
    config: Optional["MonteCarloConfig"] = None,
) -> Dict[str, Any]:
    """
    Build mapper overrides for MC gene stability (CSV pattern + enrich_disease).

    Returned dict is merged into ``resolvedConfig.mapper`` at task materialization —
    not written to ``mapper_step_override.json`` beside iteration ``project.json``.
    """
    enrich_disease = False
    csv_pattern = DISCOVERY_DMP_CSV_PATTERN
    if config is not None:
        enrich_disease = bool(getattr(config, "stability_mapper_enrich_disease", False))
        from .modeling_modes import infer_dmp_modeling_mode, mapper_csv_pattern_for_dmp_mode

        model_dump = getattr(config, "model_dump", None)
        if callable(model_dump):
            dmp_mode = infer_dmp_modeling_mode(model_dump(mode="python"))
            csv_pattern = mapper_csv_pattern_for_dmp_mode(dmp_mode)
        loci = getattr(config, "gene_featurecuts_loci_source", None)
        dmp_source = str(
            loci or getattr(config, "stability_gene_featurecuts_dmp_source", "discovery") or "discovery"
        ).strip().lower()
        if dmp_source in ("discovery", "raw_pool"):
            csv_pattern = DISCOVERY_DMP_CSV_PATTERN
        elif dmp_source in ("classifier", "featurecuts_selected", "selected"):
            csv_pattern = SELECTED_DMP_CSV_PATTERN
        elif dmp_source in ("stable", "stable_panel"):
            csv_pattern = STABLE_DMP_CSV_PATTERN
        elif dmp_mode == "featurecuts":
            csv_pattern = SELECTED_DMP_CSV_PATTERN
    payload: Dict[str, Any] = {
        "csv_filename_pattern": csv_pattern,
        "enrich_disease": enrich_disease,
    }
    return payload


def write_mapper_classifier_override(
    run_dir: Path,
    config: Optional["MonteCarloConfig"] = None,
) -> Dict[str, Any]:
    """Deprecated alias — returns override dict; does not write sidecar JSON."""
    del run_dir
    return build_mapper_classifier_override(config)
