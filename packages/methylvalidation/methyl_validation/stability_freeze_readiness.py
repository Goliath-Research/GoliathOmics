"""
Stability and freeze readiness analysis: audit MC stability outputs, production freeze,
and disease progression artifacts; emit structured JSON and markdown for pre-model review.

This module is read-only: it does not modify project data.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from methyl_utils.action_config_resolver import resolve_action_config_from_env

# Mirror deprecated detector keys rejected by MethylDetectorConfig.model_validator (subset used in configs).
_REMOVED_DETECTOR_KEYS = frozenset(
    {
        "max_dmps_for_classifier",
        "use_gpu",
        "validation_mode",
        "n_validation_samples",
        "min_sample_coverage",
        "min_validation_coverage_per_position",
        "classifier_coverage_weighting",
        "synthetic_config",
        "classifier_type",
        "eps",
        "ecdf_overlap_grid_size",
        "ecdf_ks_grid_size",
        "statistical_test",
        "distribution",
        "delta_mean_mode",
        "overlap_mode",
        "max_N_for_ecdf",
    }
)


@dataclass
class ReadinessVerdict:
    """Aggregate go / go_with_risks / no_go with reasons."""

    overall: str  # go | go_with_risks | no_go
    enricher: str
    stability: str
    freeze: str
    progression: str
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _scan_removed_detector_keys(detection: Dict[str, Any]) -> List[str]:
    return sorted(k for k in detection if k in _REMOVED_DETECTOR_KEYS)


def _progression_entity_columns(df: pd.DataFrame) -> Tuple[str, Optional[str]]:
    """Return (comparison_col, entity_col) for long-table progression CSV."""
    comp = "comparison" if "comparison" in df.columns else ("comparison_label" if "comparison_label" in df.columns else "")
    if not comp:
        return "", None
    for cand in ("module", "pathway", "gene"):
        if cand in df.columns:
            return comp, cand
    if "entity_label" in df.columns:
        return comp, "entity_label"
    return comp, None


def _module_trajectory_summary(modules_csv: Path, ordered_stages: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "modules_csv": str(modules_csv),
        "n_rows": 0,
        "n_unique_entities": 0,
        "entities_all_stages": 0,
        "median_abs_pearson_stage_vs_score": None,
        "median_abs_spearman_stage_vs_score": None,
        "fraction_monotone_up": None,
        "fraction_monotone_down": None,
        "fraction_mostly_monotone_up": None,
        "fraction_mostly_monotone_down": None,
        "monotone_denominator": 0,
        "monotone_up_count": 0,
        "monotone_down_count": 0,
        "mostly_monotone_up_count": 0,
        "mostly_monotone_down_count": 0,
        "top_by_abs_trend": [],
        "error": None,
    }
    if not modules_csv.is_file():
        out["error"] = "missing_file"
        return out
    try:
        df = pd.read_csv(modules_csv)
    except Exception as exc:
        out["error"] = str(exc)
        return out
    out["n_rows"] = len(df)
    comp_col, ent_col = _progression_entity_columns(df)
    if not comp_col or not ent_col or "stage_index" not in df.columns:
        out["error"] = "unexpected_columns"
        return out
    work = df[[comp_col, ent_col, "stage_index", "score"]].copy()
    work["stage_index"] = pd.to_numeric(work["stage_index"], errors="coerce")
    work["score"] = pd.to_numeric(work["score"], errors="coerce")
    work = work.dropna(subset=["stage_index", "score"])
    work[ent_col] = work[ent_col].astype(str)
    n_ent = work[ent_col].nunique()
    out["n_unique_entities"] = n_ent

    stage_order = {lab: i for i, lab in enumerate(ordered_stages)}
    if not stage_order:
        stage_order = {str(k): int(k) for k in sorted(work["stage_index"].unique())}
    work["_ord"] = work[comp_col].map(stage_order).fillna(work["stage_index"])
    pearson_abs: List[float] = []
    spearman_abs: List[float] = []
    mono_up = mono_down = 0
    mostly_mono_up = mostly_mono_down = 0
    ent_details: List[Tuple[str, float, float]] = []

    for ent, g in work.groupby(ent_col, sort=False):
        gg = g.sort_values("_ord").drop_duplicates(subset=["_ord"], keep="first")
        if len(gg) < 3:
            continue
        xs = gg["_ord"].to_numpy(dtype=float)
        ys = gg["score"].to_numpy(dtype=float)
        if np.std(xs) < 1e-12 or np.std(ys) < 1e-12:
            rho = 0.0
            rho_s = 0.0
        else:
            rho = float(np.corrcoef(xs, ys)[0, 1])
            if np.isnan(rho):
                rho = 0.0
            xs_rank = pd.Series(xs).rank(method="average").to_numpy(dtype=float)
            ys_rank = pd.Series(ys).rank(method="average").to_numpy(dtype=float)
            if np.std(xs_rank) < 1e-12 or np.std(ys_rank) < 1e-12:
                rho_s = 0.0
            else:
                rho_s = float(np.corrcoef(xs_rank, ys_rank)[0, 1])
                if np.isnan(rho_s):
                    rho_s = 0.0
        pearson_abs.append(abs(rho))
        spearman_abs.append(abs(rho_s))
        diffs = np.diff(ys)
        if len(diffs) >= 2:
            if np.all(diffs >= 0) and np.any(diffs > 0):
                mono_up += 1
            if np.all(diffs <= 0) and np.any(diffs < 0):
                mono_down += 1
            up_steps = int(np.sum(diffs > 0))
            down_steps = int(np.sum(diffs < 0))
            # Disease biology is often non-linear: allow one opposite-direction step,
            # but classify to a single dominant direction only.
            if up_steps > down_steps and down_steps <= 1:
                mostly_mono_up += 1
            elif down_steps > up_steps and up_steps <= 1:
                mostly_mono_down += 1
            elif up_steps == down_steps and up_steps > 0:
                net_delta = float(ys[-1] - ys[0])
                if net_delta > 0 and down_steps <= 1:
                    mostly_mono_up += 1
                elif net_delta < 0 and up_steps <= 1:
                    mostly_mono_down += 1
        ent_details.append((str(ent), rho, float(np.mean(ys))))

    all_stage_labels = set(stage_order.keys())
    present_by_ent = work.groupby(ent_col)[comp_col].apply(lambda s: set(s.astype(str)))
    n_all = sum(1 for _e, labs in present_by_ent.items() if all_stage_labels <= labs)
    out["entities_all_stages"] = n_all

    if pearson_abs:
        out["median_abs_pearson_stage_vs_score"] = float(np.median(pearson_abs))
        out["median_abs_spearman_stage_vs_score"] = float(np.median(spearman_abs))
        denom = len(pearson_abs)
        out["monotone_denominator"] = int(denom)
        out["monotone_up_count"] = int(mono_up)
        out["monotone_down_count"] = int(mono_down)
        out["mostly_monotone_up_count"] = int(mostly_mono_up)
        out["mostly_monotone_down_count"] = int(mostly_mono_down)
        out["fraction_monotone_up"] = mono_up / denom
        out["fraction_monotone_down"] = mono_down / denom
        out["fraction_mostly_monotone_up"] = mostly_mono_up / denom
        out["fraction_mostly_monotone_down"] = mostly_mono_down / denom
    ent_details.sort(key=lambda t: abs(t[1]), reverse=True)
    out["top_by_abs_trend"] = [
        {"entity": e, "pearson_stage_vs_score": round(r, 4), "mean_score": round(m, 6)}
        for e, r, m in ent_details[:15]
    ]
    return out


def _chromosome_panel_balance(panel_csv: Path, max_freq_csv: Optional[Path]) -> Dict[str, Any]:
    """Fraction of stable DMPs per chromosome (from panel or frequency table)."""
    out: Dict[str, Any] = {"source": None, "n_positions": 0, "fraction_by_chromosome": {}, "max_chrom_share": None}
    path = panel_csv if panel_csv.is_file() else None
    if path is None and max_freq_csv and max_freq_csv.is_file():
        path = max_freq_csv
        out["source"] = "dmp_frequency.csv"
    elif path is not None:
        out["source"] = panel_csv.name
    if path is None:
        return out
    try:
        df = pd.read_csv(path, usecols=lambda c: c in ("chromosome", "position"))
    except Exception:
        df = pd.read_csv(path)
    if "chromosome" not in df.columns:
        return out
    vc = df["chromosome"].astype(str).value_counts(normalize=True)
    out["n_positions"] = len(df)
    out["fraction_by_chromosome"] = {str(k): round(v, 6) for k, v in vc.items()}
    if len(vc):
        out["max_chrom_share"] = round(vc.max(), 6)
    return out


def _label_mix(labels_csv: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"n_entities": 0, "by_type": {}, "label_counts": {}}
    if not labels_csv.is_file():
        return out
    try:
        df = pd.read_csv(labels_csv)
    except Exception:
        return out
    out["n_entities"] = len(df)
    if "entity_type" in df.columns:
        out["by_type"] = df["entity_type"].value_counts().to_dict()
    if "progression_labels" in df.columns:
        vc = df["progression_labels"].astype(str).value_counts().head(25)
        out["label_counts"] = vc.to_dict()
    return out


def _load_enricher_completeness(production_dir: Path) -> Dict[str, Any]:
    manifest_path = production_dir / "enricher" / "enricher_completeness.json"
    data = _safe_read_json(manifest_path)
    if data is not None:
        return {
            "manifest_present": True,
            "manifest_path": str(manifest_path),
            "all_complete": bool(data.get("all_complete")),
            "comparisons": data.get("comparisons") or {},
        }

    enricher_root = production_dir / "enricher"
    if not enricher_root.is_dir():
        return {"manifest_present": False, "all_complete": False, "comparisons": {}}

    comparisons: Dict[str, Any] = {}
    for sub in sorted(enricher_root.iterdir()):
        if not sub.is_dir():
            continue
        for comp_dir in sorted(sub.iterdir()):
            if not comp_dir.is_dir():
                continue
            label = comp_dir.name
            n_csv = len(list(comp_dir.glob("enrich_*.csv")))
            comparisons[label] = {
                "present_libraries_count": n_csv,
                "complete": n_csv > 0,
            }
    all_complete = bool(comparisons) and all(c.get("complete") for c in comparisons.values())
    return {
        "manifest_present": False,
        "all_complete": all_complete,
        "comparisons": comparisons,
    }


def _regulatory_from_project_dict(production_project: Dict[str, Any]) -> Dict[str, Any]:
    reg = production_project.get("regulatory")
    return dict(reg) if isinstance(reg, dict) else {}


def _validation_partitions_from_project_dict(production_project: Dict[str, Any]) -> Dict[str, Any]:
    parts = production_project.get("validation_partitions")
    return dict(parts) if isinstance(parts, dict) else {}


def _resolve_action_from_project_dict(
    production_project: Dict[str, Any],
    action_key: str,
) -> Dict[str, Any]:
    """Resolve action config from raw production project JSON (profile + site via env)."""
    return resolve_action_config_from_env(
        action_key,
        regulatory=_regulatory_from_project_dict(production_project),
    )


def _extract_regulatory_context(production_project: Dict[str, Any]) -> Dict[str, Any]:
    """Extract lifecycle-stage and claim-boundary metadata from project regulatory block."""
    reg = _regulatory_from_project_dict(production_project)
    stage = str(reg.get("stage") or "feasibility").strip().lower()
    allowed = bool(reg.get("allow_clinical_performance_claims", False))
    boundary = str(reg.get("claim_boundary") or "").strip()
    if not boundary:
        if stage in {"pivotal_validation", "fda_submission", "post_market"}:
            boundary = (
                "Clinical-performance claims may be considered only for pre-specified endpoints "
                "with independent validation and confidence intervals."
            )
        else:
            boundary = (
                "Feasibility/development evidence only; not intended to support final clinical "
                "performance claims."
            )
    return {
        "stage": stage,
        "allow_clinical_performance_claims": allowed,
        "claim_boundary": boundary,
        "intended_use_summary": reg.get("intended_use_summary"),
        "target_population": reg.get("target_population"),
        "sample_type": reg.get("sample_type"),
        "primary_analyte": reg.get("primary_analyte"),
        "model_training_analyte": reg.get("model_training_analyte"),
        "reference_standard": reg.get("reference_standard"),
        "raw": reg if isinstance(reg, dict) else {},
    }


def _extract_partition_contract(production_project: Dict[str, Any]) -> Dict[str, Any]:
    parts = _validation_partitions_from_project_dict(production_project)
    role_names = [
        "development_train",
        "internal_validation",
        "locked_test",
        "pivotal_validation",
        "post_market_monitoring",
    ]
    role_counts: Dict[str, int] = {}
    present: List[str] = []
    missing: List[str] = []
    for role in role_names:
        vals = parts.get(role) or []
        if not isinstance(vals, list):
            vals = []
        n = len([v for v in vals if str(v).strip()])
        role_counts[role] = n
        if n > 0:
            present.append(role)
        else:
            missing.append(role)
    return {
        "configured": bool(parts),
        "role_counts": role_counts,
        "roles_present": present,
        "roles_missing": missing,
        "independence_keys": parts.get("independence_keys") or [],
        "raw": parts,
    }


def analyze_project_root(
    project_root: Path,
    *,
    require_complete_enricher: bool = False,
) -> Dict[str, Any]:
    """
    Collect readiness metrics under ``project_root`` (directory that contains ``monte_carlo_runs``).

    Expected layout::
        {project_root}/monte_carlo_runs/stability/...
        {project_root}/monte_carlo_runs/production/...
    """
    root = project_root.resolve()
    mc = root / "monte_carlo_runs"
    stability_dir = mc / "stability"
    production_dir = mc / "production"
    progression_dir = production_dir / "progression"

    stability_summary_path = stability_dir / "stability_summary.json"
    production_summary_path = production_dir / "production_summary.json"
    production_project_path = production_dir / "project.json"

    stability_summary = _safe_read_json(stability_summary_path) or {}
    production_summary = _safe_read_json(production_summary_path) or {}
    production_project = _safe_read_json(production_project_path) or {}

    dmp_freq_path = stability_dir / "dmp_frequency.csv"
    stable_panel_path = stability_dir / "stable_dmps_production.csv"
    merged_panel_path = production_dir / "stable_dmps_genomewide.csv"
    progression_summary_path = progression_dir / "summary.json"

    dmp_stab = stability_summary.get("dmp_stability") or {}
    progression_summary = _safe_read_json(progression_summary_path) or {}

    detection_cfg = _resolve_action_from_project_dict(production_project, "detection")
    fixed_panel = detection_cfg.get("fixed_dmp_panel") or production_summary.get("fixed_dmp_panel")

    removed_in_production = _scan_removed_detector_keys(detection_cfg if isinstance(detection_cfg, dict) else {})

    ordered_labels = list(progression_summary.get("ordered_comparison_labels") or [])
    modules_long = progression_summary.get("modules_long_csv")
    mod_path = Path(modules_long) if modules_long else progression_dir / "modules_long.csv"
    modules_long_variant = progression_summary.get("modules_long_variant_csv")
    mod_variant_path = (
        Path(modules_long_variant)
        if modules_long_variant
        else progression_dir / "modules_long_variant.csv"
    )
    modules_long_detailed = progression_summary.get("modules_long_detailed_csv")
    mod_detailed_path = (
        Path(modules_long_detailed)
        if modules_long_detailed
        else progression_dir / "modules_long_detailed.csv"
    )

    ordered_stage_narratives: List[Dict[str, Any]] = []
    if production_project_path.is_file():
        try:
            from methyl_utils.pipeline_config import load_project

            proj = load_project(production_project_path)
            tokens = ordered_labels
            if not tokens:
                get_ordered_labels = getattr(proj, "get_ordered_comparison_labels", None)
                if callable(get_ordered_labels):
                    ordered = get_ordered_labels()
                    if isinstance(ordered, (list, tuple)):
                        tokens = [t for t in ordered if isinstance(t, str) and t]
                else:
                    get_comparisons = getattr(proj, "get_comparisons", None)
                    if callable(get_comparisons):
                        comparisons = get_comparisons()
                        if not isinstance(comparisons, (list, tuple)):
                            comparisons = []
                        tokens = [
                            (getattr(c, "comparison_label", None) or getattr(c, "disease_group", None))
                            for c in comparisons
                        ]
                        tokens = [t for t in tokens if isinstance(t, str) and t]
            get_narratives = getattr(proj, "get_ordered_stage_narratives", None)
            if callable(get_narratives):
                narratives = get_narratives(tokens)
                if isinstance(narratives, list):
                    ordered_stage_narratives = [
                        item for item in narratives if isinstance(item, dict)
                    ]
        except Exception:
            ordered_stage_narratives = []

    mapper_cfg = _resolve_action_from_project_dict(production_project, "mapper")
    disease_context = mapper_cfg.get("disease_term")
    if isinstance(disease_context, str):
        disease_context = disease_context.strip() or None
    else:
        disease_context = None
    regulatory_context = _extract_regulatory_context(production_project)
    partition_contract = _extract_partition_contract(production_project)
    from .fragmentomics_context import build_fragmentomics_report

    fragmentomics_context = build_fragmentomics_report(
        project_root=root,
        production_project=production_project,
    )

    report: Dict[str, Any] = {
        "project_root": str(root),
        "disease_context": disease_context,
        "regulatory": regulatory_context,
        "fragmentomics": fragmentomics_context,
        "validation_partitions": partition_contract,
        "paths": {
            "stability_summary": str(stability_summary_path),
            "production_summary": str(production_summary_path),
            "production_project": str(production_project_path),
            "stable_panel": str(stable_panel_path),
            "merged_panel": str(merged_panel_path),
            "progression_summary": str(progression_summary_path),
        },
        "stability": {
            "present": stability_summary_path.is_file(),
            "dmp_stability": dmp_stab,
            "stable_panel_rows": _count_csv_rows(stable_panel_path),
            "dmp_frequency_rows": _count_csv_rows(dmp_freq_path),
        },
        "freeze": {
            "present": production_summary_path.is_file(),
            "success": production_summary.get("success"),
            "errors": production_summary.get("errors"),
            "timings": production_summary.get("timings"),
            "fixed_dmp_panel_in_project": fixed_panel,
            "merged_panel_rows": _count_csv_rows(merged_panel_path),
            "removed_detector_keys_in_production_project": removed_in_production,
        },
        "progression": {
            "summary_present": progression_summary_path.is_file(),
            "ordered_comparison_labels": ordered_labels,
            "missing_inputs": progression_summary.get("missing_inputs"),
            "row_counts": {
                "genes": progression_summary.get("genes_rows"),
                "pathways": progression_summary.get("pathways_rows"),
                "modules": progression_summary.get("modules_rows"),
                "modules_variant": progression_summary.get("modules_variant_rows"),
                "modules_detailed": progression_summary.get("modules_detailed_rows"),
            },
            "module_trajectory": _module_trajectory_summary(mod_path, ordered_labels),
            "module_trajectory_variant": _module_trajectory_summary(mod_variant_path, ordered_labels),
            "module_trajectory_detailed": _module_trajectory_summary(mod_detailed_path, ordered_labels),
            "entity_labels": _label_mix(progression_dir / "entities_progression_labels.csv"),
            "ordered_stage_narratives": ordered_stage_narratives,
        },
        "panel_balance": _chromosome_panel_balance(merged_panel_path, dmp_freq_path),
        "enricher": _load_enricher_completeness(production_dir),
    }
    report["verdict"] = asdict(
        _compute_verdict(report, require_complete_enricher=require_complete_enricher)
    )
    return report


def _count_csv_rows(path: Path) -> Optional[int]:
    if not path.is_file():
        return None
    try:
        return sum(1 for _ in open(path, encoding="utf-8", errors="replace")) - 1
    except Exception:
        return None


def _compute_verdict(
    report: Dict[str, Any],
    *,
    require_complete_enricher: bool = False,
) -> ReadinessVerdict:
    reasons: List[str] = []
    warnings: List[str] = []

    stab = report["stability"]
    fr = report["freeze"]
    prog = report["progression"]
    enr = report.get("enricher") or {}
    balance = report["panel_balance"]
    reg = report.get("regulatory") or {}
    frag = report.get("fragmentomics") or {}
    parts = report.get("validation_partitions") or {}

    enricher_status = "unknown"
    if not enr.get("manifest_present") and not enr.get("comparisons"):
        enricher_status = "skipped"
        warnings.append(
            "No enricher_completeness.json — run methyl-enricher --ensure-complete or verify-complete."
        )
    elif enr.get("all_complete"):
        enricher_status = "pass"
    else:
        incomplete = [
            k
            for k, v in (enr.get("comparisons") or {}).items()
            if not v.get("complete")
        ]
        msg = f"Enricher incomplete for {len(incomplete)} comparison(s): {incomplete[:5]}"
        if require_complete_enricher:
            enricher_status = "fail"
            reasons.append(msg)
        else:
            enricher_status = "warn"
            warnings.append(msg)

    stab_status = "unknown"
    if not stab["present"]:
        stab_status = "fail"
        reasons.append("Missing stability_summary.json — run stability analysis first.")
    else:
        ds = stab.get("dmp_stability") or {}
        n_runs = int(ds.get("n_runs_analyzed") or 0)
        stable_n = int(ds.get("stable_dmps_at_threshold") or 0)
        panel_rows = stab.get("stable_panel_rows")
        if panel_rows is not None and panel_rows != stable_n and stable_n:
            warnings.append(f"stable_dmps_production.csv row count ({panel_rows}) differs from summary stable_dmps_at_threshold ({stable_n}).")
        if stable_n <= 0 or (panel_rows is not None and panel_rows <= 0):
            stab_status = "fail"
            reasons.append("Stable DMP panel is empty — lower stability_dmp_freq or increase iterations.")
        elif n_runs < 5:
            stab_status = "warn"
            warnings.append(f"Only {n_runs} runs analyzed; prefer ≥10 for robust recurrence estimates.")
        else:
            stab_status = "pass"

    freeze_status = "unknown"
    if not fr["present"]:
        freeze_status = "fail"
        reasons.append("Missing production_summary.json — run --freeze after stability.")
    elif fr.get("success") is not True:
        freeze_status = "fail"
        reasons.append(f"Freeze did not succeed: errors={fr.get('errors')!r}")
    else:
        timings = fr.get("timings") or []
        bad = []
        for t in timings:
            rc = t.get("return_code")
            if rc is None:
                rc = -1
            try:
                rc_int = int(rc)
            except (TypeError, ValueError):
                rc_int = -1
            if rc_int != 0:
                bad.append(t)
        if bad:
            freeze_status = "fail"
            reasons.append(f"Freeze steps with non-zero return_code: {[t.get('step_name') for t in bad]}")
        elif fr.get("removed_detector_keys_in_production_project"):
            freeze_status = "fail"
            reasons.append(
                "production/project.json detection block contains removed legacy keys: "
                f"{fr['removed_detector_keys_in_production_project']}"
            )
        elif not fr.get("fixed_dmp_panel_in_project"):
            freeze_status = "fail"
            reasons.append("production project missing resolved detection.fixed_dmp_panel.")
        else:
            freeze_status = "pass"

    prog_status = "unknown"
    if prog["summary_present"]:
        miss = prog.get("missing_inputs") or []
        if miss:
            prog_status = "warn"
            warnings.append(f"Progression missing_inputs non-empty: {miss[:5]}...")
        else:
            traj = prog.get("module_trajectory") or {}
            if traj.get("error"):
                prog_status = "warn"
                warnings.append(f"Module trajectory could not be computed: {traj.get('error')}")
            elif int(traj.get("entities_all_stages") or 0) == 0:
                prog_status = "warn"
                warnings.append("No modules present across all ordered stages — review enricher modules_ranked.csv inputs.")
            else:
                prog_status = "pass"
        med = (prog.get("module_trajectory") or {}).get("median_abs_pearson_stage_vs_score")
        if med is not None and med < 0.15:
            warnings.append(
                "Weak median |Pearson(stage_ord, score)| across modules — progression signal may be subtle."
            )
    else:
        prog_status = "skipped"
        warnings.append(
            "No progression/summary.json — enable profile actionConfig.progression on freeze "
            "or run progression separately."
        )

    if balance.get("max_chrom_share") is not None and balance["max_chrom_share"] > 0.45:
        warnings.append(
            f"Chromosome imbalance: one chromosome holds ~{balance['max_chrom_share']*100:.1f}% of stable panel."
        )

    training_analyte = None
    if isinstance(reg.get("raw"), dict):
        from .analyte_guard import effective_training_analyte

        training_analyte = effective_training_analyte(reg.get("raw"))
    prod_root = Path(str(report.get("project_root") or "."))
    locked_spec = prod_root / "monte_carlo_runs" / "production" / "locked_model_spec.json"
    if locked_spec.is_file() and training_analyte:
        try:
            locked_payload = json.loads(locked_spec.read_text(encoding="utf-8"))
            locked_reg = (locked_payload.get("regulatory") or {}) if isinstance(locked_payload, dict) else {}
            from .analyte_guard import effective_training_analyte as _eta

            locked_analyte = _eta(locked_reg if isinstance(locked_reg, dict) else None)
            if locked_analyte and locked_analyte != training_analyte and locked_analyte != "combined":
                warnings.append(
                    f"Locked model training analyte ({locked_analyte!r}) differs from project "
                    f"training analyte ({training_analyte!r}) — re-run freeze/--model on this cohort."
                )
        except Exception:
            pass

    if frag.get("expected_for_analyte"):
        aq = frag.get("alignment_qc_fragmentomics") or {}
        if int(aq.get("n_samples_with_metrics") or 0) == 0:
            warnings.append(
                "primary_analyte=cfdna but no alignment_qc fragmentomics_metrics found — "
                "run methyl-qc with profile actionConfig.alignment_qc.fragmentomics enabled."
            )
        elif aq.get("all_fragmentomics_guardrails_pass") is False:
            warnings.append(
                "cfDNA fragmentomics guardrails failed for one or more samples in alignment_qc."
            )
        if frag.get("fragmentomics_step_enabled"):
            bam = frag.get("bam_fragmentomics") or {}
            if not bam.get("summary_present"):
                warnings.append(
                    "profile actionConfig.fragmentomics.enabled but fragmentomics_summary.json missing — "
                    "run methyl-fragmentomics."
                )

    stage = str(reg.get("stage") or "feasibility").strip().lower()
    allow_claims = bool(reg.get("allow_clinical_performance_claims", False))
    if stage in {"feasibility", "expanded_development", "internal_validation", "model_freeze"}:
        if allow_claims:
            reasons.append(
                f"Regulatory mismatch: stage={stage!r} cannot allow final clinical performance claims."
            )
    if stage in {"pivotal_validation", "fda_submission"}:
        if not (reg.get("reference_standard") or ""):
            warnings.append(
                "Pivotal/submission stage configured without reference_standard in project regulatory."
            )
        if not (reg.get("target_population") or ""):
            warnings.append(
                "Pivotal/submission stage configured without target_population in project regulatory."
            )
        if not parts.get("configured"):
            warnings.append(
                "Pivotal/submission stage configured without validation_partitions contract."
            )
        else:
            missing = set(parts.get("roles_missing") or [])
            needed = {"locked_test", "pivotal_validation"}
            missing_needed = sorted(needed & missing)
            if missing_needed:
                reasons.append(
                    "Pivotal/submission stage missing required partition roles: "
                    + ", ".join(missing_needed)
                )

    # Overall
    if stab_status == "fail" or freeze_status == "fail" or enricher_status == "fail":
        overall = "no_go"
    elif (
        stab_status == "warn"
        or freeze_status == "warn"
        or prog_status == "warn"
        or enricher_status == "warn"
        or warnings
    ):
        overall = "go_with_risks"
    else:
        overall = "go"

    return ReadinessVerdict(
        overall=overall,
        enricher=enricher_status,
        stability=stab_status,
        freeze=freeze_status,
        progression=prog_status,
        reasons=reasons,
        warnings=warnings,
    )


def _fragmentomics_plain_line(frag: Dict[str, Any]) -> str:
    if not frag.get("expected_for_analyte") and not frag.get("configured"):
        return "- **Fragmentomics**: not configured for this project."
    aq = frag.get("alignment_qc_fragmentomics") or {}
    bam = frag.get("bam_fragmentomics") or {}
    n_aq = int(aq.get("n_samples_with_metrics") or 0)
    n_bam = int(bam.get("n_samples") or 0)
    med = aq.get("cohort_median_insert_size")
    short = aq.get("cohort_mean_short_fragment_fraction")
    parts = [f"alignment_qc metrics on {n_aq} sample(s)"]
    if med is not None:
        parts.append(f"median insert ~{med} bp")
    if short is not None:
        parts.append(f"mean short-fragment fraction ~{short}")
    if frag.get("fragmentomics_step_enabled"):
        parts.append(f"BAM step: {n_bam} sample(s) with summary")
    return "- **Fragmentomics (cfDNA)**: " + "; ".join(parts) + "."


def render_markdown(report: Dict[str, Any], *, redact_paths: bool = False) -> str:
    from .grok_readiness import redact_report_for_export

    def _fmt_pct(x: Any) -> str:
        try:
            return f"{float(x) * 100:.1f}%"
        except Exception:
            return "n/a"

    def _fmt_pp(x: Any) -> str:
        try:
            return f"{float(x):+.1f} pp"
        except Exception:
            return "n/a"

    def _fmt_frac_with_counts(frac: Any, num: Any, den: Any) -> str:
        try:
            f = float(frac)
            n = int(num)
            d = int(den)
            if d <= 0:
                return "n/a"
            return f"{f:.3f} ({n}/{d})"
        except Exception:
            return "n/a"

    def _md_cell(x: Any) -> str:
        return str(x if x is not None else "").replace("|", r"\|")

    def _trend_label_from_series(vals: List[float]) -> str:
        if len(vals) < 2:
            return "insufficient"
        diffs = np.diff(np.asarray(vals, dtype=float))
        if len(diffs) == 0:
            return "insufficient"
        if np.all(diffs >= 0) and np.any(diffs > 0):
            return "strict_up"
        if np.all(diffs <= 0) and np.any(diffs < 0):
            return "strict_down"
        up_steps = int(np.sum(diffs > 0))
        down_steps = int(np.sum(diffs < 0))
        if up_steps > down_steps and down_steps <= 1:
            return "mostly_up"
        if down_steps > up_steps and up_steps <= 1:
            return "mostly_down"
        if up_steps == down_steps and up_steps > 0:
            net = float(vals[-1] - vals[0])
            if net > 0 and down_steps <= 1:
                return "mostly_up"
            if net < 0 and up_steps <= 1:
                return "mostly_down"
        return "mixed"

    def _append_stage_matrix(
        lines: List[str],
        *,
        traj: Dict[str, Any],
        ordered_labels: List[str],
        top_n: int = 8,
    ) -> None:
        csv_path = traj.get("modules_csv")
        if not isinstance(csv_path, str) or not csv_path.strip():
            return
        pth = Path(csv_path)
        if not pth.is_file():
            return
        try:
            df = pd.read_csv(pth)
        except Exception:
            return
        comp_col, ent_col = _progression_entity_columns(df)
        if not comp_col or not ent_col or "score" not in df.columns:
            return
        work = df[[comp_col, ent_col, "score"]].copy()
        work[ent_col] = work[ent_col].astype(str)
        work["score"] = pd.to_numeric(work["score"], errors="coerce")
        work = work.dropna(subset=["score"])
        if work.empty:
            return
        stage_cols = [str(s) for s in ordered_labels if str(s)]
        if not stage_cols:
            stage_cols = [str(s) for s in work[comp_col].astype(str).drop_duplicates().tolist()]
        top_entities = [str(r.get("entity")) for r in (traj.get("top_by_abs_trend") or []) if r.get("entity")]
        if not top_entities:
            top_entities = work[ent_col].drop_duplicates().astype(str).tolist()
        top_entities = top_entities[: max(1, int(top_n))]
        lines.extend(["", "#### Score matrix (entity × stage)", ""])
        lines.append("| Entity | Trend | " + " | ".join(stage_cols) + " |")
        lines.append("|--------|-------|" + "|".join(["---"] * len(stage_cols)) + "|")
        for ent in top_entities:
            sub = work[work[ent_col] == ent]
            by_stage: Dict[str, float] = {}
            for st, g in sub.groupby(sub[comp_col].astype(str)):
                by_stage[str(st)] = float(pd.to_numeric(g["score"], errors="coerce").dropna().mean())
            series_vals = [by_stage[s] for s in stage_cols if s in by_stage]
            trend = _trend_label_from_series(series_vals)
            row_vals = [f"{by_stage[s]:.4f}" if s in by_stage else "" for s in stage_cols]
            lines.append(f"| {_md_cell(ent)} | {_md_cell(trend)} | " + " | ".join(row_vals) + " |")

    def _physician_focus_lines(src_report: Dict[str, Any], verdict: Dict[str, Any]) -> List[str]:
        prog = src_report.get("progression") or {}
        reg = src_report.get("regulatory") or {}
        traj = prog.get("module_trajectory") or {}
        traj_var = prog.get("module_trajectory_variant") or {}
        traj_det = prog.get("module_trajectory_detailed") or {}
        ai = report.get("ai_review") if isinstance(report.get("ai_review"), dict) else {}
        structured = ai.get("structured") if isinstance(ai, dict) else {}
        impact = (
            str(structured.get("impact_on_confidence")).strip().lower()
            if isinstance(structured, dict) and structured.get("impact_on_confidence")
            else None
        )
        consistency = (
            str(structured.get("consistency_assessment")).strip().lower()
            if isinstance(structured, dict) and structured.get("consistency_assessment")
            else None
        )
        top = (traj.get("top_by_abs_trend") or [])[:3]
        track_match = "unknown"
        if traj and traj_var:
            keys = (
                "entities_all_stages",
                "median_abs_pearson_stage_vs_score",
                "fraction_monotone_up",
                "fraction_monotone_down",
            )
            try:
                track_match = (
                    "similar"
                    if all((traj.get(k) == traj_var.get(k)) for k in keys)
                    else "different"
                )
            except Exception:
                track_match = "unknown"

        out = [
            "## Physician-focused interpretation",
            "",
            "- This section is plain-language. Full technical details remain in sections below.",
            f"- **Readiness verdict**: `{verdict.get('overall', 'unknown')}` (enricher: {verdict.get('enricher')}, stability: {verdict.get('stability')}, freeze: {verdict.get('freeze')}, progression: {verdict.get('progression')}).",
            f"- **Primary analyte framing**: `{reg.get('primary_analyte') or 'not_declared'}` (sample type: `{reg.get('sample_type') or 'not_declared'}`).",
            _fragmentomics_plain_line(report.get("fragmentomics") or {}),
            f"- **Module persistence across all ordered stages**: {traj.get('entities_all_stages', 'n/a')} canonical entities; {traj_var.get('entities_all_stages', 'n/a')} variant-family entities.",
            f"- **Directional progression signal**: monotone-up fraction { _fmt_pct(traj.get('fraction_monotone_up')) } (canonical) and { _fmt_pct(traj_var.get('fraction_monotone_up')) } (variant-family).",
            f"- **Canonical vs variant-family agreement**: `{track_match}`.",
        ]
        if traj_det:
            out.append(
                f"- **Cluster-level granularity check**: {traj_det.get('n_unique_entities', 'n/a')} detailed entities with rows; {traj_det.get('entities_all_stages', 'n/a')} present across all stages."
            )
            try:
                canon_n = max(1, int(traj.get("n_unique_entities") or 0))
                canon_all = int(traj.get("entities_all_stages") or 0)
                det_n = max(1, int(traj_det.get("n_unique_entities") or 0))
                det_all = int(traj_det.get("entities_all_stages") or 0)
                canon_ratio = canon_all / canon_n
                det_ratio = det_all / det_n
                delta_pp = (det_ratio - canon_ratio) * 100.0
                if delta_pp <= -30.0:
                    granularity_risk = "high"
                elif delta_pp <= -10.0:
                    granularity_risk = "medium"
                else:
                    granularity_risk = "low"
                out.append(
                    "- **Granularity risk indicator**: "
                    f"`{granularity_risk}` (canonical continuity {_fmt_pct(canon_ratio)}, "
                    f"detailed continuity {_fmt_pct(det_ratio)}, delta {_fmt_pp(delta_pp)})."
                )
            except Exception:
                out.append("- **Granularity risk indicator**: `unknown` (insufficient continuity metadata).")
        if top:
            top_names = ", ".join(str(r.get("entity")) for r in top if r.get("entity"))
            if top_names:
                out.append(f"- **Top biology linked to stage trend**: {top_names}.")
        if impact:
            out.append(f"- **AI-estimated impact on confidence**: `{impact}`.")
        if consistency:
            out.append(f"- **AI consistency assessment**: `{consistency}`.")
        out.extend(
            [
                "- **Suggested physician review**: confirm whether leading modules align with expected Gleason-stage biology and treatment context.",
                "",
            ]
        )
        return out

    src = redact_report_for_export(report) if redact_paths else report
    v = src.get("verdict") or {}
    lines = [
        "# Stability and freeze readiness report",
        "",
    ]
    if not redact_paths:
        lines.append(f"- **Project root**: `{src.get('project_root', '')}`")
    lines.extend(
        [
            f"- **Overall verdict**: **{v.get('overall', 'unknown')}**",
            f"- **Enricher**: {v.get('enricher')}",
            f"- **Stability**: {v.get('stability')}",
            f"- **Freeze**: {v.get('freeze')}",
            f"- **Progression**: {v.get('progression')}",
            "",
        ]
    )
    reg = src.get("regulatory") or {}
    lines.extend(
        [
            "## Lifecycle framing",
            "",
            f"- **Lifecycle stage**: `{reg.get('stage', 'feasibility')}`",
            f"- **Clinical performance claims allowed**: `{bool(reg.get('allow_clinical_performance_claims', False))}`",
            f"- **Claim boundary**: {reg.get('claim_boundary', '')}",
        ]
    )
    if reg.get("intended_use_summary"):
        lines.append(f"- **Intended use summary**: {reg.get('intended_use_summary')}")
    if reg.get("target_population"):
        lines.append(f"- **Target population**: {reg.get('target_population')}")
    if reg.get("sample_type"):
        lines.append(f"- **Sample type**: {reg.get('sample_type')}")
    if reg.get("primary_analyte"):
        lines.append(f"- **Primary analyte**: `{reg.get('primary_analyte')}`")
    if reg.get("reference_standard"):
        lines.append(f"- **Reference standard**: {reg.get('reference_standard')}")
    lines.append("")
    vp = src.get("validation_partitions") or {}
    lines.extend(
        [
            "## Validation partition contract",
            "",
            f"- **Configured**: `{bool(vp.get('configured', False))}`",
            f"- **Roles present**: {vp.get('roles_present', [])}",
            f"- **Roles missing**: {vp.get('roles_missing', [])}",
            f"- **Independence keys**: {vp.get('independence_keys', [])}",
            "",
        ]
    )
    lines.extend(_physician_focus_lines(src, v))
    if v.get("reasons"):
        lines.extend(["## Blocking issues", ""])
        lines.extend(f"- {r}" for r in v["reasons"])
        lines.append("")
    if v.get("warnings"):
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {w}" for w in v["warnings"])
        lines.append("")

    s = src.get("stability") or {}
    lines.extend(
        [
            "## Stability",
            "",
            f"- Summary present: {s.get('present')}",
            f"- Stable panel rows (CSV): {s.get('stable_panel_rows')}",
            f"- DMP frequency table rows: {s.get('dmp_frequency_rows')}",
        ]
    )
    ds = s.get("dmp_stability") or {}
    if ds:
        lines.extend(
            [
                f"- Runs analyzed: {ds.get('n_runs_analyzed')}",
                f"- Skipped (low BA): {ds.get('skipped_low_balanced_accuracy')}",
                f"- Min frequency threshold: {ds.get('min_frequency')}",
                f"- Stable DMPs at threshold: {ds.get('stable_dmps_at_threshold')}",
                f"- Total unique DMPs seen: {ds.get('total_unique_dmps')}",
            ]
        )
    lines.append("")

    enr = src.get("enricher") or {}
    lines.extend(
        [
            "## Enricher completeness",
            "",
            f"- Manifest present: {enr.get('manifest_present')}",
            f"- All comparisons complete: {enr.get('all_complete')}",
        ]
    )
    comps = enr.get("comparisons") or {}
    if comps:
        lines.append(f"- Comparisons tracked: {len(comps)}")
        for label, comp in list(comps.items())[:8]:
            if isinstance(comp, dict) and "missing_libraries" in comp:
                n_ok = len(comp.get("present_libraries") or [])
                n_miss = len(comp.get("missing_libraries") or [])
                lines.append(f"  - `{label}`: {n_ok} libraries OK, {n_miss} missing")
            else:
                lines.append(f"  - `{label}`: complete={comp.get('complete')}")
    lines.append("")

    f = src.get("freeze") or {}
    lines.extend(
        [
            "## Freeze (production)",
            "",
            f"- Summary present: {f.get('present')}",
            f"- Success: {f.get('success')}",
            (
                f"- Fixed panel path: `{f.get('fixed_dmp_panel_in_project')}`"
                if f.get("fixed_dmp_panel_in_project")
                else "- Fixed panel path: (redacted)"
            ),
            f"- Merged panel rows: {f.get('merged_panel_rows')}",
        ]
    )
    if f.get("removed_detector_keys_in_production_project"):
        lines.append(f"- **Legacy detector keys still present**: {f['removed_detector_keys_in_production_project']}")
    if f.get("timings"):
        lines.extend(["", "| Step | Seconds | RC |", "|------|---------|-----|"])
        for t in f["timings"]:
            lines.append(
                f"| {t.get('step_name')} | {t.get('duration_seconds')} | {t.get('return_code')} |"
            )
    lines.append("")

    p = src.get("progression") or {}
    ordered_stage_cols = [str(x) for x in (p.get("ordered_comparison_labels") or []) if str(x)]
    lines.extend(
        [
            "## Progression",
            "",
            f"- Summary present: {p.get('summary_present')}",
            f"- Ordered comparisons: {p.get('ordered_comparison_labels')}",
            f"- Missing inputs: {p.get('missing_inputs')}",
            f"- Row counts: {p.get('row_counts')}",
        ]
    )
    narr = p.get("ordered_stage_narratives") or []
    if isinstance(narr, list) and narr:
        lines.extend(["", "### Stage definitions (from project config)", ""])
        for row in narr:
            if not isinstance(row, dict):
                continue
            lab = row.get("comparison_label") or "?"
            desc = row.get("description")
            if isinstance(desc, str) and desc.strip():
                lines.append(f"- **`{lab}`**: {desc.strip()}")
            else:
                lines.append(f"- **`{lab}`**")
    traj = p.get("module_trajectory") or {}
    if traj:
        lines.extend(
            [
                "",
                "### Module score trajectory (vs stage order)",
                "",
                f"- Entities with rows: {traj.get('n_unique_entities')}",
                f"- Entities present all stages: {traj.get('entities_all_stages')}",
                f"- Median |Pearson(stage_ord, score)|: {traj.get('median_abs_pearson_stage_vs_score')}",
                f"- Median |Spearman(stage_ord, score)|: {traj.get('median_abs_spearman_stage_vs_score')}",
                (
                    f"- Fraction monotone up (among scored): "
                    f"{_fmt_frac_with_counts(traj.get('fraction_monotone_up'), traj.get('monotone_up_count'), traj.get('monotone_denominator'))}"
                ),
                (
                    f"- Fraction monotone down (among scored): "
                    f"{_fmt_frac_with_counts(traj.get('fraction_monotone_down'), traj.get('monotone_down_count'), traj.get('monotone_denominator'))}"
                ),
                (
                    f"- Fraction mostly monotone up (<=1 reversal): "
                    f"{_fmt_frac_with_counts(traj.get('fraction_mostly_monotone_up'), traj.get('mostly_monotone_up_count'), traj.get('monotone_denominator'))}"
                ),
                (
                    f"- Fraction mostly monotone down (<=1 reversal): "
                    f"{_fmt_frac_with_counts(traj.get('fraction_mostly_monotone_down'), traj.get('mostly_monotone_down_count'), traj.get('monotone_denominator'))}"
                ),
            ]
        )
        top = traj.get("top_by_abs_trend") or []
        if top:
            lines.extend(["", "| Entity | Pearson | Mean score |", "|--------|---------|------------|"])
            for row in top[:10]:
                lines.append(
                    f"| {_md_cell(row.get('entity'))} | {row.get('pearson_stage_vs_score')} | {row.get('mean_score')} |"
                )
        _append_stage_matrix(lines, traj=traj, ordered_labels=ordered_stage_cols, top_n=8)
    traj_var = p.get("module_trajectory_variant") or {}
    if traj_var:
        lines.extend(
            [
                "",
                "### Module variant trajectory (dual-label view)",
                "",
                f"- Entities with rows: {traj_var.get('n_unique_entities')}",
                f"- Entities present all stages: {traj_var.get('entities_all_stages')}",
                f"- Median |Pearson(stage_ord, score)|: {traj_var.get('median_abs_pearson_stage_vs_score')}",
                f"- Median |Spearman(stage_ord, score)|: {traj_var.get('median_abs_spearman_stage_vs_score')}",
                (
                    f"- Fraction monotone up (among scored): "
                    f"{_fmt_frac_with_counts(traj_var.get('fraction_monotone_up'), traj_var.get('monotone_up_count'), traj_var.get('monotone_denominator'))}"
                ),
                (
                    f"- Fraction monotone down (among scored): "
                    f"{_fmt_frac_with_counts(traj_var.get('fraction_monotone_down'), traj_var.get('monotone_down_count'), traj_var.get('monotone_denominator'))}"
                ),
                (
                    f"- Fraction mostly monotone up (<=1 reversal): "
                    f"{_fmt_frac_with_counts(traj_var.get('fraction_mostly_monotone_up'), traj_var.get('mostly_monotone_up_count'), traj_var.get('monotone_denominator'))}"
                ),
                (
                    f"- Fraction mostly monotone down (<=1 reversal): "
                    f"{_fmt_frac_with_counts(traj_var.get('fraction_mostly_monotone_down'), traj_var.get('mostly_monotone_down_count'), traj_var.get('monotone_denominator'))}"
                ),
            ]
        )
        top_var = traj_var.get("top_by_abs_trend") or []
        if top_var:
            lines.extend(["", "| Entity | Pearson | Mean score |", "|--------|---------|------------|"])
            for row in top_var[:10]:
                lines.append(
                    f"| {_md_cell(row.get('entity'))} | {row.get('pearson_stage_vs_score')} | {row.get('mean_score')} |"
                )
        _append_stage_matrix(lines, traj=traj_var, ordered_labels=ordered_stage_cols, top_n=8)
    traj_det = p.get("module_trajectory_detailed") or {}
    if traj_det:
        lines.extend(
            [
                "",
                "### Module detailed trajectory (cluster-level view)",
                "",
                f"- Entities with rows: {traj_det.get('n_unique_entities')}",
                f"- Entities present all stages: {traj_det.get('entities_all_stages')}",
                f"- Median |Pearson(stage_ord, score)|: {traj_det.get('median_abs_pearson_stage_vs_score')}",
                f"- Median |Spearman(stage_ord, score)|: {traj_det.get('median_abs_spearman_stage_vs_score')}",
                (
                    f"- Fraction monotone up (among scored): "
                    f"{_fmt_frac_with_counts(traj_det.get('fraction_monotone_up'), traj_det.get('monotone_up_count'), traj_det.get('monotone_denominator'))}"
                ),
                (
                    f"- Fraction monotone down (among scored): "
                    f"{_fmt_frac_with_counts(traj_det.get('fraction_monotone_down'), traj_det.get('monotone_down_count'), traj_det.get('monotone_denominator'))}"
                ),
                (
                    f"- Fraction mostly monotone up (<=1 reversal): "
                    f"{_fmt_frac_with_counts(traj_det.get('fraction_mostly_monotone_up'), traj_det.get('mostly_monotone_up_count'), traj_det.get('monotone_denominator'))}"
                ),
                (
                    f"- Fraction mostly monotone down (<=1 reversal): "
                    f"{_fmt_frac_with_counts(traj_det.get('fraction_mostly_monotone_down'), traj_det.get('mostly_monotone_down_count'), traj_det.get('monotone_denominator'))}"
                ),
            ]
        )
        top_det = traj_det.get("top_by_abs_trend") or []
        if top_det:
            lines.extend(["", "| Entity | Pearson | Mean score |", "|--------|---------|------------|"])
            for row in top_det[:10]:
                lines.append(
                    f"| {_md_cell(row.get('entity'))} | {row.get('pearson_stage_vs_score')} | {row.get('mean_score')} |"
                )
        _append_stage_matrix(lines, traj=traj_det, ordered_labels=ordered_stage_cols, top_n=8)
    labels = p.get("entity_labels") or {}
    if labels.get("label_counts"):
        lines.extend(["", "### Progression label counts (entities_progression_labels)", ""])
        for lab, cnt in list(labels["label_counts"].items())[:15]:
            lines.append(f"- `{lab}`: {cnt}")
    lines.append("")

    bal = src.get("panel_balance") or {}
    lines.extend(
        [
            "## Stable panel chromosome balance",
            "",
            f"- Positions counted: {bal.get('n_positions')}",
            f"- Source: {bal.get('source')}",
            f"- Max chromosome share: {bal.get('max_chrom_share')}",
        ]
    )
    fracs = bal.get("fraction_by_chromosome") or {}
    if fracs:
        top_chrom = sorted(fracs.items(), key=lambda kv: kv[1], reverse=True)[:8]
        lines.append("- Top chromosomes: " + ", ".join(f"{c}:{frac:.3f}" for c, frac in top_chrom))
    lines.append("")

    ai = report.get("ai_review")
    if isinstance(ai, dict) and ai:
        lines.extend(["## AI readiness commentary (advisory)", "", "*Deterministic verdict above is unchanged; this section is LLM-assisted QA only.*", ""])
        reg = src.get("regulatory") or {}
        lines.append(
            f"- **Primary analyte context used for AI interpretation**: `{reg.get('primary_analyte') or 'not_declared'}`"
        )
        st = ai.get("status")
        lines.append(f"- **Status**: `{st}`")
        if ai.get("model_used"):
            lines.append(f"- **Model**: `{ai.get('model_used')}`")
        if ai.get("error"):
            lines.extend(["", f"- **Error**: {ai.get('error')}", ""])
        if ai.get("credential_hint"):
            lines.extend(["", f"- **Credential hint**: {ai.get('credential_hint')}", ""])
        if ai.get("structured_parse_note"):
            lines.extend(["", f"- **Parse note**: {ai.get('structured_parse_note')}", ""])
        if ai.get("response_preview"):
            pv = str(ai.get("response_preview") or "")[:4000]
            lines.extend(["", "### Model reply (truncated)", "", "```", pv, "```", ""])
        struct = ai.get("structured")
        if isinstance(struct, dict):
            ca = struct.get("consistency_assessment")
            if ca:
                lines.append(f"- **Consistency assessment**: `{ca}`")
            for key, title in (
                ("summary_bullets", "Summary"),
                ("caveats", "Caveats"),
                ("suggested_human_checks", "Suggested human checks"),
            ):
                items = struct.get(key)
                if isinstance(items, list) and items:
                    lines.extend(["", f"### {title}", ""])
                    for item in items:
                        lines.append(f"- {item}")
            for key, title in (
                ("canonical_track_assessment", "Canonical track assessment"),
                ("variant_track_assessment", "Variant track assessment"),
                ("detailed_track_assessment", "Detailed track assessment"),
                ("track_divergence_assessment", "Track divergence assessment"),
            ):
                txt = struct.get(key)
                if isinstance(txt, str) and txt.strip():
                    lines.extend(["", f"### {title}", "", txt.strip()])
            impact = struct.get("impact_on_confidence")
            if isinstance(impact, str) and impact.strip():
                lines.extend(["", f"- **Impact on confidence**: `{impact.strip()}`"])
            dp = struct.get("disease_progression_alignment")
            if isinstance(dp, str) and dp.strip():
                lines.extend(["", "### Disease progression alignment", "", dp.strip(), ""])
            if struct.get("narrative_text") and not dp:
                lines.extend(["", "### Narrative", "", str(struct["narrative_text"])[:4000], ""])
        lines.append("")

    lines.extend(
        [
            "---",
            "*Generated by `methyl-stability-freeze-readiness`. Interpret biology with domain review; progression labels are rule-based summaries.*",
        ]
    )
    return "\n".join(lines)


def _default_readiness_dir(project_root: Path) -> Path:
    """Reports live next to Monte Carlo outputs: ``<project>/readiness/``."""
    return project_root.resolve() / "readiness"


def _resolve_report_output_path(project_root: Path, arg: Optional[Path], default_filename: str) -> Path:
    """
    Default outputs go under ``<project_root>/readiness/``.

    - If ``arg`` is omitted: ``readiness/<default_filename>``.
    - If ``arg`` is absolute: use as-is.
    - If ``arg`` is relative: treat as relative to ``readiness/`` (not the shell cwd).
    """
    rd = _default_readiness_dir(project_root)
    if arg is None:
        return rd / default_filename
    p = arg.expanduser()
    if p.is_absolute():
        return p
    return (rd / p).resolve()


def _normalize_project_root_arg(raw: Path) -> Tuple[Optional[Path], int]:
    """
    Ensure the positional argument is a project directory, not a stray config file path.

    Returns ``(resolved_directory, 0)`` or ``(None, 2)`` when the path exists and is a file
    (common mistake: passing ``.../MyProject.json`` instead of ``.../MyProject``).
    """
    # Preserve user-visible mount aliases (e.g. /work) instead of canonicalizing
    # symlinks to host-specific prefixes (e.g. /lambda/...).
    p = raw.expanduser().absolute()
    if p.exists() and p.is_file():
        lines = [
            "methyl-stability-freeze-readiness: first argument must be the PROJECT DIRECTORY "
            "that contains monte_carlo_runs/, not a JSON/config file.",
            f"Received a file: {p}",
        ]
        if p.suffix.lower() == ".json":
            cand = p.with_suffix("")
            mc = cand / "monte_carlo_runs"
            if cand.is_dir() and mc.is_dir():
                lines.append(f"Example: methyl-stability-freeze-readiness {cand}")
            elif cand.is_dir():
                lines.append(f"You may have meant the directory: {cand}")
        print("\n".join(lines), file=sys.stderr)
        return None, 2
    return p, 0


def _default_methyl_mapper_home_for_project(project_root: Path) -> Path:
    """
    Derive default MethylMapper home from the project mount root.

    Examples:
      - /work/projects/prostate-cancer/MyProject -> /work/cache/methyl_mapper
      - /work/projects/prostate-cancer/MyProject -> /work/cache/methyl_mapper
      - /tmp/Proj -> /tmp/cache/methyl_mapper

    Falls back to ~/.methyl_mapper when mount-root inference is not possible.
    """
    p = project_root.expanduser().absolute()
    parts = p.parts
    if len(parts) >= 2 and parts[0] == os.sep:
        mount = parts[1]
        if mount == "projects" and len(parts) >= 3:
            return (Path(parts[0]) / "cache" / "methyl_mapper").absolute()
        return (Path(parts[0]) / mount / "cache" / "methyl_mapper").absolute()
    anchor = str(p.anchor or "").strip()
    if anchor and anchor != os.sep:
        return (Path(anchor) / "cache" / "methyl_mapper").absolute()
    return (Path.home() / ".methyl_mapper").absolute()


def _emit_grok_stderr_summary(ai_review: Dict[str, Any]) -> None:
    """One-line stderr hint so terminal runs show Grok outcome even if markdown is easy to miss."""
    st = ai_review.get("status")
    if st == "ok":
        mu = ai_review.get("model_used") or ai_review.get("model_requested") or "?"
        print(
            f"methyl-stability-freeze-readiness: Grok advisory review OK (model_used={mu}). "
            "See markdown section \"AI readiness commentary (advisory)\".",
            file=sys.stderr,
        )
        return
    if st == "skipped_no_key":
        hint = ai_review.get("credential_hint") or ai_review.get("error") or "(no detail)"
        print(
            "methyl-stability-freeze-readiness: Grok skipped — no API key resolved. "
            f"{hint}",
            file=sys.stderr,
        )
        return
    if st == "error":
        err = ai_review.get("error") or "(unknown)"
        print(
            f"methyl-stability-freeze-readiness: Grok request failed — {err}",
            file=sys.stderr,
        )
        return
    print(
        f"methyl-stability-freeze-readiness: Grok advisory status={st!r}.",
        file=sys.stderr,
    )


def main(argv: Optional[List[str]] = None) -> int:
    from .grok_readiness import (
        DEFAULT_GROK_MODEL,
        build_sanitized_ai_payload,
        redact_report_for_export,
        resolve_grok_api_key,
        run_grok_readiness_review,
    )

    parser = argparse.ArgumentParser(
        description="Audit stability, freeze, and progression artifacts before modeling."
    )
    parser.add_argument(
        "project_root",
        type=Path,
        help="Directory containing monte_carlo_runs/ (e.g. .../Healthy_vs_PCa1-4-CG)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write full report JSON (default: <project>/readiness/readiness.json). Relative paths are under readiness/.",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=None,
        metavar="PATH",
        help="Write markdown report (default: <project>/readiness/readiness.md). Relative paths are under readiness/.",
    )
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="Do not write JSON/markdown files; print markdown to stdout only (legacy pipe-friendly mode).",
    )
    parser.add_argument(
        "--redact-paths",
        action="store_true",
        help="Omit project_root, artifact paths, and fixed_panel path from markdown and JSON export.",
    )
    grok = parser.add_argument_group("Grok advisory review (xAI)")
    grok.add_argument(
        "--no-grok-review",
        action="store_false",
        dest="grok_review",
        help="Disable Grok consistency review (default: enabled).",
    )
    grok.set_defaults(grok_review=True)
    grok.add_argument("--grok-model", type=str, default=DEFAULT_GROK_MODEL, help="xAI chat model name.")
    grok.add_argument("--grok-max-top-rows", type=int, default=10, help="Top-N rows for module trends and labels in AI payload.")
    grok.add_argument("--grok-timeout-seconds", type=float, default=60.0, help="HTTP timeout per Grok request.")
    grok.add_argument("--grok-max-retries", type=int, default=2, help="Retries on transient Grok failures.")
    grok.add_argument("--grok-temperature", type=float, default=0.1, help="Sampling temperature for Grok.")
    grok.add_argument("--grok-api-key", type=str, default=None, help="Explicit Grok API key (otherwise MethylMapper credential chain).")
    grok.add_argument(
        "--encrypted-file-path",
        type=Path,
        default=None,
        help=(
            "Encrypted Grok credential file (same as methyl-mapper --encrypted-file-path; "
            "default is inferred from --methyl-mapper-home, or auto-derived from project mount root "
            "as <root>/cache/methyl_mapper/credentials/grok_api_key.encrypted)."
        ),
    )
    grok.add_argument("--azure-key-vault-url", type=str, default=None, help="Optional Azure Key Vault URL for Grok key.")
    grok.add_argument("--azure-secret-name", type=str, default=None, help="Optional Key Vault secret name (default grok-api-key).")
    grok.add_argument(
        "--methyl-mapper-home",
        type=Path,
        default=None,
        help=(
            "Optional MethylMapper home for credential file resolution. "
            "Default auto-derives from project mount root as <root>/cache/methyl_mapper "
            "(for /work/... projects this resolves to /work/cache/methyl_mapper)."
        ),
    )
    grok.add_argument(
        "--disease-context",
        type=str,
        default=None,
        help="Override disease label sent to Grok (default: production project mapper disease_term).",
    )
    grok.add_argument(
        "--include-ai-raw-response",
        action="store_true",
        help="Include truncated raw Grok text in ai_review (default: off).",
    )
    grok.add_argument("--ai-raw-response-max-chars", type=int, default=2000, help="Max chars when --include-ai-raw-response.")
    args = parser.parse_args(argv)

    proj_root, arg_rc = _normalize_project_root_arg(args.project_root)
    if arg_rc != 0:
        return arg_rc
    args.project_root = proj_root

    require_enricher = False
    prod_json = args.project_root / "monte_carlo_runs" / "production" / "project.json"
    if prod_json.is_file():
        try:
            from methyl_utils import load_project
            from methyl_utils.action_config_resolver import resolve_for_project

            prod_project = load_project(prod_json)
            val_cfg = resolve_for_project("validation", prod_project)
            require_enricher = bool(val_cfg.get("require_complete_enricher", False))
        except Exception:
            require_enricher = False

    report = analyze_project_root(
        args.project_root,
        require_complete_enricher=require_enricher,
    )

    ai_review: Optional[Dict[str, Any]] = None
    if getattr(args, "grok_review", True):
        default_mapper_home = (
            args.methyl_mapper_home.expanduser()
            if args.methyl_mapper_home
            else _default_methyl_mapper_home_for_project(args.project_root)
        )
        disease = (args.disease_context or "").strip() or report.get("disease_context")
        payload = build_sanitized_ai_payload(
            report,
            disease_context=str(disease) if disease else None,
            top_n=max(1, int(args.grok_max_top_rows)),
        )
        api_key, key_hint = resolve_grok_api_key(
            explicit_key=args.grok_api_key,
            azure_key_vault_url=(args.azure_key_vault_url or "").strip() or None,
            azure_secret_name=(args.azure_secret_name or "").strip() or None,
            methyl_mapper_home=default_mapper_home,
            encrypted_file_path=args.encrypted_file_path.expanduser() if args.encrypted_file_path else None,
        )
        if not api_key:
            ai_review = {
                "status": "skipped_no_key",
                "error": "No Grok API key resolved (set GROK_API_KEY, use methyl_mapper_credentials save, or pass --grok-api-key).",
                "credential_hint": key_hint,
                "advisory_only": True,
            }
        else:
            ai_review = run_grok_readiness_review(
                sanitized_payload=payload,
                api_key=api_key,
                model=args.grok_model,
                temperature=float(args.grok_temperature),
                timeout_seconds=float(args.grok_timeout_seconds),
                max_retries=int(args.grok_max_retries),
                include_raw_response=bool(args.include_ai_raw_response),
                raw_max_chars=int(args.ai_raw_response_max_chars),
            )
        report["ai_review"] = ai_review
        report["ai_review_payload_meta"] = {
            "sanitized": True,
            "top_n": max(1, int(args.grok_max_top_rows)),
            "model_requested": args.grok_model,
        }
        _emit_grok_stderr_summary(ai_review)
    else:
        report["ai_review"] = {"status": "disabled", "advisory_only": True}
        print(
            "methyl-stability-freeze-readiness: Grok advisory review disabled (--no-grok-review).",
            file=sys.stderr,
        )

    export_doc = copy.deepcopy(report)
    if args.redact_paths:
        export_doc = redact_report_for_export(export_doc)

    text = render_markdown(report, redact_paths=bool(args.redact_paths))

    if not args.stdout_only:
        json_path = _resolve_report_output_path(args.project_root, args.json_out, "readiness.json")
        md_path = _resolve_report_output_path(args.project_root, args.markdown_out, "readiness.md")

        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(export_doc, jf, indent=2, default=str)

        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(text, encoding="utf-8")

        print(
            f"methyl-stability-freeze-readiness: wrote JSON → {json_path}\n"
            f"methyl-stability-freeze-readiness: wrote markdown → {md_path}",
            file=sys.stderr,
        )
    print(text)
    return 0 if report.get("verdict", {}).get("overall") != "no_go" else 2


if __name__ == "__main__":
    sys.exit(main())
