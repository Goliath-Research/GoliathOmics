"""ECDF blind-predict derived-measures assembly (lazy import from methyl_validation)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def extract_derived_measures_from_dmp_vector(
    dmp_feature_vector: np.ndarray,
    schema: Optional[Dict[str, Any]],
) -> Tuple[np.ndarray, List[str]]:
    """
    Compute effect_size-weighted derived features for one sample from its DMP beta vector.

    Returns (feature_vector, feature_names). Empty when schema is missing/disabled.
    """
    if not schema:
        return np.zeros((0,), dtype=np.float64), []
    try:
        from methyl_validation.chromosome_features import compute_ecdf_derived_features_from_schema
    except ImportError as exc:
        raise RuntimeError(
            "derived_measures_schema present but methyl_validation is not installed"
        ) from exc
    return compute_ecdf_derived_features_from_schema(
        np.asarray(dmp_feature_vector, dtype=np.float64),
        schema,
    )


def schema_from_model_package(model_package: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return derived_measures_schema block from a saved ECDF model package."""
    schema = model_package.get("derived_measures_schema")
    return dict(schema) if isinstance(schema, dict) else None
