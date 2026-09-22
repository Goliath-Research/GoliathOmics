"""Catalog-driven CLI action provider registry.

Specialized ``CliAction`` subclasses and collectors register here by
``action_name``. ``build_action_from_catalog`` looks up providers instead of
branching on methylation-specific action names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Type

from ..action_catalog import ActionCatalogEntry
from ..collectors import ArtifactCollector, GenericPipelineCollector
from .base import ActionBase, CliAction, DEFAULT_PIPELINE_ARGV_MAP

CollectorFactory = Callable[[ActionCatalogEntry], ArtifactCollector]
CliActionFactory = Callable[[ActionCatalogEntry], ActionBase]


@dataclass(frozen=True)
class CliActionProvider:
    """Process-pack registration for one CLI ACTION."""

    action_cls: Type[CliAction]
    argv_map: Optional[Mapping[str, str]] = None
    collector_factory: Optional[CollectorFactory] = None

    def build(self, entry: ActionCatalogEntry) -> CliAction:
        cli = entry.cli_tool
        if cli is None:
            raise RuntimeError(f"Action {entry.action_name!r} has execution_mode=cli but no cli_tool")
        if self.argv_map is not None:
            argv_map = dict(self.argv_map)
        elif entry.argv_map:
            argv_map = dict(entry.argv_map)
        else:
            argv_map = dict(DEFAULT_PIPELINE_ARGV_MAP)
        if self.collector_factory is not None:
            collector = self.collector_factory(entry)
        else:
            collector = GenericPipelineCollector()
        return self.action_cls(
            entry=entry,
            cli_tool=cli,
            argv_map=argv_map,
            collector=collector,
        )


_CLI_PROVIDERS: Dict[str, CliActionProvider] = {}


def register_cli_provider(
    action_name: str,
    *,
    action_cls: Type[CliAction],
    argv_map: Optional[Mapping[str, str]] = None,
    collector_factory: Optional[CollectorFactory] = None,
) -> None:
    """Register a specialized CLI action for ``action_name`` (idempotent overwrite)."""
    _CLI_PROVIDERS[action_name] = CliActionProvider(
        action_cls=action_cls,
        argv_map=argv_map,
        collector_factory=collector_factory,
    )


def get_cli_provider(action_name: str) -> Optional[CliActionProvider]:
    return _CLI_PROVIDERS.get(action_name)


def list_cli_providers() -> Dict[str, CliActionProvider]:
    return dict(_CLI_PROVIDERS)


def ensure_providers_loaded() -> None:
    """Import action modules so ``register_cli_provider`` side effects run once."""
    # Local imports avoid circular import at package load; each module registers itself.
    from . import (  # noqa: F401
        cell_deconvolution,
        centroid,
        derived_measures,
        detector,
        dmp_select,
        enricher,
        gene_feature_select,
        gene_select,
        info_measures,
        mapper,
        mhb_mhl,
        methylation_confounder_scores,
        residualize_fit,
    )


def build_cli_action(entry: ActionCatalogEntry) -> CliAction:
    """Build a CLI action from the provider registry or a generic ``CliAction``."""
    ensure_providers_loaded()
    provider = get_cli_provider(entry.action_name)
    if provider is not None:
        return provider.build(entry)
    cli = entry.cli_tool
    if cli is None:
        raise RuntimeError(f"Action {entry.action_name!r} has execution_mode=cli but no cli_tool")
    argv_map = dict(entry.argv_map) if entry.argv_map else dict(DEFAULT_PIPELINE_ARGV_MAP)
    return CliAction(
        entry=entry,
        cli_tool=cli,
        argv_map=argv_map,
        collector=GenericPipelineCollector(),
    )
