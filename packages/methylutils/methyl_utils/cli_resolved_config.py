"""Shared helpers for pipeline CLIs consuming worker-injected resolved config."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from .action_config_resolver import load_resolved_config, resolve_for_project

ProjectLike = Any


def read_json_object(path: Union[str, Path, None]) -> Optional[Dict[str, Any]]:
    if path in (None, ""):
        return None
    p = Path(str(path)).expanduser()
    if not p.is_file():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON object expected: {p}")
    return dict(raw)


def resolve_cli_step_config(
    action_key: str,
    project: Optional[ProjectLike],
    *,
    resolved_config_path: Union[str, Path, None] = None,
    step_override_path: Union[str, Path, None] = None,
) -> Dict[str, Any]:
    """
    Resolve step config for a pipeline CLI.

    Worker path: ``--resolved-config`` (required; no profile/env fallback).
    Standalone dev: ``--project`` + env/profile via ``resolve_for_project``.
    """
    step_override = read_json_object(step_override_path)
    if resolved_config_path not in (None, ""):
        return load_resolved_config(
            action_key,
            resolved_config_path=resolved_config_path,
            step_override=step_override,
        )
    if project is None:
        raise ValueError(f"{action_key}: --project or --resolved-config is required")
    return resolve_for_project(
        action_key,
        project,
        step_override=step_override,
    )


def add_resolved_config_argument(parser, *, help_suffix: str = "") -> None:
    """Register ``--resolved-config`` on an argparse parser."""
    suffix = f" {help_suffix}".rstrip()
    parser.add_argument(
        "--resolved-config",
        type=str,
        default=None,
        metavar="JSON",
        help=(
            "Baked actionConfig slice from workflow task input (worker path). "
            "When set, profile/site/METHYL_* env are not read for tool parameters."
            + (f" {suffix}" if suffix else "")
        ),
    )
