"""Dry-run grid search (no pipeline)."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_validation.hyperparam_search import grid_to_dicts, run_search
from methyl_validation.optimization import ObjectiveWeights


def test_grid_to_dicts() -> None:
    g = grid_to_dicts({"a": [1, 2], "b": [3]})
    assert len(g) == 2
    assert {tuple(x.items()) for x in g} == {(("a", 1), ("b", 3)), (("a", 2), ("b", 3))}


def test_dry_run_writes_search_summary(tmp_path: Path) -> None:
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\nH2\nH3\nH4\nH5\n", encoding="utf-8")
    d.write_text("sample\nD1\nD2\nD3\nD4\nD5\n", encoding="utf-8")
    base_proj = tmp_path / "base_project.json"
    base_proj.write_text(
        json.dumps(
            {
                "project_name": "hp",
                "output_base": str(tmp_path / "root_out"),
                "samples_base_path": str(tmp_path),
                "groups": [
                    {"label": "healthy", "sample_paths": [str(h)]},
                    {"label": "disease", "sample_paths": [str(d)]},
                ],
            }
        ),
        encoding="utf-8",
    )
    mc = tmp_path / "mc.json"
    mc.write_text(
        json.dumps(
            {
                "samples_base_path": str(tmp_path),
                "cohorts": [
                    {"label": "healthy", "csv": str(h)},
                    {"label": "disease", "csv": str(d)},
                ],
                "train_fraction": 0.6,
                "n_iterations": 1,
                "base_project": str(base_proj),
                "output_base": str(tmp_path / "output_root"),
            }
        ),
        encoding="utf-8",
    )
    wdir = tmp_path / "work"
    run_search(
        base_config_path=mc,
        grid={"stability_dmp_freq": [0.65, 0.7]},
        work_dir=wdir,
        weights=ObjectiveWeights(),
        dry_run=True,
    )
    assert (wdir / "search_summary.json").is_file()
    with open(wdir / "search_summary.json", encoding="utf-8") as f:
        s = json.load(f)
    assert s["n_trials"] == 2
    assert s["best"] is None
