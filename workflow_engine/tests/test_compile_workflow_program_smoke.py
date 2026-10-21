"""Smoke: SamplePrep DomainProgram compiles via scripts/compile_workflow_program.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPILE = REPO / "scripts" / "compile_workflow_program.py"
SAMPLE_PREP = REPO / "workflow_engine" / "domain" / "fixtures" / "sample_prep.program.json"


def test_compile_sample_prep_cli(tmp_path: Path) -> None:
    out = tmp_path / "sample_prep_compiled.json"
    env_pythonpath = ":".join(
        [
            str(REPO / "packages" / "methyldomain"),
            str(REPO / "workers"),
            str(REPO / "workflow_engine"),
            str(REPO / "workflow_engine" / "domain"),
            str(REPO / "workflow_engine" / "contract"),
        ]
    )
    proc = subprocess.run(
        [sys.executable, str(COMPILE), str(SAMPLE_PREP), "-o", str(out)],
        check=False,
        capture_output=True,
        text=True,
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": env_pythonpath},
    )
    assert proc.returncode == 0, proc.stderr
    spec = json.loads(out.read_text(encoding="utf-8"))
    assert spec.get("name") == "SamplePrepPipeline"
    assert isinstance(spec.get("nodes"), list) and len(spec["nodes"]) > 0
    extraction = [n for n in spec["nodes"] if n.get("action_name") == "sample.extraction_qc"]
    assert extraction
    for node in extraction:
        template = node["input_template"]
        assert template["resolvedConfig"] == "${var.resolvedConfig__extraction_qc}"
        assert template["sampleContext"] == "${var.sample}"
