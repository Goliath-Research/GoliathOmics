# MethylEnricher Installation

## Repository Install

```bash
bash scripts/setup_host.sh --system-deps --gpu
```

Or install package dependencies manually:

```bash
pip install -e packages/methylutils
pip install -e packages/methylenricher
```

## Supported Usage

Project-driven:

```bash
methyl-enricher --project /path/to/project.json
```

Direct:

```bash
methyl-enricher --input /path/to/all-gene_name-combined.csv --outdir /path/to/out
```
