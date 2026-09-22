"""Load FlowSorted/IDOL seed basis and run Houseman constrained projection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

from methyl_utils.gpu_detection import get_cupy, is_gpu_available

from ..config import CellDeconvRuntimeParams

DEFAULT_SEED_BASIS = (
    Path(__file__).resolve().parent.parent / "data" / "flowsorted_blood_epic_idol_v1.json"
)


@dataclass(frozen=True)
class SeedBasis:
    cell_types: Tuple[str, ...]
    probe_ids: Tuple[str, ...]
    chroms: Tuple[str, ...]
    contexts: Tuple[str, ...]  # per-marker methylation context (CG/CHG/CHH)
    positions: np.ndarray  # int64, shape (n_markers,)
    M: np.ndarray  # float64, shape (n_markers, n_cell_types)
    provenance: Dict[str, Any]


def default_seed_basis_path() -> Path:
    return DEFAULT_SEED_BASIS


def load_seed_basis(path: Optional[str | Path] = None) -> SeedBasis:
    p = Path(path) if path else default_seed_basis_path()
    raw = json.loads(p.read_text(encoding="utf-8"))
    markers = raw.get("markers") or []
    if not markers:
        raise ValueError(f"Seed basis has no markers: {p}")
    cell_types = tuple(str(c) for c in (raw.get("cell_types") or list((markers[0].get("betas") or {}).keys())))
    if not cell_types:
        raise ValueError(f"Seed basis missing cell_types: {p}")
    probe_ids: List[str] = []
    chroms: List[str] = []
    contexts: List[str] = []
    positions: List[int] = []
    rows: List[List[float]] = []
    for m in markers:
        betas = m.get("betas") or {}
        probe_ids.append(str(m["probe_id"]))
        chroms.append(str(m["chrom"]))
        ctx = str(m.get("context") or "CG").strip().upper() or "CG"
        contexts.append(ctx)
        positions.append(int(m["pos"]))
        rows.append([float(betas[ct]) for ct in cell_types])
    provenance = {
        "seed_basis_path": str(p.resolve()),
        "source_package": raw.get("source_package"),
        "gse": raw.get("gse"),
        "citation": raw.get("citation"),
        "genome_build": raw.get("genome_build"),
        "coordinate_convention": raw.get("coordinate_convention"),
        "n_markers": len(markers),
        "cell_types": list(cell_types),
    }
    return SeedBasis(
        cell_types=cell_types,
        probe_ids=tuple(probe_ids),
        chroms=tuple(chroms),
        contexts=tuple(contexts),
        positions=np.asarray(positions, dtype=np.int64),
        M=np.asarray(rows, dtype=np.float64),
        provenance=provenance,
    )


def _want_gpu(use_gpu: Optional[bool]) -> bool:
    if use_gpu is False:
        return False
    return bool(is_gpu_available())


def houseman_qp(
    M: np.ndarray,
    y: np.ndarray,
    *,
    use_gpu: Optional[bool] = None,
) -> np.ndarray:
    """
    Constrained projection: minimize ||y - M Ω||^2 s.t. Ω >= 0, sum(Ω) = 1.

    ``M`` is (n_markers, n_cell_types); ``y`` is (n_markers,).
    """
    M = np.asarray(M, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if M.ndim != 2 or y.ndim != 1 or M.shape[0] != y.shape[0]:
        raise ValueError(f"Shape mismatch M={M.shape} y={y.shape}")
    n_ct = M.shape[1]
    if n_ct == 0:
        raise ValueError("M has zero cell types")

    # Optional CuPy residual evaluation for large marker panels / batching hooks.
    # The QP itself uses SciPy SLSQP on host (tiny K).
    if _want_gpu(use_gpu):
        cp = get_cupy()
        if cp is not None:
            _ = cp.asarray(M)  # touch device / validate CuPy path
            _ = cp.asarray(y)

    x0 = np.full(n_ct, 1.0 / n_ct, dtype=np.float64)

    def loss(w: np.ndarray) -> float:
        r = y - M @ w
        return float(np.dot(r, r))

    def jac(w: np.ndarray) -> np.ndarray:
        r = y - M @ w
        return -2.0 * (M.T @ r)

    cons = {"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}
    bounds = [(0.0, 1.0)] * n_ct
    res = minimize(
        loss,
        x0,
        method="SLSQP",
        jac=jac,
        bounds=bounds,
        constraints=cons,
        options={"maxiter": 500, "ftol": 1e-12},
    )
    w = np.asarray(res.x, dtype=np.float64)
    w = np.clip(w, 0.0, 1.0)
    s = float(np.sum(w))
    if s <= 0.0:
        return x0
    return w / s


def markers_by_chrom(basis: SeedBasis) -> Dict[str, np.ndarray]:
    """Map chromosome -> row indices into basis arrays."""
    out: Dict[str, List[int]] = {}
    for i, chrom in enumerate(basis.chroms):
        out.setdefault(str(chrom), []).append(i)
    return {k: np.asarray(v, dtype=np.int64) for k, v in out.items()}


def _resolve_h5_path(sample_dir: Path, chrom: str, ctx: str) -> Optional[Path]:
    path = sample_dir / f"{chrom}-{ctx}.h5"
    if path.is_file():
        return path
    alt = sample_dir / f"chr{chrom}-{ctx}.h5"
    return alt if alt.is_file() else None


def extract_marker_vector(
    sample_dir: str | Path,
    basis: SeedBasis,
    cfg: CellDeconvRuntimeParams,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build Y and coverage mask aligned to ``basis`` marker order from sample H5 files.

    Each marker is read from ``{chrom}-{marker.context}.h5`` when that file exists;
    otherwise configured ``cfg.contexts`` are scanned and only still-unobserved
    markers are filled (no first-context ``break``).
    """
    from methyl_utils import MethylSample

    sample_dir = Path(sample_dir)
    y = np.full(basis.M.shape[0], np.nan, dtype=np.float64)
    observed = np.zeros(basis.M.shape[0], dtype=bool)
    min_coverage = int(cfg.marker_min_coverage)
    allowed_ctx = {str(c).strip().upper() for c in (cfg.contexts or []) if str(c).strip()}

    # Group marker indices by (chrom, preferred context).
    by_key: Dict[Tuple[str, str], List[int]] = {}
    for i, chrom in enumerate(basis.chroms):
        ctx = str(basis.contexts[i] if i < len(basis.contexts) else "CG").strip().upper() or "CG"
        by_key.setdefault((str(chrom), ctx), []).append(i)

    def _fill_from_h5(path: Path, row_idx: List[int]) -> None:
        want_pos = basis.positions[np.asarray(row_idx, dtype=np.int64)].astype(np.uint32)
        sample = MethylSample.load_from_h5(path, positions=want_pos, align_positions=True)
        beta = np.asarray(sample.get_methylation_levels(), dtype=np.float64)
        cov = np.asarray(sample.get_coverage(), dtype=np.float64)
        pos = np.asarray(sample.pos, dtype=np.uint32)
        pos_to_i = {int(p): i for i, p in enumerate(pos)}
        for j, basis_i in enumerate(row_idx):
            if observed[int(basis_i)]:
                continue
            p = int(want_pos[j])
            if p not in pos_to_i:
                continue
            ii = pos_to_i[p]
            if not np.isfinite(beta[ii]) or cov[ii] < min_coverage:
                continue
            y[int(basis_i)] = float(beta[ii])
            observed[int(basis_i)] = True

    for (chrom, marker_ctx), row_idx in by_key.items():
        # Prefer the marker's own context H5.
        path = _resolve_h5_path(sample_dir, chrom, marker_ctx)
        if path is not None:
            _fill_from_h5(path, row_idx)
        # Fallback: scan remaining configured contexts for still-missing markers.
        for ctx in cfg.contexts:
            ctx_u = str(ctx).strip().upper()
            if ctx_u == marker_ctx:
                continue
            if allowed_ctx and ctx_u not in allowed_ctx:
                continue
            path = _resolve_h5_path(sample_dir, chrom, ctx_u)
            if path is None:
                continue
            pending = [i for i in row_idx if not observed[i]]
            if not pending:
                break
            _fill_from_h5(path, pending)
    return y, observed


def deconvolve_sample(
    sample_dir: str | Path,
    basis: SeedBasis,
    cfg: CellDeconvRuntimeParams,
) -> Dict[str, Any]:
    """Estimate Ω for one sample using typed runtime params (no code defaults)."""
    y, observed = extract_marker_vector(sample_dir, basis, cfg)
    n_obs = int(np.sum(observed))
    frac = float(n_obs) / float(basis.M.shape[0]) if basis.M.shape[0] else 0.0
    row: Dict[str, Any] = {
        "n_markers_total": int(basis.M.shape[0]),
        "n_markers_observed": n_obs,
        "marker_fraction": frac,
    }
    if frac < float(cfg.min_marker_fraction) or n_obs < len(basis.cell_types):
        for ct in basis.cell_types:
            row[ct] = float("nan")
        row["qp_status"] = "insufficient_markers"
        return row

    M_obs = basis.M[observed]
    y_obs = y[observed]
    omega = houseman_qp(M_obs, y_obs, use_gpu=cfg.use_gpu)
    for ct, w in zip(basis.cell_types, omega):
        row[ct] = float(w)
    row["qp_status"] = "ok"
    return row
