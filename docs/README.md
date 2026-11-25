# GoliathOmics documentation

GoliathOmics coordinates configurable genomics and multi-omics workflows, bringing sample preparation, automated quality control, biomarker discovery, and execution management into one application.

Its ready-to-run **SamplePrep** workflow processes paired FASTQ files through alignment, alignment QC, optional cfDNA fragmentomics, methylation extraction, and extraction QC—producing analysis-ready methylation matrices (`{chromosome}-CG.h5`) and structured QC reports (`*.json`) that feed into downstream cohort stability, panel freeze, classifier training, and validation workflows. Processing configurations and QC guardrails can be tailored to each workflow, assay procedure, and individual sample.

## Start here

- **For researchers and scientific leads** — *Establish whether the workflow produces the outputs and QC guarantees your data needs:*
  - [Sample prep and QC](usage/03-sample-prep-and-qc.md) — inputs, two-stage QC guardrails, remediation, and output artifacts
  - [Guided tutorial (healthy vs. cancer stages)](usage/16-tutorial-healthy-vs-cancer-stages.md) and [SaMD study lifecycle](usage/18-samd-study-lifecycle.md)
  - [Process packs](operators/process-packs.md) — analytes, library techniques, and disease/trait studies across methylation, RNA-seq, and proteomics
  - [Theory manual](theory/index.md) — statistical and biological foundations
- **For bioinformatics leads** — *Inspect how workflows are configured, executed, and troubleshot:*
  - [Start a SamplePrep instance](operators/start-sample-prep.md) — configuring runs, selecting alignment paths, and applying per-sample QC overlays
  - [Alignment engines](usage/alignment-engines.md) — choosing between linear Clara Parabricks, linear GoliathAlign, stock Giraffe pangenome, and methylGrapher WGBS pangenome
  - [DomainProgram catalog (Mermaid diagrams)](workflows/index.md) — visual flowchart reference for all 41 workflow fixtures
  - [Usage manual](usage/index.md) and [Orchestration: workflow-run](usage/04-orchestration-workflow-run.md)
- **For IT, platform, and operations teams** — *Understand how the system is deployed and operated:*
  - Live operator host: [https://omics.goliathresearch.com](https://omics.goliathresearch.com) (shared GoliathApp platform host). The company site lists live application hosts at [https://www.goliathresearch.com/apps/](https://www.goliathresearch.com/apps/).
  - [Site parameters](architecture/site-parameters.md) — typed `omics/SiteParameters` document, field sources, and config overlay precedence
  - [Semantic layer](architecture/semantic-layer.md) — `omics/*` classes, action methods, and verifiers
  - [Release, deploy, and worker enrollment](operators/release.md) (current release: **2026.10.27**)
  - [Deploy targets](deploy-targets.md) and [Rollouts](rollouts.md)
  - [Dependency direction](architecture/dependencies.md) — how GoliathOmics specializes [GoliathApp](https://github.com/Goliath-Research/GoliathApp) and calls [GoliathAlign](https://github.com/Goliath-Research/GoliathAlign) and [GoliathExtractor](https://github.com/Goliath-Research/GoliathExtractor)

## Documentation sections

| Section | What it covers |
|---------|----------------|
| [usage](usage/index.md) | How to evaluate outputs, run SamplePrep, execute staged validation studies, and use application packs |
| [workflows](workflows/index.md) | Mermaid flowchart catalog for all 41 DomainProgram fixtures (`workflow_engine/domain/fixtures/*.program.json`) |
| [operators](operators/start-sample-prep.md) | Start a SamplePrep instance, configure process packs, and release, deploy, and enroll workers |
| [theory](theory/index.md) | Statistical and biological background for methylation, feature selection, and validation |
| [regulatory](regulatory/README.md) | SaMD lifecycle and operational controls |
| [research](research/README.md) | Disease application deep-dives, notes, and assessments |
| [deploy-targets](deploy-targets.md) | Deployment profiles and target configurations |
| [rollouts](rollouts.md) | Deployment reporting |
| [architecture](architecture/dependencies.md) | Dependency direction, [semantic layer](architecture/semantic-layer.md), [site parameters](architecture/site-parameters.md), [PostgreSQL population](architecture/database-population.md), and sample-prep tooling |
| [contracts](contracts/omics-tool-requirements.md) | Features required from GoliathAlign and GoliathExtractor |
| [refresh-plan](refresh-plan.md) | Historical R0–R5 alignment plan (the current state is in Architecture) |

The MkDocs Material site is published from `main` at <https://goliath-research.github.io/GoliathOmics/>. Pull request CI builds it and does not deploy. Local preview from the repository root: `python -m pip install -r requirements-docs.txt && mkdocs serve`.

## Platform architecture and ownership

GoliathOmics specializes [GoliathApp](https://github.com/Goliath-Research/GoliathApp) and calls [GoliathAlign](https://github.com/Goliath-Research/GoliathAlign) and [GoliathExtractor](https://github.com/Goliath-Research/GoliathExtractor) as standalone utilities. GoliathApp does not reference this product, and the two utilities do not reference GoliathApp or GoliathOmics. See [Dependency direction](architecture/dependencies.md).

| Concern | Owner |
|---------|--------|
| Gateway wheel, Portal UI and portal shell, engine DDL, `goliath-cfg`, shared claim protocol | GoliathApp. The installed console script is `methyl-gateway`. This repo does not contain `workflow_engine/rest/`. |
| Science packages, DomainPrograms, profiles, analytes, `methyl_worker` handlers, science seeds | GoliathOmics |
| Alignment image and binaries | GoliathAlign (`GOLIATH_ALIGN_*`; `MOJO_ALIGN_*` is a one-cycle alias) |
| Linear BAM to HDF5 | GoliathExtractor |

## Terminology and legacy names

Older chapters say **MethylPipeline**. That name means this genomics product. `methyl-*` remains the CLI alias for one release cycle (`methyl-workflow-run`, `methyl-cfg`, `methyl-study-start`). Platform commands use the `goliath` prefix and are documented with GoliathApp.

Older chapters say **mojo-align**. That repository is **GoliathAlign**.

Chapters that link to `docs/implementation/`, `docs/reference/`, `docs/deployment/`, or `docs/plans/` describe documents that are not in this tree. The operator guides and the code are the current contract. Theory and research notes stay as background.

