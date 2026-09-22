"""
Gene FeatureCuts for MC stability iterations.

After detector (DMP FeatureCuts) and methyl-mapper, selects a discriminatory gene panel
via ECDF OvR prefix search on validation balanced accuracy.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from methyl_predictor.project_resolver import resolve_predictor_config
from methyl_utils import load_project
from methyl_utils.ecdf_aggregated_ovr import (
    predict_aggregated_ecdf_ovr_proba,
    train_aggregated_ecdf_ovr_package,
    GENE_ECDF_OVR_TYPE,
)

from .classification_metrics import compute_validation_metrics, resolve_class_roles
from .eval_split_resolver import _project_cwd
from .raw_gene_features import (
    _normalize_gene_name,
    build_gene_panel_feature_weights,
    build_raw_gene_feature_table,
)
logger = logging.getLogger(__name__)

GENE_STABILITY_DIR = "gene_stability"
GENES_CLASSIFIER_CSV = "genes-classifier.csv"
GENE_FEATURECUTS_METRICS_JSON = "gene_featurecuts_metrics.json"
GENE_DMP_LOCI_CSV = "gene-dmp-loci.csv"
BIOMARKER_PPI_HUBS_CSV = "biomarker_ppi_hubs.csv"


def _load_train_paths_and_labels(project_json: Path) -> Tuple[List[str], np.ndarray, List[str]]:
    with _project_cwd(project_json):
        project = load_project(project_json)
    roles = resolve_class_roles(project)
    class_names = list(roles["class_names"])
    paths: List[str] = []
    labels: List[int] = []
    for cls_idx, (_label, group_paths) in enumerate(project.get_resolved_groups()):
        for p in group_paths or []:
            paths.append(str(p))
            labels.append(int(cls_idx))
    if not paths:
        raise ValueError(f"No training samples resolved from {project_json}")
    return paths, np.asarray(labels, dtype=np.int32), class_names


def _load_dmp_panel_for_gene_featurecuts(
    run_dir: Path,
    config: Any,
) -> tuple[Optional[pd.DataFrame], str]:
    """Resolve DMP panel for gene features: discovery, selected/classifier, or stable panel."""
    from .dmp_panel import (
        load_classifier_dmp_panel,
        load_discovery_dmp_panel,
        load_selected_dmp_panel,
        load_stable_dmp_panel,
    )

    max_dmps = getattr(config, "stability_gene_featurecuts_max_dmps", None)
    loci = getattr(config, "gene_featurecuts_loci_source", None)
    source = str(
        loci
        or getattr(config, "stability_gene_featurecuts_dmp_source", "discovery")
        or "discovery"
    ).strip().lower()
    if source in ("stable", "stable_panel"):
        stable_csv = getattr(config, "freeze_stable_dmp_csv", None)
        dmp_df = load_stable_dmp_panel(run_dir, stable_csv=stable_csv, max_dmps=max_dmps)
        if dmp_df is not None and not dmp_df.empty:
            return dmp_df, "stable"
    if source in ("classifier", "featurecuts_selected", "selected"):
        dmp_df = load_selected_dmp_panel(run_dir, max_dmps=max_dmps)
        if dmp_df is not None and not dmp_df.empty:
            return dmp_df, "classifier"
        return load_classifier_dmp_panel(run_dir, max_dmps=max_dmps), "classifier"
    dmp_df = load_discovery_dmp_panel(run_dir, max_dmps=max_dmps)
    if dmp_df is not None and not dmp_df.empty:
        return dmp_df, "discovery"
    return load_classifier_dmp_panel(run_dir, max_dmps=max_dmps), "classifier"


def _load_validation_paths_and_labels(
    project_json: Path,
    class_names: Sequence[str],
) -> Tuple[List[str], np.ndarray]:
    predictor_cfg = resolve_predictor_config(project_json)
    samples: List[str] = []
    y_true: List[int] = []

    test_group_paths = getattr(predictor_cfg, "test_group_paths", None)
    holdout_group_paths = getattr(predictor_cfg, "holdout_group_paths", None)
    holdout_control_paths = list(getattr(predictor_cfg, "holdout_control_paths", []) or [])
    holdout_disease_paths = list(getattr(predictor_cfg, "holdout_disease_paths", []) or [])
    test_control_paths = list(getattr(predictor_cfg, "test_control_paths", []) or [])
    test_disease_paths = list(getattr(predictor_cfg, "test_disease_paths", []) or [])

    if test_group_paths:
        for idx, entry in enumerate(test_group_paths):
            cls_idx = int(entry.get("class_index", idx))
            for p in entry.get("paths") or []:
                samples.append(str(p))
                y_true.append(cls_idx)
    elif holdout_group_paths:
        for idx, entry in enumerate(holdout_group_paths):
            cls_idx = int(entry.get("class_index", idx))
            for p in entry.get("paths") or []:
                samples.append(str(p))
                y_true.append(cls_idx)
    elif holdout_control_paths or holdout_disease_paths:
        samples = holdout_control_paths + holdout_disease_paths
        y_true = [0] * len(holdout_control_paths) + [1] * len(holdout_disease_paths)
    elif test_control_paths or test_disease_paths:
        samples = test_control_paths + test_disease_paths
        y_true = [0] * len(test_control_paths) + [1] * len(test_disease_paths)
    else:
        run_dir = project_json.parent
        groups_json = run_dir / "test_groups.json"
        if not groups_json.is_file():
            groups_json = run_dir / "val_test_groups.json"
        if groups_json.is_file():
            with open(groups_json, encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, list):
                name_to_idx = {str(n): int(i) for i, n in enumerate(class_names)}
                for entry in payload:
                    if not isinstance(entry, dict):
                        continue
                    lbl = str(entry.get("label") or "")
                    cls_idx = name_to_idx.get(lbl)
                    if cls_idx is None:
                        continue
                    for p in entry.get("paths") or []:
                        samples.append(str(p))
                        y_true.append(int(cls_idx))
        if not samples:
            from .split import load_and_resolve_sample_paths

            tc = run_dir / "test_control.csv"
            td = run_dir / "test_disease.csv"
            if not (tc.is_file() and td.is_file()):
                tc = run_dir / "val_control.csv"
                td = run_dir / "val_disease.csv"
            if tc.is_file() and td.is_file():
                test_control = load_and_resolve_sample_paths(tc, None)
                test_disease = load_and_resolve_sample_paths(td, None)
                samples = [str(p) for p in test_control] + [str(p) for p in test_disease]
                y_true = [0] * len(test_control) + [1] * len(test_disease)

    if not samples:
        raise ValueError(
            f"No validation/holdout samples resolved from {project_json} for gene FeatureCuts"
        )
    return samples, np.asarray(y_true, dtype=np.int32)


def _load_mapper_gene_combined_tables(run_dir: Path) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for csv in sorted(run_dir.glob("**/mapper/**/all-gene_name-combined.csv")):
        try:
            df = pd.read_csv(csv)
        except Exception:
            continue
        if df.empty or "gene_name" not in df.columns:
            continue
        work = df.copy()
        work["comparison_label"] = str(csv.parent.name)
        frames.append(work)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _load_mapper_intersections(run_dir: Path) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for csv in sorted(run_dir.glob("**/mapper/**/*-intersections.csv")):
        try:
            df = pd.read_csv(csv)
        except Exception:
            continue
        if df.empty or "gene_name" not in df.columns:
            continue
        work = df.copy()
        work["comparison_label"] = str(csv.parent.name)
        frames.append(work)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _normalize_chromosome_token(chrom: Any) -> str:
    token = str(chrom or "").strip()
    if token.lower().startswith("chr"):
        return token[3:]
    return token


def _parse_intersection_positions(inter: pd.DataFrame) -> pd.Series:
    """Resolve DMP positions from explicit columns or methyl-mapper ``dmp_name`` tokens."""
    for candidate in ("position", "dmp_pos", "dmp_position"):
        if candidate in inter.columns:
            return pd.to_numeric(inter[candidate], errors="coerce")
    if "dmp_name" not in inter.columns:
        return pd.Series(np.nan, index=inter.index)

    def _pos_from_dmp_name(name: Any) -> float:
        parts = str(name or "").split(":")
        if len(parts) < 2:
            return np.nan
        try:
            return float(int(parts[1]))
        except (TypeError, ValueError):
            return np.nan

    return inter["dmp_name"].map(_pos_from_dmp_name)


def _cap_ranked_gene_pool(
    ranked_genes: List[str],
    gene_panel: pd.DataFrame,
    max_genes: Optional[int],
) -> Tuple[List[str], pd.DataFrame]:
    if max_genes is None or int(max_genes) <= 0:
        return ranked_genes, gene_panel
    capped = ranked_genes[: int(max_genes)]
    keep = set(capped)
    panel = gene_panel[gene_panel["gene_name"].astype(str).isin(keep)].copy()
    rank_map = {g: i for i, g in enumerate(capped)}
    panel["_rank"] = panel["gene_name"].map(rank_map)
    panel = panel.sort_values("_rank").drop(columns=["_rank"]).reset_index(drop=True)
    return capped, panel


def _normalize_dmp_frame(dmp_df: pd.DataFrame) -> pd.DataFrame:
    work = dmp_df.copy()
    rename_map: Dict[str, str] = {}
    if "chrom" in work.columns and "chromosome" not in work.columns:
        rename_map["chrom"] = "chromosome"
    if "pos" in work.columns and "position" not in work.columns:
        rename_map["pos"] = "position"
    if rename_map:
        work = work.rename(columns=rename_map)
    if "context" not in work.columns:
        work["context"] = "CG"
    if "effect_size" not in work.columns:
        work["effect_size"] = 0.0
    if "region_weight" not in work.columns:
        work["region_weight"] = 1.0
    work["chromosome"] = work["chromosome"].map(_normalize_chromosome_token)
    work["context"] = work["context"].astype(str)
    work["position"] = pd.to_numeric(work["position"], errors="coerce").fillna(-1).astype(int)
    work = work[work["position"] >= 0].copy()
    return work


def _annotate_dmps_with_genes(dmp_df: pd.DataFrame, intersections: pd.DataFrame) -> pd.DataFrame:
    work = _normalize_dmp_frame(dmp_df)
    if intersections.empty:
        work["gene_name"] = "unknown"
        return work
    inter = intersections.copy()
    if "feature_chrom" in inter.columns and "chromosome" not in inter.columns:
        inter["chromosome"] = inter["feature_chrom"]
    inter["chromosome"] = inter["chromosome"].map(_normalize_chromosome_token)
    inter["position"] = _parse_intersection_positions(inter)
    if "context" not in inter.columns:
        inter["context"] = "CG"
    inter["context"] = inter["context"].astype(str)
    inter["gene_name"] = inter["gene_name"].astype(str)
    inter = inter[inter["position"].notna()].copy()
    inter["position"] = inter["position"].astype(int)
    merged = work.merge(
        inter[["chromosome", "position", "context", "gene_name"]].drop_duplicates(
            subset=["chromosome", "position", "context", "gene_name"],
            keep="first",
        ),
        on=["chromosome", "position", "context"],
        how="left",
        suffixes=("", "_mapper"),
    )
    gene_col = "gene_name_mapper" if "gene_name_mapper" in merged.columns else "gene_name"
    if gene_col in merged.columns:
        merged["gene_name"] = merged[gene_col].fillna("unknown").astype(str)
    else:
        merged["gene_name"] = "unknown"
    return merged.drop(columns=[c for c in merged.columns if c.endswith("_mapper")], errors="ignore")


def _rank_gene_pool(gene_combined: pd.DataFrame) -> Tuple[List[str], pd.DataFrame]:
    if gene_combined.empty:
        return [], pd.DataFrame()
    work = gene_combined.copy()
    work["gene_name"] = work["gene_name"].map(_normalize_gene_name)
    work = work[work["gene_name"] != ""].copy()
    for col in ("gene_importance", "gene_support_n"):
        if col not in work.columns:
            work[col] = np.nan
    work["gene_importance"] = pd.to_numeric(work["gene_importance"], errors="coerce").fillna(0.0)
    work["gene_support_n"] = pd.to_numeric(work["gene_support_n"], errors="coerce").fillna(0).astype(int)
    grouped = (
        work.groupby("gene_name", as_index=False)
        .agg(
            gene_importance=("gene_importance", "max"),
            gene_support_n=("gene_support_n", "max"),
        )
        .sort_values(
            ["gene_importance", "gene_support_n", "gene_name"],
            ascending=[False, False, True],
        )
        .reset_index(drop=True)
    )
    ranked = [str(g) for g in grouped["gene_name"].tolist()]
    return ranked, grouped


def _apply_biomarker_gene_pool_filter(
    gene_combined: pd.DataFrame,
    *,
    project_json: Path,
    config: Any,
    out_dir: Path,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    from .biomarker_gene_pool import (
        BIOMARKER_PPI_HUBS_CSV,
        build_biomarker_gene_pool,
        load_enricher_config_from_project,
        resolve_biomarker_ppi_cache_path,
        resolve_biomarker_ppi_score_threshold,
    )

    enricher_config = load_enricher_config_from_project(project_json)
    mode = str(getattr(config, "stability_gene_biomarker_mode", "ppi_only") or "ppi_only")
    region_hits = getattr(config, "stability_gene_region_hits", None)
    top_genes = int(getattr(config, "stability_gene_biomarker_top_genes", 150) or 150)
    ppi_top_hubs = int(getattr(config, "stability_gene_biomarker_ppi_top_hubs", 100) or 100)
    _min_degree = getattr(config, "stability_gene_biomarker_min_degree", 1)
    min_degree = 1 if _min_degree is None else int(_min_degree)
    cache_path = resolve_biomarker_ppi_cache_path(config, enricher_config)
    score_threshold = resolve_biomarker_ppi_score_threshold(enricher_config)

    pool_genes, hubs_df, bio_meta = build_biomarker_gene_pool(
        gene_combined,
        enricher_config=enricher_config,
        mode=mode,
        region_hits=region_hits,
        top_genes=top_genes,
        ppi_top_hubs=ppi_top_hubs,
        min_degree=min_degree,
        cache_path=cache_path,
        score_threshold=score_threshold,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    if hubs_df is not None and not hubs_df.empty:
        hubs_df.to_csv(out_dir / BIOMARKER_PPI_HUBS_CSV, index=False)

    biomarker_meta: Dict[str, Any] = {
        "enabled": True,
        "mode": mode,
        "region_hits": list(region_hits) if region_hits else [],
        "biomarker_pool_size": int(len(pool_genes)),
        "ppi_cache_path": cache_path,
        "ppi_score_threshold": float(score_threshold),
        **bio_meta,
    }

    if not pool_genes:
        return gene_combined.iloc[0:0].copy(), biomarker_meta

    pool_set = {str(g).strip().upper() for g in pool_genes}
    work = gene_combined.copy()
    work["_gene_upper"] = work["gene_name"].astype(str).str.strip().str.upper()
    filtered = work[work["_gene_upper"].isin(pool_set)].copy()
    rank_map = {g.upper(): i for i, g in enumerate(pool_genes)}
    filtered["_bio_rank"] = filtered["_gene_upper"].map(rank_map)
    filtered = filtered.sort_values("_bio_rank", na_position="last").drop(
        columns=["_gene_upper", "_bio_rank"], errors="ignore"
    )
    biomarker_meta["n_genes_in_mapper_after_intersect"] = int(len(filtered))
    return filtered, biomarker_meta


def _evaluate_gene_prefix(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    class_names: List[str],
    feature_names: List[str],
    gene_panel: pd.DataFrame,
    k: int,
    n_bins: int = 100,
) -> Tuple[float, Dict[str, Any]]:
    if k <= 0 or X_train.shape[1] == 0:
        return 0.0, {}
    k = min(int(k), X_train.shape[1])
    X_tr = np.asarray(X_train[:, :k], dtype=np.float64)
    X_va = np.asarray(X_val[:, :k], dtype=np.float64)
    names = feature_names[:k]
    weights = build_gene_panel_feature_weights(gene_panel, names, weight_column="gene_importance")
    package = train_aggregated_ecdf_ovr_package(
        X_tr,
        y_train,
        class_names=class_names,
        feature_names=names,
        feature_weights=weights,
        feature_family_set="gene_scored",
        feature_mode="raw_gene",
        n_bins=int(max(8, n_bins)),
        temperature=1.0,
        package_metadata={"gene_featurecuts": True},
        classifier_type=GENE_ECDF_OVR_TYPE,
    )
    probs, _ = predict_aggregated_ecdf_ovr_proba(package, X_va)
    pred = np.argmax(probs, axis=1).astype(int)
    class_roles = {
        "class_names": class_names,
        "control_class_index": 0,
        "disease_class_indices": list(range(1, len(class_names))),
    }
    metrics = compute_validation_metrics(y_val, pred, class_names, class_roles=class_roles)
    ba = float(metrics.get("balanced_accuracy", 0.0))
    return ba, metrics


def _search_gene_k(
    *,
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    class_names: List[str],
    feature_names: List[str],
    gene_panel: pd.DataFrame,
    target_ba: Optional[float],
    min_genes: Optional[int],
    max_k: int,
) -> Tuple[int, float, Dict[str, Any]]:
    max_k = int(max(1, min(max_k, X_train.shape[1])))
    min_k = int(max(1, min_genes or 1))
    min_k = min(min_k, max_k)

    best_k = min_k
    best_ba = -1.0
    best_metrics: Dict[str, Any] = {}

    if target_ba is not None:
        target = float(target_ba)
        lo, hi = min_k, max_k
        found_k: Optional[int] = None
        found_ba = -1.0
        found_metrics: Dict[str, Any] = {}
        while lo <= hi:
            mid = (lo + hi) // 2
            ba, metrics = _evaluate_gene_prefix(
                X_train, y_train, X_val, y_val, class_names, feature_names, gene_panel, mid
            )
            if ba >= target:
                found_k = mid
                found_ba = ba
                found_metrics = metrics
                hi = mid - 1
            else:
                lo = mid + 1
        if found_k is not None:
            best_k, best_ba, best_metrics = found_k, found_ba, found_metrics
        else:
            for k in range(min_k, max_k + 1):
                ba, metrics = _evaluate_gene_prefix(
                    X_train, y_train, X_val, y_val, class_names, feature_names, gene_panel, k
                )
                if ba > best_ba:
                    best_k, best_ba, best_metrics = k, ba, metrics
    else:
        for k in range(min_k, max_k + 1):
            ba, metrics = _evaluate_gene_prefix(
                X_train, y_train, X_val, y_val, class_names, feature_names, gene_panel, k
            )
            if ba > best_ba:
                best_k, best_ba, best_metrics = k, ba, metrics

    if min_genes is not None and best_k < int(min_genes):
        best_k = min(int(min_genes), max_k)
        best_ba, best_metrics = _evaluate_gene_prefix(
            X_train, y_train, X_val, y_val, class_names, feature_names, gene_panel, best_k
        )

    return int(best_k), float(best_ba), best_metrics


def run_gene_featurecuts_for_iteration(
    project_json: str | Path,
    config: Any,
    *,
    run_dir: Optional[Path] = None,
) -> Tuple[int, str, str]:
    """
    Run gene FeatureCuts for one MC iteration. Returns (rc, stdout_msg, stderr_msg).
    """
    project_json = Path(project_json).resolve()
    run_dir = Path(run_dir).resolve() if run_dir is not None else project_json.parent
    warnings: List[str] = []

    dmp_df, dmp_source = _load_dmp_panel_for_gene_featurecuts(run_dir, config)
    if dmp_df is None or dmp_df.empty:
        return 1, "", (
            "Gene FeatureCuts: no DMP exports found in run directory "
            "(expected dmps-*-discovery.csv for discovery mode, or classifier exports as fallback)"
        )

    gene_combined = _load_mapper_gene_combined_tables(run_dir)
    intersections = _load_mapper_intersections(run_dir)
    if gene_combined.empty:
        return 1, "", "Gene FeatureCuts: mapper all-gene_name-combined.csv not found (run methyl-mapper first)"

    biomarker_meta: Dict[str, Any] = {"enabled": False}
    pre_biomarker_size = int(len(gene_combined))
    if bool(getattr(config, "stability_gene_biomarker_filter_enabled", False)):
        out_dir = run_dir / GENE_STABILITY_DIR
        try:
            gene_combined, biomarker_meta = _apply_biomarker_gene_pool_filter(
                gene_combined,
                project_json=project_json,
                config=config,
                out_dir=out_dir,
            )
        except ValueError as exc:
            return 1, "", f"Gene FeatureCuts biomarker filter failed: {exc}"
        if gene_combined.empty:
            return 1, "", (
                "Gene FeatureCuts: biomarker gene pool is empty after disease/PPI filters. "
                "Relax step_config.enricher filters (disease_only, min_dmp_count) or "
                "stability_gene_region_hits."
            )

    ranked_genes, gene_panel = _rank_gene_pool(gene_combined)
    if not ranked_genes:
        return 1, "", "Gene FeatureCuts: empty ranked gene pool from mapper outputs"

    ranked_genes, gene_panel = _cap_ranked_gene_pool(
        ranked_genes,
        gene_panel,
        getattr(config, "stability_gene_featurecuts_max_genes", None),
    )
    if not ranked_genes:
        return 1, "", "Gene FeatureCuts: empty gene pool after max_genes cap"

    annotated_dmp = _annotate_dmps_with_genes(dmp_df, intersections)
    panel_for_features = gene_panel.copy()

    try:
        train_paths, y_train, class_names = _load_train_paths_and_labels(project_json)
        val_paths, y_val = _load_validation_paths_and_labels(project_json, class_names)
    except Exception as e:
        return 1, "", f"Gene FeatureCuts split resolution failed: {e}"

    feat_train = build_raw_gene_feature_table(
        train_paths,
        annotated_dmp,
        panel_for_features,
        gene_name_order=ranked_genes,
    )
    feat_val = build_raw_gene_feature_table(
        val_paths,
        annotated_dmp,
        panel_for_features,
        gene_name_order=ranked_genes,
    )
    if feat_train.X.shape[1] == 0:
        return 1, "", "Gene FeatureCuts: no gene features could be built from DMP/mapper annotations"

    target_ba = getattr(config, "gene_featurecuts_target_ba", None)
    if target_ba is None:
        target_ba = getattr(config, "stability_target_balanced_accuracy", None)
    min_genes = getattr(config, "stability_min_selected_genes", None)
    best_k, best_ba, best_metrics = _search_gene_k(
        X_train=np.asarray(feat_train.X, dtype=np.float64),
        y_train=y_train,
        X_val=np.asarray(feat_val.X, dtype=np.float64),
        y_val=y_val,
        class_names=class_names,
        feature_names=list(feat_train.feature_names),
        gene_panel=panel_for_features,
        target_ba=float(target_ba) if target_ba is not None else None,
        min_genes=int(min_genes) if min_genes is not None else None,
        max_k=int(feat_train.X.shape[1]),
    )

    selected_genes = ranked_genes[:best_k]
    selected_panel = gene_panel[gene_panel["gene_name"].isin(selected_genes)].copy()
    selected_panel["rank"] = selected_panel["gene_name"].map(
        {g: i + 1 for i, g in enumerate(selected_genes)}
    )
    selected_panel = selected_panel.sort_values("rank").reset_index(drop=True)

    out_dir = run_dir / GENE_STABILITY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_panel.to_csv(out_dir / GENES_CLASSIFIER_CSV, index=False)

    loci_out = annotated_dmp[
        annotated_dmp["gene_name"].astype(str).isin(set(selected_genes))
    ].copy()
    loci_out.to_csv(out_dir / GENE_DMP_LOCI_CSV, index=False)

    metrics_payload = {
        "selected_k": int(best_k),
        "balanced_accuracy": float(best_ba),
        "class_names": class_names,
        "n_train_samples": int(len(train_paths)),
        "n_val_samples": int(len(val_paths)),
        "target_balanced_accuracy": float(target_ba) if target_ba is not None else None,
        "min_selected_genes": int(min_genes) if min_genes is not None else None,
        "warnings": warnings,
        "validation_metrics": best_metrics,
        "ranked_gene_pool_size": int(len(ranked_genes)),
        "stability_gene_featurecuts_max_genes": getattr(
            config, "stability_gene_featurecuts_max_genes", None
        ),
        "biomarker_filter": biomarker_meta,
        "n_mapper_genes_before_biomarker_filter": pre_biomarker_size,
        "dmp_panel_source": dmp_source,
        "n_dmp_loci_for_features": int(len(dmp_df)),
    }
    with open(out_dir / GENE_FEATURECUTS_METRICS_JSON, "w", encoding="utf-8") as f:
        json.dump(metrics_payload, f, indent=2)

    msg = (
        f"Gene FeatureCuts selected k={best_k} genes (BA={best_ba:.4f}) "
        f"-> {out_dir / GENES_CLASSIFIER_CSV}"
    )
    return 0, msg, ""
