#!/usr/bin/env python3
"""Rebuild FlowSorted.Blood.EPIC IDOL seed basis JSON (dev-time; not a worker path).

Downloads IDOLOptimizedCpGs.compTable from the FlowSorted.Blood.EPIC GitHub data/
and maps probes to hg38 via Zhou lab EPIC.hg38.manifest.
"""

from __future__ import annotations

import argparse
import gzip
import json
import urllib.request
from pathlib import Path

DEFAULT_OUT = (
    Path(__file__).resolve().parents[1] / "data" / "flowsorted_blood_epic_idol_v1.json"
)
COMP_URL = (
    "https://raw.githubusercontent.com/immunomethylomics/FlowSorted.Blood.EPIC/"
    "master/data/IDOLOptimizedCpGs.compTable.rda"
)
MANIFEST_URL = (
    "https://zhouserver.research.chop.edu/InfiniumAnnotation/current/EPIC/EPIC.hg38.manifest.tsv.gz"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/flowsorted_rebuild"))
    args = parser.parse_args()

    try:
        import pyreadr
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("pyreadr is required to rebuild the seed basis") from exc

    work = args.workdir
    work.mkdir(parents=True, exist_ok=True)
    comp_path = work / "IDOLOptimizedCpGs.compTable.rda"
    man_path = work / "EPIC.hg38.manifest.tsv.gz"
    if not comp_path.is_file():
        urllib.request.urlretrieve(COMP_URL, comp_path)
    if not man_path.is_file():
        urllib.request.urlretrieve(MANIFEST_URL, man_path)

    comp = pyreadr.read_r(str(comp_path))["IDOLOptimizedCpGs.compTable"]
    cell_types = list(comp.columns)
    wanted = set(comp.index.astype(str))
    coord = {}
    with gzip.open(man_path, "rt") as f:
        header = f.readline().rstrip("\n").split("\t")
        i_id = header.index("probeID")
        i_chr = header.index("CpG_chrm")
        i_pos = header.index("CpG_beg")
        for line in f:
            parts = line.rstrip("\n").split("\t")
            pid = parts[i_id]
            if pid not in wanted:
                continue
            chrom = parts[i_chr]
            pos = parts[i_pos]
            if not chrom or chrom == "NA" or not pos or pos == "NA":
                continue
            chrom_n = chrom[3:] if chrom.startswith("chr") else chrom
            # CpG_beg is BED-style zero-based; sample H5 positions are one-based.
            coord[pid] = (chrom_n, int(float(pos)) + 1)

    markers = []
    for pid in comp.index.astype(str):
        if pid not in coord:
            continue
        chrom, pos = coord[pid]
        markers.append(
            {
                "probe_id": pid,
                "chrom": chrom,
                "pos": pos,
                "context": "CG",
                "betas": {ct: float(comp.loc[pid, ct]) for ct in cell_types},
            }
        )

    asset = {
        "schema_version": 1,
        "source_package": "FlowSorted.Blood.EPIC",
        "source_objects": ["IDOLOptimizedCpGs", "IDOLOptimizedCpGs.compTable"],
        "gse": "GSE110554",
        "citation": (
            "Salas et al. 2018; IDOL optimized adult blood deconvolution "
            "(FlowSorted.Blood.EPIC)"
        ),
        "genome_build": "hg38",
        "coordinate_source": "Zhou lab EPIC.hg38.manifest (zhouserver InfiniumAnnotation)",
        "coordinate_convention": "1-based",
        "cell_types": cell_types,
        "n_markers": len(markers),
        "markers": markers,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(asset, indent=2), encoding="utf-8")
    print(f"Wrote {args.output} with {len(markers)} markers")


if __name__ == "__main__":
    main()
