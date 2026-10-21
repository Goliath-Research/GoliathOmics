# GoliathOmics documentation

This repository is the genomics product. It specializes [GoliathApp](https://github.com/Goliath-Research/GoliathApp) and calls [GoliathAlign](https://github.com/Goliath-Research/GoliathAlign) and [MethylExtractor](https://github.com/Goliath-Research/MethylExtractor) as standalone utilities. GoliathApp does not reference this product. The two utilities do not reference GoliathApp or GoliathOmics. See [Dependency direction](architecture/dependencies.md).

GoliathWorkflow is not a run path.

Release **2026.10.27** follows the tag, assemble, and deploy process in [operators/release.md](operators/release.md).

## Sections

| Section | What it covers |
|---------|----------------|
| [operators](operators/start-sample-prep.md) | Start a SamplePrep instance, release and enroll workers, process packs |
| [usage](usage/index.md) | How to run studies, SamplePrep, and validation |
| [theory](theory/index.md) | Statistical and biological background |
| [architecture](architecture/dependencies.md) | Dependency direction and sample-prep tooling |
| [regulatory](regulatory/README.md) | SaMD and operational controls |
| [research](research/README.md) | Notes and assessments. These are not the operator contract. |
| [contracts](contracts/omics-tool-requirements.md) | Features required from GoliathAlign and MethylExtractor |
| [deploy-targets](deploy-targets.md) | Deploy profiles |
| [rollouts](rollouts.md) | Deploy reporting |

The optional MkDocs Material site builds this tree and is not published. From the repository root: `python -m pip install -r requirements-docs.txt && mkdocs serve`.

## Ownership

| Concern | Owner |
|---------|--------|
| Gateway wheel, engine DDL, `goliath-cfg`, shared claim protocol | GoliathApp. The installed console script is `methyl-gateway`. This repo does not contain `workflow_engine/rest/`. |
| Science packages, DomainPrograms, profiles, analytes, `methyl_worker` handlers, science seeds | GoliathOmics |
| Alignment image and binaries | GoliathAlign (`GOLIATH_ALIGN_*`; `MOJO_ALIGN_*` is a one-cycle alias) |
| Linear BAM to HDF5 | MethylExtractor |

## Names

Older chapters say **MethylPipeline**. That name means this genomics product. `methyl-*` remains the CLI alias for one release cycle (`methyl-workflow-run`, `methyl-cfg`, `methyl-study-start`). Platform commands use the `goliath` prefix and are documented with GoliathApp.

Older chapters say **mojo-align**. That repository is **GoliathAlign**.

Chapters that link to `docs/implementation/`, `docs/reference/`, `docs/deployment/`, or `docs/plans/` describe documents that are not in this tree. The operator guides and the code are the current contract. Theory and research notes stay as background.
