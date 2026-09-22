"""
Shared covariate loading and preprocessing for model backends.

Contract:
- Join key is sample basename (or caller-provided sample id list) matched against
  ``covariate_id_column`` in sidecar.
- Numeric columns are imputed then optionally standardized.
- Ordinal columns are mapped to a single numeric code (one feature per column).
- Categorical (nominal) columns are one-hot encoded with frozen vocab, an
  ``__UNKNOWN__`` bucket, and **one dropped reference level** so the design is
  non-redundant (L levels → L−1 columns).
- Composition (simplex) groups are ALR-encoded (K parts → K−1 coordinates).
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _import_h5py_with_plugins():
    import hdf5plugin  # noqa: F401
    import h5py

    return h5py


def _load_single_covariate_table(path: Path, covariate_id_column: str) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"covariates_path not found: {p}")
    if p.suffix.lower() in (".csv", ".tsv"):
        sep = "\t" if p.suffix.lower() == ".tsv" else ","
        df = pd.read_csv(p, sep=sep)
    elif p.suffix.lower() in (".h5", ".hdf5"):
        h5py = _import_h5py_with_plugins()
        with h5py.File(p, "r") as f:
            if not all(k in f for k in ("sample_id", "values")):
                raise ValueError(f"Covariates HDF5 missing sample_id/values datasets: {p}")
            sids = np.asarray(f["sample_id"])
            if sids.dtype.kind == "S":
                sids = np.char.decode(sids, "utf-8")
            vals = np.asarray(f["values"], dtype=np.float32)
            cols = np.asarray(f["columns"]) if "columns" in f else np.array([f"cov_{i}" for i in range(vals.shape[1])])
            if cols.dtype.kind == "S":
                cols = np.char.decode(cols, "utf-8")
        df = pd.DataFrame(vals, columns=[str(c) for c in cols])
        df[covariate_id_column] = [str(x) for x in sids.tolist()]
    else:
        raise ValueError(f"Unsupported covariates sidecar format: {p.suffix}")
    if covariate_id_column not in df.columns:
        raise ValueError(f"Covariates table missing id column '{covariate_id_column}'")
    df = df.copy()
    df[covariate_id_column] = df[covariate_id_column].astype(str)
    return df


def _load_covariate_table(
    covariates_path: Union[str, Sequence[str]],
    covariate_id_column: str,
) -> pd.DataFrame:
    if isinstance(covariates_path, (list, tuple)):
        paths = [Path(str(p)) for p in covariates_path if str(p).strip()]
        if not paths:
            raise ValueError("covariates_path list is empty")
        existing = [p for p in paths if p.is_file()]
        missing = [p for p in paths if not p.is_file()]
        if missing:
            logger.warning(
                "Skipping missing covariates_path entries: %s",
                ", ".join(str(p) for p in missing),
            )
        if not existing:
            raise FileNotFoundError(
                "covariates_path list has no existing files: "
                + ", ".join(str(p) for p in paths)
            )
        merged = _load_single_covariate_table(existing[0], covariate_id_column)
        for extra in existing[1:]:
            other = _load_single_covariate_table(extra, covariate_id_column)
            overlap = [
                c
                for c in other.columns
                if c != covariate_id_column and c in merged.columns
            ]
            if overlap:
                other = other.rename(columns={c: f"{c}__dup" for c in overlap})
            merged = merged.merge(other, on=covariate_id_column, how="outer")
        return merged
    return _load_single_covariate_table(Path(str(covariates_path)), covariate_id_column)


# Never auto-include as covariates (label leakage / non-feature metadata).
# Operators may still opt in by listing these in covariate_*_columns explicitly.
_AUTO_EXCLUDE_COVARIATE_COLUMNS = frozenset(
    {
        "group",
        "label",
        "y",
        "class",
        "expected_class",
        "phenotype",
        "disease",
        "disease_status",
        "qp_status",
        "n_markers_observed",
        "marker_fraction",
    }
)


def _filter_auto_candidate_columns(candidate_cols: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Drop known label/diagnostic columns from auto-inference candidates."""
    kept: List[str] = []
    excluded: List[str] = []
    for col in candidate_cols:
        if str(col) in _AUTO_EXCLUDE_COVARIATE_COLUMNS:
            excluded.append(str(col))
        else:
            kept.append(str(col))
    return kept, excluded


def _infer_column_roles(df: pd.DataFrame, candidate_cols: List[str]) -> Tuple[List[str], List[str]]:
    numeric: List[str] = []
    categorical: List[str] = []
    for col in candidate_cols:
        series = df[col]
        # treat as numeric if we can parse any non-null values
        parsed = pd.to_numeric(series, errors="coerce")
        non_null_original = int(series.notna().sum())
        non_null_numeric = int(parsed.notna().sum())
        if non_null_original > 0 and non_null_numeric > 0:
            numeric.append(col)
        else:
            categorical.append(col)
    return numeric, categorical


_KNOWN_ORDINAL_MAPS: Dict[Tuple[str, ...], Dict[str, float]] = {
    ("low", "medium", "high"): {"low": 1.0, "medium": 2.0, "high": 3.0},
    ("very_low", "low", "medium", "high", "very_high"): {
        "very_low": 1.0,
        "low": 2.0,
        "medium": 3.0,
        "high": 4.0,
        "very_high": 5.0,
    },
}


def _auto_ordinal_map_for_series(values: Sequence[str]) -> Optional[Dict[str, float]]:
    observed = sorted({str(v).strip().lower() for v in values if str(v).strip()})
    for key, mapping in _KNOWN_ORDINAL_MAPS.items():
        if set(observed).issubset(set(key)):
            return {k: float(v) for k, v in mapping.items()}
    return None


@dataclass
class CovariatePreprocessor:
    id_column: str
    numeric_columns: List[str]
    ordinal_columns: List[str]
    categorical_columns: List[str]
    ordinal_maps: Dict[str, Dict[str, float]]
    ordinal_unknown_value: float
    numeric_fill_values: Dict[str, float]
    numeric_means: Dict[str, float]
    numeric_stds: Dict[str, float]
    categorical_levels: Dict[str, List[str]]
    output_columns: List[str]
    standardize_numeric: bool
    missing_numeric_strategy: str
    unknown_category_token: str = "__UNKNOWN__"
    missing_category_token: str = "__MISSING__"
    categorical_drop_levels: Dict[str, str] = field(default_factory=dict)
    composition_groups: List[Dict[str, Any]] = field(default_factory=list)
    composition_no_standardize_columns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id_column": self.id_column,
            "numeric_columns": list(self.numeric_columns),
            "ordinal_columns": list(self.ordinal_columns),
            "categorical_columns": list(self.categorical_columns),
            "ordinal_maps": {
                str(k): {str(kk): float(vv) for kk, vv in vm.items()}
                for k, vm in self.ordinal_maps.items()
            },
            "ordinal_unknown_value": float(self.ordinal_unknown_value),
            "numeric_fill_values": {k: float(v) for k, v in self.numeric_fill_values.items()},
            "numeric_means": {k: float(v) for k, v in self.numeric_means.items()},
            "numeric_stds": {k: float(v) for k, v in self.numeric_stds.items()},
            "categorical_levels": {k: list(v) for k, v in self.categorical_levels.items()},
            "categorical_drop_levels": {
                str(k): str(v) for k, v in self.categorical_drop_levels.items()
            },
            "output_columns": list(self.output_columns),
            "standardize_numeric": bool(self.standardize_numeric),
            "missing_numeric_strategy": str(self.missing_numeric_strategy),
            "unknown_category_token": str(self.unknown_category_token),
            "missing_category_token": str(self.missing_category_token),
            "composition_groups": [dict(g) for g in self.composition_groups],
            "composition_no_standardize_columns": list(
                self.composition_no_standardize_columns
            ),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CovariatePreprocessor":
        return cls(
            id_column=str(payload["id_column"]),
            numeric_columns=[str(x) for x in payload.get("numeric_columns", [])],
            ordinal_columns=[str(x) for x in payload.get("ordinal_columns", [])],
            categorical_columns=[str(x) for x in payload.get("categorical_columns", [])],
            ordinal_maps={
                str(k): {str(kk): float(vv) for kk, vv in vm.items()}
                for k, vm in (payload.get("ordinal_maps") or {}).items()
            },
            ordinal_unknown_value=float(payload.get("ordinal_unknown_value", 0.0)),
            numeric_fill_values={str(k): float(v) for k, v in (payload.get("numeric_fill_values") or {}).items()},
            numeric_means={str(k): float(v) for k, v in (payload.get("numeric_means") or {}).items()},
            numeric_stds={str(k): float(v) for k, v in (payload.get("numeric_stds") or {}).items()},
            categorical_levels={str(k): [str(x) for x in v] for k, v in (payload.get("categorical_levels") or {}).items()},
            categorical_drop_levels={
                str(k): str(v)
                for k, v in (payload.get("categorical_drop_levels") or {}).items()
            },
            output_columns=[str(x) for x in payload.get("output_columns", [])],
            standardize_numeric=bool(payload.get("standardize_numeric", True)),
            missing_numeric_strategy=str(payload.get("missing_numeric_strategy", "mean")),
            unknown_category_token=str(payload.get("unknown_category_token", "__UNKNOWN__")),
            missing_category_token=str(payload.get("missing_category_token", "__MISSING__")),
            composition_groups=_composition_groups_from_payload(payload),
            composition_no_standardize_columns=[
                str(x) for x in payload.get("composition_no_standardize_columns", [])
            ],
        )

    def save_json(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_json(cls, path: str | Path) -> "CovariatePreprocessor":
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        return cls.from_dict(payload)


MISSING_SAMPLE_POLICIES = ("fail", "drop")


def resolve_missing_sample_policy(
    missing_samples: Optional[str],
    *,
    strict_join: bool,
) -> str:
    """Resolve row policy: ``fail``, ``drop``, or ``impute`` (legacy non-strict)."""
    raw = None if missing_samples is None else str(missing_samples).strip().lower()
    if raw in {"", "none"}:
        raw = None
    if raw is not None and raw not in MISSING_SAMPLE_POLICIES:
        raise ValueError(
            "covariates_missing_samples must be 'fail' or 'drop' "
            f"(got {missing_samples!r})"
        )
    if raw == "drop":
        return "drop"
    if raw == "fail" or (bool(strict_join) and raw is None):
        return "fail"
    return "impute"


def _warn_dropped_covariate_samples(dropped: Sequence[str]) -> None:
    preview = ", ".join(str(x) for x in list(dropped)[:10])
    msg = (
        f"Dropping {len(dropped)} sample(s) with missing covariate rows "
        f"(first: {preview}). Covariate-using stage continues on the remaining samples."
    )
    logger.warning(msg)
    print(msg, file=sys.stderr)


def resolve_covariate_sample_ids(
    sample_ids: Sequence[str],
    covariates_path: Optional[Union[str, Sequence[str]]],
    covariate_id_column: str,
    *,
    missing_samples: Optional[str] = None,
    strict_join: bool = False,
) -> Tuple[List[str], List[str]]:
    """Return ``(kept_ids, dropped_ids)`` in the original sample order.

    ``drop`` excludes IDs absent from the sidecar and warns. ``fail`` (or
    ``strict_join`` when the policy is unset) raises. Otherwise all IDs are
    kept so ``fit_covariates`` can reindex and impute cells.
    """
    ordered = [str(sid) for sid in sample_ids]
    if not covariates_path:
        return ordered, []
    policy = resolve_missing_sample_policy(missing_samples, strict_join=strict_join)
    raw = _load_covariate_table(covariates_path, covariate_id_column)
    present = set(raw[covariate_id_column].astype(str).tolist())
    dropped = [sid for sid in ordered if sid not in present]
    if not dropped:
        return ordered, []
    if policy == "fail":
        preview = ", ".join(dropped[:5])
        raise ValueError(
            f"Missing covariate rows for {len(dropped)} sample ids (first: {preview})"
        )
    if policy == "drop":
        kept = [sid for sid in ordered if sid in present]
        if not kept:
            raise ValueError(
                "covariates_missing_samples='drop' excluded every sample; "
                "no covariate rows remain."
            )
        _warn_dropped_covariate_samples(dropped)
        return kept, dropped
    return ordered, []


def exclusion_report(dropped_ids: Sequence[str]) -> Dict[str, Any]:
    dropped = [str(x) for x in dropped_ids]
    return {
        "dropped_sample_ids": dropped,
        "n_dropped": int(len(dropped)),
    }


def _ordered_covariate_rows(
    df: pd.DataFrame,
    sample_ids: Sequence[str],
    id_column: str,
    *,
    strict_join: bool,
) -> Tuple[pd.DataFrame, List[str]]:
    lookup = df.set_index(id_column)
    missing = [sid for sid in sample_ids if sid not in lookup.index]
    if strict_join and missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"Missing covariate rows for {len(missing)} sample ids (first: {preview})")
    aligned = lookup.reindex([str(s) for s in sample_ids]).copy()
    return aligned, missing


def _close_simplex(
    frame: pd.DataFrame,
    columns: Sequence[str],
    reference: str,
) -> Tuple[np.ndarray, List[str], int]:
    """Validate and close a composition to the unit simplex."""
    ordered = [str(column) for column in columns]
    if reference not in ordered:
        raise ValueError("Composition reference must be included in composition columns.")
    missing = sorted(set(ordered) - set(frame.columns))
    if missing:
        raise ValueError(f"Composition columns are missing: {missing}")
    values = frame[ordered].apply(pd.to_numeric, errors="coerce").to_numpy(
        dtype=np.float64
    )
    if not np.isfinite(values).all():
        raise ValueError("Composition contains missing or non-finite values.")
    if np.any(values < 0.0):
        raise ValueError("Composition contains negative values.")
    row_sums = np.sum(values, axis=1)
    if np.any(row_sums <= 0.0):
        raise ValueError("Composition contains a row with zero total mass.")
    closed = values / row_sums[:, None]
    ref_index = ordered.index(reference)
    return closed, ordered, ref_index


def _alr_transform(
    frame: pd.DataFrame,
    columns: Sequence[str],
    reference: str,
    pseudocount: float,
) -> Tuple[pd.DataFrame, List[str]]:
    closed, ordered, ref_index = _close_simplex(frame, columns, reference)
    output_names = [
        f"alr_{column}_vs_{reference}" for column in ordered if column != reference
    ]
    transformed = np.column_stack(
        [
            np.log(
                (closed[:, index] + float(pseudocount))
                / (closed[:, ref_index] + float(pseudocount))
            )
            for index, column in enumerate(ordered)
            if column != reference
        ]
    )
    return pd.DataFrame(transformed, index=frame.index, columns=output_names), output_names


def _binary_probability_name(non_reference: str) -> str:
    """Canonical export/stack name for a 2-part simplex non-reference part."""
    return f"p_{non_reference}"


def _simplex_encode(
    frame: pd.DataFrame,
    columns: Sequence[str],
    reference: str,
    pseudocount: Optional[float],
) -> Tuple[pd.DataFrame, List[str]]:
    """Encode a simplex: raw closed non-reference ``p`` when K=2, else ALR (K>2).

    Binary path does not use a log-ratio and does not require ``pseudocount``.
    """
    closed, ordered, ref_index = _close_simplex(frame, columns, reference)
    if len(ordered) == 2:
        non_ref = ordered[1 - ref_index]
        name = _binary_probability_name(non_ref)
        values = closed[:, ordered.index(non_ref)].astype(np.float32)
        return pd.DataFrame({name: values}, index=frame.index), [name]
    if pseudocount is None:
        raise ValueError(
            "ALR composition (K>2) requires pseudocount > 0 "
            "(set per group in profile/site actionConfig; no code default)."
        )
    pc = float(pseudocount)
    if pc <= 0.0:
        raise ValueError("ALR composition pseudocount must be > 0.")
    return _alr_transform(frame, ordered, reference, pc)


@dataclass(frozen=True)
class CompositionGroupSpec:
    """One simplex (sum-to-1) feature set.

    ``columns`` are the parts (K >= 2); ``reference`` is the dropped/denominator
    part (defaults to the last column). Encoding:

    - K=2: closed non-reference probability ``p_<nonref>`` (no ALR, no ε)
    - K>2: ALR coordinates ``alr_<part>_vs_<reference>`` (ε required)

    ``standardize`` controls whether those coordinates are z-scored downstream.
    """

    name: str
    columns: Tuple[str, ...]
    reference: str
    pseudocount: Optional[float]
    standardize: bool

    def encoded_names(self) -> List[str]:
        if len(self.columns) == 2:
            non_ref = next(c for c in self.columns if c != self.reference)
            return [_binary_probability_name(non_ref)]
        return [f"alr_{c}_vs_{self.reference}" for c in self.columns if c != self.reference]

    def alr_names(self) -> List[str]:
        """Backward-compatible alias for :meth:`encoded_names`."""
        return self.encoded_names()


def normalize_composition_groups(
    groups: Optional[Sequence[Dict[str, Any]]] = None,
    *,
    legacy_transform: Optional[str] = None,
    legacy_columns: Optional[Sequence[str]] = None,
    legacy_reference: Optional[str] = None,
    legacy_pseudocount: Optional[float] = None,
) -> List[CompositionGroupSpec]:
    """Resolve typed composition groups (plus the legacy single-group keys) to specs.

    The legacy ``covariate_composition_transform``/``_columns``/``_reference``/
    ``_pseudocount`` set is mapped to one group named ``default`` for one release.
    """
    specs: List[CompositionGroupSpec] = []
    seen_names: set[str] = set()

    def _add(
        name: str,
        columns: Optional[Sequence[str]],
        reference: Optional[str],
        pseudocount: Optional[float],
        standardize: Optional[bool],
    ) -> None:
        cols = [str(c) for c in (columns or [])]
        if len(cols) < 2:
            raise ValueError(
                f"composition group '{name}' requires at least 2 columns (a simplex)."
            )
        if len(set(cols)) != len(cols):
            raise ValueError(f"composition group '{name}' has duplicate columns: {cols}")
        ref = str(reference).strip() if reference else cols[-1]
        if ref not in cols:
            raise ValueError(
                f"composition group '{name}' reference '{ref}' is not one of its columns."
            )
        pc: Optional[float]
        if len(cols) == 2:
            # Binary simplex uses closed non-reference p; ε unused.
            if pseudocount is None:
                pc = None
            else:
                pc = float(pseudocount)
                if pc <= 0.0:
                    raise ValueError(f"composition group '{name}' pseudocount must be > 0.")
        else:
            if pseudocount is None:
                raise ValueError(
                    f"composition group '{name}' requires pseudocount > 0 for ALR (K>2) "
                    "(set per group in profile/site actionConfig; no code default)."
                )
            pc = float(pseudocount)
            if pc <= 0.0:
                raise ValueError(f"composition group '{name}' pseudocount must be > 0.")
        std = True if standardize is None else bool(standardize)
        if name in seen_names:
            raise ValueError(f"duplicate composition group name '{name}'.")
        seen_names.add(name)
        specs.append(
            CompositionGroupSpec(
                name=name,
                columns=tuple(cols),
                reference=ref,
                pseudocount=pc,
                standardize=std,
            )
        )

    for index, group in enumerate(groups or []):
        raw_name = str(group.get("name") or "").strip() or f"group{index}"
        _add(
            raw_name,
            group.get("columns"),
            group.get("reference"),
            group.get("pseudocount"),
            group.get("standardize"),
        )

    if legacy_transform is not None:
        if str(legacy_transform).strip().lower() != "alr":
            raise ValueError("covariate composition transform must be 'alr'")
        _add("default", legacy_columns, legacy_reference, legacy_pseudocount, True)

    all_cols: List[str] = [c for spec in specs for c in spec.columns]
    shared = sorted({c for c in all_cols if all_cols.count(c) > 1})
    if shared:
        raise ValueError(f"composition groups must not share columns: {shared}")
    return specs


def _composition_groups_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Read composition groups from a serialized preprocessor, honoring legacy fields."""
    groups = payload.get("composition_groups")
    if groups:
        return [dict(g) for g in groups]
    if payload.get("composition_transform"):
        return [
            {
                "name": "default",
                "columns": [str(x) for x in payload.get("composition_columns", [])],
                "reference": payload.get("composition_reference"),
                "pseudocount": payload.get("composition_pseudocount"),
                "standardize": True,
            }
        ]
    return []


def _choose_categorical_drop_level(
    levels: Sequence[str],
    *,
    unknown_token: str,
) -> Optional[str]:
    """Pick the reference level to drop for a non-redundant one-hot.

    Prefer the first sorted observed level that is not ``unknown_token`` so the
    unknown bucket remains an explicit column. Returns ``None`` when there is
    only one level (nothing to emit after a drop would leave zero columns —
    in that case the sole level is kept).
    """
    ordered = [str(x) for x in levels]
    if len(ordered) <= 1:
        return None
    for level in ordered:
        if level != unknown_token:
            return level
    return ordered[0]


def resolve_composition_groups_from_config(config: Any) -> List[CompositionGroupSpec]:
    """Resolve typed + legacy composition groups from a MonteCarlo/backend config object."""
    if config is None:
        return []
    groups = getattr(config, "covariate_composition_groups", None)
    group_dicts: Optional[List[Dict[str, Any]]] = None
    if groups:
        group_dicts = []
        for group in groups:
            if hasattr(group, "model_dump"):
                group_dicts.append(group.model_dump())
            elif isinstance(group, dict):
                group_dicts.append(dict(group))
            else:
                raise TypeError(f"Unsupported composition group entry: {type(group)!r}")
    return normalize_composition_groups(
        group_dicts,
        legacy_transform=getattr(config, "covariate_composition_transform", None),
        legacy_columns=getattr(config, "covariate_composition_columns", None),
        legacy_reference=getattr(config, "covariate_composition_reference", None),
        legacy_pseudocount=getattr(config, "covariate_composition_pseudocount", None),
    )


def fit_covariates(
    covariates_path: Optional[Union[str, Sequence[str]]],
    sample_ids: Sequence[str],
    *,
    covariate_id_column: str,
    strict_join: bool,
    numeric_columns: Optional[Sequence[str]] = None,
    ordinal_columns: Optional[Sequence[str]] = None,
    ordinal_maps: Optional[Dict[str, Dict[str, float]]] = None,
    ordinal_unknown_value: float = 0.0,
    categorical_columns: Optional[Sequence[str]] = None,
    missing_numeric_strategy: str = "mean",
    standardize_numeric: bool = True,
    composition_groups: Optional[Sequence[CompositionGroupSpec]] = None,
) -> Tuple[Optional[np.ndarray], Optional[CovariatePreprocessor], Dict[str, Any]]:
    if not covariates_path:
        return None, None, {"used": False}

    raw = _load_covariate_table(covariates_path, covariate_id_column)
    aligned, missing = _ordered_covariate_rows(
        raw,
        sample_ids,
        covariate_id_column,
        strict_join=strict_join,
    )
    candidate_cols = [c for c in aligned.columns.tolist() if c != covariate_id_column]
    if not candidate_cols:
        raise ValueError("Covariates table has no feature columns")

    composition_specs = list(composition_groups or [])
    composition_all_cols = [c for spec in composition_specs for c in spec.columns]
    missing_comp = sorted(set(composition_all_cols) - set(candidate_cols))
    if missing_comp:
        raise ValueError(f"Composition group columns not found in covariates: {missing_comp}")
    # Composition parts are handled only via ALR; they must not be claimed as plain roles.
    candidate_role_cols = [c for c in candidate_cols if c not in set(composition_all_cols)]

    auto_excluded: List[str] = []
    if numeric_columns is not None:
        numeric = [str(c) for c in numeric_columns]
        unknown = sorted(set(numeric) - set(candidate_cols))
        if unknown:
            raise ValueError(f"Configured numeric covariate columns not found: {unknown}")
        clash = sorted(set(numeric) & set(composition_all_cols))
        if clash:
            raise ValueError(
                f"covariate_numeric_columns overlap composition group columns: {clash}"
            )
    else:
        numeric = []
    if ordinal_columns is not None:
        ordinal = [str(c) for c in ordinal_columns]
        unknown = sorted(set(ordinal) - set(candidate_cols))
        if unknown:
            raise ValueError(f"Configured ordinal covariate columns not found: {unknown}")
        clash = sorted(set(ordinal) & set(composition_all_cols))
        if clash:
            raise ValueError(
                f"covariate_ordinal_columns overlap composition group columns: {clash}"
            )
    else:
        ordinal = []
    if categorical_columns is not None:
        categorical = [str(c) for c in categorical_columns]
        unknown = sorted(set(categorical) - set(candidate_cols))
        if unknown:
            raise ValueError(f"Configured categorical covariate columns not found: {unknown}")
        clash = sorted(set(categorical) & set(composition_all_cols))
        if clash:
            raise ValueError(
                f"covariate_categorical_columns overlap composition group columns: {clash}"
            )
    else:
        categorical = []

    # Default inference only touches numeric/categorical (composition parts excluded).
    # Ordinal must be explicit or auto-discovered via known label sets.
    if numeric_columns is None and categorical_columns is None and ordinal_columns is None:
        infer_cols, auto_excluded = _filter_auto_candidate_columns(candidate_role_cols)
        if not infer_cols and not composition_specs:
            raise ValueError(
                "No usable covariate columns after excluding label/diagnostic metadata "
                f"({auto_excluded}). Set covariate_numeric_columns explicitly if needed."
            )
        numeric, categorical = _infer_column_roles(aligned, infer_cols)
        auto_ord: List[str] = []
        for col in list(categorical):
            series_vals = aligned[col].astype(object)
            series_vals = series_vals.where(pd.notna(series_vals), "").astype(str).tolist()
            inferred = _auto_ordinal_map_for_series(series_vals)
            if inferred is not None:
                auto_ord.append(col)
        if auto_ord:
            categorical = [c for c in categorical if c not in auto_ord]
            ordinal = auto_ord
            if ordinal_maps is None:
                ordinal_maps = {}
            for col in auto_ord:
                series_vals = aligned[col].astype(object)
                series_vals = series_vals.where(pd.notna(series_vals), "").astype(str).tolist()
                inferred = _auto_ordinal_map_for_series(series_vals)
                if inferred is not None:
                    ordinal_maps[col] = inferred
    elif ordinal_columns is None:
        ordinal = []
    elif numeric_columns is None:
        numeric = [c for c in candidate_role_cols if c not in categorical and c not in ordinal]
    elif categorical_columns is None:
        categorical = [c for c in candidate_role_cols if c not in numeric and c not in ordinal]

    composition_no_standardize: List[str] = []
    composition_output_names: List[str] = []
    for spec in composition_specs:
        encoded_frame, encoded_names = _simplex_encode(
            aligned,
            list(spec.columns),
            spec.reference,
            spec.pseudocount,
        )
        aligned = aligned.drop(columns=list(spec.columns)).join(encoded_frame)
        numeric = [column for column in numeric if column not in set(spec.columns)]
        numeric.extend(encoded_names)
        composition_output_names.extend(encoded_names)
        if not spec.standardize:
            composition_no_standardize.extend(encoded_names)

    no_standardize_set = set(composition_no_standardize)

    overlap = (set(numeric) & set(ordinal)) | (set(numeric) & set(categorical)) | (set(ordinal) & set(categorical))
    if overlap:
        raise ValueError(f"Covariate columns must have exactly one role, overlap found: {sorted(overlap)}")

    if not numeric and not ordinal and not categorical:
        raise ValueError("No usable covariate columns after role assignment")
    ord_maps_in = {
        str(k): {str(kk).strip().lower(): float(vv) for kk, vv in vm.items()}
        for k, vm in (ordinal_maps or {}).items()
    }
    ord_maps: Dict[str, Dict[str, float]] = {}
    unknown_ordinal_count = 0

    missing_numeric_strategy = str(missing_numeric_strategy).strip().lower()
    if missing_numeric_strategy not in {"mean", "median", "zero"}:
        raise ValueError("missing_numeric_strategy must be one of: mean, median, zero")

    out_parts: List[np.ndarray] = []
    output_columns: List[str] = []
    fill_vals: Dict[str, float] = {}
    means: Dict[str, float] = {}
    stds: Dict[str, float] = {}
    cat_levels: Dict[str, List[str]] = {}
    cat_drop_levels: Dict[str, str] = {}
    unknown_token = "__UNKNOWN__"
    missing_token = "__MISSING__"

    for col in numeric:
        vals = pd.to_numeric(aligned[col], errors="coerce").astype(float)
        if missing_numeric_strategy == "median":
            fill = float(np.nanmedian(vals.to_numpy(dtype=np.float64))) if vals.notna().any() else 0.0
        elif missing_numeric_strategy == "zero":
            fill = 0.0
        else:
            fill = float(np.nanmean(vals.to_numpy(dtype=np.float64))) if vals.notna().any() else 0.0
        x = vals.fillna(fill).to_numpy(dtype=np.float32)
        mu = float(np.mean(x, dtype=np.float64))
        sd = float(np.std(x, dtype=np.float64))
        if not np.isfinite(sd) or sd <= 0.0:
            sd = 1.0
        if standardize_numeric and col not in no_standardize_set:
            x = ((x - mu) / sd).astype(np.float32)
        out_parts.append(x.reshape(-1, 1))
        output_columns.append(col)
        fill_vals[col] = float(fill)
        means[col] = mu
        stds[col] = sd

    for col in ordinal:
        vals_obj = aligned[col].astype(object) if col in aligned.columns else pd.Series([""] * len(sample_ids), index=aligned.index, dtype=object)
        vals_str = vals_obj.where(pd.notna(vals_obj), "").astype(str)
        mapping = ord_maps_in.get(col)
        if mapping is None:
            inferred = _auto_ordinal_map_for_series(vals_str.tolist())
            if inferred is None:
                raise ValueError(
                    f"Ordinal column '{col}' requires covariate_ordinal_maps[{col!r}] "
                    "or values matching a known order (e.g. low/medium/high)."
                )
            mapping = inferred
        ord_maps[col] = mapping
        coded = np.full((len(sample_ids),), float(ordinal_unknown_value), dtype=np.float32)
        for i, raw in enumerate(vals_str.tolist()):
            key = str(raw).strip().lower()
            if key in mapping:
                coded[i] = float(mapping[key])
            else:
                unknown_ordinal_count += 1
        if standardize_numeric:
            mu = float(np.mean(coded, dtype=np.float64))
            sd = float(np.std(coded, dtype=np.float64))
            if not np.isfinite(sd) or sd <= 0.0:
                sd = 1.0
            coded = ((coded - mu) / sd).astype(np.float32)
            means[col] = mu
            stds[col] = sd
        else:
            means[col] = float(np.mean(coded, dtype=np.float64))
            stds[col] = float(np.std(coded, dtype=np.float64) or 1.0)
        fill_vals[col] = float(ordinal_unknown_value)
        out_parts.append(coded.reshape(-1, 1))
        output_columns.append(col)

    for col in categorical:
        s = aligned[col].astype(object)
        s = s.where(pd.notna(s), missing_token).astype(str)
        levels = sorted(set(s.tolist()))
        if unknown_token not in levels:
            levels.append(unknown_token)
        cat_levels[col] = levels
        drop_level = _choose_categorical_drop_level(levels, unknown_token=unknown_token)
        if drop_level is not None:
            cat_drop_levels[col] = drop_level
        emit_levels = [lvl for lvl in levels if lvl != drop_level]
        for lvl in emit_levels:
            out_parts.append((s == lvl).to_numpy(dtype=np.float32).reshape(-1, 1))
            output_columns.append(f"{col}__{lvl}")

    matrix = np.concatenate(out_parts, axis=1).astype(np.float32) if out_parts else np.zeros((len(sample_ids), 0), dtype=np.float32)
    prep = CovariatePreprocessor(
        id_column=covariate_id_column,
        numeric_columns=numeric,
        ordinal_columns=ordinal,
        categorical_columns=categorical,
        ordinal_maps=ord_maps,
        ordinal_unknown_value=float(ordinal_unknown_value),
        numeric_fill_values=fill_vals,
        numeric_means=means,
        numeric_stds=stds,
        categorical_levels=cat_levels,
        categorical_drop_levels=cat_drop_levels,
        output_columns=output_columns,
        standardize_numeric=bool(standardize_numeric),
        missing_numeric_strategy=missing_numeric_strategy,
        unknown_category_token=unknown_token,
        missing_category_token=missing_token,
        composition_groups=[
            {
                "name": spec.name,
                "columns": list(spec.columns),
                "reference": spec.reference,
                "pseudocount": spec.pseudocount,
                "standardize": spec.standardize,
            }
            for spec in composition_specs
        ],
        composition_no_standardize_columns=list(composition_no_standardize),
    )
    report = {
        "used": True,
        "n_rows_requested": int(len(sample_ids)),
        "n_rows_missing": int(len(missing)),
        "missing_sample_ids_preview": [str(x) for x in missing[:10]],
        "n_numeric_columns": int(len(numeric)),
        "n_ordinal_columns": int(len(ordinal)),
        "n_categorical_columns": int(len(categorical)),
        "n_output_columns": int(matrix.shape[1]),
        "numeric_columns": list(numeric),
        "ordinal_columns": list(ordinal),
        "categorical_columns": list(categorical),
        "categorical_drop_levels": dict(cat_drop_levels),
        "auto_excluded_columns": list(auto_excluded),
        "unknown_ordinal_values_mapped": int(unknown_ordinal_count),
        "missing_numeric_strategy": missing_numeric_strategy,
        "standardize_numeric": bool(standardize_numeric),
        "composition_transform": "alr" if composition_specs else None,
        "composition_groups": [
            {
                "name": spec.name,
                "columns": list(spec.columns),
                "reference": spec.reference,
                "pseudocount": spec.pseudocount,
                "standardize": spec.standardize,
            }
            for spec in composition_specs
        ],
        "composition_output_columns": list(composition_output_names),
    }
    return matrix, prep, report


def transform_covariates(
    covariates_path: Optional[Union[str, Sequence[str]]],
    sample_ids: Sequence[str],
    preprocessor: Optional[CovariatePreprocessor],
    *,
    strict_join: bool,
) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    if preprocessor is None:
        return None, {"used": False}
    if not covariates_path:
        raise ValueError("Covariates were used for training but no covariates_path was provided for prediction.")

    raw = _load_covariate_table(covariates_path, preprocessor.id_column)
    aligned, missing = _ordered_covariate_rows(
        raw,
        sample_ids,
        preprocessor.id_column,
        strict_join=strict_join,
    )
    for group in preprocessor.composition_groups:
        columns = [str(c) for c in group.get("columns", [])]
        reference = group.get("reference")
        pseudocount = group.get("pseudocount")
        if not columns or not reference:
            raise ValueError("Frozen composition group metadata is incomplete.")
        if len(columns) > 2 and pseudocount is None:
            raise ValueError("Frozen ALR composition group (K>2) is missing pseudocount.")
        encoded_frame, _ = _simplex_encode(
            aligned,
            columns,
            str(reference),
            float(pseudocount) if pseudocount is not None else None,
        )
        aligned = aligned.drop(columns=columns).join(encoded_frame)

    no_standardize_set = set(preprocessor.composition_no_standardize_columns)
    out_parts: List[np.ndarray] = []

    for col in preprocessor.numeric_columns:
        if col not in aligned.columns:
            vals = np.full((len(sample_ids),), preprocessor.numeric_fill_values.get(col, 0.0), dtype=np.float32)
        else:
            vals = pd.to_numeric(aligned[col], errors="coerce").astype(float).fillna(preprocessor.numeric_fill_values.get(col, 0.0)).to_numpy(dtype=np.float32)
        if preprocessor.standardize_numeric and col not in no_standardize_set:
            mu = float(preprocessor.numeric_means.get(col, 0.0))
            sd = float(preprocessor.numeric_stds.get(col, 1.0))
            if not np.isfinite(sd) or sd <= 0.0:
                sd = 1.0
            vals = ((vals - mu) / sd).astype(np.float32)
        out_parts.append(vals.reshape(-1, 1))

    unknown_ordinal_count = 0
    for col in preprocessor.ordinal_columns:
        if col in aligned.columns:
            vals_obj = aligned[col].astype(object)
            vals_str = vals_obj.where(pd.notna(vals_obj), "").astype(str)
        else:
            vals_str = pd.Series([""] * len(sample_ids), index=aligned.index, dtype=str)
        mapping = preprocessor.ordinal_maps.get(col, {})
        coded = np.full((len(sample_ids),), float(preprocessor.ordinal_unknown_value), dtype=np.float32)
        for i, raw in enumerate(vals_str.tolist()):
            key = str(raw).strip().lower()
            if key in mapping:
                coded[i] = float(mapping[key])
            else:
                unknown_ordinal_count += 1
        if preprocessor.standardize_numeric:
            mu = float(preprocessor.numeric_means.get(col, 0.0))
            sd = float(preprocessor.numeric_stds.get(col, 1.0))
            if not np.isfinite(sd) or sd <= 0.0:
                sd = 1.0
            coded = ((coded - mu) / sd).astype(np.float32)
        out_parts.append(coded.reshape(-1, 1))

    unknown_count = 0
    for col in preprocessor.categorical_columns:
        if col in aligned.columns:
            s = aligned[col].astype(object)
            s = s.where(pd.notna(s), preprocessor.missing_category_token).astype(str)
        else:
            s = pd.Series([preprocessor.missing_category_token] * len(sample_ids), index=aligned.index, dtype=str)
        levels = preprocessor.categorical_levels.get(col, [preprocessor.unknown_category_token])
        drop_level = (preprocessor.categorical_drop_levels or {}).get(col)
        # Legacy preprocessors without drop metadata emit every stored level.
        emit_levels = [lvl for lvl in levels if lvl != drop_level] if drop_level else list(levels)
        allowed = set(levels)
        mapped = []
        for v in s.tolist():
            if v in allowed:
                mapped.append(v)
            else:
                mapped.append(preprocessor.unknown_category_token)
                unknown_count += 1
        ms = pd.Series(mapped, dtype=str)
        for lvl in emit_levels:
            out_parts.append((ms == lvl).to_numpy(dtype=np.float32).reshape(-1, 1))

    matrix = np.concatenate(out_parts, axis=1).astype(np.float32) if out_parts else np.zeros((len(sample_ids), 0), dtype=np.float32)
    if preprocessor.output_columns and matrix.shape[1] != len(preprocessor.output_columns):
        raise ValueError(
            "Covariate preprocessing output shape mismatch: "
            f"{matrix.shape[1]} != {len(preprocessor.output_columns)}"
        )
    report = {
        "used": True,
        "n_rows_requested": int(len(sample_ids)),
        "n_rows_missing": int(len(missing)),
        "missing_sample_ids_preview": [str(x) for x in missing[:10]],
        "unknown_categorical_values_mapped": int(unknown_count),
        "unknown_ordinal_values_mapped": int(unknown_ordinal_count),
        "n_output_columns": int(matrix.shape[1]),
    }
    return matrix, report
