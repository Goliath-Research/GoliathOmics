# Assay procedure packs

Named **assay procedure** overlays for the methylation process pack. Pick one with
`pipelineProcedure` in instance context so operators do not hand-merge pangenome,
informME, deconvolution, and FeatureCuts knobs.

| Procedure | Analyte | SamplePrep | Lifecycle | Notes |
|-----------|---------|------------|-----------|-------|
| `buffy_wgbs_pangenome_gene_fc` | `buffy_coat` | `sample_prep` (methylGrapher / `pangenome_wgbs`) | `study_validation_lifecycle` | **Default** human buffy research (accepted 2026-07-28; requires `wl.gfa`) |
| `buffy_wgbs_linear_gene_fc` | `buffy_coat` | `sample_prep` (linear) | `study_validation_lifecycle` | Faster / cheaper linear baseline |
| `cfdna_wgbs_plasma` | `cfdna` | `sample_prep` | `study_validation_lifecycle_no_deconv` | Fragmentomics; no cell deconv |
| `cfdna_emseq_targeted` | `cfdna` | `sample_prep_emseq` | `study_validation_lifecycle_no_deconv` | BED panel + high min_cov |
| `cfdna_emseq_mhl_survival` | `cfdna` | `sample_prep_emseq` | `study_validation_mhl_survival` | Native MHB/MHL + Cox; `researchMode: mhl_survival`; does not replace gene_fc |
| `plant_wgbs_gene_fc` | `plant_tissue` | `sample_prep` | `plant_stress_study_lifecycle` | Trait applications |

Merge order (highest wins first): **instance → procedure → profile/mode → analyte → site**.

Loader: `pipeline_profiles.load_procedure` / `apply_pipeline_procedure` (via
`enrich_instance_context` when `pipelineProcedure` is set).

Schema: [`schemas/config/procedure.schema.json`](../../../../../schemas/config/procedure.schema.json).

Docs: [Usage ch.24](../../../../../docs/usage/24-methylation-application-packs.md).
