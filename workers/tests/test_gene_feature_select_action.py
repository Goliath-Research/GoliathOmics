"""Catalog argv_map stays aligned with pipeline.gene_feature_select CLI builder."""

from __future__ import annotations

from methyl_worker.action_catalog import ACTION_CATALOG
from methyl_worker.actions.gene_feature_select import GENE_FEATURE_SELECT_ARGV_MAP


def test_gene_feature_select_catalog_argv_map() -> None:
    entry = next(e for e in ACTION_CATALOG if e.action_name == "pipeline.gene_feature_select")
    assert "mapperDir" in entry.context_vars
    assert "outputDir" in entry.context_vars
    catalog_argv = dict(entry.argv_map)
    assert catalog_argv == GENE_FEATURE_SELECT_ARGV_MAP
