from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from methyl_validation import pipeline_runner


def test_uses_discovery_only_reads_detection_mode(tmp_path: Path, monkeypatch):
    project = tmp_path / "project.json"
    project.write_text('{"project_name":"t","regulatory":{}}', encoding="utf-8")

    def _mode(mode):
        def _fake(action_key, regulatory=None, **kwargs):
            if action_key == "detection":
                return {"detection_mode": mode}
            return {}

        return _fake

    monkeypatch.setattr(
        "methyl_validation.pipeline_runner.resolve_action_config_from_env",
        _mode("discovery_only"),
    )
    assert pipeline_runner._uses_discovery_only(project) is True

    monkeypatch.setattr(
        "methyl_validation.pipeline_runner.resolve_action_config_from_env",
        _mode("legacy"),
    )
    assert pipeline_runner._uses_discovery_only(project) is False


def test_run_pipeline_for_iteration_inserts_split_steps(monkeypatch, tmp_path: Path):
    project = tmp_path / "project.json"
    project.write_text('{"project_name":"t","regulatory":{}}', encoding="utf-8")
    captured: list[str] = []

    def _fake_run(*_args, **_kwargs):
        return 0, "ok", ""

    monkeypatch.setattr(pipeline_runner, "run_centroid", _fake_run)
    monkeypatch.setattr(pipeline_runner, "run_detector", _fake_run)
    monkeypatch.setattr(pipeline_runner, "run_dmp_select", _fake_run)
    monkeypatch.setattr(pipeline_runner, "run_mapper", _fake_run)
    monkeypatch.setattr(pipeline_runner, "run_gene_select", _fake_run)

    original_append = pipeline_runner._append_gene_stability_steps

    def _spy_append(steps, **kwargs):
        original_append(steps, **kwargs)
        captured.extend(step[0] for step in steps if isinstance(step, tuple))

    monkeypatch.setattr(pipeline_runner, "_append_gene_stability_steps", _spy_append)
    monkeypatch.setattr(
        "methyl_validation.pipeline_runner.resolve_action_config_from_env",
        lambda action_key, regulatory=None, **kwargs: (
            {"detection_mode": "discovery_only"} if action_key == "detection" else {}
        ),
    )

    config = SimpleNamespace(stability_gene_featurecuts_enabled=True)
    ok, errors, timings = pipeline_runner.run_pipeline_for_iteration(project, config=config)
    assert ok is True
    assert errors == []
    step_names = [t["step_name"] for t in timings]
    assert "methyl-dmp-select" in step_names
    assert "methyl-gene-select" in captured
    assert "gene-featurecuts" not in captured


def test_run_pipeline_for_iteration_legacy_keeps_in_process_gene_fc(monkeypatch, tmp_path: Path):
    project = tmp_path / "project.json"
    project.write_text('{"project_name":"t","regulatory":{}}', encoding="utf-8")
    captured: list[str] = []

    def _fake_run(*_args, **_kwargs):
        return 0, "ok", ""

    def _fake_gene_fc(_project_json, _config):
        return 0, "gene ok", ""

    monkeypatch.setattr(pipeline_runner, "run_centroid", _fake_run)
    monkeypatch.setattr(pipeline_runner, "run_detector", _fake_run)
    monkeypatch.setattr(pipeline_runner, "run_dmp_select", _fake_run)
    monkeypatch.setattr(pipeline_runner, "run_mapper", _fake_run)
    monkeypatch.setattr(
        "methyl_validation.gene_featurecuts.run_gene_featurecuts_for_iteration",
        _fake_gene_fc,
    )

    original_append = pipeline_runner._append_gene_stability_steps

    def _spy_append(steps, **kwargs):
        original_append(steps, **kwargs)
        captured.extend(step[0] for step in steps if isinstance(step, tuple))

    monkeypatch.setattr(pipeline_runner, "_append_gene_stability_steps", _spy_append)
    monkeypatch.setattr(
        "methyl_validation.pipeline_runner.resolve_action_config_from_env",
        lambda action_key, regulatory=None, **kwargs: (
            {"detection_mode": "legacy"} if action_key == "detection" else {}
        ),
    )

    config = SimpleNamespace(stability_gene_featurecuts_enabled=True)
    ok, errors, timings = pipeline_runner.run_pipeline_for_iteration(project, config=config)
    assert ok is True
    assert errors == []
    step_names = [t["step_name"] for t in timings]
    assert "methyl-dmp-select" not in step_names
    assert "gene-featurecuts" in captured
    assert "methyl-gene-select" not in captured


def test_run_dmp_select_loops_comparisons_and_chromosomes(monkeypatch, tmp_path: Path):
    project = tmp_path / "project.json"
    project.write_text(
        """
        {
          "project_name": "t",
          "output_base": "/tmp/out",
          "controls": {"label": "healthy", "groups": [{"label": "all", "sample_paths": ["/a.csv"]}]},
          "diseases": {"label": "cancer", "groups": [
            {"label": "PCa", "sample_paths": ["/b.csv"]},
            {"label": "PCa2", "sample_paths": ["/c.csv"]}
          ]},
          "comparisons": "control_vs_each_disease",
          "chromosomes": ["1", "2"],
          "contexts": ["CG"]
        }
        """.strip(),
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def _fake_cmd(cmd, **_kwargs):
        calls.append(list(cmd))
        return 0, "", ""

    monkeypatch.setattr(pipeline_runner, "run_cmd", _fake_cmd)
    rc, _, _ = pipeline_runner.run_dmp_select(project)
    assert rc == 0
    assert len(calls) == 4
    chroms = sorted(cmd[cmd.index("--chromosome") + 1] for cmd in calls)
    assert chroms == ["1", "1", "2", "2"]
    groups = sorted(cmd[cmd.index("--group") + 1] for cmd in calls if "--group" in cmd)
    assert groups == ["PCa", "PCa", "PCa2", "PCa2"]
