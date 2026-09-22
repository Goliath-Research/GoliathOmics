"""Repository scan: removed Monte Carlo top-level keys must not remain in versioned configs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_validation.config import MonteCarloConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAN_ROOTS = [
    REPO_ROOT / "workflow_engine" / "domain" / "profiles",
    REPO_ROOT / "workflow_engine" / "domain" / "fixtures",
    REPO_ROOT / "workflow_engine" / "domain" / "checks",
    REPO_ROOT / "tools" / "methyl-config-editor" / "configs",
]
REMOVED = set(MonteCarloConfig._REMOVED_FLAT_KEYS)


def _iter_json_files():
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            # Ignore editor/notebook checkpoints.
            if ".ipynb_checkpoints" in path.parts:
                continue
            yield path


def _validation_blocks(obj, *, path: str = "$") -> list[tuple[str, dict]]:
    """Collect actionConfig.validation / step_config.validation / bare validation objects."""
    found: list[tuple[str, dict]] = []
    if not isinstance(obj, dict):
        return found
    for key in ("actionConfig", "step_config"):
        nested = obj.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("validation"), dict):
            found.append((f"{path}.{key}.validation", nested["validation"]))
    # Profile mode fragments sometimes hoist validation keys at the root.
    if "backend_profiles" in obj or "n_iterations" in obj or "train_fraction" in obj:
        found.append((path, obj))
    for key, value in obj.items():
        if isinstance(value, (dict, list)):
            if isinstance(value, dict):
                found.extend(_validation_blocks(value, path=f"{path}.{key}"))
            else:
                for i, item in enumerate(value):
                    found.extend(_validation_blocks(item, path=f"{path}.{key}[{i}]"))
    return found


def _top_level_removed_hits(block: dict, path: str) -> list[str]:
    return [f"{path}.{key}" for key in block if key in REMOVED]


@pytest.mark.parametrize("path", sorted(_iter_json_files()), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_versioned_json_has_no_removed_mc_top_level_keys(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for block_path, block in _validation_blocks(data):
        hits.extend(_top_level_removed_hits(block, block_path))
    assert hits == [], (
        f"{path.relative_to(REPO_ROOT)} still contains removed top-level MC keys: {hits}"
    )
