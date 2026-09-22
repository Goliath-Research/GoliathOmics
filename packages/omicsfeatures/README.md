# omics_features

Shared `samples x features` seam for MethylPipeline non-methylation omics packs. Both
RNA-Seq (`rna_express`) and proteomics (`proteomics_features`) are thin adapters over this
package; it is the analyte-agnostic analogue of the methylation centroid/detector stack.

Provides:

- **Per-sample feature contract** (`feature_store`): a canonical per-sample HDF5
  (`{sample_id}.<kind>.h5` with datasets `feature_id`, `value`) plus read/find helpers.
- **Cohort matrix loader** (`matrix.load_feature_matrix`): stack per-sample HDF5 into a
  dense `samples x features` matrix with pluggable `transform` (logcpm / log2 / cpm /
  none), `normalize` (median / quantile / none), and `impute` (min / zero / none).
- **Differential-feature selection + classification** (`de_select`): Welch differential
  feature ranking + a tabular sklearn classifier (optionally stacking covariates via
  `methyl_validation.covariate_preprocessor`), emitting a recurrence-friendly panel CSV
  for `validation.stability`.
