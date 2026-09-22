#!/usr/bin/env python3
"""Compose the cfDNA (and optional full tissue) hierarchical basis (dev-time).

cfDNA plasma is mostly hematopoietic background with a low ctDNA fraction, so the
tree prepends a ``tumor_fraction`` vs ``non_tumor`` (immune) top split above the
shared blood immune subtree. The tumor/non-tumor discriminating betas MUST come
from a published plasma / tumor methylation atlas supplied by the operator
(``--atlas``); this script does NOT fabricate tumor methylation values.

Atlas JSON contract (exported offline from e.g. Loyfer/Moss plasma atlas):

    {
      "citation": "...",
      "genome_build": "hg38",
      "coordinate_convention": "1-based",
      "markers": [
        {"probe_id": "...", "chrom": "1", "pos": 123, "tumor_beta": 0.9,
         "non_tumor_beta": 0.1},
        ...
      ]
    }

The immune subtree is taken from the committed HiTIMED blood basis
(``hitimed_blood_extended_v1.json``, itself derived from real IDOL data).
Workers never run this script; it produces a wheel/asset JSON that operators
point ``actionConfig.cell_deconvolution.hierarchy_basis_path`` at.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_BLOOD = DATA_DIR / "hitimed_blood_extended_v1.json"
DEFAULT_OUT = DATA_DIR / "cfdna_plasma_atlas_v1.json"


def _load_blood_nodes(blood_path: Path) -> Dict[str, Any]:
    blood = json.loads(blood_path.read_text(encoding="utf-8"))
    nodes = blood.get("nodes") or {}
    if "immune" not in nodes:
        raise SystemExit(f"Blood basis missing 'immune' root node: {blood_path}")
    return nodes


def build_cfdna(atlas_path: Path, blood_path: Path) -> Dict[str, Any]:
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    atlas_markers = atlas.get("markers") or []
    if not atlas_markers:
        raise SystemExit(f"Atlas has no markers: {atlas_path}")

    blood_nodes = _load_blood_nodes(blood_path)
    nodes: Dict[str, Any] = dict(blood_nodes)

    # Top split: tumor_fraction (leaf) vs immune (shared subtree root).
    plasma_markers = []
    for m in atlas_markers:
        if "tumor_beta" not in m or "non_tumor_beta" not in m:
            raise SystemExit(
                "Atlas markers require 'tumor_beta' and 'non_tumor_beta' (no fabricated defaults)."
            )
        plasma_markers.append(
            {
                "probe_id": m["probe_id"],
                "chrom": m["chrom"],
                "pos": m["pos"],
                "context": m.get("context", "CG"),
                "betas": {
                    "tumor_fraction": float(m["tumor_beta"]),
                    "immune": float(m["non_tumor_beta"]),
                },
            }
        )
    nodes["plasma"] = {"children": ["tumor_fraction", "immune"], "markers": plasma_markers}

    return {
        "schema_version": 2,
        "basis_kind": "hierarchy",
        "source_package": "cfDNA plasma atlas (operator-supplied) + FlowSorted IDOL immune subtree",
        "citation": atlas.get("citation"),
        "genome_build": atlas.get("genome_build", "hg38"),
        "coordinate_convention": atlas.get("coordinate_convention", "1-based"),
        "note": (
            "cfDNA tree: tumor_fraction vs non-tumor immune top split over the shared "
            "blood immune subtree. Tumor betas are from the operator-supplied atlas."
        ),
        "analyte_trees": {"cfdna": "plasma", "buffy_coat": "immune"},
        "nodes": nodes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--atlas",
        type=Path,
        required=True,
        help="Operator-supplied plasma/tumor methylation atlas JSON (tumor_beta/non_tumor_beta).",
    )
    parser.add_argument("--blood", type=Path, default=DEFAULT_BLOOD)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.atlas.is_file():
        raise SystemExit(
            f"Atlas not found: {args.atlas}. Provide a published plasma atlas export; "
            "this script does not ship fabricated tumor betas."
        )
    asset = build_cfdna(args.atlas, args.blood)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(asset, indent=2), encoding="utf-8")
    print(f"Wrote {args.output} (analyte_trees={list(asset['analyte_trees'])})")


if __name__ == "__main__":
    main()
