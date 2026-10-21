# Alignment engines

SamplePrep has three `alignmentMode` values and four align executors. Linear Clara Parabricks and linear GoliathAlign share `sample.parabricks_fq2bam` and switch on `actionConfig.parabricks.engine`. Stock Giraffe and methylGrapher are separate actions. Science contracts for the GoliathAlign binaries live in the sibling [GoliathAlign](https://github.com/Goliath-Research/GoliathAlign) repository (`fq2bam-meth/`, `giraffe/`, `methylgrapher/`). That repository was named mojo-align. `GOLIATH_ALIGN_*` is canonical; `MOJO_ALIGN_*` remains a one-cycle alias. GoliathAlign does not reference GoliathApp or GoliathOmics. See [Dependency direction](../architecture/dependencies.md).

Start and pin rules: [Start a SamplePrep instance](../operators/start-sample-prep.md).

## Mode matrix

| `alignmentMode` | Primary action | Engine the runner uses | Notes |
|-----------------|----------------|------------------------|-------|
| `linear` | `sample.parabricks_fq2bam` | Clara Parabricks `fq2bam_meth` when `actionConfig.parabricks.engine` is `parabricks` (runner default, `METHYL_PARABRICKS_ENGINE`) | |
| `linear` | `sample.parabricks_fq2bam` | GoliathAlign MojoFq2bamMeth when `engine` is `mojo` | `align_device` may be `cpu`. Procedure `buffy_wgbs_linear_mojo_gene_fc`. Linear `engine=mojo` defaults to FM-index (`METHYLGRAPHER_LINEAR_ENGINE=fm` on `${REF}.bwameth.c2t`). |
| `pangenome` | `sample.parabricks_giraffe` | Clara Parabricks giraffe → BAM | Stock (non-bisulfite) HPRC-style path. |
| `pangenome_wgbs` | `sample.methylgrapher_wgbs_align` then `sample.methylgrapher_wgbs_extract` | `align_engine` `cpu_vg` when unset (`_normalize_align_engine`). `gpu_giraffe` or `mojo_giraffe` when the procedure sets them. | Graph-aware GAF, then MethylCall. Clara giraffe does not emit that GAF. |

## Configuration surface

Workers consume **`resolvedConfig`** only (Universal Action Input Contract). Tunables live under:

- `actionConfig.parabricks` — linear / stock pangenome image, `engine` (`parabricks` or `mojo`), threads, references
- `actionConfig.methylgrapher_wgbs` — image (`:1.70-mojo-cuda` / `:1.70-mojo-rocm`), `engine`, `align_engine` (`cpu_vg`, `gpu_giraffe`, `mojo_giraffe`), `giraffe_device`, threads, graph assets

Site materialization (`METHYL_SITE_CONFIG` / `methyl-cfg`) supplies genome and image pins; profiles/procedures select `alignmentMode`. The goliath-test site seed (`scripts/default_methyl_site.json`, installed as `/work/site/methyl_site.json`) pins `reference_genome.fasta` to `linear/GRCh38/ensembl-116` and `actionConfig.parabricks.image` to `nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1`. Clara `fq2bam_meth` reads the bwa-meth C2T index next to that FASTA (`${FASTA}.bwameth.c2t` plus `.amb`, `.ann`, `.bwt`, `.pac`, `.sa`).

Linear `engine=mojo` can write Bismark-style `XM:Z` / `XG:Z` when `actionConfig.parabricks.write_methylation_tags` is true (`METHYLGRAPHER_WRITE_METH_TAGS=1`). MethylExtractor and native MHL prefer those tags and fall back to sequence+`XG` when they are absent (Clara `fq2bam_meth` today). Default off except on `cfdna_emseq_mhl_survival`. See [workers/docs/parabricks.md](../../workers/docs/parabricks.md).

## Failure policy

- A DeviceContext failure on `pangenome_wgbs` stops that sample. The runner does not switch the sample to Clara Parabricks.
- Clara runs when `alignmentMode` is `linear` or `pangenome` and `actionConfig.parabricks.engine` is `parabricks`.
- Unset `methylgrapher_wgbs.align_engine` is `cpu_vg`. Procedures that want GPU Giraffe set `gpu_giraffe` or `mojo_giraffe`.

## Mode-aware QC

Alignment QC shares guardrails across modes and adds tool-family checks:

- Parabricks-family metrics for `linear` / `pangenome`
- methylGrapher-family provenance and conversion-rate checks for `pangenome_wgbs`

See [Sample prep and QC](03-sample-prep-and-qc.md) and [methylalignmentqc USAGE](../../packages/methylalignmentqc/docs/USAGE.md).

## Sibling science contracts (GoliathAlign)

| Document | Role |
|----------|------|
| `giraffe/docs/GIRAFFE_SPEC.md` | Native Mojo Giraffe GBZ → GAF contract for `pangenome_wgbs` |
| `fq2bam-meth/docs/LINEAR_FQ2BAM_SPEC.md` | MojoFq2bamMeth BAM + QC JSON contract for `linear` + `engine=mojo` |
| `fq2bam-meth/docs/LINEAR_ENGINES.md` | FM-index default (`engine=fm`) vs frozen k-mer `parity` / experimental `speed` |
| `giraffe/docs/PHASE0_GH200_ALIGN.md` | Why Parabricks `pbrun giraffe` cannot emit science GAF |
| `giraffe/docs/BENCHMARK_GIRAFFE.md` | Operator wall-time / parity gates vs `vg` |
| `fq2bam-meth/docs/BENCHMARK_FQ2BAM_METH.md` | Clara vs Mojo linear bakeoff gates |
| `giraffe/docs/ROCM_GIRAFFE_GATES.md` | AMD ROCm image and host gates |

Clone path on developer hosts is typically alongside this repo (`../GoliathAlign`). Set `GOLIATH_ALIGN_ROOT` to that checkout (`MOJO_ALIGN_ROOT` and `../mojo-align` still work for one cycle). Docker images are built via `scripts/build_goliath_align_image.sh` and documented under [`workers/docker/methylgrapher/README.md`](../../workers/docker/methylgrapher/README.md). The in-container install prefix is `/opt/goliath-align` (symlink `/opt/mojo-align`). Env knobs use the `GOLIATH_ALIGN_*` family (one-release dual-read of deprecated `MOJO_ALIGN_*`, which itself still reads `METHYLGRAPHER_MOJO_*`).

## Related

- [SamplePrep tooling](../architecture/sample-prep-tooling.md)
- [Start a SamplePrep instance](../operators/start-sample-prep.md)
- [Tool requirements](../contracts/omics-tool-requirements.md)
- [workers/docs/parabricks.md](../../workers/docs/parabricks.md)
