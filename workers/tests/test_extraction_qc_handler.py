"""Worker handler tests for extraction QC."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_worker.handlers import execute_task


def test_methyl_extraction_qc_handler_writes_guardrails(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    manifest = {
        "metadata": {
            "schema_name": "methylextractor.extraction_manifest",
            "schema_version": "1.0.0",
            "contexts_extracted": ["CG"],
        },
        "summary": {"cpg_weighted_mean_coverage": 20.0},
        "per_chromosome": {
            "21": {"CG": {"mean_coverage": 18.0}},
        },
    }
    (sample_dir / "S1.extraction_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = execute_task(
        "methyl-extraction-qc",
        "sample.extraction_qc",
        {"tool": "MethylExtractionQc", "sampleId": "S1", "sampleDir": str(sample_dir)},
    )
    out = result.output.model_dump()
    assert out["guardrails"]["overall_pass"] is True
    assert Path(out["qcPath"]).name == "S1.extraction_qc.json"
