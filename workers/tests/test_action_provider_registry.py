"""Tests for catalog-driven CLI action provider registry."""

from __future__ import annotations

from methyl_worker.action_catalog import ACTION_CATALOG, find_catalog_entry
from methyl_worker.actions.base import CliAction, build_action_from_catalog
from methyl_worker.actions.centroid import CentroidCliAction
from methyl_worker.actions.detector import DetectorCliAction
from methyl_worker.actions.registry import ensure_providers_loaded, list_cli_providers
import methyl_worker.handlers as handlers_mod


def test_specialized_cli_actions_are_registered() -> None:
    ensure_providers_loaded()
    providers = list_cli_providers()
    assert "pipeline.centroid" in providers
    assert "pipeline.detector" in providers
    assert "pipeline.enricher" in providers


def test_build_action_from_catalog_uses_registry() -> None:
    centroid = find_catalog_entry("pipeline.centroid")
    assert centroid is not None
    action = build_action_from_catalog(centroid, handlers_mod)
    assert isinstance(action, CentroidCliAction)

    detector = find_catalog_entry("pipeline.detector")
    assert detector is not None
    action = build_action_from_catalog(detector, handlers_mod)
    assert isinstance(action, DetectorCliAction)


def test_generic_cli_actions_use_base_cli_action() -> None:
    """Actions without a specialized provider still build a CliAction."""
    entry = find_catalog_entry("pipeline.classifier")
    assert entry is not None
    ensure_providers_loaded()
    assert entry.action_name not in list_cli_providers()
    action = build_action_from_catalog(entry, handlers_mod)
    assert isinstance(action, CliAction)
    assert type(action) is CliAction


def test_centroid_and_detector_export_template_rules() -> None:
    centroid = find_catalog_entry("pipeline.centroid")
    assert centroid is not None
    assert centroid.domain_effects is not None
    sides = {r.side for r in centroid.domain_effects.template_group_side_defaults}
    assert sides == {"control", "disease"}

    detector = find_catalog_entry("pipeline.detector")
    assert detector is not None
    assert detector.domain_effects is not None
    fields = {r.field for r in detector.domain_effects.template_defaults}
    assert {"centroid1Dir", "centroid2Dir", "outputDir", "comparison"} <= fields

    dmp = find_catalog_entry("pipeline.dmp_select")
    assert dmp is not None
    assert dmp.domain_effects is not None
    assert dmp.domain_effects.template_defaults == ()


def test_catalog_export_includes_template_metadata() -> None:
    detector = find_catalog_entry("pipeline.detector")
    assert detector is not None
    payload = detector.to_catalog_dict()
    de = payload["domain_effects"]
    assert any(t["field"] == "outputDir" for t in de["template_defaults"])


def test_all_in_process_handlers_resolvable_on_handlers_package() -> None:
    for entry in ACTION_CATALOG:
        if entry.execution_mode != "in_process":
            continue
        name = entry.resolved_in_process_handler()
        assert name and hasattr(handlers_mod, name), name
