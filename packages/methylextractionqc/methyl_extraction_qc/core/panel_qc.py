"""On-target depth and control-region methylation for EM-Seq capture QC."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


def load_bed(path: str | Path) -> List[Tuple[str, int, int]]:
    rows: List[Tuple[str, int, int]] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip() or line.startswith(("#", "track", "browser")):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            rows.append((parts[0], int(parts[1]), int(parts[2])))
    return rows


def _in_intervals(chrom: str, pos: int, intervals: Sequence[Tuple[str, int, int]]) -> bool:
    for c, start, end in intervals:
        if c == chrom and start <= pos < end:
            return True
    return False


def _read_sites(h5_path: Path) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    try:
        import h5py
    except ImportError:
        return None
    with h5py.File(h5_path, "r") as f:
        if "methylation_data" not in f:
            return None
        md = f["methylation_data"]
        if hasattr(md, "dtype") and getattr(md.dtype, "names", None):
            pos = np.asarray(md["pos"][:], dtype=np.int64)
            mc = np.asarray(md["mC"][:], dtype=np.float64)
            uc = np.asarray(md["uC"][:], dtype=np.float64)
            return pos, mc, uc
        if hasattr(md, "keys"):
            if "pos" not in md or "mC" not in md or "uC" not in md:
                return None
            return (
                np.asarray(md["pos"][:], dtype=np.int64),
                np.asarray(md["mC"][:], dtype=np.float64),
                np.asarray(md["uC"][:], dtype=np.float64),
            )
    return None


def _chrom_from_name(name: str) -> str:
    # {chrom}-CG.h5
    return name.split("-")[0]


def compute_panel_qc(
    sample_dir: Path,
    *,
    target_panel_bed: Optional[str] = None,
    pos_control_bed: Optional[str] = None,
    neg_control_bed: Optional[str] = None,
) -> Dict[str, Any]:
    """Return panel-aware metrics from extract H5 files (no second BAM walk)."""
    root = Path(sample_dir)
    h5s = [
        p
        for p in root.glob("*-CG.h5")
        if not p.name.endswith(".patterns.h5") and not p.name.endswith(".mhap.h5")
    ]
    panel = load_bed(target_panel_bed) if target_panel_bed else []
    pos_ctl = load_bed(pos_control_bed) if pos_control_bed else []
    neg_ctl = load_bed(neg_control_bed) if neg_control_bed else []

    n_sites = 0
    n_on = 0
    cov_on: List[float] = []
    pos_meth: List[float] = []
    neg_meth: List[float] = []
    for path in h5s:
        chrom = _chrom_from_name(path.name)
        sites = _read_sites(path)
        if sites is None:
            continue
        pos, mc, uc = sites
        cov = mc + uc
        meth = np.divide(mc, cov, out=np.full_like(mc, np.nan), where=cov > 0)
        n_sites += int(pos.size)
        if panel:
            mask = np.array([_in_intervals(chrom, int(p), panel) for p in pos.tolist()])
            n_on += int(mask.sum())
            if np.any(mask):
                cov_on.extend(cov[mask].tolist())
        if pos_ctl:
            mask = np.array([_in_intervals(chrom, int(p), pos_ctl) for p in pos.tolist()])
            if np.any(mask):
                pos_meth.extend(meth[mask][np.isfinite(meth[mask])].tolist())
        if neg_ctl:
            mask = np.array([_in_intervals(chrom, int(p), neg_ctl) for p in pos.tolist()])
            if np.any(mask):
                neg_meth.extend(meth[mask][np.isfinite(meth[mask])].tolist())

    on_target_fraction = (n_on / n_sites) if n_sites and panel else None
    on_target_mean_coverage = float(np.mean(cov_on)) if cov_on else None
    on_target_median_coverage = float(np.median(cov_on)) if cov_on else None
    return {
        "n_sites": n_sites,
        "n_on_target": n_on,
        "on_target_fraction": on_target_fraction,
        "on_target_mean_coverage": on_target_mean_coverage,
        "on_target_median_coverage": on_target_median_coverage,
        "pos_control_mean_methylation": float(np.mean(pos_meth)) if pos_meth else None,
        "neg_control_mean_methylation": float(np.mean(neg_meth)) if neg_meth else None,
    }
