# Process packs

A process pack is the omics modality plus the catalog rows that specialize it. GoliathOmics owns the JSON and the analyte SQL. GoliathApp owns the cfg tables those rows land in. Sync is `scripts/sync_cfg_profiles_and_action_catalog.py --backend postgres`, usually via `scripts/deploy_process_pack_catalog.sh --backend postgres --sync`. See [Release](release.md#process-packs-on-a-new-database).

## Three layers

| Layer | Operator field | Where it lives | What it selects |
|-------|----------------|----------------|-----------------|
| Modality | `regulatory.primary_modality` | Profile and study. Enforced by `workflow_engine/domain/modality_gate.py` | `methylation`, `rnaseq`, or `proteomics` |
| Analyte | `regulatory.primary_analyte` | `workflow_engine/domain/analytes/*.analyte.json` → `cfg.analyte` | Sample matrix |
| Technique | `libraryProtocol` on an assay procedure | `workflow_engine/domain/profiles/procedures/*.procedure.json` → `cfg.assay_procedure` | How the library was made |
| Disease or trait | `diseases` / `controls` on the project manifest, plus `pipelineProcedure` | `schemas/config/project_config.schema.json` and `docs/examples/samd/` | The study cohort on top of a procedure |

There is no `cfg.disease` catalog in this repository. A disease or trait study is an application pack: a project manifest and a small `actionConfig` overlay on a procedure that already exists. Chapters [21](../usage/21-alzheimer-cfdna-pack.md), [23](../usage/23-plant-abiotic-stress-pack.md), and [25](../usage/25-prostate-cancer-pack.md) are those studies. Chapter [24](../usage/24-methylation-application-packs.md) is the pattern.

## Analytes

| `name` | File |
|--------|------|
| `buffy_coat` | `workflow_engine/domain/analytes/buffy_coat.analyte.json` |
| `cfdna` | `workflow_engine/domain/analytes/cfdna.analyte.json` |
| `tissue` | `workflow_engine/domain/analytes/tissue.analyte.json` |
| `plant_tissue` | `workflow_engine/domain/analytes/plant_tissue.analyte.json` |
| `combined` | `workflow_engine/domain/analytes/combined.analyte.json` |

The study `primary_analyte` must match the procedure's `analyteExpectation`. Fill-in behavior for QC and deconvolution is in [Analyte profiles](../ANALYTE_PROFILES.md).

## Techniques

`schemas/config/procedure.schema.json` allows `libraryProtocol` values `wgbs_linear`, `wgbs_pangenome`, `epi_gbs`, and `emseq_targeted`.

| Procedure | Analyte | `libraryProtocol` |
|-----------|---------|-------------------|
| `buffy_wgbs_pangenome_gene_fc` | `buffy_coat` | `wgbs_pangenome` |
| `buffy_wgbs_linear_gene_fc` | `buffy_coat` | `wgbs_linear` |
| `buffy_wgbs_linear_mojo_gene_fc` | `buffy_coat` | `wgbs_linear` (`parabricks.engine` `mojo`) |
| `buffy_wgbs_mvalue_residual_gene_fc` | `buffy_coat` | `wgbs_pangenome` |
| `cfdna_wgbs_plasma` | `cfdna` | `wgbs_linear` |
| `cfdna_emseq_targeted` | `cfdna` | `emseq_targeted` |
| `cfdna_emseq_mhl_survival` | `cfdna` | `emseq_targeted` |
| `plant_wgbs_gene_fc` | `plant_tissue` | `wgbs_linear` |

## Modality programs

| Modality | SamplePrep program | Lifecycle program | Profile |
|----------|--------------------|-------------------|---------|
| Methylation | `sample_prep.program.json` (and `sample_prep_emseq`, `sample_prep_epigbs`, `sample_prep_remediate`) | `study_validation_lifecycle.program.json` | `samd_research` and the other methylation profiles under `workflow_engine/domain/profiles/` |
| RNA-seq | `sample_prep_rnaseq.program.json` | `rnaseq_study_lifecycle.program.json` | `rnaseq_research` |
| Proteomics | `sample_prep_proteomics.program.json` | `proteomics_study_lifecycle.program.json` | `proteomics_research` |

`scripts/deploy_workflow_definitions.sh` compiles the methylation trio always, and the RNA-seq and proteomics programs when the fixture files are present. Chapter [20](../usage/20-rnaseq-process-pack.md) and chapter [22](../usage/22-proteomics-process-pack.md) describe those two modalities.
