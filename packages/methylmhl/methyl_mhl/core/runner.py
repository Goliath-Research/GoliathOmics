"""Orchestrate MHB discovery / locked BED + per-sample MHL matrix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd

from methyl_utils.core.mhap_io import discover_mhap_files

from ..config import MhbMhlStepConfig
from .discovery import (
    blocks_from_bed,
    discover_mhbs,
    load_bed_intervals,
    load_stores_for_sample,
)
from .mhl import mhl_matrix_for_sample


def run_mhb_mhl_for_project(
    samples: Sequence[Tuple[str, str, str]],
    output_dir: Path,
    cfg: MhbMhlStepConfig,
    *,
    project_chromosomes: Sequence[str] | None = None,
) -> Dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    chromosomes = [str(c) for c in (cfg.chromosomes or project_chromosomes or [])]
    context = str(cfg.context or "CG")
    mode = str(cfg.mode or "discovery").strip().lower()

    any_mhap = False
    for _sid, sample_dir, _group in samples:
        chroms_probe = chromosomes or [
            p.name.split("-")[0]
            for p in Path(sample_dir).glob(f"*-{context}.mhap.h5")
        ]
        if discover_mhap_files(sample_dir, chroms_probe, context):
            any_mhap = True
            break
    if not any_mhap:
        manifest = {
            "status": "skipped",
            "reason": "no_mhap_sidecars",
            "mhl_matrix": None,
            "mhb_bed": None,
        }
        (output_dir / "mhb_mhl.manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return manifest

    control_intervals = load_bed_intervals(cfg.control_bed) if cfg.control_bed else None

    if mode == "locked":
        if not cfg.mhb_bed:
            raise ValueError("mhb_mhl mode=locked requires actionConfig.mhb_mhl.mhb_bed")
        blocks = blocks_from_bed(cfg.mhb_bed)
    else:
        r2_min = cfg.r2_min
        p_max = cfg.p_max
        core_window = cfg.core_window
        min_cpgs = cfg.min_cpgs
        min_median_reads = cfg.min_median_reads
        missing = [
            name
            for name, val in (
                ("r2_min", r2_min),
                ("p_max", p_max),
                ("core_window", core_window),
                ("min_cpgs", min_cpgs),
                ("min_median_reads", min_median_reads),
            )
            if val is None
        ]
        if missing:
            raise ValueError(
                "mhb_mhl discovery requires operator-set "
                + ", ".join(missing)
                + " (procedure/profile actionConfig.mhb_mhl)"
            )
        frames: List[pd.DataFrame] = []
        chroms = chromosomes or sorted(
            {
                p.name.split("-")[0]
                for _sid, sample_dir, _g in samples
                for p in Path(sample_dir).glob(f"*-{context}.mhap.h5")
            }
        )
        for chrom in chroms:
            stores = []
            for _sid, sample_dir, _g in samples:
                stores.extend(load_stores_for_sample(sample_dir, [chrom], context))
            if not stores:
                continue
            frames.append(
                discover_mhbs(
                    stores,
                    r2_min=float(r2_min),
                    p_max=float(p_max),
                    core_window=int(core_window),
                    min_cpgs=int(min_cpgs),
                    min_median_reads=int(min_median_reads),
                    control_intervals=control_intervals,
                    chrom=str(chrom),
                )
            )
        blocks = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            columns=["chrom", "start", "end", "name", "n_cpgs", "median_reads", "mean_r2"]
        )

    bed_path = output_dir / "mhb.bed"
    if not blocks.empty:
        blocks[["chrom", "start", "end", "name"]].to_csv(
            bed_path, sep="\t", header=False, index=False
        )
    else:
        bed_path.write_text("", encoding="utf-8")
    qc_path = output_dir / "mhb_qc.csv"
    blocks.to_csv(qc_path, index=False)

    max_len = cfg.mhl_max_length
    if max_len is None:
        raise ValueError(
            "mhb_mhl requires operator-set mhl_max_length (procedure/profile actionConfig.mhb_mhl)"
        )

    matrix_rows = []
    for sample_id, sample_dir, group in samples:
        chroms_for_sample = chromosomes or [
            p.name.split("-")[0] for p in Path(sample_dir).glob(f"*-{context}.mhap.h5")
        ]
        stores = load_stores_for_sample(sample_dir, chroms_for_sample, context)
        values = mhl_matrix_for_sample(stores, blocks, max_len=int(max_len))
        row: Dict[str, Any] = {cfg.sample_id_column: sample_id, "group": group}
        for j, name in enumerate(blocks["name"].tolist() if not blocks.empty else []):
            row[str(name)] = float(values[j]) if j < len(values) else float("nan")
        matrix_rows.append(row)

    matrix = pd.DataFrame(matrix_rows)
    matrix_path = output_dir / "mhl_matrix.csv"
    matrix.to_csv(matrix_path, index=False)

    manifest = {
        "status": "ok",
        "mode": mode,
        "n_samples": len(samples),
        "n_blocks": int(len(blocks)),
        "mhl_matrix": str(matrix_path),
        "mhb_bed": str(bed_path),
        "mhb_qc": str(qc_path),
        "output_dir": str(output_dir),
    }
    (output_dir / "mhb_mhl.manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest
