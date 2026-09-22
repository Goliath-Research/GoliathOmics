"""
Load sample CSVs, resolve paths with samples_base_path, and perform stratified train/val split.
"""

import csv
import random
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _resolve_entry(entry: str, base: Path) -> str:
    entry = entry.strip()
    if not entry:
        return ""
    if not _looks_like_absolute_path(entry):
        return str(base.resolve() / entry)
    return entry


def _looks_like_absolute_path(entry: str) -> bool:
    if not entry:
        return False
    e = entry.strip()
    return e.startswith("/") or (len(e) > 1 and e[1] == ":")


def load_and_resolve_sample_paths(csv_path: str | Path, base_path: str | Path) -> List[str]:
    """
    Load sample names/paths from a CSV (one column or header 'sample'/'path') and resolve
    with base_path (relative names -> base_path / name). Returns list of full paths.
    """
    base = Path(base_path).resolve()
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Sample list file not found: {path}")
    out: List[str] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        first_row = next(reader, None)
        if first_row is None:
            return out
        # Header row?
        if first_row and first_row[0].strip().lower() in (
            "sample",
            "path",
            "sample_path",
            "name",
            "id",
        ):
            for row in reader:
                if row and row[0].strip():
                    r = _resolve_entry(row[0], base)
                    if r:
                        out.append(r)
        else:
            if first_row and first_row[0].strip():
                r = _resolve_entry(first_row[0], base)
                if r:
                    out.append(r)
            for row in reader:
                if row and row[0].strip():
                    r = _resolve_entry(row[0], base)
                    if r:
                        out.append(r)
    return out


def _split_one_cohort(
    paths: List[str],
    train_fraction: float,
    rng: random.Random,
    label: str,
) -> Tuple[List[str], List[str]]:
    n = len(paths)
    if n == 0:
        raise ValueError(f"Need at least one sample in cohort {label!r}")
    shuffled = list(paths)
    rng.shuffle(shuffled)
    n_train = max(1, int(n * train_fraction))
    if n_train >= n and n > 1:
        n_train = n - 1
    train = shuffled[:n_train]
    val = shuffled[n_train:]
    if not train:
        raise ValueError(f"Stratified split produced empty train set for cohort {label!r}")
    if not val:
        raise ValueError(
            f"Stratified split produced empty validation set for cohort {label!r}: "
            "need enough samples per class for train and validation"
        )
    return train, val


def stratified_split_multiclass(
    cohorts: List[Tuple[str, List[str]]],
    train_fraction: float,
    seed: Optional[int] = None,
    *,
    warn_min_train: int = 2,
    warn_min_val: int = 1,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """
    Split each cohort independently with the same train_fraction (stratified across classes).

    ``cohorts`` is an ordered list of (label, paths). Labels must be unique.

    Returns:
        (train_by_label, val_by_label)
    """
    if len(cohorts) < 2:
        raise ValueError("stratified_split_multiclass requires at least two cohorts")
    labels = [c[0] for c in cohorts]
    if len(set(labels)) != len(labels):
        raise ValueError("Cohort labels must be unique")
    rng = random.Random(seed)
    train_out: Dict[str, List[str]] = {}
    val_out: Dict[str, List[str]] = {}
    sparse: List[str] = []
    for label, paths in cohorts:
        tr, va = _split_one_cohort(paths, train_fraction, rng, label)
        train_out[label] = tr
        val_out[label] = va
        if len(tr) < warn_min_train or len(va) < warn_min_val:
            sparse.append(f"{label}: train_n={len(tr)} val_n={len(va)}")
    if sparse:
        warnings.warn(
            "Monte Carlo: small train/val counts for some cohorts (every label still split; "
            "metrics may be high-variance): " + "; ".join(sparse),
            UserWarning,
            stacklevel=2,
        )
    return train_out, val_out


def stratified_split(
    control_paths: List[str],
    disease_paths: List[str],
    train_fraction: float,
    seed: int | None = None,
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """
    Binary split: same semantics as stratified_split_multiclass with two cohorts.

    Returns:
        (train_control, train_disease, val_control, val_disease)
    """
    train_m, val_m = stratified_split_multiclass(
        [("__control__", control_paths), ("__disease__", disease_paths)],
        train_fraction,
        seed=seed,
    )
    return (
        train_m["__control__"],
        train_m["__disease__"],
        val_m["__control__"],
        val_m["__disease__"],
    )
