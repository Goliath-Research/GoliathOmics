"""Load DMP panels from MC run directories."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple

import pandas as pd

DISCOVERY_DMP_CSV_PATTERN = "dmps-*-discovery.csv"
SELECTED_DMP_CSV_PATTERN = "dmps-*-selected.csv"
CLASSIFIER_DMP_CSV_PATTERN = "dmps-*-classifier.csv"
CLASSIFIER_EXTENDED_DMP_CSV_PATTERN = "dmps-*-classifier-extended.csv"
STABLE_DMP_CSV_GLOB = "stable_dmps*.csv"


def _iter_detection_dirs(run_dir: Path) -> list:
    detection_dirs = list(run_dir.glob("**/detections/*/*"))
    if not detection_dirs:
        detection_dirs = list(run_dir.glob("detections/*/*"))
    return detection_dirs


def _read_csv_frames(csv_paths: list) -> list:
    frames: list = []
    for csv in csv_paths:
        try:
            frames.append(pd.read_csv(csv))
        except Exception:
            continue
    return frames


def _collect_dmp_csv_frames(
    run_dir: Path,
    *,
    glob_pattern: str,
    exclude_extended: bool = False,
) -> list:
    frames: list = []
    for d in _iter_detection_dirs(run_dir):
        csvs = sorted(d.glob(glob_pattern))
        if exclude_extended:
            csvs = [p for p in csvs if not p.stem.endswith("-classifier-extended")]
        frames.extend(_read_csv_frames(csvs))
    return frames


def _collect_classifier_dmp_csv_frames(run_dir: Path) -> list:
    """Collect classifier DMP CSVs, preferring extended exports per detection directory."""
    frames: list = []
    for d in _iter_detection_dirs(run_dir):
        csvs = sorted(d.glob(CLASSIFIER_EXTENDED_DMP_CSV_PATTERN))
        if not csvs:
            csvs = sorted(
                p
                for p in d.glob(CLASSIFIER_DMP_CSV_PATTERN)
                if not p.stem.endswith("-classifier-extended")
            )
        frames.extend(_read_csv_frames(csvs))
    return frames


def _dmp_key_from_row(row: Any) -> Tuple[Any, int]:
    chrom = row["chromosome"]
    try:
        chrom_n = int(chrom)
    except (TypeError, ValueError):
        chrom_n = str(chrom).strip()
    return (chrom_n, int(row["position"]))


def _dedupe_dmp_frame(df: pd.DataFrame, *, max_dmps: Optional[int] = None) -> pd.DataFrame:
    if df.empty:
        return df
    if "chromosome" not in df.columns or "position" not in df.columns:
        return df

    work = df.copy()
    if "effect_size" not in work.columns:
        work["effect_size"] = 0.0
    work["_abs_effect"] = pd.to_numeric(work["effect_size"], errors="coerce").fillna(0.0).abs()
    keys: List[Tuple[Any, int]] = []
    for _, row in work.iterrows():
        try:
            keys.append(_dmp_key_from_row(row))
        except Exception:
            keys.append((None, -1))
    work["_dmp_key"] = keys
    work = work[work["_dmp_key"].map(lambda k: k[1] >= 0)].copy()
    work = work.sort_values(["_abs_effect"], ascending=False, na_position="last")
    work = work.drop_duplicates(subset=["_dmp_key"], keep="first")
    if max_dmps is not None and int(max_dmps) > 0 and len(work) > int(max_dmps):
        work = work.head(int(max_dmps)).copy()
    return work.drop(columns=["_abs_effect", "_dmp_key"], errors="ignore")


def load_discovery_dmp_panel(
    run_dir: Path,
    *,
    max_dmps: Optional[int] = None,
) -> Optional[pd.DataFrame]:
    """
    Load detector discovery DMP exports for gene-axis feature building.

    Uses ``dmps-*-discovery.csv`` from each comparison/chromosome under ``run_dir``.
    """
    frames = _collect_dmp_csv_frames(run_dir, glob_pattern=DISCOVERY_DMP_CSV_PATTERN)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    return _dedupe_dmp_frame(df, max_dmps=max_dmps)


def load_selected_dmp_panel(
    run_dir: Path,
    *,
    max_dmps: Optional[int] = None,
) -> Optional[pd.DataFrame]:
    """Load unified selected DMP panel (dmps-*-selected.csv), else classifier core."""
    frames = _collect_dmp_csv_frames(run_dir, glob_pattern=SELECTED_DMP_CSV_PATTERN)
    if not frames:
        return load_classifier_dmp_panel(run_dir, max_dmps=max_dmps)
    df = pd.concat(frames, ignore_index=True)
    return _dedupe_dmp_frame(df, max_dmps=max_dmps)


def load_stable_dmp_panel(
    run_dir: Path,
    *,
    stable_csv: Optional[str | Path] = None,
    max_dmps: Optional[int] = None,
) -> Optional[pd.DataFrame]:
    """
    Load frozen stable DMP panel for Phase B gene modeling.

    Resolution order: explicit ``stable_csv``, run-local stable exports, parent stability dir.
    """
    candidates: List[Path] = []
    if stable_csv:
        p = Path(stable_csv)
        if p.is_file():
            candidates.append(p)
    for d in _iter_detection_dirs(run_dir):
        candidates.extend(sorted(d.glob(STABLE_DMP_CSV_GLOB)))
    stability_dir = run_dir.parent / "stability"
    if stability_dir.is_dir():
        for name in ("stable_dmps_production.csv", "stable_dmps_genomewide.csv"):
            p = stability_dir / name
            if p.is_file():
                candidates.append(p)
    seen: set = set()
    frames: list = []
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            df = pd.read_csv(path)
            if {"chromosome", "position"}.issubset(df.columns):
                frames.append(df)
        except Exception:
            continue
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    return _dedupe_dmp_frame(df, max_dmps=max_dmps)


def load_classifier_dmp_panel(
    run_dir: Path,
    *,
    max_dmps: Optional[int] = None,
) -> Optional[pd.DataFrame]:
    """
    Load detector classifier DMP panel for gene-axis work.

    Prefers ``dmps-*-classifier-extended.csv`` per detection directory, else core
    classifier CSV for that directory.
    """
    frames = _collect_classifier_dmp_csv_frames(run_dir)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    return _dedupe_dmp_frame(df, max_dmps=max_dmps)
