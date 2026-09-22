# Relationship to Theory and Implementation

This usage manual owns **operator commands**, stage handoffs, artifact gates, and deployment checklists. It does not derive statistical methods or document compiler internals.

| Need | Read |
|------|------|
| DMP detection math, assumptions, limitations | [Theory book](../theory/index.md) — start with ch.03 |
| DomainProgram language and schemas | [Reference: domain-program-language](../reference/domain-program-language.md) |
| Parameter keys and defaults | [Reference: configuration-reference](../reference/configuration-reference.md) |
| Workflow engine, workers, compiler | [Implementation guide](../implementation/index.md) |
| System layers and orchestration paths | [Architecture](../architecture/index.md) |

**Canonical orchestration:** [`methyl-workflow-run`](04-orchestration-workflow-run.md) with repo DomainPrograms, pipeline profiles, and `/work` study manifests. This is the path for new studies, local development, and production workers.

**Legacy orchestration:** `methyl-validation --stability/--freeze/--model` and the file-queue subcommands (`plan-runs`, `run-task`) remain documented for transitional scripts and recovery. Do not start new studies on the monolithic CLI — migrate to a DomainProgram that encodes the same stages.

| Need | Read |
|------|------|
| Entry points, program selection | [Orchestration (ch.04)](04-orchestration-workflow-run.md) |
| Path matrix (local / gateway / legacy) | [Architecture: orchestration paths](../architecture/orchestration-paths.md) |

**Repo vs `/work`:** profiles and programs live in the git repo; `project_*.json` and run artifacts live under `/work/<disease>/`. See ch.02 and [work-config-paths rule](../../.cursor/rules/work-config-paths.mdc).
