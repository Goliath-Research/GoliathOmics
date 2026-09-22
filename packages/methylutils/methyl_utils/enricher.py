#!/usr/bin/env python3
"""
Run over-representation analysis (ORA) on your prostate-cancer DMP-mapped genes.
Requires: gseapy >= 1.1.0 and internet access (to query Enrichr libraries).
Usage:
  python enrich_prostate_genes.py --input top_genes_for_enrichment.txt --outdir results
"""
import argparse
import os
import pandas as pd

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Text file with one gene symbol per line")
    ap.add_argument("--outdir", default="results", help="Output folder")
    ap.add_argument("--libraries", nargs="+", default=[
        "KEGG_2021_Human",
        "Reactome_2022",
        "GO_Biological_Process_2023",
        "GO_Molecular_Function_2023",
        "GO_Cellular_Component_2023",
        "MSigDB_Hallmark_2020",
        "WikiPathway_2023_Human"
    ], help="Enrichr libraries to query")
    ap.add_argument("--top", type=int, default=200, help="Use top-N genes from the list")
    args = ap.parse_args()

    # Lazy import to allow this script to be created without gseapy installed
    try:
        import gseapy as gp
    except Exception as ex:
        raise SystemExit(f"[ERROR] gseapy is required. Install with: pip install gseapy\n{ex}")

    os.makedirs(args.outdir, exist_ok=True)

    # Read gene list
    with open(args.input) as f:
        genes = [x.strip() for x in f if x.strip()]
    if args.top and len(genes) > args.top:
        genes = genes[:args.top]

    # Run Enrichr ORA for each library
    all_results = []
    for lib in args.libraries:
        print(f"[INFO] Running Enrichr ORA on {lib} with {len(genes)} genes...")
        try:
            enr = gp.enrichr(gene_list=genes,
                             gene_sets=[lib],
                             outdir=args.outdir,
                             cutoff=1.0,  # store all; filter later
                             background=None,  # use Enrichr default
                             organism="Human")
            if hasattr(enr, "results") and enr.results is not None and not enr.results.empty:
                df = enr.results.copy()
                df["library"] = lib
                all_results.append(df)

                # Save per-library
                per_lib_csv = os.path.join(args.outdir, f"enrich_{lib}.csv")
                df.to_csv(per_lib_csv, index=False)
                print(f"[INFO] Completed enrichment for {lib} ({len(df)} terms)")
            else:
                print(f"[WARN] No results returned for {lib}")
        except Exception as e:
            print(f"[ERROR] Failed to process library {lib}: {e}")
            continue

    if all_results:
        merged = pd.concat(all_results, ignore_index=True)
        merged.sort_values(["Adjusted P-value", "P-value", "Odds Ratio"], inplace=True, ascending=[True, True, False])
        merged.to_csv(os.path.join(args.outdir, "enrichment_merged.csv"), index=False)

        # Also emit a top summary (q<=0.05)
        top_hits = merged[merged["Adjusted P-value"] <= 0.05].copy()
        top_hits = top_hits.head(200)
        top_hits.to_csv(os.path.join(args.outdir, "enrichment_top_q0.05.csv"), index=False)
        print(f"[DONE] Wrote merged results and top hits to {args.outdir}")
    else:
        print("[WARN] No enrichment results found.")

if __name__ == "__main__":
    main()
