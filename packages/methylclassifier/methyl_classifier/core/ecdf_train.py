"""Train ECDF classifier pickle from selected DMP panel CSV (post dmp_select)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

logger = logging.getLogger(__name__)


def train_ecdf_pickle_from_dmps(
    dmps_df: pd.DataFrame,
    *,
    chromosome: str,
    contexts: List[str],
    centroid1_dir: Union[str, Path],
    centroid2_dir: Union[str, Path],
    output_dir: Union[str, Path],
    temperature: float = 1.0,
    enable_platt_calibration: bool = False,
    extra_config: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    Build and save classifier-{chrom}-{contexts}.pkl from a classifier DMP CSV.

    Delegates ECDF construction to MethylDetector (centroid bin_counts / weights).
    """
    from methyl_detector.core.methyldetector import MethylDetector
    from methyl_detector.models.config import MethylDetectorConfig

    ctx_list = [str(c) for c in contexts] if contexts else ["CG"]
    payload: Dict[str, Any] = {
        "chromosome": str(chromosome),
        "contexts": ctx_list,
        "centroid1_dir": str(centroid1_dir),
        "centroid2_dir": str(centroid2_dir),
        "output_dir": str(output_dir),
        "temperature": float(temperature),
        "enable_platt_calibration": bool(enable_platt_calibration),
        "export_classifier": True,
    }
    if extra_config:
        payload.update(extra_config)
    cfg = MethylDetectorConfig(**payload)
    detector = MethylDetector(cfg)
    detector.chromosome = str(chromosome)
    detector._save_unified_model(None, dmps_df)
    ctx_str = ",".join(sorted(ctx_list))
    model_path = Path(output_dir) / f"classifier-{chromosome}-{ctx_str}.pkl"
    if not model_path.is_file():
        raise FileNotFoundError(f"Expected classifier pickle at {model_path}")
    logger.info("Trained ECDF classifier: %s (%s DMPs)", model_path, len(dmps_df))
    return model_path


def train_ecdf_pickle_from_csv(
    classifier_csv: Union[str, Path],
    *,
    chromosome: str,
    contexts: List[str],
    centroid1_dir: Union[str, Path],
    centroid2_dir: Union[str, Path],
    output_dir: Union[str, Path],
    **kwargs: Any,
) -> Path:
    df = pd.read_csv(classifier_csv)
    if df.empty:
        raise ValueError(f"Classifier DMP CSV is empty: {classifier_csv}")
    return train_ecdf_pickle_from_dmps(
        df,
        chromosome=chromosome,
        contexts=contexts,
        centroid1_dir=centroid1_dir,
        centroid2_dir=centroid2_dir,
        output_dir=output_dir,
        **kwargs,
    )
