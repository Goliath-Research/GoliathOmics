"""CLI for methyl-dmp-select."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import click

from ..core.runner import run_dmp_selection, selection_outputs_exist
from ..models.config import DmpSelectionConfig
from ..utils.project_resolver import resolve_dmp_selection_config


@click.command()
@click.option("--project", "-p", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--config", "config_path", type=click.Path(exists=True, path_type=Path), default=None)
@click.option("--group", type=str, default=None, help="Comparison label (e.g. pca1)")
@click.option("--chromosome", "-c", type=str, default=None)
@click.option("--discovery-csv", type=click.Path(path_type=Path), default=None)
@click.option("--output-dir", type=click.Path(path_type=Path), default=None)
@click.option("--step-override", type=click.Path(path_type=Path), default=None)
@click.option(
    "--resolved-config",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Baked dmp_selection actionConfig slice from workflow task input.",
)
@click.option("--force", is_flag=True, default=False, help="Re-run even if outputs exist")
@click.option("--verbose", "-v", is_flag=True, default=False)
def main(
    project: Optional[Path],
    config_path: Optional[Path],
    group: Optional[str],
    chromosome: Optional[str],
    discovery_csv: Optional[Path],
    output_dir: Optional[Path],
    step_override: Optional[Path],
    resolved_config: Optional[Path],
    force: bool,
    verbose: bool,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if config_path is not None:
        import json

        with open(config_path, encoding="utf-8") as f:
            payload = json.load(f)
        config = DmpSelectionConfig(**payload)
    elif project is not None:
        config = resolve_dmp_selection_config(
            project,
            comparison=group,
            chromosome=chromosome,
            step_override_path=step_override,
            resolved_config_path=resolved_config,
        )
    else:
        click.echo("Provide --project or --config", err=True)
        sys.exit(2)

    if discovery_csv is not None:
        config = config.model_copy(update={"discovery_csv": str(discovery_csv)})
    if output_dir is not None:
        config = config.model_copy(update={"output_dir": str(output_dir)})

    try:
        result = run_dmp_selection(config, skip_if_exists=not force)
        click.echo(f"DMP selection {result['status']} for chromosome {config.chromosome}")
        if result["status"] == "ok":
            click.echo(
                f"  classifier={result.get('n_classifier')} extended={result.get('n_extended')}"
            )
    except Exception as exc:
        logging.exception("DMP selection failed")
        click.echo(str(exc), err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
