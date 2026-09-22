"""
Resolve DMP CSV paths for dual-branch exports (discovery vs classifier).

MethylDetector ``dmp_export_mode=dual`` writes:
  - ``dmps-{chrom}-discovery.csv`` — broad biology
  - ``dmps-{chrom}-classifier.csv`` — core prediction panel (matches classifier pickle)
  - ``dmps-{chrom}-classifier-extended.csv`` — mapper / gene annotation (k_core + margin)

Unified mode writes a single ``dmps-{chrom}.csv`` (classifier panel; may be widened for mapper).
"""

from __future__ import annotations

from pathlib import Path
from typing import List


def find_classifier_dmps_csvs(detection_dir: Path) -> List[Path]:
    """
    Per-chromosome classifier / prediction DMP tables for multiclass merge and similar tools.

    Prefers ``dmps-*-classifier.csv`` when present; otherwise legacy ``dmps-*.csv`` excluding
    ``*-discovery`` stems.
    """
    if not detection_dir.exists():
        return []
    classifier = sorted(
        p
        for p in detection_dir.glob("dmps-*-classifier.csv")
        if not p.stem.endswith("-classifier-extended")
    )
    if classifier:
        return classifier
    out: List[Path] = []
    for p in sorted(detection_dir.glob("dmps-*.csv")):
        stem = p.stem
        if stem.endswith("-discovery"):
            continue
        out.append(p)
    return out


def find_discovery_dmps_csvs(detection_dir: Path) -> List[Path]:
    """DMP tables intended for MethylMapper / MethylEnricher (broad lists)."""
    if not detection_dir.exists():
        return []
    disc = sorted(detection_dir.glob("dmps-*-discovery.csv"))
    if disc:
        return disc
    # Unified mode: same file serves both roles
    return find_classifier_dmps_csvs(detection_dir)


def glob_discovery_dmps_with_unified_fallback(search_dir: Path, pattern: str) -> List[Path]:
    """
    When a configured glob like ``dmps-*-discovery.csv`` matches nothing (e.g. unified
    ``dmp_export_mode`` wrote ``dmps-{chr}.csv`` only), resolve the same logical inputs
    as :func:`find_discovery_dmps_csvs` per target directory.

    ``pattern`` is relative to ``search_dir`` (e.g. ``dmps-*-discovery.csv`` or
    ``*/*/dmps-*-discovery.csv`` for comparison layout). If ``pattern`` does not end
    with ``dmps-*-discovery.csv``, returns an empty list.
    """
    norm = pattern.replace("\\", "/")
    suffix = "dmps-*-discovery.csv"
    if not norm.endswith(suffix):
        return []
    prefix = norm[: -len(suffix)].rstrip("/")
    if not prefix:
        return find_discovery_dmps_csvs(search_dir)
    out: List[Path] = []
    for d in sorted(search_dir.glob(prefix)):
        if d.is_dir():
            out.extend(find_discovery_dmps_csvs(d))
    return sorted(out)


def first_classifier_dmps_csv(detection_dir: Path) -> Path | None:
    csvs = find_classifier_dmps_csvs(detection_dir)
    return csvs[0] if csvs else None
