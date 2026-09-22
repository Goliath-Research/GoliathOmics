# rna_alignment_qc

RNA-Seq alignment/quantification QC for the MethylPipeline transcriptomics process
pack. Parallel to `methyl_alignment_qc` (WGBS), but the guardrails are RNA-specific:
mapping / pseudoalignment rate, number of genes detected, and (for STAR) the uniquely
mapped fraction. It does **not** apply bisulfite-conversion or CpG-coverage checks.

Reads whichever quantifier outputs are present under the sample directory:

- `pbrun rna_fq2bam`: `{sample_id}.star/Log.final.out` and `{sample_id}.gene_counts.tsv`
- `pbrun kallisto`: `{sample_id}.kallisto/run_info.json` and `abundance.tsv`

Writes `{sample_id}.rna_qc.json` with a guardrails block whose `overall_pass` gates the
DomainProgram (`sample.rna_qc` → `${qcPass}`).
