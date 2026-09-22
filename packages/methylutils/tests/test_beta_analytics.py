"""Tests for the shared Beta log-pdf helper (consumed by methyl_cluster)."""

from __future__ import annotations

import numpy as np
from scipy.stats import beta as scipy_beta

from methyl_utils.beta_analytics import beta_log_pdf


def test_beta_log_pdf_matches_scipy_scalar():
    got = beta_log_pdf(0.3, 2.0, 5.0)
    expected = scipy_beta.logpdf(0.3, 2.0, 5.0)
    assert np.isclose(got, expected)


def test_beta_log_pdf_vectorized():
    x = np.array([0.1, 0.5, 0.9])
    got = beta_log_pdf(x, 2.0, 2.0)
    expected = scipy_beta.logpdf(x, 2.0, 2.0)
    assert np.allclose(got, expected)


def test_beta_log_pdf_clips_endpoints_to_finite():
    # x exactly at 0 and 1 would be -inf/undefined; helper clips to stay finite.
    got = beta_log_pdf(np.array([0.0, 1.0]), 2.0, 2.0)
    assert np.all(np.isfinite(got))


def test_beta_log_pdf_broadcasts_params():
    x = np.array([0.2, 0.8])
    got = beta_log_pdf(x, np.array([2.0, 3.0]), np.array([2.0, 3.0]))
    expected = scipy_beta.logpdf(x, np.array([2.0, 3.0]), np.array([2.0, 3.0]))
    assert np.allclose(got, expected)
