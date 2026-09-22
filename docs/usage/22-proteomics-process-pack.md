MethylPipeline supports **proteomics** as a third omics modality alongside DNA methylation
and RNA-Seq. The execution platform (DomainProgram, typed actions, scheduler, CAAS, cfg/wf)
is shared; the front of the pipeline (mass-spec search or panel ingest) is new and the back
reuses the same `samples x features` seam as RNA-Seq. This chapter is the operator guide.

For the full topology and diagrams see the [proteomics end-to-end workflow](../architecture/end-to-end-workflow-proteomics.md).

## Modality selection

Set `regulatory.primary_modality: proteomics` (or `methyl-study-init --modality proteomics`).
Modality is the assay axis and is independent of `primary_analyte`.

## Ingest modes

`actionConfig.proteomics_quant.ingest_mode` picks the path (like RNA `quant_mode`):

| `ingest_mode` | Action | GPU | Output |
|---------------|--------|-----|--------|
| `dia` (default) | `sample.diann` (+ optional `sample.dl_rescore`) → `sample.register_abundance` | Yes (GH200) | DIA-NN report → `abundance.h5` |
| `dda` | `sample.sage` → `sample.register_abundance` | No (CPU) | Sage search + LFQ → `abundance.h5` |
| `panel` | `sample.ingest_panel` | No (CPU) | Olink NPX / SomaScan RFU / open matrix → `abundance.h5` |

Set `rescore: true` under `proteomics_quant` to add Prosit deep-learning rescoring on the
DIA path. Casanovo de novo (`sample.casanovo`) is available as a complementary GPU action.

**DDA via Sage.** `ingest_mode: dda` runs [Sage](https://github.com/lazear/sage) (Apache-2.0,
Rust) — a fast, open database-search engine that replaces the license-encumbered
MSFragger/FragPipe. It is CPU-only and multi-arch, so it lands on cheap CPU workers (not the
GH200s). Sage knobs live under `actionConfig.proteomics_quant` (`missed_cleavages`,
`precursor_tol_ppm`, `fragment_tol_ppm`, `variable_mods` — e.g. phospho on `S`/`T`/`Y` for
tau phosphopeptide discovery, `lfq`). Set `METHYL_SAGE_IMAGE` (or install the `sage`
binary). Sage produces protein LFQ intensities normalized into the same `abundance.h5`
contract as DIA-NN and panels.

## Reference assets (site)

Pin proteomics references once per cluster under site `proteomics_reference`:

```json
"proteomics_reference": {
  "protein_fasta": "/work/genomes/proteomics/GRCh38/uniprot/UP000005640_9606.fasta",
  "spectral_library": "/work/genomes/proteomics/GRCh38/diann/human_plasma.speclib",
  "prosit_model": "/work/genomes/proteomics/models/prosit/intensity_transformer",
  "casanovo_model": "/work/genomes/proteomics/models/casanovo/casanovo_massivekb.ckpt"
}
```

DIA-NN can run library-free with just `protein_fasta`. DL model weights should be pinned as
cfg `reference_asset`s for reproducibility.

## GPU on GH200

`sample.diann` / `sample.dl_rescore` / `sample.casanovo` run on the same Lambda/Nebius
GH200 GPU VMs as Parabricks, each behind its own image env (`METHYL_DIANN_IMAGE`,
`METHYL_PROSIT_IMAGE`, `METHYL_CASANOVO_IMAGE`) and gated by GPU availability. On ARM64
(Grace) the images must be linux/arm64 or multi-arch — see the
[GPU worker runbook](../deployment/gpu_worker_runbook.md#proteomics-gpu-tools-dia-nn-prosit-casanovo).
Panel ingest is CPU-only and lands on non-GPU workers.

## Downstream

`pipeline.protein_de_select` builds the cohort `samples x proteins` matrix (log2 +
median-normalize + per-protein min imputation), ranks by Welch differential abundance,
trains the shared tabular classifier (optionally stacking covariates), and writes a
recurrence-friendly protein panel for `validation.stability`. `feature_mode` is
`proteomics_abundance`.

## Example run

```bash
methyl-workflow-run \
  --program workflow_engine/domain/fixtures/sample_prep_proteomics.program.json \
  --context '{"projectPath":"/work/projects/my-prot-study/configs/project_Healthy_vs_Disease.json","pipelineProfile":"proteomics_research"}'
```

## Not included

- Sage results → spectral library → DIA-NN (DDA-assisted DIA) — a later optional extension.
- TMT/iTRAQ labeled quant (LFQ only this iteration).
- MSFragger/FragPipe is intentionally not used (commercial license); Sage is the open DDA engine.
