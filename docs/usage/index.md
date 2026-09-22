# MethylPipeline Usage Manual

## What this manual is

This is the operational companion to the theory book. It is designed for execution teams who need:

- exact commands,
- required config keys,
- expected output artifacts,
- stage handoff checks,
- recovery actions when a run fails.

For statistical background, use [`docs/theory/`](../theory/index.md). For compiler and worker internals, use [`docs/implementation/`](../implementation/index.md). Start with [Relationship to Theory and Implementation](00-relationship-to-theory-and-implementation.md).

## How to run staged validation

**Use `methyl-workflow-run`** with a DomainProgram and pipeline profile. That is the canonical path for new studies, local runs, and production gateway workers.

**`methyl-validation --stability/--freeze/--model` is legacy** — documented for transitional scripts and narrow recovery, not for new workflow design. See [Orchestration: workflow-run](04-orchestration-workflow-run.md) before the stage chapters.

| Goal | Start here |
|------|------------|
| Understand entry points | [Orchestration (ch.04)](04-orchestration-workflow-run.md) |
| First end-to-end run | [Tutorial (ch.16)](16-tutorial-healthy-vs-cancer-stages.md) |
| SaMD holdouts / pivotal path | [SaMD lifecycle (ch.18)](18-samd-study-lifecycle.md) |
| One stage's artifacts and gates | Part II (ch.05–09) — each chapter links back to workflow-run |
| Legacy CLI flags | `packages/methylvalidation/docs/USAGE.md` |

## Reading paths

- **Sample prep (FASTQ → HDF5):** Chapter `03` (`03-sample-prep-and-qc.md`).
- **New project setup:** Chapters `01`, `02`, `04` (orchestration), then `05` or [Tutorial ch.16](16-tutorial-healthy-vs-cancer-stages.md).
- **Fast path (stage execution):** [Orchestration ch.04](04-orchestration-workflow-run.md) → `methyl-workflow-run`; stage detail in ch.05–09.
- **Guided tutorial (healthy vs. cancer stages):** Chapter `16` — end-to-end walkthrough; [profile catalog in ch.16](16-tutorial-healthy-vs-cancer-stages.md#step-4-pick-a-profile).
- **SaMD study ladder (research → holdouts → pivotal):** Chapter `18` — [`18-samd-study-lifecycle.md`](18-samd-study-lifecycle.md); scaffold with `methyl-study-init`.
- **Application packs (config on a process):** Chapter `24` — pattern + checklist; instances in ch.21 (Alzheimer), ch.23 (plant abiotic stress), and [ch.25 (prostate cancer options)](25-prostate-cancer-pack.md).
- **Prostate cancer (research + diagnostic options):** Chapter `25` — [`25-prostate-cancer-pack.md`](25-prostate-cancer-pack.md); physician/payer narrative in the [application deep-dive](../research/Prostate_Cancer_Application_Deep_Dive.md).
- **Hyperparameter versioning (CAAS):** `17-content-addressed-action-store.md` (Part V; follows the tutorial).
- **Daily operations:** `10-artifacts-and-qa-checks.md` through `12-command-cookbook.md`.
- **Scaling MC across machines:** Chapter `13` — shared `/work` storage and queue subcommands.
- **Deployment (DB + gateway + workers):** Chapter `14`.
- **Optional hyperparameter tuning:** Chapter `15` — `methyl-hyperparam-search` grid driver.
- **Standalone package usage:** Chapter `04b` (`04b-package-reference-individual-usage.md`).

## End-to-end workflow map

```mermaid
flowchart LR
  prep["Sample prep FASTQ to HDF5"]
  qc["Alignment + extraction QC"]
  stability["MC stability centroid detector"]
  freeze["Freeze fixed panel mapper enricher"]
  model["Model train + predictor"]
  validation["Post-model validation"]
  blind["Blind prediction"]

  prep --> qc --> stability --> freeze --> model --> validation --> blind
```

*End-to-end pipeline stages*


## Scope boundaries

- **SamplePrepPipeline** (FASTQ → aligned BAM → methylation HDF5) runs before staged validation; see Chapter 3.
- **Canonical orchestration:** `methyl-workflow-run` + DomainProgram + profile — [chapter 04](04-orchestration-workflow-run.md).
- **Legacy orchestration:** `methyl-validation --stability/--freeze/--model` — transitional scripts and recovery only; do not use for new studies.
- Blind-only prediction is documented as a standalone `methyl-predictor` operation.
- Package behavior is documented from implementation in `packages/*`.

## Canonical references

- Sample prep workflow: [`workflow_engine/sql_mssql/SamplePrepFlow.md`](../../workflow_engine/sql_mssql/SamplePrepFlow.md)
- DomainProgram language: [`docs/reference/domain-program-language.md`](../reference/domain-program-language.md)
- Pipeline stages diagram: [`docs/architecture/pipeline-stages.md`](../architecture/pipeline-stages.md)
- Validation orchestration: `packages/methylvalidation/docs/USAGE.md`
- Distributed queue: `packages/methylvalidation/docs/DISTRIBUTED_QUEUE.md`
- Hyperparameter search: `packages/methylvalidation/docs/HYPERPARAMETER_SEARCH.md`
