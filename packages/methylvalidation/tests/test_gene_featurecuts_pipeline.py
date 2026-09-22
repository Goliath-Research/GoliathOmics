from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from methyl_validation import pipeline_runner


def test_run_pipeline_for_iteration_appends_gene_stability_steps(monkeypatch):
    captured: list[str] = []

    def _fake_run(*_args, **_kwargs):
        return 0, "ok", ""

    monkeypatch.setattr(pipeline_runner, "run_centroid", _fake_run)
    monkeypatch.setattr(pipeline_runner, "run_detector", _fake_run)
    monkeypatch.setattr(pipeline_runner, "run_mapper", _fake_run)

    def _fake_gene_fc(_project_json, _config):
        return 0, "gene ok", ""

    monkeypatch.setattr(
        "methyl_validation.gene_featurecuts.run_gene_featurecuts_for_iteration",
        _fake_gene_fc,
    )

    original_append = pipeline_runner._append_gene_stability_steps

    def _spy_append(steps, **kwargs):
        original_append(steps, **kwargs)
        captured.extend(step[0] for step in steps if isinstance(step, tuple))

    monkeypatch.setattr(pipeline_runner, "_append_gene_stability_steps", _spy_append)

    config = SimpleNamespace(stability_gene_featurecuts_enabled=True)
    ok, errors, timings = pipeline_runner.run_pipeline_for_iteration(
        Path("/tmp/fake_project.json"),
        config=config,
    )
    assert ok is True
    assert errors == []
    step_names = [t["step_name"] for t in timings]
    assert "methyl-mapper" in step_names
    assert "gene-featurecuts" in step_names
