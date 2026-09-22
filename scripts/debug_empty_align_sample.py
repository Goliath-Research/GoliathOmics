#!/usr/bin/env python3
"""
Debug empty_align warnings for one sample/chrom/context.

Example:
  source .venv/bin/activate && \
  python scripts/debug_empty_align_sample.py \
    --project-json /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/monte_carlo_runs/production/project.json \
    --sample 004560_7D15_63 \
    --chromosomes 4 5 \
    --contexts CG
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np

from methyl_utils import MethylSample, load_pos_from_h5
from methyl_validation.model_bundle import load_bundle_dmp_index


def _resolve_bundle_h5(project_json: Path, bundle_h5: str | None) -> Path:
    if bundle_h5:
        return Path(bundle_h5).resolve()
    return (project_json.parent / "model_bundle" / "model_feature_bundle.h5").resolve()


def _load_project_json(path: Path) -> Dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sample_path(project: Dict, sample: str) -> Path:
    sample_path = Path(sample)
    if sample_path.is_absolute():
        return sample_path
    base = str(project.get("samples_base_path") or "").strip()
    if not base:
        return sample_path
    return (Path(base) / sample).resolve()


def _build_panel_map(
    bundle_h5: Path,
    max_dmps: int,
) -> Dict[Tuple[str, str], np.ndarray]:
    dmp_df = load_bundle_dmp_index(bundle_h5)
    if max_dmps and len(dmp_df) > max_dmps:
        dmp_df = dmp_df.sort_values(["effect_size"], ascending=[False]).head(max_dmps).copy()
    out: Dict[Tuple[str, str], np.ndarray] = {}
    for (chrom, ctx), gdf in dmp_df.groupby(["chromosome", "context"], sort=False):
        vals = np.asarray(sorted(set(int(v) for v in gdf["position"].tolist())), dtype=np.uint32)
        out[(str(chrom), str(ctx))] = vals
    return out


def _is_sorted_uint32(a: np.ndarray) -> bool:
    if a.size <= 1:
        return True
    return bool(np.all(a[:-1] <= a[1:]))


def _analyze_one(sample_dir_or_h5: Path, chrom: str, ctx: str, panel_positions: np.ndarray) -> Dict[str, object]:
    h5_path = sample_dir_or_h5 if sample_dir_or_h5.suffix == ".h5" else sample_dir_or_h5 / f"{chrom}-{ctx}.h5"
    result: Dict[str, object] = {
        "h5_path": str(h5_path),
        "exists": h5_path.is_file(),
        "chrom": chrom,
        "context": ctx,
        "n_panel_positions": int(panel_positions.size),
    }
    if not h5_path.is_file():
        return result

    pos = np.asarray(load_pos_from_h5(h5_path), dtype=np.uint32)
    result["n_sample_positions"] = int(pos.size)
    result["sample_pos_sorted"] = _is_sorted_uint32(pos)
    result["n_sample_unique"] = int(np.unique(pos).size)
    result["n_sample_duplicates"] = int(pos.size - np.unique(pos).size)

    overlap = np.intersect1d(pos, panel_positions, assume_unique=False)
    result["n_overlap_intersect1d"] = int(overlap.size)

    # This is the exact path used by extraction (positions-filtered load).
    filtered = MethylSample.load_from_h5(h5_path, positions=panel_positions, align_positions=False)
    result["n_rows_loaded_with_positions_filter"] = int(len(filtered))

    if int(result["n_overlap_intersect1d"]) > 0 and int(result["n_rows_loaded_with_positions_filter"]) == 0:
        result["suspect_binary_search_on_unsorted_pos"] = True
    else:
        result["suspect_binary_search_on_unsorted_pos"] = False
    return result


def _iter_pairs(chromosomes: Iterable[str], contexts: Iterable[str]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for c in chromosomes:
        for ctx in contexts:
            out.append((str(c), str(ctx)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Diagnose empty_align for a single sample.")
    ap.add_argument("--project-json", required=True, help="Production project.json path.")
    ap.add_argument("--sample", required=True, help="Sample directory name or absolute sample path.")
    ap.add_argument("--bundle-h5", default=None, help="Optional model_feature_bundle.h5 path.")
    ap.add_argument("--max-dmps", type=int, default=5000, help="Apply same top-N effect_size truncation as tabular.")
    ap.add_argument("--chromosomes", nargs="+", default=["4", "5"], help="Chromosomes to inspect.")
    ap.add_argument("--contexts", nargs="+", default=["CG"], help="Contexts to inspect.")
    args = ap.parse_args()

    project_json = Path(args.project_json).resolve()
    project = _load_project_json(project_json)
    bundle_h5 = _resolve_bundle_h5(project_json, args.bundle_h5)
    panel_map = _build_panel_map(bundle_h5, max_dmps=int(max(0, args.max_dmps)))
    sample_path = _sample_path(project, args.sample)

    print(f"project_json={project_json}")
    print(f"bundle_h5={bundle_h5}")
    print(f"sample={args.sample}")
    print(f"resolved_sample_path={sample_path}")
    print("")

    for chrom, ctx in _iter_pairs(args.chromosomes, args.contexts):
        panel_positions = panel_map.get((chrom, ctx), np.asarray([], dtype=np.uint32))
        row = _analyze_one(sample_path, chrom, ctx, panel_positions)
        print(
            f"[{chrom}-{ctx}] exists={row['exists']} "
            f"panel={row['n_panel_positions']} "
            f"sample_pos={row.get('n_sample_positions', 'n/a')} "
            f"overlap={row.get('n_overlap_intersect1d', 'n/a')} "
            f"loaded_with_filter={row.get('n_rows_loaded_with_positions_filter', 'n/a')}"
        )
        if row["exists"]:
            print(
                f"  sorted={row['sample_pos_sorted']} "
                f"duplicates={row['n_sample_duplicates']} "
                f"suspect_binary_search_on_unsorted_pos={row['suspect_binary_search_on_unsorted_pos']}"
            )
        else:
            print(f"  missing file: {row['h5_path']}")


if __name__ == "__main__":
    main()
