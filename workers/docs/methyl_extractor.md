# MethylExtractor on sample-prep workers

Sample prep extraction (`sample.methyl_extract`) runs the **native MethylExtractor binary** on the **linear / stock pangenome** path. For `pangenome_wgbs`, SamplePrep uses `sample.methylgrapher_wgbs_extract` (native-Mojo MethylCall/MergeCpG) instead — see [`workers/docker/methylgrapher/README.md`](../docker/methylgrapher/README.md). Implementation: [`methyl_worker/extract_runner.py`](../methyl_worker/extract_runner.py).

## Source and deploy

MethylExtractor is maintained separately at `/home/ubuntu/MethylExtractor`. On each worker machine:

```bash
cd /home/ubuntu/MethylExtractor
make deps && make && make install
```

This installs `MethylExtractor` to `/usr/local/bin/` (machine image provisioning, not per-task JSON).

## Production worker contract

Workers consume **`--resolved-config` only** (Universal Action Input Contract). They do **not** re-read `project.json`, pipeline profiles, or `METHYL_*` env for tool parameters. `projectPath` on `input_json` is provenance/logging only.

| Source | Fields |
|--------|--------|
| `input_json` | `sampleId`, `sampleRoot`, `sampleDir` (arm leaf), `project`, `referenceFasta` (+ optional overrides) |
| `--resolved-config` | `actionConfig.methyl_extract` (chromosomes, extract contexts, `chrom_parallel`, `max_rss_gb`, read-level knobs) |

### Extract contexts vs downstream `contexts`

| Config | Purpose |
|--------|---------|
| `project.contexts` | Downstream centroid/detector (often `["CG"]` only) |
| `actionConfig.methyl_extract.extract_contexts` | What sample prep writes (default `["CG","CHG","CHH"]`) |

Extracting all contexts lets `sample.delete_bam` reclaim space without losing CHG/CHH for later analysis.

## Post-deploy smoke test

```bash
./scripts/verify_methyl_extractor.sh
```

## Manual / local dev

```bash
# Local wrapper: --sample-dir is the arm leaf. Distributed workers use --resolved-config only.
./scripts/methyl_extract.sh DPLST-051425-111148 \
  --project /work/projects/prostate-cancer/configs/project_Plasma_healthy_vs_PCa.json \
  --sample-dir /work/samples/DPLST-051425-111148/align.linear.parabricks
```

Optional dev env: `METHYL_EXTRACTOR_BIN` if the binary is not on default PATH.

## Outputs

Under `{sampleDir}`:

| Artifact | Pattern | Role |
|----------|---------|------|
| Per-chrom HDF5 | `{chrom}-{ctx}.h5` | Marginal per-CpG counts (e.g. `1-CG.h5`) |
| Read-level sidecar | `{chrom}-{ctx}.patterns.h5` | Per-tile read co-methylation histograms (when `--read-level`) |
| Extraction log | `{sampleId}.methyl_extract.log` | Command log |
| Phase timing | `{sampleId}.timing.json` | Elapsed-ms per chromosome (BAM vs write) |
| Extraction manifest | `{sampleId}.extraction_manifest.json` | **Preserve-or-synthesize:** keep a complete native `methylextractor.extraction_manifest` (including `read_filtering`); synthesize stubs/incomplete files from sidecars; overlay `h5_files` / `pattern_files` / provenance when missing |

### Read-level pattern sidecar (optional)

When profile `actionConfig.methyl_extract.read_level.enabled` is true (or `--read-level`
is passed), MethylExtractor also writes `{chrom}-{ctx}.patterns.h5` files. Schema:
[`docs/reference/read_level_pattern_contract.md`](../../docs/reference/read_level_pattern_contract.md).

Throughput knobs (operator-set, not code defaults):

| Config | CLI | Role |
|--------|-----|------|
| `threads` | `--threads` | Total region-worker budget, split across in-flight chromosomes |
| `chrom_parallel` | `--chrom-parallel` | Max chromosomes processed at once (memory-gated) |
| `max_rss_gb` | `--max-rss-gb` | Peak RSS gate for chrom-parallel |

MethylExtractor writes `{sampleId}.timing.json` next to the extraction manifest (phase elapsed-ms).

SaMD / research profiles enable read-level **by default**. Missing `*.patterns.h5` does **not**
fail sample prep or the validation lifecycle: extract warns and continues; `pipeline.info_measures`
skips with status `no_pattern_sidecars`; multi-path `covariates_path` lists omit missing sidecars.

Profile example:

```json
"methyl_extract": {
  "threads": 10,
  "chrom_parallel": 2,
  "max_rss_gb": 32,
  "read_level": { "enabled": true, "tile_size": 4 }
}
```

Worker CLI flags: `--read-level`, `--tile-size=<k>`, `--chrom-parallel`, `--max-rss-gb` (forwarded from resolved config).

See [`workflow_engine/contract/sample_prep_capabilities.md`](../../workflow_engine/contract/sample_prep_capabilities.md) for the full contract.
