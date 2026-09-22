"""Concordance between read-level JSD / Ising differential windows and DMP/gene rankings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from .cohort_jsd import TileJsdRecord
from .differential import TileDifferentialRecord, build_mi_gene_ranking


def _load_dmp_positions(dmp_csv: Path) -> List[tuple[str, int]]:
    df = pd.read_csv(dmp_csv)
    pos_col = None
    chrom_col = None
    for candidate in ("pos", "position", "start"):
        if candidate in df.columns:
            pos_col = candidate
            break
    for candidate in ("chrom", "chromosome", "chr"):
        if candidate in df.columns:
            chrom_col = candidate
            break
    if pos_col is None:
        return []
    out: List[tuple[str, int]] = []
    for _, row in df.iterrows():
        chrom = str(row[chrom_col]) if chrom_col else ""
        if chrom_col and chrom.startswith("chr"):
            chrom = chrom[3:]
        try:
            pos = int(row[pos_col])
        except (TypeError, ValueError):
            continue
        out.append((chrom, pos))
    return out


def _tile_overlaps_dmp(
    chrom: str,
    tile_start_pos: int,
    tile_cpg_positions: Sequence[int],
    dmp_loci: Sequence[tuple[str, int]],
) -> bool:
    positions = set(tile_cpg_positions)
    for dmp_chrom, pos in dmp_loci:
        if str(dmp_chrom) != str(chrom):
            continue
        if int(pos) in positions or int(pos) == int(tile_start_pos):
            return True
    return False


def _tile_overlaps_dmp_jsd(record: TileJsdRecord, dmp_loci: Sequence[tuple[str, int]]) -> bool:
    return _tile_overlaps_dmp(
        record.chrom, record.tile_start_pos, record.tile_cpg_positions, dmp_loci
    )


def _tile_overlaps_dmp_diff(record: TileDifferentialRecord, dmp_loci: Sequence[tuple[str, int]]) -> bool:
    return _tile_overlaps_dmp(
        record.chrom, record.tile_start_pos, record.tile_cpg_positions, dmp_loci
    )


def _gene_concordance_from_jsd(
    jsd_records: Sequence[TileJsdRecord],
    mapper_path: Path,
) -> Dict[str, Any]:
    mapper_df = pd.read_csv(mapper_path)
    if "gene_name" not in mapper_df.columns or "gene_importance" not in mapper_df.columns:
        return {"skipped": True, "reason": "missing_gene_importance_columns"}

    mapper_dir = mapper_path.parent
    intersection_files = sorted(mapper_dir.glob("*-intersections.csv"))
    gene_jsd: Dict[str, List[float]] = {}
    if intersection_files:
        for record in jsd_records:
            mid = int(np.median(record.tile_cpg_positions))
            for ix_path in intersection_files:
                ix = pd.read_csv(ix_path)
                if "gene_name" not in ix.columns or "pos" not in ix.columns:
                    continue
                chrom_col = "chrom" if "chrom" in ix.columns else None
                for _, row in ix.iterrows():
                    if chrom_col and str(row[chrom_col]).replace("chr", "") != str(record.chrom):
                        continue
                    try:
                        pos = int(row["pos"])
                    except (TypeError, ValueError):
                        continue
                    if abs(pos - mid) <= 500:
                        gene = str(row["gene_name"]).strip()
                        if gene:
                            gene_jsd.setdefault(gene, []).append(record.jsd)

    if not gene_jsd:
        return {
            "mapper_gene_csv": str(mapper_path.resolve()),
            "skipped": True,
            "reason": "no_intersection_files_for_gene_jsd_mapping",
        }

    jsd_rank = pd.DataFrame(
        {
            "gene_name": list(gene_jsd.keys()),
            "mean_jsd": [float(np.mean(v)) for v in gene_jsd.values()],
        }
    )
    merged = jsd_rank.merge(
        mapper_df[["gene_name", "gene_importance"]],
        on="gene_name",
        how="inner",
    )
    if len(merged) < 3:
        return {
            "mapper_gene_csv": str(mapper_path.resolve()),
            "skipped": True,
            "reason": "insufficient_gene_overlap",
        }

    rho, pval = spearmanr(
        merged["mean_jsd"].astype(float),
        merged["gene_importance"].astype(float),
    )
    top_k = min(50, len(merged))
    top_jsd = set(
        merged.sort_values("mean_jsd", ascending=False).head(top_k)["gene_name"].astype(str)
    )
    top_imp = set(
        merged.sort_values("gene_importance", ascending=False).head(top_k)["gene_name"].astype(str)
    )
    return {
        "mapper_gene_csv": str(mapper_path.resolve()),
        "n_genes_compared": int(len(merged)),
        "spearman_rho": float(rho) if np.isfinite(rho) else None,
        "spearman_pvalue": float(pval) if np.isfinite(pval) else None,
        "top_k": int(top_k),
        "top_k_overlap": int(len(top_jsd & top_imp)),
        "top_k_overlap_fraction": float(len(top_jsd & top_imp) / top_k),
    }


def _gene_concordance_from_mi(
    diff_records: Sequence[TileDifferentialRecord],
    mapper_path: Path,
) -> Dict[str, Any]:
    mapper_df = pd.read_csv(mapper_path)
    if "gene_name" not in mapper_df.columns or "gene_importance" not in mapper_df.columns:
        return {"skipped": True, "reason": "missing_gene_importance_columns"}

    mi_ranking = build_mi_gene_ranking(diff_records, str(mapper_path))
    if len(mi_ranking) < 3:
        return {
            "mapper_gene_csv": str(mapper_path.resolve()),
            "skipped": True,
            "reason": "insufficient_mi_gene_mapping",
        }

    mi_df = pd.DataFrame(mi_ranking)
    merged = mi_df.merge(
        mapper_df[["gene_name", "gene_importance"]],
        on="gene_name",
        how="inner",
    )
    if len(merged) < 3:
        return {
            "mapper_gene_csv": str(mapper_path.resolve()),
            "skipped": True,
            "reason": "insufficient_gene_overlap",
        }

    rho, pval = spearmanr(
        merged["mean_mutual_information"].astype(float),
        merged["gene_importance"].astype(float),
    )
    top_k = min(50, len(merged))
    top_mi = set(
        merged.sort_values("mean_mutual_information", ascending=False)
        .head(top_k)["gene_name"]
        .astype(str)
    )
    top_imp = set(
        merged.sort_values("gene_importance", ascending=False).head(top_k)["gene_name"].astype(str)
    )
    return {
        "mapper_gene_csv": str(mapper_path.resolve()),
        "n_genes_compared": int(len(merged)),
        "spearman_rho": float(rho) if np.isfinite(rho) else None,
        "spearman_pvalue": float(pval) if np.isfinite(pval) else None,
        "top_k": int(top_k),
        "top_k_overlap": int(len(top_mi & top_imp)),
        "top_k_overlap_fraction": float(len(top_mi & top_imp) / top_k),
        "top_mi_genes": mi_ranking[:20],
    }


def build_confirmation_report(
    jsd_records: Sequence[TileJsdRecord],
    *,
    dmp_panel_csv: Optional[str] = None,
    mapper_gene_csv: Optional[str] = None,
    differential_records: Optional[Sequence[TileDifferentialRecord]] = None,
    dynamics_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "n_jsd_windows": int(len(jsd_records)),
        "top_jsd": [
            {
                "chrom": r.chrom,
                "context": r.context,
                "tile_start_pos": r.tile_start_pos,
                "jsd": r.jsd,
                "n_reads_group1": r.n_reads_group1,
                "n_reads_group2": r.n_reads_group2,
            }
            for r in jsd_records[:20]
        ],
    }

    diff_records = list(differential_records or [])
    if diff_records:
        report["n_ising_differential_windows"] = int(len(diff_records))
        report["top_dnme"] = [
            {
                "chrom": r.chrom,
                "context": r.context,
                "tile_start_pos": r.tile_start_pos,
                "dnme": r.dnme,
                "dmml": r.dmml,
                "model_jsd": r.model_jsd,
                "mutual_information": r.mutual_information,
            }
            for r in sorted(diff_records, key=lambda x: abs(x.dnme), reverse=True)[:20]
        ]

    if dmp_panel_csv:
        dmp_path = Path(dmp_panel_csv)
        if dmp_path.is_file():
            loci = _load_dmp_positions(dmp_path)
            if loci and jsd_records:
                overlaps = sum(1 for r in jsd_records if _tile_overlaps_dmp_jsd(r, loci))
                report["dmp_concordance"] = {
                    "dmp_panel_csv": str(dmp_path.resolve()),
                    "n_dmp_loci": int(len(loci)),
                    "n_jsd_windows": int(len(jsd_records)),
                    "n_overlapping_windows": int(overlaps),
                    "overlap_fraction": float(overlaps / len(jsd_records)),
                }
            if loci and diff_records:
                dnme_overlaps = sum(1 for r in diff_records if _tile_overlaps_dmp_diff(r, loci))
                report["dmp_concordance_dnme"] = {
                    "dmp_panel_csv": str(dmp_path.resolve()),
                    "n_dmp_loci": int(len(loci)),
                    "n_dnme_windows": int(len(diff_records)),
                    "n_overlapping_windows": int(dnme_overlaps),
                    "overlap_fraction": float(dnme_overlaps / len(diff_records)),
                }

    if mapper_gene_csv:
        mapper_path = Path(mapper_gene_csv)
        if mapper_path.is_file() and jsd_records:
            report["gene_concordance"] = _gene_concordance_from_jsd(jsd_records, mapper_path)
        if mapper_path.is_file() and diff_records:
            report["gene_concordance_mi"] = _gene_concordance_from_mi(diff_records, mapper_path)

    if dynamics_report is not None:
        report["dynamics"] = dynamics_report

    return report
