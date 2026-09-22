"""Cohort differential Ising measures and mutual-information gene ranking."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.spatial.distance import jensenshannon

from methyl_utils.array_backend import get_array_module
from methyl_utils.gpu_detection import cleanup_gpu_memory

from ..config import InfoTheoryStepConfig
from .cohort_jsd import _accumulate_group_histograms
from .ising import build_state_design, fit_ising_batch, resolve_ising_runtime
from .ising_measures import compute_measures_from_prob


@dataclass(frozen=True)
class TileDifferentialRecord:
    chrom: str
    context: str
    tile_start_pos: int
    tile_cpg_positions: Tuple[int, ...]
    mml_group1: float
    mml_group2: float
    nme_group1: float
    nme_group2: float
    dmml: float
    dnme: float
    model_jsd: float
    mutual_information: float
    n_reads_group1: int
    n_reads_group2: int


def _fit_single_histogram(
    vec: np.ndarray,
    design,
    runtime: dict,
) -> Tuple[np.ndarray, Dict[str, float]]:
    xp, _ = get_array_module(runtime.get("prefer_gpu"))
    dense = xp.asarray(vec[None, :], dtype=xp.float64)
    fit = fit_ising_batch(
        dense,
        design,
        xp=xp,
        max_iter=runtime["max_iter"],
        tol=runtime["tol"],
        l2=runtime["l2"],
    )
    measures = compute_measures_from_prob(fit.prob, design, xp=xp)
    prob = fit.prob[0]
    return prob, {
        "mml": float(measures["mml"][0]),
        "nme": float(measures["nme"][0]),
    }


def _mutual_information(p1: np.ndarray, p2: np.ndarray) -> float:
    eps = 1e-12
    p1 = np.clip(p1, eps, 1.0)
    p2 = np.clip(p2, eps, 1.0)
    p1 = p1 / np.sum(p1)
    p2 = p2 / np.sum(p2)
    m = 0.5 * (p1 + p2)
    kl1 = np.sum(p1 * np.log2(p1 / m))
    kl2 = np.sum(p2 * np.log2(p2 / m))
    jsd = 0.5 * (kl1 + kl2)
    return float(jsd)


def compute_differential_records(
    group1_dirs: Sequence[str],
    group2_dirs: Sequence[str],
    *,
    chromosomes: Sequence[str],
    contexts: Sequence[str],
    cfg: InfoTheoryStepConfig,
) -> List[TileDifferentialRecord]:
    runtime = resolve_ising_runtime(cfg)
    min_reads = int(cfg.jsd_min_cohort_reads) if cfg.jsd_min_cohort_reads is not None else 10
    coupling = runtime["coupling"]
    if coupling not in ("nearest", "all"):
        coupling = "nearest"

    g1 = _accumulate_group_histograms(
        group1_dirs, chromosomes, contexts, min_cohort_reads=min_reads
    )
    g2 = _accumulate_group_histograms(
        group2_dirs, chromosomes, contexts, min_cohort_reads=min_reads
    )

    records: List[TileDifferentialRecord] = []
    design_cache: Dict[int, object] = {}

    for key in sorted(set(g1) & set(g2)):
        chrom, ctx, start, positions = key
        v1 = g1[key]
        v2 = g2[key]
        s1 = float(np.sum(v1))
        s2 = float(np.sum(v2))
        if s1 < min_reads or s2 < min_reads:
            continue
        k = int(round(np.log2(v1.size)))
        if k not in design_cache:
            design_cache[k] = build_state_design(k, coupling)  # type: ignore[arg-type]
        design = design_cache[k]

        prob1, m1 = _fit_single_histogram(v1, design, runtime)
        prob2, m2 = _fit_single_histogram(v2, design, runtime)
        jsd = float(jensenshannon(prob1, prob2, base=2.0) ** 2)
        mi = _mutual_information(prob1, prob2)
        if not np.isfinite(jsd):
            continue

        records.append(
            TileDifferentialRecord(
                chrom=str(chrom),
                context=str(ctx),
                tile_start_pos=int(start),
                tile_cpg_positions=tuple(int(p) for p in positions),
                mml_group1=m1["mml"],
                mml_group2=m2["mml"],
                nme_group1=m1["nme"],
                nme_group2=m2["nme"],
                dmml=float(m1["mml"] - m2["mml"]),
                dnme=float(m1["nme"] - m2["nme"]),
                model_jsd=jsd,
                mutual_information=mi,
                n_reads_group1=int(s1),
                n_reads_group2=int(s2),
            )
        )

    cleanup_gpu_memory()
    records.sort(key=lambda r: abs(r.dnme), reverse=True)
    top_n = int(cfg.jsd_top_windows) if cfg.jsd_top_windows is not None else 100
    return records[:top_n]


def build_mi_gene_ranking(
    records: Sequence[TileDifferentialRecord],
    mapper_gene_csv: str,
) -> List[Dict[str, object]]:
    """Rank genes by mean tile mutual information near intersection loci."""
    mapper_path = Path(mapper_gene_csv)
    if not mapper_path.is_file() or not records:
        return []

    mapper_dir = mapper_path.parent
    intersection_files = sorted(mapper_dir.glob("*-intersections.csv"))
    if not intersection_files:
        return []

    gene_mi: Dict[str, List[float]] = {}
    for record in records:
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
                        gene_mi.setdefault(gene, []).append(record.mutual_information)

    ranking = [
        {"gene_name": gene, "mean_mutual_information": float(np.mean(vals)), "n_tiles": len(vals)}
        for gene, vals in gene_mi.items()
    ]
    ranking.sort(key=lambda x: float(x["mean_mutual_information"]), reverse=True)
    return ranking
