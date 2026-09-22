"""BAM path resolution for fragmentomics step."""

from pathlib import Path

from methyl_fragmentomics.config import FragmentomicsStepConfig
from methyl_fragmentomics.core.bam_resolve import resolve_sample_bams


def test_resolve_explicit_sample_bam_paths(tmp_path: Path):
    bam = tmp_path / "s1.bam"
    bam.write_bytes(b"")
    cfg = FragmentomicsStepConfig(
        enabled=True,
        sample_bam_paths={"sampleA": str(bam)},
    )
    got = resolve_sample_bams([], cfg)
    assert got == [("sampleA", bam)]


def test_resolve_canonical_bam_in_sample_dir(tmp_path: Path):
    sample_dir = tmp_path / "sampleB"
    sample_dir.mkdir()
    bam = sample_dir / "sampleB.bam"
    bam.write_bytes(b"")
    cfg = FragmentomicsStepConfig(enabled=True)
    got = resolve_sample_bams([str(sample_dir)], cfg)
    assert got == [("sampleB", bam)]
