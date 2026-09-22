"""Real-data: genome-wide derived measures on a designated reference sample.

Closes the gap that ``compute_sample_genome_measures`` had no H5 integration test
(only pure-kernel unit tests). Skips when no reference sample is mounted; see
docs/reference/test-data-registry.md.
"""

from __future__ import annotations

import math

import pytest

from methyl_derived_measures.config import DerivedMeasuresStepConfig
from methyl_derived_measures.core.genome_measures import compute_sample_genome_measures
from methyl_utils.testing import require_reference_sample


@pytest.mark.real_data
@pytest.mark.parametrize("analyte", ["cfdna", "buffy_coat"])
def test_compute_genome_measures_real_sample(analyte: str) -> None:
    sample = require_reference_sample(analyte, chromosome="21", context="CG")
    sample_id = sample.ref.sample_id or sample.key

    row = compute_sample_genome_measures(
        sample_id,
        str(sample.sample_dir),
        chromosomes=["21"],
        contexts=["CG"],
        cfg=DerivedMeasuresStepConfig(),
    )

    assert row["sample_id"] == sample_id
    mean_beta = row["genome::global_mean_beta"]
    assert not math.isnan(mean_beta), "real chr21 CG should yield a finite mean beta"
    assert 0.0 <= mean_beta <= 1.0
    assert row["genome::median_coverage"] >= 0.0
    # chromosome-scoped column is emitted for the requested chromosome
    assert "genome::chrom_21::mean_beta" in row
