from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
from typing import Optional

import numpy as np
import pandas as pd
import pytest

from methyl_validation import model_bundle, tabular_backend


class _StubProject:
    def __init__(self, detection_dir: Path):
        self.project_name = "stubproj"
        self._det = detection_dir

    def get_comparisons(self):
        return [
            SimpleNamespace(
                control_group="healthy",
                disease_group="pca1",
                comparison_label="healthy_vs_pca1",
            )
        ]

    def get_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        assert control_group == "healthy"
        assert disease_group == "pca1"
        return str(self._det)

    def resolve_detection_output_dir(self, control_group: str, disease_group: str) -> str:
        return self.get_detection_output_dir(control_group, disease_group)

    def get_derived_paths(self):
        return SimpleNamespace(detection_dir=str(self._det))

    def get_resolved_groups(self):
        return [
            ("healthy", ["/tmp/S1", "/tmp/S2"]),
            ("pca1", ["/tmp/S3", "/tmp/S4"]),
        ]


class _StubProjectWithMapper(_StubProject):
    def __init__(
        self,
        detection_dir: Path,
        mapper_dir: Path,
        *,
        model_bundle_cfg: dict | None = None,
        mapper_cfg: dict | None = None,
    ):
        super().__init__(detection_dir)
        self._mapper = mapper_dir
        self._model_bundle_cfg = dict(model_bundle_cfg or {})
        self._mapper_cfg = dict(mapper_cfg or {})

    def get_mapper_output_dir(self, control_group: str, disease_group: str) -> str:
        assert control_group == "healthy"
        assert disease_group == "pca1"
        return str(self._mapper)


class _StubProjectWithModelBundleConfig(_StubProject):
    def __init__(
        self,
        detection_dir: Path,
        mapper_annotation_csv: Optional[Path] = None,
        fixed_gene_features_csv: Optional[Path] = None,
        fixed_gene_panel_csv: Optional[Path] = None,
    ):
        super().__init__(detection_dir)
        self._model_bundle_cfg: dict = {}
        if mapper_annotation_csv is not None:
            self._model_bundle_cfg["mapper_annotation_csv"] = str(mapper_annotation_csv)
        if fixed_gene_features_csv is not None:
            self._model_bundle_cfg["fixed_gene_features"] = str(fixed_gene_features_csv)
        if fixed_gene_panel_csv is not None:
            self._model_bundle_cfg["fixed_gene_panel"] = str(fixed_gene_panel_csv)


def _resolve_for_project_stub(action_key: str, project, **_kwargs):
    if action_key == "model_bundle" and hasattr(project, "_model_bundle_cfg"):
        return dict(project._model_bundle_cfg)
    if action_key == "mapper" and hasattr(project, "_mapper_cfg"):
        return dict(project._mapper_cfg)
    return {}


def _patch_model_bundle_resolver(monkeypatch) -> None:
    monkeypatch.setattr(model_bundle, "resolve_for_project", _resolve_for_project_stub)


@pytest.fixture(autouse=True)
def _autouse_resolve_for_project_stub(monkeypatch):
    _patch_model_bundle_resolver(monkeypatch)


def test_build_model_feature_bundle_and_load(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    df = pd.DataFrame(
        {
            "chromosome": ["1", "1", "1"],
            "position": [100, 120, 200],
            "context": ["CG", "CG", "CHG"],
            "effect_size": [0.7, 0.4, 0.9],
            "weight": [0.8, 0.3, 1.0],
            "gene_name": ["TP53", "TP53", "MYC"],
            "dmr_region": ["R1", "R1", "R2"],
        }
    )
    dmp_csv = det / "dmps-1-classifier.csv"
    df.to_csv(dmp_csv, index=False)

    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))

    manifest_path = model_bundle.build_model_feature_bundle(
        project_json=tmp_path / "project.json",
        output_dir=tmp_path / "bundle",
    )
    assert manifest_path.is_file()
    assert (tmp_path / "bundle" / "detection_model_bundle.json").is_file()

    out_df = model_bundle.load_bundle_dmp_index(tmp_path / "bundle" / "model_feature_bundle.h5")
    assert len(out_df) == 3
    assert set(out_df.columns) >= {
        "chromosome",
        "position",
        "context",
        "weight",
        "comparison_label",
        "gene_name",
        "dmr_region",
    }


def test_build_mapper_annotation_cache_deterministic_collapse(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    intersections = pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20", "1:100:CG:eff=0.20", "1:200:CG:eff=0.10"],
            "feature_chrom": ["chr1", "chr1", "chr1"],
            "gene_name": ["GENE_WEAK", "GENE_STRONG", "GENE2"],
            "feature_type": ["intron", "promoter", "exon"],
            "region_weight": [0.7, 2.0, 1.5],
            "combined_weight": [0.5, 1.8, 1.1],
            "effect_size": [0.20, 0.20, 0.10],
            "context": ["CG", "CG", "CG"],
        }
    )
    intersections.to_csv(mapper / "chr1-intersections.csv", index=False)
    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(det, mapper),
    )

    out_csv = tmp_path / "bundle" / "mapper_dmp_annotations.csv"
    cache_info = model_bundle.build_mapper_annotation_cache(
        project_json=tmp_path / "project.json",
        output_csv=out_csv,
    )
    assert out_csv.is_file()
    assert cache_info["rows"] == 2

    out_df = pd.read_csv(out_csv)
    row_100 = out_df[(out_df["chromosome"].astype(str) == "1") & (out_df["position"] == 100)]
    assert len(row_100) == 1
    assert row_100.iloc[0]["gene_name"] == "GENE_STRONG"
    assert row_100.iloc[0]["feature_type"] == "promoter"
    assert float(row_100.iloc[0]["region_weight"]) == pytest.approx(2.0)
    assert cache_info["mapper_annotation_collapse_mode"] == "priority"


def test_build_mapper_annotation_cache_priority_intron_beats_gene_body(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    intersections = pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20", "1:100:CG:eff=0.20"],
            "feature_chrom": ["chr1", "chr1"],
            "gene_name": ["G1", "G1"],
            "feature_type": ["intron", "gene_body"],
            "region_weight": [0.7, 1.0],
            "combined_weight": [0.5, 1.8],
            "effect_size": [0.20, 0.20],
            "context": ["CG", "CG"],
        }
    )
    intersections.to_csv(mapper / "chr1-intersections.csv", index=False)
    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(det, mapper),
    )
    out_csv = tmp_path / "bundle" / "mapper_dmp_annotations.csv"
    model_bundle.build_mapper_annotation_cache(
        project_json=tmp_path / "project.json",
        output_csv=out_csv,
        collapse_mode="priority",
    )
    out_df = pd.read_csv(out_csv)
    row_100 = out_df[(out_df["chromosome"].astype(str) == "1") & (out_df["position"] == 100)]
    assert len(row_100) == 1
    assert row_100.iloc[0]["feature_type"] == "intron"


def test_build_mapper_annotation_cache_weight_mode_preserves_gene_body(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    intersections = pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20", "1:100:CG:eff=0.20"],
            "feature_chrom": ["chr1", "chr1"],
            "gene_name": ["G1", "G1"],
            "feature_type": ["intron", "gene_body"],
            "region_weight": [0.7, 1.0],
            "combined_weight": [0.5, 1.8],
            "effect_size": [0.20, 0.20],
            "context": ["CG", "CG"],
        }
    )
    intersections.to_csv(mapper / "chr1-intersections.csv", index=False)
    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(det, mapper),
    )
    out_csv = tmp_path / "bundle" / "mapper_dmp_annotations.csv"
    model_bundle.build_mapper_annotation_cache(
        project_json=tmp_path / "project.json",
        output_csv=out_csv,
        collapse_mode="weight",
    )
    out_df = pd.read_csv(out_csv)
    row_100 = out_df[(out_df["chromosome"].astype(str) == "1") & (out_df["position"] == 100)]
    assert len(row_100) == 1
    assert row_100.iloc[0]["feature_type"] == "gene_body"


def test_build_mapper_annotation_cache_unknown_fallback_to_gene_body(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    intersections = pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20"],
            "feature_chrom": ["chr1"],
            "gene_name": ["G1"],
            "feature_type": ["unknown"],
            "region_weight": [1.0],
            "combined_weight": [0.5],
            "effect_size": [0.20],
            "context": ["CG"],
        }
    )
    intersections.to_csv(mapper / "chr1-intersections.csv", index=False)
    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(det, mapper),
    )
    out_csv = tmp_path / "bundle" / "mapper_dmp_annotations.csv"
    model_bundle.build_mapper_annotation_cache(
        project_json=tmp_path / "project.json",
        output_csv=out_csv,
        unknown_fallback="gene_body",
    )
    out_df = pd.read_csv(out_csv)
    row_100 = out_df[(out_df["chromosome"].astype(str) == "1") & (out_df["position"] == 100)]
    assert len(row_100) == 1
    assert row_100.iloc[0]["feature_type"] == "gene_body"
    assert float(row_100.iloc[0]["region_weight"]) == pytest.approx(1.0)


def test_build_mapper_annotation_cache_joins_default_mapper_gene_columns(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20"],
            "feature_chrom": ["chr1"],
            "gene_name": ["GENE_A"],
            "feature_type": ["promoter"],
            "region_weight": [2.0],
            "combined_weight": [1.8],
            "effect_size": [0.20],
            "context": ["CG"],
        }
    ).to_csv(mapper / "chr1-intersections.csv", index=False)
    pd.DataFrame(
        {
            "gene_name": ["GENE_A"],
            "gene_importance": [3.2],
            "gene_effect_signed_wsum": [2.3],
            "gene_direction": [1.0],
            "gene_effect_abs_wsum": [4.7],
            "gene_support_n": [3],
            "gene_effect_compound": [1.7],
            "feature_importance_promoter": [1.2],
        }
    ).to_csv(mapper / "all-gene_name-combined.csv", index=False)

    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(det, mapper),
    )
    out_csv = tmp_path / "bundle" / "mapper_dmp_annotations.csv"
    cache_info = model_bundle.build_mapper_annotation_cache(
        project_json=tmp_path / "project.json",
        output_csv=out_csv,
    )

    out_df = pd.read_csv(out_csv)
    assert "gene_importance" in out_df.columns
    assert "gene_effect_signed_wsum" in out_df.columns
    assert "gene_direction" in out_df.columns
    assert "gene_effect_abs_wsum" in out_df.columns
    assert "gene_support_n" in out_df.columns
    assert "gene_effect_compound" in out_df.columns
    assert float(out_df.iloc[0]["gene_importance"]) == pytest.approx(3.2)
    assert float(out_df.iloc[0]["gene_effect_signed_wsum"]) == pytest.approx(2.3)
    assert float(out_df.iloc[0]["gene_direction"]) == pytest.approx(1.0)
    assert float(out_df.iloc[0]["gene_effect_abs_wsum"]) == pytest.approx(4.7)
    assert float(out_df.iloc[0]["gene_support_n"]) == pytest.approx(3.0)
    assert float(out_df.iloc[0]["gene_effect_compound"]) == pytest.approx(1.7)
    assert cache_info["mapper_gene_columns_requested"] == [
        "gene_importance",
        "gene_effect_signed_wsum",
        "gene_direction",
        "gene_effect_abs_wsum",
        "gene_support_n",
        "gene_effect_compound",
        "feature_importance_promoter",
        "feature_importance_exon",
        "feature_importance_intron",
        "feature_importance_gene_body",
        "feature_importance_terminator",
    ]
    assert cache_info["mapper_gene_columns_effective"] == [
        "gene_importance",
        "gene_effect_signed_wsum",
        "gene_direction",
        "gene_effect_abs_wsum",
        "gene_support_n",
        "gene_effect_compound",
        "feature_importance_promoter",
    ]


def test_build_mapper_annotation_cache_empty_mapper_gene_columns_disables_join(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20"],
            "feature_chrom": ["chr1"],
            "gene_name": ["GENE_A"],
            "feature_type": ["promoter"],
            "region_weight": [2.0],
            "combined_weight": [1.8],
            "effect_size": [0.20],
            "context": ["CG"],
        }
    ).to_csv(mapper / "chr1-intersections.csv", index=False)
    pd.DataFrame(
        {
            "gene_name": ["GENE_A"],
            "gene_score": [9.5],
            "mean_effect_size": [0.42],
        }
    ).to_csv(mapper / "all-gene_name-combined.csv", index=False)

    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(
            det,
            mapper,
            model_bundle_cfg={"mapper_gene_columns": []},
        ),
    )
    out_csv = tmp_path / "bundle" / "mapper_dmp_annotations.csv"
    cache_info = model_bundle.build_mapper_annotation_cache(
        project_json=tmp_path / "project.json",
        output_csv=out_csv,
    )

    out_df = pd.read_csv(out_csv)
    assert "gene_score" not in out_df.columns
    assert "mean_effect_size" not in out_df.columns
    assert cache_info["mapper_gene_columns_requested"] == []
    assert cache_info["mapper_gene_columns_effective"] == []


def test_build_frozen_gene_panel_writes_gene_and_feature_outputs(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    pd.DataFrame(
        {
            "gene_name": ["GENE_A", "GENE_B"],
            "gene_id": ["ENSGA", "ENSGB"],
            "gene_importance": [3.0, 0.8],
            "unique_dmps": [3, 1],
            "gene_support_n": [3, 1],
            "gene_effect_abs_wsum": [4.5, 0.9],
            "mean_effect_size": [0.4, 0.3],
            "hits_promoter": [2, 0],
            "hits_exon": [1, 1],
            "feature_effect_compound_promoter": [0.7, 0.1],
            "feature_effect_compound_exon": [0.4, 0.2],
        }
    ).to_csv(mapper / "all-gene_name-combined.csv", index=False)
    pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20", "1:110:CG:eff=0.22", "1:300:CG:eff=0.10"],
            "feature_chrom": ["chr1", "chr1", "chr1"],
            "gene_name": ["GENE_A", "GENE_A", "GENE_B"],
            "feature_type": ["promoter", "promoter", "exon"],
            "feature_start": [90, 90, 290],
            "feature_end": [130, 130, 330],
        }
    ).to_csv(mapper / "chr1-intersections.csv", index=False)

    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(det, mapper),
    )

    out = model_bundle.build_frozen_gene_panel(
        project_json=tmp_path / "project.json",
        output_dir=tmp_path / "bundle",
        min_dmps_per_feature=2,
        gene_importance_min=1.0,
        top_genes=1,
    )
    genes_df = pd.read_csv(out["gene_panel_path"])
    feats_df = pd.read_csv(out["gene_features_path"])
    assert out["genes_rows"] == 1
    assert out["features_rows"] == 1
    assert genes_df.iloc[0]["gene_name"] == "GENE_A"
    assert feats_df.iloc[0]["gene_name"] == "GENE_A"
    assert feats_df.iloc[0]["feature_type"] == "promoter"
    assert int(feats_df.iloc[0]["n_dmps_in_feature"]) == 2
    assert float(feats_df.iloc[0]["feature_effect_compound"]) == pytest.approx(0.7)


def test_build_frozen_gene_panel_collapses_overlapping_isoform_intervals(
    tmp_path: Path, monkeypatch
):
    """Overlapping isoform gene_body spans become one row with union hull + unique DMPs."""
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    pd.DataFrame(
        {
            "gene_name": ["DLGAP2"],
            "gene_id": ["ENSG_DLGAP2"],
            "gene_importance": [2.5],
            "unique_dmps": [3],
            "gene_support_n": [3],
            "gene_effect_abs_wsum": [4.0],
            "feature_effect_compound_gene_body": [0.9],
        }
    ).to_csv(mapper / "all-gene_name-combined.csv", index=False)
    # Nested/overlapping isoform intervals sharing some DMPs (unique count = 3, not 2+3).
    pd.DataFrame(
        {
            "dmp_name": [
                "8:100:CG:eff=0.20",
                "8:200:CG:eff=0.22",
                "8:100:CG:eff=0.20",
                "8:200:CG:eff=0.22",
                "8:300:CG:eff=0.18",
            ],
            "feature_chrom": ["chr8"] * 5,
            "gene_name": ["DLGAP2"] * 5,
            "feature_type": ["gene_body"] * 5,
            "feature_start": [50, 50, 80, 80, 80],
            "feature_end": [250, 250, 400, 400, 400],
        }
    ).to_csv(mapper / "chr8-intersections.csv", index=False)

    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(det, mapper),
    )
    out = model_bundle.build_frozen_gene_panel(
        project_json=tmp_path / "project.json",
        output_dir=tmp_path / "bundle",
        min_dmps_per_feature=1,
    )
    feats_df = pd.read_csv(out["gene_features_path"])
    gene_body = feats_df[feats_df["feature_type"] == "gene_body"]
    assert len(gene_body) == 1
    row = gene_body.iloc[0]
    assert row["gene_name"] == "DLGAP2"
    assert int(row["feature_start"]) == 50
    assert int(row["feature_end"]) == 400
    assert int(row["n_dmps_in_feature"]) == 3
    assert float(row["feature_effect_compound"]) == pytest.approx(0.9)


def test_build_frozen_gene_panel_one_row_for_disjoint_exons_under_option_a(
    tmp_path: Path, monkeypatch
):
    """Option A: disjoint exons for the same gene still collapse to one hull row."""
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    pd.DataFrame(
        {
            "gene_name": ["PTPRN2"],
            "gene_id": ["ENSG_PTPRN2"],
            "gene_importance": [2.0],
            "unique_dmps": [2],
            "gene_support_n": [2],
            "feature_effect_compound_exon": [0.5],
        }
    ).to_csv(mapper / "all-gene_name-combined.csv", index=False)
    pd.DataFrame(
        {
            "dmp_name": ["7:100:CG:eff=0.20", "7:5000:CG:eff=0.22"],
            "feature_chrom": ["chr7", "chr7"],
            "gene_name": ["PTPRN2", "PTPRN2"],
            "feature_type": ["exon", "exon"],
            "feature_start": [90, 4900],
            "feature_end": [150, 5100],
        }
    ).to_csv(mapper / "chr7-intersections.csv", index=False)

    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(det, mapper),
    )
    out = model_bundle.build_frozen_gene_panel(
        project_json=tmp_path / "project.json",
        output_dir=tmp_path / "bundle",
        min_dmps_per_feature=1,
    )
    feats_df = pd.read_csv(out["gene_features_path"])
    exon = feats_df[feats_df["feature_type"] == "exon"]
    assert len(exon) == 1
    row = exon.iloc[0]
    assert row["gene_name"] == "PTPRN2"
    assert int(row["feature_start"]) == 90
    assert int(row["feature_end"]) == 5100
    assert int(row["n_dmps_in_feature"]) == 2


def test_resolve_fixed_gene_features_panel_rebuilds_missing_compounds(tmp_path: Path, monkeypatch):
    mapper = tmp_path / "mapper" / "healthy" / "pca1"
    mapper.mkdir(parents=True)
    pd.DataFrame(
        {
            "gene_name": ["GENE_A"],
            "gene_id": ["ENSGA"],
            "gene_importance": [3.0],
            "unique_dmps": [3],
            "gene_support_n": [3],
            "feature_effect_compound_promoter": [0.7],
        }
    ).to_csv(mapper / "all-gene_name-combined.csv", index=False)
    pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20", "1:110:CG:eff=0.22"],
            "feature_chrom": ["chr1", "chr1"],
            "gene_name": ["GENE_A", "GENE_A"],
            "feature_type": ["promoter", "promoter"],
            "feature_start": [90, 90],
            "feature_end": [130, 130],
        }
    ).to_csv(mapper / "chr1-intersections.csv", index=False)

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True)
    stale = bundle_dir / "frozen_gene_features.csv"
    pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_pca1"],
            "gene_name": ["GENE_A"],
            "chromosome": ["1"],
            "feature_type": ["promoter"],
            "feature_start": [90],
            "feature_end": [130],
            "n_dmps_in_feature": [2],
            "feature_effect_compound": [0.0],
        }
    ).to_csv(stale, index=False)

    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithMapper(det, mapper),
    )

    out_df, out_path = model_bundle.resolve_fixed_gene_features_panel(
        project_json=tmp_path / "project.json",
        bundle_dir=bundle_dir,
        auto_rebuild=True,
    )
    assert out_path == stale
    assert float(out_df["feature_effect_compound"].iloc[0]) == pytest.approx(0.7)


def test_normalize_mapper_intersections_uses_dmp_fallback_for_nan_keys(tmp_path: Path):
    intersections = pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20"],
            "chromosome": [np.nan],
            "context": [np.nan],
            "gene_name": ["GENE1"],
            "feature_type": ["promoter"],
        }
    )
    out = model_bundle._normalize_mapper_intersections(
        intersections,
        comparison_label="healthy_vs_pca1",
        source_csv=tmp_path / "chr1-intersections.csv",
    )
    assert len(out) == 1
    assert out.iloc[0]["chromosome"] == "1"
    assert int(out.iloc[0]["position"]) == 100
    assert out.iloc[0]["context"] == "CG"


def test_normalize_mapper_intersections_does_not_use_feature_start_as_dmp_position(tmp_path: Path):
    intersections = pd.DataFrame(
        {
            "dmp_name": ["1:100:CG:eff=0.20"],
            "feature_start": [9999],
            "feature_chrom": ["chr1"],
            "context": ["CG"],
            "gene_name": ["GENE1"],
            "feature_type": ["promoter"],
        }
    )
    out = model_bundle._normalize_mapper_intersections(
        intersections,
        comparison_label="healthy_vs_pca1",
        source_csv=tmp_path / "chr1-intersections.csv",
    )
    assert len(out) == 1
    assert int(out.iloc[0]["position"]) == 100


def test_build_model_feature_bundle_merges_mapper_annotations(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.5],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    ann_csv = tmp_path / "mapper_dmp_annotations.csv"
    pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_pca1"],
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "gene_name": ["TP53"],
            "feature_type": ["promoter"],
            "region_weight": [2.0],
            "mapper_source_csv": ["/tmp/mapper.csv"],
        }
    ).to_csv(ann_csv, index=False)

    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    model_bundle.build_model_feature_bundle(
        project_json=tmp_path / "project.json",
        output_dir=tmp_path / "bundle",
        feature_family_set="gene_scored",
        require_mapper_annotations=True,
        mapper_annotation_csv=ann_csv,
    )
    out_df = model_bundle.load_bundle_dmp_index(tmp_path / "bundle" / "model_feature_bundle.h5")
    assert out_df.iloc[0]["gene_name"] == "TP53"
    assert out_df.iloc[0]["feature_type"] == "promoter"
    assert float(out_df.iloc[0]["region_weight"]) == pytest.approx(2.0)


def test_build_model_feature_bundle_preserves_mapper_gene_columns(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.5],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    ann_csv = tmp_path / "mapper_dmp_annotations.csv"
    pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_pca1"],
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "gene_name": ["TP53"],
            "feature_type": ["promoter"],
            "region_weight": [2.0],
            "mapper_source_csv": ["/tmp/mapper.csv"],
            "gene_score": [8.8],
            "mean_effect_size": [0.51],
            "gene_effect_compound": [1.3],
            "gene_feature_effect_compound": [1.1],
        }
    ).to_csv(ann_csv, index=False)

    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    model_bundle.build_model_feature_bundle(
        project_json=tmp_path / "project.json",
        output_dir=tmp_path / "bundle",
        feature_family_set="gene_scored",
        require_mapper_annotations=True,
        mapper_annotation_csv=ann_csv,
    )
    out_df = model_bundle.load_bundle_dmp_index(tmp_path / "bundle" / "model_feature_bundle.h5")
    assert "gene_score" in out_df.columns
    assert "mean_effect_size" in out_df.columns
    assert "gene_effect_compound" in out_df.columns
    assert "gene_feature_effect_compound" in out_df.columns
    assert float(out_df.iloc[0]["gene_score"]) == pytest.approx(8.8)


def test_build_model_feature_bundle_persists_fixed_gene_feature_ranges(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.5],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    fixed_features_csv = tmp_path / "frozen_gene_features.csv"
    pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_pca1"],
            "gene_name": ["TP53"],
            "chromosome": ["1"],
            "feature_type": ["promoter"],
            "feature_start": [90],
            "feature_end": [150],
            "n_dmps_in_feature": [2],
            "feature_effect_compound": [0.8],
            "gene_importance": [3.4],
        }
    ).to_csv(fixed_features_csv, index=False)

    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithModelBundleConfig(
            det,
            mapper_annotation_csv=None,
            fixed_gene_features_csv=fixed_features_csv,
        ),
    )
    model_bundle.build_model_feature_bundle(
        project_json=tmp_path / "project.json",
        output_dir=tmp_path / "bundle",
    )
    ranges_df = model_bundle.load_bundle_gene_feature_ranges(tmp_path / "bundle" / "model_feature_bundle.h5")
    assert len(ranges_df) == 1
    assert ranges_df.iloc[0]["gene_name"] == "TP53"
    assert int(ranges_df.iloc[0]["feature_start"]) == 90
    assert int(ranges_df.iloc[0]["feature_end"]) == 150


def test_build_model_feature_bundle_uses_project_mapper_annotation_pointer(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [101],
            "context": ["CG"],
            "effect_size": [0.6],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    ann_csv = tmp_path / "cached_mapper_annotations.csv"
    pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_pca1"],
            "chromosome": ["1"],
            "position": [101],
            "context": ["CG"],
            "gene_name": ["BRCA1"],
            "feature_type": ["exon"],
            "region_weight": [1.5],
            "mapper_source_csv": ["/tmp/mapper.csv"],
        }
    ).to_csv(ann_csv, index=False)

    monkeypatch.setattr(
        model_bundle,
        "load_project",
        lambda _p: _StubProjectWithModelBundleConfig(det, ann_csv),
    )
    model_bundle.build_model_feature_bundle(
        project_json=tmp_path / "project.json",
        output_dir=tmp_path / "bundle",
        feature_family_set="gene_scored",
        require_mapper_annotations=True,
    )
    out_df = model_bundle.load_bundle_dmp_index(tmp_path / "bundle" / "model_feature_bundle.h5")
    assert out_df.iloc[0]["gene_name"] == "BRCA1"


def test_build_model_feature_bundle_requires_mapper_annotations_for_non_dmp(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.7],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))

    with pytest.raises(FileNotFoundError, match="Mapper annotation cache is required"):
        model_bundle.build_model_feature_bundle(
            project_json=tmp_path / "project.json",
            output_dir=tmp_path / "bundle",
            feature_family_set="gene_scored",
            require_mapper_annotations=True,
        )


def test_build_model_feature_bundle_canonicalizes_weight_to_effect_size(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1", "1"],
            "position": [100, 120, 200],
            "context": ["CG", "CG", "CG"],
            "effect_size": [0.9, 0.2, 0.6],
            # Deliberately conflicting to verify effect_size canonicalization.
            "weight": [0.1, 5.0, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))

    model_bundle.build_model_feature_bundle(tmp_path / "project.json", tmp_path / "bundle")
    out_df = model_bundle.load_bundle_dmp_index(tmp_path / "bundle" / "model_feature_bundle.h5")
    assert np.allclose(out_df["weight"].to_numpy(dtype=float), out_df["effect_size"].to_numpy(dtype=float))
    assert out_df.iloc[0]["position"] == 100


def test_build_model_feature_bundle_requires_effect_size(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "weight": [0.8, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))

    with pytest.raises(ValueError, match="effect_size"):
        model_bundle.build_model_feature_bundle(tmp_path / "project.json", tmp_path / "bundle")


def test_build_model_feature_bundle_loads_all_chromosome_csvs(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.7],
            "weight": [0.8],
        }
    ).to_csv(det / "dmps-1.csv", index=False)
    pd.DataFrame(
        {
            "chromosome": ["2"],
            "position": [200],
            "context": ["CG"],
            "effect_size": [0.9],
            "weight": [1.0],
        }
    ).to_csv(det / "dmps-2.csv", index=False)

    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    manifest_path = model_bundle.build_model_feature_bundle(tmp_path / "project.json", tmp_path / "bundle")
    out_df = model_bundle.load_bundle_dmp_index(tmp_path / "bundle" / "model_feature_bundle.h5")
    assert len(out_df) == 2
    assert set(out_df["chromosome"].astype(str).tolist()) == {"1", "2"}
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    cmp0 = manifest["comparisons"][0]
    assert len(cmp0["classifier_dmps_csvs"]) == 2


def test_tabular_backend_train_and_predict(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1", "1"],
            "position": [100, 120, 200],
            "context": ["CG", "CG", "CG"],
            "effect_size": [0.7, 0.4, 0.9],
            "weight": [0.8, 0.3, 1.0],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)

    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)

    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: _StubProject(det))

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        n = len(sample_paths)
        X = np.zeros((n, len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            # Control-like samples low methylation, disease-like high methylation.
            base = 0.1 if Path(str(p)).name in {"S1", "S2"} else 0.9
            X[i, :] = base
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(
        tabular_backend.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract,
    )

    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        model_type="random_forest",
        max_dmps=100,
        save_test_dataset=False,
    )
    assert (model_dir / "tabular-model.joblib").is_file()
    assert (model_dir / "tabular-model-metadata.json").is_file()
    assert (model_dir / "training_metrics.json").is_file()
    assert (model_dir / "tabular_methods" / "00_random_forest" / "training_metrics.json").is_file()
    assert not (model_dir / "tabular_methods" / "00_random_forest" / "selection_eval").exists()
    with open(model_dir / "training_metrics.json", encoding="utf-8") as f:
        training_metrics = json.load(f)
    assert "balanced_accuracy" in training_metrics
    assert int(training_metrics.get("n_samples", 0)) == 4
    assert int(training_metrics.get("n_classes", 0)) == 2
    assert training_metrics.get("method") == "random_forest"
    assert int(training_metrics.get("method_index", -1)) == 0
    with open(model_dir / "tabular-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta.get("selected_tabular_method_index") == 0
    assert meta.get("tabular_method_selection_score") is None

    predictor_cfg = SimpleNamespace(
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    metrics = tabular_backend.predict_tabular_model_from_project(
        project_json=tmp_path / "project.json",
        model_dir=model_dir,
        output_dir=tmp_path / "predict",
    )
    assert "balanced_accuracy" in metrics
    assert (tmp_path / "predict" / "predictions.csv").is_file()


def test_tabular_resolve_eval_prefers_holdout_binary_paths(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    stub = _StubProject(det)
    predictor_cfg = SimpleNamespace(
        test_group_paths=None,
        holdout_group_paths=None,
        train_group_paths=None,
        test_control_paths=[],
        test_disease_paths=[],
        train_control_paths=["/tmp/TR_C1", "/tmp/TR_C2"],
        train_disease_paths=["/tmp/TR_D1", "/tmp/TR_D2"],
        holdout_control_paths=["/tmp/HO_C1"],
        holdout_disease_paths=["/tmp/HO_D1", "/tmp/HO_D2"],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: stub)
    samples, y_true = tabular_backend._resolve_eval_paths_and_labels(
        project_json=tmp_path / "project.json",
        class_names=["healthy", "pca1"],
    )
    assert samples == ["/tmp/HO_C1", "/tmp/HO_D1", "/tmp/HO_D2"]
    assert y_true.tolist() == [0, 1, 1]


def test_tabular_resolve_eval_prefers_holdout_group_paths_for_binary(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    stub = _StubProject(det)
    predictor_cfg = SimpleNamespace(
        test_group_paths=None,
        holdout_group_paths=[
            {"label": "healthy", "class_index": 0, "paths": ["/tmp/HO_C1"]},
            {"label": "pca1", "class_index": 1, "paths": ["/tmp/HO_D1", "/tmp/HO_D2"]},
        ],
        train_group_paths=[
            {"label": "healthy", "class_index": 0, "paths": ["/tmp/TR_C1"]},
            {"label": "pca1", "class_index": 1, "paths": ["/tmp/TR_D1"]},
        ],
        test_control_paths=[],
        test_disease_paths=[],
        train_control_paths=[],
        train_disease_paths=[],
        holdout_control_paths=[],
        holdout_disease_paths=[],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: stub)
    samples, y_true = tabular_backend._resolve_eval_paths_and_labels(
        project_json=tmp_path / "project.json",
        class_names=["healthy", "pca1"],
    )
    assert samples == ["/tmp/HO_C1", "/tmp/HO_D1", "/tmp/HO_D2"]
    assert y_true.tolist() == [0, 1, 1]


def test_tabular_covariate_preprocessor_categorical(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "effect_size": [0.7, 0.4],
            "weight": [0.8, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)

    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: _StubProject(det))

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.full((len(sample_paths), len(positions)), 0.5, dtype=np.float32)
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)

    cov_csv = tmp_path / "cov.csv"
    pd.DataFrame(
        {
            "sample_id": ["S1", "S2", "S3", "S4"],
            "age": [55, 60, 68, 71],
            "ethnicity": ["A", "B", "A", "C"],
        }
    ).to_csv(cov_csv, index=False)

    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        model_type="logistic_regression",
        covariates_path=str(cov_csv),
        covariates_strict_join=True,
        save_test_dataset=False,
    )
    assert (model_dir / "covariate-preprocessor.json").is_file()
    with open(model_dir / "tabular-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert bool(meta["covariate_preprocessing"]["used"]) is True


def test_tabular_drops_missing_covariate_samples(tmp_path: Path, monkeypatch, capsys):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "effect_size": [0.7, 0.4],
            "weight": [0.8, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)

    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: _StubProject(det))

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.full((len(sample_paths), len(positions)), 0.5, dtype=np.float32)
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)

    cov_csv = tmp_path / "cov.csv"
    pd.DataFrame(
        {
            "sample_id": ["S1", "S2", "S3"],
            "age": [55, 60, 68],
            "ethnicity": ["A", "B", "A"],
        }
    ).to_csv(cov_csv, index=False)

    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        model_type="logistic_regression",
        covariates_path=str(cov_csv),
        covariates_strict_join=True,
        covariates_missing_samples="drop",
        save_test_dataset=False,
    )
    captured = capsys.readouterr()
    assert "Dropping 1 sample" in captured.err
    with open(model_dir / "tabular-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["covariate_preprocessing"]["n_dropped"] == 1
    assert meta["covariate_preprocessing"]["dropped_sample_ids"] == ["S4"]
    train_metrics = json.loads(
        (model_dir / "tabular_methods" / "00_logistic_regression" / "training_metrics.json").read_text(
            encoding="utf-8"
        )
    )
    assert train_metrics["n_train_samples"] == 3


def test_tabular_covariate_preprocessor_ordinal(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "effect_size": [0.7, 0.4],
            "weight": [0.8, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)

    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: _StubProject(det))

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.full((len(sample_paths), len(positions)), 0.5, dtype=np.float32)
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)

    cov_train_csv = tmp_path / "cov-train.csv"
    pd.DataFrame(
        {
            "sample_id": ["S1", "S2", "S3", "S4"],
            "age": [55, 60, 68, 71],
            "risk_band": ["low", "medium", "high", "high"],
            "ethnicity": ["A", "B", "A", "C"],
        }
    ).to_csv(cov_train_csv, index=False)

    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        model_type="logistic_regression",
        covariates_path=str(cov_train_csv),
        covariates_strict_join=True,
        covariate_numeric_columns=["age"],
        covariate_ordinal_columns=["risk_band"],
        covariate_ordinal_maps={"risk_band": {"low": 1, "medium": 2, "high": 3}},
        covariate_categorical_columns=["ethnicity"],
        save_test_dataset=False,
    )

    with open(model_dir / "covariate-preprocessor.json", encoding="utf-8") as f:
        pre = json.load(f)
    assert pre["ordinal_columns"] == ["risk_band"]
    assert pre["ordinal_maps"]["risk_band"]["high"] == 3.0

    predictor_cfg = SimpleNamespace(
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)

    cov_predict_csv = tmp_path / "cov-predict.csv"
    pd.DataFrame(
        {
            "sample_id": ["S1", "S2", "S3", "S4"],
            "age": [56, 61, 69, 73],
            "risk_band": ["low", "very_high", "high", "medium"],
            "ethnicity": ["A", "B", "A", "C"],
        }
    ).to_csv(cov_predict_csv, index=False)
    metrics = tabular_backend.predict_tabular_model_from_project(
        project_json=tmp_path / "project.json",
        model_dir=model_dir,
        output_dir=tmp_path / "predict",
        covariates_path=str(cov_predict_csv),
        covariates_strict_join=True,
    )
    assert int(metrics["covariate_preprocessing"]["unknown_ordinal_values_mapped"]) >= 1


def test_tabular_resolve_eval_paths_multiclass_falls_back_from_binary_predictor(tmp_path: Path, monkeypatch):
    class _StubProjectMulti:
        def get_resolved_groups(self):
            return [
                ("healthy", ["/tmp/S1", "/tmp/S2"]),
                ("pca1", ["/tmp/S3", "/tmp/S4"]),
                ("pca2", ["/tmp/S5", "/tmp/S6"]),
            ]

    stub = _StubProjectMulti()
    predictor_cfg = SimpleNamespace(
        test_group_paths=None,
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: stub)
    samples, y_true = tabular_backend._resolve_eval_paths_and_labels(
        project_json=tmp_path / "project.json",
        class_names=["healthy", "pca1", "pca2"],
    )
    assert len(samples) == 6
    assert set(np.unique(y_true).tolist()) == {0, 1, 2}


def test_tabular_observed_hybrid_train_predict_schema_parity(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1", "1"],
            "position": [100, 120, 140],
            "context": ["CG", "CG", "CG"],
            "effect_size": [0.9, 0.7, 0.5],
            "weight": [1.0, 0.8, 0.4],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: _StubProject(det))

    def _fake_extract_observed(sample_paths, reference_positions, chromosome, min_coverage=1):
        del chromosome, min_coverage
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        if len(positions) >= 2:
            positions = positions[:2]
        n = len(sample_paths)
        X = np.zeros((n, len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            X[i, :] = 0.2 if Path(str(p)).name in {"S1", "S2"} else 0.8
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(
        tabular_backend.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_observed,
    )
    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        model_type="logistic_regression",
        feature_mode="observed_hybrid",
        observed_feature_min_obs_fraction=0.75,
        save_test_dataset=False,
    )
    predictor_cfg = SimpleNamespace(
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)
    metrics = tabular_backend.predict_tabular_model_from_project(
        project_json=tmp_path / "project.json",
        model_dir=model_dir,
        output_dir=tmp_path / "predict",
    )
    assert "balanced_accuracy" in metrics
    pred_df = pd.read_csv(tmp_path / "predict" / "predictions.csv")
    assert {"obs_fraction", "low_evidence", "prediction_evidence_filtered"}.issubset(pred_df.columns)
    assert pred_df["low_evidence"].astype(bool).all()
    with open(tmp_path / "predict" / "feature_family_ablation.json", encoding="utf-8") as f:
        ablation = json.load(f)
    assert ablation["backend"] == "tabular_sklearn"
    assert "balanced_accuracy" in ablation
    with open(model_dir / "tabular-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert isinstance(meta.get("observed_healthy_reference_vector"), list)
    assert isinstance(meta.get("observed_cancer_reference_vector"), list)
    assert isinstance(meta.get("observed_per_cancer_reference_vectors"), list)
    assert meta.get("observed_healthy_class_label") == "healthy"
    assert meta.get("observed_feature_order_fingerprint")
    assert "max_weighted_directional_score" in set(meta.get("observed_feature_names") or [])
    assert "weighted_directional_agreement__pca1" in set(meta.get("observed_feature_names") or [])
    assert "weighted_cosine_similarity_to_cancer_centroid__pca1" in set(meta.get("observed_feature_names") or [])
    assert "weighted_healthy_tail_evidence__pca1" in set(meta.get("observed_feature_names") or [])
    assert "weighted_mean_abs_error_to_cancer_centroid__pca1" not in set(meta.get("observed_feature_names") or [])
    assert "weighted_fraction_dmps_closer_to_cancer_centroid__pca1" not in set(
        meta.get("observed_feature_names") or []
    )
    training_names = meta.get("training_feature_names") or []
    quality_names = meta.get("quality_feature_names") or []
    assert training_names
    assert quality_names
    assert {"obs_fraction", "n_obs_dmps", "n_total_dmps"}.issubset(set(quality_names))
    assert {"obs_fraction", "n_obs_dmps", "n_total_dmps"}.isdisjoint(set(training_names))
    assert len(training_names) + len(quality_names) == len(meta.get("observed_feature_names") or [])
    assert float(meta.get("observed_hist_eps", 0.0)) > 0.0
    assert float(meta.get("observed_hist_alpha", -1.0)) >= 0.0
    assert float(meta.get("observed_hist_evidence_clip_cap", -1.0)) >= 0.0
    assert 0.0 <= float(meta.get("observed_hist_tail_agreement_threshold", -1.0)) <= 1.0


def test_tabular_multi_method_sequence_outputs_ranking(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1", "1"],
            "position": [100, 120, 140],
            "context": ["CG", "CG", "CG"],
            "effect_size": [0.9, 0.7, 0.5],
            "weight": [1.0, 0.8, 0.4],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: _StubProject(det))

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.zeros((len(sample_paths), len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            X[i, :] = 0.2 if Path(str(p)).name in {"S1", "S2"} else 0.8
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)
    predictor_cfg = SimpleNamespace(
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)

    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        tabular_methods=[
            {"method": "random_forest", "params": {"n_estimators": 50, "random_state": 13}},
            {"method": "logistic_regression", "params": {"max_iter": 400, "random_state": 13}},
        ],
        save_test_dataset=False,
    )
    assert (model_dir / "tabular-model.joblib").is_file()
    assert (model_dir / "tabular_method_metrics.csv").is_file()
    assert (model_dir / "tabular_method_ranking.json").is_file()
    assert (model_dir / "training_metrics.json").is_file()
    assert (model_dir / "tabular_methods" / "00_random_forest" / "training_metrics.json").is_file()
    assert (model_dir / "tabular_methods" / "01_logistic_regression" / "training_metrics.json").is_file()
    assert (model_dir / "tabular_methods" / "00_random_forest" / "selection_eval").is_dir()
    assert (model_dir / "tabular_methods" / "01_logistic_regression" / "selection_eval").is_dir()
    with open(model_dir / "training_metrics.json", encoding="utf-8") as f:
        training_metrics = json.load(f)
    assert "balanced_accuracy" in training_metrics
    assert int(training_metrics.get("n_samples", 0)) == 4
    assert int(training_metrics.get("n_classes", 0)) == 2
    assert training_metrics.get("method") in {"random_forest", "logistic_regression"}
    with open(model_dir / "tabular-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert len(meta.get("tabular_methods_evaluated") or []) == 2
    assert meta.get("selected_tabular_method") in {"random_forest", "logistic_regression"}


def test_tabular_train_dataset_cache_hit_skips_feature_recompute(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "effect_size": [0.7, 0.4],
            "weight": [0.8, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: _StubProject(det))

    calls = {"count": 0}

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        del chromosome, min_coverage
        calls["count"] += 1
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.zeros((len(sample_paths), len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            X[i, :] = 0.2 if Path(str(p)).name in {"S1", "S2"} else 0.8
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)

    dataset_path = tmp_path / "cache" / "train_dataset.h5"
    model_dir_1 = tmp_path / "model_first"
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir_1,
        model_type="logistic_regression",
        save_train_dataset=True,
        reuse_train_dataset=True,
        train_dataset_path=dataset_path,
        save_test_dataset=False,
    )
    assert calls["count"] > 0
    assert dataset_path.is_file()
    assert (tmp_path / "cache" / "train_dataset.h5.meta.json").is_file()

    calls["count"] = 0
    model_dir_2 = tmp_path / "model_second"
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir_2,
        model_type="logistic_regression",
        save_train_dataset=True,
        reuse_train_dataset=True,
        train_dataset_path=dataset_path,
        save_test_dataset=False,
    )
    assert calls["count"] == 0
    with open(model_dir_2 / "tabular-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert bool(meta.get("train_dataset_cache_hit")) is True


def test_tabular_observed_hybrid_cache_schema_mismatch_recomputes(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "effect_size": [0.7, 0.4],
            "weight": [0.8, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    model_bundle.build_model_feature_bundle(tmp_path / "project.json", bundle_dir)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: _StubProject(det))

    calls = {"count": 0}

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        del chromosome, min_coverage
        calls["count"] += 1
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.zeros((len(sample_paths), len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            X[i, :] = 0.2 if Path(str(p)).name in {"S1", "S2"} else 0.8
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)

    dataset_path = tmp_path / "cache" / "train_dataset.h5"
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=tmp_path / "model_first",
        model_type="logistic_regression",
        feature_mode="observed_hybrid",
        save_train_dataset=True,
        reuse_train_dataset=True,
        train_dataset_path=dataset_path,
        save_test_dataset=False,
    )
    assert calls["count"] > 0

    meta_path = tmp_path / "cache" / "train_dataset.h5.meta.json"
    with open(meta_path, encoding="utf-8") as f:
        cache_meta = json.load(f)
    cache_meta["observed_feature_names"] = cache_meta["observed_feature_names"][:-1]
    cache_meta["observed_feature_fill_values"] = cache_meta["observed_feature_fill_values"][:-1]
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(cache_meta, f, indent=2)

    calls["count"] = 0
    tabular_backend.train_tabular_model(
        project_json=tmp_path / "project.json",
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=tmp_path / "model_second",
        model_type="logistic_regression",
        feature_mode="observed_hybrid",
        save_train_dataset=True,
        reuse_train_dataset=True,
        train_dataset_path=dataset_path,
        save_test_dataset=False,
    )
    assert calls["count"] > 0
    with open(tmp_path / "model_second" / "tabular-model-metadata.json", encoding="utf-8") as f:
        model_meta = json.load(f)
    assert bool(model_meta.get("train_dataset_cache_hit")) is False
    assert "schema mismatch" in str(model_meta.get("train_dataset_cache_miss_reason"))


def _write_holdout_test_groups(
    project_json: Path,
    *,
    control_paths: list[str],
    disease_paths: list[str],
) -> Path:
    """Write Model-MC test_groups.json beside project.json (disjoint holdout)."""
    project_json.parent.mkdir(parents=True, exist_ok=True)
    manifest = project_json.parent / "test_groups.json"
    manifest.write_text(
        json.dumps(
            [
                {"class_index": 0, "paths": list(control_paths)},
                {"class_index": 1, "paths": list(disease_paths)},
            ]
        ),
        encoding="utf-8",
    )
    return manifest


def _patch_eval_split_load_project(monkeypatch, stub) -> None:
    """assert_model_mc_train_partition uses eval_split_resolver.load_project directly."""
    from methyl_validation import eval_split_resolver

    monkeypatch.setattr(eval_split_resolver, "load_project", lambda _p: stub)


def test_tabular_saves_test_dataset_next_to_train_dataset(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "effect_size": [0.7, 0.4],
            "weight": [0.8, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    project_json = tmp_path / "project.json"
    model_bundle.build_model_feature_bundle(project_json, bundle_dir)
    stub = _StubProject(det)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: stub)
    _patch_eval_split_load_project(monkeypatch, stub)
    _write_holdout_test_groups(
        project_json,
        control_paths=["/tmp/H1"],
        disease_paths=["/tmp/D1"],
    )

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        del chromosome, min_coverage
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.zeros((len(sample_paths), len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            X[i, :] = 0.2 if Path(str(p)).name in {"S1", "S2", "H1"} else 0.8
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)
    # Legacy misnamed predictor test_* must not drive export (train paths).
    predictor_cfg = SimpleNamespace(
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
        test_group_paths=[],
        holdout_group_paths=[],
        holdout_control_paths=[],
        holdout_disease_paths=[],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)

    train_dataset_path = tmp_path / "export" / "train_dataset.h5"
    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=project_json,
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        model_type="random_forest",
        save_train_dataset=True,
        train_dataset_path=train_dataset_path,
        save_test_dataset=True,
    )
    test_dataset_path = tmp_path / "export" / "test_dataset.h5"
    assert train_dataset_path.is_file()
    assert test_dataset_path.is_file()
    assert (tmp_path / "export" / "test_dataset.h5.meta.json").is_file()
    with open(model_dir / "tabular-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert bool(meta.get("test_dataset_saved")) is True
    assert str(meta.get("test_dataset_path")).endswith("test_dataset.h5")


def test_tabular_test_dataset_uses_test_groups_not_predictor_test_paths(
    tmp_path: Path, monkeypatch
):
    """Regression: misnamed predictor test_* (train cohort) must not fill test Parquet."""
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "effect_size": [0.7, 0.4],
            "weight": [0.8, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    project_json = tmp_path / "project.json"
    model_bundle.build_model_feature_bundle(project_json, bundle_dir)
    stub = _StubProject(det)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: stub)
    _patch_eval_split_load_project(monkeypatch, stub)
    holdout_control = ["/tmp/HoldC1", "/tmp/HoldC2"]
    holdout_disease = ["/tmp/HoldD1"]
    _write_holdout_test_groups(
        project_json,
        control_paths=holdout_control,
        disease_paths=holdout_disease,
    )

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        del chromosome, min_coverage
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.zeros((len(sample_paths), len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            name = Path(str(p)).name
            X[i, :] = 0.2 if name.startswith("S") or name.startswith("HoldC") else 0.8
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)
    train_paths_control = ["/tmp/S1", "/tmp/S2"]
    train_paths_disease = ["/tmp/S3", "/tmp/S4"]
    predictor_cfg = SimpleNamespace(
        test_control_paths=list(train_paths_control),
        test_disease_paths=list(train_paths_disease),
        test_group_paths=[],
        holdout_group_paths=[],
        holdout_control_paths=[],
        holdout_disease_paths=[],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)

    export_dir = tmp_path / "export"
    train_dataset_path = export_dir / "train_dataset.h5"
    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=project_json,
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        model_type="random_forest",
        save_train_dataset=True,
        train_dataset_path=train_dataset_path,
        save_test_dataset=True,
    )
    test_dataset_path = export_dir / "test_dataset.h5"
    from methyl_validation.model_datasets import read_dataset_frame

    train_df = read_dataset_frame(train_dataset_path)
    test_df = read_dataset_frame(test_dataset_path)
    train_ids = set(train_df["sample_id"].astype(str))
    test_ids = set(test_df["sample_id"].astype(str))
    expected_holdout = {"HoldC1", "HoldC2", "HoldD1"}
    assert test_ids == expected_holdout
    assert len(test_df) == 3
    assert train_ids == {"S1", "S2", "S3", "S4"}
    assert not (train_ids & test_ids)
    # Misnamed predictor paths would have produced train IDs — must not.
    assert test_ids != {"S1", "S2", "S3", "S4"}
    # Manifest lands under model_bundle (bundle_h5 parent) even when dataset paths are explicit.
    manifest = json.loads((bundle_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert int(manifest["train_test_overlap_count"]) == 0
    assert int(manifest["n_test_samples"]) == 3
    assert int(manifest["n_train_samples"]) == 4


def test_tabular_gene_scored_test_export_passes_frozen_gene_panel(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "effect_size": [0.7, -0.4],
            "weight": [0.8, 0.3],
            "gene_name": ["G1", "G1"],
            "feature_type": ["promoter", "exon"],
            "comparison_label": ["healthy_vs_pca1", "healthy_vs_pca1"],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    bundle_dir = tmp_path / "bundle"
    frozen_panel_path = bundle_dir / "frozen_genes_production.csv"
    frozen_panel_path.parent.mkdir(parents=True)
    pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_pca1"],
            "gene_name": ["G1"],
            "gene_support_n": [2],
            "gene_importance": [1.0],
        }
    ).to_csv(frozen_panel_path, index=False)

    stub = _StubProjectWithModelBundleConfig(
        det,
        fixed_gene_panel_csv=frozen_panel_path,
    )
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: stub)
    project_json = tmp_path / "project.json"
    model_bundle.build_model_feature_bundle(project_json, bundle_dir)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: stub)
    _patch_eval_split_load_project(monkeypatch, stub)
    _write_holdout_test_groups(
        project_json,
        control_paths=["/tmp/H1"],
        disease_paths=["/tmp/D1"],
    )

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        del chromosome, min_coverage
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.zeros((len(sample_paths), len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            X[i, :] = 0.2 if Path(str(p)).name in {"S1", "S2", "H1"} else 0.8
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)
    predictor_cfg = SimpleNamespace(
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
        test_group_paths=[],
        holdout_group_paths=[],
        holdout_control_paths=[],
        holdout_disease_paths=[],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)

    hybrid_calls: list[dict] = []
    real_build = tabular_backend.build_observed_hybrid_feature_table

    def _spy_build(*args, **kwargs):
        panel = kwargs.get("frozen_gene_panel_df")
        hybrid_calls.append(
            {
                "n_samples": len(args[0]) if args else 0,
                "panel_empty": panel is None or getattr(panel, "empty", True),
            }
        )
        return real_build(*args, **kwargs)

    monkeypatch.setattr(tabular_backend, "build_observed_hybrid_feature_table", _spy_build)

    train_dataset_path = tmp_path / "export" / "train_dataset.h5"
    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=project_json,
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        output_dir=model_dir,
        model_type="random_forest",
        feature_mode="observed_hybrid",
        feature_family_set="gene_scored",
        save_train_dataset=True,
        train_dataset_path=train_dataset_path,
        save_test_dataset=True,
    )
    test_dataset_path = tmp_path / "export" / "test_dataset.h5"
    assert train_dataset_path.is_file()
    assert test_dataset_path.is_file()
    assert len(hybrid_calls) >= 2
    assert hybrid_calls[0]["panel_empty"] is False
    assert hybrid_calls[1]["panel_empty"] is False
    assert hybrid_calls[0]["n_samples"] == 4
    assert hybrid_calls[1]["n_samples"] == 2
    with open(model_dir / "tabular-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    observed_names = [str(x) for x in (meta.get("observed_feature_names") or [])]
    assert any(name.startswith("gene_directional_score__") for name in observed_names)


def test_tabular_defaults_train_and_test_dataset_paths_to_bundle_dir(tmp_path: Path, monkeypatch):
    det = tmp_path / "detections" / "healthy" / "pca1"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 120],
            "context": ["CG", "CG"],
            "effect_size": [0.7, 0.4],
            "weight": [0.8, 0.3],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    monkeypatch.setattr(model_bundle, "load_project", lambda _p: _StubProject(det))
    bundle_dir = tmp_path / "bundle"
    project_json = tmp_path / "project.json"
    model_bundle.build_model_feature_bundle(project_json, bundle_dir)
    stub = _StubProject(det)
    monkeypatch.setattr(tabular_backend, "load_project", lambda _p: stub)
    _patch_eval_split_load_project(monkeypatch, stub)
    _write_holdout_test_groups(
        project_json,
        control_paths=["/tmp/H1"],
        disease_paths=["/tmp/D1"],
    )

    def _fake_extract(sample_paths, reference_positions, chromosome, min_coverage=1):
        del chromosome, min_coverage
        positions = np.asarray(reference_positions["CG"], dtype=np.uint32)
        X = np.zeros((len(sample_paths), len(positions)), dtype=np.float32)
        for i, p in enumerate(sample_paths):
            X[i, :] = 0.2 if Path(str(p)).name in {"S1", "S2", "H1"} else 0.8
        ctx = np.asarray(["CG"] * len(positions), dtype=object)
        return X, positions, ctx, {"CG": np.arange(len(positions), dtype=np.uint32)}

    monkeypatch.setattr(tabular_backend.MethylCentroidPair, "extract_methylation_fractions", _fake_extract)
    predictor_cfg = SimpleNamespace(
        test_control_paths=["/tmp/S1", "/tmp/S2"],
        test_disease_paths=["/tmp/S3", "/tmp/S4"],
        test_group_paths=[],
        holdout_group_paths=[],
        holdout_control_paths=[],
        holdout_disease_paths=[],
    )
    monkeypatch.setattr(tabular_backend, "resolve_predictor_config", lambda _p: predictor_cfg)

    model_dir = tmp_path / "model"
    tabular_backend.train_tabular_model(
        project_json=project_json,
        bundle_h5=bundle_dir / "model_feature_bundle.h5",
        bundle_dir=bundle_dir,
        output_dir=model_dir,
        model_type="random_forest",
        save_train_dataset=True,
        save_test_dataset=True,
    )
    assert (bundle_dir / "train_dataset.h5").is_file()
    assert (bundle_dir / "test_dataset.h5").is_file()
    with open(model_dir / "tabular-model-metadata.json", encoding="utf-8") as f:
        meta = json.load(f)
    assert str(meta.get("train_dataset_path")).endswith("bundle/train_dataset.h5")
    assert str(meta.get("test_dataset_path")).endswith("bundle/test_dataset.h5")


def test_build_estimator_from_config_xgboost(monkeypatch):
    class _FakeXGBClassifier:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(tabular_backend, "XGBClassifier", _FakeXGBClassifier)
    estimator, resolved = tabular_backend._build_estimator_from_config(
        {
            "method": "xgboost",
            "params": {"n_estimators": 123, "max_depth": 4, "learning_rate": 0.05},
        }
    )
    assert isinstance(estimator, _FakeXGBClassifier)
    assert resolved["n_estimators"] == 123
    assert resolved["max_depth"] == 4
    assert resolved["learning_rate"] == 0.05
    assert estimator.kwargs["tree_method"] == "hist"


def test_build_estimator_from_config_xgboost_requires_dependency(monkeypatch):
    monkeypatch.setattr(tabular_backend, "XGBClassifier", None)
    with pytest.raises(ImportError, match="xgboost is required"):
        tabular_backend._build_estimator_from_config({"method": "xgboost", "params": {}})

