# Sample preparation tooling

GoliathOmics creates the sample directories and chooses the tool. GoliathAlign and GoliathExtractor write only the directory the worker gives them. They do not import this product or GoliathApp. See [Dependency direction](dependencies.md).

| Step | Tool | Where the bytes are written |
|------|------|------------------------------|
| FASTQ → BAM | Clara Parabricks `fq2bam_meth`, GoliathAlign on the same action (`parabricks.engine=mojo`), Clara `giraffe`, or methylGrapher | Worker-local `{METHYL_LOCAL_TMP}/bam/{slug}-{digest}/{sample_id}.bam` |
| Linear or stock-pangenome BAM → HDF5 | GoliathExtractor | Worker-local `{METHYL_LOCAL_TMP}/extract/{slug}-{digest}/`, then `*.h5` and `*.json` published into `sampleDir` |
| WGBS pangenome calls | GoliathAlign `MethylCall` / `MergeCpG` | Same local extract directory, then `*.h5` and `*.json` published into `sampleDir` |

`sampleDir` is `{sampleRoot}/align.{mode}.{engine}/` on the shared mount. The BAM stays on the align worker until `sample.delete_bam`. The pin and the four align paths are in [Start a SamplePrep instance](../operators/start-sample-prep.md).

Operator matrix: [alignment engines](../usage/alignment-engines.md). Required tool features: [omics-tool-requirements.md](../contracts/omics-tool-requirements.md). Image bake: [workers/docker/methylgrapher/README.md](../../workers/docker/methylgrapher/README.md).

`GOLIATH_ALIGN_ROOT` points at the GoliathAlign checkout. `MOJO_ALIGN_ROOT` and the directory name `mojo-align` remain one-cycle aliases.
