"""Orchestrate read-level info measures and confirmation report."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd

from methyl_utils.array_backend import get_array_module
from methyl_utils.core.read_level_io import discover_pattern_files
from methyl_utils.gpu_detection import cleanup_gpu_memory

from ..config import InfoTheoryStepConfig
from .cohort_jsd import compute_cohort_jsd_records
from .confirmation import build_confirmation_report
from .differential import compute_differential_records
from .dynamics import build_dynamics_report
from .sample_measures import compute_sample_readlevel_measures

logger = logging.getLogger(__name__)


def _any_pattern_files(
    samples: Sequence[Tuple[str, str, str]],
    chromosomes: Sequence[str],
    contexts: Sequence[str],
) -> bool:
    for _sid, sample_dir, _group in samples:
        if discover_pattern_files(sample_dir, chromosomes, contexts):
            return True
    return False


def _default_comparison_groups(
    samples: Sequence[Tuple[str, str, str]],
) -> Tuple[List[str], List[str], str, str]:
    labels = sorted({g for _sid, _dir, g in samples})
    if len(labels) < 2:
        return [], [], "", ""
    return (
        [d for _sid, d, g in samples if g == labels[0]],
        [d for _sid, d, g in samples if g == labels[1]],
        labels[0],
        labels[1],
    )


def run_info_measures_for_project(
    samples: Sequence[Tuple[str, str, str]],
    output_dir: Path,
    cfg: InfoTheoryStepConfig,
    *,
    project_chromosomes: Sequence[str] | None = None,
) -> Dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    chromosomes = [str(c) for c in (cfg.chromosomes or project_chromosomes or ["1"])]
    contexts = [str(c) for c in (cfg.contexts or ["CG"])]
    ising_on = bool(cfg.ising_enabled)

    if not _any_pattern_files(samples, chromosomes, contexts):
        logger.warning(
            "No *.patterns.h5 sidecars found; skipping info_measures "
            "(enable methyl_extract.read_level and re-extract samples)"
        )
        manifest = {
            "status": "skipped",
            "reason": "no_pattern_sidecars",
            "output_csv": None,
            "confirmation_report": None,
        }
        manifest_path = output_dir / "readlevel_measures.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    _, gpu_used = get_array_module(cfg.prefer_gpu)

    rows: List[Dict[str, Any]] = []
    for sample_id, sample_dir, _group in samples:
        rows.append(
            compute_sample_readlevel_measures(
                sample_id,
                sample_dir,
                chromosomes=chromosomes,
                contexts=contexts,
                cfg=cfg,
            )
        )

    csv_path = output_dir / "readlevel_measures.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    g1_dirs, g2_dirs, g1_label, g2_label = _default_comparison_groups(samples)
    jsd_records = []
    diff_records = []
    if g1_dirs and g2_dirs:
        jsd_records = compute_cohort_jsd_records(
            g1_dirs,
            g2_dirs,
            chromosomes=chromosomes,
            contexts=contexts,
            cfg=cfg,
        )
        if ising_on:
            diff_records = compute_differential_records(
                g1_dirs,
                g2_dirs,
                chromosomes=chromosomes,
                contexts=contexts,
                cfg=cfg,
            )

    ising_regions_path = None
    if ising_on and diff_records:
        ising_regions_path = output_dir / "ising_regions.csv"
        pd.DataFrame(
            [
                {
                    "chrom": r.chrom,
                    "context": r.context,
                    "tile_start_pos": r.tile_start_pos,
                    "tile_cpg_positions": ",".join(str(p) for p in r.tile_cpg_positions),
                    "mml_group1": r.mml_group1,
                    "mml_group2": r.mml_group2,
                    "nme_group1": r.nme_group1,
                    "nme_group2": r.nme_group2,
                    "dmml": r.dmml,
                    "dnme": r.dnme,
                    "model_jsd": r.model_jsd,
                    "mutual_information": r.mutual_information,
                    "n_reads_group1": r.n_reads_group1,
                    "n_reads_group2": r.n_reads_group2,
                }
                for r in diff_records
            ]
        ).to_csv(ising_regions_path, index=False)

    dynamics_report = build_dynamics_report(cfg) if cfg.dynamics_enabled else None

    report = build_confirmation_report(
        jsd_records,
        dmp_panel_csv=cfg.dmp_panel_csv,
        mapper_gene_csv=cfg.mapper_gene_csv,
        differential_records=diff_records if ising_on else None,
        dynamics_report=dynamics_report,
    )
    report["comparison"] = {"group1": g1_label, "group2": g2_label}
    report["ising_enabled"] = ising_on
    report_path = output_dir / "confirmation_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    cleanup_gpu_memory()

    manifest = {
        "status": "ok",
        "output_csv": str(csv_path.resolve()),
        "confirmation_report": str(report_path.resolve()),
        "ising_regions": str(ising_regions_path.resolve()) if ising_regions_path else None,
        "ising_enabled": ising_on,
        "gpu_used": bool(gpu_used),
        "n_samples": int(len(rows)),
        "n_columns": int(len(rows[0]) if rows else 0),
        "n_jsd_windows": int(len(jsd_records)),
        "n_ising_differential_windows": int(len(diff_records)),
        "chromosomes": chromosomes,
        "contexts": contexts,
    }
    manifest_path = output_dir / "readlevel_measures.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
