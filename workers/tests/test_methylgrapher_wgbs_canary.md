# WGBS pangenome canary (vs linear fq2bam_meth)

Gate production promotion of `buffy_wgbs_pangenome_gene_fc` (`alignmentMode=pangenome_wgbs`)
on an explicit real-sample canary. Prefer the **executable** path below over ad-hoc
operator studies.

Pinned public sample: **GSE261315 / GSM8140413 / SRR28293403** (`WGBS-HG00621-Rep1`).
Provenance: [`tests/real_data/sample_prep_canary/provenance.json`](../../tests/real_data/sample_prep_canary/provenance.json).

## Executable canary (recommended)

```bash
source .venv/bin/activate
# 0) One-time: provision under /work/genomes/pangenome/canary/... (pangenome inventory)
bash scripts/provision_sample_prep_canary.sh --subset-pairs 2000000
scripts/sync_genomes_to_s3.sh --only pangenome/canary
# Merge emitted checksums into site testing.sample_prep_canary
# (fastq_storage.basePath=/work/genomes/pangenome). See registry.example.json

# 1) 64K image smoke
bash workers/docker/methylgrapher/smoke_64k.sh "$METHYL_METHYLGRAPHER_IMAGE"

# 2) Routine tier (subset) — all three SamplePrep modes
unset WORKER_STUB_EXTERNAL
bash scripts/smoke_sample_prep_real.sh --tier subset

# 3) Full qualification (after subset passes; expensive)
bash scripts/smoke_sample_prep_real.sh --tier full
```

GitHub Actions: [`.github/workflows/sample-prep-canary.yml`](../../.github/workflows/sample-prep-canary.yml)
(manual or monthly subset schedule on `production-work-agents`).

Reports (JSON + JUnit + Markdown) land under the `--report-dir` / run-root `reports/`
folder. Stock Giraffe (`alignmentMode=pangenome`) is an **engineering comparator only** —
no methylation parity vs linear/methylGrapher is required.

## Inputs (modes)

Same FASTQ pair run through:

1. `alignmentMode=linear` → `sample.parabricks_fq2bam` + `sample.methyl_extract`
2. `alignmentMode=pangenome` → `sample.parabricks_giraffe` + `sample.methyl_extract` (contract only)
3. `alignmentMode=pangenome_wgbs` → `sample.methylgrapher_wgbs_align` + `sample.methylgrapher_wgbs_extract`

Site pins: stock `pangenome` + BS `pangenome_wgbs` via `METHYL_SITE_CONFIG`.
Image: `METHYL_METHYLGRAPHER_IMAGE`.

## Compare (operator-set thresholds)

| Metric | Artifact | Acceptance |
|--------|----------|------------|
| Mapping rate | alignment QC JSON | within `thresholds.mapping_rate_delta` of linear |
| Duplication rate | dedup metrics / QC | within `thresholds.duplication_rate_delta` |
| CpG sites called | extraction manifest / H5 | ≥ `thresholds.cpg_sites_min_fraction_of_linear` of linear |
| Mean coverage / beta | H5 / manifest summaries | within `thresholds.mean_coverage_delta` |
| Read-level tiles | `*.patterns.h5` | required when `require_read_level_patterns=true` |
| Extraction QC | extraction_qc JSON | pass when `require_extraction_qc_pass` |

Promotion requires documented pass against the operator thresholds above; stock Giraffe
remains available but is **not** a silent fallback when BS assets are missing.
