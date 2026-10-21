# Relationship to Theory and Implementation

This usage manual owns **operator commands**, stage handoffs, artifact gates, and deployment checklists. It does not derive statistical methods.

| Need | Read |
|------|------|
| DMP detection math, assumptions, limitations | [Theory book](../theory/index.md) — start with ch.03 |
| How this product sits on GoliathApp and the two utilities | [Dependency direction](../architecture/dependencies.md) |
| Start SamplePrep, release, enroll, process packs | [Operators](../operators/start-sample-prep.md) |
| Parameter keys used by SamplePrep QC | [Sample prep and QC](03-sample-prep-and-qc.md) and [Analyte profiles](../ANALYTE_PROFILES.md) |
| Align path matrix | [Alignment engines](alignment-engines.md) |

`docs/implementation/`, `docs/reference/`, and `docs/deployment/` are not in this tree. Where an older chapter links there, use the operator guides and the code paths they name.

**SamplePrep instances** are started with [`methyl-study-start sample-prep-start`](../operators/start-sample-prep.md). That command calls `finalize_instance_context` and writes the instance to the workflow database.

**Staged validation on a workstation** uses `methyl-workflow-run` from the methyl-gateway wheel (GoliathApp), with a DomainProgram, a pipeline profile, and a `/work` study manifest. This repository does not declare that console script. See [Orchestration](04-orchestration-workflow-run.md).

**Legacy orchestration:** `methyl-validation --stability/--freeze/--model` and the file-queue subcommands (`plan-runs`, `run-task`) remain for transitional scripts and recovery.

**Repo vs `/work`:** profiles and programs live in the git repo. `project_*.json` and run artifacts live under `/work`. See [Project config and layout](02-project-config-and-layout.md).
