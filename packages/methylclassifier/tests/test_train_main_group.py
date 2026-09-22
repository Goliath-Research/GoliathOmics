"""Tests for methyl-classifier-train CLI group resolution."""

from __future__ import annotations

import pytest

from methyl_classifier.cli import train_main


def test_train_main_rejects_unknown_group(monkeypatch):
    class _Cfg:
        chromosome = "1"
        contexts = ["CG"]
        detection_dir = "/tmp/det"
        output_dir = "/tmp/det"
        centroid1_dir = "/tmp/c1"
        centroid2_dir = "/tmp/c2"

    monkeypatch.setattr(
        "methyl_classifier.project_resolver.resolve_classifier_config_per_cancer_group",
        lambda _p: [(_Cfg(), "pca1"), (_Cfg(), "pca2")],
    )

    with pytest.raises(SystemExit):
        train_main.main(
            [
                "--project",
                "/tmp/project.json",
                "--group",
                "unknown_group",
            ]
        )
