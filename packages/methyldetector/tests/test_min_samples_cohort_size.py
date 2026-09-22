"""Cohort size for min_samples_pct should follow metadata / lists, not only N.max()."""

import numpy as np

from methyl_detector.explorer import _min_samples_cohort_size_from_centroid


def test_cohort_size_prefers_n_samples_metadata():
    class Cent:
        N = np.array([2, 3, 5], dtype=np.int64)
        metadata = {"n_samples": 40}

    assert _min_samples_cohort_size_from_centroid(Cent()) == 40


def test_cohort_size_falls_back_to_n_max():
    class Cent:
        N = np.array([2, 3, 7], dtype=np.int64)
        metadata = {}

    assert _min_samples_cohort_size_from_centroid(Cent()) == 7
