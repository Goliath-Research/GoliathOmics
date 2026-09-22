"""Locate the MethylPipeline repository root from installed package paths or cwd.

Editable installs normally keep ``__file__`` under ``packages/<name>/...``, so
``Path(__file__).parents[N]`` reaches the repo. When pip reinstalls a dependency
into ``.venv/lib/pythonX/site-packages`` (common during ``pip install -e workers
--with-deps``), those fixed parent counts resolve to ``.venv/lib/schemas/...``
instead of the committed ``schemas/`` tree. Walk-up discovery avoids that.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


_REPO_MARKERS = (
    ("schemas", "domain"),
    ("scripts", "packages.list"),
)


def _looks_like_repo_root(candidate: Path) -> bool:
    return all((candidate.joinpath(*parts)).exists() for parts in _REPO_MARKERS)


def find_repo_root(start: Optional[Path] = None) -> Path:
    """Return the repo root containing ``schemas/domain`` and ``scripts/packages.list``.

    Search order:
    1. ``METHYL_REPO_ROOT`` if set and valid
    2. Walk parents of ``start`` (default: caller should pass ``Path(__file__)``)
    3. Walk parents of ``Path.cwd()``
    """
    env = os.environ.get("METHYL_REPO_ROOT", "").strip()
    if env:
        env_path = Path(env).expanduser().resolve()
        if _looks_like_repo_root(env_path):
            return env_path

    origins: list[Path] = []
    if start is not None:
        origins.append(Path(start).expanduser().resolve())
    origins.append(Path.cwd().resolve())

    seen: set[Path] = set()
    for origin in origins:
        for candidate in (origin, *origin.parents):
            if candidate in seen:
                continue
            seen.add(candidate)
            if _looks_like_repo_root(candidate):
                return candidate

    raise FileNotFoundError(
        "Could not locate MethylPipeline repo root (expected schemas/domain and "
        "scripts/packages.list). Set METHYL_REPO_ROOT or run from the repository."
    )


def repo_schemas_dir(*parts: str, start: Optional[Path] = None) -> Path:
    """``<repo>/schemas`` or ``<repo>/schemas/<parts...>``."""
    root = find_repo_root(start)
    path = root / "schemas"
    for part in parts:
        path = path / part
    return path
