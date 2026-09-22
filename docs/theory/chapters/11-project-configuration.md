# The Project Configuration File {#sec-project-configuration}
## Role and design principle

MethylPipeline separates **study facts** (cohorts, paths, comparisons) from **tool parameters** (detector thresholds, MC iteration counts, mapper windows). The study manifest — usually `project_<name>.json` on `/work/projects/<study>/configs/` — holds only study-level fields validated by `ProjectConfig` in `packages/methylutils/methyl_utils/pipeline_config.py`.

**Study manifests must not contain `step_config`.** Tool parameters live in profile and site `actionConfig`, resolved at workflow plan time and worker claim time into `resolvedConfig`.

## Four-layer configuration

| Layer | Artifact | Owns |
|-------|----------|------|
| Study manifest | `project_*.json` | Cohorts, comparisons, paths, `regulatory`, `validation_partitions` |
| Pipeline profile | `*.profile.json` | `actionConfig` parameter packs + scope booleans |
| Site manifest | `/work/site/methyl_site.json` | Genomes, GTF, caches, deployment-wide defaults |
| DomainProgram | `*.program.json` | Control flow; optional per-action `with` / `stepOverride` |

**Precedence** (highest wins): program/instance override → profile `actionConfig` → analyte defaults → site `actionConfig` → *(no Python fallback for tunable science knobs)*.

See [§ two workflows](12-two-workflows.md#sec-two-workflows) for how Monte Carlo and production workflows consume these layers.

## Top-level study manifest fields

| Field | Type | Description |
|---|---|---|
| `project_name` | string | Output directory name under `output_base`. |
| `laboratory` | string | Metadata; appears in centroid H5 attributes. |
| `batch` | string | Metadata; appears in centroid H5 attributes. |
| `output_base` | string (path) | Root directory for all pipeline outputs. |
| `samples_base_path` | string (path) | Base directory for resolving relative sample paths in cohort CSVs. |
| `chromosomes` | list of strings | Chromosomes to process (e.g. `["1", …, "22", "X", "Y"]`). |
| `contexts` | list of strings | Methylation contexts: `"CG"`, `"CHG"`, `"CHH"`. |
| `comparisons` | string or list | Comparison layout: `"control_vs_each_disease"` or explicit pairs. |
| `regulatory` | object | Optional regulatory metadata; `primary_analyte` seeds analyte profile merge. |

## The cohort hierarchy

The `controls` and `diseases` blocks define the study cohort structure. Each block has a `label` and a `groups` list. For hierarchical multiclass designs, disease groups can have `stages`:

```json
{
  "controls": {
    "label": "healthy",
    "groups": [
      { "label": "all", "sample_paths": ["data/healthy.csv"] }
    ]
  },
  "diseases": {
    "label": "cancer",
    "groups": [
      {
        "label": "pca",
        "stages": [
          { "label": "pca1", "sample_paths": ["data/pca1.csv"] },
          { "label": "pca2", "sample_paths": ["data/pca2.csv"] }
        ]
      }
    ]
  }
}
```

`methyl-centroid` builds one centroid per leaf group; `methyl-detector` compares control leaves versus each disease stage. Resolved group labels for Monte Carlo and predictors concatenate parent and leaf names (`pca_pca1`, …).

## Where tool parameters live: `actionConfig`

Parameters previously embedded in `step_config` now live under **`actionConfig.<action>`** in pipeline profiles and optionally the site manifest:

| `actionConfig` key | Handled by | Description |
|---|---|---|
| `centroid` | `methyl-centroid` | Coverage thresholds, histogram bins, GPU, parallelism. |
| `detection` | `methyl-detector` | Statistical test, effect size, DMP export mode. |
| `dmp_selection` | `methyl-dmp-select` | FeatureCuts BA gate, panel size bounds. |
| `mapper` | `methyl-mapper` | GTF, gene window sizes, enrichment source. |
| `enricher` | `methyl-enricher` | Library selection, disease filters, module scoring. |
| `classifier` | `methyl-classifier` | Chromosome weighting, OvR layout, panel definition. |
| `gene_selection` | `methyl-gene-select` | Gene FeatureCuts caps, biomarker filter. |
| `predictor` | `methyl-predictor` | Panel definition, debug mode. |
| `validation` | `methyl-validation` | Monte Carlo settings, stability gates, backend profiles. |
| `alignment_qc` | `methyl-alignment-qc` | Schema validation, guardrails. |

Exhaustive parameter tables: [configuration-reference](../../reference/configuration-reference.md) (keys listed as `actionConfig.*` sections).

Resolution: `resolve_action_config` / `resolve_for_project` in `packages/methylutils/methyl_utils/action_config_resolver.py`.

## Monte Carlo configuration

Monte Carlo settings belong in **profile** `actionConfig.validation` (and optional site defaults), not in the study manifest. When `methyl-validation` or the workflow planner runs, merged validation config becomes `MonteCarloConfig` and is snapshotted to `monte_carlo_runs/queue/mc_config.json` for workers.

Example profile fragment:

```json
"actionConfig": {
  "validation": {
    "train_fraction": 0.8,
    "n_iterations": 10,
    "seed": 42,
    "run_stability": true,
    "stability_dmp_freq": 0.8,
    "dmp_modeling_mode": "featurecuts",
    "gene_modeling_mode": "featurecuts",
    "dmp_featurecuts_target_ba": 0.9,
    "gene_featurecuts_target_ba": 0.88
  }
}
```

Inherited from the study manifest (not duplicated in the profile): `samples_base_path`, `output_base`, cohort structure, `chromosomes`, `contexts`.

## Annotated study manifest example

Slim project file on `/work` (tool parameters in `mc_gene_fc.profile.json`, not here):

```json
{
  "project_name": "Buffy_healthy_vs_PCa",
  "laboratory": "psomagen",
  "batch": "prostate buffy coat",
  "output_base": "/work/projects/prostate-cancer",
  "samples_base_path": "/work/samples",
  "chromosomes": ["1", "2", "21", "22"],
  "contexts": ["CG"],
  "comparisons": "control_vs_each_disease",
  "regulatory": {
    "primary_analyte": "buffy_coat",
    "stage": "feasibility"
  },
  "controls": {
    "label": "healthy",
    "groups": [{ "label": "all", "sample_paths": ["data/healthy_b.csv"] }]
  },
  "diseases": {
    "label": "cancer",
    "groups": [{ "label": "pca", "sample_paths": ["data/pca_b.csv"] }]
  }
}
```

Run with a profile:

```bash
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/mc_stability.program.json \
  --context-file workflow_engine/domain/profiles/mc_gene_fc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json"}'
```

## Output directory layout

Given `output_base = "/work/projects/prostate-cancer"` and `project_name = "Buffy_healthy_vs_PCa"`:

```
/work/projects/prostate-cancer/
└── Buffy_healthy_vs_PCa/
    ├── centroids/
    ├── detections/
    │   └── all/<comparison>/dmps-<chrom>-discovery.csv
    │                        dmps-<chrom>-selected.csv
    ├── classifiers/
    ├── mapper/
    ├── enricher/
    └── monte_carlo_runs/
        ├── queue/mc_config.json
        ├── run_0001/
        ├── stability/stable_dmps_production.csv
        └── production/
```

Downstream FeatureCuts and mapper steps prefer **`dmps-*-selected.csv`** (BA-gated panel). Discovery CSVs remain for exploration and raw-pool modeling modes.

## Related

- Usage: [Project config and layout](../../usage/02-project-config-and-layout.md)
- Reference: [Config parameter matrix](../../reference/config-parameter-matrix.md)
- Architecture: [Layer model](../../architecture/layer-model.md)
