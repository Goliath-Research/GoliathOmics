"""sample.extraction_qc applies the baked instance window, not site defaults."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from methyl_worker.handlers.sample_prep import _run_methyl_extraction_qc
from methyl_worker.task_models.sample_prep_models import ExtractionQcTaskInput

REPO = Path(__file__).resolve().parents[2]
COMPILE = REPO / "scripts" / "compile_workflow_program.py"
SAMPLE_PREP = REPO / "workflow_engine" / "domain" / "fixtures" / "sample_prep.program.json"

_RELAXED = {
    "expected_chromosomes": ["1", "21"],
    "guardrails": {
        "min_cpg_weighted_mean_coverage": 0,
        "max_chh_methylation_level": 1,
        "max_chg_methylation_level": 1,
        "min_autosomal_coverage_uniformity_ratio": 0,
        "max_discard_fraction": 1,
    },
}


def _site_path(tmp_path: Path) -> str:
    site = tmp_path / "site.json"
    site.write_text(
        json.dumps(
            {
                "actionConfig": {
                    "extraction_qc": {
                        "guardrails": {
                            "min_cpg_weighted_mean_coverage": 10.0,
                            "max_chh_methylation_level": 0.02,
                            "max_chg_methylation_level": 0.02,
                            "min_autosomal_coverage_uniformity_ratio": 0.5,
                            "max_discard_fraction": 0.9,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return str(site)


def _project(tmp_path: Path, sample_dir: Path) -> Path:
    project = {
        "project_name": "Canary",
        "output_base": str(tmp_path / "out"),
        "chromosomes": ["1", "21"],
        "group1": {"label": "g1", "sample_paths": [str(sample_dir)]},
        "group2": {"label": "g2", "sample_paths": [str(tmp_path / "other")]},
    }
    path = tmp_path / "project.json"
    path.write_text(json.dumps(project), encoding="utf-8")
    return path


def _sample(tmp_path: Path) -> Path:
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    manifest = {
        "metadata": {
            "schema_name": "methylextractor.extraction_manifest",
            "schema_version": "1.0.0",
            "contexts_extracted": ["CG"],
        },
        "summary": {"cpg_weighted_mean_coverage": 9.59},
        "per_chromosome": {
            "1": {"CG": {"num_positions": 1000, "mean_coverage": 11.772, "methylation_level": 0.6}},
            "21": {"CG": {"num_positions": 608, "mean_coverage": 6.0, "methylation_level": 0.6}},
        },
    }
    (sample_dir / "S1.extraction_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return sample_dir


def _coverage(qc_path: str) -> dict[str, Any]:
    payload = json.loads(Path(qc_path).read_text(encoding="utf-8"))
    guardrails = payload["guardrails"]
    if not isinstance(guardrails, dict):
        raise AssertionError("guardrails is not an object")
    metric = guardrails["cpg_weighted_mean_coverage"]
    if not isinstance(metric, dict):
        raise AssertionError("cpg_weighted_mean_coverage is not an object")
    return metric


def test_project_path_uses_baked_window_not_site_default(tmp_path: Path, monkeypatch) -> None:
    """Canary observation: coverage 9.59 must pass a baked minimum of 0, not the site 10."""
    monkeypatch.setenv("METHYL_SITE_CONFIG", _site_path(tmp_path))
    sample_dir = _sample(tmp_path)
    project = _project(tmp_path, sample_dir)

    failed = _run_methyl_extraction_qc(
        "methyl-extraction-qc",
        "sample.extraction_qc",
        ExtractionQcTaskInput(
            sampleId="S1",
            sampleDir=str(sample_dir),
            projectPath=str(project),
        ),
    )
    assert failed.extraction_pass is False
    assert _coverage(failed.qcPath)["normal_range"] == ">= 10.0"

    passed = _run_methyl_extraction_qc(
        "methyl-extraction-qc",
        "sample.extraction_qc",
        ExtractionQcTaskInput(
            sampleId="S1",
            sampleDir=str(sample_dir),
            projectPath=str(project),
            resolvedConfig=_RELAXED,
        ),
    )
    assert passed.extraction_pass is True
    metric = _coverage(passed.qcPath)
    assert metric["value"] == 9.59
    assert metric["pass"] is True
    assert metric["normal_range"] == ">= 0.0"


def test_sample_context_overlays_baked_window_with_and_without_project(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("METHYL_SITE_CONFIG", _site_path(tmp_path))
    sample_dir = _sample(tmp_path)
    project = _project(tmp_path, sample_dir)
    task = ExtractionQcTaskInput(
        sampleId="S1",
        sampleDir=str(sample_dir),
        projectPath=str(project),
        resolvedConfig=_RELAXED,
        sampleContext={"guardrails": {"min_cpg_weighted_mean_coverage": 20}},
    )
    out = _run_methyl_extraction_qc("methyl-extraction-qc", "sample.extraction_qc", task)
    assert out.extraction_pass is False
    assert _coverage(out.qcPath)["normal_range"] == ">= 20.0"

    no_project = ExtractionQcTaskInput(
        sampleId="S1",
        sampleDir=str(sample_dir),
        resolvedConfig=_RELAXED,
        sampleContext={
            "actionConfig": {
                "extraction_qc": {"guardrails": {"min_cpg_weighted_mean_coverage": 0}}
            }
        },
    )
    relaxed = _run_methyl_extraction_qc(
        "methyl-extraction-qc", "sample.extraction_qc", no_project
    )
    assert relaxed.extraction_pass is True
    assert _coverage(relaxed.qcPath)["normal_range"] == ">= 0.0"


def test_compiler_binds_resolved_config_into_extraction_qc(tmp_path: Path) -> None:
    out = tmp_path / "sample_prep_compiled.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO / "packages" / "methyldomain"),
            str(REPO / "workers"),
            str(REPO / "workflow_engine"),
            str(REPO / "workflow_engine" / "domain"),
            str(REPO / "workflow_engine" / "contract"),
            env.get("PYTHONPATH", ""),
        ]
    )
    proc = subprocess.run(
        [sys.executable, str(COMPILE), str(SAMPLE_PREP), "-o", str(out)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    spec = json.loads(out.read_text(encoding="utf-8"))
    nodes = [n for n in spec["nodes"] if n.get("action_name") == "sample.extraction_qc"]
    assert len(nodes) == 2
    for node in nodes:
        template = node["input_template"]
        assert template["resolvedConfig"] == "${var.resolvedConfig__extraction_qc}"
        assert template["sampleContext"] == "${var.sample}"
