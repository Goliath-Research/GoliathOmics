# Linear WGBS Align: Clara Parabricks or MojoFq2bamMeth

This path is selected by **explicit** `alignmentMode: linear` (or stock `pangenome` for Giraffe) — never as an automatic fallback when `pangenome_wgbs` native-Mojo fails. Sample prep alignment (`sample.parabricks_fq2bam`) runs **either**:

1. **NVIDIA Clara Parabricks `fq2bam_meth`** (`actionConfig.parabricks.engine=parabricks`) — NGC Docker, CUDA only  
2. **MojoFq2bamMeth** (`engine=mojo`) — `goliath/methylgrapher:1.70-mojo{,-cuda,-rocm}`, portable `align_device=auto|nvidia|amd` (`cpu` only for unknown GPU vendors)

Implementation: [`methyl_worker/parabricks_runner.py`](../methyl_worker/parabricks_runner.py). Dual-Align strategy: [`docs/architecture/mojo-multi-gpu-dual-align.md`](../../docs/architecture/mojo-multi-gpu-dual-align.md). For WGBS pangenome see [`workers/docker/methylgrapher/README.md`](../docker/methylgrapher/README.md).

## Prerequisites

### Clara (`engine=parabricks`)

- NVIDIA GPU driver + `nvidia-smi`
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (`docker run --gpus all`)
- Parabricks image pulled locally (NGC license)
- **samtools** on the worker host PATH (methyl_qc flagstat)

### Mojo (`engine=mojo`)

- CUDA **or** ROCm host (or CPU-only with `align_device=cpu`)
- mojo-align image (`goliath/methylgrapher:1.70-mojo-*`) with `bwa` + `samtools` (see Dockerfile.mojo)
- ROCm: see [`docs/deployment/worker-rocm.md`](../../docs/deployment/worker-rocm.md)

## Configuration (site / profile → resolvedConfig)

Prefer DB-backed `actionConfig.parabricks` (not host env for science knobs):

```json
"parabricks": {
  "engine": "mojo",
  "align_device": "auto",
  "image": "goliath/methylgrapher:1.70-mojo-rocm",
  "bwa_threads": 32,
  "write_methylation_tags": true
}
```

`write_methylation_tags` is a Mojo-only env (`METHYLGRAPHER_WRITE_METH_TAGS=1`) that annotates the BAM with Bismark-style `XM:Z` / `XG:Z` after align. Native MHL / MethylExtractor prefer those tags and fall back to sequence+`XG` when they are absent (Clara `fq2bam_meth` today). Default off except on the `cfdna_emseq_mhl_survival` procedure.

Clara rollback:

```json
"parabricks": {
  "engine": "parabricks",
  "image": "nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1",
  "gpu_flags": "--gpus all",
  "bwa_threads": 16
}
```

Legacy env (local/dev only): `METHYL_PARABRICKS_IMAGE`, `METHYL_PARABRICKS_ENGINE`, `METHYL_PARABRICKS_GPU_FLAGS`, `METHYL_PARABRICKS_BWA_THREADS`.

## Smoke test

```bash
export METHYL_PARABRICKS_IMAGE=nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1
./scripts/verify_parabricks.sh
```

## Outputs

Under `{sampleDir}`:

- `{sampleId}.bam` — input to `sample.methyl_extract`
- `{sampleId}.qc-metrics.tar` — input to `sample.methyl_qc`
- `{sampleId}.json` — Parabricks-shaped metrics (Mojo emits MVP subset; see [`docs/reference/mojo-fq2bam-meth-parity.md`](../../docs/reference/mojo-fq2bam-meth-parity.md))

See [`workflow_engine/contract/sample_prep_capabilities.md`](../../workflow_engine/contract/sample_prep_capabilities.md).
