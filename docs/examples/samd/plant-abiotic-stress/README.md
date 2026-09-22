# Plant abiotic stress methylation pack (example)

Instance of the [methylation application-pack pattern](../../../usage/24-methylation-application-packs.md)
(trait application). A **binary Control vs Drought** *Arabidopsis thaliana* leaf WGBS
methylation study on the existing methylation control plane. It selects assay procedure
`plant_wgbs_gene_fc` (linear WGBS, gene FeatureCuts, plant lifecycle without blood
deconvolution), the `samd_research` profile, and the `plant_tissue` analyte profile. The
pack is config + cohorts + partitions + a trait overlay plus a non-human site reference
— no new actions or aligners.

This instance extends the application-pack pattern with plant platform unblockers: the
`plant_tissue` analyte (plant-safe QC that treats CG/CHG/CHH as real biology, no cfDNA
fragmentomics), a linear TAIR10 site reference, a lifecycle program **without** blood
cell deconvolution, and the `plant-stress-core` enrichment preset.

See the operator guide: [Usage ch.23 Plant abiotic stress pack](../../../usage/23-plant-abiotic-stress-pack.md),
the analyte defaults in [ANALYTE_PROFILES](../../../ANALYTE_PROFILES.md), and the plant
epigenomics background in [Plant Research](../../../research/Plant%20Research.md).

## Files

| File | Role |
|------|------|
| `project_Control_vs_Drought.json` | Default (Arabidopsis): Control vs Drought, chroms 1–5 |
| `context_plant_abiotic_stress.json` | Arabidopsis overlay: `pipelineProcedure=plant_wgbs_gene_fc`, `plant_traits` prior + `plant-stress-core`, `string_species=3702` |
| `project_Control_vs_Drought_{soybean,maize,wheat}.json` | Crop manifests (same binary design; crop chromosome lists) |
| `context_{soybean,maize,wheat}_drought.json` | Crop overlays (`string_species` 3847 / 4577 / 4565) |
| `data/*.csv` | Shared cohort CSV stubs (replace with real, plant-disjoint sample IDs) |
| `data/arabidopsis_drought_gene_traits.tsv` | Demo offline gene↔trait prior (`enrich_source=plant_traits`) |

## Provision a plant genome (once per cluster)

Plants align linear only (no HPRC pangenome). Pick one site recipe:

| Species | Download script | Site example | STRING taxon |
|---------|-----------------|--------------|--------------|
| Arabidopsis (TAIR10) | `scripts/download_arabidopsis_tair10.sh` | `site_tair10.example.json` | 3702 |
| Soybean (Wm82 / v2.1) | `scripts/download_glycine_max_wm82.sh` | `site_glycine_max_wm82.example.json` | 3847 |
| Maize (B73 NAM 5.0) | `scripts/download_zea_mays_b73.sh` | `site_zea_mays_b73.example.json` | 4577 |
| Wheat (IWGSC) | `scripts/download_triticum_aestivum_iwgsc.sh` | `site_triticum_aestivum_iwgsc.example.json` | 4565 |

```bash
scripts/download_arabidopsis_tair10.sh   # or soybean / maize / wheat script
export METHYL_SITE_CONFIG=workflow_engine/domain/profiles/site_tair10.example.json
```

Wheat toplevel FASTA is large (~4 GB compressed); restrict study `chromosomes` (e.g. `["1A"]`) for research runs if needed.

## Instantiate on /work

Studies live under `/work/projects/<study>/`. Scaffold with the CLI, then copy this
manifest/overlay in:

```bash
methyl-study-init \
  --study-id plant-abiotic-stress \
  --name Control_vs_Drought \
  --analyte plant_tissue \
  --modality methylation \
  --binary \
  --intended-use "Research-use Arabidopsis drought methylation study." \
  --output-root /work/projects
# then fill data/*.csv and assign plant-disjoint validation_partitions.
```

## Run

```bash
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/plant_stress_study_lifecycle.program.json \
  --context-file docs/examples/samd/plant-abiotic-stress/context_plant_abiotic_stress.json
```

Enrichment uses the `plant-stress-core` library preset from the enrichment preset
registry (`methyl-cfg sync-library-presets`), not the oncology `cancer-core` default.
Human-symbol Enrichr libraries do not map to Arabidopsis AGI locus IDs, so for true
Arabidopsis/crop term enrichment supply a custom plant GMT via
`actionConfig.enricher.libraries`; `plant-stress-core` (organism-general GO terms) is the
discovery default. Cell deconvolution is not run (blood-only bases); the plant lifecycle
program omits that node.

**Plant trait prior (not Open Targets).** Open Targets / DisGeNET are human-only. The
Arabidopsis overlay sets `mapper.enrich_source: plant_traits` and
`mapper.plant_traits_path` to the demo TSV above (columns: `gene`, `disease_term`,
optional `score` / `evidence_level`). For crops, point `plant_traits_path` at a
SoyBase / MaizeGDB / WheatIS / Gramene extract with the same column names.

## Other crops

Use the matching project + context pair with the crop site:

```bash
export METHYL_SITE_CONFIG=workflow_engine/domain/profiles/site_glycine_max_wm82.example.json
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/plant_stress_study_lifecycle.program.json \
  --context-file docs/examples/samd/plant-abiotic-stress/context_soybean_drought.json
```

Same program and analyte; only site pins, chromosome list, organism, and STRING taxon change.

## Optional: plant cell deconvolution / epi-GBS

- **Atlas:** provision a Houseman/HiTIMED JSON, set site `actionConfig.cell_deconvolution`,
  run `plant_stress_study_lifecycle_with_deconv.program.json` (default lifecycle still omits
  the node). CI fixture:
  `workflow_engine/domain/checks/plant_abiotic_stress/data/plant_houseman_seed_fixture.json`.
- **epi-GBS:** `libraryProtocol: epi_gbs` + `sample_prep_epigbs.program.json` +
  `actionConfig.docker_align` image/argv (Parabricks WGBS SamplePrep stays separate).
  See [ch.23](../../../usage/23-plant-abiotic-stress-pack.md) and
  [plant-deconv-epigbs-seams](../../../plans/plant-deconv-epigbs-seams.plan.md).
