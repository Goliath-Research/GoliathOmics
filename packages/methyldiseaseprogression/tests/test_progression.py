import csv
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from methyl_disease_progression.progression import run_progression_report

_LONG_GENE_COLS = ["stage_index", "comparison", "rank", "score", "gene"]
_LONG_PATHWAY_COLS = ["stage_index", "comparison", "rank", "score", "pathway"]
_LONG_MODULE_COLS = ["stage_index", "comparison", "rank", "score", "module"]
_LEGACY_LONG_COLS = {
    "entity_type",
    "entity_id",
    "entity_label",
    "comparison_label",
    "disease_group",
    "control_group",
    "q_value",
    "source_file",
}


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


def test_progression_report_writes_outputs(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    summary = run_progression_report(project_path=project_path, report_md=True)

    out_dir = Path(summary["output_dir"])
    assert (out_dir / "genes_long.csv").exists()
    assert (out_dir / "pathways_long.csv").exists()
    assert (out_dir / "modules_long.csv").exists()
    assert (out_dir / "modules_long_variant.csv").exists()
    assert (out_dir / "modules_long_detailed.csv").exists()
    assert (out_dir / "entities_progression_labels.csv").exists()
    assert (out_dir / "summary.json").exists()
    assert (out_dir / "report.md").exists()
    labels = (out_dir / "entities_progression_labels.csv").read_text(encoding="utf-8")
    assert "GENE_A" in labels
    assert "stable_across_stages" in labels

    def _header(path: Path) -> list[str]:
        with path.open(encoding="utf-8", newline="") as f:
            return next(csv.reader(f))

    assert _header(out_dir / "genes_long.csv") == _LONG_GENE_COLS
    assert _header(out_dir / "pathways_long.csv") == _LONG_PATHWAY_COLS
    assert _header(out_dir / "modules_long.csv") == _LONG_MODULE_COLS
    assert _header(out_dir / "modules_long_variant.csv") == _LONG_MODULE_COLS
    assert _header(out_dir / "modules_long_detailed.csv") == _LONG_MODULE_COLS

    mod_text = (out_dir / "modules_long.csv").read_text(encoding="utf-8")
    for legacy in _LEGACY_LONG_COLS:
        assert legacy not in mod_text


def test_progression_report_respects_explicit_order(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    summary = run_progression_report(
        project_path=project_path,
        ordered_comparison_labels=["pca_pca3", "pca_pca1", "pca_pca2"],
    )
    assert summary["ordered_disease_groups"] == ["pca_pca3", "pca_pca1", "pca_pca2"]


def test_progression_report_strict_missing_fails(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    missing_file = tmp_path / "out" / "prog" / "enricher" / "all" / "pca_pca2" / "enrichment_merged.csv"
    missing_file.unlink()
    with pytest.raises(ValueError, match="Missing required progression inputs"):
        run_progression_report(project_path=project_path, strict_missing=True)


def test_progression_report_skips_malformed_mapper_stage_when_not_strict(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    bad_mapper = tmp_path / "out" / "prog" / "mapper" / "all" / "pca_pca2" / "all-gene_name-combined.csv"
    bad_mapper.write_text("gene_name,total_weight\nGENE_A,1.2\n", encoding="utf-8")

    summary = run_progression_report(project_path=project_path, strict_missing=False)
    missing = summary.get("missing_inputs") or []
    assert any("pca_pca2" in msg and "mapper" in msg for msg in missing)
    genes_df = pd.read_csv(summary["genes_long_csv"])
    assert set(genes_df["comparison"].astype(str).unique()) == {"pca_pca1", "pca_pca3"}


def test_progression_prefers_module_primary_for_long_table(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"
    stages = ["pca_pca1", "pca_pca2", "pca_pca3"]
    for stage in stages:
        enricher_dir = root / "enricher" / "all" / stage
        m_rows = [
            "Module_display,Module_primary,Module,Score",
            f"Display_{stage},Primary_{stage},Legacy_{stage},0.5",
        ]
        (enricher_dir / "modules_ranked.csv").write_text("\n".join(m_rows) + "\n", encoding="utf-8")

    summary = run_progression_report(project_path=project_path)
    out_dir = Path(summary["output_dir"])
    mod_text = (out_dir / "modules_long.csv").read_text(encoding="utf-8")
    assert "Primary_pca_pca1" in mod_text
    assert "Primary_pca_pca2" in mod_text
    assert "Primary_pca_pca3" in mod_text
    assert "Display_pca_pca1" not in mod_text


def test_progression_falls_back_to_module_display_when_primary_missing(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"
    stages = ["pca_pca1", "pca_pca2", "pca_pca3"]
    for stage in stages:
        enricher_dir = root / "enricher" / "all" / stage
        m_rows = [
            "Module_display,Module,Score",
            f"Display_{stage},Legacy_{stage},0.5",
        ]
        (enricher_dir / "modules_ranked.csv").write_text("\n".join(m_rows) + "\n", encoding="utf-8")

    summary = run_progression_report(project_path=project_path)
    out_dir = Path(summary["output_dir"])
    mod_text = (out_dir / "modules_long.csv").read_text(encoding="utf-8")
    assert "Display_pca_pca1" in mod_text


def test_progression_variant_prefers_stable_family_key(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"
    stages = ["pca_pca1", "pca_pca2", "pca_pca3"]
    for stage in stages:
        enricher_dir = root / "enricher" / "all" / stage
        m_rows = [
            "Module_variant_family,Module_display,Module,Score",
            f"Stable_PI3K_variant,Display_{stage},Legacy_{stage},0.5",
        ]
        (enricher_dir / "modules_ranked.csv").write_text("\n".join(m_rows) + "\n", encoding="utf-8")

    summary = run_progression_report(project_path=project_path)
    out_dir = Path(summary["output_dir"])
    mod_variant_text = (out_dir / "modules_long_variant.csv").read_text(encoding="utf-8")
    assert "Stable_PI3K_variant" in mod_variant_text
    assert "Display_pca_pca1" not in mod_variant_text


def test_progression_variant_derives_stable_family_from_legacy_columns(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"
    stages = ["pca_pca1", "pca_pca2", "pca_pca3"]
    for stage in stages:
        enricher_dir = root / "enricher" / "all" / stage
        m_rows = [
            "Module_primary,Module_display,Module,Score",
            f"PI3K / growth-factor signaling,PI3K / growth-factor signaling | Drug_{stage},Legacy_{stage},0.5",
        ]
        (enricher_dir / "modules_ranked.csv").write_text("\n".join(m_rows) + "\n", encoding="utf-8")

    summary = run_progression_report(project_path=project_path)
    out_dir = Path(summary["output_dir"])
    mod_variant_text = (out_dir / "modules_long_variant.csv").read_text(encoding="utf-8")
    assert "PI3K / growth-factor signaling | perturbation_evidence" in mod_variant_text
    assert "Drug_pca_pca1" not in mod_variant_text
    assert "Drug_pca_pca2" not in mod_variant_text
    assert "Drug_pca_pca3" not in mod_variant_text


def test_progression_detailed_matches_similar_noncanonical_entities(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"
    specs = [
        ("pca_pca1", "Cluster Alpha (M1)", "State A", "TP53, EGFR, AKT1", "HIF1 signaling; angiogenesis"),
        ("pca_pca2", "Cluster Beta (M7)", "State B", "TP53, EGFR, AKT1", "angiogenesis; HIF1 signaling"),
        ("pca_pca3", "Cluster Gamma (M4)", "State C", "TP53, EGFR, AKT1", "HIF1 signaling; angiogenesis"),
    ]
    for stage, module_name, primary, overlap, pathways in specs:
        enricher_dir = root / "enricher" / "all" / stage
        md_rows = [
            "Module,Module_primary,Score,Overlap_genes,Main_pathways",
            f"{module_name},{primary},0.5,\"{overlap}\",\"{pathways}\"",
        ]
        (enricher_dir / "modules_ranked_detailed.csv").write_text(
            "\n".join(md_rows) + "\n", encoding="utf-8"
        )

    summary = run_progression_report(project_path=project_path)
    out_dir = Path(summary["output_dir"])
    detailed = pd.read_csv(out_dir / "modules_long_detailed.csv")
    assert detailed["module"].nunique() == 1
    assert detailed["module"].iloc[0].endswith("family_001")


def test_progression_gene_score_mode_effect_x_support(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"

    # Stage pca2: set high gene_importance for G2 but higher effect_x_support for G1.
    mapper_p2 = root / "mapper" / "all" / "pca_pca2" / "all-gene_name-combined.csv"
    mapper_p2.write_text(
        "\n".join(
            [
                "gene_name,gene_importance,mean_effect_size,unique_dmps",
                "G1,0.2,0.9,4",
                "G2,5.0,0.1,2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = run_progression_report(
        project_path=project_path,
        resolved_progression_config={"gene_score_mode": "effect_x_support"},
    )
    genes_df = pd.read_csv(summary["genes_long_csv"])
    p2 = genes_df[genes_df["comparison"] == "pca_pca2"].sort_values("rank")
    assert p2.iloc[0]["gene"] == "G1"
    assert summary["gene_score_mode_requested"] == "effect_x_support"
    assert summary["gene_score_mode_by_stage"]["pca_pca2"]["requested_mode"] == "effect_x_support"


def test_progression_uses_project_comparison_order(tmp_path: Path):
    list_dir = tmp_path / "lists"
    list_dir.mkdir(parents=True, exist_ok=True)
    for name in ("healthy.csv", "p1.csv", "p2.csv", "p3.csv"):
        (list_dir / name).write_text("sample\ns1\n", encoding="utf-8")

    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(
            {
                "project_name": "prog_order",
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
                                {
                                    "label": "pca3",
                                    "description": "Gleason Score 4+3",
                                    "sample_paths": [str((list_dir / "p3.csv").resolve())],
                                },
                                {
                                    "label": "pca1",
                                    "description": "Gleason Score 3+3",
                                    "sample_paths": [str((list_dir / "p1.csv").resolve())],
                                },
                                {
                                    "label": "pca2",
                                    "description": "Gleason Score 3+4",
                                    "sample_paths": [str((list_dir / "p2.csv").resolve())],
                                },
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
    root = tmp_path / "out" / "prog_order"
    for stage in ("pca_pca1", "pca_pca2", "pca_pca3"):
        mapper_dir = root / "mapper" / "all" / stage
        enricher_dir = root / "enricher" / "all" / stage
        mapper_dir.mkdir(parents=True, exist_ok=True)
        enricher_dir.mkdir(parents=True, exist_ok=True)
        (mapper_dir / "all-gene_name-combined.csv").write_text(
            "gene_name,gene_importance,mean_effect_size,unique_dmps\nGENE_A,1.0,0.5,2\n",
            encoding="utf-8",
        )
        (enricher_dir / "enrichment_merged.csv").write_text(
            "Term,Adjusted P-value,Combined Score\nPathway One,0.01,3.0\n",
            encoding="utf-8",
        )
        (enricher_dir / "modules_ranked.csv").write_text(
            f"Module,Score\nModule_{stage},0.5\n",
            encoding="utf-8",
        )
        (enricher_dir / "modules_ranked_detailed.csv").write_text(
            f"Module,Score\nModule_{stage} (M1),0.5\n",
            encoding="utf-8",
        )
    summary = run_progression_report(project_path=project_path)
    assert summary["ordering_strategy"] == "project_order"
    assert summary["ordered_disease_groups"] == ["pca_pca3", "pca_pca1", "pca_pca2"]


def test_progression_effect_x_support_fallback_to_gene_importance(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"

    mapper_p1 = root / "mapper" / "all" / "pca_pca1" / "all-gene_name-combined.csv"
    mapper_p1.write_text(
        "\n".join(
            [
                "gene_name,gene_importance",
                "GENE_A,0.3",
                "GENE_B,0.6",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = run_progression_report(
        project_path=project_path,
        resolved_progression_config={"gene_score_mode": "effect_x_support"},
    )
    stage_meta = summary["gene_score_mode_by_stage"]["pca_pca1"]
    assert stage_meta["effective_mode"] == "gene_importance_fallback"


def test_progression_writes_modules_activity_long_when_columns_present(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"
    for idx, stage in enumerate(("pca_pca1", "pca_pca2", "pca_pca3"), start=1):
        enricher_dir = root / "enricher" / "all" / stage
        m_rows = [
            (
                "Module_variant_family,Module_primary,Score,Activity_combined_score_mean,"
                "Activity_gene_effect_mean,Activity_log10q_mean,Activity_overlap_effect_mean"
            ),
            f"Stable_Module_A,Primary_A,0.9,{10+idx},{0.2*idx},{2+idx},{0.1*idx}",
            f"Stable_Module_B,Primary_B,0.5,{4+idx},{0.05*idx},{1+idx},{0.03*idx}",
        ]
        (enricher_dir / "modules_ranked.csv").write_text("\n".join(m_rows) + "\n", encoding="utf-8")

    summary = run_progression_report(project_path=project_path, report_md=True)
    assert summary["modules_activity_rows"] > 0
    assert "modules_activity_note" not in summary
    activity_df = pd.read_csv(summary["modules_activity_long_csv"])
    assert not activity_df.empty
    assert {"stage_index", "comparison", "module", "metric", "value", "rank"} == set(activity_df.columns)
    assert {"combined_score", "effect_size"}.issubset(set(activity_df["metric"].astype(str)))
    report_text = Path(summary["report_md"]).read_text(encoding="utf-8")
    assert "Module activity trajectories" in report_text


def test_progression_activity_columns_backward_compatible_when_absent(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    summary = run_progression_report(project_path=project_path)

    assert summary["modules_activity_rows"] == 0
    assert "No module activity columns detected" in str(summary.get("modules_activity_note", ""))
    activity_df = pd.read_csv(summary["modules_activity_long_csv"])
    assert activity_df.empty


def test_progression_activity_sanitizes_non_finite_values(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"
    for stage in ("pca_pca1", "pca_pca2", "pca_pca3"):
        enricher_dir = root / "enricher" / "all" / stage
        m_rows = [
            (
                "Module_variant_family,Module_primary,Score,Activity_combined_score_mean,"
                "Activity_gene_effect_mean,Activity_log10q_mean,Activity_overlap_effect_mean"
            ),
            "Stable_Module_A,Primary_A,0.9,inf,0.25,2.4,0.11",
            "Stable_Module_B,Primary_B,0.5,3.2,0.12,1.7,0.04",
        ]
        (enricher_dir / "modules_ranked.csv").write_text("\n".join(m_rows) + "\n", encoding="utf-8")

    summary = run_progression_report(project_path=project_path, report_md=True)
    activity_df = pd.read_csv(summary["modules_activity_long_csv"])
    combined = activity_df[activity_df["metric"] == "combined_score"]["value"]
    assert combined.notna().all()
    assert all(math.isfinite(float(v)) for v in combined.tolist())
    report_text = Path(summary["report_md"]).read_text(encoding="utf-8")
    assert "combined_score: inf" not in report_text


def test_progression_activity_preserves_nan_as_zero_and_negative_inf_as_zero(tmp_path: Path):
    project_path = _write_project(tmp_path)
    _write_stage_outputs(tmp_path)
    root = tmp_path / "out" / "prog"
    for stage in ("pca_pca1", "pca_pca2", "pca_pca3"):
        enricher_dir = root / "enricher" / "all" / stage
        m_rows = [
            (
                "Module_variant_family,Module_primary,Score,Activity_combined_score_mean,"
                "Activity_gene_effect_mean,Activity_log10q_mean,Activity_overlap_effect_mean"
            ),
            "Stable_Module_A,Primary_A,0.9,inf,0.25,2.4,0.11",
            "Stable_Module_B,Primary_B,0.5,5.0,0.12,1.7,0.04",
            "Stable_Module_C,Primary_C,0.4,bad_value,0.11,1.6,0.03",
            "Stable_Module_D,Primary_D,0.3,-inf,0.10,1.5,0.02",
        ]
        (enricher_dir / "modules_ranked.csv").write_text("\n".join(m_rows) + "\n", encoding="utf-8")

    summary = run_progression_report(project_path=project_path)
    activity_df = pd.read_csv(summary["modules_activity_long_csv"])
    combined = activity_df[activity_df["metric"] == "combined_score"][["module", "value"]].copy()
    values = dict(zip(combined["module"].astype(str), combined["value"].astype(float)))
    assert values["Stable_Module_A"] == 5.0  # +inf -> max finite value
    assert values["Stable_Module_B"] == 5.0
    assert values["Stable_Module_C"] == 0.0  # unparseable NaN remains bottom-ranked
    assert values["Stable_Module_D"] == 0.0  # -inf treated as bottom-ranked
