# SamplePrepPipeline — per-sample upstream workflow

**SamplePrepPipeline** is the **universal entry point** for every sample entering MethylPipeline: cfDNA or buffy-coat, from any structured laboratory `fastqSource` (file / S3 / Azure Blob). It orchestrates ingest, alignment (**native-Mojo** methylGrapher on NVIDIA/AMD for `pangenome_wgbs`, or **explicit** Clara Parabricks for `linear`/`pangenome`), **mode-aware alignment QC** (with optional fastp remediation), optional cfDNA fragmentomics, methylation extraction (CPU MethylExtractor or native-Mojo MethylCall), **extraction QC**, optional HDF5 archive, and cleanup **per sample** — in parallel — before **DataDrivenPipeline** consumes `{chrom}-CG.h5` files.

**Source of truth (workflow graph):** [`../domain/fixtures/sample_prep.program.json`](../domain/fixtures/sample_prep.program.json)

**Implementation detail:** [`docs/implementation/sample-preparation-flow.md`](../../docs/implementation/sample-preparation-flow.md) — guardrail boundaries, focused FASTP remediation, extraction filtering, cfDNA fragmentomics.

**Deploy:** `bash scripts/deploy_workflow_definitions.sh` (direct DB)

**Integrity verify (git + optional Azure):**

```bash
# Graph / catalog / methylgrapher bake (site + Buffy procedure)
methyl-cfg verify-workflow \
  --program workflow_engine/domain/fixtures/sample_prep.program.json

# Also compare compiled ACTION nodes to active wf.workflow_version
set -a; source /work/goliath/env/gateway.env; set +a
methyl-cfg verify-workflow --check-db
```

**Legacy SQL seed** [`deprecated/wf_sample_prep_pipeline_seed.sql`](deprecated/wf_sample_prep_pipeline_seed.sql) is **deprecated**; use DomainProgram deploy above.

## Worker affinity (soft, catalog-driven)

Sample stickiness is **not** hard-wired into the engine or DomainProgram IR. Sample-scoped
catalog actions declare `dispatch.affinity_key_field: "sampleId"` plus
`prefer_previous_worker` / `prefer_continue_group` (see
[`docs/architecture/action-provider-registry.md`](../../docs/architecture/action-provider-registry.md)).
GPU Align (`sample.parabricks_fq2bam`, giraffe, methylGrapher WGBS, rna_fq2bam) also
sets `exclusive_worker` and `max_per_worker: 1` so **one Clara/Mojo align per VM**.
The claim SP then:

1. Prefers continuing an affinity key that already has `SUCCEEDED` work (QC/extract after align)
   over starting a fresh sample’s first step.
2. Soft-prefers the worker that last completed that key when it is idle and capable; otherwise
   any capable worker may claim (shared `/work` remains the correctness path).
3. Refuses a second claim while an exclusive Align is leased — do **not** park QC/extract/trim
   at `PENDING` to steer GPUs.

`wf` only sees opaque keys and catalog flags — no SamplePrep node names in SQL.

**FOREACH (parallel samples):** one sample `FAILED` does **not** fail the instance.
Siblings stay `READY`/`RUNNING`. After every iteration is terminal (succeeded, skipped
after fail_task, or cancelled), the FOREACH succeeds and the instance can **COMPLETED**.
Missing FASTQ, disqualified samples, and QC fails must not leave the instance `RUNNING`.

**Archive:** `${var.sampleDestination}` always resolves (JSON `null` when no archive profile). Full-archive `rejectReason` is JSON `null` (QC-fail archives set a literal reason).
The handler skips upload when destination is null. Do not omit the key from `samples[]`.

## Architecture

```text
Engine (generic)                    Workflow definition
─────────────────                   ─────────────────────
SEQUENCE / IF / FOREACH             SamplePrepPipeline (DomainProgram)
scope_variable / placeholders       FOREACH samples (parallel=true)
worker_action + capabilities        ACTION nodes + two QC gates

Instance (project-specific)         Performance stack
─────────────────────────           ─────────────────
context_json: samples[],            native-Mojo methylGrapher (pangenome_wgbs: NVIDIA CUDA / AMD HIP)
  fastqStorage, projectPath           Clara Parabricks (explicit linear / stock Giraffe only)
                                    fastp (R2 trim remediation)
                                    MethylExtractor (GPU) or native-Mojo MethylCall/MergeCpG
```

## Workflow tree

Program `IF` order checks **`useWgbsPangenome` before `usePangenome`** (WGBS methylGrapher never falls through to stock Giraffe). See [`sample_prep.program.json`](../domain/fixtures/sample_prep.program.json).

```text
SEQUENCE root
└─ FOREACH samples (parallel)
   └─ SEQUENCE one_sample
      ├─ ACTION download_fastq       ← laboratory fastqSource → local sampleDir
      ├─ IF useWgbsPangenome
      │    └─ THEN methylgrapher_wgbs_align   ← methylGrapher C2T+G2A → QC BAM
      │    ELSE IF usePangenome
      │         └─ THEN parabricks_giraffe    ← GPU vg Giraffe (stock HPRC → GRCh38 surjection)
      │         ELSE parabricks_fq2bam        ← GPU linear WGBS align (fq2bam_meth)
      ├─ ACTION methyl_qc            ← alignment guardrail #1
      │     binds: qcPass, qcDisposition, trimFront1/2, trimTail1/2, remediateAlignment
      └─ IF qcPass
         ├─ THEN (pass path)
         │    ├─ [IF isCfdna → fragmentomics]
         │    ├─ IF useWgbsPangenome → methylgrapher_wgbs_extract
         │    │  ELSE methyl_extract
         │    ├─ extraction_qc       ← extraction guardrail #2
         │    └─ IF extractionQcPass
         │         ├─ archive_sample mode=full (when sampleDestination configured)
         │         ├─ [IF deleteFastqs → delete_fastqs]
         │         └─ delete_bam
         │       ELSE → archive_sample mode=qc_only → [delete_fastqs] → delete_bam → qc_failed
         └─ ELSE (alignment fail)
            └─ IF remediateAlignment
               ├─ trim_fastq (fastp, read-end trim from screening)
               ├─ same align branch as above (methylgrapher / giraffe / fq2bam, forceRealign)
               ├─ methyl_qc retry (attempt 2)
               └─ IF qcPass → same pass path as above
                  ELSE → archive_sample mode=qc_only → [delete_fastqs] → delete_bam → qc_failed
            ELSE → archive_sample mode=qc_only → [delete_fastqs] → delete_bam → qc_failed
```

### Two guardrails

| Gate | Action | Scope variable | When | Blocks |
|------|--------|----------------|------|--------|
| **Alignment** | `sample.methyl_qc` | `qcPass` | After linear / stock Giraffe / methylGrapher QC BAM, before extract | Extract path (unless fastp remediation) |
| **Extraction** | `sample.extraction_qc` | `extractionQcPass` | After `sample.methyl_extract` or `sample.methylgrapher_wgbs_extract` | Full archive + BAM delete |

Alignment QC may recommend **REALIGN_TRIM** (Picard cycle screening, or WGBS `remediate_without_cycles` from conversion/mapped-rate signals). When `remediateAlignment` is true, fastp trims Read 1/2 start or end bases per `trimFront1`/`trimTail1`/`trimFront2`/`trimTail2`, then the **same-mode** aligner re-runs (`methylgrapher_wgbs_realign` when `useWgbsPangenome`, else Parabricks), and methyl_qc runs again — **FASTQs must remain on disk** until archive (pass or reject).

Extraction QC reads `{sampleId}.extraction_manifest.json` (MethylExtractor or methylGrapher canonical shape) and writes `{sampleId}.extraction_qc.json`. Failures are **terminal** (no retry loop today). Read-level `{chr}-CG.patterns.h5` sidecars are emitted at extract time; **`pipeline.info_measures` runs later in the study lifecycle**, not inside SamplePrep.

**Ordering:** methyl-qc runs **before** fragmentomics so failed samples skip BAM scanning. Fragmentomics runs before extraction (needs aligned BAM).

## Analytes and sources

| Dimension | Behavior |
|-----------|----------|
| **Analyte** | Set `primaryAnalyte` (`cfdna`, `buffy_coat`, …) and `isCfdna` in instance context. Fragmentomics runs only when `isCfdna` is true. Alignment and extraction QC apply to **all** analytes. |
| **FASTQ ingress** | **Required** `fastqStorage` on study start (laboratory-owned). Planner materializes `samples[].fastqSource` from storage + per-sample prefix. |
| **Sample archive** | Optional `sampleStorage` / `sampleDestination` from portal profile when omitted. **Full** archive (FASTQs + QC + H5) after **extractionQcPass**; **qc_only** archive on any final reject. Legacy keys `h5Storage` / `h5Destination` accepted. |

See [`../../docs/ANALYTE_PROFILES.md`](../../docs/ANALYTE_PROFILES.md) for downstream step profiles.

## Instance `context_json`

Top-level keys become scope-0 variables. FOREACH object elements flatten into per-sample scope (`${var.sampleId}`, `${var.sampleDir}`, …).

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `projectPath` | string | yes | Absolute path to `project.json` |
| `primaryAnalyte` | string | yes | `cfdna`, `buffy_coat`, etc. |
| `isCfdna` | boolean | yes | Drives fragmentomics **IF** |
| `referenceFasta` | string | yes | Reference FASTA for linear Parabricks fq2bam and MethylExtractor |
| `referenceGtf` | string | no | GTF for Parabricks |
| `alignmentMode` | string | no | `linear` (default when unset), `pangenome` (stock Giraffe), or `pangenome_wgbs` (methylGrapher WGBS; binds `useWgbsPangenome` + `usePangenome`). **Buffy default procedure:** `buffy_wgbs_pangenome_gene_fc`; linear alternate `buffy_wgbs_linear_gene_fc`. |
| `deleteFastqs` | boolean | no | **Default `true`.** When true, run `sample.delete_fastqs` after archive/terminal QC. Set `false` (or profile `actionConfig.sample_prep.delete_fastqs: false`) to retain FASTQs under `/work/samples/{id}/`. |
| `fastqStorage` | object | yes | Laboratory-owned ingress (never inferred from archive profile) |
| `sampleStorage` | object | no | Internal archive defaults from `portal.resource_profile` → `cfg.storage_endpoint` when omitted (`h5Storage` alias) |
| `samples` | array | yes | Each object: `sampleId`, `sampleDir`, materialized `fastqSource`, optional `sampleDestination` |

After `sample.methyl_qc`: `qcPass`, `qcDisposition`, `trimFront1`, `trimTail1`, `trimFront2`, `trimTail2`, `qcAttemptReason`, `remediateAlignment`.

After `sample.archive_sample`: `sampleArchived`.

After `sample.extraction_qc`: `extractionQcPass` ← `guardrails.overall_pass`.

Examples:

- [`instance_context_examples/sample_prep_plasma.json`](instance_context_examples/sample_prep_plasma.json)
- [`instance_context_examples/sample_prep_from_planner.json`](instance_context_examples/sample_prep_from_planner.json)

**Planner:** `methyl-study-start sample-prep-start` or `methyl_validation.sample_prep_planner.plan_sample_prep_context()`. See [`../docs/sample_prep_test_bed.md`](../docs/sample_prep_test_bed.md).

## Storage contract

Per sample under `/work/samples/{sample_id}/`. FASTQs stay at the **sample root**; alignment/extract products live in the **arm leaf** (`sampleDir` = `{sampleRoot}/align.{mode}.{engine}/`). Switching engines must not overwrite another arm.

```text
/work/samples/{sample_id}/
  *.fastq.gz                          sampleRoot; retained until archive_sample, then deleted
  .caas/                              sample-identity skip store
  align.linear.parabricks/            example sampleDir (linear + Clara)
    {sample_id}.bam                   deleted after archive (pass or reject)
    {sample_id}.alignment.gaf         methylGrapher WGBS mode (retained)
    {sample_id}.alignment_metrics.json  methylGrapher align provenance (retained)
    {sample_id}.json                  Parabricks / align metrics (retained)
    *.qc-metrics.tar                  optional Parabricks tar (retained)
    {sample_id}.sample_prep_log.jsonl append-only audit (retained)
    {sample_id}.extraction_manifest.json   MethylExtractor or methylGrapher export (retained)
    {sample_id}.extraction_qc.json    post-extract guardrails (retained)
    {chr}-{ctx}.h5                    methylation matrices (retained; optional remote copy)
```

`delete_fastqs` targets `sampleRoot`. `delete_bam` and BAM/H5 archive target `sampleDir`. After a historical flat-root Clara run, `scripts/relocate_root_clara_products.py` moves root products into `align.linear.parabricks/` without deleting existing `align.*` trees.

Alignment QC export path when project-scoped: `{output_base}/{project}/alignment_qc/{sampleId}.json`.

## Downstream handoff (`MethylSampleRef`)

Workers enrich scope with a tagged **`MethylSampleRef`** (`$type: MethylSampleRef`) accumulating:

- `alignmentQc` — pass/fail + QC JSON path
- `extractionQc` — pass/fail + extraction QC JSON path
- `methylation` — `MethylationMatrixRef` (`sampleDir`, `chromosomes`, `contexts`, `h5Pattern`)
- optional `h5Archive`, `fragmentomics`

Downstream analysis loads HDF5 via **`methyl_domain.helpers.resolve_methylation_h5_path()`** and **`MethylSample.load_from_h5()`** (GPU-capable in methylutils). Do not hard-code `{chr}-CG.h5` paths.

## Deploy & run

Direct DB deploy + CI start:

```bash
bash scripts/deploy_workflow_definitions.sh
methyl-study-start sample-prep-start request.json
```

Workers poll by capability; task count scales with `len(samples) × actions per sample` including remediation when triggered.

## Portal handoff

1. Start **SamplePrepPipeline** when laboratory FASTQs are ready.
2. On instance **COMPLETED**, start **StudyValidationLifecycle** or **DataDrivenPipeline** with the same `projectPath`.

See [pipeline architecture §0](../docs/pipeline_architecture.md).

## Worker capability matrix

| action_name | capability | Owner |
|-------------|------------|-------|
| `sample.download_fastq` | `sample.download-fastq` | In-process worker |
| `sample.parabricks_fq2bam` | `parabricks.fq2bam` | External GPU (Docker Parabricks) |
| `sample.parabricks_giraffe` | `parabricks.giraffe` | External GPU (stock HPRC pangenome) |
| `sample.methylgrapher_wgbs_align` | `methylgrapher.wgbs_align` / `methylgrapher.wgbs_gpu_align` | External Docker native-Mojo (`engine=mojo` / `align_engine=gpu_giraffe|mojo_giraffe`; NVIDIA CUDA or AMD HIP; optional vg QC BAM) |
| `sample.methylgrapher_wgbs_extract` | `methylgrapher.wgbs_extract` | External Docker native-Mojo MethylCall/MergeCpG (CPU-parallel; graph-aware H5 + patterns) |
| `sample.trim_fastq` | `sample.trim-fastq` | In-process (fastp) |
| `sample.delete_fastqs` | `sample.delete-fastqs` | In-process |
| `sample.methyl_qc` | `methyl-qc` | In-repo methylalignmentqc |
| `sample.fragmentomics` | `methyl-fragmentomics` | In-repo (cfDNA only) |
| `sample.methyl_extract` | `methyl-extract` | External MethylExtractor |
| `sample.extraction_qc` | `methyl-extraction-qc` | In-repo methylextractionqc |
| `sample.archive_sample` | `sample.archive-sample` | In-process (curated qc/fastq/h5 bundle; replaces retired `sample.upload_h5`) |
| `sample.delete_bam` | `sample.delete-bam` | In-process |
| `sample.qc_failed` | `sample.mark-failed` | In-process |

Contract details: [`../contract/sample_prep_capabilities.md`](../contract/sample_prep_capabilities.md)

## Related

- DomainProgram fixture: [`../domain/fixtures/sample_prep.program.json`](../domain/fixtures/sample_prep.program.json)
- Remediation cohort: [`../domain/fixtures/sample_prep_remediate.program.json`](../domain/fixtures/sample_prep_remediate.program.json)
- Test bed: [`../docs/sample_prep_test_bed.md`](../docs/sample_prep_test_bed.md)
- Extraction QC contract (upstream): MethylExtractor `docs/extraction_qc_contract.md`
- Downstream analysis: [`DataDrivenPipeline.md`](DataDrivenPipeline.md)
