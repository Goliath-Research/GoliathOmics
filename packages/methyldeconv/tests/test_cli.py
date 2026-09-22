"""Regression tests for the methyl-cell-deconv command-line boundary."""

from __future__ import annotations

import sys
from pathlib import Path

from methyl_deconv import cli
from methyl_deconv.config import CellDeconvStepConfig


def test_main_uses_shared_resolved_config_contract(tmp_path: Path, monkeypatch) -> None:
    project_path = tmp_path / "project.json"
    project_path.write_text("{}", encoding="utf-8")
    override_path = tmp_path / "override.json"
    override_path.write_text('{"marker_min_coverage": 4}', encoding="utf-8")
    output_dir = tmp_path / "out"
    project = object()
    captured = {}

    monkeypatch.setattr(cli, "load_project", lambda path: project)

    def fake_resolve(action_key, loaded_project, **kwargs):
        captured.update(
            action_key=action_key,
            project=loaded_project,
            kwargs=kwargs,
        )
        return {"marker_min_coverage": 4}

    monkeypatch.setattr(cli, "resolve_cli_step_config", fake_resolve)
    monkeypatch.setattr(
        cli,
        "resolve_cell_deconv_step_config",
        lambda *args, **kwargs: (
            CellDeconvStepConfig(marker_min_coverage=4),
            [("S1", str(tmp_path / "S1"), "healthy")],
            str(output_dir),
        ),
    )
    monkeypatch.setattr(
        cli,
        "run_cell_deconv_for_samples",
        lambda samples, out, cfg: {
            "n_samples": len(samples),
            "output_csv": str(out / "cell_fractions.csv"),
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "methyl-cell-deconv",
            "--project",
            str(project_path),
            "--output-dir",
            str(output_dir),
            "--step-override",
            str(override_path),
        ],
    )

    cli.main()

    assert captured["action_key"] == "cell_deconvolution"
    assert captured["project"] is project
    assert captured["kwargs"] == {
        "resolved_config_path": None,
        "step_override_path": override_path,
    }
