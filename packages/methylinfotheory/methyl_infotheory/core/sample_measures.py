"""Per-sample readlevel:: feature rows from pattern sidecars."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np

from methyl_utils.core.read_level_io import discover_pattern_files, load_read_level_patterns

from ..config import InfoTheoryStepConfig
from .ising_measures import aggregate_file_ising_measures
from .patterns import aggregate_file_measures


def _chrom_from_path(path: Path) -> str:
    stem = path.name.replace(".patterns.h5", "")
    if "-" in stem:
        return stem.split("-", 1)[0]
    return stem


def compute_sample_readlevel_measures(
    sample_id: str,
    sample_dir: str,
    *,
    chromosomes: Sequence[str],
    contexts: Sequence[str],
    cfg: InfoTheoryStepConfig,
) -> Dict[str, Any]:
    min_reads = int(cfg.min_tile_reads) if cfg.min_tile_reads is not None else 1
    ising_on = bool(cfg.ising_enabled)
    row: Dict[str, Any] = {str(cfg.sample_id_column): str(sample_id)}

    chrom_acc: Dict[str, Dict[str, List[float]]] = {}
    global_acc = {
        "entropy": [],
        "epipolymorphism": [],
        "pdr": [],
        "weight": [],
    }
    ising_global = {"mml": [], "nme": [], "esi": [], "msi": [], "weight": []}
    ising_chrom: Dict[str, Dict[str, List[float]]] = {}

    paths = discover_pattern_files(sample_dir, chromosomes, contexts)
    for path in paths:
        try:
            patterns = load_read_level_patterns(path)
        except Exception:
            continue
        if cfg.tile_size is not None and int(patterns.tile_size) != int(cfg.tile_size):
            continue
        agg = aggregate_file_measures(patterns, min_tile_reads=min_reads)
        if not np.isfinite(agg["entropy"]):
            continue
        chrom = _chrom_from_path(path)
        bucket = chrom_acc.setdefault(
            chrom,
            {"entropy": [], "epipolymorphism": [], "pdr": [], "weight": []},
        )
        w = float(agg["n_tiles"])
        bucket["entropy"].append(agg["entropy"] * w)
        bucket["epipolymorphism"].append(agg["epipolymorphism"] * w)
        bucket["pdr"].append(agg["pdr"] * w)
        bucket["weight"].append(w)
        global_acc["entropy"].append(agg["entropy"] * w)
        global_acc["epipolymorphism"].append(agg["epipolymorphism"] * w)
        global_acc["pdr"].append(agg["pdr"] * w)
        global_acc["weight"].append(w)

        if ising_on:
            ising_agg = aggregate_file_ising_measures(patterns, cfg=cfg)
            if np.isfinite(ising_agg.get("mml", float("nan"))):
                iw = float(ising_agg["n_tiles"])
                ising_global["mml"].append(ising_agg["mml"] * iw)
                ising_global["nme"].append(ising_agg["nme"] * iw)
                ising_global["esi"].append(ising_agg["esi"] * iw)
                ising_global["msi"].append(ising_agg["msi"] * iw)
                ising_global["weight"].append(iw)
                ib = ising_chrom.setdefault(
                    chrom,
                    {"mml": [], "nme": [], "esi": [], "msi": [], "weight": []},
                )
                ib["mml"].append(ising_agg["mml"] * iw)
                ib["nme"].append(ising_agg["nme"] * iw)
                ib["esi"].append(ising_agg["esi"] * iw)
                ib["msi"].append(ising_agg["msi"] * iw)
                ib["weight"].append(iw)

    def _wmean(acc: Dict[str, List[float]], key: str) -> float:
        weights = acc.get("weight") or []
        if not weights:
            return float("nan")
        w_sum = float(np.sum(weights))
        vals = acc.get(key) or []
        if not vals or w_sum <= 0.0:
            return float("nan")
        return float(np.sum(vals) / w_sum)

    row["readlevel::global_entropy"] = _wmean(global_acc, "entropy")
    row["readlevel::global_epipolymorphism"] = _wmean(global_acc, "epipolymorphism")
    row["readlevel::global_pdr"] = _wmean(global_acc, "pdr")
    row["readlevel::n_pattern_files"] = float(len(paths))

    for chrom in chromosomes:
        acc = chrom_acc.get(str(chrom), {"entropy": [], "epipolymorphism": [], "pdr": [], "weight": []})
        row[f"readlevel::chrom_{chrom}::entropy"] = _wmean(acc, "entropy")
        row[f"readlevel::chrom_{chrom}::epipolymorphism"] = _wmean(acc, "epipolymorphism")
        row[f"readlevel::chrom_{chrom}::pdr"] = _wmean(acc, "pdr")

    if ising_on:
        row["readlevel::global_mml"] = _wmean(ising_global, "mml")
        row["readlevel::global_nme"] = _wmean(ising_global, "nme")
        row["readlevel::global_esi"] = _wmean(ising_global, "esi")
        row["readlevel::global_msi"] = _wmean(ising_global, "msi")
        for chrom in chromosomes:
            acc = ising_chrom.get(
                str(chrom),
                {"mml": [], "nme": [], "esi": [], "msi": [], "weight": []},
            )
            row[f"readlevel::chrom_{chrom}::mml"] = _wmean(acc, "mml")
            row[f"readlevel::chrom_{chrom}::nme"] = _wmean(acc, "nme")
            row[f"readlevel::chrom_{chrom}::esi"] = _wmean(acc, "esi")
            row[f"readlevel::chrom_{chrom}::msi"] = _wmean(acc, "msi")

    return row
