"""Tests for enricher queue planning and verify-only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_enricher.enricher_completeness import TASK_STATUS_FILENAME
from methyl_enricher.enricher_queue import (
    EnricherComparisonTaskV1,
    export_enricher_queue,
    parse_enricher_task_file,
    plan_enricher_tasks,
)
from methyl_enricher.ensure_complete import verify_project_complete


@pytest.fixture
def minimal_production_tree(tmp_path: Path, monkeypatch) -> Path:
    """Minimal production layout with one comparison."""
    sample_csv = tmp_path / "PCa1.csv"
    sample_csv.write_text("sample_id\ns1\n", encoding="utf-8")
    healthy_csv = tmp_path / "healthy.csv"
    healthy_csv.write_text("sample_id\nh1\n", encoding="utf-8")
    proj = {
        "project_name": "production",
        "output_base": str(tmp_path),
        "controls": {
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": [str(healthy_csv)]}],
        },
        "diseases": {
            "label": "cancer",
            "groups": [
                {
                    "label": "PCa",
                    "stages": [{"label": "PCa1", "sample_paths": [str(sample_csv)]}],
                }
            ],
        },
        "comparisons": "control_vs_each_disease",
    }
    # Enricher tool params come from site actionConfig (config-not-code), not study manifest.
    site = tmp_path / "methyl_site.json"
    site.write_text(
        json.dumps(
            {
                "actionConfig": {
                    "enricher": {
                        "library_preset": "cancer-core",
                        "modules": False,
                        "ensure_complete": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(site))
    prod = tmp_path / "production"
    prod.mkdir(parents=True)
    (prod / "project.json").write_text(json.dumps(proj), encoding="utf-8")
    comp = prod / "enricher" / "all" / "PCa_PCa1"
    comp.mkdir(parents=True)
    (comp / "enrich_KEGG_2021_Human.csv").write_text(
        "Term,Adjusted P-value,P-value,Odds Ratio,library\nx,0.01,0.001,1.0,KEGG\n",
        encoding="utf-8",
    )
    (comp / TASK_STATUS_FILENAME).write_text(
        json.dumps({"status": "completed", "libraries_ok": ["KEGG_2021_Human"]}),
        encoding="utf-8",
    )
    mapper = prod / "mapper" / "all" / "PCa_PCa1"
    mapper.mkdir(parents=True)
    (mapper / "all-gene_name-combined.csv").write_text(
        "gene_name,gene_importance,mean_effect_size,unique_dmps,dmp_count\nBRCA1,0.8,0.8,2,2\n",
        encoding="utf-8",
    )
    return prod / "project.json"


def test_plan_and_export_queue(minimal_production_tree: Path):
    plan = plan_enricher_tasks(minimal_production_tree, overwrite=True)
    assert plan["n_tasks"] >= 1
    summary = export_enricher_queue(minimal_production_tree)
    assert Path(summary["manifest"]).is_file()
    assert Path(summary["commands_sh"]).is_file()


def test_parse_task_file(minimal_production_tree: Path):
    plan = plan_enricher_tasks(minimal_production_tree, overwrite=True)
    task_path = Path(plan["runs"][0]["task_json"])
    task = parse_enricher_task_file(task_path)
    assert isinstance(task, EnricherComparisonTaskV1)
    assert task.mode == "enricher_comparison"
