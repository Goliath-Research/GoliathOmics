"""Run cell-type deconvolution (flat Houseman or hierarchical HiTIMED) for project samples."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from ..config import CellDeconvRuntimeParams, CellDeconvStepConfig
from .houseman import deconvolve_sample, load_seed_basis
from .hitimed import (
    HierarchyBasis,
    deconvolve_sample_hierarchical,
    load_hierarchy_basis,
)


def _run_houseman(
    samples: Sequence[Tuple[str, str, str]],
    output_dir: Path,
    cfg: CellDeconvStepConfig,
    runtime: CellDeconvRuntimeParams,
) -> Dict[str, Any]:
    basis = load_seed_basis(cfg.seed_basis_path)
    id_col = runtime.sample_id_column

    rows: List[Dict[str, Any]] = []
    for sample_id, sample_dir, group in samples:
        props = deconvolve_sample(sample_dir, basis, runtime)
        row: Dict[str, Any] = {id_col: str(sample_id), "group": str(group)}
        for ct in basis.cell_types:
            row[ct] = props.get(ct)
        row["n_markers_observed"] = props.get("n_markers_observed")
        row["marker_fraction"] = props.get("marker_fraction")
        row["qp_status"] = props.get("qp_status")
        rows.append(row)

    df = pd.DataFrame(rows)
    ordered = [
        id_col,
        "group",
        *list(basis.cell_types),
        "n_markers_observed",
        "marker_fraction",
        "qp_status",
    ]
    df = df.reindex(columns=ordered)
    csv_path = output_dir / "cell_fractions.csv"
    df.to_csv(csv_path, index=False)

    n_ok = int((df["qp_status"] == "ok").sum()) if "qp_status" in df.columns else 0
    return {
        "method": "houseman",
        "output_csv": str(csv_path.resolve()),
        "n_samples": int(len(rows)),
        "n_columns": int(len(df.columns)),
        "n_ok": n_ok,
        "contexts": list(runtime.contexts),
        "cell_types": list(basis.cell_types),
        "seed_basis": basis.provenance,
        "marker_min_coverage": runtime.marker_min_coverage,
        "min_marker_fraction": runtime.min_marker_fraction,
        "use_gpu": runtime.use_gpu,
    }


def _run_hitimed(
    samples: Sequence[Tuple[str, str, str]],
    output_dir: Path,
    cfg: CellDeconvStepConfig,
    runtime: CellDeconvRuntimeParams,
) -> Dict[str, Any]:
    basis: HierarchyBasis = load_hierarchy_basis(cfg.hierarchy_basis_path)
    root = basis.root_for_analyte(runtime.analyte)
    leaf_types = basis.leaf_order(root)
    id_col = runtime.sample_id_column

    rows: List[Dict[str, Any]] = []
    for sample_id, sample_dir, group in samples:
        props = deconvolve_sample_hierarchical(sample_dir, basis, root, runtime)
        row: Dict[str, Any] = {id_col: str(sample_id), "group": str(group)}
        for ct in leaf_types:
            row[ct] = props.get(ct)
        row["n_markers_observed"] = props.get("n_markers_observed")
        row["marker_fraction"] = props.get("marker_fraction")
        row["qp_status"] = props.get("qp_status")
        rows.append(row)

    df = pd.DataFrame(rows)
    ordered = [
        id_col,
        "group",
        *list(leaf_types),
        "n_markers_observed",
        "marker_fraction",
        "qp_status",
    ]
    df = df.reindex(columns=ordered)
    csv_path = output_dir / "cell_fractions.csv"
    df.to_csv(csv_path, index=False)

    n_ok = int((df["qp_status"] == "ok").sum()) if "qp_status" in df.columns else 0
    return {
        "method": "hitimed",
        "analyte": runtime.analyte,
        "tree_root": root,
        "output_csv": str(csv_path.resolve()),
        "n_samples": int(len(rows)),
        "n_columns": int(len(df.columns)),
        "n_ok": n_ok,
        "contexts": list(runtime.contexts),
        "cell_types": list(leaf_types),
        "hierarchy_basis": basis.provenance,
        "marker_min_coverage": runtime.marker_min_coverage,
        "min_marker_fraction": runtime.min_marker_fraction,
        "use_gpu": runtime.use_gpu,
    }


def _is_plant_analyte(analyte: Optional[str]) -> bool:
    key = str(analyte or "").strip().lower()
    if not key:
        return False
    try:
        from methyl_utils.analyte_profiles import normalize_primary_analyte

        return normalize_primary_analyte(key) == "plant_tissue"
    except Exception:
        return key in {"plant_tissue", "plant", "leaf", "root", "meristem", "seed"}


def _require_plant_atlas_paths(cfg: CellDeconvStepConfig, runtime: CellDeconvRuntimeParams) -> None:
    """Plant tissue must never fall back to packaged human-blood atlases."""
    if not _is_plant_analyte(runtime.analyte):
        return
    if runtime.method == "hitimed":
        if not (cfg.hierarchy_basis_path and str(cfg.hierarchy_basis_path).strip()):
            raise ValueError(
                "plant_tissue HiTIMED deconvolution requires hierarchy_basis_path "
                "(operator-supplied plant atlas JSON). Packaged blood hierarchy "
                "fallback is not allowed."
            )
        return
    if not (cfg.seed_basis_path and str(cfg.seed_basis_path).strip()):
        raise ValueError(
            "plant_tissue Houseman deconvolution requires seed_basis_path "
            "(operator-supplied plant atlas JSON). Packaged FlowSorted blood "
            "fallback is not allowed."
        )


def run_cell_deconv_for_samples(
    samples: Sequence[Tuple[str, str, str]],
    output_dir: Path,
    cfg: CellDeconvStepConfig,
) -> Dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime = cfg.require_runtime()
    _require_plant_atlas_paths(cfg, runtime)

    if runtime.method == "hitimed":
        manifest = _run_hitimed(samples, output_dir, cfg, runtime)
    else:
        manifest = _run_houseman(samples, output_dir, cfg, runtime)

    manifest_path = output_dir / "cell_fractions.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
