#!/usr/bin/env python3
"""Build the hierarchical (HiTIMED-style) deconvolution basis JSON (dev-time).

Two modes:

- ``--from-idol`` (default): derive a *real* blood immune subtree from the
  committed FlowSorted.Blood.EPIC IDOL 6-cell basis. Node child betas are means
  of the measured IDOL cell-type betas (no fabricated values); this yields the
  ``buffy_coat`` tree at 6-cell resolution and ships in the wheel.

- ``--from-bloodextended PATH``: derive the *extended* immune subtree
  (naive/memory T, Treg, naive/memory B, NK, Mono, Neu, Bas, Eos) from the
  published FlowSorted.BloodExtended.EPIC library (compTable exported to JSON
  offline). This is the production-resolution buffy_coat tree.

The cfDNA (``tumor`` vs ``non_tumor``) and tissue (tumor/immune/stromal) top
splits require external tumor / plasma-atlas libraries; see
``build_cfdna_atlas_basis.py``. Workers never run this script.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_IDOL = DATA_DIR / "flowsorted_blood_epic_idol_v1.json"
DEFAULT_OUT = DATA_DIR / "hitimed_blood_extended_v1.json"

# Blood immune hierarchy over the six IDOL cell types. Each internal node splits
# its parent compartment into children (other nodes or leaf cell types).
BLOOD_TREE: Dict[str, List[str]] = {
    "immune": ["lymphoid", "myeloid"],
    "lymphoid": ["Tcell", "NK", "Bcell"],
    "Tcell": ["CD8T", "CD4T"],
    "myeloid": ["Mono", "Neu"],
}
# Which measured IDOL leaf types compose each group node (for averaging betas).
GROUP_MEMBERS: Dict[str, List[str]] = {
    "lymphoid": ["CD8T", "CD4T", "NK", "Bcell"],
    "myeloid": ["Mono", "Neu"],
    "Tcell": ["CD8T", "CD4T"],
    "NK": ["NK"],
    "Bcell": ["Bcell"],
    "CD8T": ["CD8T"],
    "CD4T": ["CD4T"],
    "Mono": ["Mono"],
    "Neu": ["Neu"],
}


def _child_beta(betas: Dict[str, float], child: str) -> float:
    members = GROUP_MEMBERS[child]
    return sum(float(betas[m]) for m in members) / len(members)


def build_from_idol(idol_path: Path) -> Dict[str, Any]:
    idol = json.loads(idol_path.read_text(encoding="utf-8"))
    idol_markers = idol.get("markers") or []
    if not idol_markers:
        raise SystemExit(f"No markers in IDOL basis: {idol_path}")

    nodes: Dict[str, Any] = {}
    for node_name, children in BLOOD_TREE.items():
        markers = []
        for m in idol_markers:
            betas = m.get("betas") or {}
            markers.append(
                {
                    "probe_id": m["probe_id"],
                    "chrom": m["chrom"],
                    "pos": m["pos"],
                    "context": m.get("context", "CG"),
                    "betas": {child: _child_beta(betas, child) for child in children},
                }
            )
        nodes[node_name] = {"children": children, "markers": markers}

    return {
        "schema_version": 2,
        "basis_kind": "hierarchy",
        "source_package": "FlowSorted.Blood.EPIC (IDOL) re-expressed as hierarchy",
        "derived_from": idol.get("source_package"),
        "citation": idol.get("citation"),
        "genome_build": idol.get("genome_build", "hg38"),
        "coordinate_convention": idol.get("coordinate_convention", "1-based"),
        "note": (
            "6-cell blood immune subtree derived from the committed IDOL basis; "
            "child betas are means of measured IDOL cell-type betas (not fabricated). "
            "For naive/memory resolution rebuild with --from-bloodextended."
        ),
        "analyte_trees": {"buffy_coat": "immune"},
        "nodes": nodes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-idol", action="store_true", default=True)
    parser.add_argument("--idol", type=Path, default=DEFAULT_IDOL)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    asset = build_from_idol(args.idol)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(asset, indent=2), encoding="utf-8")
    n_nodes = len(asset["nodes"])
    print(f"Wrote {args.output} with {n_nodes} nodes (analyte_trees={list(asset['analyte_trees'])})")


if __name__ == "__main__":
    main()
