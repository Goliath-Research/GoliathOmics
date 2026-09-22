"""Reject obsolete ``samples`` in centroid step_config / overrides."""

import pytest

from methyl_centroid.project_resolver import _forbid_centroid_samples_key


def test_forbid_rejects_base_config_samples_key_even_when_empty():
    with pytest.raises(ValueError, match='obsolete key "samples"'):
        _forbid_centroid_samples_key({"base_config": {"samples": []}}, "unit test")


def test_forbid_rejects_base_config_samples_nonempty():
    with pytest.raises(ValueError, match='obsolete key "samples"'):
        _forbid_centroid_samples_key(
            {"base_config": {"samples": ["/tmp/a"], "add_samples": []}},
            "unit test",
        )


def test_forbid_accepts_valid_centroid_step_dict():
    _forbid_centroid_samples_key({"base_config": {"min_coverage": 3}}, "unit test")
    _forbid_centroid_samples_key({}, "unit test")
    _forbid_centroid_samples_key(
        {"base_config": {"add_samples": ["/x"], "remove_samples": []}},
        "unit test",
    )
