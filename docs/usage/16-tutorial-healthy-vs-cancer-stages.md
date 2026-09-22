# Tutorial: Healthy vs. Cancer (single group or increasing stages)

## What you will build

A comparison study that contrasts **one healthy control group** against either:

- **one cancer group** (binary: healthy vs. cancer), or
- **several cancer groups in increasing stages** (healthy vs. stage 1 vs. stage 2 …).

The pipeline treats both the same way: one control pool is compared against **each** disease group. Multi-stage is just "more disease groups," so this tutorial covers both at once.

This is a short, end-to-end walkthrough. For field-by-field detail see [Project config and layout](02-project-config-and-layout.md); for the stability stage see [Stage: Stability](05-stage-stability.md).

## Mental model (four layers)

MethylPipeline is disease-agnostic. You describe **your study** (who is healthy, who is cancer, where the samples are) in a **study manifest**; the **science parameters** (iterations, thresholds, caps) come from a **pipeline profile** shipped in the repo. You never hard-code study facts in a profile, and you never put tool parameters in a manifest.

| Layer | Artifact | You edit it? |
|-------|----------|--------------|
| Study manifest | `/work/projects/<disease>/configs/project_*.json` | **Yes** — this is your study |
| Cohort lists | `/work/projects/<disease>/data/*.csv` | **Yes** — one CSV per group |
| Pipeline profile | `workflow_engine/domain/profiles/*.profile.json` | No — pick one |
| DomainProgram | `workflow_engine/domain/checks/**/*.program.json` | No — pick one |

## Step 1 — Lay out cohort CSVs

Put one sample list per group under `/work/projects/<disease>/data/`. Each CSV has a `sample` header and one sample folder name per row. Names resolve against `samples_base_path` (typically `/work/samples/<name>`).

`/work/projects/prostate-cancer/data/PCaH.csv` (healthy):

```csv
sample
HBCST-052125-87293
HBCST-061825-52197
HBCST-051425-74294
```

Create one more for the binary case (`pca_b.csv`), or one per stage for the multi-stage case (`PCa1.csv`, `PCa2.csv`, …). A row may also be an absolute path or a direct sample directory; bare names use `samples_base_path`.

## Step 2 — Write the study manifest

### Option A — Binary (healthy vs. one cancer group)

`/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json`:

```json
{
  "project_name": "Buffy_healthy_vs_PCa",
  "output_base": "/work/projects/prostate-cancer",
  "samples_base_path": "/work/samples",
  "controls": {
    "label": "healthy",
    "groups": [
      { "label": "all", "sample_paths": ["/work/projects/prostate-cancer/data/healthy_b.csv"] }
    ]
  },
  "diseases": {
    "label": "cancer",
    "groups": [
      { "label": "PCa", "sample_paths": ["/work/projects/prostate-cancer/data/pca_b.csv"] }
    ]
  },
  "comparisons": "control_vs_each_disease",
  "chromosomes": ["1","2","3","4","5","6","7","8","9","10","11","12","13","14","15","16","17","18","19","20","21","22","X","Y"],
  "contexts": ["CG"]
}
```

### Option B — Increasing stages (healthy vs. stage 1 … stage N)

To add stages, nest a `stages[]` array under a single disease parent. Each stage is its own group with its own CSV. **The parent must not carry `sample_paths` when it uses `stages`.** Stages expand to resolved labels of the form `{parent}_{stage}` — here `PCa_PCa1 … PCa_PCa5`.

```json
{
  "project_name": "Healthy_vs_PCa1-5-CG",
  "output_base": "/work/projects/prostate-cancer",
  "samples_base_path": "/work/samples",
  "controls": {
    "label": "healthy",
    "groups": [
      { "label": "all", "sample_paths": ["/work/projects/prostate-cancer/data/PCaH.csv"] }
    ]
  },
  "diseases": {
    "label": "cancer",
    "groups": [
      {
        "label": "PCa",
        "stages": [
          { "label": "PCa1", "description": "Gleason Score 3+3",     "sample_paths": ["/work/projects/prostate-cancer/data/PCa1.csv"] },
          { "label": "PCa2", "description": "Gleason Score 3+4",     "sample_paths": ["/work/projects/prostate-cancer/data/PCa2.csv"] },
          { "label": "PCa3", "description": "Gleason Score 4+3",     "sample_paths": ["/work/projects/prostate-cancer/data/PCa3.csv"] },
          { "label": "PCa4", "description": "Gleason Score 4+4",     "sample_paths": ["/work/projects/prostate-cancer/data/PCa4.csv"] },
          { "label": "PCa5", "description": "Gleason Score 4+5/5+4", "sample_paths": ["/work/projects/prostate-cancer/data/PCa5.csv"] }
        ]
      }
    ]
  },
  "comparisons": "control_vs_each_disease",
  "progression_order": "explicit",
  "progression_labels": ["PCa_PCa1", "PCa_PCa2", "PCa_PCa3", "PCa_PCa4", "PCa_PCa5"],
  "chromosomes": ["1","2","3","4","5","6","7","8","9","10","11","12","13","14","15","16","17","18","19","20","21","22","X","Y"],
  "contexts": ["CG"]
}
```

A complete, annotated version of this staged manifest lives at `workflow_engine/domain/checks/pca1_5_cg/configs/project_Healthy_vs_PCa1-5-CG.json`.

## Step 3 — Understand what `comparisons` does

`comparisons` decides which group pairs get analyzed. For a healthy-vs-stages study you almost always want `"control_vs_each_disease"`.

| Value | Meaning | Use for |
|-------|---------|---------|
| `"control_vs_each_disease"` | First control group vs. **each** disease group/stage | Healthy vs. stage 1, stage 2, stage 3 … (this tutorial) |
| `"all_pairs"` | Every control group × every disease group | Multiple control strata |
| `[{ "control_group": "all", "disease_group": "PCa_PCa1" }, …]` | Hand-picked pairs (using **resolved** labels) | Custom subsets |

Important: `"control_vs_each_disease"` compares **healthy against each stage** — it does **not** run stage-vs-stage contrasts. If you omit `comparisons` entirely, the pipeline defaults to `control_vs_each_disease` for a single control group. To model the ordered stage narrative (early → advanced) after per-comparison detection, set `progression_order` / `progression_labels` as in Option B.

## Step 4 — Pick a profile

The profile supplies the science knobs (iterations, thresholds, feature caps). Profiles are **process-agnostic** — study facts stay in your manifest; the profile selects which statistical branch and scope flags run.

All shipped profiles live in `workflow_engine/domain/profiles/*.profile.json` (production: `/work/goliath/current/runtime-bundle/domain/profiles/`). Pass one with `--context-file` or set `pipelineProfile` in `--context`.

### Quick picks (this tutorial)

| Goal | `pipelineProfile` / mode |
|------|--------------------------|
| Standard binary Monte-Carlo stability (DMP + gene FeatureCuts) | `samd_research` (default `researchMode: dual_fc`) or legacy `mc_dmp_gene_fc` |
| Multi-stage, one-vs-rest Monte-Carlo stability | `staged_ovr_mc` |
| Multi-stage end-to-end (stability + freeze + model) | `staged_full_lifecycle` |
| Progression / interpretation only (no MC) | `staged_progression_interpretation` |

### Statistical modeling modes (`samd_research` + `researchMode`)

Five reusable **research axes** for Monte-Carlo stability. Prefer `pipelineProfile: samd_research` with `researchMode`; legacy `mc_*` names are deprecated aliases that fold into the same overlays. See also [Stage: Stability](05-stage-stability.md) and [SaMD lifecycle (ch.18)](18-samd-study-lifecycle.md).

| Mode | `researchMode` | Legacy profile | `dmp_modeling_mode` | `gene_modeling_mode` | Use when |
|------|----------------|----------------|---------------------|----------------------|----------|
| 1 | `dmp_raw` | `mc_dmp` | `raw_pool` | `none` | Exploratory DMP recurrence (no FeatureCuts) |
| 2 | `dmp_fc` | `mc_dmp_fc` | `featurecuts` | `none` | BA-gated DMP panel stability |
| 3 | `gene_enricher` | `mc_gene` | `raw_pool` | `none` | Enricher gene recurrence (PPI hubs or Enrichr) |
| 4 | `gene_fc` | `mc_gene_fc` | `raw_pool` | `featurecuts` | Gene-axis BA gate on enricher/PPI genes |
| 5 | `dual_fc` | `mc_dmp_gene_fc` | `featurecuts` | `featurecuts` | Full DMP + gene FeatureCuts |

: Statistical modeling modes {tbl-colwidths="[6,12,14,14,14,40]"}

`gene_enricher` / `gene_fc` (and legacy `mc_gene` / `mc_gene_fc`) default to `actionConfig.enricher.ppi_only: true` (STRING PPI hubs). Set `ppi_only: false` in a site/profile/instance override for Enrichr pathway libraries.

### Full profile catalog

Every profile file in the repo/runtime bundle:

| Profile | Detector / selection | Mapper CSV | Stability / lifecycle | Typical use |
|---------|---------------------|------------|----------------------|-------------|
| `mc_dmp` | discovery only | `dmps-*-discovery.csv` | DMP recurrence (raw pool) | **Deprecated** → `samd_research` + `dmp_raw` |
| `mc_dmp_fc` | discovery + `dmp_select` | `dmps-*-selected.csv` | DMP panel MC + FeatureCuts | **Deprecated** → `samd_research` + `dmp_fc` |
| `mc_gene` | discovery only | discovery | enricher gene recurrence | **Deprecated** → `samd_research` + `gene_enricher` |
| `mc_gene_fc` | discovery only | discovery | gene FeatureCuts on enricher genes | **Deprecated** → `samd_research` + `gene_fc` |
| `mc_dmp_gene_fc` | discovery + `dmp_select` | discovery / selected | DMP + gene FeatureCuts MC | **Deprecated** → `samd_research` + `dual_fc` |
| `samd_research` | discovery + `dmp_select` | discovery / selected | SaMD research (+ `researchMode`) | Preferred binary research path |
| `phase_a_dmp_stability` | discovery + `dmp_select` | selected | DMP panel lock (phase A) | Two-phase: lock DMP panel first |
| `phase_b_gene_from_stable_dmps` | stable-panel detector | discovery | gene FC from frozen DMPs (phase B) | Phase B after `stable_dmps_production.csv` |
| `staged_ovr_mc` | staged OvR + FeatureCuts | discovery | MC stability + progression | Multi-stage healthy vs. stages |
| `staged_full_lifecycle` | staged OvR + FeatureCuts | discovery | MC + freeze + model | Multi-stage end-to-end |
| `staged_progression_interpretation` | discovery only | discovery | mapper/enricher + progression (no MC) | Interpretation / progression only |
| `discovery_interpretation` | discovery only | discovery | single-run mapper/enricher | One-shot interpretation |
| `structural_features` | discovery only | discovery + intersections | gene×region ranked catalog | Structural / region features |
| `full_biomarker_gene_fc` | discovery + selects | selected | DMP + gene FC + biomarker filter | Biomarker-filtered gene FC |
| `discovery_gene_featurecuts` | discovery + gene FC | discovery / selected | dual-axis FeatureCuts (legacy name) | Alias → `mc_dmp_gene_fc` |
| `legacy_dual` | legacy inline FeatureCuts | discovery | DMP classifier panels | Historical dual-axis layout |
| `dmp_panel_stability` | discovery + `dmp_select` | selected | DMP panel MC | **Deprecated** → `mc_dmp_fc` |
| `gene_enricher_stability` | discovery only | discovery | enricher recurrence | **Deprecated** → `mc_dmp` |

: Shipped pipeline profiles {tbl-colwidths="[14,20,14,22,30]"}

**Deprecated aliases** (fold into `samd_research` + mode via `pipeline_profiles.py`): `mc_dmp` → `dmp_raw`, `mc_dmp_fc` → `dmp_fc`, `mc_gene` → `gene_enricher`, `mc_gene_fc` → `gene_fc`, `mc_dmp_gene_fc` / `buffy_mc_gene_fc` / `discovery_gene_featurecuts` → `dual_fc`, plus older names `mc_dmp_discovery` → `mc_dmp`, `mc_dmp_featurecuts` → `mc_dmp_fc`, `mc_gene_mapper` → `mc_gene`, `mc_gene_featurecuts` → `mc_gene_fc`, `dmp_panel_stability` → `mc_dmp_fc`, `gene_enricher_stability` → `mc_dmp`.

**Two-phase workflow:** run Phase A with `phase_a_dmp_stability`; pass `stable_dmps_production.csv` via context `stableDmpCsv` (or `freeze_stable_dmp_csv` in validation overrides) for Phase B `phase_b_gene_from_stable_dmps`.

Parameter detail: [DomainProgram language — Pipeline profiles](../reference/domain-program-language.md#pipeline-profiles-and-site-manifest).

## Step 5 — Run it

Always activate the repo virtualenv first. You pass three things: a **program** (the workflow topology), a **profile** (via `--context-file`), and a **context** JSON that points at your manifest and names the profile.

Binary (healthy vs. one cancer group):

```bash
source .venv/bin/activate
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/mc_stability.program.json \
  --context-file workflow_engine/domain/profiles/mc_dmp_gene_fc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Buffy_healthy_vs_PCa.json","pipelineProfile":"mc_dmp_gene_fc"}' \
  --parallel-workers 1
```

Multi-stage (healthy vs. PCa1…PCa5):

```bash
source .venv/bin/activate
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/mc_stability_staged.program.json \
  --context-file workflow_engine/domain/profiles/staged_ovr_mc.profile.json \
  --context '{"projectPath":"/work/projects/prostate-cancer/configs/project_Healthy_vs_PCa1-5-CG.json","pipelineProfile":"staged_ovr_mc"}' \
  --parallel-workers 1
```

Before a full run, you can validate the manifest and compile the program with the check harness:

```bash
source .venv/bin/activate
python workflow_engine/domain/checks/pca1_5_cg/check_pipeline.py \
  --program workflow_engine/domain/fixtures/mc_stability_smoke.program.json
```

In production, point `--program` and `--context-file` at the promoted runtime bundle instead of the repo, e.g. `/work/goliath/current/runtime-bundle/domain/...` (see [Deployment and distributed workflow](14-deployment-and-distributed-workflow.md)).

## Step 6 — Find the outputs

Results land under `output_base/project_name/`. Each comparison gets its own detection folder keyed by control and disease group:

```
/work/projects/prostate-cancer/Healthy_vs_PCa1-5-CG/
├── detections/all/PCa_PCa1/     # healthy vs. stage 1
├── detections/all/PCa_PCa2/     # healthy vs. stage 2
├── ...
└── monte_carlo_runs/            # MC iterations, stability summaries
```

For a binary study the single comparison appears under `detections/all/PCa/`. Monte-Carlo stability writes recurrence panels and `stability_summary.json` under `monte_carlo_runs/`; see [Artifacts and QA checks](10-artifacts-and-qa-checks.md) for what each artifact means and the handoff checks before the freeze stage.

## Recap

1. One CSV per group under `data/`.
2. One manifest: `controls` + `diseases` (nest `stages[]` for increasing stages), `comparisons: "control_vs_each_disease"`.
3. Pick a profile from the [full catalog](#step-4-pick-a-profile) (`mc_dmp_gene_fc` binary dual-axis, `mc_*` research axes, `staged_*` multi-stage). For FDA-style holdout/claim controls use the [SaMD ladder (ch.18)](18-samd-study-lifecycle.md) (`samd_research` → `samd_holdout_enrichment` → `samd_pivotal`).
4. `methyl-workflow-run --program … --context-file …profile.json --context '{"projectPath":…,"pipelineProfile":…}'`.
5. Read results per comparison under `<project_name>/detections/<control>/<disease>/`.

## See also

- [SaMD study lifecycle (ch.18)](18-samd-study-lifecycle.md) — research → holdout enrichment → pivotal profile ladder, real holdouts, `methyl-study-init`
- [Project config and layout](02-project-config-and-layout.md) — every manifest field
- [Stage: Stability](05-stage-stability.md) — profiles, modeling modes, required `actionConfig` keys
- [DomainProgram language](../reference/domain-program-language.md) — program vs. project vs. profile vs. context
- `packages/methylutils/docs/COHORT_TREE.md` — stages and comparison-shorthand semantics
- [Command cookbook](12-command-cookbook.md) — copy-ready command sequences
