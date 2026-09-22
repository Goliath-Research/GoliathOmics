"""
One-vs-rest (OvR) helpers for K-way ECDF classification.

Stateless utilities: union DMP table construction, column maps, and fusion of K
binary (n, 2) probability matrices into (n, K). Orchestration lives on MethylClassifier.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

ECDF_ONE_VS_REST_TYPE = "ecdf_one_vs_rest"
OV_R_PACKAGE_VERSION = 2
OVR_INFERENCE_VERSION = 1


def _normalize_chrom(c: Any) -> str:
    """
    Human chromosomes are **symbolic IDs**, not integers: ``"1"``…``"22"``, ``"X"``, ``"Y"``
    (optional ``"chr"`` prefix stripped). Code must not use Python ``int`` for autosomes in
    dict keys or DataFrame equality — ``1`` and ``"1"`` must not diverge — and ``X``/``Y``
    are never numeric. Always normalize to ``str`` for joins with sample H5 layout
    (``{chrom}-{context}.h5``).
    """
    s = str(c).strip()
    if s.lower().startswith("chr"):
        s = s[3:]
    return s or str(c)


def _normalize_dmp_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "position" not in out.columns and "pos" in out.columns:
        out = out.rename(columns={"pos": "position"})
    if "chromosome" not in out.columns and "chrom" in out.columns:
        out = out.rename(columns={"chrom": "chromosome"})
    return out


class OvrMultiChromBinaryExpert:
    """
    One OvR class head: per-chromosome binary ECDFs + weights, fused to (n_samples, 2)
    like MethylClassifier multi-chromosome mode.
    """

    def __init__(
        self,
        classifiers: Dict[str, Any],
        chromosome_weights: Dict[str, float],
    ) -> None:
        self.classifiers = {str(k): v for k, v in classifiers.items()}
        self.chromosome_weights = {str(k): float(v) for k, v in chromosome_weights.items()}

    def set_temperature(self, temperature: float) -> None:
        for clf in self.classifiers.values():
            _t = (
                clf.classifier
                if (
                    hasattr(clf, "classifier")
                    and hasattr(getattr(clf, "classifier", None), "set_temperature")
                )
                else clf
            )
            if hasattr(_t, "set_temperature"):
                _t.set_temperature(temperature)

    def predict_proba_binary(
        self,
        methylation_data: np.ndarray,
        availability_mask: Optional[np.ndarray],
        idx: np.ndarray,
        positions_df: pd.DataFrame,
        *,
        calibrated: bool,
        debug: bool = False,
    ) -> np.ndarray:
        n_samples = int(methylation_data.shape[0])
        idx = np.asarray(idx, dtype=np.intp)
        sub_df = positions_df.iloc[idx].reset_index(drop=True)
        sub_df["__slot"] = np.arange(len(sub_df), dtype=np.intp)

        chrom_order = sorted(self.classifiers.keys(), key=lambda x: (len(str(x)), str(x)))
        n_classes = 2
        weighted = np.zeros((n_samples, n_classes), dtype=np.float64)
        active_w = 0.0

        for chrom in chrom_order:
            w = float(self.chromosome_weights.get(chrom, 0.0))
            if w == 0.0:
                continue
            clf = self.classifiers[chrom]
            fi = clf.get_feature_info()
            n_feat = int(fi["n_features"])
            pos_expected = np.asarray(fi["positions"], dtype=np.uint32)

            sel = sub_df.loc[sub_df["chromosome"].astype(str).values == str(chrom)]
            if len(sel) == 0:
                continue
            sel = sel.sort_values("position")
            slots = sel["__slot"].values.astype(np.intp)
            if len(slots) != n_feat:
                raise ValueError(
                    f"OvR multichrom expert chr{chrom}: classifier expects {n_feat} features, "
                    f"union slice has {len(slots)} rows for this chromosome"
                )
            got_pos = sel["position"].astype(np.uint32).values
            # Union DMP rows are globally sorted by (chrom, position); each chrom ECDF may keep
            # feature order from training (dmpDF row order). Same multiset of positions is enough;
            # reorder columns to match get_feature_info()["positions"].
            if np.array_equal(got_pos, pos_expected):
                ordered_slots = slots
            else:
                exp_set = {int(p) for p in pos_expected.tolist()}
                got_set = {int(p) for p in got_pos.tolist()}
                if exp_set != got_set:
                    raise ValueError(
                        f"OvR multichrom expert chr{chrom}: union positions do not match classifier "
                        f"(expected {n_feat} sites; set mismatch vs union slice)"
                    )
                pos_to_slot = {int(got_pos[i]): int(slots[i]) for i in range(len(slots))}
                try:
                    ordered_slots = np.array(
                        [pos_to_slot[int(p)] for p in pos_expected], dtype=np.intp
                    )
                except KeyError as err:
                    raise ValueError(
                        f"OvR multichrom expert chr{chrom}: union positions do not match classifier order"
                    ) from err

            Xc = np.ascontiguousarray(methylation_data[:, idx[ordered_slots]], dtype=np.float64)
            Mc = availability_mask[:, idx[ordered_slots]] if availability_mask is not None else None

            if (
                calibrated
                and hasattr(clf, "predict_proba_calibrated")
                and getattr(clf, "calibrator", None) is not None
            ):
                pc = clf.predict_proba_calibrated(Xc, Mc)
            else:
                pc = clf.predict_proba(Xc, Mc, debug=debug)
            weighted += w * pc
            active_w += w

        if active_w <= 0.0:
            # No chromosome contributed features for this head (wrong paths, missing H5, etc.).
            # Do **not** return (0.5, 0.5): that yields logit 0 in fusion, identical to a genuinely
            # ambiguous ECDF and forces uniform softmax + spurious argmax→0 across heads. NaN signals
            # "no evidence" to :func:`fuse_ovr_binary_probas`.
            return np.full((n_samples, n_classes), np.nan, dtype=np.float64)
        s = np.sum(weighted, axis=1, keepdims=True)
        s = np.where(s == 0, 1.0, s)
        return weighted / s


class OvrPairwiseControlAggregateExpert:
    """
    OvR head for the **control** class when only pairwise control-vs-disease_i detectors exist.

    Each pairwise model outputs ``(n, 2)`` with column 0 = P(control / centroid1) and column 1 =
    P(that disease), matching MethylDetector's binary packages. This head combines those into a
    single ``(n, 2)`` of ``[P(\\neg control), P(control)]`` via the **geometric mean** of the
    per-pairwise P(control), then row-normalizes. Disease OvR heads use each pairwise model once
    (no duplicate path for control vs first disease).
    """

    def __init__(
        self,
        disease_experts: List[Any],
        disease_column_indices: List[np.ndarray],
    ) -> None:
        self.disease_experts = list(disease_experts)
        self.disease_column_indices = [
            np.asarray(x, dtype=np.intp) for x in disease_column_indices
        ]

    def set_temperature(self, temperature: float) -> None:
        for ex in self.disease_experts:
            if isinstance(ex, OvrMultiChromBinaryExpert):
                ex.set_temperature(temperature)
            elif hasattr(ex, "set_temperature"):
                ex.set_temperature(temperature)

    def _pairwise_p_control_column0(
        self,
        expert: Any,
        methylation_data: np.ndarray,
        availability_mask: Optional[np.ndarray],
        idx: np.ndarray,
        positions_df: pd.DataFrame,
        *,
        calibrated: bool,
        debug: bool,
    ) -> np.ndarray:
        if isinstance(expert, OvrMultiChromBinaryExpert):
            pk = expert.predict_proba_binary(
                methylation_data,
                availability_mask,
                idx,
                positions_df,
                calibrated=calibrated,
                debug=debug,
            )
        else:
            Xk = np.ascontiguousarray(methylation_data[:, idx], dtype=np.float64)
            Mk = availability_mask[:, idx] if availability_mask is not None else None
            use_cal = (
                calibrated
                and hasattr(expert, "predict_proba_calibrated")
                and getattr(expert, "calibrator", None) is not None
            )
            if use_cal:
                pk = expert.predict_proba_calibrated(Xk, Mk)
            else:
                pk = expert.predict_proba(Xk, Mk, debug=debug)
        return np.clip(pk[:, 0], 1e-15, 1.0)

    def predict_proba_binary(
        self,
        methylation_data: np.ndarray,
        availability_mask: Optional[np.ndarray],
        idx: np.ndarray,
        positions_df: pd.DataFrame,
        *,
        calibrated: bool,
        debug: bool = False,
    ) -> np.ndarray:
        n_samples = int(methylation_data.shape[0])
        p_controls: List[np.ndarray] = []
        for expert, col_idx in zip(self.disease_experts, self.disease_column_indices):
            pc0 = self._pairwise_p_control_column0(
                expert,
                methylation_data,
                availability_mask,
                col_idx,
                positions_df,
                calibrated=calibrated,
                debug=debug,
            )
            p_controls.append(pc0)
        stack = np.stack(p_controls, axis=1)
        log_m = np.mean(np.log(stack), axis=1)
        p_ctrl = np.clip(np.exp(log_m), 1e-15, 1.0 - 1e-15)
        p_not = np.clip(1.0 - p_ctrl, 1e-15, 1.0 - 1e-15)
        out = np.stack([p_not, p_ctrl], axis=1)
        row_sums = np.sum(out, axis=1, keepdims=True)
        out = out / np.where(row_sums <= 0, 1.0, row_sums)
        return out


class OvrPairwiseColumnAggregateExpert:
    """
    Geometric-mean aggregate over several **pairwise** binary experts, taking one probability column
    from each (column 0 = first centroid / “control side” of that pairwise, column 1 = second).

    Used for **multi-control × multi-disease** bipartite graphs: each control class aggregates
    column 0 over all pairwises for that control; each disease class aggregates column 1 over all
    pairwises for that disease.
    """

    def __init__(
        self,
        pairwise_experts: List[Any],
        column_indices: List[np.ndarray],
        *,
        probability_column: int,
    ) -> None:
        self.pairwise_experts = list(pairwise_experts)
        self.column_indices = [np.asarray(x, dtype=np.intp) for x in column_indices]
        self.probability_column = int(probability_column)
        if self.probability_column not in (0, 1):
            raise ValueError("probability_column must be 0 or 1")

    def set_temperature(self, temperature: float) -> None:
        for ex in self.pairwise_experts:
            if isinstance(ex, OvrMultiChromBinaryExpert):
                ex.set_temperature(temperature)
            elif hasattr(ex, "set_temperature"):
                ex.set_temperature(temperature)

    def _pairwise_p_column(
        self,
        expert: Any,
        methylation_data: np.ndarray,
        availability_mask: Optional[np.ndarray],
        idx: np.ndarray,
        positions_df: pd.DataFrame,
        *,
        calibrated: bool,
        debug: bool,
    ) -> np.ndarray:
        if isinstance(expert, OvrMultiChromBinaryExpert):
            pk = expert.predict_proba_binary(
                methylation_data,
                availability_mask,
                idx,
                positions_df,
                calibrated=calibrated,
                debug=debug,
            )
        else:
            Xk = np.ascontiguousarray(methylation_data[:, idx], dtype=np.float64)
            Mk = availability_mask[:, idx] if availability_mask is not None else None
            use_cal = (
                calibrated
                and hasattr(expert, "predict_proba_calibrated")
                and getattr(expert, "calibrator", None) is not None
            )
            if use_cal:
                pk = expert.predict_proba_calibrated(Xk, Mk)
            else:
                pk = expert.predict_proba(Xk, Mk, debug=debug)
        c = self.probability_column
        return np.clip(pk[:, c], 1e-15, 1.0)

    def predict_proba_binary(
        self,
        methylation_data: np.ndarray,
        availability_mask: Optional[np.ndarray],
        idx: np.ndarray,
        positions_df: pd.DataFrame,
        *,
        calibrated: bool,
        debug: bool = False,
    ) -> np.ndarray:
        n_samples = int(methylation_data.shape[0])
        cols: List[np.ndarray] = []
        for expert, col_idx in zip(self.pairwise_experts, self.column_indices):
            pc = self._pairwise_p_column(
                expert,
                methylation_data,
                availability_mask,
                col_idx,
                positions_df,
                calibrated=calibrated,
                debug=debug,
            )
            cols.append(pc)
        stack = np.stack(cols, axis=1)
        log_m = np.mean(np.log(stack), axis=1)
        p_pos = np.clip(np.exp(log_m), 1e-15, 1.0 - 1e-15)
        p_neg = np.clip(1.0 - p_pos, 1e-15, 1.0 - 1e-15)
        out = np.stack([p_neg, p_pos], axis=1)
        row_sums = np.sum(out, axis=1, keepdims=True)
        out = out / np.where(row_sums <= 0, 1.0, row_sums)
        return out


def dmp_rows_from_binary_entry(
    entry: Dict[str, Any],
    default_chromosome: str = "1",
) -> List[Tuple[str, int]]:
    """
    Return ordered list of (chromosome, position) for one OvR binary model.

    Prefer entry['dmp_df'] with chromosome + position (or pos).
    Else use entry['chromosome'] + ECDF positions.
    """
    if entry.get("control_pairwise_geometric"):
        return []

    if entry.get("chrom_classifiers"):
        if entry.get("dmp_df") is None:
            raise ValueError("Multichrom OvR binary entry requires dmp_df")
        df = _normalize_dmp_columns(entry["dmp_df"])
        if "chromosome" not in df.columns or "position" not in df.columns:
            raise ValueError("Multichrom OvR dmp_df must have chromosome and position columns")
        chrom_col = df["chromosome"].map(_normalize_chrom)
        pos_col = df["position"].astype(np.uint64).astype(np.int64)
        return list(zip(chrom_col.tolist(), pos_col.tolist()))

    if "dmp_df" in entry and entry["dmp_df"] is not None:
        df = _normalize_dmp_columns(entry["dmp_df"])
        if "chromosome" not in df.columns or "position" not in df.columns:
            raise ValueError(
                "OvR binary model dmp_df must have chromosome and position (or pos) columns"
            )
        chrom_col = df["chromosome"].map(_normalize_chrom)
        pos_col = df["position"].astype(np.uint64).astype(np.int64)
        return list(zip(chrom_col.tolist(), pos_col.tolist()))

    ecdf = entry.get("ecdf")
    if ecdf is None:
        raise ValueError("OvR binary model needs 'ecdf' or 'dmp_df'")
    chrom = _normalize_chrom(entry.get("chromosome", default_chromosome))
    pos = np.asarray(ecdf.positions, dtype=np.uint32).ravel()
    return [(chrom, int(p)) for p in pos]


def build_union_dmp_dataframe(
    binary_entries: Sequence[Dict[str, Any]],
    default_chromosome: str = "1",
) -> Tuple[pd.DataFrame, List[np.ndarray]]:
    """
    Unique (chromosome, position) rows in stable sorted order; one column per row in flat X.

    Returns
    -------
    dmp_positions_df : DataFrame with columns chromosome (category), position (uint32)
    column_indices : list of length K; column_indices[k] maps binary model k columns -> union columns
    """
    entries_list = list(binary_entries)
    prefix_geo = bool(entries_list and entries_list[0].get("control_pairwise_geometric"))
    body = entries_list[1:] if prefix_geo else entries_list
    if not body:
        raise ValueError(
            "OvR union needs at least one disease binary model "
            "(entries after control_pairwise_geometric, if present)"
        )

    all_rows: List[Tuple[str, int]] = []
    per_entry_rows: List[List[Tuple[str, int]]] = []
    for entry in body:
        rows = dmp_rows_from_binary_entry(entry, default_chromosome=default_chromosome)
        rows_n = [(_normalize_chrom(c), int(p)) for c, p in rows]
        per_entry_rows.append(rows_n)
        all_rows.extend(rows_n)

    unique_sorted = sorted(set(all_rows), key=lambda t: (t[0], t[1]))
    df = pd.DataFrame(unique_sorted, columns=["chromosome", "position"])
    df["chromosome"] = df["chromosome"].astype(str).astype("category")
    df["position"] = df["position"].astype(np.uint32)

    key_to_idx = {(c, int(p)): i for i, (c, p) in enumerate(unique_sorted)}
    column_indices: List[np.ndarray] = []
    for rows in per_entry_rows:
        idx = np.array([key_to_idx[(c, int(p))] for c, p in rows], dtype=np.int32)
        column_indices.append(idx)

    if prefix_geo:
        full = np.arange(len(df), dtype=np.int32)
        column_indices = [full] + column_indices

    return df, column_indices


def build_union_dmp_dataframe_flat(
    binary_entries: Sequence[Dict[str, Any]],
    default_chromosome: str = "1",
) -> Tuple[pd.DataFrame, List[np.ndarray]]:
    """
    Union DMP table over a flat list of binary model entries (no ``control_pairwise_geometric`` marker).
    Used for bipartite multi-control × multi-disease OvR (M×N pairwises).
    """
    entries_list = list(binary_entries)
    if not entries_list:
        raise ValueError("OvR union needs at least one binary model")
    all_rows: List[Tuple[str, int]] = []
    per_entry_rows: List[List[Tuple[str, int]]] = []
    for entry in entries_list:
        if entry.get("control_pairwise_geometric") or entry.get("bipartite_pairwise_geometric"):
            raise ValueError("flat union does not accept aggregate marker entries")
        rows = dmp_rows_from_binary_entry(entry, default_chromosome=default_chromosome)
        rows_n = [(_normalize_chrom(c), int(p)) for c, p in rows]
        per_entry_rows.append(rows_n)
        all_rows.extend(rows_n)

    unique_sorted = sorted(set(all_rows), key=lambda t: (t[0], t[1]))
    df = pd.DataFrame(unique_sorted, columns=["chromosome", "position"])
    df["chromosome"] = df["chromosome"].astype(str).astype("category")
    df["position"] = df["position"].astype(np.uint32)

    key_to_idx = {(c, int(p)): i for i, (c, p) in enumerate(unique_sorted)}
    column_indices: List[np.ndarray] = []
    for rows in per_entry_rows:
        idx = np.array([key_to_idx[(c, int(p))] for c, p in rows], dtype=np.int32)
        column_indices.append(idx)

    return df, column_indices


def _logits_from_binary_columns(
    binary_probas: Sequence[np.ndarray],
    head_indices: Sequence[int],
    n: int,
    eps: float,
) -> np.ndarray:
    """Extract log(P1/P0) per row for selected heads; shape (n, len(head_indices))."""
    out = np.full((n, len(head_indices)), -np.inf, dtype=np.float64)
    for j, k in enumerate(head_indices):
        p = binary_probas[k]
        if p.shape != (n, 2):
            raise ValueError(f"Expected proba shape ({n}, 2), got {p.shape}")
        p0 = p[:, 0]
        p1 = p[:, 1]
        ok = np.isfinite(p0) & np.isfinite(p1) & (p0 > 0.0) & (p1 > 0.0)
        p0c = np.clip(p0, eps, 1.0)
        p1c = np.clip(p1, eps, 1.0)
        col = np.full(n, -np.inf, dtype=np.float64)
        col[ok] = np.log(p1c[ok]) - np.log(p0c[ok])
        out[:, j] = col
    return out


def _softmax_ovr_logits(logits: np.ndarray, K: int) -> np.ndarray:
    """Stable softmax over rows; all-missing rows → uniform ``1/K``."""
    finite = np.isfinite(logits) & (logits > -np.inf)
    has_any = np.any(finite, axis=1, keepdims=True)
    row_max = np.max(np.where(finite, logits, -np.inf), axis=1, keepdims=True)
    row_max_safe = np.where(has_any, row_max, 0.0)
    shifted = np.where(finite, logits - row_max_safe, -np.inf)
    shifted = np.clip(shifted, -700.0, 700.0)
    ex = np.exp(shifted)
    ex = np.where(finite, ex, 0.0)
    denom = np.sum(ex, axis=1, keepdims=True)
    no_signal = (~np.squeeze(has_any, axis=1)) | (np.squeeze(denom, axis=1) <= 0.0)
    out = ex / np.where(denom > 0.0, denom, 1.0)
    if np.any(no_signal):
        out = out.copy()
        out[no_signal, :] = 1.0 / float(K)
    return out


def fuse_ovr_binary_probas(
    binary_probas: Sequence[np.ndarray],
    eps: float = 1e-12,
    *,
    pairwise_max_contrast_control: bool = False,
) -> np.ndarray:
    """
    Fuse K matrices of shape (n_samples, 2) into (n_samples, K) probabilities.

    **Flat fusion** (default): logit_k = log(P_k+) - log(P_k-) for every head, then softmax.

    **Pairwise max-contrast** (``pairwise_max_contrast_control=True``): for ``control_pairwise_geometric``
    bundles, the first head is a geometric aggregate of the **same** pairwise P(control) values that
    define heads ``1..K-1``. Softmaxing that aggregate logit **together** with pairwise logits
    double-counts control evidence and inflates class 0. This mode **drops** the aggregate from the
    softmax and sets

    ``logit_control = -max(logit_disease_1, …, logit_disease_{K-1})``,

    so control wins only when **no** pairwise head claims strong disease-vs-control log-odds.

    **NaN** binary columns mean no evidence for that head. All-missing rows → uniform ``1/K``.
    """
    if not binary_probas:
        raise ValueError("binary_probas must be non-empty")
    n = int(binary_probas[0].shape[0])
    K = len(binary_probas)

    if pairwise_max_contrast_control and K >= 2:
        logits = np.full((n, K), -np.inf, dtype=np.float64)
        if K > 1:
            logits[:, 1:K] = _logits_from_binary_columns(
                binary_probas, list(range(1, K)), n, eps
            )
            d_slice = logits[:, 1:K]
            finite_d = np.isfinite(d_slice)
            has_any_d = np.any(finite_d, axis=1)
            row_max_d = np.max(np.where(finite_d, d_slice, -np.inf), axis=1)
            logits[:, 0] = np.where(has_any_d, -row_max_d, -np.inf)
        return _softmax_ovr_logits(logits, K)

    logits = _logits_from_binary_columns(binary_probas, list(range(K)), n, eps)
    return _softmax_ovr_logits(logits, K)
