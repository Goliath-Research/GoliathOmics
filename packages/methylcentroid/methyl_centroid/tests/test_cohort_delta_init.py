"""Tests for incremental cohort list resolution when baseline HDF5 is absent."""

from pathlib import Path
from tempfile import TemporaryDirectory

from methyl_centroid.methyl_centroid import MethylCentroid, _plan_cohort_lists_for_runner


def test_plan_cohort_lists_empty_baseline_with_add_and_remove():
    with TemporaryDirectory() as temp_dir:
        out = Path(temp_dir)
        orig_s, orig_a, orig_r = _plan_cohort_lists_for_runner(
            add_samples=["/samples/new_a"],
            remove_samples=["/samples/old_b"],
            output_dir=out,
            chrom="1",
            ctx="CG",
        )
        assert orig_s == []
        assert orig_a == ["/samples/new_a"]
        assert orig_r == ["/samples/old_b"]


def test_methyl_centroid_init_keeps_adds_separate_when_removes_without_baseline():
    with TemporaryDirectory() as temp_dir:
        out = Path(temp_dir)
        mc = MethylCentroid(
            chrom="1",
            ctx="CG",
            output_dir=out,
            add_samples=["/samples/new_a"],
            remove_samples=["/samples/old_b"],
            min_coverage=4,
            verbose=False,
        )
        assert mc._original_samples == []
        assert mc._original_add_samples == ["/samples/new_a"]
        assert mc._original_remove_samples == ["/samples/old_b"]
        assert mc._resolve_effective_sample_dirs() == ["/samples/new_a"]
