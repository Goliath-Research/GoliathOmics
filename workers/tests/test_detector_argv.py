"""Tests for pipeline.detector CLI argv synthesis."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_WORKERS = Path(__file__).resolve().parents[1]
_DOMAIN = Path(__file__).resolve().parents[2] / "workflow_engine" / "domain"
for _p in (_WORKERS, _DOMAIN):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from methyl_worker.action_catalog import find_catalog_entry
from methyl_worker.actions.detector import DETECTOR_ARGV_MAP, DetectorCliAction, merge_detector_step_override

_COMPILED_DETECT_TEMPLATE = {
    "tool": "MethylDetector",
    "projectPath": "${var.projectPath}",
    "project": "${var.projectPath}",
    "chromosome": "${var.chromosome}",
    "context": "${var.context}",
    "comparison": "${var.label}",
    "fixedDmpPanel": "${var.fixedDmpPanel}",
    "stepOverride": "${var.stepOverride}",
    "centroid1Dir": "${var.centroid1Dir}",
    "centroid2Dir": "${var.centroid2Dir}",
    "outputDir": "${var.detectOutDir}",
}


def test_merge_detector_step_override_folds_scope_fields() -> None:
    merged = merge_detector_step_override(
        {
            "chromosome": "21",
            "context": "CG",
            "fixedDmpPanel": "/work/out/stable_dmps_genomewide.csv",
            "outputDir": "/work/out/detections/healthy/PCa1",
            "stepOverride": {
                "base_config": {
                    "add_samples": ["/work/samples/S1"],
                    "remove_samples": [],
                }
            },
        }
    )
    assert merged is not None
    assert merged["chromosome"] == "21"
    assert merged["contexts"] == ["CG"]
    assert merged["fixed_dmp_panel"] == "/work/out/stable_dmps_genomewide.csv"
    assert merged["output_dir"] == "/work/out/detections/healthy/PCa1"
    assert "base_config" not in merged
    assert "context" not in merged
    assert "comparison" not in merged


def test_merge_detector_step_override_strips_null_workflow_keys() -> None:
    merged = merge_detector_step_override(
        {
            "chromosome": "21",
            "context": "CG",
            "stepOverride": {
                "detection_mode": "discovery_only",
                "export_classifier": False,
                "context": None,
                "comparison": None,
                "base_config": None,
                "dmp_export_mode": None,
            },
        }
    )
    assert merged == {
        "chromosome": "21",
        "contexts": ["CG"],
        "detection_mode": "discovery_only",
        "export_classifier": False,
    }


def _load_resolve_input_json_from_template():
    # GoliathApp owns workflow_engine/domain/workflow_context.py. This repo
    # only inserts that directory on sys.path; the module is absent here and on main.
    workflow_context = pytest.importorskip(
        "workflow_context",
        reason="workflow_context is provided by GoliathApp, not this repository",
    )
    return workflow_context.resolve_input_json_from_template


def test_detector_build_argv_from_lifecycle_scope() -> None:
    resolve_input_json_from_template = _load_resolve_input_json_from_template()
    scope = {
        "projectPath": "/work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-5-CG.json",
        "chromosome": "21",
        "context": "CG",
        "label": "PCa_PCa1",
        "fixedDmpPanel": "/work/out/monte_carlo_runs/production/stable_dmps_genomewide.csv",
        "stepOverride": None,
        "centroid1Dir": "/work/out/centroids/healthy",
        "centroid2Dir": "/work/out/centroids/PCa1",
        "detectOutDir": "/work/out/detections/healthy/PCa1",
    }
    resolved = resolve_input_json_from_template(_COMPILED_DETECT_TEMPLATE, scope)
    entry = find_catalog_entry("pipeline.detector")
    assert entry is not None
    action = DetectorCliAction(entry=entry, cli_tool="methyl-detector", argv_map=DETECTOR_ARGV_MAP)
    cmd = action.build_argv(resolved)

    assert cmd[0] == "methyl-detector"
    assert "--chromosome" not in cmd
    assert "--context" not in cmd
    assert "--comparison" not in cmd
    assert "--fixed-dmp-panel" not in cmd
    assert "--group" in cmd
    assert "PCa_PCa1" in cmd
    assert "--step-override" in cmd
    override_idx = cmd.index("--step-override")
    override_path = Path(cmd[override_idx + 1])
    try:
        payload = json.loads(override_path.read_text(encoding="utf-8"))
        assert payload["chromosome"] == "21"
        assert payload["contexts"] == ["CG"]
        assert payload["fixed_dmp_panel"].endswith("stable_dmps_genomewide.csv")
        assert payload["output_dir"] == "/work/out/detections/healthy/PCa1"
    finally:
        override_path.unlink(missing_ok=True)


@pytest.mark.skipif(shutil.which("methyl-detector") is None, reason="methyl-detector not on PATH")
def test_detector_argv_accepted_by_methyl_detector_help() -> None:
    """Smoke-check that emitted flags exist on the real methyl-detector CLI."""
    help_text = subprocess.check_output(["methyl-detector", "--help"], text=True)
    for flag in ("--project", "--group", "--step-override", "--centroid1-dir", "--centroid2-dir"):
        assert flag in help_text
    for unsupported in ("--chromosome", "--comparison", "--fixed-dmp-panel"):
        assert unsupported not in help_text

    resolve_input_json_from_template = _load_resolve_input_json_from_template()
    scope = {
        "projectPath": "/work/project.json",
        "chromosome": "21",
        "context": "CG",
        "label": "PCa1",
        "fixedDmpPanel": "/work/panel.csv",
        "stepOverride": None,
        "centroid1Dir": "/work/c1",
        "centroid2Dir": "/work/c2",
        "detectOutDir": "/work/detect",
    }
    resolved = resolve_input_json_from_template(_COMPILED_DETECT_TEMPLATE, scope)
    entry = find_catalog_entry("pipeline.detector")
    assert entry is not None
    action = DetectorCliAction(entry=entry, cli_tool="methyl-detector", argv_map=DETECTOR_ARGV_MAP)
    cmd = action.build_argv(resolved)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    combined = (proc.stderr + proc.stdout).lower()
    assert "no such option" not in combined
    assert "unknown option" not in combined
