"""Tests for unified classification metrics."""

from __future__ import annotations

import numpy as np
import pytest

from methyl_validation.classification_metrics import (
    compute_validation_metrics,
    resolve_class_roles,
)


class _StubProjectControlDisease:
    def _get_resolved_groups_with_side(self):
        return [
            ("all", ["/tmp/h1"], "control"),
            ("PCa_Low", ["/tmp/l1"], "disease"),
            ("PCa_High", ["/tmp/hi1"], "disease"),
        ]

    def get_resolved_groups(self):
        return [(label, paths) for label, paths, _ in self._get_resolved_groups_with_side()]


def test_multiclass_h_pcal_h_cm_no_top_level_sensitivity():
    """Regression: H_PCaL-H-like CM must not expose misleading top-level sensitivity."""
    y_true = np.array([0] * 20 + [1] * 13 + [2] * 17, dtype=int)
    y_pred = np.zeros_like(y_true)
    class_names = ["all", "PCa_Low", "PCa_High"]
    roles = resolve_class_roles(_StubProjectControlDisease())
    metrics = compute_validation_metrics(y_true, y_pred, class_names, class_roles=roles)

    assert metrics["n_classes"] == 3
    assert "sensitivity" not in metrics
    assert "specificity" not in metrics
    assert "precision_binary" not in metrics
    assert metrics["macro_recall"] == pytest.approx(1.0 / 3.0)
    assert len(metrics["per_class"]) == 3
    assert metrics["per_class"][0]["recall"] == pytest.approx(1.0)
    assert metrics["per_class"][1]["recall"] == pytest.approx(0.0)

    screening = metrics["screening_binary"]
    assert screening["definition"] == "control_vs_pooled_disease"
    assert screening["control_class_index"] == 0
    assert screening["disease_class_indices"] == [1, 2]
    # All cancer samples predicted as control -> screening sensitivity 0
    assert screening["sensitivity"] == pytest.approx(0.0)
    assert screening["specificity"] == pytest.approx(1.0)


def test_binary_emits_sensitivity_specificity():
    y_true = np.array([0, 0, 1, 1], dtype=int)
    y_pred = np.array([0, 1, 1, 0], dtype=int)
    class_names = ["healthy", "cancer"]
    metrics = compute_validation_metrics(y_true, y_pred, class_names)

    assert metrics["n_classes"] == 2
    assert metrics["sensitivity"] == pytest.approx(0.5)
    assert metrics["specificity"] == pytest.approx(0.5)
    assert "screening_binary" not in metrics
    assert metrics["per_class"][1]["class_name"] == "cancer"


def test_resolve_class_roles_uses_cohort_side():
    roles = resolve_class_roles(_StubProjectControlDisease())
    assert roles["control_class_index"] == 0
    assert roles["disease_class_indices"] == [1, 2]
    assert roles["class_names"] == ["all", "PCa_Low", "PCa_High"]
