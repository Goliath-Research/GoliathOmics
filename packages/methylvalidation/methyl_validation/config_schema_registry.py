"""
Registry of pipeline Pydantic config models and their committed JSON Schema artifact paths.

Pydantic models are the source of truth; schemas under ``schemas/config/`` are generated artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import List, Sequence, Type

from pydantic import BaseModel


@dataclass(frozen=True)
class ConfigSchemaSpec:
    """One config model and its committed JSON Schema file (relative to repo ``schemas/config/``)."""

    schema_id: str
    module: str
    class_name: str
    filename: str
    title: str | None = None

    def load_model(self) -> Type[BaseModel]:
        mod = import_module(self.module)
        model = getattr(mod, self.class_name, None)
        if model is None:
            raise AttributeError(f"{self.module}.{self.class_name} not found")
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError(f"{self.module}.{self.class_name} is not a Pydantic BaseModel")
        return model


# Committed artifact root: <repo>/schemas/config/
CONFIG_SCHEMA_SPECS: Sequence[ConfigSchemaSpec] = (
    ConfigSchemaSpec(
        schema_id="project",
        module="methyl_utils.pipeline_config",
        class_name="ProjectConfig",
        filename="project_config.schema.json",
        title="ProjectConfig",
    ),
    ConfigSchemaSpec(
        schema_id="validation_monte_carlo",
        module="methyl_validation.config",
        class_name="MonteCarloConfig",
        filename="validation_monte_carlo.schema.json",
        title="MonteCarloConfig",
    ),
    ConfigSchemaSpec(
        schema_id="validation_step",
        module="methyl_validation.config",
        class_name="ValidationStepConfig",
        filename="validation.schema.json",
        title="ValidationStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="validation_regulatory",
        module="methyl_validation.config",
        class_name="RegulatoryLifecycleConfig",
        filename="validation_regulatory.schema.json",
        title="RegulatoryLifecycleConfig",
    ),
    ConfigSchemaSpec(
        schema_id="validation_partitions",
        module="methyl_validation.config",
        class_name="ValidationPartitionContract",
        filename="validation_partitions.schema.json",
        title="ValidationPartitionContract",
    ),
    ConfigSchemaSpec(
        schema_id="validation_backend_profiles",
        module="methyl_validation.config",
        class_name="BackendProfilesConfig",
        filename="validation_backend_profiles.schema.json",
        title="BackendProfilesConfig",
    ),
    ConfigSchemaSpec(
        schema_id="hyperparam_search_request",
        module="methyl_validation.hyperparam_models",
        class_name="HyperparamSearchRequest",
        filename="hyperparam_search_request.schema.json",
        title="HyperparamSearchRequest",
    ),
    ConfigSchemaSpec(
        schema_id="hyperparam_grid_spec",
        module="methyl_validation.hyperparam_models",
        class_name="HyperparamGridSpec",
        filename="hyperparam_grid_spec.schema.json",
        title="HyperparamGridSpec",
    ),
    ConfigSchemaSpec(
        schema_id="hyperparam_trial_overlay",
        module="methyl_validation.hyperparam_models",
        class_name="HyperparamTrialOverlay",
        filename="hyperparam_trial_overlay.schema.json",
        title="HyperparamTrialOverlay",
    ),
    ConfigSchemaSpec(
        schema_id="hyperparam_scenario_request",
        module="methyl_validation.hyperparam_models",
        class_name="HyperparamScenarioRequest",
        filename="hyperparam_scenario_request.schema.json",
        title="HyperparamScenarioRequest",
    ),
    ConfigSchemaSpec(
        schema_id="hyperparam_search_status",
        module="methyl_validation.hyperparam_models",
        class_name="HyperparamSearchStatus",
        filename="hyperparam_search_status.schema.json",
        title="HyperparamSearchStatus",
    ),
    ConfigSchemaSpec(
        schema_id="detection",
        module="methyl_detector.models.config",
        class_name="MethylDetectorConfig",
        filename="detection.schema.json",
        title="MethylDetectorConfig",
    ),
    ConfigSchemaSpec(
        schema_id="mapper",
        module="methyl_mapper.config",
        class_name="MapperStepConfig",
        filename="mapper.schema.json",
        title="MapperStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="enricher",
        module="methyl_enricher.config",
        class_name="EnricherStepConfig",
        filename="enricher.schema.json",
        title="EnricherStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="library_presets",
        module="methyl_enricher.preset_registry",
        class_name="LibraryPresetCatalog",
        filename="library_presets.schema.json",
        title="LibraryPresetCatalog",
    ),
    ConfigSchemaSpec(
        schema_id="classifier",
        module="methyl_classifier.models.config_schema",
        class_name="ClassificationConfig",
        filename="classifier.schema.json",
        title="ClassificationConfig",
    ),
    ConfigSchemaSpec(
        schema_id="predictor",
        module="methyl_predictor.models.config",
        class_name="PredictorConfig",
        filename="predictor.schema.json",
        title="PredictorConfig",
    ),
    ConfigSchemaSpec(
        schema_id="alignment_qc",
        module="methyl_alignment_qc.models.config",
        class_name="AlignmentQCConfig",
        filename="alignment_qc.schema.json",
        title="AlignmentQCConfig",
    ),
    ConfigSchemaSpec(
        schema_id="study_action_config_overlay",
        module="methyl_utils.study_action_config",
        class_name="StudyActionConfigOverlay",
        filename="study_action_config_overlay.schema.json",
        title="StudyActionConfigOverlay",
    ),
    ConfigSchemaSpec(
        schema_id="sample_prep_guardrails_overlay",
        module="methyl_utils.study_action_config",
        class_name="StudyActionConfigOverlay",
        filename="sample_prep_guardrails_overlay.schema.json",
        title="SamplePrepGuardrailsOverlay",
    ),
    ConfigSchemaSpec(
        schema_id="sample_prep_guardrails",
        module="methyl_utils.study_action_config",
        class_name="SamplePrepGuardrails",
        filename="sample_prep_guardrails.schema.json",
        title="SamplePrepGuardrails",
    ),
    ConfigSchemaSpec(
        schema_id="fragmentomics",
        module="methyl_fragmentomics.config",
        class_name="FragmentomicsStepConfig",
        filename="fragmentomics.schema.json",
        title="FragmentomicsStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="derived_measures",
        module="methyl_derived_measures.config",
        class_name="DerivedMeasuresStepConfig",
        filename="derived_measures.schema.json",
        title="DerivedMeasuresStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="cell_deconvolution",
        module="methyl_deconv.config",
        class_name="CellDeconvStepConfig",
        filename="cell_deconvolution.schema.json",
        title="CellDeconvStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="residualize",
        module="methyl_utils.residualize_config",
        class_name="ResidualizeStepConfig",
        filename="residualize.schema.json",
        title="ResidualizeStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="methylation_confounder_scores",
        module="methyl_utils.residualize_config",
        class_name="ConfounderScoresStepConfig",
        filename="methylation_confounder_scores.schema.json",
        title="ConfounderScoresStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="info_measures",
        module="methyl_infotheory.config",
        class_name="InfoTheoryStepConfig",
        filename="info_measures.schema.json",
        title="InfoTheoryStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="mhb_mhl",
        module="methyl_mhl.config",
        class_name="MhbMhlStepConfig",
        filename="mhb_mhl.schema.json",
        title="MhbMhlStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="centroid",
        module="methyl_centroid.config",
        class_name="MethylCentroidConfig",
        filename="centroid.schema.json",
        title="MethylCentroidConfig",
    ),
    ConfigSchemaSpec(
        schema_id="progression",
        module="methyl_disease_progression.config",
        class_name="ProgressionStepConfig",
        filename="progression.schema.json",
        title="ProgressionStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="queue_discovery_task",
        module="methyl_validation.task_schema",
        class_name="DiscoveryRunTaskV1",
        filename="queue_discovery_task_v1.schema.json",
        title="DiscoveryRunTaskV1",
    ),
    ConfigSchemaSpec(
        schema_id="alignment_qc_export_v1",
        module="methyl_alignment_qc.models.sample_qc",
        class_name="ExportedSampleQCPayload",
        filename="alignment_qc/exported_sample_qc.schema.json",
        title="ExportedSampleQCPayload",
    ),
    ConfigSchemaSpec(
        schema_id="alignment_qc_export_v2",
        module="methyl_alignment_qc.models.sample_qc_v2",
        class_name="ExportedSampleQCV2Payload",
        filename="alignment_qc/exported_sample_qc_v2.schema.json",
        title="ExportedSampleQCV2Payload",
    ),
    ConfigSchemaSpec(
        schema_id="test_data_registry",
        module="methyl_utils.test_data_registry",
        class_name="TestDataRegistry",
        filename="test_data_registry.schema.json",
        title="TestDataRegistry",
    ),
    ConfigSchemaSpec(
        schema_id="storage_transfer",
        module="methyl_domain.storage_transfer_config",
        class_name="StorageTransferStepConfig",
        filename="storage_transfer.schema.json",
        title="StorageTransferStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="methyl_extract",
        module="methyl_utils.methyl_extract_config",
        class_name="MethylExtractStepConfig",
        filename="methyl_extract.schema.json",
        title="MethylExtractStepConfig",
    ),
    ConfigSchemaSpec(
        schema_id="rna_qc",
        module="rna_alignment_qc.models.config",
        class_name="RnaQcGuardrailConfig",
        filename="rna_qc.schema.json",
        title="RnaQcGuardrailConfig",
    ),
    ConfigSchemaSpec(
        schema_id="rna_de_select",
        module="rna_express.models.config",
        class_name="RnaDeSelectConfig",
        filename="rna_de_select.schema.json",
        title="RnaDeSelectConfig",
    ),
    ConfigSchemaSpec(
        schema_id="proteomics_qc",
        module="proteomics_qc.models.config",
        class_name="ProteomicsQcGuardrailConfig",
        filename="proteomics_qc.schema.json",
        title="ProteomicsQcGuardrailConfig",
    ),
    ConfigSchemaSpec(
        schema_id="protein_de_select",
        module="proteomics_features.models.config",
        class_name="ProteinDeSelectConfig",
        filename="protein_de_select.schema.json",
        title="ProteinDeSelectConfig",
    ),
)


def list_config_schema_specs() -> List[ConfigSchemaSpec]:
    return list(CONFIG_SCHEMA_SPECS)
