"""Tests for pangenome site manifest resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_utils.action_config_resolver import resolve_for_project, resolve_pangenome_genome, site_slice_for_action


def test_site_slice_uses_pangenome_linear_ref_fasta() -> None:
    site = {
        "reference_genome": {"fasta": "/work/genomes/legacy.fa"},
        "pangenome_genome": {
            "linear_ref_fasta": "/work/genomes/pangenome/GRCh38.fa",
            "gbz": "/work/genomes/pangenome/graph.gbz",
        },
    }
    methyl = site_slice_for_action(site, "methyl_extract")
    assert methyl["reference_fasta"] == "/work/genomes/pangenome/GRCh38.fa"
    assert methyl["genome_fasta"] == "/work/genomes/pangenome/GRCh38.fa"


def test_resolve_pangenome_genome_requires_all_keys(tmp_path: Path) -> None:
    site = {
        "pangenome_genome": {
            "gbz": str(tmp_path / "g.gbz"),
            "dist": str(tmp_path / "g.dist"),
            "min": str(tmp_path / "g.min"),
            "zipcodes": str(tmp_path / "g.zip"),
            "ref_paths": str(tmp_path / "g.paths"),
            "linear_ref_fasta": str(tmp_path / "GRCh38.fa"),
        }
    }
    for p in site["pangenome_genome"].values():
        Path(p).write_text("x")
    resolved = resolve_pangenome_genome(site)
    assert resolved["linear_ref_fasta"].endswith("GRCh38.fa")


def test_pangenome_linear_fasta_flows_to_project_methyl_extract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site_path = tmp_path / "methyl_site.json"
    linear = str(tmp_path / "GRCh38.fa")
    Path(linear).write_text(">ref\n")
    site_path.write_text(
        json.dumps(
            {
                "reference_genome": {"fasta": str(tmp_path / "legacy.fa")},
                "pangenome_genome": {
                    "gbz": str(tmp_path / "g.gbz"),
                    "dist": str(tmp_path / "g.dist"),
                    "min": str(tmp_path / "g.min"),
                    "zipcodes": str(tmp_path / "g.zip"),
                    "ref_paths": str(tmp_path / "g.paths"),
                    "linear_ref_fasta": linear,
                },
            }
        ),
        encoding="utf-8",
    )
    for name in ("g.gbz", "g.dist", "g.min", "g.zip", "g.paths", "legacy.fa"):
        (tmp_path / name).write_text("x")

    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "project_name": "test",
                "output_base": str(tmp_path / "out"),
                "chromosomes": ["1"],
                "contexts": ["CG"],
                "group1": {"label": "g1", "sample_paths": [str(tmp_path / "s.csv")]},
                "group2": {"label": "g2", "sample_paths": [str(tmp_path / "s.csv")]},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "s.csv").write_text("S1\n")
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(site_path))

    from methyl_utils import load_project

    cfg = resolve_for_project("methyl_extract", load_project(str(project)), site_path=site_path)
    assert cfg.get("reference_fasta") == linear
