"""Smoke and compile tests for the PCa1-5 CG workflow check bundle."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

CHECK_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CHECK_ROOT.parents[3]


def _run_check_pipeline(*args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(CHECK_ROOT / "check_pipeline.py"), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=str(REPO_ROOT))


@pytest.mark.parametrize(
    "program_name",
    [
        "mc_stability_staged.program.json",
        "mc_stability_smoke.program.json",
        "validation_freeze.program.json",
        "validation_model.program.json",
        "full_lifecycle.program.json",
    ],
)
def test_pca1_5_programs_compile(program_name: str) -> None:
    fixtures = REPO_ROOT / "workflow_engine" / "domain" / "fixtures"
    project = CHECK_ROOT / "configs" / "project_Healthy_vs_PCa1-5-CG_smoke.json"
    if program_name == "mc_stability_staged.program.json":
        project = CHECK_ROOT / "configs" / "project_Healthy_vs_PCa1-5-CG.json"
    proc = _run_check_pipeline(
        "--project",
        str(project),
        "--program",
        str(fixtures / program_name),
        "--write-spec",
        str(CHECK_ROOT / "compiled" / program_name.replace(".program.json", "")),
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_full_lifecycle_includes_model_mc_before_select_best() -> None:
    """Production full_lifecycle must run model_mc before select_best_model."""
    program_path = (
        REPO_ROOT / "workflow_engine" / "domain" / "fixtures" / "full_lifecycle.program.json"
    )
    program = json.loads(program_path.read_text(encoding="utf-8"))
    actions = [n.get("do") for n in program.get("body", []) if isinstance(n, dict) and "do" in n]
    assert "validation.model_mc" in actions
    assert actions.index("validation.model_mc") < actions.index("validation.select_best_model")


def test_smoke_project_has_resolved_stage_groups() -> None:
    from methyl_utils import load_project

    project = load_project(CHECK_ROOT / "configs" / "project_Healthy_vs_PCa1-5-CG.json")
    labels = [label for label, _ in project.get_resolved_groups()]
    assert "all" in labels
    assert "PCa_PCa1" in labels
    assert "PCa_PCa5" in labels


def test_mc_stability_program_has_centroid_incremental_actions() -> None:
    spec_path = CHECK_ROOT / "compiled" / "compiled_workflow.json"
    if not spec_path.is_file():
        _run_check_pipeline()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    centroid_nodes = [
        n for n in spec.get("nodes", []) if n.get("action_name") == "pipeline.centroid"
    ]
    assert centroid_nodes
    template = centroid_nodes[0]["input_template"]
    assert "addSamples" in template
    assert "removeSamples" in template


def _find_comparison_for_loops(body: list) -> list[dict]:
    loops: list[dict] = []
    for item in body:
        if not isinstance(item, dict):
            continue
        for_loop = item.get("for")
        if isinstance(for_loop, dict) and for_loop.get("as") == "comparison":
            loops.append(item)
        nested = item.get("do")
        if isinstance(nested, list):
            loops.extend(_find_comparison_for_loops(nested))
        elif isinstance(nested, dict):
            inner = nested.get("do")
            if isinstance(inner, list):
                loops.extend(_find_comparison_for_loops(inner))
    return loops


def _collect_step_actions(steps: list) -> list[str]:
    actions: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if "do" in step and isinstance(step["do"], str):
            actions.append(step["do"])
        inner = step.get("do")
        if isinstance(inner, dict) and isinstance(inner.get("do"), str):
            actions.append(inner["do"])
        for branch in ("then", "else"):
            nested = step.get(branch)
            if isinstance(nested, list):
                actions.extend(_collect_step_actions(nested))
    return actions


@pytest.mark.parametrize(
    "program_name",
    [
        "mc_stability_staged.program.json",
        "mc_stability.program.json",
    ],
)
def test_gene_steps_run_once_per_iteration_not_per_comparison(program_name: str) -> None:
    """Biomarker filter and gene_select operate on iteration runDir, not per comparison."""
    fixtures = REPO_ROOT / "workflow_engine" / "domain" / "fixtures"
    program = json.loads((fixtures / program_name).read_text(encoding="utf-8"))
    comparison_loops = _find_comparison_for_loops(program.get("body", []))
    assert comparison_loops, f"expected comparison loop in {program_name}"
    iteration_only_actions = {"validation.biomarker_filter", "pipeline.gene_select"}
    for loop in comparison_loops:
        do_steps = loop.get("do")
        assert isinstance(do_steps, list)
        nested_actions = set(_collect_step_actions(do_steps))
        overlap = nested_actions & iteration_only_actions
        assert not overlap, (
            f"{program_name}: {overlap} must run after all comparisons complete, "
            "not inside the parallel comparison loop"
        )

    iteration_loop = next(
        item
        for item in program["body"]
        if isinstance(item, dict) and item.get("for", {}).get("as") == "iteration"
    )
    iteration_actions = set(_collect_step_actions(iteration_loop["do"]))
    assert "validation.biomarker_filter" in iteration_actions
    assert "pipeline.gene_select" in iteration_actions


def test_mc_gene_enricher_stability_program_compiles() -> None:
    fixtures = REPO_ROOT / "workflow_engine" / "domain" / "fixtures"
    project = CHECK_ROOT / "configs" / "project_Healthy_vs_PCa1-5-CG_smoke.json"
    proc = _run_check_pipeline(
        "--project",
        str(project),
        "--program",
        str(fixtures / "mc_gene_enricher_stability.program.json"),
        "--write-spec",
        str(CHECK_ROOT / "compiled" / "mc_gene_enricher_stability"),
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
