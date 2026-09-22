"""CLI for methyl-gene-select."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

import click

from ..core.gene_featurecuts import run_gene_featurecuts_for_iteration
from ..utils.project_config import build_gene_select_config


@click.command()
@click.option("--project", "-p", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--run-dir", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--max-genes", type=int, default=None)
@click.option("--max-dmps", type=int, default=None)
@click.option("--biomarker-filter", is_flag=True, default=False)
@click.option("--verbose", "-v", is_flag=True, default=False)
def main(
    project: Path,
    run_dir: Path,
    max_genes: Optional[int],
    max_dmps: Optional[int],
    biomarker_filter: bool,
    verbose: bool,
) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    overrides: dict[str, Any] = {}
    if max_genes is not None:
        overrides["stability_gene_featurecuts_max_genes"] = max_genes
    if max_dmps is not None:
        overrides["stability_gene_featurecuts_max_dmps"] = max_dmps
    if biomarker_filter:
        overrides["stability_gene_biomarker_filter_enabled"] = True
    cfg = build_gene_select_config(project, run_dir=run_dir, **overrides)
    rc, msg, err = run_gene_featurecuts_for_iteration(project, cfg, run_dir=run_dir)
    if rc != 0:
        click.echo(err or msg, err=True)
        sys.exit(rc)
    click.echo(msg or "Gene selection complete")


if __name__ == "__main__":
    main()
