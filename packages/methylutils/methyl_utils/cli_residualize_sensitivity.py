"""CLI: methyl-residualize-sensitivity — unadjusted vs residualized DMP/gene overlap."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Set

import pandas as pd


def _loci(df: pd.DataFrame) -> Set[str]:
    chrom = df["chromosome"].astype(str) if "chromosome" in df.columns else df.get("chrom")
    if chrom is None:
        chrom = pd.Series(["?"] * len(df))
    pos = df["position"].astype(str) if "position" in df.columns else df["pos"].astype(str)
    return set(chrom.astype(str) + ":" + pos)


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return float(len(a & b) / len(union)) if union else 0.0


def _spearman(a: pd.Series, b: pd.Series) -> float:
    if a.empty or b.empty:
        return float("nan")
    return float(a.rank().corr(b.rank(), method="pearson"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare unadjusted vs residualized DMP panels and gene_importance ranks.",
    )
    parser.add_argument("--unadjusted-dmps", type=Path, required=True)
    parser.add_argument("--adjusted-dmps", type=Path, required=True)
    parser.add_argument("--unadjusted-genes", type=Path, default=None)
    parser.add_argument("--adjusted-genes", type=Path, default=None)
    parser.add_argument("--panel-bed", type=Path, action="append", default=[], help="BED of confounder panel sites")
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()

    u = pd.read_csv(args.unadjusted_dmps)
    a = pd.read_csv(args.adjusted_dmps)
    u_loci = _loci(u)
    a_loci = _loci(a)
    payload: Dict[str, Any] = {
        "n_unadjusted_dmps": int(len(u_loci)),
        "n_adjusted_dmps": int(len(a_loci)),
        "dmp_jaccard": _jaccard(u_loci, a_loci),
        "dmp_overlap": int(len(u_loci & a_loci)),
    }
    if args.unadjusted_genes and args.adjusted_genes:
        ug = pd.read_csv(args.unadjusted_genes)
        ag = pd.read_csv(args.adjusted_genes)
        if "gene_name" in ug.columns and "gene_importance" in ug.columns:
            merged = ug.merge(ag, on="gene_name", suffixes=("_u", "_a"))
            payload["n_shared_genes"] = int(len(merged))
            payload["gene_importance_spearman"] = _spearman(
                merged["gene_importance_u"], merged["gene_importance_a"]
            )
    bed_hits = {}
    for bed in args.panel_bed:
        hits_u = 0
        hits_a = 0
        lines = bed.read_text(encoding="utf-8").splitlines()
        sites: Set[str] = set()
        for line in lines:
            if not line.strip() or line.startswith("#") or line.startswith("track"):
                continue
            parts = line.split("\t")
            chrom = parts[0].lstrip("chr")
            pos = parts[1]
            sites.add(f"{chrom}:{pos}")
        hits_u = len(u_loci & sites)
        hits_a = len(a_loci & sites)
        bed_hits[bed.name] = {
            "unadjusted_overlap": hits_u,
            "adjusted_overlap": hits_a,
            "n_panel_sites": len(sites),
        }
    if bed_hits:
        payload["panel_bed_overlap"] = bed_hits
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
