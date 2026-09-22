"""Tests for hierarchical OvR panel readout (pairwise max-contrast bundles)."""

import numpy as np
import pytest

from methyl_classifier.models.config_schema import ClassificationConfig
from methyl_classifier.core.panel_fusion import (
    compute_family_max_logits,
    compute_panel_outputs,
    panel_labels_from_combined_logits,
)


def _proba_head(p0: float, p1: float, n: int) -> np.ndarray:
    return np.tile(np.array([[p0, p1]], dtype=np.float64), (n, 1))


def _make_pmc_bundle(*, n: int, heads: list[tuple[float, float]]) -> list[np.ndarray]:
    """heads[k] = (p0, p1) for binary head k; len = 1 + n_diseases for PMC."""
    return [_proba_head(p0, p1, n) for p0, p1 in heads]


def test_validate_spec_duplicate_class_raises():
    spec = {
        "primary_family": "A",
        "families": {"A": ["d1", "d2"], "B": ["d2"]},
    }
    class_names = ["ctrl", "d1", "d2"]
    with pytest.raises(ValueError, match="more than one family"):
        compute_panel_outputs(
            _make_pmc_bundle(n=1, heads=[(0.5, 0.5)] * 3),
            class_names,
            pairwise_max_contrast=True,
            spec=spec,
        )


def test_validate_spec_missing_disease_raises():
    spec = {
        "primary_family": "A",
        "families": {"A": ["d1"]},
    }
    class_names = ["ctrl", "d1", "d2"]
    with pytest.raises(ValueError, match="Missing"):
        compute_panel_outputs(
            _make_pmc_bundle(n=1, heads=[(0.5, 0.5)] * 3),
            class_names,
            pairwise_max_contrast=True,
            spec=spec,
        )


def test_pairwise_max_contrast_required():
    spec = {"primary_family": "A", "families": {"A": ["d1"], "B": ["d2"]}}
    class_names = ["ctrl", "d1", "d2"]
    with pytest.raises(ValueError, match="pairwise max-contrast"):
        compute_panel_outputs(
            _make_pmc_bundle(n=1, heads=[(0.5, 0.5)] * 3),
            class_names,
            pairwise_max_contrast=False,
            spec=spec,
        )


def test_compute_panel_outputs_labels():
    """Three diseases: d1 in primary, d2/d3 in other family."""
    class_names = ["ctrl", "d1", "d2", "d3"]
    spec = {
        "primary_family": "P",
        "families": {"P": ["d1"], "Q": ["d2", "d3"]},
        "indeterminate_delta": 0.25,
    }
    n = 5
    # Head 0 unused for disease logits; still required in bundle.
    # Healthy: all disease logits negative, similar → ctrl = -max(d) positive.
    h_healthy = [(0.5, 0.5), (0.9, 0.1), (0.9, 0.1), (0.9, 0.1)]
    # Primary: d1 strongly positive, others weak.
    h_primary = [(0.5, 0.5), (0.05, 0.95), (0.9, 0.1), (0.9, 0.1)]
    # Alternative: d2 wins.
    h_alt = [(0.5, 0.5), (0.9, 0.1), (0.05, 0.95), (0.9, 0.1)]
    # Q family max: d3 wins (alternative).
    h_alt2 = [(0.5, 0.5), (0.9, 0.1), (0.9, 0.1), (0.05, 0.95)]
    # Indeterminate: winning logit is disease but top-two spread < 0.25.
    h_indet = [(0.5, 0.5), (0.1, 0.9), (0.11, 0.89), (0.9, 0.05)]

    rows = [h_healthy, h_primary, h_alt, h_alt2, h_indet]
    bp = []
    for k in range(4):
        bp.append(
            np.array(
                [[rows[i][k][0], rows[i][k][1]] for i in range(n)],
                dtype=np.float64,
            )
        )

    out = compute_panel_outputs(
        bp, class_names, pairwise_max_contrast=True, spec=spec
    )
    labels = [str(x) for x in out["panel_label"].tolist()]
    codes = out["panel_code"].tolist()
    assert labels[0] == "healthy"
    assert codes[0] == 0
    assert labels[1] == "primary_disease"
    assert codes[1] == 1
    assert labels[2] == "alternative_panel_disease"
    assert labels[3] == "alternative_panel_disease"
    # Row 4: ambiguous top diseases → indeterminate when winning class is disease
    assert labels[4] == "indeterminate"
    assert codes[4] == 3

    fam_p = out["family_max_logit"]["P"]
    fam_q = out["family_max_logit"]["Q"]
    assert fam_p.shape == (n,)
    assert fam_q.shape == (n,)
    # Sample 1: d1 is max in Q block is max of d2,d3 — primary family max should track d1.
    assert fam_p[1] > fam_q[1]


def test_compute_family_max_logits_column_mismatch_raises():
    logit_disease_heads = np.zeros((2, 2), dtype=np.float64)
    class_names = ["c", "a", "b", "c2"]  # three diseases but 2 columns
    with pytest.raises(ValueError, match="columns"):
        compute_family_max_logits(
            logit_disease_heads,
            class_names,
            {"P": ["a"], "Q": ["b", "c2"]},
        )


def test_classification_config_accepts_panel():
    panel = {
        "primary_family": "P",
        "families": {"P": ["d1"], "Q": ["d2"]},
    }
    cfg = ClassificationConfig(
        model_path="/tmp/x.pkl",
        input_path="/tmp/in",
        output_path="/tmp/out.csv",
        panel=panel,
    )
    assert cfg.panel == panel


def test_panel_labels_explicit():
    ctrl = np.array([0.0, 5.0], dtype=np.float64)
    dheads = np.array([[3.0, -1.0], [-1.0, -1.0]], dtype=np.float64)
    class_names = ["ctrl", "d1", "d2"]
    families = {"P": ["d1"], "Q": ["d2"]}
    lint, lstr = panel_labels_from_combined_logits(
        ctrl,
        dheads,
        class_names,
        "P",
        families,
        indeterminate_delta=0.25,
    )
    assert lstr[0] == "primary_disease"
    assert lstr[1] == "healthy"
