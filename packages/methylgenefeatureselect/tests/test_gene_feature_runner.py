"""Unit tests for the structural gene-feature selection runner scaffold."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from methyl_gene_feature_select.core.runner import (
    GENE_FEATURE_SELECTION_JSON,
    GENE_FEATURES_CLASSIFIER_CSV,
    _feature_importance_column,
    build_structural_feature_catalog,
    gene_feature_outputs_exist,
    run_gene_feature_selection,
)


def _write_intersections(mapper_dir: Path, stem: str, genes: list[str]) -> None:
    mapper_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"gene_name": genes}).to_csv(
        mapper_dir / f"{stem}-intersections.csv", index=False
    )


def test_feature_importance_column_falls_back_to_gene_body() -> None:
    assert _feature_importance_column("promoter") == "feature_importance_promoter"
    assert _feature_importance_column("UNKNOWN") == "feature_importance_gene_body"
    assert _feature_importance_column(None) == "feature_importance_gene_body"


def test_build_structural_feature_catalog_drops_placeholder_genes(tmp_path: Path) -> None:
    mapper_dir = tmp_path / "mapper"
    _write_intersections(mapper_dir, "promoter", ["BRCA1", "unknown", "nan", "TP53"])

    catalog = build_structural_feature_catalog(mapper_dir)
    assert set(catalog["gene_name"]) == {"BRCA1", "TP53"}
    assert set(catalog["feature_type"]) == {"promoter"}


def test_build_structural_feature_catalog_empty_when_no_files(tmp_path: Path) -> None:
    catalog = build_structural_feature_catalog(tmp_path / "empty")
    assert list(catalog.columns) == ["gene_name", "feature_type"]
    assert catalog.empty


def test_run_gene_feature_selection_writes_outputs_and_audit(tmp_path: Path) -> None:
    mapper_dir = tmp_path / "mapper"
    _write_intersections(mapper_dir, "promoter", ["BRCA1", "TP53", "EGFR"])
    output_dir = tmp_path / "out"

    result = run_gene_feature_selection(mapper_dir=mapper_dir, output_dir=output_dir)
    assert result["status"] == "ok"
    assert (output_dir / GENE_FEATURES_CLASSIFIER_CSV).is_file()
    assert (output_dir / GENE_FEATURE_SELECTION_JSON).is_file()

    audit = json.loads((output_dir / GENE_FEATURE_SELECTION_JSON).read_text())
    assert audit["n_features"] == 3
    assert audit["selection_mode"] == "ranked_catalog"
    assert gene_feature_outputs_exist(output_dir)


def test_run_gene_feature_selection_respects_max_features(tmp_path: Path) -> None:
    mapper_dir = tmp_path / "mapper"
    _write_intersections(mapper_dir, "promoter", ["A", "B", "C", "D", "E"])
    output_dir = tmp_path / "out"

    result = run_gene_feature_selection(
        mapper_dir=mapper_dir, output_dir=output_dir, max_features=2
    )
    assert result["audit"]["n_features"] == 2
    written = pd.read_csv(output_dir / GENE_FEATURES_CLASSIFIER_CSV)
    assert len(written) == 2


def test_run_gene_feature_selection_skips_when_outputs_exist(tmp_path: Path) -> None:
    mapper_dir = tmp_path / "mapper"
    _write_intersections(mapper_dir, "promoter", ["A", "B"])
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / GENE_FEATURES_CLASSIFIER_CSV).write_text("gene_name,feature_type\n")
    (output_dir / GENE_FEATURE_SELECTION_JSON).write_text(json.dumps({"n_features": 0}))

    result = run_gene_feature_selection(mapper_dir=mapper_dir, output_dir=output_dir)
    assert result["status"] == "skipped"


def test_run_gene_feature_selection_raises_when_no_features(tmp_path: Path) -> None:
    mapper_dir = tmp_path / "mapper"
    mapper_dir.mkdir()
    with pytest.raises(ValueError, match="No structural features"):
        run_gene_feature_selection(mapper_dir=mapper_dir, output_dir=tmp_path / "out")
