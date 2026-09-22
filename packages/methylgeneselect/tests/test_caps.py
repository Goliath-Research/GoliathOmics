"""Unit tests for gene FeatureCuts cap resolution across config layers.

The config-not-code rule requires that missing caps resolve to None (no invented
defaults); these tests lock in that contract and the layer precedence.
"""

from __future__ import annotations

import json
from pathlib import Path

from methyl_gene_select.caps import (
    _coerce_positive_int,
    load_mc_config_gene_caps,
    resolve_gene_featurecuts_caps,
)


def test_coerce_positive_int_rejects_nonpositive_and_garbage() -> None:
    assert _coerce_positive_int(5) == 5
    assert _coerce_positive_int("10") == 10
    assert _coerce_positive_int(0) is None
    assert _coerce_positive_int(-3) is None
    assert _coerce_positive_int(None) is None
    assert _coerce_positive_int("abc") is None


def test_no_layer_supplies_caps_returns_none() -> None:
    assert resolve_gene_featurecuts_caps() == (None, None)


def test_explicit_task_input_takes_precedence() -> None:
    genes, dmps = resolve_gene_featurecuts_caps(
        max_genes=200,
        max_dmps=1000,
        resolved_config={"max_genes": 50, "max_dmps": 100},
    )
    assert (genes, dmps) == (200, 1000)


def test_resolved_config_used_when_task_input_absent() -> None:
    genes, dmps = resolve_gene_featurecuts_caps(
        resolved_config={
            "stability_gene_featurecuts_max_genes": 150,
            "stability_gene_featurecuts_max_dmps": 900,
        }
    )
    assert (genes, dmps) == (150, 900)


def test_mc_config_snapshot_fills_remaining_caps(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_001"
    queue = tmp_path / "queue"
    queue.mkdir()
    run_dir.mkdir()
    (queue / "mc_config.json").write_text(
        json.dumps(
            {
                "stability_gene_featurecuts_max_genes": 75,
                "stability_gene_featurecuts_max_dmps": 500,
            }
        ),
        encoding="utf-8",
    )

    caps = load_mc_config_gene_caps(run_dir)
    assert caps["stability_gene_featurecuts_max_genes"] == 75

    genes, dmps = resolve_gene_featurecuts_caps(run_dir=run_dir)
    assert (genes, dmps) == (75, 500)


def test_load_mc_config_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_mc_config_gene_caps(tmp_path / "run_002") == {}
