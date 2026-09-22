"""Model-mc backend artifact layout: train under backend classifiers/, not shared/."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from methyl_validation.classification_metrics import (
    CLASSIFICATION_RESULTS_FILENAME,
    write_classification_results_csv,
)
from methyl_validation.project_gen import prepare_model_mc_backend_run_from_shared
from methyl_validation.trainer_api import _classifier_output_dir


def test_prepare_model_mc_backend_run_from_shared_routes_output_base(tmp_path: Path) -> None:
    shared_root = tmp_path / "model_mc" / "shared"
    backend_root = tmp_path / "model_mc" / "ecdf"
    shared_run = shared_root / "run_0001"
    backend_run = backend_root / "run_0001"
    shared_run.mkdir(parents=True)
    (shared_run / "train_control.csv").write_text("sample\nh1\n", encoding="utf-8")
    (shared_run / "detections").mkdir()
    (shared_run / "project.json").write_text(
        json.dumps({"output_base": str(shared_root), "project_name": "run_0001", "comparisons": []}),
        encoding="utf-8",
    )

    project_path = prepare_model_mc_backend_run_from_shared(
        shared_run,
        backend_run,
        backend_root=backend_root,
    )
    assert project_path == backend_run / "project.json"
    payload = json.loads(project_path.read_text(encoding="utf-8"))
    assert payload["output_base"] == str(backend_root)
    assert payload["project_name"] == "run_0001"
    assert (backend_run / "train_control.csv").is_file()
    assert (backend_run / "detections").is_symlink()


def test_prepare_model_mc_backend_run_links_frozen_bundle_support(tmp_path: Path) -> None:
    shared_run = tmp_path / "model_mc" / "shared" / "run_0001"
    backend_root = tmp_path / "model_mc" / "ecdf"
    backend_run = backend_root / "run_0001"
    production_bundle = tmp_path / "production" / "model_bundle"
    production_bundle.mkdir(parents=True)
    mapper_annotations = production_bundle / "mapper_dmp_annotations.csv"
    frozen_genes = production_bundle / "frozen_genes_production.csv"
    mapper_annotations.write_text("chromosome,position\n1,10\n", encoding="utf-8")
    frozen_genes.write_text("gene_name\nTP53\n", encoding="utf-8")
    shared_run.mkdir(parents=True)
    (shared_run / "project.json").write_text(
        json.dumps(
            {
                "output_base": str(shared_run.parent),
                "project_name": "run_0001",
                "actionConfig": {
                    "model_bundle": {
                        "mapper_annotation_csv": str(mapper_annotations),
                        "fixed_gene_panel": str(frozen_genes),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    prepare_model_mc_backend_run_from_shared(
        shared_run,
        backend_run,
        backend_root=backend_root,
    )

    linked_mapper = backend_run / "model_bundle" / mapper_annotations.name
    linked_genes = backend_run / "model_bundle" / frozen_genes.name
    assert linked_mapper.is_symlink()
    assert linked_mapper.resolve() == mapper_annotations.resolve()
    assert linked_genes.is_symlink()
    assert linked_genes.resolve() == frozen_genes.resolve()


def test_classifier_output_dir_uses_project_parent(tmp_path: Path) -> None:
    run_dir = tmp_path / "ecdf" / "run_0001"
    run_dir.mkdir(parents=True)
    project_json = run_dir / "project.json"
    project_json.write_text("{}", encoding="utf-8")
    predictor_out = run_dir / "predictors" / "all" / "PCa"
    predictor_out.mkdir(parents=True)
    out = _classifier_output_dir(project_json, predictor_out)
    assert out == run_dir / "classifiers"


def test_write_classification_results_csv_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "classifiers" / "all" / "PCa" / CLASSIFICATION_RESULTS_FILENAME
    write_classification_results_csv(
        path,
        sample_paths=["/data/s1", "/data/s2"],
        y_true=np.array([0, 1], dtype=int),
        y_pred=np.array([0, 1], dtype=int),
        probs=np.array([[0.9, 0.1], [0.2, 0.8]], dtype=float),
    )
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["sample"] == "s1"
    assert float(rows[1]["prob_class1"]) == 0.8
