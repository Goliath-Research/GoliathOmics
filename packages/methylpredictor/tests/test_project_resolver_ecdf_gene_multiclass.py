"""Regression: raw_gene ECDF OvR models must resolve as multiclass predictors."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_predictor.project_resolver import resolve_predictor_config_per_comparison


def test_per_comparison_resolver_uses_ecdf_gene_ovr_as_multiclass(tmp_path: Path, monkeypatch) -> None:
    out_base = tmp_path / "monte_carlo_runs"
    classifier_dir = out_base / "production" / "classifiers"
    classifier_dir.mkdir(parents=True)
    gene_model = classifier_dir / "ecdf_gene_ovr.pkl"
    gene_model.write_bytes(b"fake")

    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "production",
                "output_base": str(out_base),
                "samples_base_path": str(tmp_path / "samples"),
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "all", "sample_paths": ["C_TRAIN_1"]}],
                },
                "diseases": {
                    "label": "cancer",
                    "groups": [
                        {
                            "label": "PCa",
                            "stages": [
                                {"label": "Low", "sample_paths": ["D_LOW_TRAIN_1"]},
                                {"label": "High", "sample_paths": ["D_HIGH_TRAIN_1"]},
                            ],
                        }
                    ],
                },
                "comparisons": "control_vs_each_disease",
            }
        ),
        encoding="utf-8",
    )
    site = tmp_path / "methyl_site.json"
    site.write_text(
        json.dumps(
            {
                "actionConfig": {
                    "predictor": {
                        "test_group_paths": [
                            {"label": "all", "paths": ["/x/TE_C1"]},
                            {"label": "PCa_Low", "paths": ["/x/TE_LOW"]},
                            {"label": "PCa_High", "paths": ["/x/TE_HIGH"]},
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(site))

    configs = resolve_predictor_config_per_comparison(project_path)
    assert len(configs) == 1
    cfg, label = configs[0]
    assert label == "multiclass"
    assert cfg.model_path == str(gene_model.resolve())
    assert cfg.test_group_paths is not None
    assert [g["label"] for g in cfg.test_group_paths] == ["all", "PCa_Low", "PCa_High"]
