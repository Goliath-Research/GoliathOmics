# Pipeline config JSON Schemas

Committed JSON Schema artifacts generated from Pydantic config models (source of truth).

## Regenerate

From the repository root with `.venv` activated:

```bash
methyl-export-config-schemas
# or
./scripts/export_config_schemas.sh
```

## Drift check (CI / pre-commit)

Fails when models changed but schemas were not regenerated:

```bash
methyl-export-config-schemas --check
```

Pytest also enforces this: `packages/methylvalidation/tests/test_config_schema_export.py`.

## Layout

| File | Pydantic model |
|------|----------------|
| `project_config.schema.json` | `methyl_utils.pipeline_config.ProjectConfig` |
| `validation.schema.json` | `methyl_validation.config.ValidationStepConfig` (`step_config.validation`) |
| `validation_monte_carlo.schema.json` | `methyl_validation.config.MonteCarloConfig` (standalone `--config`) |
| `progression.schema.json` | `methyl_disease_progression.config.ProgressionStepConfig` |
| `validation_regulatory.schema.json` | `RegulatoryLifecycleConfig` |
| `validation_partitions.schema.json` | `ValidationPartitionContract` |
| `validation_backend_profiles.schema.json` | `BackendProfilesConfig` |
| `detection.schema.json` | `MethylDetectorConfig` |
| `mapper.schema.json` | `MapperStepConfig` |
| `enricher.schema.json` | `EnricherStepConfig` |
| `classifier.schema.json` | `ClassificationConfig` |
| `predictor.schema.json` | `PredictorConfig` |
| `alignment_qc.schema.json` | `AlignmentQCConfig` |
| `study_action_config_overlay.schema.json` | `StudyActionConfigOverlay` (study GET `schema_id` alias; overlay Guardrails editor) |
| `sample_prep_guardrails_overlay.schema.json` | `StudyActionConfigOverlay` (profile / procedure overlay type name) |
| `sample_prep_guardrails.schema.json` | `SamplePrepGuardrails` (site full published window; no sample paths) |
| `centroid.schema.json` | `MethylCentroidConfig` |
| `queue_discovery_task_v1.schema.json` | `DiscoveryRunTaskV1` |
| `alignment_qc/exported_sample_qc*.schema.json` | AlignmentQC export payloads |
| `storage_transfer.schema.json` | `StorageTransferStepConfig` |
| `methyl_extract.schema.json` | `methyl_utils.methyl_extract_config.MethylExtractStepConfig` |

Registry: `packages/methylvalidation/methyl_validation/config_schema_registry.py`.
