"""CLI for methyl-gene-feature-select (Phase 3)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import click

from ..core.runner import run_gene_feature_selection


@click.command()
@click.option("--mapper-dir", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output-dir", type=click.Path(path_type=Path), required=True)
@click.option("--max-features", type=int, default=None)
@click.option("--target-ba", type=float, default=None)
@click.option("--force", is_flag=True, default=False)
@click.option("--verbose", "-v", is_flag=True, default=False)
def main(
    mapper_dir: Path,
    output_dir: Path,
    max_features: Optional[int],
    target_ba: Optional[float],
    force: bool,
    verbose: bool,
) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    try:
        result = run_gene_feature_selection(
            mapper_dir=mapper_dir,
            output_dir=output_dir,
            target_balanced_accuracy=target_ba,
            max_features=max_features,
            skip_if_exists=not force,
        )
        click.echo(f"Gene-feature selection: {result['status']}")
    except Exception as exc:
        logging.exception("Gene-feature selection failed")
        click.echo(str(exc), err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
