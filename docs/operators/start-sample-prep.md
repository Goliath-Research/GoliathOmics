# Start a SamplePrep instance

SamplePrepPipeline is the per-sample workflow that turns paired FASTQs into methylation matrices. The graph is the DomainProgram [`workflow_engine/domain/fixtures/sample_prep.program.json`](../../workflow_engine/domain/fixtures/sample_prep.program.json). An operator starts an instance with `methyl-study-start sample-prep-start`. That command plans the sample list, calls `finalize_instance_context`, compiles the program into a workflow version when the request does not already name one, then creates and starts the instance.

The admin CLI lives in [`workflow_engine/admin/study_start.py`](../../workflow_engine/admin/study_start.py). A release virtualenv exposes it as `methyl-study-start` (`scripts/verify_setup.sh` checks for that script). From a checkout:

```bash
python workflow_engine/admin/study_start.py sample-prep-start request.json
```

The CLI opens the workflow database with the gateway settings (`BACKEND_DB` and `POSTGRES_*` or `AZURE_SQL_*`). The template is [`deploy/env/gateway.postgres.env.example`](../../deploy/env/gateway.postgres.env.example). It imports `rest.db_client` from the methyl-gateway wheel, which is built from GoliathApp. See [Dependency direction](../architecture/dependencies.md).

Portal starts go through `cfg.study_start_request`. `methyl-study-start drain-requests` claims those rows, bakes context from materialized `/work`, and calls `portal.sp_create_and_start_instance`.

## Before the first start

1. The database has the GoliathApp schema for this release and the Omics analyte catalog. The release deploy does that. See [Release](release.md).
2. Workflow definitions are loaded. `scripts/deploy_workflow_definitions.sh` compiles each DomainProgram with `scripts/compile_workflow_program.py` and stores the graph through `wf.wf_repo_create_workflow_graph`. The default keeps instances that already reference a version (`--keep-instances`). `--dry-run` compiles and does not write.
3. At least one worker has enrolled and is polling. See [Worker enrollment](release.md#worker-enrollment).
4. The study manifest exists at `projectPath`. `finalize_instance_context` syncs cfg onto `/work` before it plans.

`compile_workflow_program.py` uses `domain.compiler` from the methyl-gateway wheel. When that import is missing it runs `methyl-workflow-run --compile-only` from `PATH`. This repository does not declare `methyl-workflow-run` in its own `pyproject.toml`.

## Request body

`projectPath` is required. Samples come from `samples`, from `sampleCsv` / `sampleCsvs`, or from `useProjectSamples: true`. When `fastqStorage` is already a wire location (`type` plus `basePath` or `bucket`), the CLI keeps it. Otherwise `apply_storage_to_planner_payload` expands the cfg storage profile (`archiveProfileKey`, `archiveStorageKey`, `storageKey`, or the default archive profile). A profile with no `fastqStorage` raises `KeyError`.

```json
{
  "projectPath": "/work/projects/my-study/configs/project.json",
  "pipelineProcedure": "buffy_wgbs_linear_gene_fc",
  "pipelineProfile": "samd_research",
  "samples": [
    {
      "sampleId": "S1",
      "core_guardrails": { "max_gc_dropout": 6.0 }
    }
  ],
  "fastqStorage": { "type": "file", "basePath": "/data/fastqs" }
}
```

Keys copied from the request onto the planned context before finalize: `alignmentMode`, `pipelineProfile`, `pipelineProcedure`, `libraryProtocol`, `usePangenome`, `useWgbsPangenome`, `actionConfig`, `profilePath`, `procedurePath`, `siteConfigPath`, and `program_path` (also `program` / `programPath`). `--project-path` and `--workflow-version-id` override the file. `disableArchive: true` drops sample and H5 destinations and sets them to JSON `null`.

The command prints JSON with `instance_id`, `workflow_version_id`, `context_json`, and `n_samples`.

## What finalize bakes

`finalize_instance_context` in `workflow_engine/domain/workflow_context.py` runs in this order:

1. Sync the study from cfg onto `/work`.
2. Load the project, site, and profile or procedure, and seed pipeline scope flags (`usePangenome`, `useWgbsPangenome`, and the related mode flags).
3. Enforce process-pack pairing and the study primary analyte.
4. Bind each methylation sample's `sampleDir` to an `align.*` arm leaf.
5. Write one `resolvedConfig__<action>` scope variable per catalog action-config key, including `resolvedConfig__alignment_qc` and `resolvedConfig__extraction_qc`.
6. Set `executionScopeId` to a hash of those slices. `hyperparamSetId` is the same value, kept so older graphs still resolve.

The compiler places `${var.resolvedConfig__alignment_qc}` and `${var.resolvedConfig__extraction_qc}` on the QC tasks, and `${var.executionScopeId}` on every ACTION template.

## Align paths

After `sample.download_fastq`, the program picks one align node. The same choice runs again when alignment QC sets `remediateAlignment` and the sample is trimmed.

| Path | When | Action | Engine knob |
|------|------|--------|-------------|
| Linear Clara Parabricks | `usePangenome` and `useWgbsPangenome` are false, and `actionConfig.parabricks.engine` is `parabricks` (the runner default) | `sample.parabricks_fq2bam` | `METHYL_PARABRICKS_ENGINE`, `METHYL_PARABRICKS_IMAGE` |
| Linear GoliathAlign | Same program node, `actionConfig.parabricks.engine` is `mojo` | `sample.parabricks_fq2bam` | Procedure `buffy_wgbs_linear_mojo_gene_fc` sets this. `align_device` may be `cpu`. |
| Stock Giraffe | `usePangenome` true, `useWgbsPangenome` false | `sample.parabricks_giraffe` | Site `pangenome_genome` |
| methylGrapher | `useWgbsPangenome` true | `sample.methylgrapher_wgbs_align` | `actionConfig.methylgrapher_wgbs.align_engine`: `cpu_vg` (runner default when unset), `gpu_giraffe`, or `mojo_giraffe` |

The epi-GBS program [`sample_prep_epigbs.program.json`](../../workflow_engine/domain/fixtures/sample_prep_epigbs.program.json) aligns with `sample.docker_align` instead of the four actions above.

Arm leaves from `align_arm_for_mode_and_config`:

| Arm | Selected by |
|-----|-------------|
| `align.linear.parabricks` | Linear, engine other than `mojo` |
| `align.linear.mojo` | Linear, `parabricks.engine=mojo` |
| `align.pangenome.parabricks` | Stock pangenome |
| `align.pangenome_wgbs.mojo` | WGBS with `align_engine` `gpu_giraffe` or `mojo_giraffe`, or `engine=mojo` |
| `align.pangenome_wgbs.vg` | WGBS with `align_engine` `cpu_vg`, or with the align engine unset |

## One worker, local scratch

Every align node sets `stageFastqs` to `local`. The align task copies the pair (trimmed FASTQs when they exist) from `/work` into `{METHYL_LOCAL_TMP}/bam/{slug}-{digest}/fastq/` and aligns from those copies. `METHYL_LOCAL_TMP` defaults to `$TMPDIR/methyl-pb`, or `/tmp/methyl-pb` when `TMPDIR` is unset. `digest` is the first 10 hex characters of the SHA-256 of the resolved `sampleDir`.

The BAM is `{METHYL_LOCAL_TMP}/bam/{slug}-{digest}/{sample_id}.bam`. It is not written to `/work`. `sample.parabricks_fq2bam`, `sample.parabricks_giraffe`, `sample.methylgrapher_wgbs_align`, and `sample.docker_align` are exclusive (`max_per_worker` 1) and set `require_previous_worker`. Alignment QC, fragmentomics, both extract actions, extraction QC, and `sample.delete_bam` also set `require_previous_worker`, so they run on the worker that holds the BAM.

`sample.delete_bam` removes that local BAM directory on the pass path and on every reject path. A failing align, QC, fragmentomics, extract, or extraction-QC task removes it as well. Alignment QC that returns `remediateAlignment` removes it on success, because the next align may land on another worker.

Download, trim, archive, and `sample.delete_fastqs` stay unpinned. Archive reads FASTQs from `/work`. `delete_fastqs` then removes those `/work` copies when `deleteFastqs` is set.

MethylExtractor writes under `{METHYL_LOCAL_TMP}/extract/{slug}-{digest}/`. methylGrapher extract does the same (`workDirMode` `local`). On success only `*.h5` and `*.json` are published into `sampleDir`. The local extract directory is removed on success and on failure.

Boot runs `scripts/check_local_scratch.sh` before the worker starts. It refuses a scratch directory on the shared work mount and refuses to start when free space is below `METHYL_LOCAL_TMP_MIN_GB` (default 60). The worker profile `local_disk_gb` default is 200 (minimum 150). At boot the same script deletes `bam/` and `extract/` directories with nothing modified in `METHYL_LOCAL_STALE_HOURS` (default 72; 0 disables the sweep).

If the pinned worker is gone, later pinned tasks for that sample stay unclaimed. Cancel them and start SamplePrep again for that sample. The new align may run on any capable worker.

These BAM-producing align actions are a CAAS opt-out with reason `worker_local_bam`. The BAM is not harvested into `/work/samples/{id}/.caas/`.

## QC window

Alignment QC (`sample.methyl_qc`) and extraction QC (`sample.extraction_qc`) both take the baked task `resolvedConfig`. `resolve_alignment_qc_config` and `resolve_extraction_qc_config` use that object when it is present and do not re-read the site from the worker environment.

A sample may overlay the window. Alignment QC merges `sampleContext.core_guardrails` or `sampleContext.actionConfig.alignment_qc`. Extraction QC merges `sampleContext.guardrails` or `sampleContext.actionConfig.extraction_qc`. The program passes `sampleContext` from the foreach sample (`with.sampleContext` `${var.sample}`).

`qcPass` comes from alignment `guardrails.overall_pass`. `extractionQcPass` comes from extraction `guardrails.overall_pass`. A failed alignment gate can remediate (trim, same align path, QC again) or archive QC-only and delete the BAM. A failed extraction gate archives QC-only and deletes the BAM. `pipeline.info_measures` is a later study-lifecycle action, not a SamplePrep node.

Mode-specific artifact rules and the published guardrail keys are in [Sample prep and QC](../usage/03-sample-prep-and-qc.md) and [Alignment engines](../usage/alignment-engines.md). The storage contract in [`workflow_engine/sql_mssql/SamplePrepFlow.md`](../../workflow_engine/sql_mssql/SamplePrepFlow.md) matches the paths above.
