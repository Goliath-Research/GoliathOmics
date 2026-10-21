"""methyl_qc applies the baked instance window instead of the site default."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from methyl_worker.handlers.sample_prep import _run_methyl_qc
from methyl_worker.task_models.sample_prep_models import MethylQcTaskInput


def _qc_json(out_dir: str, sample_id: str) -> None:
    Path(out_dir, f"{sample_id}.json").write_text(
        json.dumps(
            {
                "guardrails": {
                    "overall_pass": True,
                    "screening": {"disposition": "PASS"},
                },
                "qc_history": [],
            }
        ),
        encoding="utf-8",
    )


def test_handler_passes_instance_max_at_dropout(tmp_path: Path, monkeypatch) -> None:
    site = tmp_path / "site.json"
    site.write_text(
        json.dumps(
            {"actionConfig": {"alignment_qc": {"core_guardrails": {"max_at_dropout": 3.0}}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(site))
    sample = tmp_path / "samples" / "S1" / "align.linear.parabricks"
    sample.mkdir(parents=True)
    seen: dict = {}

    def fake(_samples, out_dir, **kwargs):
        seen["core"] = kwargs.get("core_guardrails")
        _qc_json(out_dir, "S1")

    task = MethylQcTaskInput(
        sampleId="S1",
        sampleDir=str(sample),
        resolvedConfig={"core_guardrails": {"max_at_dropout": 100.0}},
    )
    with patch("methyl_alignment_qc.core.process_samples_to_qc_jsons", side_effect=fake):
        out = _run_methyl_qc("methyl-qc", "sample.methyl_qc", task)
    assert out.sampleId == "S1"
    assert seen["core"] is not None
    assert seen["core"].max_at_dropout == 100.0


def test_handler_sample_context_overrides_instance_window(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("METHYL_SITE_CONFIG", raising=False)
    sample = tmp_path / "S1"
    sample.mkdir()
    seen: dict = {}

    def fake(_samples, out_dir, **kwargs):
        seen["core"] = kwargs.get("core_guardrails")
        _qc_json(out_dir, "S1")

    task = MethylQcTaskInput(
        sampleId="S1",
        sampleDir=str(sample),
        resolvedConfig={"core_guardrails": {"max_at_dropout": 100.0}},
        sampleContext={"core_guardrails": {"max_at_dropout": 40.0}},
    )
    with patch("methyl_alignment_qc.core.process_samples_to_qc_jsons", side_effect=fake):
        _run_methyl_qc("methyl-qc", "sample.methyl_qc", task)
    assert seen["core"].max_at_dropout == 40.0
