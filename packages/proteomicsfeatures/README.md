# proteomics_features

Proteomics adapter over the shared `omics_features` seam. Ingests protein abundances from
either DIA-NN (`report.tsv`, GPU search) or a panel matrix (Olink NPX / SomaScan RFU /
open matrix, CPU) into the canonical `{sample}.abundance.h5` contract, and exposes
differential-abundance selection (`pipeline.protein_de_select`) that reuses the generic
tabular classifier + covariate stacking. Proteomics differs from RNA-Seq only in
normalization/imputation and the feature key (protein vs gene).
