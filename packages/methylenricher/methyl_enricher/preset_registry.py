"""Enrichment library preset registry.

Presets used to live as a hardcoded ``LIBRARY_PRESETS`` dict in ``enricher.py``. They
now come from a committed, schema-backed data file (``data/library_presets.json``) so
operators can author them as config and sync them into the cfg registry (kind
``enrichment_library_preset``), mirroring the action catalog. The Python module only
loads and resolves; it holds no preset values.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_DATA_PATH = Path(__file__).parent / "data" / "library_presets.json"


class LibraryPreset(BaseModel):
    """One named preset: default libraries (optional) plus preset-specific libraries."""

    model_config = ConfigDict(extra="forbid")

    description: str = ""
    include_default: bool = Field(
        default=True,
        description="Prepend the catalog default_libraries before this preset's libraries.",
    )
    libraries: List[str] = Field(default_factory=list)


class LibraryPresetCatalog(BaseModel):
    """Committed catalog of enrichment library presets (source of truth)."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    description: str = ""
    default_libraries: List[str] = Field(default_factory=list)
    presets: Dict[str, LibraryPreset] = Field(default_factory=dict)

    def resolve(self, name: str) -> List[str]:
        """Resolve a preset name to its ordered, de-duplicated library list."""
        preset = self.presets.get(name)
        if preset is None:
            valid = ", ".join(sorted(self.presets))
            raise ValueError(f"Unknown library_preset '{name}'. Valid presets: {valid}")
        source: List[str] = []
        if preset.include_default:
            source.extend(self.default_libraries)
        source.extend(preset.libraries)
        out: List[str] = []
        seen = set()
        for lib in source:
            if lib and lib not in seen:
                out.append(lib)
                seen.add(lib)
        return out

    def resolved_presets(self) -> Dict[str, List[str]]:
        return {name: self.resolve(name) for name in self.presets}


def load_catalog(path: Optional[str | Path] = None) -> LibraryPresetCatalog:
    """Load and validate the library preset catalog from the committed data file."""
    data_path = Path(path) if path else _DEFAULT_DATA_PATH
    raw = json.loads(data_path.read_text(encoding="utf-8"))
    return LibraryPresetCatalog.model_validate(raw)


@lru_cache(maxsize=1)
def _cached_catalog() -> LibraryPresetCatalog:
    return load_catalog()


def get_default_libraries() -> List[str]:
    return list(_cached_catalog().default_libraries)


def get_library_presets() -> Dict[str, List[str]]:
    """Return {preset_name: resolved library list} for all committed presets."""
    return _cached_catalog().resolved_presets()
