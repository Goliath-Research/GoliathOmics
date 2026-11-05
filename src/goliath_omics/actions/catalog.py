"""Action catalog entry point (science specs remain in methyl_worker for R2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from goliath_app import ActionSpec

_ACTION_CATALOG: List[ActionSpec] | None = None


def _load_action_catalog() -> List[ActionSpec]:
    global _ACTION_CATALOG
    if _ACTION_CATALOG is not None:
        return _ACTION_CATALOG
    from methyl_worker.action_catalog import ACTION_CATALOG

    _ACTION_CATALOG = ACTION_CATALOG
    return _ACTION_CATALOG


def __getattr__(name: str):
    if name == "ACTION_CATALOG":
        return _load_action_catalog()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
