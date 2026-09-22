# Analyte-driven pipeline profiles

> **Modality vs analyte.** `regulatory.primary_modality` (`methylation` | `rnaseq` | `proteomics`) selects the **omics process pack** and is distinct from `regulatory.primary_analyte` (the DNA-methylation sample matrix: `cfdna`, `buffy_coat`, `combined`). Modality defaults to `methylation` when unset. The non-methylation packs have their own programs/profiles/actions and the analyte packs below apply to the methylation modality only. See [RNA-Seq process pack](usage/20-rnaseq-process-pack.md) and [Proteomics process pack](usage/22-proteomics-process-pack.md). RNA-Seq and proteomics share a common `samples x features` seam (`omics_features`) feeding the tabular classifier + covariate stacking.

> **Application packs.** An [application pack](usage/24-methylation-application-packs.md) is a study configuration on an existing process modality (cohorts + partitions + a config overlay), not a new process pack. Worked methylation instances: the [Alzheimer cfDNA pack](usage/21-alzheimer-cfdna-pack.md) (disease application — staged Control -> MCI -> AD on `primary_analyte: cfdna`, `neuro-core` enrichment), the [plant abiotic stress pack](usage/23-plant-abiotic-stress-pack.md) (trait application — Control vs Drought on `primary_analyte: plant_tissue`, `plant-stress-core` enrichment), and the [prostate cancer options catalog](usage/25-prostate-cancer-pack.md) (multi-path: buffy / plasma / EM-Seq / `tissue`).

> **Assay procedure packs.** Between process and application, pick a named
> `pipelineProcedure` (e.g. `buffy_wgbs_pangenome_gene_fc`, `cfdna_wgbs_plasma`,
> `cfdna_emseq_targeted`, `plant_wgbs_gene_fc`) under
> [`workflow_engine/domain/profiles/procedures/`](../workflow_engine/domain/profiles/procedures/).
> Procedures pin library protocol, SamplePrep/lifecycle program hints, FeatureCuts
> axis, and covariate defaults. The study `primary_analyte` must match the
> procedure’s `analyteExpectation`. See [Usage ch.24](usage/24-methylation-application-packs.md).

Set **`regulatory.primary_analyte`** once in the study manifest (`cfdna`, `buffy_coat`, or `combined`). The resolver merges analyte-specific defaults into profile/site `actionConfig` via `merge_step_config` in `packages/methylutils/methyl_utils/analyte_profiles.py` (explicit profile or site keys always win). Procedure and instance overlays still win over analyte fill-missing defaults.

The **core QC window** (`alignment_qc.core_guardrails`, `extraction_qc.guardrails`) is published on the **site**. Analyte packs still **fill-missing at instance bake** (fragmentomics, bisulfite, CHG/CHH caps, …). They are **not** part of the portal SQL inherited merge used by the Guardrails grids. Do not pin `fragmentomics.enabled: false` on the site document if analyte fill-missing should still enable cfDNA fragmentomics.

Opt out: `"auto_apply_analyte_profile": false` under `regulatory`.

## What each analyte enables

| Step | `cfdna` | `buffy_coat` | `tissue` | `plant_tissue` |
|------|---------|--------------|----------|----------------|
| `alignment_qc` | cfDNA fragmentomics + **alignment guardrails** + bisulfite QC | **alignment guardrails** + bisulfite QC (no cfDNA fragmentomics profile) | bisulfite QC defaults (no analyte pack) | **alignment guardrails** (min mapping 0.90) + bisulfite conversion-rate gate with **non-CpG cap opened** (`max_non_cpg_methylation_pct: 100`) |
| `fragmentomics` | `methyl-fragmentomics` enabled (WPS + end motifs) | disabled | disabled | disabled |
| `extraction_qc` | mammalian caps (`max_chh`/`max_chg` = 0.02) | mammalian caps | mammalian caps | **CHG/CHH caps lifted** (`max_chh`/`max_chg` = 1.0) — plant non-CG is real biology |
| `cell_deconvolution` | Houseman 6 Ω, or HiTIMED plasma tree (`tumor_fraction` + immune) | Houseman 6 Ω, or HiTIMED immune subtree (no tumor) | HiTIMED full tumor/immune/stromal tree (flat Houseman stays blood-oriented) | **not applicable** (blood-only bases); plant lifecycle program omits the node |
| `enricher` | `library_preset: cancer-core`; CIS-BP **gene_sets + motif_scan + annotate** | CIS-BP **gene_sets** only | defaults unless profile overrides | `library_preset: plant-stress-core`; `organism: Arabidopsis_thaliana` |
| `validation` | `enforce_training_analyte_match: true` | `false` | not set by analyte pack | `false` |

`combined` / unknown analytes: bisulfite QC defaults only.

**`plant_tissue` (Arabidopsis / crop WGBS).** Aliases: `plant`, `leaf`, `root`, `meristem`, `seed`. Selected by the [plant abiotic stress pack](usage/23-plant-abiotic-stress-pack.md) (TAIR10 plus soybean / maize / wheat site recipes). Because plant genomes methylate in CG, CHG and CHH contexts, non-CpG methylation is a biological signal rather than a bisulfite-conversion failure: the conversion-rate gate stays on (spike-in / sidecar) but the non-CpG cap and mammalian CHG/CHH extraction caps are opened. Blood cell deconvolution and cfDNA fragmentomics do not apply. Gene↔trait priors use mapper `enrich_source: plant_traits` (offline TSV), not Open Targets.

**`cell_deconvolution` note.** The method switch and analyte-driven HiTIMED tree roots are **not** applied by the analyte profile merge today; set `method` (`houseman` | `hitimed`) under profile `actionConfig.cell_deconvolution`. HiTIMED reads its tree from the `analyte` field, which defaults to `regulatory.primary_analyte`. There is no dedicated `tissue` entry in `analyte_profiles.py`, so tissue prep/enricher steps use `combined`/unknown defaults unless a profile overrides them. See [Theory ch.07a MethylDeconv](theory/chapters/07a-methyldeconv.md).

Background research on analyte tradeoffs: [docs/research/](research/README.md).

## CIS-BP multi-mode (cfDNA)

When the profile sets `cisbp_modes` under `actionConfig.enricher`, the enricher runs modes in order and merges separate libraries:

- `CIS-BP` — gene_sets (1A)
- `CIS-BP-motif` — motif_scan at DMP loci (1C)
- `CIS-BP-annotate` — CIS-BP metadata on ChEA/TRRUST hits (1B; requires TF libraries from `cancer-core`)

## Plasma retrain

Profiles set guards and defaults; they do **not** train a classifier. For cfDNA production models, still run MC/freeze on plasma cohorts with `model_training_analyte: cfdna`. See [PLASMA_RETRAIN_PATH.md](../packages/methylvalidation/docs/PLASMA_RETRAIN_PATH.md).

## Minimal cfDNA study example

Study manifest (cohorts + regulatory only):

```json
{
  "project_name": "Plasma_cfDNA_CG",
  "output_base": "/work/projects/prostate-cancer",
  "samples_base_path": "/work/samples",
  "chromosomes": ["1", "21", "22"],
  "contexts": ["CG"],
  "comparisons": "control_vs_each_disease",
  "regulatory": {
    "primary_analyte": "cfdna",
    "stage": "feasibility"
  },
  "controls": {
    "label": "healthy",
    "groups": [{ "label": "all", "sample_paths": ["data/healthy_p.csv"] }]
  },
  "diseases": {
    "label": "cancer",
    "groups": [{ "label": "pca", "sample_paths": ["data/pca_p.csv"] }]
  }
}
```

Tool parameters (fragmentomics, enricher CIS-BP modes, validation guards) live in
profile / **procedure** `actionConfig` — prefer
[`cfdna_wgbs_plasma.procedure.json`](../workflow_engine/domain/profiles/procedures/cfdna_wgbs_plasma.procedure.json)
for plasma WGBS, or a study-specific overlay on top. Site defaults remain in
`/work/site/methyl_site.json`.

Example: [`tools/methyl-config-editor/configs/project_Plasma_cfDNA_CG.example.json`](../tools/methyl-config-editor/configs/project_Plasma_cfDNA_CG.example.json).
