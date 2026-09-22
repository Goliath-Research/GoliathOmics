MethylPipeline supports **RNA-Seq (transcriptomics)** as a second omics modality
alongside DNA methylation. The execution platform (DomainProgram, typed actions,
scheduler, CAAS, cfg/wf) is shared; only the science layer differs. This chapter is the
operator guide for running the RNA-Seq pack.

## Modality selection

Set `regulatory.primary_modality` in the study manifest (or `methyl-study-init
--modality rnaseq`):

```json
"regulatory": {
  "primary_modality": "rnaseq",
  "intended_use_summary": "Research-use RNA-Seq classifier for healthy vs disease.",
  "stage": "expanded_development"
}
```

`primary_modality` (`methylation` | `rnaseq`) is distinct from `primary_analyte`
(the DNA-methylation sample matrix). It defaults to `methylation` when unset, so
existing methylation studies are unaffected.

## Quantifier selection (STAR vs kallisto)

The RNA pack exposes two Parabricks quantifiers, selected exactly like the WGBS
linear/pangenome choice. Set `actionConfig.rna_align.quant_mode`:

| `quant_mode` | Action | Output |
|--------------|--------|--------|
| `star` (default) | `sample.parabricks_rna_fq2bam` (`pbrun rna_fq2bam`) | coordinate-sorted BAM + STAR gene counts |
| `kallisto` | `sample.kallisto` (`pbrun kallisto`) | transcript-level `abundance.tsv` (est_counts + TPM) |

The DomainProgram branches on the derived `${useKallisto}` flag
(`pipeline_profiles.py`), mirroring `${usePangenome}`.

## Reference assets (site)

Pin RNA references once per cluster under site `rna_reference` (see
`workflow_engine/domain/profiles/site_grch38.example.json`):

```json
"rna_reference": {
  "star_index_dir": "/work/genomes/rna/GRCh38/star/ensembl-116",
  "gtf": "/work/genomes/annotation/gencode/v50/gencode.v50.annotation.gtf",
  "reference_fasta": "/work/genomes/linear/GRCh38/ensembl-116/Homo_sapiens.GRCh38.dna.primary_assembly.fa",
  "kallisto_index": "/work/genomes/rna/GRCh38/kallisto/gencode.v50.transcripts.idx",
  "transcriptome_fasta": "/work/genomes/rna/GRCh38/kallisto/gencode.v50.transcripts.fa",
  "tx2gene": "/work/genomes/rna/GRCh38/kallisto/gencode.v50.tx2gene.tsv"
}
```

Provision with `scripts/download_rna_reference_grch38.sh` (builds the STAR + kallisto
indexes and derives `tx2gene` from the GTF). The script prefers host STAR/kallisto when
present (otherwise Clara `nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1`). GENCODE PRI
uses `chr1` while the Ensembl primary_assembly FASTA uses `1`/`MT`; STAR is given a
temporary seqname-remapped GTF so splice junctions index correctly. The inventory GTF
keeps GENCODE chromosome names.

## Sample prep flow

Program `sample_prep_rnaseq.program.json`:

```
download_fastq
  -> IF useKallisto THEN sample.kallisto ELSE sample.parabricks_rna_fq2bam
  -> sample.rna_qc
  -> IF qcPass
       -> sample.register_expression   # writes {sample_id}.expression.h5
       -> sample.archive_sample -> (delete_fastqs)
     ELSE archive(qc_only) -> qc_failed
```

`sample.rna_qc` (package `rna_alignment_qc`) gates on mapping / pseudoalignment rate and
number of genes detected — **not** bisulfite conversion or CpG coverage. Thresholds are
operator-set under `actionConfig.rna_qc`.

`sample.register_expression` (package `rna_express`) normalizes STAR gene counts or
kallisto transcript abundances (aggregated to genes via `tx2gene`) into a canonical
per-sample `expression.h5` (`gene_id`, `count`, `tpm`).

## Study modeling flow

Program `rnaseq_study_lifecycle.program.json` runs `pipeline.rna_de_select` per
comparison. This action:

1. loads the cohort `samples x genes` log-CPM matrix (`rna_express.load_expression_matrix`),
2. ranks genes by Welch differential expression and keeps a discriminatory panel
   (`actionConfig.rna_de_select.max_genes`),
3. trains a tabular sklearn classifier (optionally stacking covariates via
   `methyl_validation.covariate_preprocessor`) and reports cross-validated balanced
   accuracy,
4. writes a gene panel CSV (recurrence-friendly schema for `validation.stability`) and a
   results JSON.

DE gene selection replaces the methylation centroid/detector/ECDF science; Houseman /
HiTIMED cell-type deconvolution and bisulfite QC do **not** apply to RNA-Seq.

## Profile

Use `rnaseq_research` (`workflow_engine/domain/profiles/rnaseq_research.profile.json`):

```json
{ "pipelineProfile": "rnaseq_research", "actionConfig": { "rna_align": { "quant_mode": "star" } } }
```

## Example run

```bash
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/sample_prep_rnaseq.program.json \
  --context '{"projectPath":"/work/projects/my-rna-study/configs/project_Healthy_vs_Disease.json","pipelineProfile":"rnaseq_research"}'
```
