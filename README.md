# GoliathOmics

GoliathOmics specializes [GoliathApp](https://github.com/Goliath-Research/GoliathApp) for genomics. It calls [GoliathAlign](https://github.com/Goliath-Research/GoliathAlign) and [MethylExtractor](https://github.com/Goliath-Research/MethylExtractor) as standalone utilities. GoliathApp contains no GoliathOmics types. GoliathAlign and MethylExtractor contain no reference to GoliathApp or GoliathOmics. The edges are drawn in [docs/architecture/dependencies.md](docs/architecture/dependencies.md).

The product is ready for a full sample-alignment workflow with QC and configurable parameters, and for process packs that cover analytes, library techniques, and disease or trait studies. Release **2026.10.27** is the tag, assemble, and deploy process in [docs/operators/release.md](docs/operators/release.md).

## What lives here

- Science packages (`packages/`), including methylation, RNA-seq, and proteomics
- DomainPrograms, profiles, analytes, and cohort checks (`workflow_engine/domain/`)
- Action handlers in `workers/methyl_worker/` and the methylgrapher image wiring
- Database content GoliathApp does not own: `cfg.analyte` (`cfg_analyte_catalog.sql`), action-catalog and data-type seeds, reference-asset seeds, and study pipelines
- Product docs under `docs/usage`, `docs/theory`, `docs/operators`, `docs/regulatory`, and `docs/research`

Operator guides:

- [Start a SamplePrep instance](docs/operators/start-sample-prep.md)
- [Release, deploy, and worker enrollment](docs/operators/release.md)
- [Process packs](docs/operators/process-packs.md)

Manual index: [docs/README.md](docs/README.md).

## What stays upstream

- Python gateway. The wheel's console script is `methyl-gateway`, built from GoliathApp. This repository does not contain `workflow_engine/rest/`. `goliath-gateway` is the platform name for that service.
- Engine DDL for Meta, RBAC, the portal shell, `wf`, and `cfg`
- Shared claim/submit worker protocol and the optional `/work` layout
- Portal UI. It is still written in Delphi and is migrating to Node.js and React. It is not part of GoliathOmics.

Inherited platform names use the `goliath` prefix (`goliath-gateway`, `goliath-cfg`, `GOLIATH_WORK_ROOT`, database `goliath`). `methyl-*` remains the genomics CLI alias for one release cycle.

GoliathWorkflow is not a run path.

## Tools

GoliathOmics calls GoliathAlign and MethylExtractor through a pinned image and binary. A change in either tool can break this product. The required features are listed in [docs/contracts/omics-tool-requirements.md](docs/contracts/omics-tool-requirements.md).

`GOLIATH_ALIGN_ROOT` points at the GoliathAlign checkout (sibling `../GoliathAlign`). `MOJO_ALIGN_ROOT` and sibling `../mojo-align` remain one-cycle aliases.

## Docs site

An optional MkDocs Material site renders `docs/`. It is not published.

```bash
python -m pip install -r requirements-docs.txt
mkdocs build
mkdocs serve
```

## Machine-readable summary

[`.goliath/product.json`](.goliath/product.json) is the summary GoliathWeb reads. The schema is [`.goliath/product.schema.json`](.goliath/product.schema.json). `dependsOn` lists `goliathapp`, `goliathalign`, and `methylextractor`.
