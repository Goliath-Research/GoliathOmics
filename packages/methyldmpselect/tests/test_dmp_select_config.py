"""Tests for methyl-dmp-select config."""

from methyl_dmp_select.models.config import DmpSelectionConfig


def test_dmp_selection_config_accepts_selection_mode():
    cfg = DmpSelectionConfig(
        chromosome="1",
        centroid1_dir="/tmp/c1",
        centroid2_dir="/tmp/c2",
        output_dir="/tmp/out",
        selection_mode="featurecuts_validation",
        target_balanced_accuracy=0.9,
    )
    assert cfg.selection_mode == "featurecuts_validation"
    assert cfg.target_balanced_accuracy == 0.9
