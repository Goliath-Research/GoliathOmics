# Bedtools Mapper

The bedtools mapper is the supported local mapping engine behind the current MethylMapper workflow.

## Preferred Usage

Project-driven:

```bash
methyl-mapper --project /path/to/project.json
```

Direct:

```bash
methyl_mapper_bedtools \
  --csv-pattern "/path/to/dmps-*.csv" \
  --gtf /path/to/gencode.gtf \
  --output-dir /path/to/out
```

## Expected Inputs

- detector DMP CSVs, typically `dmps-*.csv`
- a GTF annotation file

### Minimum DMP CSV schema (for non-MethylDetector sources)

If you provide your own DMP table, `methyl_mapper_bedtools` only requires:

- `chromosome` (aliases accepted: `chrom`, `chr`)
- `position` (aliases accepted: `pos`)

Column-name normalization is case-insensitive and also normalizes spaces/hyphens, so these are valid examples:

- `p-value` -> `p_value`
- `q-value` -> `q_value`
- `effect size` -> `effect_size`
- `delta mean` -> `delta_mean`

Recommended additional columns (improve ranking/statistics but are not mandatory):

- `context` (especially for mixed-context runs: CG/CHG/CHH)
- `effect_size` (used by weighting and BED name metadata)
- `p_value` and `q_value` (used in gene-level significance summaries)
- `delta_mean` (used for signed directionality / Stouffer-style aggregation)
- `direction` (used as direction/sign fallback when `delta_mean` is absent)

`direction` accepted values include common forms like `hyper`/`hypo`, `up`/`down`, `+`/`-`, `1`/`-1`.

Data expectations:

- `position` must be numeric and interpreted as 1-based genomic coordinate.
- `chromosome` may be `1..22/X/Y` or `chr1..chr22/chrX/chrY` (mapper normalizes both).

Minimal valid CSV example:

```csv
chromosome,position
1,123456
chr2,789012
X,456789
```

Recommended rich CSV example:

```csv
chromosome,position,context,p_value,q_value,effect_size,delta_mean
1,123456,CG,1.2e-07,3.4e-06,0.42,-0.38
chr2,789012,CHG,2.0e-05,1.1e-04,0.31,0.27
X,456789,CG,8.1e-04,2.9e-03,0.18,-0.12
```

## Expected Outputs

- per-feature overlap CSVs
- combined gene tables such as `all-gene_name-combined.csv`

## Notes

- Comparison-aware project runs resolve input/output directories automatically.
- Disease enrichment credentials should come from environment variables or local override files.
- **Default GTF mapping** uses all feature types in the file (gene, transcript, exon, CDS, UTR, …). Pass `--feature-types gene exon` (for example) to restrict.
- **Grok** runs synchronously (one request at a time, ≤20 genes per batch) unless you opt in to the xAI Batch API (`--grok-batch-api`). **Open Targets** supplies disease evidence and scores in merged tables.
- Optional: `--auxiliary-bed path.bed` (repeatable) for regulatory overlaps; `--closest-gene` for nearest gene body per DMP.
