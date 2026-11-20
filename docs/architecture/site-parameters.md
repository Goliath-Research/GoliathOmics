# Site parameters

GoliathOmics registers `omics/SiteParameters` as the document type of cfg kind `site` through the existing child-app hook (`goliath-omics-seed-config-document-types` → `cfg.sp_register_kind_document_type`). GoliathApp stays generic: it does not know Omics keys. Operators edit the document in the generic typed grid. There is no child-app UI and no free-form JSON editor.

The schema is the bundled form of `schemas/config/site_manifest.schema.json`. Field names, types, units, and defaults come only from schemas, Pydantic step models, and worker/resolver code that already read these values. Defaults that are not declared in that code are omitted.

## Precedence

Workers already merge per-action configuration in `packages/methylutils/methyl_utils/action_config_resolver.py` (`resolve_action_config`). Later overlays win; JSON `null` deletes a key.

1. Site (`siteConfig` / `actionConfig` slice, plus a few top-level site pins such as `reference_genome`, `parabricks`, `rna_reference`, `proteomics_reference`)
2. Pipeline profile `actionConfig` (and mode overlays)
3. Program / procedure overlay
4. Instance / step overlay (`stepOverride` or the instance `actionConfig` after finalize rebuilds it)
5. Analyte fill-missing-only (does not override a value that is already set)

`workflow_engine/domain/workflow_context.py` states the same order as highest-first: instance → procedure → profile/mode → analyte → site, and rebuilds `actionConfig` so a procedure JSON null can clear a profile key. `finalize_instance_context` bakes each slice into `resolvedConfig__<key>` scope variables that the worker receives on the task. A worker `stepOverride` still wins over that baked slice.

## How values reach a cluster's workers

1. Study start records a published `cfg.site` id on the instance link (preferring the site named `default`, same rule as today).
2. `ops/study_start_queue.py` loads that document with `portal.fn_published_site_document(site_id)` (generic GoliathApp function) and places it on the start body as `siteConfig`. If the function is missing or returns null, the worker/bake path falls back to the work-root site manifest (`load_site_manifest`, typically `/work/site/site.json` after `goliath-cfg materialize`).
3. Sample-prep planning resolves reference FASTA/GTF from that `siteConfig` (not only from the work-root file). `finalize_instance_context` / `resolve_action_config` then merge site → profile → overlays and bake `resolvedConfig__*`.
4. Workers prefer the baked `resolvedConfig` on the task input and do not re-read the site file when that slice is present.

There is no `wf.cluster.site_id` column today; a cluster's workers see the site document that was baked for the instance (or the manifest on that cluster's work root).

## Top-level site fields

All of these already existed on `site_manifest.schema.json`. None of them declare a schema default.

| Field | Type | Derived from |
|-------|------|----------------|
| `reference_selection` | object (`linear`, `gene_annotation`, `pangenome` strings) | `site_manifest.schema.json` |
| `reference_genome` | object (`fasta` string) | `site_manifest.schema.json` |
| `annotation` | object (`gtf` string) | `site_manifest.schema.json` |
| `methyl_mapper_home` | string | `site_manifest.schema.json` |
| `caches` | object of string paths | `site_manifest.schema.json` |
| `parabricks` | `parabricks.schema.json` (legacy top-level copy) | `ParabricksStepConfig` in `workers/methyl_worker/task_models/sample_prep_models.py`; also `site_slice_for_action` |
| `methyl_extract` | `methyl_extract.schema.json` (legacy top-level copy) | existing extract schema; merged by `site_slice_for_action` |
| `pangenome_genome` | object (gbz, dist, min, zipcodes, ref_paths, linear_ref_fasta) | `site_manifest.schema.json` / `resolve_pangenome_genome` |
| `pangenome_wgbs_genome` | object (index bundle) | `site_manifest.schema.json` / `resolve_methylgrapher_wgbs_genome` |
| `rna_reference` | object (star_index_dir, gtf, reference_fasta, kallisto_index, transcriptome_fasta, tx2gene) | `site_manifest.schema.json` / `resolve_rna_reference` |
| `proteomics_reference` | object (protein_fasta, spectral_library, prosit_model, casanovo_model) | `site_manifest.schema.json` / `resolve_proteomics_reference` |
| `testing` | object | `site_manifest.schema.json` (unchanged; not a pipeline threshold) |

`scripts/default_methyl_site.json` pins example paths and a Parabricks image for a work-root layout. Those are instance pins, not type defaults, and are not copied onto the schema.

## actionConfig keys

Each key is a slice workers already resolve from the site (ActionConfigKey, site examples, and/or `site_slice_for_action`). Site documents do not require the per-run path fields some step schemas mark required.

| Key | Schema source |
|-----|----------------|
| `alignment_qc` | `sample_prep_guardrails.schema.json#/properties/alignment_qc` (already referenced) |
| `extraction_qc` | `sample_prep_guardrails.schema.json#/properties/extraction_qc` (already referenced) |
| `fragmentomics` | `fragmentomics.schema.json` |
| `methyl_extract` | `methyl_extract.schema.json` |
| `centroid` | `centroid.schema.json` |
| `detection` | `detection.schema.json` |
| `dmp_selection` | `dmp_selection.schema.json` |
| `mapper` | `mapper.schema.json` |
| `enricher` | `enricher.schema.json` |
| `classifier` | `classifier.schema.json` |
| `gene_selection` | `gene_selection.schema.json` |
| `predictor` | `predictor.schema.json` |
| `validation` | `validation.schema.json` |
| `progression` | `progression.schema.json` |
| `rna_qc` | `rna_qc.schema.json` |
| `rna_de_select` | `rna_de_select.schema.json` |
| `rna_align` | `rna_align.schema.json` (rna_fq2bam_runner path overrides + `ParabricksStepConfig` knobs copied by `site_slice_for_action`) |
| `proteomics_qc` | `proteomics_qc.schema.json` |
| `protein_de_select` | `protein_de_select.schema.json` |
| `proteomics_quant` | `proteomics_quant.schema.json` (`diann_runner.py`) |
| `residualize` | `residualize.schema.json` |
| `methylation_confounder_scores` | `methylation_confounder_scores.schema.json` |
| `cell_deconvolution` | `cell_deconvolution.schema.json` (used in `site_tair10.example.json`) |
| `storage_transfer` | `storage_transfer.schema.json` (used in site examples) |
| `derived_measures` | `derived_measures.schema.json` |
| `info_measures` | `info_measures.schema.json` |
| `mhb_mhl` | `mhb_mhl.schema.json` |
| `parabricks` | `parabricks.schema.json` from `ParabricksStepConfig` |
| `methylgrapher_wgbs` | `methylgrapher_wgbs.schema.json` from `MethylGrapherWgbsStepConfig` |
| `docker_align` | `docker_align.schema.json` from `docker_align_runner.py` |
| `demultiplex` | `demultiplex.schema.json` from `demultiplex_runner.py` |

## QC and threshold defaults that already exist in schemas

Units are taken from the field name or the existing schema title/description (for example `_pct`, `_bp`, `_rate`). No unit was invented.

From `sample_prep_guardrails.schema.json` `$defs` (alignment / extraction QC):

| Field | Default | Notes |
|-------|---------|--------|
| `AlignmentGuardrailsFull.enabled` | `false` | |
| `AlignmentGuardrailsFull.flagstat_enabled` | `true` | |
| `AlignmentGuardrailsFull.max_secondary_supplementary_rate` | `0.05` | rate |
| `AlignmentGuardrailsFull.max_supplementary_rate_flagstat` | `0.02` | rate |
| `AlignmentGuardrailsFull.min_mapping_rate` | `0.98` | rate |
| `AlignmentGuardrailsFull.min_properly_paired_rate` | `0.9` | rate |
| `AlignmentGuardrailsFull.min_gc_coverage_uniformity` | none (`null` in schema) | |
| `AlignmentGuardrailsFull.wgbs_min_mapped_rate` | none | |
| `BisulfiteConversionFull.enabled` | `false` | |
| `BisulfiteConversionFull.max_deamination_qscore_proxy` | `30` | Phred-style qscore |
| `BisulfiteConversionFull.max_non_cpg_methylation_pct` | `2.0` | percent |
| `BisulfiteConversionFull.min_conversion_rate_pct` | `99.0` | percent |
| `BisulfiteConversionFull.sidecar_filename` | `bisulfite_conversion.json` | |
| `BisulfiteConversionFull.source` | `auto` | |
| `CoreGuardrailsFull.max_at_dropout` | `3.0` | |
| `CoreGuardrailsFull.max_gc_dropout` | `5.0` | |
| `CoreGuardrailsFull.max_deamination_qscore` | `30` | qscore |
| `CoreGuardrailsFull.median_insert_max_bp` | `300` | bp |
| `CoreGuardrailsFull.median_insert_min_bp` | `150` | bp |
| `CoreGuardrailsFull.min_mean_quality` | `35.0` | |
| `CoreGuardrailsFull.min_oxog_qscore` | `20` | qscore |
| `CoreGuardrailsFull.min_pf_percent` | `90.0` | percent |
| `CoreGuardrailsFull.min_q30_percent` | `85.0` | percent |
| `CoreGuardrailsFull.min_quality_post20` | `30.0` | |
| `CycleScreeningFull.enabled` | `true` | |
| `CycleScreeningFull.broad_bad_cycle_count` | `8` | cycles |
| `CycleScreeningFull.localized_max_span` | `6` | |
| `CycleScreeningFull.max_trim_bases` | `8` | bases |
| `CycleScreeningFull.multi_region_min_separate_dips` | `2` | |
| `CycleScreeningFull.r2_quality_threshold` | `30.0` | |
| `CycleScreeningFull.r2_start_window_cycles` | `5` | cycles |
| `CycleScreeningFull.read_edge_window` | `10` | |
| `CycleScreeningFull.recovery_cycles` | `10` | cycles |
| `CycleScreeningFull.fallback_trim_*` / `read_length` / `remediate_without_cycles` | none | |
| `ExtractionQcGuardrailFull.max_chg_methylation_level` | `0.02` | |
| `ExtractionQcGuardrailFull.max_chh_methylation_level` | `0.02` | |
| `ExtractionQcGuardrailFull.max_discard_fraction` | `0.9` | |
| `ExtractionQcGuardrailFull.min_autosomal_coverage_uniformity_ratio` | `0.5` | |
| `ExtractionQcGuardrailFull.min_cpg_weighted_mean_coverage` | `10.0` | |
| `FragmentomicsFull.enabled` | `false` | |
| `FragmentomicsFull.long_fragment_min_bp` | `300` | bp |
| `FragmentomicsFull.max_short_fragment_fraction` | `0.35` | |
| `FragmentomicsFull.median_insert_max_bp` | `220` | bp |
| `FragmentomicsFull.median_insert_min_bp` | `120` | bp |
| `FragmentomicsFull.nucleosome_peak_bp_max` | `200` | bp |
| `FragmentomicsFull.nucleosome_peak_bp_min` | `140` | bp |
| `FragmentomicsFull.profile` | `off` | |
| `FragmentomicsFull.short_fragment_max_bp` | `150` | bp |
| `OptionalGuardrailsFull.duplication_rate_max` | none | |
| `OptionalGuardrailsFull.min_pf_reads` | none | |

Other step schemas already ship their own defaults (detection alphas, dmp_selection cutoffs, enricher CIS-BP windows, and so on). Those values were not changed and were not re-invented here; they stay on the referenced schema files.

`actionConfig.proteomics_quant.threads` defaults to `8`, taken from `os.environ.get("METHYL_DIANN_THREADS", "8")` in `workers/methyl_worker/diann_runner.py`.

## Fields without a code-sourced default

- Every top-level site path/pin listed above (references, caches, genomes).
- Every field on `ParabricksStepConfig` / `actionConfig.parabricks` (model defaults are `None`).
- Every field on `MethylGrapherWgbsStepConfig` / `actionConfig.methylgrapher_wgbs` (model defaults are `None`). The Field description for `engine` notes a runtime default of `python` when unset, and `conversion_rate_sidecar` is described as defaulting to `bisulfite_conversion.json` at runtime; neither is a model default, so the schema leaves them unset.
- `actionConfig.docker_align`: `image`, `argv`/`command`, `extra_docker_args`, `gpu_flags` (required at runtime or empty; no constant default).
- `actionConfig.demultiplex`: `barcode_tsv`, `barcode_len` (runtime default is `len(barcode)`, not a constant), `skip`.
- `actionConfig.rna_align` path and engine knobs (no non-null defaults).
- `actionConfig.proteomics_quant.image` and reference path overrides (no default; missing image is a hard error unless `METHYL_DIANN_IMAGE` is set).
- Guardrail fields whose schema default is JSON `null` (for example `min_gc_coverage_uniformity`, `wgbs_min_mapped_rate`, `duplication_rate_max`, `min_pf_reads`, cycle-screening fallback trims).

Program `scopeDefaults` only declare `centroid1Dir` and do not add site parameter defaults. Compile / pipeline profile presets remain profile-level flags, not site document fields.

## Verifier

`scripts/verify_omics_config_document_types.sh` checks that cfg kind `site` is registered to `omics/SiteParameters` and kind `pipeline_profile` to `omics/PipelineProfile`, and that both data types are published.
