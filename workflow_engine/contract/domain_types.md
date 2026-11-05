# Domain types for workflow scope variables

The workflow engine stores **opaque JSON** in `wf.scope_variable.value_json`. The **domain layer** (`packages/methyldomain`) defines typed objects that clients, compilers, and workers agree on.

## Tagged JSON convention

Every domain value is a JSON object with a **`"$type"`** discriminator:

```json
{
  "$type": "MethylSampleRef",
  "sampleId": "DPLST-001",
  "sampleDir": "/work/samples/DPLST-001",
  "bamPath": "/tmp/methyl-pb/bam/DPLST-001-3f9a1c2b7e/DPLST-001.bam"
}
```

`bamPath` is a worker-local path on the align worker (`METHYL_LOCAL_TMP`), never on `/work`.
Only actions pinned to that worker (`dispatch.require_previous_worker`) may read it.

- Pydantic models use `Field(alias="$type")` for serialization.
- Use `parse_domain_value()` / `to_tagged_json()` from `methyl_domain.types`.
- JSON Schemas live under `schemas/domain/` (export via `methyl-export-domain-schemas`).

FOREACH flattening and `${var.*}` templates work unchanged: domain objects are ordinary JSON objects in scope.

## Type catalog

| `$type` | Purpose |
|---------|---------|
| `MethylIngestRef` | Pre-download structured `fastqSource` / file count |
| `MethylSampleRef` | Sample lifecycle handle (grows through prep) |
| `AlignmentQcRef` | QC JSON path + pass gate |
| `ExtractionQcRef` | Post-extraction QC JSON path + pass gate |
| `FragmentomicsRef` | cfDNA fragmentomics artifacts |
| `MethylationMatrixRef` | Post-extract HDF5 matrix handles |
| `MethylGroup` | Cohort (static project group or MC train/val draw) |
| `CentroidSeedGroup` | Shared MC centroid seed pool per cohort label (`_centroid_seed/{label}/`) |
| `MethylCentroidRef` | Centroid HDF5 per group × chr × ctx |
| `ComparisonSpec` | Control vs disease pair |
| `MethylDetectionRef` | Detector run summary (paths, not full DMP table) |
| `StratifiedCohortDraw` | One Monte Carlo stratified subsample iteration |

See `schemas/domain/registry.json` for schema file names.

## Sample prep state machine

Actions in `workers/methyl_worker/action_catalog.py` declare **`domain_effects`** (exported to `schemas/actions/catalog.json`):

| Action | Reads | Writes on scope |
|--------|-------|-----------------|
| `sample.download_fastq` | `MethylIngestRef` | `MethylSampleRef.fastqFiles` |
| `sample.parabricks_fq2bam` | `MethylSampleRef` | `bamPath`, `metricsJson` |
| `sample.methyl_qc` | `MethylSampleRef` | `alignmentQc`; binds `qcPass` bool |
| `sample.extraction_qc` | `MethylSampleRef` | `extractionQc`; binds `extractionQcPass` bool |
| `sample.fragmentomics` | `MethylSampleRef` | `fragmentomics` |
| `sample.methyl_extract` | `MethylSampleRef` | `methylation` |
| `pipeline.centroid` | `MethylGroup` | `MethylCentroidRef` |
| `pipeline.detector` | `ComparisonSpec` + centroids | `MethylDetectionRef` |
| `validation.plan_iterations` | project groups | `centroidSeedGroups[]`, `iterations[]` as `StratifiedCohortDraw` with `centroidGroups` |

Optional worker adapter: `methyl_domain.helpers.enrich_sample_prep_output()` merges handler `output_json` into a `MethylSampleRef`.

After successful SamplePrep, downstream code resolves HDF5 paths via `resolve_methylation_h5_path(sample_ref, chromosome, context)` and loads GPU-capable data with `load_methyl_sample_from_ref()` (wraps `MethylSample.load_from_h5` in methylutils).

## DomainProgram IR and compiler

**IR schema:** `schemas/domain/domain_program.schema.json` (v1 phases + v2 statement `body`)

Clients author a declarative **DomainProgram** (`for`, `parallel`, `do`/`with`, `if`). The compiler lowers it to engine artifacts:

```
DomainProgram  →  compile_domain_program()
                    ├─ WorkflowDefinitionSpec  (POST /v1/workflows/definitions)
                    ├─ collection_bindings     (engine resolves project.* → scope arrays)
                    └─ context_json            (minimal: { projectPath })
```

At **`sp_start_workflow_instance`**, the engine runs `wf_resolve_collection_bindings` (generic JSON file + JSONPath extraction) before FOREACH activation. Workers receive concrete `input_json` per chromosome/context/group unit.

Implementation: `workflow_engine/domain/compiler.py`

Example fixtures:
- `workflow_engine/domain/fixtures/sample_prep.program.json`
- `workflow_engine/domain/fixtures/two_group_comparison.program.json`

## Monte Carlo iterations

`methyl_validation.workflow_planner` emits:

- Top-level **`centroidSeedGroups[]`** (`CentroidSeedGroup`) — full per-cohort sample pools under `{monteCarloRunsRoot}/_centroid_seed/`.
- Each **`iterations[]`** element as a tagged **`StratifiedCohortDraw`**, with typed `taskConfig` (`McIterationTaskConfig`) and **`centroidGroups[]`** (`CentroidGroupScope`) using cohort-relative deltas and `centroidSeedDir`.

Helpers:

- `groups_from_mc_run_dir()` — train/val CSV paths per cohort for a run directory
- `build_stratified_cohort_draw()` — assemble tagged iteration object
- `MethylGroup_from_project()` — resolve a static group from `project.json`

## Invariants

1. **Engine agnostic** — no domain types in SQL schema or CHECK constraints.
2. **Storage authoritative** — HDF5/JSON on `/work/`; scope holds references only.
3. **Backward compatible** — flat scope vars (`sampleId`, `qcPass`, …) remain during migration; compiler may emit dual bindings.
