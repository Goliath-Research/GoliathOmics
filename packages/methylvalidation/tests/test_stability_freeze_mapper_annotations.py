from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from methyl_validation import model_bundle, pipeline_runner, stability


def _base_project_payload(tmp_path: Path) -> dict:
    healthy_csv = tmp_path / "healthy.csv"
    disease_csv = tmp_path / "disease.csv"
    healthy_csv.write_text("sample\nH1\n", encoding="utf-8")
    disease_csv.write_text("sample\nD1\n", encoding="utf-8")
    return {
        "project_name": "demo",
        "output_base": str(tmp_path / "out"),
        "samples_base_path": str(tmp_path),
        "groups": [
            {"label": "healthy", "sample_paths": [str(healthy_csv)]},
            {"label": "disease", "sample_paths": [str(disease_csv)]},
        ],
        "step_config": {
            "detection": {"alpha": 0.05},
        },
    }


def test_freeze_production_model_writes_mapper_annotation_pointer(tmp_path: Path, monkeypatch):
    monte_root = tmp_path / "mc"
    monte_root.mkdir(parents=True)
    production_dir = monte_root / "production"
    base_project = tmp_path / "project.json"
    base_project.write_text(
        json.dumps(_base_project_payload(tmp_path), indent=2),
        encoding="utf-8",
    )
    stable_csv = tmp_path / "stable_dmps_production.csv"
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.4],
        }
    ).to_csv(stable_csv, index=False)

    monkeypatch.setattr(
        pipeline_runner,
        "run_pipeline_for_production",
        lambda *args, **kwargs: (True, [], []),
    )

    def _fake_build_cache(*, project_json, output_csv):
        Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
        Path(output_csv).write_text(
            "comparison_label,chromosome,position,context,gene_name,feature_type,region_weight,mapper_source_csv\n",
            encoding="utf-8",
        )
        return {
            "path": str(Path(output_csv).absolute()),
            "rows": 1,
            "unique_loci": 1,
            "source_files": ["/tmp/fake-intersections.csv"],
            "comparisons": ["default"],
        }

    def _fake_build_frozen_genes(*, project_json, output_dir, min_dmps_per_feature, gene_importance_min, top_genes, **kwargs):
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        genes = out_dir / "frozen_genes_production.csv"
        feats = out_dir / "frozen_gene_features.csv"
        genes.write_text("comparison_label,gene_name,gene_importance\n", encoding="utf-8")
        feats.write_text(
            "comparison_label,gene_name,chromosome,feature_type,feature_start,feature_end,n_dmps_in_feature\n",
            encoding="utf-8",
        )
        return {
            "gene_panel_path": str(genes.absolute()),
            "gene_features_path": str(feats.absolute()),
            "genes_rows": 0,
            "features_rows": 0,
            "min_dmps_per_feature": int(min_dmps_per_feature),
            "gene_importance_min": gene_importance_min,
            "top_genes": top_genes,
        }

    monkeypatch.setattr(model_bundle, "build_mapper_annotation_cache", _fake_build_cache)
    monkeypatch.setattr(model_bundle, "build_frozen_gene_panel", _fake_build_frozen_genes)

    summary = stability.freeze_production_model(
        base_project=base_project,
        stable_dmp_csv=str(stable_csv),
        monte_carlo_runs_root=monte_root,
        production_output_dir=str(production_dir),
        skip_centroid=True,
        config=None,
    )
    assert summary["success"] is True
    assert summary["mapper_annotation_cache"]["rows"] == 1

    prod_project = json.loads((production_dir / "project.json").read_text(encoding="utf-8"))
    mb_cfg = prod_project["actionConfig"]["model_bundle"]
    assert mb_cfg["mapper_annotation_csv"] == summary["mapper_annotation_cache"]["path"]
    assert mb_cfg["fixed_gene_panel"] == summary["frozen_gene_panel"]["gene_panel_path"]
    assert mb_cfg["fixed_gene_features"] == summary["frozen_gene_panel"]["gene_features_path"]

    prod_summary = json.loads((production_dir / "production_summary.json").read_text(encoding="utf-8"))
    assert prod_summary["mapper_annotation_cache"]["rows"] == 1
    assert "frozen_gene_panel" in prod_summary


def test_freeze_production_model_forwards_skip_detection(tmp_path: Path, monkeypatch):
    monte_root = tmp_path / "mc"
    monte_root.mkdir(parents=True)
    production_dir = monte_root / "production"
    base_project = tmp_path / "project.json"
    base_project.write_text(
        json.dumps(_base_project_payload(tmp_path), indent=2),
        encoding="utf-8",
    )
    stable_csv = tmp_path / "stable_dmps_production.csv"
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.4],
        }
    ).to_csv(stable_csv, index=False)

    calls: dict[str, object] = {}

    def _fake_pipeline_for_production(*args, **kwargs):
        calls["kwargs"] = kwargs
        return True, [], []

    monkeypatch.setattr(
        pipeline_runner,
        "run_pipeline_for_production",
        _fake_pipeline_for_production,
    )

    def _fake_build_cache(*, project_json, output_csv):
        Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
        Path(output_csv).write_text(
            "comparison_label,chromosome,position,context,gene_name,feature_type,region_weight,mapper_source_csv\n",
            encoding="utf-8",
        )
        return {
            "path": str(Path(output_csv).absolute()),
            "rows": 1,
            "unique_loci": 1,
            "source_files": ["/tmp/fake-intersections.csv"],
            "comparisons": ["default"],
        }

    def _fake_build_frozen_genes(*, project_json, output_dir, min_dmps_per_feature, gene_importance_min, top_genes, **kwargs):
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        genes = out_dir / "frozen_genes_production.csv"
        feats = out_dir / "frozen_gene_features.csv"
        genes.write_text("comparison_label,gene_name,gene_importance\n", encoding="utf-8")
        feats.write_text(
            "comparison_label,gene_name,chromosome,feature_type,feature_start,feature_end,n_dmps_in_feature\n",
            encoding="utf-8",
        )
        return {
            "gene_panel_path": str(genes.absolute()),
            "gene_features_path": str(feats.absolute()),
            "genes_rows": 0,
            "features_rows": 0,
            "min_dmps_per_feature": int(min_dmps_per_feature),
            "gene_importance_min": gene_importance_min,
            "top_genes": top_genes,
        }

    monkeypatch.setattr(model_bundle, "build_mapper_annotation_cache", _fake_build_cache)
    monkeypatch.setattr(model_bundle, "build_frozen_gene_panel", _fake_build_frozen_genes)

    summary = stability.freeze_production_model(
        base_project=base_project,
        stable_dmp_csv=str(stable_csv),
        monte_carlo_runs_root=monte_root,
        production_output_dir=str(production_dir),
        skip_detection=True,
        config=None,
    )
    assert summary["success"] is True
    assert "kwargs" in calls
    kwargs = calls["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["skip_detection"] is True
    assert kwargs["skip_centroid"] is True


def test_freeze_production_model_wires_stable_genes_and_raw_gene(tmp_path: Path, monkeypatch):
    monte_root = tmp_path / "mc"
    monte_root.mkdir(parents=True)
    stability_dir = monte_root / "stability"
    stability_dir.mkdir()
    stable_gene_csv = stability_dir / "stable_genes_production.csv"
    pd.DataFrame(
        {
            "gene_name": ["BRCA1"],
            "frequency": [1.0],
            "count": [3],
            "n_runs": [3],
            "gene_importance": [0.9],
            "mean_effect_size": [0.3],
        }
    ).to_csv(stable_gene_csv, index=False)

    production_dir = monte_root / "production"
    base_project = tmp_path / "project.json"
    base_project.write_text(
        json.dumps(_base_project_payload(tmp_path), indent=2),
        encoding="utf-8",
    )
    stable_dmp_csv = tmp_path / "stable_dmps_production.csv"
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.4],
        }
    ).to_csv(stable_dmp_csv, index=False)

    monkeypatch.setattr(
        pipeline_runner,
        "run_pipeline_for_production",
        lambda *args, **kwargs: (True, [], []),
    )
    monkeypatch.setattr(
        model_bundle,
        "build_mapper_annotation_cache",
        lambda **kwargs: {
            "path": str(production_dir / "model_bundle" / "mapper.csv"),
            "rows": 1,
            "unique_loci": 1,
        },
    )

    frozen_calls: dict = {}

    def _fake_build_frozen_genes(**kwargs):
        frozen_calls.update(kwargs)
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        genes = out_dir / "frozen_genes_production.csv"
        genes.write_text("comparison_label,gene_name,gene_importance\n", encoding="utf-8")
        feats = out_dir / "frozen_gene_features.csv"
        feats.write_text(
            "comparison_label,gene_name,chromosome,feature_type,feature_start,feature_end,n_dmps_in_feature\n",
            encoding="utf-8",
        )
        return {
            "gene_panel_path": str(genes.absolute()),
            "gene_features_path": str(feats.absolute()),
        }

    monkeypatch.setattr(model_bundle, "build_frozen_gene_panel", _fake_build_frozen_genes)

    from methyl_validation.config import MonteCarloConfig

    config = MonteCarloConfig.model_validate(
        {
            "samples_base_path": str(tmp_path),
            "cohorts": [
                {"label": "healthy", "csv": str(tmp_path / "healthy.csv")},
                {"label": "disease", "csv": str(tmp_path / "disease.csv")},
            ],
            "train_fraction": 0.8,
            "n_iterations": 1,
            "base_project": str(base_project),
            "output_base": str(tmp_path),
            "backend_profiles": {
                "ecdf": {"enabled": True, "params": {}},
                "tabular_sklearn": {"enabled": False, "params": {}},
                "generative_hybrid": {"enabled": False, "params": {}},
            },
            "stability_gene_featurecuts_enabled": True,
        }
    )

    summary = stability.freeze_production_model(
        base_project=base_project,
        stable_dmp_csv=str(stable_dmp_csv),
        monte_carlo_runs_root=monte_root,
        production_output_dir=str(production_dir),
        skip_centroid=True,
        config=config,
    )
    assert summary["success"] is True
    assert summary["stable_gene_panel"] is not None
    prod_project = json.loads((production_dir / "project.json").read_text(encoding="utf-8"))
    params = prod_project["actionConfig"]["validation"]["backend_profiles"]["ecdf"]["params"]
    assert params["feature_mode"] == "raw_gene"
    assert params["feature_family_set"] == "gene_scored"
    mb_cfg = prod_project["actionConfig"]["model_bundle"]
    assert "stability_gene_panel" in mb_cfg
    assert frozen_calls.get("stability_gene_panel_path") == summary["stable_gene_panel"]


def test_resolve_production_ecdf_feature_settings_prefers_project_action_config(tmp_path: Path):
    prod = tmp_path / "production"
    prod.mkdir()
    project = prod / "project.json"
    project.write_text(
        json.dumps(
            {
                "project_name": "production",
                "actionConfig": {
                    "validation": {
                        "backend_profiles": {
                            "ecdf": {
                                "enabled": True,
                                "params": {
                                    "feature_mode": "raw_gene",
                                    "feature_family_set": "gene_scored",
                                },
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    mode, family = stability.resolve_production_ecdf_feature_settings(project, config=None)
    assert mode == "raw_gene"
    assert family == "gene_scored"
    assert stability.production_requires_mapper_annotations(mode, family) is True
    assert stability.production_requires_frozen_genes(mode, family) is True


def test_finalize_requires_genes_from_production_project_not_config(tmp_path: Path, monkeypatch):
    """Bug: config may lack feature_mode while prepare_freeze wrote raw_gene on production."""
    prod = tmp_path / "production"
    bundle = prod / "model_bundle"
    bundle.mkdir(parents=True)
    project = prod / "project.json"
    project.write_text(
        json.dumps(
            {
                "project_name": "production",
                "output_base": str(tmp_path),
                "actionConfig": {
                    "validation": {
                        "backend_profiles": {
                            "ecdf": {
                                "enabled": True,
                                "params": {
                                    "feature_mode": "raw_gene",
                                    "feature_family_set": "gene_scored",
                                },
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (bundle / "stable_genes_from_stability.csv").write_text(
        "gene_name,frequency\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        model_bundle,
        "build_mapper_annotation_cache",
        lambda **kwargs: {
            "path": str(bundle / "mapper_dmp_annotations.csv"),
            "rows": 1,
            "unique_loci": 1,
        },
    )

    def _empty_genes(**kwargs):
        genes = bundle / "frozen_genes_production.csv"
        feats = bundle / "frozen_gene_features.csv"
        genes.write_text("comparison_label,gene_name,gene_importance\n", encoding="utf-8")
        feats.write_text(
            "comparison_label,gene_name,chromosome,feature_type,feature_start,feature_end,n_dmps_in_feature\n",
            encoding="utf-8",
        )
        return {
            "gene_panel_path": str(genes),
            "gene_features_path": str(feats),
            "n_genes": 0,
        }

    monkeypatch.setattr(model_bundle, "build_frozen_gene_panel", _empty_genes)

    # config=None → old bug would set needs_genes=False and skip the error.
    try:
        stability.finalize_production_model_bundle(
            production_project=project,
            config=None,
            require_mapper=False,  # old handler bug also passed False from empty config attrs
        )
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "no frozen genes" in str(exc).lower()
    assert raised is True
