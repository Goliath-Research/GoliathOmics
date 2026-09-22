# Alignment engines

MethylPipeline SamplePrep supports three `alignmentMode` values. This page is the operator matrix for choosing an engine; science contracts for the Mojo cutover live in the sibling [mojo-align](https://github.com/Goliath-Research/mojo-align) repository (`fq2bam-meth/`, `giraffe/`, `methylgrapher/`).

## Mode matrix

| `alignmentMode` | Primary action(s) | Default engine | Explicit alternative | Notes |
|-----------------|-------------------|----------------|----------------------|-------|
| `linear` | `sample.parabricks_fq2bam` | Clara Parabricks `fq2bam_meth` (NVIDIA) | **MojoFq2bamMeth** when `actionConfig.parabricks.engine=mojo` | Portable NVIDIA / AMD / CPU; Clara remains an explicit config choice. Linear `engine=mojo` defaults to FM-index (`METHYLGRAPHER_LINEAR_ENGINE=fm` on `${REF}.bwameth.c2t`), not the frozen k-mer parity engine. |
| `pangenome` | `sample.parabricks_giraffe` | Clara Parabricks giraffe → BAM | — | Stock (non-bisulfite) HPRC-style path; **not** replaced by mojo-align Giraffe |
| `pangenome_wgbs` | `sample.methylgrapher_wgbs_align` → extract | Native Mojo Giraffe (`align_engine=gpu_giraffe` / `mojo_giraffe`) on NVIDIA CUDA or AMD HIP | `cpu_vg` (`vg giraffe`) only when GPU vendor is unknown or for parity/rollback | Graph-aware GAF → MethylCall; Parabricks giraffe is **not** a GAF substitute |

## Configuration surface

Workers consume **`resolvedConfig`** only (Universal Action Input Contract). Tunables live under:

- `actionConfig.parabricks` — linear / stock pangenome image, `engine` (`clara` \| `mojo`), threads, references
- `actionConfig.methylgrapher_wgbs` — image (`:1.70-mojo-cuda` / `:1.70-mojo-rocm`), `engine=mojo`, `align_engine`, `giraffe_device`, threads, graph assets

Site materialization (`METHYL_SITE_CONFIG` / `methyl-cfg`) supplies genome and image pins; profiles/procedures select `alignmentMode`.

Linear `engine=mojo` can write Bismark-style `XM:Z` / `XG:Z` when `actionConfig.parabricks.write_methylation_tags` is true (`METHYLGRAPHER_WRITE_METH_TAGS=1`). MethylExtractor and native MHL prefer those tags and fall back to sequence+`XG` when they are absent (Clara `fq2bam_meth` today). Default off except on `cfdna_emseq_mhl_survival`. See [workers/docs/parabricks.md](../../workers/docs/parabricks.md).

## Failure policy

- On known NVIDIA/AMD fleets, DeviceContext failure for `pangenome_wgbs` is **fail-closed** — it does **not** silently switch to Clara Parabricks.
- Clara is selected only via explicit `alignmentMode: linear|pangenome` (and `actionConfig.parabricks`), never as an automatic Mojo fallback.
- CPU `vg` align is for unknown GPU vendors and scientific parity, not the production GPU path.

## Mode-aware QC

Alignment QC shares guardrails across modes and adds tool-family checks:

- Parabricks-family metrics for `linear` / `pangenome`
- methylGrapher-family provenance and conversion-rate checks for `pangenome_wgbs`

See [Sample prep and QC](03-sample-prep-and-qc.md), [Sample preparation flow](../implementation/sample-preparation-flow.md), and [methylalignmentqc USAGE](../../packages/methylalignmentqc/docs/USAGE.md).

## Sibling science contracts (mojo-align)

| Document | Role |
|----------|------|
| `giraffe/docs/GIRAFFE_SPEC.md` | Native Mojo Giraffe GBZ → GAF contract for `pangenome_wgbs` |
| `fq2bam-meth/docs/LINEAR_FQ2BAM_SPEC.md` | MojoFq2bamMeth BAM + QC JSON contract for `linear` + `engine=mojo` |
| `fq2bam-meth/docs/LINEAR_ENGINES.md` | FM-index default (`engine=fm`) vs frozen k-mer `parity` / experimental `speed` |
| `giraffe/docs/PHASE0_GH200_ALIGN.md` | Why Parabricks `pbrun giraffe` cannot emit science GAF |
| `giraffe/docs/BENCHMARK_GIRAFFE.md` | Operator wall-time / parity gates vs `vg` |
| `fq2bam-meth/docs/BENCHMARK_FQ2BAM_METH.md` | Clara vs Mojo linear bakeoff gates |
| `giraffe/docs/ROCM_GIRAFFE_GATES.md` | AMD ROCm image and host gates |

Clone path on developer hosts is typically alongside this repo (`../mojo-align`). Set `MOJO_ALIGN_ROOT` to that checkout (or leave unset to auto-detect). Docker images are built via `scripts/build_mojo_align_image.sh` and documented under [`workers/docker/methylgrapher/README.md`](../../workers/docker/methylgrapher/README.md). The in-container install prefix is `/opt/mojo-align`. Env knobs use the `MOJO_ALIGN_*` family (one-release dual-read of deprecated `METHYLGRAPHER_MOJO_*`).

## Related

- [SamplePrep tooling (cross-repo)](../architecture/sample-prep-tooling.md)
- [Mojo multi-GPU dual align](../architecture/mojo-multi-gpu-dual-align.md)
- [Mojo fq2bam parity](../reference/mojo-fq2bam-meth-parity.md)
- [Action parameter contract](../reference/action-parameter-contract.md)
- [Worker ROCm](../deployment/worker-rocm.md)
