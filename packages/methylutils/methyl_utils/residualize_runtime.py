"""Load a ResidualizeApplier from a coefficient directory (opt-in centroid/classifier)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from methyl_utils.mvalue_residualize import (
    MANIFEST_NAME,
    ResidualizeApplier,
    ResidualizeModel,
    load_model_for_chrom,
)
from methyl_utils.residualize_fit import load_joined_covariates


def load_applier(
    coef_dir: str | Path,
    chrom: str,
    ctx: str,
) -> Optional[ResidualizeApplier]:
    """Return None when the directory or chromosome model is absent (parity path)."""
    d = Path(coef_dir)
    if not d.is_dir():
        return None
    model = load_model_for_chrom(d, str(chrom), str(ctx))
    if model is None:
        return None
    man_path = d / MANIFEST_NAME
    if not man_path.is_file():
        raise FileNotFoundError(f"Missing {MANIFEST_NAME} in {d}")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    paths = man.get("covariates_path") or []
    sid = str(man.get("sample_id_column") or "sample_id")
    cov = load_joined_covariates([str(p) for p in paths], sid)
    return ResidualizeApplier(
        model,
        cov,
        numeric_columns=man.get("numeric_columns") or None,
        composition_columns=man.get("composition_columns") or None,
        composition_reference=man.get("composition_reference"),
        composition_pseudocount=man.get("composition_pseudocount"),
        sample_id_column=sid,
    )
