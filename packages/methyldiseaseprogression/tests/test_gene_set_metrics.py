"""Tests for optional gene set overlap metrics."""

import json
from pathlib import Path

from methyl_disease_progression.progression import run_progression_report


def _write_project(tmp_path: Path) -> Path:
    list_dir = tmp_path / "lists"
    list_dir.mkdir(parents=True, exist_ok=True)
    for name in ("healthy.csv", "p1.csv", "p2.csv", "p3.csv"):
        (list_dir / name).write_text("sample\ns1\n", encoding="utf-8")

    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "prog",
                "output_base": str((tmp_path / "out").resolve()),
                "samples_base_path": str(tmp_path.resolve()),
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "all", "sample_paths": [str((list_dir / "healthy.csv").resolve())]}],
                },
                "diseases": {
                    "label": "cancer",
                    "groups": [
                        {
                            "label": "pca",
                            "stages": [
                                {"label": "pca1", "sample_paths": [str((list_dir / "p1.csv").resolve())]},
                                {"label": "pca2", "sample_paths": [str((list_dir / "p2.csv").resolve())]},
                                {"label": "pca3", "sample_paths": [str((list_dir / "p3.csv").resolve())]},
                            ],
                        }
                    ],
                },
                "comparisons": "control_vs_each_disease",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return project_path


def _run_progression(
    project_path: Path,
    *,
    progression_config: dict | None = None,
    **kwargs,
):
    return run_progression_report(
        project_path=project_path,
        resolved_progression_config=progression_config or {},
        **kwargs,
    )


def _write_stage_outputs(tmp_path: Path) -> None:
    root = tmp_path / "out" / "prog"
    stages = ["pca_pca1", "pca_pca2", "pca_pca3"]
    stage_genes = {
        "pca_pca1": [("GENE_A", 0.9), ("GENE_B", 0.5)],
        "pca_pca2": [("GENE_A", 1.2), ("GENE_C", 0.8)],
        "pca_pca3": [("GENE_A", 2.0), ("GENE_D", 1.1)],
    }
    stage_paths = {
        "pca_pca1": [("Pathway One", 1e-3, 10.0)],
        "pca_pca2": [("Pathway One", 1e-4, 12.0), ("Pathway Two", 5e-3, 8.0)],
        "pca_pca3": [("Pathway One", 1e-5, 16.0)],
    }
    for stage in stages:
        mapper_dir = root / "mapper" / "all" / stage
        enricher_dir = root / "enricher" / "all" / stage
        mapper_dir.mkdir(parents=True, exist_ok=True)
        enricher_dir.mkdir(parents=True, exist_ok=True)
        gene_rows = ["gene_name,gene_importance"] + [f"{g},{w}" for g, w in stage_genes[stage]]
        (mapper_dir / "all-gene_name-combined.csv").write_text("\n".join(gene_rows) + "\n", encoding="utf-8")
        p_rows = ["Term,Adjusted P-value,Combined Score"] + [f"{t},{q},{s}" for t, q, s in stage_paths[stage]]
        (enricher_dir / "enrichment_merged.csv").write_text("\n".join(p_rows) + "\n", encoding="utf-8")
        m_rows = ["Module,Score", f"Module_{stage},0.5"]
        (enricher_dir / "modules_ranked.csv").write_text("\n".join(m_rows) + "\n", encoding="utf-8")
        md_rows = ["Module,Score", f"Module_{stage} (M1),0.5"]
        (enricher_dir / "modules_ranked_detailed.csv").write_text("\n".join(md_rows) + "\n", encoding="utf-8")


def test_compute_metrics_custom_profile(tmp_path: Path) -> None:
    profile_path = tmp_path / "sets.json"
    profile_path.write_text(
        json.dumps({"overlap": ["GENE_A"], "other": ["ZZZ"]}),
        encoding="utf-8",
    )
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    summary = _run_progression(
        project_path,
        progression_config={
            "gene_set_metrics_enabled": True,
            "gene_sets_path": str(profile_path.resolve()),
            "gene_set_denominator": "all_genes",
        },
    )
    mpath = Path(summary["gene_set_metrics_csv"])
    assert mpath.exists()
    text = mpath.read_text(encoding="utf-8")
    assert "overlap" in text
    assert "fraction" in text
    assert summary["gene_set_metrics"]["enabled"] is True
    assert summary["gene_set_metrics"]["rows"] == 6


def test_cli_enables_metrics_with_bundled_profile(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    summary = run_progression_report(
        project_path=project_path,
        gene_set_metrics_enabled=True,
        disease_profile="prostate_cancer",
        report_md=True,
    )
    assert "gene_set_metrics_csv" in summary
    df_path = Path(summary["gene_set_metrics_csv"])
    assert df_path.exists()
    body = df_path.read_text(encoding="utf-8")
    assert "replication" in body
    assert summary["gene_set_metrics"]["rows"] == 12
    report = Path(summary["report_md"])
    assert "Gene set overlap metrics" in report.read_text(encoding="utf-8")


def test_metrics_disabled_by_default(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    summary = _run_progression(project_path)
    assert "gene_set_metrics_csv" not in summary
    assert summary["gene_set_metrics"]["enabled"] is False
    assert summary["gene_set_fractions"]["enabled"] is False


def test_nested_gene_set_metrics_plan_config(tmp_path: Path) -> None:
    """Nested progression.gene_set_metrics + disease_context writes plan default artifacts."""
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    summary = _run_progression(
        project_path,
        progression_config={
            "disease_context": "prostate_cancer",
            "gene_set_metrics": {"enabled": True, "gene_universe": "mapper_all"},
        },
    )
    assert summary["gene_set_fractions"]["output_basename"] == "stage_gene_set_fractions"
    assert summary["gene_set_fractions"]["gene_universe"] == "mapper_all"
    csv_p = Path(summary["gene_set_fractions_csv"])
    json_p = Path(summary["gene_set_fractions_json"])
    assert csv_p.name == "stage_gene_set_fractions.csv"
    assert json_p.name == "stage_gene_set_fractions.json"
    assert csv_p.read_text(encoding="utf-8").count("\n") >= 2
    payload = json.loads(json_p.read_text(encoding="utf-8"))
    assert payload["n_rows"] == 12


def test_inline_gene_set_profile_categories(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    summary = _run_progression(
        project_path,
        progression_config={
            "gene_set_metrics": {"enabled": True},
            "gene_set_profile": {
                "categories": [
                    {"id": "overlap", "label": "test", "genes": ["GENE_A"]},
                    {"id": "empty", "label": "e", "genes": ["ZZZ"]},
                ]
            },
        },
    )
    assert summary["gene_set_fractions"]["profile_source"] == "inline:gene_set_profile.categories"
    text = Path(summary["gene_set_fractions_csv"]).read_text(encoding="utf-8")
    assert "overlap" in text and "empty" in text
    assert summary["gene_set_fractions"]["rows"] == 6


def test_gene_set_metrics_skips_stage_with_malformed_mapper_columns(tmp_path: Path) -> None:
    profile_path = tmp_path / "sets.json"
    profile_path.write_text(
        json.dumps({"overlap": ["GENE_A"], "other": ["ZZZ"]}),
        encoding="utf-8",
    )
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    bad_mapper = tmp_path / "out" / "prog" / "mapper" / "all" / "pca_pca2" / "all-gene_name-combined.csv"
    bad_mapper.write_text("gene_name,total_weight\nGENE_A,1.2\n", encoding="utf-8")

    summary = _run_progression(
        project_path,
        progression_config={
            "gene_set_metrics_enabled": True,
            "gene_sets_path": str(profile_path.resolve()),
            "gene_set_denominator": "all_genes",
        },
    )
    assert summary["gene_set_metrics"]["enabled"] is True
    # Two categories, but only two valid stages remain => 4 rows.
    assert summary["gene_set_metrics"]["rows"] == 4
