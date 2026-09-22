"""
Hierarchical panel readout for OvR ECDF bundles with multiple disease families.

Uses the same pairwise binary heads as standard OvR fusion, but aggregates disease logits
within named families (e.g. prostate stages vs colorectal) and emits interpretable labels:
healthy, primary_family, alternative_panel_disease, indeterminate.

Requires the pairwise max-contrast layout (first binary head = geometric aggregate control,
heads 1..K-1 = control vs one disease class each), matching ``fuse_ovr_binary_probas`` with
``pairwise_max_contrast_control=True``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .multiclass_ovr import _logits_from_binary_columns

PANEL_SPEC_VERSION = 1


def _disease_logits_from_pairwise_ovr(
    binary_probas: Sequence[np.ndarray],
    eps: float = 1e-12,
    *,
    pairwise_max_contrast: bool,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Return ``(logit_control, logit_disease_heads)`` with shapes ``(n,)`` and ``(n, D)``.
    ``D = len(binary_probas) - 1`` when ``pairwise_max_contrast``; else all heads are used
    and control is column 0 of the full logit matrix (legacy flat OvR).
    """
    if not binary_probas:
        raise ValueError("binary_probas must be non-empty")
    n = int(binary_probas[0].shape[0])
    K = len(binary_probas)

    if pairwise_max_contrast and K >= 2:
        d_logits = _logits_from_binary_columns(
            binary_probas, list(range(1, K)), n, eps
        )
        finite_d = np.isfinite(d_logits) & (d_logits > -np.inf)
        row_max_d = np.max(np.where(finite_d, d_logits, -np.inf), axis=1)
        has_any = np.any(finite_d, axis=1)
        ctrl = np.where(has_any, -row_max_d, -np.inf)
        return ctrl.astype(np.float64), d_logits

    full = _logits_from_binary_columns(binary_probas, list(range(K)), n, eps)
    return full[:, 0].copy(), full[:, 1:].copy()


def _validate_spec(
    class_names: Sequence[str],
    spec: Dict[str, Any],
) -> Tuple[str, Dict[str, List[str]], float]:
    if not class_names or len(class_names) < 2:
        raise ValueError("panel fusion requires at least two class_names (control + diseases)")
    primary = str(spec.get("primary_family") or "").strip()
    families = spec.get("families") or {}
    if not primary:
        raise ValueError("panel spec requires 'primary_family' string")
    if not isinstance(families, dict) or not families:
        raise ValueError("panel spec requires non-empty 'families' dict: family -> [class_name, ...]")
    if primary not in families:
        raise ValueError(f"primary_family {primary!r} must be a key in families")
    ctrl_name = str(class_names[0])
    disease_names = [str(x) for x in class_names[1:]]
    disease_set = set(disease_names)
    seen: set[str] = set()
    flat_fams: Dict[str, List[str]] = {}
    for fam, members in families.items():
        key = str(fam)
        if not isinstance(members, list) or not members:
            raise ValueError(f"families[{key!r}] must be a non-empty list of class names")
        ms = [str(m) for m in members]
        for m in ms:
            if m not in disease_set:
                raise ValueError(
                    f"Unknown class name {m!r} in family {key!r}; "
                    f"expected subset of disease class_names: {disease_names}"
                )
            if m == ctrl_name:
                raise ValueError("Do not include control class name inside families")
            if m in seen:
                raise ValueError(f"class {m!r} appears in more than one family")
            seen.add(m)
        flat_fams[key] = ms
    missing = disease_set - seen
    if missing:
        raise ValueError(
            "Every disease class_name must appear in exactly one family. Missing: "
            f"{sorted(missing)}"
        )
    tau_indet = float(spec.get("indeterminate_delta", 0.25))
    return primary, flat_fams, tau_indet


def compute_family_max_logits(
    logit_disease_heads: np.ndarray,
    class_names: Sequence[str],
    families: Dict[str, List[str]],
) -> Tuple[Dict[str, np.ndarray], np.ndarray]:
    """
    ``logit_disease_heads`` shape ``(n, D)`` aligned with ``class_names[1:][:D]``.
    Returns per-family max logit arrays of shape ``(n,)`` and stacked ``(n, n_families)``.
    """
    disease_names = [str(x) for x in class_names[1:]]
    D = logit_disease_heads.shape[1]
    if D != len(disease_names):
        raise ValueError(
            f"logit_disease_heads has {D} columns but class_names has "
            f"{len(disease_names)} disease labels"
        )
    name_to_col = {disease_names[i]: i for i in range(D)}
    fam_keys = list(families.keys())
    stacked = np.full((logit_disease_heads.shape[0], len(fam_keys)), -np.inf, dtype=np.float64)
    out_dict: Dict[str, np.ndarray] = {}
    for j, fam in enumerate(fam_keys):
        cols = [name_to_col[m] for m in families[fam]]
        block = logit_disease_heads[:, cols]
        finite = np.isfinite(block) & (block > -np.inf)
        row_max = np.max(np.where(finite, block, -np.inf), axis=1)
        out_dict[fam] = row_max
        stacked[:, j] = row_max
    return out_dict, stacked


def _class_to_family(
    disease_class_name: str,
    families: Dict[str, List[str]],
) -> str:
    for fam, members in families.items():
        if disease_class_name in members:
            return str(fam)
    raise KeyError(disease_class_name)


def panel_labels_from_combined_logits(
    logit_control: np.ndarray,
    logit_disease_heads: np.ndarray,
    class_names: Sequence[str],
    primary_family: str,
    families: Dict[str, List[str]],
    *,
    indeterminate_delta: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Use the same logit layout as PMC OvR softmax: column 0 = control, columns 1.. = disease heads.
    Argmax matches standard fused OvR when fusion is pairwise max-contrast.

    Integer codes: 0 healthy, 1 primary_disease, 2 alternative_panel_disease, 3 indeterminate.
    """
    n = int(logit_control.shape[0])
    L = np.concatenate([logit_control[:, np.newaxis], logit_disease_heads], axis=1)
    finite = np.isfinite(L) & (L > -np.inf)
    neg_inf = np.full_like(L, -np.inf, dtype=np.float64)
    L_eff = np.where(finite, L, neg_inf)
    order = np.argsort(-L_eff, axis=1, kind="mergesort")
    top0 = order[:, 0]
    best_v = np.take_along_axis(L_eff, order[:, :1], axis=1).ravel()
    second_v = (
        np.take_along_axis(L_eff, order[:, 1:2], axis=1).ravel()
        if L.shape[1] > 1
        else np.full(n, -np.inf, dtype=np.float64)
    )
    spread_ok = (best_v - second_v) >= indeterminate_delta
    disease_names = [str(x) for x in class_names[1:]]

    labels_str = np.empty(n, dtype=object)
    labels_int = np.zeros(n, dtype=np.int32)

    for i in range(n):
        if not np.any(finite[i]):
            labels_str[i] = "indeterminate"
            labels_int[i] = 3
            continue
        j = int(top0[i])
        if j == 0:
            labels_str[i] = "healthy"
            labels_int[i] = 0
            continue
        if not spread_ok[i]:
            labels_str[i] = "indeterminate"
            labels_int[i] = 3
            continue
        dname = disease_names[j - 1]
        fam = _class_to_family(dname, families)
        if fam == primary_family:
            labels_str[i] = "primary_disease"
            labels_int[i] = 1
        else:
            labels_str[i] = "alternative_panel_disease"
            labels_int[i] = 2

    return labels_int, labels_str.astype(str)


def compute_panel_outputs(
    binary_probas: Sequence[np.ndarray],
    class_names: Sequence[str],
    *,
    pairwise_max_contrast: bool,
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build panel arrays for all samples in one batch.

    Returns a dict with numpy arrays and strings suitable for JSON/csv augmentation.
    """
    if not pairwise_max_contrast:
        raise ValueError(
            "Panel readout requires pairwise max-contrast OvR (MethylDetector-style bundles: "
            "first head = geometric control aggregate, heads 1..K-1 = pairwise diseases). "
            "Set metadata ovr_fuse_mode to pairwise_max_contrast or use an aggregate-control export."
        )
    primary, families, tau_indet = _validate_spec(class_names, spec)
    ctrl, dheads = _disease_logits_from_pairwise_ovr(
        binary_probas, pairwise_max_contrast=pairwise_max_contrast
    )
    fam_max, stacked = compute_family_max_logits(dheads, class_names, families)
    fam_order = list(families.keys())
    lint, lstr = panel_labels_from_combined_logits(
        ctrl,
        dheads,
        class_names,
        primary,
        families,
        indeterminate_delta=tau_indet,
    )
    return {
        "spec_version": PANEL_SPEC_VERSION,
        "primary_family": primary,
        "families": families,
        "logit_control": ctrl,
        "disease_logits_heads": dheads,
        "family_max_logit": {k: fam_max[k] for k in fam_order},
        "family_max_logit_stack": stacked,
        "family_keys_order": fam_order,
        "panel_label": lstr,
        "panel_code": lint,
    }
