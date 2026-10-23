"""Semantic action catalog (goliath_app ActionSpec) and runtime catalog re-exports."""

from __future__ import annotations

from typing import List, TYPE_CHECKING

from .action_runtime import (
    CENTROID_ARGV_MAP,
    CLASSIFIER_ARGV_MAP,
    CONTROL_DRAIN_ONLY,
    CONTROL_STOPPABLE,
    DEFAULT_ACTION_CONTROL,
    DEFAULT_ACTION_DISPATCH,
    DEFAULT_PIPELINE_ARGV_MAP,
    DISPATCH_EXCLUSIVE_ONE,
    DISPATCH_EXCLUSIVE_PINNED,
    DISPATCH_SAMPLE_AFFINITY,
    DISPATCH_SAMPLE_PINNED,
    PROJECT_ACTION_CONFIG_KEYS,
    RUNTIME_CATALOG,
    ActionCatalogEntry,
    ActionCatalogExport,
    ActionCategory,
    ActionConfigKey,
    ActionControl,
    ActionDispatch,
    ArgvMap,
    ContextVars,
    DomainEffects,
    DomainOutputBinding,
    ExecutionMode,
    NodeType,
    TemplateDefault,
    TemplateGroupSideDefault,
    build_capability_handlers,
    build_tool_cli_map,
    control_for,
    find_catalog_entry,
    find_catalog_entry_by_capability,
    idempotency_enabled_for,
    idempotency_opt_out_reason_for,
    list_action_catalog,
    validate_catalog,
    validate_catalog_linkage,
)

if TYPE_CHECKING:
    from goliath_app import ActionSpec

_SEMANTIC_CATALOG: List[ActionSpec] | None = None
_BUILDING_SEMANTIC = False


def _semantic_catalog() -> List[ActionSpec]:
    global _SEMANTIC_CATALOG, _BUILDING_SEMANTIC
    if _SEMANTIC_CATALOG is not None:
        return _SEMANTIC_CATALOG
    _BUILDING_SEMANTIC = True
    try:
        from .action_spec_build import build_domain_action_catalog

        _SEMANTIC_CATALOG = build_domain_action_catalog()
        return _SEMANTIC_CATALOG
    finally:
        _BUILDING_SEMANTIC = False
        try:
            from goliath_app.actions import registry

            registry.reload_action_catalog()
        except Exception:
            pass


def __getattr__(name: str):
    if name == "ACTION_CATALOG":
        return _semantic_catalog()
    if name == "ActionSpec":
        from goliath_app import ActionSpec

        return ActionSpec
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ACTION_CATALOG",
    "RUNTIME_CATALOG",
    "ActionCatalogEntry",
    "ActionSpec",
    "build_capability_handlers",
    "build_tool_cli_map",
    "control_for",
    "find_catalog_entry",
    "find_catalog_entry_by_capability",
    "idempotency_enabled_for",
    "list_action_catalog",
    "validate_catalog_linkage",
]
