# GoliathOmics documentation

This repository is the genomics product. The platform (gateway, engine DDL, RBAC, portal shell, claim protocol) is [GoliathApp](https://github.com/Goliath-Research/GoliathApp). The map of the whole workspace is [GoliathApp/docs/workspace-index.md](../../GoliathApp/docs/workspace-index.md).

GoliathWorkflow is not a documented run path.

## Sections

| Section | What it covers |
|---------|----------------|
| [usage](usage/index.md) | How to run studies, SamplePrep, and validation |
| [theory](theory/index.md) | Statistical and biological background |
| [architecture](architecture/) | Component boundaries inside the product |
| [regulatory](regulatory/README.md) | SaMD and operational controls |
| [research](research/README.md) | Notes and assessments |
| [contracts](contracts/omics-tool-requirements.md) | Features required from GoliathAlign and MethylExtractor |

## Ownership

| Concern | Owner |
|---------|--------|
| Gateway, engine DDL, `goliath-cfg`, shared worker | GoliathApp |
| Science packages, DomainPrograms, profiles, analytes, `methyl_worker` handlers, science seeds | GoliathOmics |
| Alignment image | GoliathAlign (`GOLIATH_ALIGN_*`; `MOJO_ALIGN_*` is a one-cycle alias) |
| Linear BAM → HDF5 | MethylExtractor |

## Names

Older chapters say **MethylPipeline**. That name means this genomics product. `methyl-*` remains the CLI alias for one release cycle (`methyl-workflow-run`, `methyl-cfg`). Platform commands use the `goliath` prefix and are documented with GoliathApp.

Older chapters say **mojo-align**. That repository is **GoliathAlign**.
