# goliath-worker container image

Provider-neutral GPU worker with **NVIDIA Clara Parabricks 4.7.0-1** on `PATH`
(`pbrun`). SamplePrep aligners use **host** binaries — no nested Docker.

## Base / licensing

`FROM nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1` (Ubuntu 22.04). NGC API key
required. **Keep GHCR private** (Parabricks EULA). RunPod needs
`RUNPOD_REGISTRY_AUTH_ID` / `--registry-auth`.

## Offline system-deps

Release builds full `ubuntu-22.04-amd64` offline apt repos (jammy + updates +
security + deadsnakes python3.12). Pods install from `/work` automatically.

## GoliathAlign / Mojo (automatic)

`release-assemble` downloads `goliath-align-host-linux-amd64-jammy.tar.gz`
(GoliathAlign GitHub Release, built on jammy) into the deploy bundle. On every
boot, `start_shared_worker.sh` → `goliath_align_local.sh` extracts it onto
**local disk** (same reason as MethylExtractor: `/work` cannot exec reliably)
and sets `METHYL_METHYLGRAPHER_BIN` / `GOLIATH_ALIGN_HOME`.

| Path | Host binary |
|------|-------------|
| Clara `fq2bam_meth` / `giraffe` / metrics | `pbrun` (in image) |
| MojoFq2bamMeth / `pangenome_wgbs` | `methylGrapher` (from release) |

No manual `stage_goliath_align_host.sh`.

## Build

See `.github/workflows/goliath-worker-image.yml` (secret `NGC_API_KEY`).
