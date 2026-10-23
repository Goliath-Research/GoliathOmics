"""Build goliath_app ActionSpec rows from runtime catalog entries."""

from __future__ import annotations

import importlib
from typing import List, Optional, Type

from pydantic import BaseModel

from .action_runtime import ActionCatalogEntry, DomainEffects, RUNTIME_CATALOG


def entity_code(action_name: str) -> str:
    return action_name.split(".", 1)[0]


def display_name(action_name: str, description: str) -> str:
    suffix = action_name.split(".", 1)[-1]
    return suffix.replace("_", " ").replace("-", " ").title()


def load_model(module: str, class_name: str) -> Type[BaseModel]:
    mod = importlib.import_module(module)
    cls = getattr(mod, class_name)
    if not isinstance(cls, type) or not issubclass(cls, BaseModel):
        raise TypeError(f"{module}.{class_name} is not a Pydantic model")
    return cls


def _goliath_domain_effects():
    from goliath_app.actions.domain_effects import (
        DomainEffects as GoliathDomainEffects,
        OutputBindingRule,
        TemplateDefaultRule,
        TemplateGroupSideRule,
    )

    return GoliathDomainEffects, OutputBindingRule, TemplateDefaultRule, TemplateGroupSideRule


def convert_domain_effects(de: Optional[DomainEffects]):
    if de is None:
        return None
    GoliathDomainEffects, OutputBindingRule, TemplateDefaultRule, TemplateGroupSideRule = (
        _goliath_domain_effects()
    )
    return GoliathDomainEffects(
        scope_bindings=de.scope_bindings,
        output_bindings=tuple(
            OutputBindingRule(scope_field=b.scope_field, output_json_path=b.output_json_path)
            for b in de.output_bindings
        ),
        template_defaults=tuple(
            TemplateDefaultRule(
                field=t.field,
                scope_var=t.scope_var,
                only_if_absent=t.only_if_absent,
                absent_also=t.absent_also,
            )
            for t in de.template_defaults
        ),
        template_group_side_defaults=tuple(
            TemplateGroupSideRule(side=t.side, field=t.field, scope_var=t.scope_var)
            for t in de.template_group_side_defaults
        ),
    )


def handler_module_path(handler: Optional[str]) -> Optional[str]:
    if not handler:
        return None
    if ":" in handler:
        return handler
    return f"methyl_worker.handlers:{handler}"


def _prime_goliath_catalog_cache() -> None:
    import goliath_app.actions.registry as registry

    if registry._CACHED_CATALOG is None:
        registry._CACHED_CATALOG = []


def runtime_entry_to_spec(entry: ActionCatalogEntry):
    from goliath_app.actions.spec import ActionLevel, ActionSpec

    handler = entry.resolved_in_process_handler()
    return ActionSpec(
        action_name=entry.action_name,
        namespace="omics",
        level=ActionLevel.DOMAIN,
        entity_code=entity_code(entry.action_name),
        display_name=display_name(entry.action_name, entry.description),
        description=entry.description,
        input_model=load_model(entry.input_module, entry.input_class),
        output_model=load_model(entry.output_module, entry.output_class),
        schema_version="1",
        capability=entry.capability,
        tool=entry.tool or entry.action_name,
        action_config_key=entry.action_config_key,
        context_vars=entry.context_vars,
        domain_effects=convert_domain_effects(entry.domain_effects),
        in_process_handler=handler_module_path(handler),
    )


_SKIP_SEMANTIC_FROM_RUNTIME = frozenset({"validation.plan_iterations"})


def build_domain_action_catalog() -> List:
    _prime_goliath_catalog_cache()
    from .omics_core import PLAN_ITERATIONS_ACTION_SPEC

    specs = [
        runtime_entry_to_spec(entry)
        for entry in RUNTIME_CATALOG
        if entry.action_name not in _SKIP_SEMANTIC_FROM_RUNTIME
    ]
    specs.append(PLAN_ITERATIONS_ACTION_SPEC)
    return specs
