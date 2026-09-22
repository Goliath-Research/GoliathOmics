# Orchestration: `methyl-workflow-run` (canonical)

## Purpose

Choose the right **entry point** for staged validation. New studies and production runs should use **`methyl-workflow-run`** with a DomainProgram and pipeline profile. The monolithic **`methyl-validation --stability/--freeze/--model`** path is **legacy** — kept for transitional scripts and one-off recovery, not for new workflow design.

## Preferred vs legacy

| | **Canonical (use this)** | **Legacy (transitional only)** |
|--|--------------------------|--------------------------------|
| **Entry** | `methyl-workflow-run` | `methyl-validation --stability`, `--freeze`, `--model`, … |
| **Topology** | DomainProgram JSON (`workflow_engine/domain/**/*.program.json`) | Hard-coded stage sequence inside the CLI |
| **Parameters** | Profile `actionConfig` + site manifest + optional instance overrides | Same keys, but resolved only through project path and CLI flags |
| **Skip / resume** | Per-action `.action_results/` manifests in the compiled graph | `--resume`, `--skip-detection`, coarse stage flags |
| **Production cluster** | `methyl-gateway` + `methyl-worker` (same programs as local) | `plan-runs` / `run-task` file queue (legacy distributed MC) |
| **When to use** | All new studies, CI smoke, portal/gateway instances | Existing shell scripts, queue workers not yet migrated, narrow recovery |

See also [Architecture: orchestration paths](../architecture/orchestration-paths.md).

## What you pass

Every canonical run needs three inputs:

1. **`--program`** — workflow topology (which actions run, in what order, FOREACH loops, gates).
2. **`--context-file`** — pipeline profile (`*.profile.json`) with merged `actionConfig`.
3. **`--context`** — JSON with at least `projectPath` (study manifest on `/work`) and usually `pipelineProfile`.

```bash
source .venv/bin/activate
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/mc_stability.program.json \
  --context-file workflow_engine/domain/profiles/mc_dmp_gene_fc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json","pipelineProfile":"mc_dmp_gene_fc"}' \
  --parallel-workers 1
```

In production, point `--program` and `--context-file` at the promoted runtime bundle, e.g. `/work/goliath/current/runtime-bundle/domain/...` (see [Deployment and distributed workflow](14-deployment-and-distributed-workflow.md)).

## Programs by scope

Pick a DomainProgram that matches how much of the lifecycle you need. Stage chapters (ch.05–09) describe artifacts and gates; this table maps them to programs.

| Scope | Example program | Covers |
|-------|-----------------|--------|
| MC stability only | `mc_stability.program.json`, `mc_stability_staged.program.json` | Centroid + detector loops, stability, freeze-readiness gate |
| Freeze only (after stability) | `pca1_5_freeze.program.json` | `prepare_freeze_project`, mapper, enricher, progression |
| Full study lifecycle | `study_validation_lifecycle.program.json` | Stability → freeze → model MC → selection → hold-out |

Binary healthy-vs-cancer walkthrough: [Tutorial: healthy vs. cancer stages](16-tutorial-healthy-vs-cancer-stages.md).

## Local development without a database

```bash
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/mc_stability_smoke.program.json \
  --context-file workflow_engine/domain/profiles/mc_dmp.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-5-CG.json"}' \
  --stub-external
```

Validate compile/bindings before a long run: `python workflow_engine/domain/checks/<study>/check_pipeline.py --program ...`.

## Command presets

Copy-ready program/profile/context combinations:

```bash
bash scripts/workflow_presets.sh list
bash scripts/workflow_presets.sh show lifecycle
```

Set `METHYL_RUNTIME_ROOT=/work/goliath/current/runtime-bundle` on production workers to resolve bundle paths.

## Legacy `methyl-validation` (when it still appears)

Use **`methyl-validation`** only when:

- You must run an existing shell script or notebook that has not been migrated.
- You need a **narrow recovery** command (e.g. `--stability --skip-detection` after MC iterations exist).
- You operate the **file-queue** distributed path (`plan-runs` → `run-task` → `aggregate-results`) — see [Distributed MethylValidation](13-distributed-methyl-validation.md).

Do **not** start new studies with `methyl-validation --stability` as the primary entry. Migrate to a DomainProgram + profile (same `actionConfig` keys; different invocation).

```bash
# Legacy example — prefer methyl-workflow-run for new work
methyl-validation --project /work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json --stability
```

Package-level detail and CLI flag reference: `packages/methylvalidation/docs/USAGE.md` (documents legacy flags for parity with workflow actions).

## Related

- [Project config and layout](02-project-config-and-layout.md) — four-layer config model
- [DomainProgram language](../reference/domain-program-language.md) — program vs profile vs manifest
- [Command cookbook](12-command-cookbook.md) — copy-ready sequences (canonical first)
- [Architecture: orchestration paths](../architecture/orchestration-paths.md)
