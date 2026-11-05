# Sample prep worker capabilities

Contract for **SamplePrepPipeline** remote workers. All workers use the standard poll/submit protocol ([GoliathApp `WORKER_PROTOCOL.md`](https://github.com/Goliath-Research/GoliathApp/blob/main/workers/WORKER_PROTOCOL.md)).

## Result codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `> 0` | Success with optional semantic payload (avoid for gate variables) |
| `< 0` | Permanent failure — instance marks **FAILED** |

QC gate variable `qcPass` comes from **`output_json.guardrails.overall_pass`** (boolean), not `result_code`.

After `methyl-qc`, scope also receives **`qcDisposition`**, **`trimFront2`**, **`qcAttemptReason`**, and **`remediateR2Trim`** (boolean) for the remediation branch.

After `sample.extraction_qc`, scope receives **`extractionQcPass`** from **`output_json.guardrails.overall_pass`**. Extraction QC runs after `sample.methyl_extract` or `sample.methylgrapher_wgbs_extract` and before `sample.archive_sample` and `sample.delete_bam`.

## FASTQ retention

**Do not delete FASTQs before final QC.** `sample.delete_fastqs` runs only after QC pass or final fail (including post-trim retry), and only when scope **`deleteFastqs` is true** (default). Trimming (`sample.trim-fastq`) requires original `*_1.fastq.gz` / `*_2.fastq.gz` still present.

| Setting | Where | Effect |
|---------|--------|--------|
| `deleteFastqs: true` (default) | Instance context (seeded from `PIPELINE_FLAG_DEFAULTS` / planner) | Delete local FASTQs after archive/terminal path |
| `deleteFastqs: false` | Instance context | Skip all `sample.delete_fastqs` nodes; keep FASTQs on `/work/samples/{id}/` |
| `actionConfig.sample_prep.delete_fastqs` | Profile / site | Same boolean; context key wins if already set |

BAM deletion (`sample.delete_bam`) is **not** gated by this flag. Durable copies of FASTQs may still exist on `sampleDestination` after `archive_sample` mode=`full`.

## Audit trail

- QC export: append-only **`qc_history`** on each `{sampleId}.json` in alignment QC output.
- Sample dir: append-only **`{sampleId}.sample_prep_log.jsonl`** for every prep action (download, align, trim, QC, delete).

## Idempotency

| Capability | Safe to retry when |
|------------|-------------------|
| `sample.download-fastq` | Destination FASTQs missing, or remote size/mtime differ from local |
| `sample.delete-fastqs` | FASTQs already absent (no-op success) |
| `sample.delete-bam` | BAM already absent (no-op success) |
| `parabricks.fq2bam` / `parabricks.giraffe` | Only when BAM missing or QC artifact missing (`{sampleId}.json` or `{sampleId}.qc-metrics.tar`); pass **`forceRealign: true`** after trim to clear stale outputs |
| `methylgrapher.wgbs_align` | Same idempotency as Parabricks; also requires `{sampleId}.alignment.gaf` + QC tar when present |
| `sample.trim-fastq` | When trimmed FASTQs missing or `trimFront2` changed |
| `methyl-extract` / `methylgrapher.wgbs_extract` | When HDF5 outputs missing for `project.chromosomes × extract_contexts` |
| `methyl-extraction-qc` | When `{sampleId}.extraction_qc.json` missing or manifest changed |
| `sample.archive-sample` | Remote object missing or size/ETag differs from local bundle member |

## Lease / retry

- Default lease: **3600 s** for alignment and extraction; **600 s** for download/delete/QC.
- Transient failures (network, GPU OOM): worker returns lease via heartbeat extension or explicit fail; middle-tier may re-queue if policy allows.
- Permanent failures (`result_code < 0`) on one sample: that sample's remaining steps are **SKIPPED**; sibling samples keep running. When every sample is terminal, the instance **COMPLETED**. Failed download/QC nodes stay `FAILED` for operators. Do not leave the instance `RUNNING` because a sample was missing, disqualified, or failed QC.

---

## `sample.download-fastq`

**action_name:** `sample.download_fastq`

### input_json

```json
{
  "tool": "SampleDownloadFastq",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148",
  "fastqSource": {
    "type": "s3",
    "bucket": "methyl-cohort",
    "prefix": "plasma/DPLST-051425-111148/",
    "region": "us-east-1",
    "credentials": { "authMode": "instance_profile" }
  }
}
```

`fastqSource.prefix` points at a **folder prefix** (or single `.fastq.gz` object). All `*.fastq.gz` / `*.fq.gz` objects under that prefix are downloaded into `sampleDir`. Typical layout: `<sample>_1.fastq.gz`, `<sample>_2.fastq.gz`, but any matching extension in the folder is included.

**Idempotency:** for each file, download is skipped when the local copy already exists with the same **size** and **mtime** (±1 s) as the remote object.

### Authentication (typed JSON only)

Credentials are supplied in `fastqSource.credentials` — there is **no** env-var fallback on the worker for Access Keys. Production secrets live in **`cfg.credential`** (portal admin upsert); schedule-time expand embeds them into task JSON over the gateway.

| `type` | `credentials.authMode` | Notes |
|--------|------------------------|-------|
| `s3` | `explicit_keys` | Production path from DB expand: `accessKeyId`, `secretAccessKey`, optional `sessionToken`, plus `contentHash` |
| `s3` | `instance_profile` | boto3 default chain (IAM role on worker node) |
| `s3` / `azure_blob` | `azure_key_vault` | Optional escape hatch: `{ vaultUrl, secretName }` — worker MI; **not** the default dumb-worker path |
| `s3` / `azure_blob` | `encrypted_file` | Node-local / air-gapped Fernet file (`/var/lib/methyl…`, never `/work`) |
| `azure_blob` | `account_key` | From DB expand / portal |
| `azure_blob` | `connection_string` | From DB expand / portal |
| `azure_blob` | `default_credential` | `DefaultAzureCredential` (managed identity, Azure CLI dev) |
| `file` | — | `basePath` + `prefix` on shared NFS/local storage |

**DB SoT:** Lab / infrastructure admins upsert via `portal.sp_*`. Expand attaches `credentialName`, `credentialVersion`, `contentHash`. Workers refresh node-local cache when the hash changes ([`storage_secrets.py`](../../packages/methyldomain/methyl_domain/storage_secrets.py)). `methyl-cfg` upsert is **dev/bootstrap only**.

Transfer performance knobs (multipart / concurrency): site or profile `actionConfig.storage_transfer` — see `schemas/config/storage_transfer.schema.json`.

Do not write secrets under `/work/projects`. Materialized `/work/site/storage_endpoints/*.json` is redacted.

Portal / planner JSON: instance-level `fastqStorage` (laboratory-owned ingress — **required** on every study start) plus per-sample `fastqPrefix`. Internal archive (`sampleStorage` / `h5Storage`) defaults from `portal.resource_profile` → `cfg.storage_endpoint`. See [portal_resource_profile.md](../../docs/deployment/portal_resource_profile.md).

### output_json

```json
{
  "sampleId": "DPLST-051425-111148",
  "fastqFiles": ["R1.fastq.gz", "R2.fastq.gz"]
}
```

---

## `parabricks.fq2bam`

**action_name:** `sample.parabricks_fq2bam`  
**Runtime:** Docker GPU container running `pbrun fq2bam_meth` (WGBS/bisulfite alignment).

### Worker environment (fallback)

Production settings resolve from merged **profile/site `actionConfig.parabricks`** (materialized as task `resolvedConfig`) and optional task `input_json` overrides. Environment variables are used only when profile/site/task values are absent:

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `METHYL_PARABRICKS_IMAGE` | fallback | — | Clara Parabricks image (e.g. `nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1`) |
| `METHYL_PARABRICKS_GPU_FLAGS` | no | `--gpus all` | Passed to `docker run` |
| `METHYL_PARABRICKS_BWA_THREADS` | no | `16` | `--bwa-cpu-thread-pool` |
| `METHYL_PARABRICKS_EXTRA_DOCKER_ARGS` | no | — | Extra `docker run` flags (shell-split) |
| `METHYL_PARABRICKS_CLEANUP_TMP` | no | `1` | Remove `{sampleDir}/tmp` after success |

Operators pre-pull the image on GPU nodes; the worker does not auto-pull. Use `scripts/verify_parabricks.sh` for a smoke test.

### Storage contract (`/work/samples/{sampleId}/`)

Production SamplePrep binds **`sampleRoot`** to `/work/samples/{sampleId}/` and **`sampleDir`** to `{sampleRoot}/align.{mode}.{engine}/` (see [sample-prep-tooling.md](../../docs/architecture/sample-prep-tooling.md)). FASTQs live at `sampleRoot`; alignment products live in the arm leaf:

| Artifact | Path |
|----------|------|
| FASTQ | `{sampleRoot}/{sampleId}_1.fastq.gz` (hardlinked into `sampleDir`) |
| BAM + `.bai` | worker-local `{METHYL_LOCAL_TMP}/bam/{slug}-{digest}/{sampleId}.bam` (never `/work`; see below) |
| QC metrics archive | `{sampleDir}/{sampleId}.qc-metrics.tar` |
| Duplicate metrics | `{sampleDir}/{sampleId}.deduplicate_metrics.txt` |
| Alignment log | `{sampleDir}/{sampleId}.fq2bam_meth.log` |
| CAAS store | `{sampleRoot}/.caas/` |

The BAM exists only on the align worker. `sample.methyl_qc`, `sample.fragmentomics`, `sample.methyl_extract`, `sample.extraction_qc` and `sample.delete_bam` are catalog-pinned (`dispatch.require_previous_worker`) to that worker. Align is never CAAS-harvested. If the pinned worker is lost, restart the sample from Align.

FASTQ inputs: prefer `{sampleId}_1.fastq.gz` + `{sampleId}_2.fastq.gz` at `sampleRoot`; otherwise exactly two `**/*.fastq.gz` under `sampleDir`.

**`stageFastqs: "local"` (optional; all Align actions: `sample.parabricks_fq2bam` including the Mojo engine, `sample.parabricks_giraffe`, `sample.methylgrapher_wgbs_align`, `sample.docker_align`).** After the FASTQ pair (trimmed when present) is resolved on `/work`, the Align task copies it to `{METHYL_LOCAL_TMP}/bam/{slug}-{digest}/fastq/` (read-only `copyfile` from `/work`, `.part` + rename on local disk; geesefs-safe) and the aligner reads it there. The copies share the BAM directory, so `sample.delete_bam` and failed-task / remediation cleanup remove them. The `/work` FASTQs are untouched: `sample.archive_sample` uploads them from `/work` (unpinned, no GPU capability) and `sample.delete_fastqs` then removes them. A remediation re-Align re-stages from `/work`. Omit the key (or `null`) to align straight from `/work`. SamplePrepPipeline sets it on every Align node.

### input_json

```json
{
  "tool": "ParabricksFq2Bam",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148/align.linear.parabricks",
  "sampleRoot": "/work/samples/DPLST-051425-111148",
  "referenceFasta": "/work/genomes/.../Homo_sapiens.GRCh38.dna.primary_assembly.fa",
  "referenceGtf": "/work/genomes/.../Homo_sapiens.GRCh38.114.gtf",
  "parabricksImage": null,
  "bwaThreads": null
}
```

### Profile/site `actionConfig.parabricks`

```json
"parabricks": {
  "image": "nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1",
  "bwa_threads": 16,
  "gpu_flags": "--gpus all",
  "cleanup_tmp": true
}
```

Task `input_json` keys: `parabricksImage`, `bwaThreads`, `gpuFlags`, `extraDockerArgs`, `cleanupTmp`.

### output_json

```json
{
  "sampleId": "DPLST-051425-111148",
  "bamPath": "/tmp/methyl-pb/bam/DPLST-051425-111148-3f9a1c2b7e/DPLST-051425-111148.bam",
  "metricsJson": "/work/samples/DPLST-051425-111148/DPLST-051425-111148.json",
  "qcMetricsTar": "/work/samples/DPLST-051425-111148/DPLST-051425-111148.qc-metrics.tar"
}
```

---

## `sample.delete-fastqs`

**action_name:** `sample.delete_fastqs`

### input_json

```json
{
  "tool": "SampleDeleteFastqs",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148"
}
```

### output_json

```json
{
  "sampleId": "DPLST-051425-111148",
  "deleted": true
}
```

---

## `parabricks.giraffe`

**action_name:** `sample.parabricks_giraffe`
**Runtime:** Docker GPU container running `pbrun giraffe` (HPRC pangenome, GRCh38 surjection) followed by `pbrun collectmultiplemetrics --gen-all-metrics` on the surjected BAM.

**When:** SamplePrep **IF** `usePangenome` is true (`alignmentMode: "pangenome"`). Idempotency and `forceRealign` semantics match `parabricks.fq2bam`.

**Site manifest (`pangenome_genome`):** `gbz`, `dist`, `min`, `zipcodes`, `ref_paths`, `linear_ref_fasta` (required). `linear_ref_fasta` is also used for `collectmultiplemetrics`, `methyl_extract`, and `alignment_qc`.

**Outputs:** Same artifacts as `parabricks.fq2bam` (`{sampleId}.bam`, `{sampleId}.deduplicate_metrics.txt`, `{sampleId}.qc-metrics.tar`, alignment log). `methyl-qc` consumes the regenerated qc-metrics tar unchanged.

**Bisulfite caveat:** stock HPRC graphs are **not** bisulfite-aware. For WGBS pangenome use `alignmentMode: "pangenome_wgbs"` (methylGrapher) — not stock Giraffe.


## `methylgrapher.wgbs_align`

**action_name:** `sample.methylgrapher_wgbs_align`  
**Capability:** `methylgrapher.wgbs_align` / `methylgrapher.wgbs_gpu_align`  
**Runtime:** Docker **GPU** container (`:1.70-mojo-cuda` or `:1.70-mojo-rocm`) running **native-Mojo** methylGrapher Align (dual C→T / G→A GBZ → science GAF) via `std.gpu.host.DeviceContext` on **NVIDIA CUDA or AMD HIP**. QC BAM is a separate C2T pass (Mojo QC BAM or `vg giraffe -o BAM` when configured). The science GAF is in named-segment space and cannot be fed to `vg surject`. On known NVIDIA/AMD fleets DeviceContext failure is **fail-closed** — not a switch to Clara Parabricks. CPU (`cpu_vg`) is only for unknown GPU vendors or explicit parity rollback. On 64 KB-page ARM64 the image must ship `jemalloc=off` vg for any vg-assisted QC path (see [`workers/docker/methylgrapher/README.md`](../../workers/docker/methylgrapher/README.md)).

**When:** SamplePrep **IF** `useWgbsPangenome` is true (`alignmentMode: "pangenome_wgbs"`). Checked **before** `usePangenome` in the program graph. Idempotency and `forceRealign` semantics match `parabricks.fq2bam`.

**Site manifest (`pangenome_wgbs_genome`) / `actionConfig.methylgrapher_wgbs`:** dual converted indexes (`gbz`, `dist`, `min`, `zipcodes`), `cpg_tsv`, `ref_paths`, `original_gbz`, `linear_ref_fasta` (QNAP asset `pangenome-grch38-d9-bs-1.70`), plus `engine=mojo`, `align_engine=gpu_giraffe|mojo_giraffe`, `giraffe_device`, `image`. **Do not** fall back to stock `pangenome_genome` when the BS bundle is missing. Clara is an **explicit** `alignmentMode: linear|pangenome` choice only.

**Worker environment (fallback):**

| Variable | Required | Purpose |
|----------|----------|---------|
| `METHYL_METHYLGRAPHER_IMAGE` | fallback | methylGrapher Docker image (pin in profile/site `actionConfig.methylgrapher_wgbs.image`) |

Gate production promotion with [`workers/tests/test_methylgrapher_wgbs_canary.md`](../../workers/tests/test_methylgrapher_wgbs_canary.md).

**Outputs (mode-aware methyl-qc):**

| Artifact | Path |
|----------|------|
| QC BAM + `.bai` | worker-local `{METHYL_LOCAL_TMP}/bam/{slug}-{digest}/{sampleId}.bam` (surjected, original read sequences restored; never `/work`, never CAAS-harvested; QC BAM intermediates live in the same directory) |
| Merged GAF | `{sampleId}.alignment.gaf` |
| Duplicate metrics | `{sampleId}.deduplicate_metrics.txt` |
| QC metrics archive | `{sampleId}.qc-metrics.tar` (optional; when Picard collectmultiplemetrics succeeds) |
| Align provenance | `{sampleId}.alignment_metrics.json` (primary methylGrapher-family QC input) |

Implementation: [`workers/methyl_worker/methylgrapher_wgbs_runner.py`](../../workers/methyl_worker/methylgrapher_wgbs_runner.py).


## `methylgrapher.wgbs_extract`

**action_name:** `sample.methylgrapher_wgbs_extract`  
**Runtime:** Same native-Mojo methylGrapher Docker image as align (`MethylCall` + `MergeCpG` → linear-coordinate CpG TSV → `{chrom}-{ctx}.h5` + optional `{chrom}-{ctx}.patterns.h5`). Hot path is **native-Mojo** with Mojo `parallelize()` (CPU-parallel — **not** a CUDA/HIP GPU workload and **not** stock Python).

**When:** SamplePrep pass path when `useWgbsPangenome` is true (replaces `sample.methyl_extract`). Requires prior `sample.methylgrapher_wgbs_align` GAF + QC BAM on disk.

**Extraction manifest:** canonical `metadata` / `summary` / `per_chromosome` shape for `methyl-extraction-qc` (read-filtering block omitted when unavailable — discard-fraction guardrail reports skipped). Graph provenance retained in manifest sidecars.

**Storage contract:** HDF5 names are `{chrom}-{ctx}.h5`, `{chrom}-{ctx}.patterns.h5`, and `{sampleId}.extraction_manifest.json`.

**`workDirMode: "local"` (optional).** MethylCall / MergeCpG scratch and every output (H5, patterns, manifest) are written under worker-local `{METHYL_LOCAL_TMP}/extract/{slug}-{digest}/`; on success only `*.h5` and `*.json` are published to `sampleDir` (bytes to a sibling file, then one rename). Manifest and `h5Files` paths name `sampleDir`. The local directory is removed on success and failure. Omit to use `{sampleDir}/methylgrapher_work`. SamplePrepPipeline sets it on both extract nodes.


## `methyl-qc`

**action_name:** `sample.methyl_qc`  
**Owner:** In-repo — [`packages/methylalignmentqc`](../../packages/methylalignmentqc/)

### input_json

```json
{
  "tool": "MethylAlignmentQc",
  "project": "/work/projects/prostate-cancer/configs/project_Plasma_healthy_vs_PCa.json",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148",
  "primaryAnalyte": "cfdna"
}
```

**Phase 1:** Shell wrapper invokes `methyl-qc --project ${project}` with sample dir override in step JSON if CLI lacks `--sample-id`.  
**Phase 2:** Add `--sample-id` to methyl-qc CLI for explicit scoping.

### output_json

V2 alignment QC document. Workflow binds:

```json
{
  "guardrails": {
    "overall_pass": true
  }
}
```

Path binding: `$.guardrails.overall_pass` → scope variable **`qcPass`**.

Hard QC failure: `result_code < 0` skips the rest of that sample (instance stays RUNNING until the cohort drains). Soft fail uses `overall_pass: false` and **IF** routes to `sample.qc_failed`.

---

## `methyl-fragmentomics`

**action_name:** `sample.fragmentomics`  
**Owner:** In-repo — [`packages/methylfragmentomics`](../../packages/methylfragmentomics/)  
**When:** Only when `isCfdna` is true (workflow **IF** node).

### input_json

```json
{
  "tool": "MethylFragmentomics",
  "project": "/work/projects/prostate-cancer/configs/project_Plasma_healthy_vs_PCa.json",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148"
}
```

### output_json

```json
{
  "sampleId": "DPLST-051425-111148",
  "outputDir": "/work/projects/prostate-cancer/Plasma_healthy_vs_PCa/fragmentomics/DPLST-051425-111148"
}
```

---

## `methyl-extract`

**action_name:** `sample.methyl_extract`  
**Owner:** external native CLI on the worker `PATH` after that tool is installed.

Production remote workers are **self-contained per task**: config comes from **`input_json`** plus **`project.json` on shared storage**. Environment variables are **not** required (dev/CLI only).

### Required input_json

| Field | Description |
|-------|-------------|
| `sampleId` | Sample identifier |
| `sampleDir` | Shared storage path (`/work/samples/{id}`) |
| `project` | Absolute path to project JSON on shared storage |
| `referenceFasta` | Reference FASTA (site `reference_genome.fasta`, instance `context_json`, or profile `actionConfig.alignment_qc.genome_fasta`) |

Optional per-task overrides: `extractContexts`, `threads`, `minMapq`, `chromMapping`, …

### Profile/site `actionConfig.methyl_extract`

Production defaults (not env vars):

```json
"methyl_extract": {
  "extract_contexts": ["CG", "CHG", "CHH"],
  "contig_naming": "ensembl",
  "threads": 10,
  "min_mapq": 20,
  "min_phred": 20,
  "split": true,
  "output_format": "hdf5"
}
```

**`chrom_mapping`** is optional. When omitted, the worker derives `{reference, chromosomes: [{name, bam, fasta, extract}]}` from `project.chromosomes` and `contig_naming` (`ensembl` | `ucsc_chr` | `custom` with `chromosome_overrides`). Inline objects and file paths remain supported.

**`extract_contexts`** is independent of root-level `project.contexts` (which controls downstream centroid/detector). Production typically extracts all three contexts so `sample.delete_bam` can run without losing CHG/CHH.

`chromosomes` for idempotency come from `project.chromosomes`.

### Storage contract (`/work/samples/{sampleId}/`)

| Artifact | Path |
|----------|------|
| Input BAM | worker-local `{METHYL_LOCAL_TMP}/bam/{slug}-{digest}/{sampleId}.bam` (not on `/work`) |
| Per-chrom HDF5 | `{chrom}-{ctx}.h5` (e.g. `1-CG.h5`, `1-CHG.h5`, `1-CHH.h5`) |
| Log | `{sampleId}.methyl_extract.log` |
| Extraction manifest | `{sampleId}.extraction_manifest.json` |
| Per-context QC sidecar | `{chrom}-{ctx}.json` (e.g. `1-CG.json`) |

Extraction writes to worker-local `{METHYL_LOCAL_TMP}/extract/{slug}-{digest}/`. On success only `*.h5` and `*.json` are copied into `sampleDir` (bytes to a sibling `.tmp-*` file, then one rename; no `utime`, `chmod` or hardlink). The local directory is removed on success and failure, so partial HDF5s never reach `/work`.

### input_json example

```json
{
  "tool": "MethylExtract",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148",
  "project": "/work/projects/prostate-cancer/configs/project_Plasma_healthy_vs_PCa.json",
  "referenceFasta": "/work/genomes/.../Homo_sapiens.GRCh38.dna.primary_assembly.fa"
}
```

### output_json

```json
{
  "sampleId": "DPLST-051425-111148",
  "h5Files": ["1-CG.h5", "1-CHG.h5", "1-CHH.h5", "2-CG.h5", "..."]
}
```

---

## `methyl-extraction-qc`

**action_name:** `sample.extraction_qc`  
**Owner:** In-repo — [`packages/methylextractionqc`](../../packages/methylextractionqc/)  
**When:** After every successful `sample.methyl_extract` or `sample.methylgrapher_wgbs_extract` (both direct pass and post-remediation pass paths).

Reads `{sampleId}.extraction_manifest.json` (extraction or methylGrapher canonical schema). Writes `{sampleId}.extraction_qc.json` in `sampleDir` with `guardrails.overall_pass`.

### input_json

```json
{
  "tool": "MethylExtractionQc",
  "project": "/work/projects/prostate-cancer/configs/project_Plasma_healthy_vs_PCa.json",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148"
}
```

Optional: `chromosomes` list when `project` is omitted (defaults to full genome expectation from manifest).

### Profile/site `actionConfig.extraction_qc`

```json
"extraction_qc": {
  "guardrails": {
    "min_cpg_weighted_mean_coverage": 10.0,
    "max_chh_methylation_level": 0.02,
    "max_chg_methylation_level": 0.02,
    "min_autosomal_coverage_uniformity_ratio": 0.5,
    "max_discard_fraction": 0.9
  },
  "expected_chromosomes": ["1", "2", "..."]
}
```

When `expected_chromosomes` is omitted, uses `project.chromosomes`.

### output_json

```json
{
  "sampleId": "DPLST-051425-111148",
  "qcPath": "/work/samples/DPLST-051425-111148/DPLST-051425-111148.extraction_qc.json",
  "guardrails": {
    "cpg_weighted_mean_coverage": { "value": 15.2, "pass": true },
    "overall_pass": true
  },
  "extractionQc": {
    "qcPath": "/work/samples/.../DPLST-051425-111148.extraction_qc.json",
    "overallPass": true
  }
}
```

Path binding: `$.guardrails.overall_pass` → scope variable **`extractionQcPass`**.

---

## `sample.archive-sample`

**action_name:** `sample.archive_sample`  
**When:** After terminal QC disposition (extraction pass → `mode=full`; alignment or extraction fail → `mode=qc_only`). Skipped with `skipReason: sample_destination_not_configured` when no `sampleDestination` is set.

Uploads a curated bundle to durable object storage (`file` / `s3` / `azure_blob`). **Local `/work/samples/{id}/` HDF5 files are retained** for downstream validation; BAM is never uploaded.

> **Note:** `sample.upload_h5` is **retired**. Use `sample.archive_sample` with `sampleDestination` (alias `sampleStorage` / deprecated `h5Storage`).

### input_json

Instance-level `sampleStorage` (planner) merges with per-sample `sampleDestination`:

```json
{
  "tool": "SampleArchive",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148",
  "mode": "full",
  "sampleDestination": {
    "type": "s3",
    "bucket": "methyl-archive",
    "prefix": "plasma/DPLST-051425-111148/",
    "region": "us-east-1",
    "credentials": { "authMode": "instance_profile" }
  }
}
```

| Mode | Remote layout |
|------|---------------|
| `full` | `qc/*.json`, `fastq/*.fastq.gz`, `h5/{chr}-{ctx}.h5` (including patterns), `archive_manifest.json` |
| `qc_only` | QC JSONs + `sample_prep_log.jsonl` + `reject_reason`; no FASTQs or H5 |

Credential modes mirror [`sample.download-fastq`](#sampledownload-fastq). **Idempotency:** skip upload when remote size and ETag/md5 match local.

### output_json

```json
{
  "sampleId": "DPLST-051425-111148",
  "mode": "full",
  "uploadedCount": 12,
  "remotePrefix": "plasma/DPLST-051425-111148/",
  "sampleArchived": true
}
```

Path binding: `sampleArchived` → scope variable **`sampleArchived`**.

---

## `sample.delete-bam`

**action_name:** `sample.delete_bam`

Runs on the align worker (`require_previous_worker`). Deletes the worker-local BAM, `.bai` and align directory, plus any `{sampleId}.bam` a non-linear aligner wrote to `sampleDir`.

### input_json

```json
{
  "tool": "SampleDeleteBam",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148"
}
```

### output_json

```json
{
  "sampleId": "DPLST-051425-111148",
  "deleted": true
}
```

---

## `sample.mark-failed`

**action_name:** `sample.qc_failed`  
**Owner:** Optional portal/DB worker

### input_json

```json
{
  "tool": "SampleMarkFailed",
  "sampleId": "DPLST-051425-111148",
  "sampleDir": "/work/samples/DPLST-051425-111148",
  "reason": "alignment_qc_failed"
}
```

### output_json

```json
{
  "sampleId": "DPLST-051425-111148",
  "status": "QC_FAILED"
}
```

Use `result_code = 0` even when marking failed — the sample is intentionally skipped; instance continues other FOREACH iterations.

---

## Registered actions (catalog)

| action_name | capability |
|-------------|------------|
| `sample.download_fastq` | `sample.download-fastq` |
| `sample.parabricks_fq2bam` | `parabricks.fq2bam` |
| `sample.parabricks_giraffe` | `parabricks.giraffe` |
| `sample.methylgrapher_wgbs_align` | `methylgrapher.wgbs_align` |
| `sample.methylgrapher_wgbs_extract` | `methylgrapher.wgbs_extract` |
| `sample.delete_fastqs` | `sample.delete-fastqs` |
| `sample.trim_fastq` | `sample.trim-fastq` |
| `sample.methyl_qc` | `methyl-qc` |
| `sample.fragmentomics` | `methyl-fragmentomics` |
| `sample.methyl_extract` | `methyl-extract` |
| `sample.extraction_qc` | `methyl-extraction-qc` |
| `sample.archive_sample` | `sample.archive-sample` |
| `sample.delete_bam` | `sample.delete-bam` |
| `sample.qc_failed` | `sample.mark-failed` |

**Workflow source of truth:** [`../domain/fixtures/sample_prep.program.json`](../domain/fixtures/sample_prep.program.json) deployed via `scripts/deploy_workflow_definitions.sh`.

Legacy SQL workflow seeds are **deprecated**. Canonical workflow: [`../domain/fixtures/sample_prep.program.json`](../domain/fixtures/sample_prep.program.json) (mirror: [`../tests/fixtures/sample_prep.program.json`](../tests/fixtures/sample_prep.program.json)).
