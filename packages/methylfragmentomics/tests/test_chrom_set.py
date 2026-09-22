"""Chromosome filter normalization for fragmentomics."""

from methyl_fragmentomics.config import FragmentomicsStepConfig
from methyl_fragmentomics.core.runner import _chrom_set


def test_chrom_set_bare_names_include_chr_prefixed():
    cfg = FragmentomicsStepConfig(enabled=True, chromosomes=["1", "X"])
    assert _chrom_set(cfg, None) == {"1", "chr1", "X", "chrX"}


def test_chrom_set_chr_prefixed_names_include_bare():
    cfg = FragmentomicsStepConfig(enabled=True, chromosomes=["chr1", "chrX"])
    assert _chrom_set(cfg, None) == {"chr1", "1", "chrX", "X"}


def test_chrom_set_falls_back_to_project_chromosomes():
    cfg = FragmentomicsStepConfig(enabled=True)
    assert _chrom_set(cfg, ["chr2"]) == {"chr2", "2"}


def test_chrom_set_none_when_unconfigured():
    cfg = FragmentomicsStepConfig(enabled=True)
    assert _chrom_set(cfg, None) is None
