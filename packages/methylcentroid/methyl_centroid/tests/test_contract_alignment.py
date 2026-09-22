import json
from pathlib import Path
from tempfile import TemporaryDirectory

import h5py
import numpy as np
import pytest
from pydantic import ValidationError

from methyl_centroid.config import MethylCentroidConfig
from methyl_centroid.methyl_centroid import MethylCentroid


def create_sample_file(
    filepath: Path,
    positions: np.ndarray,
    mC: np.ndarray,
    uC: np.ndarray,
    tnc: np.ndarray,
) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(filepath, "w") as f:
        group = f.create_group("methylation_data")
        group.create_dataset("pos", data=positions, dtype=np.uint32)
        group.create_dataset("mC", data=mC, dtype=np.uint32)
        group.create_dataset("uC", data=uC, dtype=np.uint32)
        group.create_dataset("tnc", data=tnc, dtype=np.uint8)


def read_json_attr(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value


def test_build_centroid_applies_add_and_remove_samples_and_persists_final_cohort():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        output_dir = root / "output"
        output_dir.mkdir()

        sample1_dir = root / "sample1"
        sample2_dir = root / "sample2"
        sample3_dir = root / "sample3"

        create_sample_file(
            sample1_dir / "1-CG.h5",
            np.array([1000, 2000], dtype=np.uint32),
            np.array([10, 20], dtype=np.uint32),
            np.array([10, 10], dtype=np.uint32),
            np.array([1, 1], dtype=np.uint8),
        )
        create_sample_file(
            sample2_dir / "1-CG.h5",
            np.array([2000], dtype=np.uint32),
            np.array([12], dtype=np.uint32),
            np.array([8], dtype=np.uint32),
            np.array([1], dtype=np.uint8),
        )
        create_sample_file(
            sample3_dir / "1-CG.h5",
            np.array([2000], dtype=np.uint32),
            np.array([18], dtype=np.uint32),
            np.array([2], dtype=np.uint32),
            np.array([1], dtype=np.uint8),
        )

        mc0 = MethylCentroid(
            laboratory="lab",
            disease="disease",
            group="group",
            batch="batch",
            chrom="1",
            ctx="CG",
            output_dir=output_dir,
            add_samples=[str(sample1_dir), str(sample2_dir)],
            min_coverage=1,
            min_samples=1,
            use_gpu=False,
            binned_stats_bins=20,
            verbose=False,
        )
        mc0.build_centroid()

        runner = MethylCentroid(
            laboratory="lab",
            disease="disease",
            group="group",
            batch="batch",
            chrom="1",
            ctx="CG",
            output_dir=output_dir,
            add_samples=[str(sample3_dir)],
            remove_samples=[str(sample1_dir)],
            min_coverage=1,
            min_samples=1,
            use_gpu=False,
            binned_stats_bins=20,
            verbose=False,
        )

        results = runner.build_centroid()
        centroid_path = Path(results.final_centroid_path)
        config_path = output_dir / "1-CG_config.json"

        assert centroid_path.exists()
        assert config_path.exists()
        assert results.total_samples_processed == 2

        with h5py.File(centroid_path, "r") as f:
            data = f["methylation_data"]
            positions = data["pos"][:]
            assert 1000 not in positions
            pos_2000_idx = np.where(positions == 2000)[0][0]
            assert data["N"][pos_2000_idx] == 2
            assert f.attrs["binned_stats_bins"] == 20
            samples_used = read_json_attr(f.attrs["samples_used"])
            assert samples_used == ["sample2", "sample3"]
            assert f.attrs.get("samples_base_path") == str(root)

        saved_config = json.loads(config_path.read_text(encoding="utf-8"))
        assert "samples" not in saved_config
        assert saved_config["add_samples"] == []
        assert saved_config["remove_samples"] == []


def test_runner_rejects_non_positive_binned_stats_bins():
    with TemporaryDirectory() as temp_dir:
        with pytest.raises(ValueError, match="binned_stats_bins must be >= 1"):
            MethylCentroid(
                laboratory="lab",
                disease="disease",
                group="group",
                batch="batch",
                chrom="1",
                ctx="CG",
                output_dir=Path(temp_dir),
                use_gpu=False,
                binned_stats_bins=0,
            )


def test_config_rejects_non_positive_binned_stats_bins():
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        MethylCentroidConfig(
            laboratory="lab",
            disease="disease",
            group="group",
            batch="batch",
            chrom="1",
            ctx="CG",
            output_dir="/tmp/out",
            binned_stats_bins=0,
        )


def test_config_accepts_gpu_backend_choices():
    kwargs = dict(
        laboratory="lab",
        disease="disease",
        group="group",
        batch="batch",
        chrom="1",
        ctx="CG",
        output_dir="/tmp/out",
    )
    assert MethylCentroidConfig(**kwargs).gpu_backend is None
    assert MethylCentroidConfig(**kwargs, gpu_backend="numpy").gpu_backend == "numpy"
    assert MethylCentroidConfig(**kwargs, gpu_backend="CUPY").gpu_backend == "cupy"
    assert MethylCentroidConfig(**kwargs, gpu_backend="mojo").gpu_backend == "mojo"
    with pytest.raises(ValidationError, match="gpu_backend"):
        MethylCentroidConfig(**kwargs, gpu_backend="opencl")


def test_streaming_builder_mojo_respects_use_gpu_false(monkeypatch, tmp_path):
    """gpu_backend=mojo must not force prefer_gpu=True when use_gpu=False."""

    def boom():
        raise RuntimeError("require_mojo_numeric should not run when use_gpu=False")

    monkeypatch.setattr(
        "methyl_utils.mojo_numeric.require_mojo_numeric", boom
    )
    mc = MethylCentroid(
        laboratory="lab",
        disease="disease",
        group="group",
        batch="batch",
        chrom="1",
        ctx="CG",
        output_dir=tmp_path,
        use_gpu=False,
        gpu_backend="mojo",
    )
    assert mc.use_gpu is False
    builder = mc._create_streaming_builder()
    assert builder.use_gpu is False
    assert builder.gpu_backend == "numpy"


def test_config_rejects_removed_samples_field():
    with pytest.raises(ValidationError, match="samples"):
        MethylCentroidConfig(
            laboratory="lab",
            disease="disease",
            group="group",
            batch="batch",
            chrom="1",
            ctx="CG",
            output_dir="/tmp/out",
            samples=["/tmp/a"],
        )
