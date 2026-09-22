"""Classifier resolver should use project training cohort for validation when unset."""

from unittest.mock import MagicMock

from methyl_classifier.project_resolver import _apply_training_cohort_to_classifier_base


def test_apply_training_cohort_to_classifier_base_sets_sample_paths():
    project = MagicMock()
    project.uses_control_disease.return_value = True
    project._get_resolved_groups_with_side.return_value = [
        ("healthy", ["/samples/c1", "/samples/c2"], "control"),
        ("disease", ["/samples/d1"], "disease"),
    ]
    base: dict = {}
    _apply_training_cohort_to_classifier_base(project, base)
    assert base["centroid1_sample_paths"] == ["/samples/c1", "/samples/c2"]
    assert base["centroid2_sample_paths"] == ["/samples/d1"]


def test_apply_training_cohort_skips_when_explicit_paths_set():
    project = MagicMock()
    base = {"centroid1_sample_paths": ["/explicit"], "centroid2_sample_paths": ["/explicit2"]}
    _apply_training_cohort_to_classifier_base(project, base)
    assert base["centroid1_sample_paths"] == ["/explicit"]
