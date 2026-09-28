# GoliathOmics

**GoliathApp** is the platform. **GoliathOmics** is the genomics product. **GoliathAlign** (formerly mojo-align) aligns reads. **MethylExtractor** calls methylation from linear BAM files. **GoliathWeb** is the public hub.

Index: [../GoliathApp/docs/workspace-index.md](../GoliathApp/docs/workspace-index.md).

Specialization of [GoliathApp](https://github.com/Goliath-Research/GoliathApp) for genomics. GoliathWorkflow is not a documented run path.

## What lives here

- Science packages (`packages/`), including methylation, RNA, and proteomics
- DomainPrograms, profiles, analytes, and cohort checks (`workflow_engine/domain/`)
- Action handlers in `workers/methyl_worker/` and the methylgrapher image wiring
- Database content GoliathApp does not own: `cfg.analyte` (`cfg_analyte_catalog.sql`), action-catalog and data-type seeds, reference-asset seeds, and study pipelines
- Product docs under `docs/usage`, `docs/theory`, `docs/regulatory`, and `docs/research`

Manual front door: [docs/README.md](docs/README.md).

## What stays in GoliathApp

- Python gateway `goliath-gateway` (`workflow_engine/rest/`). The Delphi gateway is deprecated.
- Engine DDL for Meta, RBAC, the portal shell, `wf`, and `cfg`
- Shared claim/submit worker and the optional `/work` layout
- Portal UI. It is still written in Delphi and is migrating to Node.js and React. It is not part of GoliathOmics.

Inherited platform names use the `goliath` prefix (`goliath-gateway`, `goliath-cfg`, `GOLIATH_WORK_ROOT`, database `goliath`). `methyl-*` remains the genomics CLI alias for one release cycle.

## Tools

GoliathOmics calls [GoliathAlign](https://github.com/Goliath-Research/GoliathAlign) and [MethylExtractor](https://github.com/Goliath-Research/MethylExtractor) through a pinned image and binary. A change in either tool can break this product. The required features are listed in [docs/contracts/omics-tool-requirements.md](docs/contracts/omics-tool-requirements.md).

`GOLIATH_ALIGN_ROOT` points at the GoliathAlign checkout (sibling `../GoliathAlign`). `MOJO_ALIGN_ROOT` and sibling `../mojo-align` remain one-cycle aliases.
