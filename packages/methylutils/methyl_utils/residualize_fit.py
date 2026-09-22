"""Fit per-chromosome M-value residualization coefficients from sample H5 files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd

from methyl_utils.core.io import load_from_h5
from methyl_utils.mvalue_residualize import (
    ResidualizeModel,
    beta_to_m,
    design_matrix_from_covariates,
    fit_ols_mvalues,
    write_manifest,
)
from methyl_utils.residualize_config import ResidualizeStepConfig


def _load_chrom(sample_dir: str | Path, chrom: str, ctx: str):
    path = Path(sample_dir) / f"{chrom}-{ctx}.h5"
    if not path.is_file():
        return None
    return load_from_h5(path)


def load_joined_covariates(paths: Sequence[str], sample_id_column: str) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_file():
            raise FileNotFoundError(f"Covariate CSV not found: {p}")
        df = pd.read_csv(p)
        if sample_id_column not in df.columns:
            raise ValueError(f"{p} missing {sample_id_column!r}")
        df[sample_id_column] = df[sample_id_column].astype(str)
        frames.append(df)
    out = frames[0]
    for extra in frames[1:]:
        overlap = [c for c in extra.columns if c != sample_id_column and c in out.columns]
        extra = extra.drop(columns=overlap)
        out = out.merge(extra, on=sample_id_column, how="inner")
    return out


def _position_betas(
    samples: Sequence[Tuple[str, str]],
    chrom: str,
    ctx: str,
    min_coverage: int,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Return positions, beta matrix (n_pos × n_samples), and sample_ids (complete cases)."""
    loaded = []
    ids: List[str] = []
    for sid, sdir in samples:
        sample = _load_chrom(sdir, chrom, ctx)
        if sample is None:
            continue
        loaded.append(sample)
        ids.append(str(sid))
    if not loaded:
        return np.array([], dtype=np.uint32), np.zeros((0, 0)), []
    pos_sets = []
    for sample in loaded:
        pos = np.asarray(sample.pos, dtype=np.uint32).ravel()
        cov = np.asarray(sample.get_coverage(), dtype=np.float64).ravel()
        n = min(pos.size, cov.size)
        pos_sets.append(set(pos[:n][cov[:n] >= float(min_coverage)].tolist()))
    common = pos_sets[0]
    for s in pos_sets[1:]:
        common &= s
    if not common:
        for sample in loaded:
            try:
                sample.close()
            except Exception:
                pass
        return np.array([], dtype=np.uint32), np.zeros((0, 0)), []
    positions = np.array(sorted(common), dtype=np.uint32)
    cols = []
    for sample in loaded:
        vals, avail = sample.lookup_at_positions(
            positions, min_coverage=min_coverage, missing_value=np.nan
        )
        cols.append(np.asarray(vals, dtype=np.float64))
        try:
            sample.close()
        except Exception:
            pass
    mat = np.column_stack(cols)
    ok = np.all(np.isfinite(mat), axis=1)
    return positions[ok], mat[ok], ids


def run_residualize_fit(
    samples: Sequence[Tuple[str, str]],
    output_dir: Path,
    cfg: ResidualizeStepConfig,
    *,
    project_chromosomes: Sequence[str],
) -> Dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if cfg.covariates_path is None:
        raise ValueError("actionConfig.residualize.covariates_path is required.")
    if cfg.m_value_eps is None:
        raise ValueError("actionConfig.residualize.m_value_eps is required.")
    if cfg.min_coverage is None:
        raise ValueError("actionConfig.residualize.min_coverage is required.")
    if cfg.variance_threshold is None:
        raise ValueError("actionConfig.residualize.variance_threshold is required.")
    sid_col = cfg.sample_id_column or "sample_id"
    cov = load_joined_covariates(cfg.covariates_path, sid_col)
    sample_ids = [s for s, _ in samples]
    z, names, dropped, missing = design_matrix_from_covariates(
        cov,
        sample_ids,
        numeric_columns=cfg.numeric_columns,
        composition_columns=cfg.composition_columns,
        composition_reference=cfg.composition_reference,
        composition_pseudocount=cfg.composition_pseudocount,
        sample_id_column=sid_col,
        variance_threshold=float(cfg.variance_threshold),
    )
    if missing:
        raise ValueError(
            "residualize_fit: covariates missing train sample_id(s) "
            f"{missing[:8]}{'…' if len(missing) > 8 else ''}."
        )
    id_to_dir = {sid: sdir for sid, sdir in samples}
    aligned_samples = [(sid, id_to_dir[sid]) for sid in sample_ids]
    chroms = [str(c) for c in (cfg.chromosomes or project_chromosomes)]
    ctxs = [str(c) for c in (cfg.contexts or ["CG"])]
    files: List[str] = []
    for chrom in chroms:
        for ctx in ctxs:
            pos, beta, used_ids = _position_betas(
                aligned_samples, chrom, ctx, int(cfg.min_coverage)
            )
            if pos.size == 0:
                continue
            # Reorder Z to used_ids (should already match sample_ids).
            order = [sample_ids.index(u) for u in used_ids]
            z_use = z[order]
            m = beta_to_m(beta, float(cfg.m_value_eps))
            coef = fit_ols_mvalues(m, z_use)
            model = ResidualizeModel(
                chrom=str(chrom),
                ctx=str(ctx),
                positions=pos,
                coef=coef,
                covariate_names=names,
                eps=float(cfg.m_value_eps),
                dropped_covariates=dropped,
                train_sample_ids=used_ids,
            )
            files.append(str(model.save(output_dir)))
    extra_manifest = {
        "n_files": len(files),
        "chromosomes": chroms,
        "contexts": ctxs,
        "covariates_path": list(cfg.covariates_path),
        "numeric_columns": list(cfg.numeric_columns or []),
        "composition_columns": list(cfg.composition_columns or []),
        "composition_reference": cfg.composition_reference,
        "composition_pseudocount": cfg.composition_pseudocount,
        "sample_id_column": sid_col,
        "variance_threshold": float(cfg.variance_threshold),
        "min_coverage": int(cfg.min_coverage),
    }
    manifest = write_manifest(
        output_dir,
        files=files,
        covariate_names=names,
        dropped_covariates=dropped,
        train_sample_ids=sample_ids,
        eps=float(cfg.m_value_eps),
        extra=extra_manifest,
    )
    contract = {
        "covariates_path": list(cfg.covariates_path),
        "numeric_columns": list(cfg.numeric_columns or []),
        "composition_columns": list(cfg.composition_columns or []),
        "composition_reference": cfg.composition_reference,
        "composition_pseudocount": cfg.composition_pseudocount,
        "sample_id_column": sid_col,
        "eps": float(cfg.m_value_eps),
        "covariate_names": names,
        "dropped_covariates": dropped,
    }
    (output_dir / "apply_contract.json").write_text(
        json.dumps(contract, indent=2) + "\n", encoding="utf-8"
    )
    return json.loads(manifest.read_text(encoding="utf-8"))
