"""
DMP CSV path resolution for dual-branch exports (discovery vs classifier).

Duplicated here so ``methyl-detector`` does not require an upgraded ``methyl_utils``
install (the same helpers also live in ``methyl_utils.dmp_export_paths``).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional


def find_classifier_extended_dmps_csvs(detection_dir: Path) -> List[Path]:
    if not detection_dir.exists():
        return []
    return sorted(detection_dir.glob("dmps-*-classifier-extended.csv"))


def find_classifier_dmps_csvs(detection_dir: Path) -> List[Path]:
    if not detection_dir.exists():
        return []
    classifier = sorted(
        p for p in detection_dir.glob("dmps-*-classifier.csv") if not p.stem.endswith("-classifier-extended")
    )
    if classifier:
        return classifier
    out: List[Path] = []
    for p in sorted(detection_dir.glob("dmps-*.csv")):
        if p.stem.endswith("-discovery"):
            continue
        out.append(p)
    return out


def find_discovery_dmps_csvs(detection_dir: Path) -> List[Path]:
    if not detection_dir.exists():
        return []
    disc = sorted(detection_dir.glob("dmps-*-discovery.csv"))
    if disc:
        return disc
    return find_classifier_dmps_csvs(detection_dir)


def first_classifier_dmps_csv(detection_dir: Path) -> Optional[Path]:
    csvs = find_classifier_dmps_csvs(detection_dir)
    return csvs[0] if csvs else None
