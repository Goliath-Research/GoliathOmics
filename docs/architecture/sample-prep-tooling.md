# Sample preparation tooling

GoliathOmics creates the sample directories. GoliathAlign and MethylExtractor write only the work directory they are given.

| Step | Tool | Output |
|------|------|--------|
| FASTQ → BAM or GAF | GoliathAlign (or Clara Parabricks when the profile says so) | `align.*` under `/work/samples/<sampleId>/` |
| Linear BAM → HDF5 | MethylExtractor | `extract.methylextractor/` |
| WGBS pangenome calls | GoliathAlign `MethylCall` / `MergeCpG` | Graph-aware H5 next to the GAF |

Operator matrix: [alignment engines](../usage/alignment-engines.md). Required tool features: [omics-tool-requirements.md](../contracts/omics-tool-requirements.md). Image bake: [workers/docker/methylgrapher/README.md](../../workers/docker/methylgrapher/README.md).

`GOLIATH_ALIGN_ROOT` points at the GoliathAlign checkout. `MOJO_ALIGN_ROOT` and the directory name `mojo-align` remain one-cycle aliases.
