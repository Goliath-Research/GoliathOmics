from __future__ import annotations

import json
from pathlib import Path

from methyl_gene_select.utils.project_config import build_gene_select_config


def test_build_gene_select_config_merges_gene_selection_and_validation(tmp_path: Path, monkeypatch):
    project = tmp_path / "project.json"
    project.write_text(
        """
        {
          "project_name": "t",
          "output_base": "/tmp/out",
          "controls": {"label": "healthy", "groups": [{"label": "all", "sample_paths": ["/a.csv"]}]},
          "diseases": {"label": "cancer", "groups": [{"label": "PCa", "sample_paths": ["/b.csv"]}]},
          "comparisons": "control_vs_each_disease",
          "chromosomes": ["1"],
          "contexts": ["CG"]
        }
        """.strip(),
        encoding="utf-8",
    )

    def _fake_resolve(action_key, project, **kwargs):
        if action_key == "gene_selection":
            return {
                "target_balanced_accuracy": 0.95,
                "min_selected_genes": 200,
                "biomarker_filter_enabled": True,
                "biomarker_mode": "ppi_only",
            }
        if action_key == "validation":
            return {
                "stability_gene_featurecuts_max_genes": 500,
                "stability_gene_region_hits": ["promoter"],
            }
        return {}

    monkeypatch.setattr(
        "methyl_gene_select.utils.project_config.resolve_for_project",
        _fake_resolve,
    )
    cfg = build_gene_select_config(project)
    assert cfg.stability_target_balanced_accuracy == 0.95
    assert cfg.stability_min_selected_genes == 200
    assert cfg.stability_gene_biomarker_filter_enabled is True
    assert cfg.stability_gene_biomarker_mode == "ppi_only"
    assert cfg.stability_gene_featurecuts_max_genes == 500
    assert cfg.stability_gene_region_hits == ["promoter"]


def test_build_gene_select_config_applies_site_and_profile_caps(tmp_path: Path, monkeypatch):
    project = tmp_path / "project.json"
    project.write_text(
        """
        {
          "project_name": "t",
          "output_base": "/tmp/out",
          "controls": {"label": "healthy", "groups": [{"label": "all", "sample_paths": ["/a.csv"]}]},
          "diseases": {"label": "cancer", "groups": [{"label": "PCa", "sample_paths": ["/b.csv"]}]},
          "comparisons": "control_vs_each_disease",
          "chromosomes": ["1"],
          "contexts": ["CG"]
        }
        """.strip(),
        encoding="utf-8",
    )

    def _fake_resolve(action_key, project, **kwargs):
        if action_key == "validation":
            return {
                "stability_gene_featurecuts_max_dmps": 1000,
                "stability_gene_featurecuts_max_genes": 200,
            }
        return {}

    monkeypatch.setattr(
        "methyl_gene_select.utils.project_config.resolve_for_project",
        _fake_resolve,
    )
    cfg = build_gene_select_config(project)
    assert cfg.stability_gene_featurecuts_max_dmps == 1000
    assert cfg.stability_gene_featurecuts_max_genes == 200


def test_study_null_max_dmps_clears_site_gene_selection_cap(tmp_path: Path, monkeypatch):
    """Study actionConfig.validation null must uncap despite site gene_selection.max_dmps=1000."""
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "project_name": "t",
                "output_base": "/tmp/out",
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "all", "sample_paths": ["/a.csv"]}],
                },
                "diseases": {
                    "label": "cancer",
                    "groups": [{"label": "PCa", "sample_paths": ["/b.csv"]}],
                },
                "comparisons": "control_vs_each_disease",
                "chromosomes": ["1"],
                "contexts": ["CG"],
                "actionConfig": {
                    "gene_selection": {"max_dmps": None, "max_genes": 200},
                    "validation": {"stability_gene_featurecuts_max_dmps": None},
                },
            }
        ),
        encoding="utf-8",
    )

    def _fake_resolve(action_key, project, **kwargs):
        if action_key == "gene_selection":
            return {"max_dmps": 1000, "max_genes": 200}
        if action_key == "validation":
            return {"stability_gene_featurecuts_max_dmps": 1000}
        return {}

    monkeypatch.setattr(
        "methyl_gene_select.utils.project_config.resolve_for_project",
        _fake_resolve,
    )
    cfg = build_gene_select_config(project)
    assert not hasattr(cfg, "stability_gene_featurecuts_max_dmps") or getattr(
        cfg, "stability_gene_featurecuts_max_dmps", None
    ) is None
    assert cfg.stability_gene_featurecuts_max_genes == 200


def test_mc_config_null_max_dmps_clears_site_cap(tmp_path: Path, monkeypatch):
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "project_name": "t",
                "output_base": "/tmp/out",
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "all", "sample_paths": ["/a.csv"]}],
                },
                "diseases": {
                    "label": "cancer",
                    "groups": [{"label": "PCa", "sample_paths": ["/b.csv"]}],
                },
                "comparisons": "control_vs_each_disease",
                "chromosomes": ["1"],
                "contexts": ["CG"],
            }
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run_0001"
    queue = tmp_path / "queue"
    run_dir.mkdir()
    queue.mkdir()
    (queue / "mc_config.json").write_text(
        json.dumps({"stability_gene_featurecuts_max_dmps": None, "gene_featurecuts_target_ba": 0.95}),
        encoding="utf-8",
    )

    def _fake_resolve(action_key, project, **kwargs):
        if action_key == "gene_selection":
            return {"max_dmps": 1000, "max_genes": 200}
        return {}

    monkeypatch.setattr(
        "methyl_gene_select.utils.project_config.resolve_for_project",
        _fake_resolve,
    )
    cfg = build_gene_select_config(project, run_dir=run_dir)
    assert getattr(cfg, "stability_gene_featurecuts_max_dmps", None) is None
    assert cfg.gene_featurecuts_target_ba == 0.95
    assert cfg.stability_gene_featurecuts_max_genes == 200
