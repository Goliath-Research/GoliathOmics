"""Label-free methylation confounder scores (smoking, clocks, BMI, inflammation)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from methyl_utils.core.io import load_from_h5

PACKAGED_PANELS = Path(__file__).resolve().parent / "data" / "confounder_panels"


class PanelSite(BaseModel):
    model_config = ConfigDict(extra="ignore")

    illumina_id: Optional[str] = None
    gene: Optional[str] = None
    chrom: str
    position: int
    weight: float


class ScorePanel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    panel_id: str
    genome: str = "GRCh38"
    score_name: str
    score_kind: str = "weighted_beta"
    citation: Optional[str] = None
    intercept: float = 0.0
    sites: List[PanelSite] = Field(default_factory=list)


def packaged_panel_path(name: str) -> Path:
    return PACKAGED_PANELS / name


def load_panel(path: str | Path) -> ScorePanel:
    p = Path(path)
    if not p.is_file():
        packaged = PACKAGED_PANELS / p.name
        if packaged.is_file():
            p = packaged
        else:
            raise FileNotFoundError(f"Confounder panel not found: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return ScorePanel.model_validate(data)


def _load_chrom_sample(sample_dir: str | Path, chrom: str, context: str):
    path = Path(sample_dir) / f"{chrom}-{context}.h5"
    if not path.is_file():
        return None
    return load_from_h5(path)


def score_sample_panel(
    sample_dir: str | Path,
    panel: ScorePanel,
    *,
    contexts: Sequence[str],
    min_coverage: int,
) -> Tuple[float, int, int, str]:
    """Return (score, n_used, n_sites, status)."""
    n_sites = len(panel.sites)
    if n_sites == 0:
        return float("nan"), 0, 0, "empty_panel"
    weighted = 0.0
    used = 0
    for site in panel.sites:
        value: Optional[float] = None
        for ctx in contexts:
            sample = _load_chrom_sample(sample_dir, str(site.chrom), str(ctx))
            if sample is None:
                continue
            try:
                vals, avail = sample.lookup_at_positions(
                    np.array([int(site.position)], dtype=np.uint32),
                    min_coverage=int(min_coverage),
                    missing_value=np.nan,
                )
            finally:
                try:
                    sample.close()
                except Exception:
                    pass
            if avail.size and bool(avail[0]) and np.isfinite(vals[0]):
                value = float(vals[0])
                break
        if value is None:
            continue
        weighted += float(site.weight) * value
        used += 1
    if used == 0:
        return float("nan"), 0, n_sites, "insufficient_markers"
    score = float(panel.intercept) + weighted
    if panel.score_kind == "clock":
        status = "ok" if used == n_sites else "partial"
    else:
        status = "ok" if used == n_sites else "partial"
    return score, used, n_sites, status


def run_confounder_scores(
    samples: Sequence[Tuple[str, str]],
    output_dir: Path,
    *,
    panels: Mapping[str, ScorePanel],
    contexts: Sequence[str],
    min_coverage: int,
    min_sites_fraction: float,
) -> Dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for sample_id, sample_dir in samples:
        row: Dict[str, Any] = {"sample_id": str(sample_id)}
        statuses: List[str] = []
        for score_name, panel in panels.items():
            score, used, n_sites, status = score_sample_panel(
                sample_dir,
                panel,
                contexts=contexts,
                min_coverage=min_coverage,
            )
            frac = (used / n_sites) if n_sites else 0.0
            if frac < float(min_sites_fraction):
                status = "insufficient_markers"
                score = float("nan")
            row[score_name] = score
            row[f"{score_name}_n_sites"] = int(used)
            row[f"{score_name}_status"] = status
            statuses.append(status)
        if all(s == "ok" for s in statuses):
            row["score_status"] = "ok"
        elif any(s == "insufficient_markers" for s in statuses):
            row["score_status"] = "insufficient_markers"
        else:
            row["score_status"] = "partial"
        rows.append(row)
    df = pd.DataFrame(rows)
    csv_path = output_dir / "confounder_scores.csv"
    df.to_csv(csv_path, index=False)
    manifest = {
        "output_csv": str(csv_path.resolve()),
        "n_samples": int(len(rows)),
        "n_columns": int(len(df.columns)),
        "panels": {k: p.panel_id for k, p in panels.items()},
        "contexts": list(contexts),
        "min_coverage": int(min_coverage),
        "min_sites_fraction": float(min_sites_fraction),
    }
    manifest_path = output_dir / "confounder_scores.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
