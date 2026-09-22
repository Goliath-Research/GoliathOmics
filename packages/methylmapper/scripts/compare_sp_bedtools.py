#!/usr/bin/env python3
"""
Compare gene-mapping results from spMapDMP2Genes (SP) vs BedtoolsMapper (BEDTOOLS).

Reads two CSVs (SP export of sample_genes and BEDTOOLS features output), aligns on
gene_id or gene_name, and reports:
  - Gene set overlap (Jaccard index, counts)
  - Correlation of p_value and q_value for genes in common
  - Fraction of common genes with matching direction

Usage:
  python compare_sp_bedtools.py --sp sample_genes_sp.csv --bedtools chr1-features-gene_name.csv [--output report.json]
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _normalize_df(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Normalize column names to gene_key, p_value, q_value, direction."""
    out = df.copy()
    rename = {}
    if "gene_p_value" in out.columns and "p_value" not in out.columns:
        rename["gene_p_value"] = "p_value"
    if "gene_q_value" in out.columns and "q_value" not in out.columns:
        rename["gene_q_value"] = "q_value"
    if "gene_direction" in out.columns and "direction" not in out.columns:
        rename["gene_direction"] = "direction"
    out = out.rename(columns=rename)
    if "gene_name" in out.columns:
        out["gene_key"] = out["gene_name"].astype(str).str.strip()
    elif "gene_id" in out.columns:
        out["gene_key"] = out["gene_id"].astype(str).str.strip()
    else:
        raise ValueError(f"{source} CSV must have gene_id or gene_name")
    return out


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compare(sp_path: Path, bedtools_path: Path) -> dict:
    sp_df = pd.read_csv(sp_path)
    bt_df = pd.read_csv(bedtools_path)

    sp_df = _normalize_df(sp_df, "SP")
    bt_df = _normalize_df(bt_df, "BEDTOOLS")

    # Drop rows with null gene_key
    sp_df = sp_df[sp_df["gene_key"].notna() & (sp_df["gene_key"].astype(str).str.strip() != "")]
    bt_df = bt_df[bt_df["gene_key"].notna() & (bt_df["gene_key"].astype(str).str.strip() != "")]

    set_sp = set(sp_df["gene_key"].unique())
    set_bt = set(bt_df["gene_key"].unique())
    common = set_sp & set_bt

    report = {
        "sp_file": str(sp_path),
        "bedtools_file": str(bedtools_path),
        "gene_set": {
            "sp_count": len(set_sp),
            "bedtools_count": len(set_bt),
            "common_count": len(common),
            "jaccard_index": round(jaccard(set_sp, set_bt), 6),
        },
        "p_value_correlation": None,
        "q_value_correlation": None,
        "direction_agreement": None,
    }

    if not common:
        return report

    sp_common = sp_df[sp_df["gene_key"].isin(common)].drop_duplicates(subset=["gene_key"], keep="first")
    bt_common = bt_df[bt_df["gene_key"].isin(common)].drop_duplicates(subset=["gene_key"], keep="first")
    # Merge on gene_key; keep one p_value, q_value, direction from each side (suffixes for duplicates)
    merged = sp_common.merge(
        bt_common,
        on="gene_key",
        how="inner",
        suffixes=("_sp", "_bt"),
    )

    p_sp_col = "p_value_sp" if "p_value_sp" in merged.columns else "p_value"
    p_bt_col = "p_value_bt" if "p_value_bt" in merged.columns else "p_value"
    if p_sp_col in merged.columns and p_bt_col in merged.columns:
        p_sp = pd.to_numeric(merged[p_sp_col], errors="coerce")
        p_bt = pd.to_numeric(merged[p_bt_col], errors="coerce")
        valid = p_sp.notna() & p_bt.notna()
        if valid.sum() > 1:
            report["p_value_correlation"] = {
                "pearson": round(float(np.corrcoef(p_sp[valid], p_bt[valid])[0, 1]), 6),
                "spearman": round(float(pd.Series(p_sp[valid]).corr(pd.Series(p_bt[valid]), method="spearman")), 6),
            }

    q_sp_col = "q_value_sp" if "q_value_sp" in merged.columns else "q_value"
    q_bt_col = "q_value_bt" if "q_value_bt" in merged.columns else "q_value"
    if q_sp_col in merged.columns and q_bt_col in merged.columns:
        q_sp = pd.to_numeric(merged[q_sp_col], errors="coerce")
        q_bt = pd.to_numeric(merged[q_bt_col], errors="coerce")
        valid = q_sp.notna() & q_bt.notna()
        if valid.sum() > 1:
            report["q_value_correlation"] = {
                "pearson": round(float(np.corrcoef(q_sp[valid], q_bt[valid])[0, 1]), 6),
                "spearman": round(float(pd.Series(q_sp[valid]).corr(pd.Series(q_bt[valid]), method="spearman")), 6),
            }

    d_sp_col = "direction_sp" if "direction_sp" in merged.columns else "direction"
    d_bt_col = "direction_bt" if "direction_bt" in merged.columns else "direction"
    if d_sp_col in merged.columns and d_bt_col in merged.columns:
        d_sp = pd.to_numeric(merged[d_sp_col], errors="coerce").fillna(0).astype(int).clip(-1, 1)
        d_bt = pd.to_numeric(merged[d_bt_col], errors="coerce").fillna(0).astype(int).clip(-1, 1)
        same = (d_sp == d_bt).sum()
        total = len(merged)
        report["direction_agreement"] = round(same / total, 6) if total else None

    return report


def main():
    ap = argparse.ArgumentParser(
        description="Compare SP (sample_genes) vs BEDTOOLS gene-mapping results.",
    )
    ap.add_argument("--sp", required=True, type=Path, help="CSV export of sample_genes (SP output)")
    ap.add_argument("--bedtools", required=True, type=Path, help="BEDTOOLS features CSV (e.g. chr1-features-gene_name.csv)")
    ap.add_argument("--output", "-o", type=Path, default=None, help="Write JSON report to file")
    ap.add_argument("--quiet", action="store_true", help="Only print JSON (no summary)")
    args = ap.parse_args()

    if not args.sp.exists():
        print(f"Error: SP file not found: {args.sp}", file=sys.stderr)
        sys.exit(1)
    if not args.bedtools.exists():
        print(f"Error: BEDTOOLS file not found: {args.bedtools}", file=sys.stderr)
        sys.exit(1)

    report = compare(args.sp, args.bedtools)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        if not args.quiet:
            print(f"Report written to {args.output}")

    if args.quiet:
        print(json.dumps(report))
    else:
        print("Comparison (SP vs BEDTOOLS)")
        print("=" * 50)
        gs = report["gene_set"]
        print(f"  Genes (SP):       {gs['sp_count']}")
        print(f"  Genes (BEDTOOLS): {gs['bedtools_count']}")
        print(f"  Common:          {gs['common_count']}")
        print(f"  Jaccard index:   {gs['jaccard_index']:.4f}")
        if report.get("p_value_correlation"):
            print(f"  p_value Spearman: {report['p_value_correlation']['spearman']:.4f}")
        if report.get("q_value_correlation"):
            print(f"  q_value Spearman: {report['q_value_correlation']['spearman']:.4f}")
        if report.get("direction_agreement") is not None:
            print(f"  Direction agree:  {report['direction_agreement']:.2%}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
