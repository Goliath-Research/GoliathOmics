"""Run genome-wide derived measures for all project samples."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd

from ..config import DerivedMeasuresStepConfig
from .genome_measures import compute_cohort_median_coverages, compute_sample_genome_measures


def run_derived_measures_for_samples(
    samples: Sequence[Tuple[str, str]],
    output_dir: Path,
    cfg: DerivedMeasuresStepConfig,
    *,
    project_chromosomes: Sequence[str] | None = None,
) -> Dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    chromosomes = [str(c) for c in (cfg.chromosomes or project_chromosomes or ["1"])]
    contexts = [str(c) for c in (cfg.contexts or ["CG"])]
    min_cov = int(cfg.min_coverage) if cfg.min_coverage is not None else 1
    cohort_medians = compute_cohort_median_coverages(
        samples,
        chromosomes=chromosomes,
        contexts=contexts,
        min_coverage=min_cov,
    )
    rows: List[Dict[str, Any]] = []
    for sample_id, sample_dir in samples:
        rows.append(
            compute_sample_genome_measures(
                sample_id,
                sample_dir,
                chromosomes=chromosomes,
                contexts=contexts,
                cfg=cfg,
                cohort_median_coverages=cohort_medians,
            )
        )
    df = pd.DataFrame(rows)
    csv_path = output_dir / "derived_measures.csv"
    df.to_csv(csv_path, index=False)
    manifest = {
        "output_csv": str(csv_path.resolve()),
        "n_samples": int(len(rows)),
        "n_columns": int(len(df.columns)),
        "chromosomes": chromosomes,
        "contexts": contexts,
        "cohort_median_coverages": cohort_medians,
    }
    manifest_path = output_dir / "derived_measures.manifest.json"
    import json

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
