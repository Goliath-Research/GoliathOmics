#!/usr/bin/env python3
"""Rebuild Hannum 2013 71-CpG blood clock JSON (dev-time; not a worker path).

Downloads published Table S3 coefficients and maps probes to GRCh38 via Zhou lab
HM450.hg38.manifest. Commit the JSON; do not download at runtime.
"""

from __future__ import annotations

import argparse
import gzip
import json
import urllib.request
from pathlib import Path

DEFAULT_OUT = (
    Path(__file__).resolve().parents[1] / "data" / "confounder_panels" / "hannum2013_v1.json"
)
# Published Table S3 coefficients as redistributed by methylclock (same 71 markers).
COEF_RDA_URL = (
    "https://raw.githubusercontent.com/isglobal-brge/methylclock/main/data/coefHannum.rda"
)
MANIFEST_URL = (
    "https://zhouserver.research.chop.edu/InfiniumAnnotation/current/HM450/"
    "HM450.hg38.manifest.tsv.gz"
)
CITATION = (
    "Hannum et al. Mol Cell 2013 doi:10.1016/j.molcel.2012.10.016; "
    "71 methylation markers from Table S3 (CoefficientTraining). Coordinates are "
    "GRCh38 via Zhou lab HM450.hg38.manifest (1-based). Residualization covariate, "
    "not a product DNAmAge."
)
_UA = "MethylPipeline-hannum-panel-builder/1.0"


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req) as resp:
        dest.write_bytes(resp.read())


def _load_coefficients(work: Path) -> list[tuple[str, float]]:
    try:
        import pyreadr
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("pyreadr is required to rebuild the Hannum panel") from exc

    rda = work / "coefHannum.rda"
    _download(COEF_RDA_URL, rda)
    df = pyreadr.read_r(str(rda))["coefHannum"]
    rows: list[tuple[str, float]] = []
    for _, rec in df.iterrows():
        marker = str(rec["CpGmarker"]).strip()
        weight = float(rec["CoefficientTraining"])
        if marker and marker.lower() != "nan":
            rows.append((marker, weight))
    return rows


def _load_coords(manifest_path: Path, wanted: set[str]) -> dict[str, tuple[str, int, str]]:
    coord: dict[str, tuple[str, int, str]] = {}
    with gzip.open(manifest_path, "rt") as f:
        header = f.readline().rstrip("\n").split("\t")
        i_id = header.index("probeID")
        i_chr = header.index("CpG_chrm")
        i_pos = header.index("CpG_beg")
        i_gene = header.index("gene") if "gene" in header else None
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
            gene = ""
            if i_gene is not None and i_gene < len(parts):
                gene = parts[i_gene].split(";")[0].strip()
                if gene in {"NA", "."}:
                    gene = ""
            coord[pid] = (chrom_n, int(float(pos)) + 1, gene)
    return coord


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workdir", type=Path, default=Path("/tmp/hannum2013_rebuild"))
    args = parser.parse_args()

    work = args.workdir
    work.mkdir(parents=True, exist_ok=True)
    man_path = work / "HM450.hg38.manifest.tsv.gz"
    _download(MANIFEST_URL, man_path)

    coefs = _load_coefficients(work)
    if len(coefs) != 71:
        raise SystemExit(f"Expected 71 Hannum markers, got {len(coefs)}")
    wanted = {m for m, _ in coefs}
    coord = _load_coords(man_path, wanted)
    missing = sorted(wanted - set(coord))
    if missing:
        raise SystemExit(f"Missing GRCh38 coordinates for {len(missing)} probes: {missing[:8]}")

    sites = []
    for marker, weight in coefs:
        chrom, position, gene = coord[marker]
        site = {
            "illumina_id": marker,
            "chrom": chrom,
            "position": position,
            "weight": weight,
        }
        if gene:
            site["gene"] = gene
        sites.append(site)

    panel = {
        "panel_id": "hannum2013_v1",
        "genome": "GRCh38",
        "score_name": "age_score",
        "score_kind": "weighted_beta",
        "citation": CITATION,
        "intercept": 0.0,
        "coordinate_source": "Zhou lab HM450.hg38.manifest (zhouserver InfiniumAnnotation)",
        "coordinate_convention": "1-based",
        "sites": sites,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(panel, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} with {len(sites)} sites")


if __name__ == "__main__":
    main()
