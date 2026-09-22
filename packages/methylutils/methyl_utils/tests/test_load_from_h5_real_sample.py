"""Real-data: load a designated reference sample H5 (real MethylExtractor output).

Exercises the loader against real Blosc-compressed, real-``tnc`` H5 - a code path
synthetic fixtures do not cover. Skips when no reference sample is mounted; see
docs/reference/test-data-registry.md.
"""

from __future__ import annotations

import numpy as np
import pytest

from methyl_utils import MethylSample
from methyl_utils.testing import require_reference_sample


@pytest.mark.real_data
@pytest.mark.parametrize("analyte", ["cfdna", "buffy_coat"])
def test_load_reference_sample_cg(analyte: str) -> None:
    sample = require_reference_sample(analyte, chromosome="21", context="CG")
    ms = MethylSample.load_from_h5(sample.h5_path("21", "CG"))

    pos = np.asarray(ms.pos)
    coverage = np.asarray(ms.get_coverage())
    beta = np.asarray(ms.get_methylation_levels())

    assert pos.size > 0
    assert pos.size == coverage.size == beta.size
    # positions sorted ascending (indexed reads rely on this)
    assert np.all(np.diff(pos.astype(np.int64)) >= 0)
    assert np.all(coverage >= 0)
    finite_beta = beta[np.isfinite(beta)]
    assert finite_beta.size > 0
    assert np.all((finite_beta >= 0.0) & (finite_beta <= 1.0))
