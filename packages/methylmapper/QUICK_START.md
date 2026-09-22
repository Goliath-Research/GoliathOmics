# MethylMapper Quick Start

## Recommended Workflow

Run MethylMapper from a project:

```bash
methyl-mapper --project /path/to/project.json
```

This reads detector outputs from the canonical comparison layout and writes results to the matching mapper comparison directory.

## Direct Bedtools Run

```bash
methyl_mapper_bedtools \
  --csv-pattern "/path/to/dmps-*.csv" \
  --gtf /path/to/gencode.gtf \
  --output-dir /path/to/out
```

## Notes

- Install `bedtools` on the host or use the repository setup scripts.
- Use `GROK_API_KEY` from the environment if disease enrichment is enabled.
- The project-aware bedtools flow is the supported path; legacy Azure SQL mode is compatibility-only.
- For custom non-detector DMP files, minimum schema is `chromosome` + `position`; see [`BEDTOOLS_MAPPER_README.md`](BEDTOOLS_MAPPER_README.md) for accepted aliases and full examples.
