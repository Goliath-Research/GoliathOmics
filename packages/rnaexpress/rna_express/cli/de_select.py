"""CLI: methyl-rna-de-select --project P --comparison C --output-dir D.

Modeling action for the RNA process pack. Resolves ``rna_de_select`` config from the
worker ``--resolved-config`` (or profile/site via the project), selects a DE gene panel,
and evaluates a tabular classifier.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _resolve_config(
    project: Optional[str],
    resolved_config: Optional[str],
    step_override: Optional[str],
) -> Dict[str, Any]:
    from methyl_utils import load_project
    from methyl_utils.action_config_resolver import resolve_for_project

    step_override_dict: Optional[Dict[str, Any]] = None
    if step_override:
        step_override_dict = json.loads(step_override)
    project_obj = load_project(project) if project else None
    return dict(
        resolve_for_project(
            "rna_de_select",
            project_obj,
            resolved_config_path=resolved_config,
            step_override=step_override_dict,
        )
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RNA-Seq DE gene panel selection + classification.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--comparison", default=None)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--resolved-config", default=None)
    parser.add_argument("--step-override", default=None)
    args = parser.parse_args(argv)

    from rna_express.core.de_select import run_rna_de_select

    config = _resolve_config(args.project, args.resolved_config, args.step_override)
    results = run_rna_de_select(
        project_path=args.project,
        output_dir=Path(args.output_dir),
        comparison=args.comparison,
        config=config,
    )
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
