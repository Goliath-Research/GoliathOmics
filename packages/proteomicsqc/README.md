# proteomics_qc

Proteomics QC guardrails for the MethylPipeline proteomics pack. Reads the registered
`{sample}.abundance.h5` (and optional DIA-NN report) and gates on proteins identified and
(when an expected panel size is configured) missingness fraction. It does not apply
bisulfite/CpG or RNA-mapping checks. Writes `{sample}.proteomics_qc.json` whose
`guardrails.overall_pass` gates the DomainProgram.
