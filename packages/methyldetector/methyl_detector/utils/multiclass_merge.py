"""
Merge DMP CSVs from multiple per-cancer detection dirs for native multiclass building.
"""

from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel

from .dmp_export_paths import find_classifier_dmps_csvs

DMP_GLOB = "dmps-*.csv"
REQUIRED_COLS = {"chromosome", "context", "position"}
WEIGHT_COLS = ["weight", "effect_size"]


def _find_dmp_csvs(detection_dir: Path) -> List[Path]:
    """Return classifier/prediction DMP CSV paths (dual-branch aware)."""
    return find_classifier_dmps_csvs(detection_dir)


def _resolve_weight_column(df: pd.DataFrame, weights_column: Optional[str]) -> str:
    if weights_column and weights_column in df.columns:
        return str(weights_column)
    for col in WEIGHT_COLS:
        if col in df.columns:
            return col
    df["effect_size"] = 1.0
    return "effect_size"


def check_detection_dirs_have_dmps(
    configs_and_labels: List[Tuple[Any, str]],
    output_dir_attr: str = "output_dir",
) -> List[Tuple[Path, str]]:
    """
    Check that each (config, label) has at least one dmps-*.csv in config.output_dir.
    Returns list of (output_dir, label) that are missing DMP CSVs.
    """
    missing = []
    for config, label in configs_and_labels:
        if isinstance(config, (str, Path)):
            out_dir = Path(config)
        elif isinstance(config, BaseModel):
            cfg_map = config.model_dump(mode="python")
            out_val = cfg_map.get(output_dir_attr)
            if not out_val:
                raise ValueError(
                    f"Config {type(config).__name__} is missing required field {output_dir_attr!r}"
                )
            out_dir = Path(out_val)
        elif isinstance(config, dict):
            out_val = config.get(output_dir_attr)
            if not out_val:
                raise ValueError(
                    f"Config dict is missing required key {output_dir_attr!r}"
                )
            out_dir = Path(out_val)
        else:
            raise TypeError(
                f"Unsupported config type for detection dir resolution: {type(config)!r}"
            )
        csvs = _find_dmp_csvs(out_dir)
        if not csvs:
            missing.append((out_dir, label))
    return missing


def merge_dmp_csvs_from_detection_dirs(
    detection_dirs: List[Path],
    merged_path: Path,
    weights_column: Optional[str] = "effect_size",
    detection_labels: Optional[List[str]] = None,
) -> Path:
    """
    Merge per-comparison DMP CSVs into one multiclass feature table.

    When ``detection_labels`` are provided, the output preserves per-comparison
    provenance in wide columns (for example ``effect_size__pca1``) while also
    emitting aggregated site-level weights such as ``effect_size_sum`` and
    ``weight``. Without labels, the function keeps the legacy behavior of a
    simple de-duplicated union by maximum weight.
    """
    frames = []
    if detection_labels is not None and len(detection_labels) != len(detection_dirs):
        raise ValueError("detection_labels must match detection_dirs length")

    for idx, ddir in enumerate(detection_dirs):
        label = None if detection_labels is None else str(detection_labels[idx])
        for csv_path in _find_dmp_csvs(ddir):
            df = pd.read_csv(csv_path)
            if not REQUIRED_COLS.issubset(df.columns):
                continue
            if label is not None:
                df["comparison_label"] = label
            frames.append(df)

    if not frames:
        raise ValueError(
            f"No DMP CSVs found under the given detection dirs; expected {DMP_GLOB} in each."
        )

    combined = pd.concat(frames, ignore_index=True)
    combined["chromosome"] = combined["chromosome"].astype(str)
    combined["context"] = combined["context"].astype(str)
    combined["position"] = combined["position"].astype(np.uint32)
    weight_col = _resolve_weight_column(combined, weights_column)
    combined["_merge_weight"] = combined[weight_col].astype(float)

    if detection_labels is None or "comparison_label" not in combined.columns:
        combined = combined.sort_values("_merge_weight", ascending=False)
        merged = combined.drop_duplicates(
            subset=["chromosome", "context", "position"], keep="first"
        ).reset_index(drop=True)
        merged = merged.drop(columns=["_merge_weight"], errors="ignore")
        merged_path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(merged_path, index=False)
        return merged_path

    # Within each comparison, keep the strongest row per site.
    combined = combined.sort_values("_merge_weight", ascending=False)
    per_comparison = combined.drop_duplicates(
        subset=["chromosome", "context", "position", "comparison_label"],
        keep="first",
    ).reset_index(drop=True)

    key_cols = ["chromosome", "context", "position"]
    grouped = per_comparison.groupby(key_cols, sort=False, observed=True)
    base = grouped.agg(
        comparison_count=("comparison_label", "nunique"),
        comparison_labels=("comparison_label", lambda s: "|".join(sorted({str(x) for x in s}))),
        effect_size_sum=("_merge_weight", "sum"),
        effect_size_max=("_merge_weight", "max"),
        effect_size_mean=("_merge_weight", "mean"),
    )
    base["weight"] = base["effect_size_sum"].astype(np.float64)

    for value_col in ("effect_size", "delta_mean", "mean1", "mean2"):
        source_col = weight_col if value_col == "effect_size" else value_col
        if source_col not in per_comparison.columns:
            continue
        pivot = per_comparison.pivot_table(
            index=key_cols,
            columns="comparison_label",
            values=source_col,
            aggfunc="first",
            fill_value=0.0,
        )
        pivot = pivot.rename(
            columns={label: f"{value_col}__{label}" for label in pivot.columns}
        )
        base = base.join(pivot, how="left")

    merged = base.reset_index()
    merged_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(merged_path, index=False)
    return merged_path
