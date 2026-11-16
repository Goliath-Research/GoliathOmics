# GoliathOmics

GoliathOmics coordinates configurable genomics and multi-omics workflows—bringing sample preparation, automated quality control, biomarker discovery, and execution management into a single application.

Adopting GoliathOmics gives laboratories, research teams, and clinical developers a reproducible path from raw sequencing reads to analysis-ready matrices and validated study models, without requiring you to adopt the entire platform at once. An initial evaluation or deployment can focus on a single workflow—such as processing an agreed sample set through alignment, quality control, and methylation extraction—and expand into cohort validation or additional modalities when needed.

## What you receive from adopting GoliathOmics

- **Analysis-ready sample outputs with built-in QC guardrails:** The **SamplePrep** workflow processes paired FASTQ files through alignment, alignment QC, optional cfDNA fragmentomics, methylation extraction, and extraction QC. You receive chromosome-level methylation matrices (`{chromosome}-CG.h5`), read-level pattern sidecars, and structured QC reports (`*.json`), while automated gates trim-and-realign recoverable reads or quarantine failed samples before they waste compute or corrupt downstream cohorts.
- **Configurable Process Packs for your assay and study:** Pre-configured packs tailor processing parameters, reference assets, and QC thresholds to your sample matrix (`buffy_coat`, `cfdna`, `tissue`, `plant_tissue`), library technique (`wgbs_linear`, `wgbs_pangenome`, `epi_gbs`, `emseq_targeted`), and study cohort across **methylation**, **RNA-seq**, and **proteomics**.
- **End-to-end biomarker discovery and validation:** Validated sample matrices feed directly into staged study workflows—Monte Carlo stability selection, fixed-panel freeze, classifier training, holdout validation, and blind prediction—designed for both exploratory research and regulated Software as a Medical Device (SaMD) lifecycles.
- **Auditable, high-efficiency execution:** Workflow configurations and QC windows are frozen at run start (with explicit per-sample overrides when a sample requires them), and large intermediate BAM files stay on the assigned worker's local scratch storage through extraction and cleanup so shared storage only receives compact final outputs.

## Start here

Choose the path that matches your role:

### For researchers and scientific leads
*Establish whether the workflows produce the outputs and quality guarantees your study needs.*

- **Evaluate the sample-processing workflow and QC outputs:** [Sample prep and QC](docs/usage/03-sample-prep-and-qc.md)
- **Walk through an end-to-end study (healthy vs. cancer stages):** [Guided tutorial](docs/usage/16-tutorial-healthy-vs-cancer-stages.md) and [SaMD study lifecycle](docs/usage/18-samd-study-lifecycle.md)
- **Explore process packs and disease/trait applications:** [Process packs](docs/operators/process-packs.md), [Prostate cancer pack](docs/usage/25-prostate-cancer-pack.md), [Alzheimer cfDNA pack](docs/usage/21-alzheimer-cfdna-pack.md), [Plant abiotic stress pack](docs/usage/23-plant-abiotic-stress-pack.md), [RNA-seq](docs/usage/20-rnaseq-process-pack.md), and [Proteomics](docs/usage/22-proteomics-process-pack.md)
- **Review statistical and biological foundations:** [Theory manual](docs/theory/index.md)

### For bioinformatics leads
*Inspect how workflows are configured, executed, and troubleshot.*

- **Configure and run SamplePrep instances:** [Start a SamplePrep instance](docs/operators/start-sample-prep.md)
- **Compare alignment paths and scientific trade-offs:** [Alignment engines](docs/usage/alignment-engines.md) (Linear Clara Parabricks, Linear GoliathAlign, Stock Giraffe pangenome, and methylGrapher WGBS pangenome)
- **Orchestrate staged validation and inspect artifacts:** [Usage manual index](docs/usage/index.md) and [Orchestration: workflow-run](docs/usage/04-orchestration-workflow-run.md)

### For IT, platform, and operations teams
*Understand how the system is deployed, scaled, and maintained.*

- **Deploy releases and enroll workers:** [Release, deploy, and worker enrollment](docs/operators/release.md) (current release: **2026.10.27**)
- **Review deployment profiles and distributed execution:** [Deploy targets](docs/deploy-targets.md) and [Deployment & distributed workflow](docs/usage/14-deployment-and-distributed-workflow.md)
- **Inspect system architecture and component boundaries:** [Dependency direction](docs/architecture/dependencies.md) and [Full documentation index](docs/README.md)

---

## Why the system is built this way

| Capability | What it does for your team |
|------------|----------------------------|
| **Two-stage QC with per-sample overlays** | Runs use an established study QC baseline baked at run start, with explicit exceptions where an individual sample requires custom guardrails. |
| **Four alignment paths under one contract** | Select linear GPU (`Clara Parabricks`), linear GPU/CPU (`GoliathAlign`), stock pangenome (`Giraffe`), or bisulfite pangenome (`methylGrapher`) based on assay and hardware requirements without changing downstream QC or study steps. |
| **Worker-pinned local scratch for BAMs** | Intermediate FASTQ copies and BAM files remain on the assigned worker's local disk from alignment through extraction and cleanup, eliminating network I/O bottlenecks and automatic cleanup leaks. |
| **Database-compiled workflow definitions** | Workflow graphs and resolved parameters are stored in PostgreSQL at instance start, providing a complete, inspectable execution record for every sample and study run. |

---

## Architecture and repository scope

GoliathOmics specializes [GoliathApp](https://github.com/Goliath-Research/GoliathApp) for genomics and calls [GoliathAlign](https://github.com/Goliath-Research/GoliathAlign) and [GoliathExtractor](https://github.com/Goliath-Research/GoliathExtractor) as standalone utilities. GoliathApp contains no GoliathOmics types, and GoliathAlign and GoliathExtractor contain no reference to GoliathApp or GoliathOmics.

- **Component boundaries:** [docs/architecture/dependencies.md](docs/architecture/dependencies.md)
- **Platform environment aliases during decoupling:** [docs/goliathapp-extension.md](docs/goliathapp-extension.md)
- **Platform alignment and roadmap (R0–R5):** [docs/refresh-plan.md](docs/refresh-plan.md)

### What lives here

- Science packages (`packages/`), including methylation, RNA-seq, and proteomics
- DomainPrograms, profiles, analytes, and cohort checks (`workflow_engine/domain/`)
- Action handlers in `workers/methyl_worker/` and the methylgrapher image wiring
- Database content GoliathApp does not own: `cfg.analyte` (`cfg_analyte_catalog.sql`), action-catalog and data-type seeds, reference-asset seeds, and study pipelines
- Product docs under `docs/usage`, `docs/theory`, `docs/operators`, `docs/regulatory`, and `docs/research`

### What stays upstream

- Python gateway. The wheel's console script is `methyl-gateway`, built from GoliathApp. This repository does not contain `workflow_engine/rest/`. `goliath-gateway` is the platform name for that service.
- Engine DDL for Meta, RBAC, the portal shell, `wf`, and `cfg`
- Shared claim/submit worker protocol and the optional `/work` layout
- Portal UI and portal shell, which live in GoliathApp.

Inherited platform names use the `goliath` prefix (`goliath-gateway`, `goliath-cfg`, `GOLIATH_WORK_ROOT`, database `goliath`). `methyl-*` remains the genomics CLI alias for one release cycle.

### Tools

GoliathOmics calls GoliathAlign and GoliathExtractor through a pinned image and binary. A change in either tool can break this product. The required features are listed in [docs/contracts/omics-tool-requirements.md](docs/contracts/omics-tool-requirements.md).

`GOLIATH_ALIGN_ROOT` points at the GoliathAlign checkout (sibling `../GoliathAlign`). `MOJO_ALIGN_ROOT` and sibling `../mojo-align` remain one-cycle aliases.

### Docs site

The MkDocs Material site renders `docs/` and is published from `main` at <https://goliath-research.github.io/GoliathOmics/>. Pull request CI builds it and does not deploy.

```bash
python -m pip install -r requirements-docs.txt
mkdocs build
mkdocs serve
```

### Machine-readable summary

[`/.goliath/product.json`](.goliath/product.json) is the summary GoliathWeb reads. The schema is [`.goliath/product.schema.json`](.goliath/product.schema.json). `dependsOn` lists `goliathapp`, `goliathalign`, and `goliathextractor` (product id for the `goliath-extractor` distribution).

