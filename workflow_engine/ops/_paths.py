"""Repo layout sys.path bootstrap for ops helpers."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WF_ENGINE = REPO_ROOT / "workflow_engine"


def ensure_import_paths() -> None:
    """Add repo sibling paths when packages are not pip-installed."""
    for rel in (
        WF_ENGINE,
        WF_ENGINE / "domain",
        WF_ENGINE / "contract",
        WF_ENGINE / "portal",
        REPO_ROOT / "workers",
        REPO_ROOT / "packages" / "methyldomain",
        REPO_ROOT / "packages" / "methylvalidation",
        REPO_ROOT / "packages" / "methylutils",
    ):
        if rel.is_dir() and str(rel) not in sys.path:
            sys.path.insert(0, str(rel))
