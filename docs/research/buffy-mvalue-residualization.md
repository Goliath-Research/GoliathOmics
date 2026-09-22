# Buffy M-value residualization

Research note for the **opt-in** buffy procedure `buffy_wgbs_mvalue_residual_gene_fc`. This is not a new omics process pack. Shipped methylation / RNA / proteomics process-packs, `buffy_wgbs_pangenome_gene_fc`, `mc_stability.program.json`, and `study_validation_lifecycle.program.json` keep today’s unadjusted DMP path.

Companion canvas: [Buffy M-value residualization](../canvas/buffy-mvalue-residualization.canvas.tsx). Implementation (each confounder): [M-value residualization](../implementation/mvalue-residualization.md). Plan: [buffy-mvalue-residualization.plan.md](../plans/buffy-mvalue-residualization.plan.md).

## Isolation

Residualization rewrites the betas that centroids, DMPs, and `gene_importance` see. It is therefore a **new assay procedure + forked DomainPrograms** that reuse catalog actions:

| Artifact | Touched? |
|----------|----------|
| Methylation / RNA / proteomics process packs | No behavior change |
| `buffy_wgbs_pangenome_gene_fc` and other procedures | Untouched |
| `mc_stability` / `study_validation_lifecycle` | Forks only (`*_residual`) |
| `pipeline.centroid` / classifier | Opt-in: transform runs iff `residualizeCoefDir` (or `ClassificationConfig.residualize_coef_dir`) is bound |
| `pipeline.methylation_confounder_scores`, `pipeline.residualize_fit` | New catalog actions; shipped programs never call them |
| `analyte_profiles.py` buffy defaults | Untouched — do not auto-enable from `primary_analyte: buffy_coat` |

## Leakage table

| Quantity | When it may see all samples | Must be train-only |
|----------|-----------------------------|--------------------|
| Houseman / HiTIMED Ω | Yes (no healthy/cancer label) | — |
| Smoking / clock / BMI / CRP scores | Yes (label-free weighted betas) | — |
| OLS `M ~ Z` coefficients | **No** | Fit on that split’s **training IDs only**, pooled healthy+cancer, **no Group term** |
| Apply `β_adj` | Train and held-out use the **same frozen** `γ̂` | Never re-fit on held-out or a new production sample |
| Monte Carlo | Repeat fit inside every iteration | Do not residualize the full cohort then split |
| Production / new sample | Compute scores independently on the incoming sample | Apply freeze-time coefficients from `production/residualize/` |

Constant covariates (all never-smokers, etc.) are dropped before OLS (`variance_threshold` in `actionConfig.residualize`).

## M-value round-trip

MethylCentroid and MethylDetector are defined on \(x_{si}=m_{si}/(m_{si}+u_{si})\in[0,1]\). Raw OLS residuals are not betas. Locked transform (`methyl_utils.mvalue_residualize`):

\[
M=\log_2\frac{\beta}{1-\beta},\qquad
\beta_{\mathrm{adj}}=\frac{2^{M_{\mathrm{res}}}}{1+2^{M_{\mathrm{res}}}}
\]

Clip \(\beta\) to \([\varepsilon,1-\varepsilon]\) before \(M\); clip \(\beta_{\mathrm{adj}}\) to \((0,1)\) after the inverse. \(\varepsilon\) is a procedure/site knob (`m_value_eps`, Pydantic `default=None`).

\(\beta_{\mathrm{adj}}\) is an **adjusted signal forced back into \((0,1)\)**, not a biological methylation proportion. Detector ECDFs at smoking- or cell-type–driven loci will move; that is the point of the adjustment.

Count summaries \(S_m\)/\(S_u\) stay **raw** (coverage gating, binomial thinning). Only \(x_{si}\) that enter \(S_x\), \(S_{x^2}\), and histogram `bin_counts` are replaced.

## Confounder panels

`pipeline.methylation_confounder_scores` is label-free and runs once per study. Per-confounder mechanics (Hannum 2013 age score, smoking, BMI, CRP, Ω ALR) live in [M-value residualization implementation](../implementation/mvalue-residualization.md). Panels are versioned JSON under `methyl_utils/data/confounder_panels/` or a site/reference asset path.

Missing or low-coverage panel CpGs set sample-level `score_status` (`ok` / `partial` / `insufficient_markers`). The action does not invent clinical BMI.

Ω uses the same Neu-referenced ALR already used for ECDF second-stage composition (avoid a simplex dummy trap). Procedure default **includes Ω**. Operators may omit `composition_columns` to keep composition-mediated DMPs and today’s ALR stacker.

## Inference

1. Compute the same scores on the new sample independently.
2. Apply **frozen** freeze-time coefficients from `{output_base}/production/residualize/` (`residualize-{chrom}-{ctx}.npz` + `residualize_manifest.json`).
3. Never re-fit OLS on the incoming sample.
4. Classifier/predictor must bind the same coefficient directory; scoring residualized centroids on raw sample betas is invalid.

## `gene_importance` is unchanged

Keep the canonical chain in [`packages/methylmapper/docs/BIOLOGICAL_IMPORTANCE_AUDIT.md`](../../packages/methylmapper/docs/BIOLOGICAL_IMPORTANCE_AUDIT.md):

`effect_size` (detector) → `w_i = frequency × bio_weight(feature, hyper|hypo)` → `gene_importance`.

After residualization this quantity means **confounder-adjusted host-response**, not composition-dominated leukocyte mix.

### Causal caveat (Ω as a confounder)

If cancer → inflammation → Ω → methylation, residualizing Ω **removes part of the buffy cancer signal** from the DMP list. That is the Grok recommendation for “forced buffy + multiple confounders.” Operators who want composition-mediated DMPs omit Ω from `residualize.composition_columns` and keep the ECDF second-stage ALR stack.

## Sensitivity

`methyl-residualize-sensitivity` compares unadjusted vs adjusted DMP Jaccard, `gene_importance` Spearman, and overlap with panel BEDs (those sites should largely disappear if adjustment worked). If the panel barely changes, confounding was weak; if it changes dramatically, confounding was large. Report both. Do not silently pick one.

## Topology (residual fork only)

Shipped freeze order remains `freeze_detect → mapper → derived_measures → cell_deconvolution → …`. The residual programs run deconv + scores **before** the MC centroid loop, `residualize_fit` per iteration on train IDs, and again at freeze (coefficients persisted for inference). Duplicate post-mapper deconv is omitted on the residual lifecycle because Ω already exists.
