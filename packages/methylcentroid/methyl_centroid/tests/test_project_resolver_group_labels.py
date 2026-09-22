"""Resolve project group labels for methyl-centroid --group."""

import pytest

from methyl_centroid.project_resolver import _normalize_centroid_group_arg


class _StubProject:
    def get_resolved_groups(self):
        return [("all", ["/samples/H1"]), ("PCa", ["/samples/D1"])]


def test_normalize_accepts_group_aliases() -> None:
    project = _StubProject()
    assert _normalize_centroid_group_arg(project, "group1") == "group1"
    assert _normalize_centroid_group_arg(project, "group2") == "group2"
    assert _normalize_centroid_group_arg(project, 0) == 0


def test_normalize_accepts_project_group_labels() -> None:
    project = _StubProject()
    assert _normalize_centroid_group_arg(project, "all") == 0
    assert _normalize_centroid_group_arg(project, "PCa") == 1
    assert _normalize_centroid_group_arg(project, "pca") == 1


def test_normalize_rejects_unknown_label() -> None:
    project = _StubProject()
    with pytest.raises(ValueError, match="group 'missing' not found"):
        _normalize_centroid_group_arg(project, "missing")
