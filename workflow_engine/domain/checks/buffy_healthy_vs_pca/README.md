# Buffy healthy vs PCa — DomainProgram architecture check

Validates **DomainProgram → compiler → collection bindings → instance context** before running workers on the cluster.

## What lives where

| Location | Artifacts |
|----------|-----------|
| **Repo** (this bundle) | `configs/*.program.json`, `instance/*.json` examples, smoke `project_*.json`, `data/*.csv` for CI |
| **Repo** (`workflow_engine/domain/profiles/`) | Named pipeline profiles (`mc_dmp_gene_fc`, `mc_gene_fc`, …) |
| **`/work/projects/prostate-cancer/`** | `configs/project_Buffy_healthy_vs_PCa.json`, `data/healthy_b.csv`, `pca_b.csv`, run outputs |

Edit the **study manifest** on `/work` only. Programs and profiles stay in the repository.

| Repo path | Role |
|-----------|------|
| `configs/buffy_data_driven.program.json` | Single-run discovery (centroid → detector → mapper → enricher) |
| `configs/mc_stability.program.json` | 10-iteration MC stability (FeatureCuts + gene select) |
| `../profiles/mc_dmp_gene_fc.profile.json` | Profile for Buffy MC (`runDmpSelection`, gene FeatureCuts, stability axes) |

| `/work` path | Role |
|--------------|------|
| `configs/project_Buffy_healthy_vs_PCa.json` | Cohorts, comparisons, paths (no tool parameters) |
| `data/healthy_b.csv`, `data/pca_b.csv` | Sample lists |

The `configs/project_*.json` and `data/` folders in this check bundle are **CI smoke mirrors** for `check_pipeline.py` when `/work` is unavailable.

## Quick validation (repo smoke mirror)

```bash
source .venv/bin/activate
python workflow_engine/domain/checks/buffy_healthy_vs_pca/check_pipeline.py
```

Or validate against the live `/work` project (program still from repo):

```bash
python workflow_engine/domain/checks/buffy_healthy_vs_pca/check_pipeline.py \
  --project /work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json \
  --program workflow_engine/domain/fixtures/data_driven.program.json
```

## Run analysis (`methyl-workflow-run`)

From repo root with `.venv` activated. Paths below are relative to repo root.

**Single-run discovery** (no MC):

```bash
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/data_driven.program.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json"}' \
  --parallel-workers 1
```

**10 MC iterations, balanced-accuracy tuning** (DMP + gene FeatureCuts):

```bash
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/mc_stability.program.json \
  --context-file workflow_engine/domain/profiles/mc_dmp_gene_fc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json","pipelineProfile":"mc_dmp_gene_fc"}' \
  --parallel-workers 1
```

Outputs: `/work/projects/prostate-cancer/Buffy_healthy_vs_PCa/monte_carlo_runs/` → `stability/stability_summary.json`.

## Bootstrap sample lists on `/work` (optional)

Copy sample CSVs (and optionally a project template) to shared storage — **never** DomainPrograms:

```bash
bash workflow_engine/domain/checks/buffy_healthy_vs_pca/install_to_work.sh --force
```

Edit `project_Buffy_healthy_vs_PCa.json` on `/work` after bootstrap.

## Related

- [`../h_pca_good/README.md`](../h_pca_good/README.md) — `project_H_PCa_good.json` MC run
- [`docs/reference/domain-program-language.md`](../../../docs/reference/domain-program-language.md) — profiles and programs
- [`workflow_engine/docs/pipeline_architecture.md`](../../docs/pipeline_architecture.md)
