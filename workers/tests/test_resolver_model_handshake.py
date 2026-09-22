"""Cross-package resolver -> config-model handshake meta-test.

Locks the contract between the config resolver's output slice (upstream of every
action) and each action's typed config Pydantic model. A field rename on either
side -- resolver key or model field -- fails loudly here.

Scope: the actions whose config model *is* the ``actionConfig`` slice model
(all-optional step-config models). Actions whose CLI config model also carries
runtime identity fields (centroid ``chrom``/``ctx``, detector ``chromosome``,
classifier/predictor ``output_dir``, alignment_qc ``sample_path``) are excluded
because those fields come from context_vars, not the resolvable actionConfig
slice -- validating the slice alone against the full model is meaningless there.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from methyl_utils.action_config_resolver import resolve_action_config

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROFILE = (
    _REPO_ROOT
    / "workflow_engine"
    / "domain"
    / "profiles"
    / "mc_gene_fc.profile.json"
)

# action_config_key -> "module:Class" of the actionConfig-slice model.
SLICE_MODELS: dict[str, str] = {
    "mapper": "methyl_mapper.config:MapperStepConfig",
    "enricher": "methyl_enricher.config:EnricherStepConfig",
    "gene_selection": "methyl_gene_select.models.config:GeneSelectionConfig",
    "validation": "methyl_validation.config:ValidationStepConfig",
    "progression": "methyl_disease_progression.config:ProgressionStepConfig",
    "fragmentomics": "methyl_fragmentomics.config:FragmentomicsStepConfig",
    "derived_measures": "methyl_derived_measures.config:DerivedMeasuresStepConfig",
    "cell_deconvolution": "methyl_deconv.config:CellDeconvStepConfig",
    "info_measures": "methyl_infotheory.config:InfoTheoryStepConfig",
    "mhb_mhl": "methyl_mhl.config:MhbMhlStepConfig",
}


def _load_model(spec: str):
    module_name, class_name = spec.split(":", 1)
    return getattr(importlib.import_module(module_name), class_name)


def _profile_action_config() -> dict:
    return json.loads(_PROFILE.read_text(encoding="utf-8")).get("actionConfig", {})


def test_slice_model_keys_are_real_catalog_keys():
    """Every mapped key must be a live catalog action_config_key."""
    from methyl_worker.action_catalog import PROJECT_ACTION_CONFIG_KEYS

    unknown = sorted(set(SLICE_MODELS) - set(PROJECT_ACTION_CONFIG_KEYS))
    assert unknown == [], f"SLICE_MODELS references non-catalog keys: {unknown}"


@pytest.mark.parametrize("action_key", sorted(SLICE_MODELS))
def test_empty_resolved_slice_validates(action_key: str):
    """The default (empty profile/site) resolved slice must satisfy the model.

    This is the config-not-code guarantee: tunable knobs default to None, so an
    empty slice is always model-valid.
    """
    model = _load_model(SLICE_MODELS[action_key])
    slice_ = resolve_action_config(action_key, profile_action_config={}, site={})
    model.model_validate(slice_)


@pytest.mark.parametrize("action_key", sorted(SLICE_MODELS))
def test_profile_resolved_slice_validates_and_keys_are_declared(action_key: str):
    """Resolve a real profile section and validate the handshake key-by-key.

    Beyond validation, every scalar key the resolver emits must be a declared
    field on the model. Step-config models use ``extra='ignore'``, which would
    silently drop a renamed/removed field -- this assertion turns that into a
    loud failure.
    """
    model = _load_model(SLICE_MODELS[action_key])
    section = _profile_action_config().get(action_key, {})
    slice_ = resolve_action_config(
        action_key, profile_action_config={action_key: section}, site={}
    )
    model.model_validate(slice_)

    scalar_keys = {
        k for k, v in slice_.items() if not isinstance(v, (dict, list))
    }
    undeclared = sorted(k for k in scalar_keys if k not in model.model_fields)
    assert undeclared == [], (
        f"{action_key}: resolver emitted keys not declared on "
        f"{model.__name__}: {undeclared}"
    )
