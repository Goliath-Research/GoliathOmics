# H_PCa_good — MC stability (healthy vs PCa, good cohort)

Buffy-coat **healthy vs PCa** using `healthy_good.csv` / `pca_good.csv`. Same MC design as Buffy (10 iterations, FeatureCuts BA tuning) with **biomarker filter** enabled.

## What lives where

| Location | Artifacts |
|----------|-----------|
| **Repo** (this bundle) | `configs/h_pca_good_mc_stability.program.json`, smoke `project_H_PCa_good.json` |
| **Repo** (`workflow_engine/domain/profiles/`) | `full_biomarker_gene_fc.profile.json` |
| **`/work/projects/prostate-cancer/`** | `configs/project_H_PCa_good.json`, `data/healthy_good.csv`, `pca_good.csv`, run outputs |

| Repo path | Role |
|-----------|------|
| `configs/h_pca_good_mc_stability.program.json` | MC DomainProgram |
| `../profiles/full_biomarker_gene_fc.profile.json` | Profile (DMP + gene FeatureCuts + biomarker filter) |

| `/work` path | Role |
|--------------|------|
| `configs/project_H_PCa_good.json` | Study manifest (cohorts, comparisons, paths) |
| `data/healthy_good.csv`, `data/pca_good.csv` | Sample lists |

The `configs/project_H_PCa_good.json` in this folder is a **reference / CI mirror** only; edit production JSON on `/work`.

## Run

From repo root with `.venv` activated:

```bash
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/mc_stability.program.json \
  --context-file workflow_engine/domain/profiles/full_biomarker_gene_fc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_H_PCa_good.json","pipelineProfile":"full_biomarker_gene_fc"}' \
  --parallel-workers 1
```

Outputs: `/work/projects/prostate-cancer/H_PCa_good/monte_carlo_runs/`.

## Config notes (four-layer)

- **Study manifest** (`/work/.../project_H_PCa_good.json`): cohorts, comparisons, paths only
- **Profile** (`full_biomarker_gene_fc.profile.json`): MC knobs — use canonical `stability_*` keys under `actionConfig.validation` (not legacy `stability_dmps_*` names)
- DMP FeatureCuts: profile `actionConfig.dmp_selection`
- `actionConfig.enricher.sort_by`: `gene_importance`
- `actionConfig.validation.n_iterations`: 10 for the standard MC stability run
