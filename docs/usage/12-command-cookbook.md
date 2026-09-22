# Command Cookbook

## Purpose

Provide copy-ready command sequences for the full solution lifecycle. **Prefer `methyl-workflow-run`** (see [Orchestration](04-orchestration-workflow-run.md)). Legacy `methyl-validation` sequences are at the end for transitional scripts.

## 1) Full staged run (canonical — DomainProgram)

```bash
source .venv/bin/activate
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/study_validation_lifecycle.program.json \
  --context-file workflow_engine/domain/profiles/mc_dmp_gene_fc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-5-CG.json","pipelineProfile":"mc_dmp_gene_fc"}' \
  --parallel-workers 1
```

MC stability only (freeze-readiness gate included):

```bash
source .venv/bin/activate
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/mc_stability.program.json \
  --context-file workflow_engine/domain/profiles/mc_dmp_gene_fc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json","pipelineProfile":"mc_dmp_gene_fc"}'
```

## 2) Full staged run (legacy CLI — transitional only)

```bash
source .venv/bin/activate
PROJECT="/work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json"

methyl-validation --project "$PROJECT" --stability
methyl-validation --project "$PROJECT" --freeze
methyl-validation --project "$PROJECT" --model
methyl-validation --project "$PROJECT" --post-model-validation
```

## 3) Backend comparison and selection (legacy CLI)

```bash
source .venv/bin/activate
PROJECT="/work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json"

methyl-validation --project "$PROJECT" --model-mc --model-mc-all
methyl-validation --project "$PROJECT" --select-best-model --model-mc-all \
  --selection-metric balanced_accuracy --selection-stat median
```

## 4) Rollout comparison report (legacy CLI)

```bash
source .venv/bin/activate
PROJECT="/work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-4-CG.json"
BASE="/work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/monte_carlo_runs/model_mc/ecdf/metrics_summary.json"
CAND="/work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/monte_carlo_runs/model_mc/tabular_sklearn/metrics_summary.json"

methyl-validation --project "$PROJECT" --rollout-compare \
  --baseline-summary "$BASE" --candidate-summary "$CAND"
```

## 5) Standalone enricher run from mapper output

```bash
source .venv/bin/activate
methyl-enricher \
  --input /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/mapper/all/pca_pca1/all-gene_name-combined.csv \
  --modules \
  --library-preset cancer-extended \
  --outdir /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/enricher/all/pca_pca1
```

## 6) Standalone blind prediction

```bash
source .venv/bin/activate
methyl-predictor --project /work/projects/prostate-cancer/Healthy_vs_PCa1-4-CG/monte_carlo_runs/production/project.json
```

## 7) Manual rendering commands (this manual)

```bash
mkdocs build --strict
mkdocs build -f mkdocs.pdf.yml
```

## 8) Distributed queue (legacy — one task)

Full workflow: [Distributed MethylValidation on shared storage](13-distributed-methyl-validation.md) (Chapter 13). Prefer gateway workers for new deployments. One file-queue worker invocation:

```bash
source .venv/bin/activate
methyl-validation run-task --task /work/.../monte_carlo_runs/queue/tasks/run_0001.json
```

## 9) Hyperparameter search grid (example)

After stability is understood (via workflow-run or legacy CLI), use the grid driver. Details: [Optional hyperparameter search](15-optional-hyperparameter-search.md) (Chapter 15).

```bash
source .venv/bin/activate
methyl-hyperparam-search \
  --config /work/experiments/my_mc_config.json \
  --work-dir /work/experiments/hp_run1 \
  --grid '{"stability_dmp_freq": [0.6, 0.7]}' \
  -- --stability
```

## Execution notes

- Always activate `.venv` before `methyl-*` commands.
- Use absolute project paths in production scripts.
- Keep command logs with timestamp and git commit hash for audit.
- New studies: start from [Orchestration (ch.04)](04-orchestration-workflow-run.md), not legacy stage flags.
