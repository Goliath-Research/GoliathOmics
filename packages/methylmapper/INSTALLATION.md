# MethylMapper Installation

## Repository Install

From the repo root, prefer the shared install scripts:

```bash
bash scripts/setup_host.sh --system-deps --gpu
```

Or, if the environment already exists:

```bash
bash scripts/install_all.sh --pipeline-reqs
```

## Package-Only Install

```bash
pip install -e packages/methylutils
pip install -e packages/methylmapper
```

System requirement:

- `bedtools`

Optional environment variables:

- `GENE_GTF`
- `GROK_API_KEY`

## Supported Entry Points

- `methyl-mapper`
- `methyl_mapper`
- `methyl_mapper_bedtools`
- `methyl_mapper_credentials`

`methyl-mapper --project ...` is the primary supported workflow.
