# methylGrapher worker image

Pinned runtime for `alignmentMode: pangenome_wgbs` (`sample.methylgrapher_wgbs_*`).

Canonical SamplePrep contract: [`docs/implementation/sample-preparation-flow.md`](../../../docs/implementation/sample-preparation-flow.md) · [`docs/architecture/mojo-multi-gpu-dual-align.md`](../../../docs/architecture/mojo-multi-gpu-dual-align.md).

## Engines (dual-ship)

| `actionConfig.methylgrapher_wgbs.engine` | Image (typical) | Notes |
|-----------------------------------------|-----------------|-------|
| `mojo` (**canonical**) | `goliath/methylgrapher:1.70-mojo-cuda` or `:1.70-mojo-rocm` | native-Mojo Align / MethylCall / MergeCpG; same userspace binary; host runtime + tag select NVIDIA vs AMD |
| `python` (dev/parity rollback) | `goliath/methylgrapher:1.70` | Stock methylGrapher 0.2.0 + GAF-header patch |

Build Mojo image: `scripts/build_mojo_align_image.sh` (requires `MOJO_ALIGN_ROOT` pointing at **mojo-align**, or auto-detect of sibling `../mojo-align`, and a prior vg bake from `build_methylgrapher_image.sh`). Set `MOJO_ALIGN_GPU_VARIANT=cuda|rocm` for the twin tags. Plan: [`docs/plans/archive-methylgrapher-mojo.plan.md`](../../../docs/plans/archive-methylgrapher-mojo.plan.md).

## Compute model (native-Mojo GPU)

`pangenome_wgbs` Align runs **native-Mojo** Giraffe (`giraffe_stream_map`) via `std.gpu.host.DeviceContext`:

| Vendor | API | Image tag | Container devices |
|--------|-----|-----------|-------------------|
| NVIDIA | `cuda` (e.g. `sm_90`) | `:1.70-mojo-cuda` | `--gpus all` (or site GPU flags) |
| AMD | `hip` (e.g. `gfx942`) | `:1.70-mojo-rocm` | `/dev/kfd` + `/dev/dri` (see [`docs/deployment/worker-rocm.md`](../../../docs/deployment/worker-rocm.md)) |

| Mode | Tool | Accelerator |
|------|------|-------------|
| `pangenome_wgbs` | native-Mojo methylGrapher Align | **NVIDIA CUDA or AMD HIP** (fail-closed on known GPUs) |
| `linear` / `pangenome` | Clara Parabricks (explicit config) | NVIDIA GPU only |
| Unknown GPU vendor | `align_engine=cpu_vg` / host seeds | CPU last resort |

Pin `actionConfig.methylgrapher_wgbs.align_engine=gpu_giraffe|mojo_giraffe` and `giraffe_device=auto|nvidia|amd`. Clara Parabricks is **never** an automatic Mojo failure path — choose `alignmentMode: linear|pangenome` explicitly. Extract (`MethylCall`/`MergeCpG`) is native-Mojo with Mojo `parallelize()` (CPU-parallel, not a CUDA/HIP kernel).

On 64 KB-page ARM64 (Grace / GH200) any vg-assisted QC BAM path needs `jemalloc=off` vg baked into the image.

**Code default flag:** the sibling `mojo-align` launcher may still default `align_engine=cpu_vg` for dual-ship rollback until Buffy ≤2 h + DS20M gates pass. Production site/profile config should pin Mojo GPU as above.

## Production model (do not compile on deploy)

| When | What |
|------|------|
| **CI / image refresh** | `scripts/build_methylgrapher_image.sh` (vg bake) then `scripts/build_mojo_align_image.sh` (Mojo stage + CUDA/ROCm twin). |
| **Cluster deploy** | Set `METHYL_METHYLGRAPHER_IMAGE` (or site `actionConfig.methylgrapher_wgbs.image`), then `scripts/ensure_methylgrapher_image.sh` → **pull or load only**. |

Rebuild the image only when **vg**, **mojo-align**, or this Dockerfile changes — not on every study or release promote.

## Why arm64 builds vg from source

Stock GitHub / quay.io arm64 `vg` binaries link jemalloc compiled for **4 KB** pages. On **64 KB** kernels (Grace / GH200) they abort with `Unsupported system page size`. CI builds `make jemalloc=off` once and bakes that binary into the image.

Amd64 uses the stock vg release binary.

## Local (developer) build

```bash
./scripts/build_methylgrapher_image.sh
MOJO_ALIGN_GPU_VARIANT=cuda ./scripts/build_mojo_align_image.sh
# optional: METHYLGRAPHER_IMAGE_TAR=/tmp/mg.tar.gz
bash workers/docker/methylgrapher/smoke_64k.sh   # on a real 64K host
```

## Deploy on a worker

```bash
# NVIDIA
export METHYL_METHYLGRAPHER_IMAGE=goliath/methylgrapher:1.70-mojo-cuda
# AMD
# export METHYL_METHYLGRAPHER_IMAGE=goliath/methylgrapher:1.70-mojo-rocm
./scripts/ensure_methylgrapher_image.sh
```

`scripts/write_worker_env.sh` writes `METHYL_METHYLGRAPHER_IMAGE` from `platform_matrix.env` when set.
