"""Tests for MethylSampleRef → HDF5 path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from methyl_domain.helpers import apply_methylation_to_sample, resolve_methylation_h5_path
from methyl_domain.types import MethylSampleRef


def test_resolve_methylation_h5_path_from_pattern() -> None:
    sample = MethylSampleRef(sampleId="S1", sampleDir="/work/samples/S1")
    sample = apply_methylation_to_sample(
        sample,
        chromosomes=["21"],
        contexts=["CG"],
    )
    path = resolve_methylation_h5_path(sample, "21", "CG")
    assert path == Path("/work/samples/S1/21-CG.h5")


def test_resolve_methylation_h5_path_from_h5_files() -> None:
    sample = MethylSampleRef(sampleId="S1", sampleDir="/work/samples/S1")
    sample = apply_methylation_to_sample(
        sample,
        chromosomes=["21"],
        contexts=["CG"],
        h5_files=["21-CG.h5", "21-CHG.h5"],
    )
    path = resolve_methylation_h5_path(sample, "21", "CG")
    assert path.name == "21-CG.h5"


def test_resolve_methylation_h5_path_requires_methylation_ref() -> None:
    sample = MethylSampleRef(sampleId="S1", sampleDir="/work/samples/S1")
    with pytest.raises(ValueError, match="no methylation ref"):
        resolve_methylation_h5_path(sample, "21")
