import csv
import json
from pathlib import Path
from types import SimpleNamespace

from methyl_validation.cli import (
    _load_model_mc_shared_rows,
    _shared_run_metadata_from_step_timings,
)
from methyl_validation.model_mc_runner import (
    _exclude_configured_holdout,
    _requires_detector_classifier_models,
)


def test_model_mc_excludes_locked_holdout_from_reuse_pool() -> None:
    config = SimpleNamespace(
        holdout_exclude_from_training=True,
        holdout_partition="locked_test",
        validation_partitions=SimpleNamespace(
            locked_test=["/work/samples/control-held", "/work/samples/disease-held"]
        ),
    )

    filtered = _exclude_configured_holdout(
        [
            ("control", ["/work/samples/control-dev", "/other/control-held"]),
            ("disease", ["/work/samples/disease-dev", "/other/disease-held"]),
        ],
        config,
    )

    assert filtered == [
        ("control", ["/work/samples/control-dev"]),
        ("disease", ["/work/samples/disease-dev"]),
    ]


def test_model_mc_keeps_full_pool_when_holdout_exclusion_disabled() -> None:
    pools = [("control", ["/work/samples/control", "/work/samples/held"])]
    config = SimpleNamespace(
        holdout_exclude_from_training=False,
        holdout_partition="locked_test",
        validation_partitions=SimpleNamespace(locked_test=["/work/samples/held"]),
    )

    assert _exclude_configured_holdout(pools, config) == pools


def test_raw_gene_ecdf_does_not_require_detector_classifier_models() -> None:
    class Config:
        def with_backend_selection(self, backend: str) -> SimpleNamespace:
            assert backend == "ecdf"
            return SimpleNamespace(feature_mode="raw_gene", ecdf_aggregated_enabled=False)

    assert _requires_detector_classifier_models(Config(), ["ecdf"]) is False


def test_classic_raw_dmp_ecdf_requires_detector_classifier_models() -> None:
    class Config:
        def with_backend_selection(self, backend: str) -> SimpleNamespace:
            assert backend == "ecdf"
            return SimpleNamespace(feature_mode="raw_dmp", ecdf_aggregated_enabled=False)

    assert _requires_detector_classifier_models(Config(), ["ecdf"]) is True


def test_shared_run_metadata_marks_detector_ok_from_synthetic_timing() -> None:
    meta = _shared_run_metadata_from_step_timings(
        [
            {
                "run_id": "run_0001",
                "step_name": "methyl-detector",
                "return_code": 0,
                "n_train_samples": 10,
                "n_val_samples": 4,
            }
        ]
    )
    assert meta["run_0001"]["detector_ok"] is True
    assert meta["run_0001"]["n_train_samples"] == 10


def test_load_model_mc_shared_rows_keeps_runs_with_detector_timing(tmp_path: Path) -> None:
    """Runs kept via _shared_run_ready must appear in step_timings or backend reuse drops them."""
    shared = tmp_path / "shared"
    for run_id in ("run_0001", "run_0002"):
        run_dir = shared / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "project.json").write_text(
            json.dumps({"project_name": run_id, "output_base": str(shared)}),
            encoding="utf-8",
        )
    timings_path = shared / "step_timings.csv"
    with timings_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "step_name",
                "duration_seconds",
                "return_code",
                "n_train_samples",
                "n_val_samples",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "run_id": "run_0001",
                "step_name": "methyl-detector",
                "duration_seconds": "0.0",
                "return_code": "0",
                "n_train_samples": "10",
                "n_val_samples": "4",
            }
        )
        # run_0002 has no detector timing → must be skipped when timings exist

    rows = _load_model_mc_shared_rows(shared_root=shared, n_iterations=2)
    assert [row["run_id"] for row in rows] == ["run_0001"]
