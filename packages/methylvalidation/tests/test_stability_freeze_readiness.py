"""Tests for stability_freeze_readiness analyzer."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from methyl_validation.grok_readiness import (
    build_sanitized_ai_payload,
    payload_contains_path_like_strings,
    redact_report_for_export,
    resolve_grok_api_key,
)
from methyl_validation.stability_freeze_readiness import (
    _default_methyl_mapper_home_for_project,
    _module_trajectory_summary,
    _resolve_report_output_path,
    analyze_project_root,
    main,
    render_markdown,
)


def _write_minimal_project(tree: Path) -> None:
    mc = tree / "monte_carlo_runs"
    stab = mc / "stability"
    prod = mc / "production"
    prog = prod / "progression"
    stab.mkdir(parents=True)
    prod.mkdir(parents=True)
    prog.mkdir(parents=True)

    stability_summary = {
        "dmp_stability": {
            "n_runs_analyzed": 10,
            "skipped_no_discovery": 0,
            "skipped_low_balanced_accuracy": 0,
            "min_balanced_accuracy": None,
            "total_unique_dmps": 1000,
            "stable_dmps_at_threshold": 2,
            "min_frequency": 0.8,
            "stable_dmp_fraction": 0.002,
        }
    }
    (stab / "stability_summary.json").write_text(json.dumps(stability_summary), encoding="utf-8")
    pd.DataFrame(
        {
            "chromosome": ["1", "1"],
            "position": [100, 200],
            "frequency": [1.0, 1.0],
            "count": [10, 10],
            "n_runs": [10, 10],
            "effect_size": [0.5, 0.4],
            "p_value": [0.01, 0.02],
            "q_value": [0.05, 0.06],
        }
    ).to_csv(stab / "stable_dmps_production.csv", index=False)

    production_summary = {
        "success": True,
        "errors": [],
        "timings": [
            {"step_name": "methyl-detector", "duration_seconds": 1.0, "return_code": 0},
        ],
        "fixed_dmp_panel": str(prod / "stable_dmps_genomewide.csv"),
        "production_project": str(prod / "project.json"),
    }
    (prod / "production_summary.json").write_text(json.dumps(production_summary), encoding="utf-8")

    project = {
        "project_name": "production",
        "regulatory": {
            "sample_type": "whole blood buffy coat",
            "primary_analyte": "buffy_coat",
        },
    }
    (prod / "project.json").write_text(json.dumps(project), encoding="utf-8")
    pd.DataFrame({"chromosome": ["1", "1"], "position": [100, 200]}).to_csv(
        prod / "stable_dmps_genomewide.csv", index=False
    )

    progression_summary = {
        "ordered_comparison_labels": ["g1", "g2", "g3", "g4"],
        "missing_inputs": [],
        "genes_rows": 10,
        "pathways_rows": 20,
        "modules_rows": 8,
        "modules_variant_rows": 8,
        "modules_detailed_rows": 8,
        "modules_long_csv": str(prog / "modules_long.csv"),
        "modules_long_variant_csv": str(prog / "modules_long_variant.csv"),
        "modules_long_detailed_csv": str(prog / "modules_long_detailed.csv"),
    }
    (prog / "summary.json").write_text(json.dumps(progression_summary), encoding="utf-8")

    # Slim progression schema (stage_index + comparison + module + score)
    rows = []
    for si, comp in enumerate(["g1", "g2", "g3", "g4"]):
        rows.append(
            {"stage_index": si, "comparison": comp, "rank": 1, "score": 0.1 * (si + 1), "module": "ModA"}
        )
        rows.append(
            {"stage_index": si, "comparison": comp, "rank": 2, "score": 0.5, "module": "ModB"}
        )
    pd.DataFrame(rows).to_csv(prog / "modules_long.csv", index=False)
    pd.DataFrame(rows).to_csv(prog / "modules_long_variant.csv", index=False)
    pd.DataFrame(rows).to_csv(prog / "modules_long_detailed.csv", index=False)
    pd.DataFrame(
        {
            "entity_type": ["module"],
            "entity_id": ["ModA"],
            "entity_label": ["ModA"],
            "stages_present": ["0,1,2,3"],
            "n_stages_present": [4],
            "progression_labels": ["monotonic_up"],
        }
    ).to_csv(prog / "entities_progression_labels.csv", index=False)


def test_resolve_report_output_defaults_under_project_readiness(tmp_path: Path):
    proj = tmp_path / "MyProj"
    proj.mkdir()
    assert _resolve_report_output_path(proj, None, "readiness.json") == proj.resolve() / "readiness" / "readiness.json"
    assert _resolve_report_output_path(proj, Path("archive.json"), "x") == proj.resolve() / "readiness" / "archive.json"


def test_resolve_report_output_absolute_unchanged(tmp_path: Path):
    proj = tmp_path / "MyProj"
    proj.mkdir()
    abs_path = tmp_path / "elsewhere" / "out.json"
    assert _resolve_report_output_path(proj, abs_path, "readiness.json") == abs_path.resolve()


def test_default_methyl_mapper_home_follows_project_mount_root():
    assert _default_methyl_mapper_home_for_project(Path("/work/prostate-cancer/MyProject")) == Path(
        "/work/cache/methyl_mapper"
    )
    assert _default_methyl_mapper_home_for_project(
        Path("/work/projects/prostate-cancer/MyProject")
    ) == Path("/work/cache/methyl_mapper")
    assert _default_methyl_mapper_home_for_project(Path("/tmp/MyProject")) == Path(
        "/tmp/cache/methyl_mapper"
    )


def test_analyze_and_render_go(tmp_path: Path):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    report = analyze_project_root(root)
    assert report["verdict"]["overall"] in ("go", "go_with_risks")
    assert report["verdict"]["stability"] == "pass"
    assert report["verdict"]["freeze"] == "pass"
    md = render_markdown(report)
    assert "Stability and freeze readiness report" in md
    assert "Physician-focused interpretation" in md
    assert "Full technical details remain in sections below" in md
    assert "Granularity risk indicator" in md
    assert "Score matrix (entity × stage)" in md
    assert "## Progression" in md
    assert "ModA" in md or "module" in md.lower()


def test_no_go_on_legacy_detector_keys(tmp_path: Path, monkeypatch):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    prod = root / "monte_carlo_runs" / "production"
    panel = str(prod / "stable_dmps_genomewide.csv")

    def _fake_resolve(production_project, action_key):
        if action_key == "detection":
            return {"fixed_dmp_panel": panel, "max_dmps_for_classifier": 10000}
        return {}

    monkeypatch.setattr(
        "methyl_validation.stability_freeze_readiness._resolve_action_from_project_dict",
        _fake_resolve,
    )
    report = analyze_project_root(root)
    assert report["verdict"]["freeze"] == "fail"
    assert report["verdict"]["overall"] == "no_go"


def test_build_sanitized_ai_payload_no_path_like_strings(tmp_path: Path):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    report = analyze_project_root(root)
    payload = build_sanitized_ai_payload(report, disease_context="Example disease", top_n=5)
    assert not payload_contains_path_like_strings(payload)
    analyte = payload.get("analyte_context") or {}
    assert analyte.get("primary_analyte") == "buffy_coat"
    ps = payload.get("progression_summary") or {}
    assert "module_trajectory_variant" in ps
    assert "module_trajectory_detailed" in ps
    assert "module_top_trends_variant" in ps
    assert "module_top_trends_detailed" in ps
    assert "module_track_comparison" in ps


def test_build_sanitized_ai_payload_scrubs_paths_in_verdict_text(tmp_path: Path):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    report = analyze_project_root(root)
    report.setdefault("verdict", {})
    report["verdict"]["warnings"] = list(report["verdict"].get("warnings") or []) + [
        "Missing file /home/someuser/secret/run/output.csv"
    ]
    payload = build_sanitized_ai_payload(report, disease_context=None, top_n=3)
    assert not payload_contains_path_like_strings(payload)
    dumped = json.dumps(payload)
    assert "[redacted]" in dumped
    assert "/home/someuser" not in dumped


def test_resolve_grok_api_key_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("GROK_API_KEY", "from-env")
    key, hint = resolve_grok_api_key(explicit_key=" explicit ")
    assert key == "explicit"
    assert hint is None


def test_resolve_grok_api_key_decrypt_failure_surfaces_hint(tmp_path: Path):
    bad = tmp_path / "grok_api_key.encrypted"
    bad.write_bytes(b"not-valid-fernet-payload")
    key, hint = resolve_grok_api_key(encrypted_file_path=bad)
    assert key is None
    assert hint is not None
    assert "decrypt" in hint.lower()


def test_render_markdown_ai_advisory_section(tmp_path: Path):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    report = analyze_project_root(root)
    report["ai_review"] = {
        "status": "ok",
        "model_used": "grok-4.3",
        "advisory_only": True,
        "structured": {
            "consistency_assessment": "mixed",
            "summary_bullets": ["Signal A", "Signal B"],
            "caveats": ["Limited stages"],
            "suggested_human_checks": ["Review pathways"],
            "canonical_track_assessment": "Canonical track is stable.",
            "variant_track_assessment": "Variant track is sparse.",
            "detailed_track_assessment": "Detailed clusters provide finer granularity.",
            "track_divergence_assessment": "Tracks diverge due to label granularity.",
            "impact_on_confidence": "medium",
            "disease_progression_alignment": "Placeholder alignment text.",
        },
    }
    md = render_markdown(report)
    assert "AI readiness commentary (advisory)" in md
    assert "Primary analyte context used for AI interpretation" in md
    assert "mixed" in md
    assert "Signal A" in md
    assert "Canonical track assessment" in md
    assert "Detailed track assessment" in md
    assert "Impact on confidence" in md


def test_redact_report_for_export_strips_paths(tmp_path: Path):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    report = analyze_project_root(root)
    redacted = redact_report_for_export(report)
    assert "project_root" not in redacted
    assert "paths" not in redacted
    mt = (redacted.get("progression") or {}).get("module_trajectory") or {}
    assert "modules_csv" not in mt


def test_primary_analyte_missing_is_non_blocking(tmp_path: Path):
    root = tmp_path / "ProjNoAnalyte"
    root.mkdir()
    _write_minimal_project(root)
    prod_project = root / "monte_carlo_runs" / "production" / "project.json"
    payload = json.loads(prod_project.read_text(encoding="utf-8"))
    if isinstance(payload.get("regulatory"), dict):
        payload["regulatory"].pop("primary_analyte", None)
    prod_project.write_text(json.dumps(payload), encoding="utf-8")

    report = analyze_project_root(root)
    ai_payload = build_sanitized_ai_payload(report, disease_context=None, top_n=3)
    assert "analyte_context" in ai_payload
    assert (ai_payload.get("analyte_context") or {}).get("primary_analyte") is None
    md = render_markdown(report)
    assert "Stability and freeze readiness report" in md


def test_main_no_grok_review_json(tmp_path: Path):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    out = tmp_path / "report.json"
    prev = os.environ.get("GROK_API_KEY")
    try:
        if "GROK_API_KEY" in os.environ:
            del os.environ["GROK_API_KEY"]
        rc = main([str(root), "--no-grok-review", "--json-out", str(out)])
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["ai_review"]["status"] == "disabled"
        assert rc == 0
    finally:
        if prev is not None:
            os.environ["GROK_API_KEY"] = prev


def test_main_redact_paths_json(tmp_path: Path):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    out = tmp_path / "redacted.json"
    prev = os.environ.get("GROK_API_KEY")
    try:
        if "GROK_API_KEY" in os.environ:
            del os.environ["GROK_API_KEY"]
        main([str(root), "--no-grok-review", "--json-out", str(out), "--redact-paths"])
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "project_root" not in data
    finally:
        if prev is not None:
            os.environ["GROK_API_KEY"] = prev


def test_main_mock_grok_does_not_change_verdict(tmp_path: Path):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    expected = analyze_project_root(root)["verdict"]
    out = tmp_path / "with_ai.json"
    fake_review = {
        "provider": "xai",
        "status": "ok",
        "model_used": "grok-4.3",
        "advisory_only": True,
        "structured": {"consistency_assessment": "high", "summary_bullets": ["ok"]},
    }
    with patch("methyl_validation.grok_readiness.run_grok_readiness_review", return_value=fake_review):
        main([str(root), "--grok-api-key", "dummy", "--json-out", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["verdict"] == expected
    assert data["ai_review"]["status"] == "ok"


def test_main_rejects_existing_file_as_project_root(tmp_path: Path):
    fake_json = tmp_path / "Healthy_vs_PCa1-4-CG.json"
    fake_json.write_text("{}", encoding="utf-8")
    rc = main([str(fake_json), "--stdout-only"])
    assert rc == 2


def test_main_skipped_no_key_non_blocking(tmp_path: Path):
    root = tmp_path / "Proj"
    root.mkdir()
    _write_minimal_project(root)
    out = tmp_path / "skipped.json"
    with patch("methyl_validation.grok_readiness.resolve_grok_api_key", return_value=(None, None)):
        rc = main([str(root), "--json-out", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["ai_review"]["status"] == "skipped_no_key"
    assert rc == 0


def test_ordered_stage_narratives_in_report_grok_payload_and_markdown(tmp_path: Path):
    root = tmp_path / "ProjNarr"
    root.mkdir()
    _write_minimal_project(root)
    lists = root / "lists"
    lists.mkdir()
    for name in ("h.csv", "p1.csv", "p2.csv"):
        (lists / name).write_text("s\nx\n", encoding="utf-8")

    prod = root / "monte_carlo_runs" / "production"
    prog = prod / "progression"
    project_full = {
        "project_name": "ProdRead",
        "output_base": str(tmp_path / "outbase"),
        "samples_base_path": str(root),
        "controls": {
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": [str((lists / "h.csv").resolve())]}],
        },
        "diseases": {
            "label": "cancer",
            "groups": [
                {
                    "label": "pca",
                    "stages": [
                        {
                            "label": "pca1",
                            "sample_paths": [str((lists / "p1.csv").resolve())],
                            "description": "Early stage narrative",
                        },
                        {
                            "label": "pca2",
                            "sample_paths": [str((lists / "p2.csv").resolve())],
                            "description": "Late stage narrative",
                        },
                    ],
                }
            ],
        },
        "comparisons": "control_vs_each_disease",
        "chromosomes": ["1"],
        "contexts": ["CG"],
        "regulatory": {"sample_type": "plasma", "primary_analyte": "cfdna"},
    }
    (prod / "project.json").write_text(json.dumps(project_full), encoding="utf-8")

    progression_summary = {
        "ordered_comparison_labels": ["pca_pca1", "pca_pca2"],
        "missing_inputs": [],
        "genes_rows": 10,
        "pathways_rows": 20,
        "modules_rows": 8,
        "modules_variant_rows": 8,
        "modules_detailed_rows": 8,
        "modules_long_csv": str(prog / "modules_long.csv"),
        "modules_long_variant_csv": str(prog / "modules_long_variant.csv"),
        "modules_long_detailed_csv": str(prog / "modules_long_detailed.csv"),
    }
    (prog / "summary.json").write_text(json.dumps(progression_summary), encoding="utf-8")

    rows = []
    for si, comp in enumerate(["pca_pca1", "pca_pca2"]):
        rows.append({"stage_index": si, "comparison": comp, "rank": 1, "score": 0.2, "module": "ModA"})
    pd.DataFrame(rows).to_csv(prog / "modules_long.csv", index=False)
    pd.DataFrame(rows).to_csv(prog / "modules_long_variant.csv", index=False)
    pd.DataFrame(rows).to_csv(prog / "modules_long_detailed.csv", index=False)

    report = analyze_project_root(root)
    narr = report["progression"]["ordered_stage_narratives"]
    assert len(narr) == 2
    assert narr[0]["description"] == "Early stage narrative"
    assert narr[1]["description"] == "Late stage narrative"

    payload = build_sanitized_ai_payload(
        report, disease_context=report.get("disease_context"), top_n=5
    )
    assert (payload.get("analyte_context") or {}).get("primary_analyte") == "cfdna"
    assert "Early stage narrative" in json.dumps(payload)
    assert not payload_contains_path_like_strings(payload)

    md = render_markdown(report)
    assert "Stage definitions (from project config)" in md
    assert "Early stage narrative" in md
    assert "Primary analyte" in md


def test_ordered_stage_narratives_tolerates_project_without_ordered_label_api(tmp_path: Path):
    root = tmp_path / "ProjLegacy"
    root.mkdir()
    _write_minimal_project(root)

    class _LegacyProject:
        def get_ordered_stage_narratives(self, ordered_tokens):
            return [
                {"comparison_label": tok, "disease_group": None, "description": f"desc:{tok}"}
                for tok in ordered_tokens
            ]

    with patch("methyl_utils.pipeline_config.load_project", return_value=_LegacyProject()):
        report = analyze_project_root(root)

    narr = report["progression"]["ordered_stage_narratives"]
    assert [n["comparison_label"] for n in narr] == ["g1", "g2", "g3", "g4"]
    assert narr[0]["description"] == "desc:g1"


def test_mostly_monotone_uses_single_dominant_direction(tmp_path: Path):
    mod_csv = tmp_path / "modules_long.csv"
    pd.DataFrame(
        [
            {"stage_index": 0, "comparison": "g1", "rank": 1, "score": 1.0, "module": "A"},
            {"stage_index": 1, "comparison": "g2", "rank": 1, "score": 2.0, "module": "A"},
            {"stage_index": 2, "comparison": "g3", "rank": 1, "score": 1.0, "module": "A"},
            {"stage_index": 3, "comparison": "g4", "rank": 1, "score": 1.0, "module": "A"},
        ]
    ).to_csv(mod_csv, index=False)

    traj = _module_trajectory_summary(mod_csv, ["g1", "g2", "g3", "g4"])
    assert traj["monotone_denominator"] == 1
    assert traj["mostly_monotone_up_count"] == 0
    assert traj["mostly_monotone_down_count"] == 0


def test_spearman_ranks_both_axes_with_missing_stage_gaps(tmp_path: Path):
    mod_csv = tmp_path / "modules_long.csv"
    # Missing stage g3 -> non-uniform x-spacing [0, 1, 3].
    pd.DataFrame(
        [
            {"stage_index": 0, "comparison": "g1", "rank": 1, "score": 10.0, "module": "A"},
            {"stage_index": 1, "comparison": "g2", "rank": 1, "score": 5.0, "module": "A"},
            {"stage_index": 3, "comparison": "g4", "rank": 1, "score": 8.0, "module": "A"},
        ]
    ).to_csv(mod_csv, index=False)

    traj = _module_trajectory_summary(mod_csv, ["g1", "g2", "g3", "g4"])
    # True Spearman corr(rank([0,1,3]), rank([10,5,8])) = corr([1,2,3], [3,1,2]) = -0.5
    assert abs(float(traj["median_abs_spearman_stage_vs_score"]) - 0.5) < 1e-9
