"""Unit tests for MethylCluster configuration validation and (de)serialization.

Imports submodules directly so the tests do not pull the package __init__
(which imports GPU/plotting-heavy modules).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from methyl_cluster.config import ClusterMetric, ClusteringMethod, MethylClusterConfig


def _base_kwargs(tmp_path: Path, **overrides):
    kwargs = dict(
        samples=["/data/sampleA", "/data/sampleB"],
        chrom="1",
        ctx="CG",
        output_dir=str(tmp_path / "out"),
    )
    kwargs.update(overrides)
    return kwargs


def test_minimal_config_applies_documented_defaults(tmp_path: Path) -> None:
    cfg = MethylClusterConfig(**_base_kwargs(tmp_path))
    assert cfg.clustering_method == ClusteringMethod.CENTROID
    assert cfg.metric == ClusterMetric.JENSEN_SHANNON
    # output_dir is normalized to an absolute resolved path.
    assert Path(cfg.output_dir).is_absolute()


def test_context_must_be_valid(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Context must be one of"):
        MethylClusterConfig(**_base_kwargs(tmp_path, ctx="XY"))


def test_requires_at_least_two_samples(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="At least 2 samples"):
        MethylClusterConfig(**_base_kwargs(tmp_path, samples=["/data/only"]))


def test_selection_method_must_be_eom_or_leaf(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Cluster selection method"):
        MethylClusterConfig(**_base_kwargs(tmp_path, cluster_selection_method="bogus"))


@pytest.mark.parametrize("bad_temp", [0.05, 25.0])
def test_assignment_temperature_bounds(tmp_path: Path, bad_temp: float) -> None:
    with pytest.raises(ValueError):
        MethylClusterConfig(**_base_kwargs(tmp_path, assignment_temperature=bad_temp))


def test_forced_groups_sizes_must_sum_to_samples(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must equal number of samples"):
        MethylClusterConfig(
            **_base_kwargs(
                tmp_path,
                samples=["/a", "/b", "/c"],
                forced_groups={"Healthy": 1, "Cancer": 1},
            )
        )


def test_forced_groups_require_centroid_method(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="clustering_method='centroid'"):
        MethylClusterConfig(
            **_base_kwargs(
                tmp_path,
                samples=["/a", "/b"],
                clustering_method=ClusteringMethod.HIERARCHICAL,
                forced_groups={"Healthy": 1, "Cancer": 1},
            )
        )


def test_valid_forced_groups_roundtrip_to_file(tmp_path: Path) -> None:
    cfg = MethylClusterConfig(
        **_base_kwargs(
            tmp_path,
            samples=["/a", "/b", "/c"],
            forced_groups={"Healthy": 2, "Cancer": 1},
        )
    )
    dest = tmp_path / "cfg.json"
    cfg.to_file(dest)
    assert dest.is_file()

    loaded = MethylClusterConfig.from_file(dest)
    assert loaded.forced_groups == {"Healthy": 2, "Cancer": 1}
    assert loaded.samples == ["/a", "/b", "/c"]
    assert loaded.output_dir == cfg.output_dir


def test_cluster_metric_to_factory_name_matches_value() -> None:
    assert ClusterMetric.WEIGHTED_JENSEN_SHANNON.to_factory_name() == "weighted_jensen_shannon"
    assert ClusterMetric.HELLINGER.to_factory_name() == "hellinger"
