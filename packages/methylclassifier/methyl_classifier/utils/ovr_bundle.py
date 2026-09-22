"""
Build and load ``ecdf_one_vs_rest`` pickle bundles for MethylClassifier OvR mode.

Each binary entry is either a single-chrom detector package (``classifier`` + ``dmpDF``) or a
multi-chromosome dict (``chrom_classifiers`` + ``chromosome_weights`` + ``dmp_df``).
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..core.classifier import CustomUnpickler
from ..core.multiclass_ovr import ECDF_ONE_VS_REST_TYPE, OV_R_PACKAGE_VERSION


def binary_entry_from_detector_pickle(
    path: Union[str, Path],
    *,
    chromosome: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Load a detector-style PKL and return one OvR ``binary_models`` element:

    ``{"ecdf": ECDFClassifier, "dmp_df": DataFrame with chromosome, position}``

    If the package has top-level ``chromosome``, it is used when ``dmpDF`` lacks it.
    """
    path = Path(path)
    with open(path, "rb") as f:
        pkg = CustomUnpickler(f).load()
    if not isinstance(pkg, dict) or "classifier" not in pkg:
        raise ValueError(f"Not a detector model package: {path}")
    ecdf = pkg["classifier"]
    dmp = pkg.get("dmpDF")
    if dmp is None or not hasattr(dmp, "columns"):
        raise ValueError(f"Package missing dmpDF: {path}")
    dmp_df = dmp.copy()
    if "chromosome" not in dmp_df.columns:
        chrom = chromosome or pkg.get("chromosome")
        if chrom is None:
            raise ValueError(
                f"dmpDF has no chromosome column and none provided for {path}"
            )
        dmp_df = dmp_df.copy()
        dmp_df["chromosome"] = str(chrom)
    entry: Dict[str, Any] = {"ecdf": ecdf, "dmp_df": dmp_df}
    return entry


def binary_entry_from_sole_classifier_in_dir(
    directory: Union[str, Path],
) -> Dict[str, Any]:
    """
    Load the only ``classifier*.pkl`` in a directory (typical MethylDetector output).

    If zero or multiple matches exist, raises with a message to use explicit
    ``ovr_binary_model_paths`` or reduce to one pickle per class directory.
    """
    d = Path(directory)
    if not d.is_dir():
        raise ValueError(f"Not a directory: {d}")
    pkls = sorted(d.glob("classifier*.pkl"))
    if len(pkls) == 0:
        raise ValueError(f"No classifier*.pkl under {d}")
    if len(pkls) > 1:
        raise ValueError(
            f"{d} contains {len(pkls)} classifier*.pkl files; expected exactly one for "
            "this helper. (When building OvR from directories with multiple chromosomes, "
            "MethylClassifier loads all ``classifier*.pkl`` per class without using this helper.)"
        )
    return binary_entry_from_detector_pickle(pkls[0])


def build_ecdf_ovr_package(
    binary_entries: List[Dict[str, Any]],
    class_names: List[str],
    *,
    metadata_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble an in-memory package dict suitable for pickle.dump."""
    if len(binary_entries) != len(class_names):
        raise ValueError(
            f"binary_entries ({len(binary_entries)}) and class_names ({len(class_names)}) length mismatch"
        )
    if len(class_names) < 2:
        raise ValueError("OvR requires at least 2 classes")
    meta = {
        "n_classes": len(class_names),
        "class_names": list(class_names),
        "classifier_type": ECDF_ONE_VS_REST_TYPE,
        **(metadata_extra or {}),
    }
    return {
        "classifier_type": ECDF_ONE_VS_REST_TYPE,
        "package_version": OV_R_PACKAGE_VERSION,
        "class_names": list(class_names),
        "binary_models": list(binary_entries),
        "metadata": meta,
    }


def save_ecdf_ovr_pickle(
    binary_entries: List[Dict[str, Any]],
    class_names: List[str],
    output_path: Union[str, Path],
    *,
    metadata_extra: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write ``multiclass-classifier.pkl``-style OvR bundle."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pkg = build_ecdf_ovr_package(
        binary_entries, class_names, metadata_extra=metadata_extra
    )
    with open(output_path, "wb") as f:
        pickle.dump(pkg, f, protocol=pickle.HIGHEST_PROTOCOL)
    return output_path
