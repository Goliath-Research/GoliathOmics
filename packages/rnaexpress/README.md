# rna_express

RNA-Seq expression layer for the MethylPipeline transcriptomics process pack. This is
the RNA analogue of the methylation HDF5 / centroid / detector stack; it does **not**
reuse methylation beta values.

Provides:

- **Per-sample expression contract** (`sample.register_expression`): normalize STAR gene
  counts (`{sample_id}.gene_counts.tsv`) or kallisto transcript abundances
  (`abundance.tsv`, aggregated to genes via `rna_reference.tx2gene`) into a canonical
  `{sample_id}.expression.h5` (`gene_id`, `count`, `tpm`).
- **Cohort matrix loader** (`load_expression_matrix`): stack per-sample `expression.h5`
  into a dense `samples x genes` matrix (log-CPM), the RNA analogue of
  `MethylCentroidPair.extract_methylation_fractions`.
- **DE gene panel selection + classification** (`pipeline.rna_de_select`): Welch
  differential-expression ranking replaces the methylation centroid/detector step; the
  resulting gene panel CSV (one row per gene, with a recurrence-friendly schema) feeds
  `validation.stability`, and a tabular sklearn classifier (optionally stacking
  covariates via `methyl_validation.covariate_preprocessor`) reports balanced accuracy.
