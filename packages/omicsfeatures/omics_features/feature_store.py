"""Per-sample feature contract shared across omics packs.

Canonical layout (one file per sample, under the sample directory):

    {sample_id}.<kind>.h5      # kind e.g. "expression", "abundance"
        /feature_id  (variable-length UTF-8 strings, sorted)
        /value       (float64; counts, TPM, intensity, NPX, ...)
        attrs: source, n_features, sample_id, [aux dataset names]

Adapters (RNA expression, proteomics abundance) build the ``{feature_id: value}`` mapping
from their tool outputs and call ``write_sample_features``; downstream matrix/DE code reads
this contract uniformly regardless of modality.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import h5py
import numpy as np


def feature_h5_path(sample_dir: str | Path, sample_id: str, *, kind: str) -> Path:
    return Path(sample_dir) / f"{sample_id}.{kind}.h5"


def write_sample_features(
    *,
    sample_dir: str | Path,
    sample_id: str,
    kind: str,
    values: Mapping[str, float],
    source: str,
    aux: Optional[Mapping[str, Mapping[str, float]]] = None,
) -> Dict[str, Any]:
    """Write a per-sample ``{sample_id}.{kind}.h5`` from a feature->value mapping.

    ``aux`` optionally stores extra per-feature vectors (e.g. tpm) aligned to the same
    sorted feature order.
    """
    sample_path = Path(sample_dir)
    if not sample_path.is_dir():
        raise RuntimeError(f"sampleDir not found: {sample_path}")

    features: List[str] = sorted(values.keys())
    value_arr = np.asarray([float(values[f]) for f in features], dtype=np.float64)

    out_path = feature_h5_path(sample_path, sample_id, kind=kind)
    with h5py.File(out_path, "w") as h5:
        dt = h5py.string_dtype(encoding="utf-8")
        h5.create_dataset("feature_id", data=np.asarray(features, dtype=object), dtype=dt)
        h5.create_dataset("value", data=value_arr)
        if aux:
            for name, mapping in aux.items():
                arr = np.asarray([float(mapping.get(f, np.nan)) for f in features], dtype=np.float64)
                h5.create_dataset(name, data=arr)
        h5.attrs["source"] = source
        h5.attrs["kind"] = kind
        h5.attrs["n_features"] = len(features)
        h5.attrs["sample_id"] = str(sample_id)

    return {
        "sampleId": str(sample_id),
        "featureH5": str(out_path),
        "n_features": len(features),
        "source": source,
    }


def _decode_str_dataset(raw: Any) -> np.ndarray:
    return np.asarray([f.decode() if isinstance(f, bytes) else str(f) for f in raw])


def read_feature_datasets(h5: h5py.File) -> Tuple[np.ndarray, np.ndarray]:
    """Read ``(feature_ids, values)`` from a sample feature HDF5.

    Canonical contract: ``feature_id`` + ``value``.
    Legacy RNA ``expression.h5`` (pre-omics_features): ``gene_id`` + ``count``.
    """
    if "feature_id" in h5 and "value" in h5:
        return _decode_str_dataset(h5["feature_id"][:]), np.asarray(h5["value"][:], dtype=np.float64)
    if "gene_id" in h5 and "count" in h5:
        # Backward-compat shim for files written before the shared feature_store contract.
        return _decode_str_dataset(h5["gene_id"][:]), np.asarray(h5["count"][:], dtype=np.float64)
    keys = sorted(h5.keys())
    raise KeyError(
        "feature HDF5 missing canonical datasets feature_id/value "
        f"(and no legacy gene_id/count); keys={keys}"
    )


def read_sample_features(sample_dir: str | Path, sample_id: str, *, kind: str) -> Tuple[np.ndarray, np.ndarray, str]:
    """Return (feature_ids, values, source) for a registered sample."""
    path = feature_h5_path(sample_dir, sample_id, kind=kind)
    if not path.is_file():
        raise RuntimeError(f"{kind}.h5 not found: {path}")
    with h5py.File(path, "r") as h5:
        feature_ids, values = read_feature_datasets(h5)
        source = str(h5.attrs.get("source", h5.attrs.get("quant_mode", "unknown")))
    return feature_ids, values, source


def find_feature_h5(sample_path: str | Path, *, kind: str) -> Optional[Path]:
    """Locate a ``*.{kind}.h5`` under a sample directory (any sample id)."""
    p = Path(sample_path)
    suffix = f".{kind}.h5"
    if p.is_file() and p.name.endswith(suffix):
        return p
    if p.is_dir():
        matches = sorted(p.glob(f"*{suffix}"))
        if matches:
            return matches[0]
    return None
