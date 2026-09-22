# Tool features required by GoliathOmics

GoliathOmics consumes **mojo-align** and **MethylExtractor** only through the features below. A change in either tool that removes or renames a listed feature breaks GoliathOmics, even when the platform (`goliath-gateway`, database `goliath`, `GOLIATH_WORK_ROOT`) is unchanged.

Inherited platform pieces use the `goliath` prefix. These tools stay separate repositories. They are not part of GoliathApp.

## mojo-align

Toolchain pin: Mojo 1.1 / Modular 26.6. Not Mojo 1.0.0b2.

Image: `goliath/methylgrapher`. In-image tree: `/opt/mojo-align`.

Consumer: `workers/methyl_worker/methylgrapher_wgbs_runner.py` (`sample.methylgrapher_wgbs_align`, `sample.methylgrapher_wgbs_extract`).

| Feature | What a break looks like |
|---------|-------------------------|
| `methylGrapher` Align | WGBS pangenome alignment cannot start. No silent fallback to stock Giraffe. |
| `methylGrapher MethylCall` | Graph-aware methylation calls are not produced. Requires PrepareGenome artifacts (`{index_prefix}.wl.gfa`) on disk first. |
| `methylGrapher MergeCpG` | CpG merge step of the WGBS extract path fails. |
| `methylGrapher help` | The worker cannot probe the binary. |
| GPU target `METHYLGRAPHER_TARGET_ACCELERATOR` | NVIDIA targets from `sm_90` upward, or an AMD arch via `METHYLGRAPHER_AMDGPU_ARCH`. Hosts whose default GPU is older than `sm_52` do not compile. |
| Outputs: GAF, QC BAM, graph-aware H5 | Downstream QC and extraction have nothing to read. |

## MethylExtractor

Not used on the WGBS pangenome path. That path uses mojo-align MethylCall / MergeCpG.

Binary layout: `/work/goliath/methyl-extractor-<arch>/`. `HDF5_PLUGIN_PATH` points at the zstd plugin shipped beside the binary. `GOLIATH_WORK_ROOT` (alias `METHYL_WORK_ROOT`) is the mount that holds that tree. Workflows that never extract do not need the mount.

Consumer: `workers/methyl_worker/extract_runner.py` (`sample.methyl_extract`) and `methylextractionqc`.

| Feature | What a break looks like |
|---------|-------------------------|
| `MethylExtractor --help` | The worker cannot confirm the binary or which flags it supports. |
| `--threads` | Thread count from resolved config is ignored. |
| `--chrom-parallel` | Chromosome-parallel extract cannot be requested. |
| `--max-rss-gb` | RSS cap from resolved config is ignored. |
| `--min-mapq`, `--min-phred`, `--min-cov`, `--cap-cov` | Alignment and coverage filters no longer match the action config. |
| `--CHG`, `--CHH` | Contexts other than CG are dropped. CG stays implicit. |
| `--chrom-mapping` | Contig name mapping no longer matches the reference. |
| `--compression`, `--chunk-size` | HDF5 layout written for downstream readers changes. |
| `--read-level` | Pattern-level output is skipped and only marginal H5 is emitted. |
| `{chrom}-{context}.json` sidecars | `methylextractionqc` cannot build the extraction manifest. |
| HDF5 output plus the zstd plugin | Sample H5 files do not open under `HDF5_PLUGIN_PATH`. |
