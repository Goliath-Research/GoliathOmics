# MethylPredictor, MethylValidation, and MethylDiseaseProgression {#sec-methylpredictor-validation}
## Role

`methylpredictor`, `methylvalidation`, and `methyldiseaseprogression` do not introduce a new generative methylation model. Instead they evaluate, operationalize, and synthesize outputs exported by detector/classifier/mapper/enricher stages.

- `methylpredictor` applies trained classifier artifacts to new cohorts and computes prediction summaries.
- `methylvalidation` creates repeated train/validation splits and aggregates run-level metrics across Monte Carlo style repetitions.
- `methyldiseaseprogression` aggregates per-comparison mapper/enricher outputs into ordered cross-stage progression tables and labels.

## MethylPredictor: Labeled Evaluation

When labels are available, `methylpredictor` computes standard classification metrics using `sklearn.metrics`. For $n$ labeled samples with true labels $y_i$ and predictions $\hat y_i$,

<div id="eq-predictor-metrics" markdown="1">

$$
\operatorname{Accuracy}
=
\frac{1}{n}\sum_{i=1}^{n}\mathbf{1}\{\hat y_i = y_i\},
$$

$$
\operatorname{BalancedAccuracy}
=
\frac{1}{K}\sum_{k=1}^{K}
\operatorname{Recall}_k.
$$

</div>

For binary classification the package also reports:

<div id="eq-entropy" markdown="1">

$$
\operatorname{Sensitivity} = \operatorname{Recall}_{\text{positive}},
\qquad
\operatorname{Specificity} = \operatorname{Recall}_{\text{negative}}.
$$

For multiclass settings it reports per-class precision, recall, $F_1$, as well as macro and weighted $F_1$ summaries.

These are standard descriptive metrics. Their interpretation depends on how the evaluation cohort was assembled rather than on any special estimator inside the package.

## Blind Prediction Summaries

In blind mode, `methylpredictor` cannot estimate discrimination performance because no truth labels are available. Instead it reports:

- predicted class counts,
- mean predicted probability per class, and
- mean Shannon entropy of the per-sample class probabilities.

For one sample with class probabilities $p_1,\dots,p_K$, entropy is

$$
H(p) = -\sum_{k=1}^{K} p_k \log p_k.
$$

</div>

Low entropy corresponds to confident predictions; high entropy corresponds to diffuse class assignments. This is an uncertainty summary, not an accuracy estimate.

## MethylValidation: Split Strategy

`methylvalidation` implements repeated train/validation splitting by cohort. For each cohort with $n$ samples and train fraction $\rho$, the code computes

<div id="eq-stability-frequency" markdown="1">

$$
n_{\text{train}} = \max(1, \lfloor \rho n \rfloor),
$$

and then reduces $n_{\text{train}}$ to $n-1$ if necessary so that the validation set is non-empty for $n > 1$.

This is called “stratified” in the code because each cohort is split separately, but it is not identical to the textbook `StratifiedKFold` abstraction. The package performs independent random shuffles within each cohort and then concatenates the resulting class-specific train and validation groups.

## Monte Carlo Evaluation

The validation package repeatedly:

1. generates train and validation cohorts,
2. writes run-specific project files,
3. runs the full pipeline from centroid through predictor, and
4. aggregates scalar metrics across repetitions.

If $m_r$ denotes some metric from run $r$, the package reports descriptive summaries such as

$$
\bar m = \frac{1}{R}\sum_{r=1}^{R} m_r,
\qquad
s_m^2 = \frac{1}{R-1}\sum_{r=1}^{R}(m_r - \bar m)^2,
$$

along with empirical percentiles.

This is best interpreted as repeated internal validation of the **whole pipeline**, not as a formal confidence interval for an external deployment population.

## What The Validation Layer Does Not Do

The current implementation does not attempt:

- nested hyperparameter tuning,
- exact sampling-theory confidence intervals for each metric,
- dependence-aware aggregation across repeated splits, or
- external-cohort transportability analysis.

Those omissions are not flaws in themselves, but they matter for publication wording. The package measures empirical variation across repeated pipeline runs; it does not prove generalization bounds.

## Stability Analysis {#sec-stability-analysis}
After completing a set of Monte Carlo runs, the `--stability` mode aggregates DMP recurrence across runs. For each discovery DMP $d$ (a `(chromosome, position)` pair), define the recurrence indicator

$$
\mathbf{1}_r(d) =
\begin{cases}
1 & \text{if } d \in \mathcal{D}_{\text{disc},r} \\
0 & \text{otherwise}
\end{cases}
$$

where $\mathcal{D}_{\text{disc},r}$ is the set of discovery DMPs from run $r$. When a minimum balanced accuracy threshold $\beta_{\min}$ is configured, only qualifying runs (those with $\widehat{\text{BA}}_r \ge \beta_{\min}$) contribute to the count. Let $R^*$ denote the number of qualifying runs. The estimated recurrence frequency is

$$
\hat{f}(d) = \frac{1}{R^*} \sum_{r=1}^{R^*} \mathbf{1}_r(d).
$$

</div>

A DMP is declared **stable** if $\hat{f}(d) \ge \tau$ for a user-specified threshold $\tau$ (default 0.7, `stability_dmp_freq`). The output is a CSV file `stable_dmps_production.csv` containing all stable DMPs with their frequencies, sorted by decreasing frequency.

Gene stability is an analogous measure over enricher gene lists: the frequency at which each gene appears in enrichment results across qualifying runs.

**Theoretical note.** $\hat{f}(d)$ is an empirical frequency, not a formal probability. It depends on the train fraction $\rho$, the random seed, and the number of iterations $R$. With small $R$ the estimate is noisy. The threshold $\tau$ is a heuristic engineering choice, not derived from any optimality criterion. Higher $\tau$ produces smaller, more reproducible panels; lower $\tau$ retains more positions at the cost of including split-dependent noise.

## Production Freeze {#sec-production-freeze}
The production freeze converts the stable DMP panel into a fixed discovery/interpretable production run on the full dataset. The `--freeze` flag calls `freeze_production_model()` in `packages/methylvalidation/methyl_validation/stability.py`, which:

1. Merges one or more per-chromosome stable DMP CSVs into a single genome-wide panel (`stable_dmps_genomewide.csv`).
2. Loads the base project JSON and injects `fixed_dmp_panel` into resolved `actionConfig.detection` (pointing at the merged CSV).
3. Writes a `production/project.json` with `project_name = "production"` and `output_base` routed to the production directory.
4. Executes `run_pipeline_for_production()`, which runs: `methyl-centroid → methyl-detector(fixed panel) → methyl-mapper → methyl-enricher`.
5. Optionally runs `methyl-disease-progression` when profile `actionConfig.progression.enabled=true`.
6. Writes a `production_summary.json` summarizing the run.

Final model fitting is a separate explicit step: `methyl-validation --model` runs `methyl-classifier → methyl-predictor` on `production/project.json`.

If configured (`actionConfig.validation.require_biological_review_for_model=true`), `--model` is blocked until `biological_review_confirmed=true`.

The production project JSON (`production/project.json`) is the artifact that links the frozen model to the `--predictor-only` workflow: it encodes the centroid directories, detection output paths, and classifier paths so that `methyl-predictor` can resolve them without re-running any upstream steps.

## Hybrid Generative Backend (`model_backend=generative_hybrid`)

The `--model` stage now supports a first-class hybrid generative backend in addition to the legacy ECDF path and the tabular sklearn path. This backend is orchestrated by `methylvalidation` and uses the same detector-derived DMP bundle as the other non-ECDF model backends.

### Data Contract and Bundle Reuse

The backend does **not** re-derive DMP biology or feature importance. Instead it consumes the `ModelFeatureBundle` generated from detector exports:

- DMP index (`chromosome`, `position`, `context`)
- detector-side `effect_size` and `weight` columns
- class labels resolved from project comparisons/groups

This keeps biological weighting aligned with detector outputs and avoids backend-specific feature redefinition.

### Modeling Structure

The implemented hybrid backend follows a VAE-style latent-density pattern:

1. encode weighted methylation features into a latent representation $z$ (current implementation uses a linear latent encoder surrogate),
2. fit class-conditional latent densities (currently diagonal Gaussian),
3. compute class posteriors via Bayes-style normalization.

In symbolic form, for classes $k=1,\dots,K$:

$$
p(z \mid y=k) \approx \mathcal{N}\!\left(\mu_k,\operatorname{diag}(\sigma_k^2)\right),
$$

$$
\hat P(y=k\mid z)
=
\frac{\pi_k\,p(z\mid y=k)}
{\sum_{r=1}^{K}\pi_r\,p(z\mid y=r)}.
$$

This yields likelihood-based scoring for both binary and multiclass settings while preserving the detector-derived feature index.

### Orchestration in `--model`

When profile `actionConfig.validation.model_backend="generative_hybrid"`, `run_pipeline_for_model()` executes:

1. **model-bundle**: `build_model_feature_bundle(...)`
2. **generative-train**: `train_generative_model(...)`
3. **generative-predictor**: `predict_generative_model_from_project(...)`

instead of the legacy `methyl-classifier -> methyl-predictor` ECDF chain.

### Configuration Surface

The backend is controlled under profile `actionConfig.validation`:

- `model_backend: "generative_hybrid"`
- `model_bundle_dir` (and legacy `model_weight_column`; weighting is canonicalized to `effect_size`)
- `generative_latent_dim`
- `generative_kl_weight`
- `generative_density_type` (currently `diag_gaussian`)
- `generative_epochs`, `generative_batch_size`, `generative_seed`
- `generative_calibrate`
- `covariates_path`, `covariate_id_column`
- `generative_covariates_strict`

Those fields are part of the same validation config object as Monte Carlo settings, so backend choice is fully reproducible from the merged profile and `mc_config.json` snapshot.

## MethylDiseaseProgression Synthesis

`methyl-disease-progression` is a post-freeze synthesis step that treats disease groups as an ordered stage sequence and aggregates entity-level signals across stages.

### Inputs and Stage Resolution

For each ordered stage, the code reads:

- mapper combined gene table (`all-gene_name-combined.csv` by default),
- enricher pathway table (`enrichment_merged.csv`, fallback `enrichment_top_q0.05.csv`),
- optional module table (`modules_ranked.csv`).

Stage order comes from:

1. CLI `--ordered-comparison-labels` (highest precedence),
2. profile `actionConfig.progression.ordered_comparison_labels` (or alias `ordered_disease_groups`),
3. project comparison order.

### Entity Scores

The synthesis creates long-format tables for genes, pathways, and modules with `(entity_type, entity_id, stage_index, rank, score)`.

- **Genes:** score is max of available gene-weight columns per gene (e.g. `total_weight`, `gene_importance`).
- **Pathways:** when q-values exist, score is transformed to

$$
\operatorname{score}_{\text{pathway}} = -\log_{10}(\max(q, 10^{-300})),
$$

so larger values indicate stronger enrichment.
- **Modules:** score is the module score exported by `modules_ranked.csv`.

### Progression Labels

For each entity across ordered stages, the package assigns categorical progression labels:

- `stage_specific`, `early_only`, `late_only`,
- `stable_across_stages`,
- `monotonic_up` / `monotonic_down` (based on adjacent score differences over at least three points).

These are deterministic rule-based labels, not fitted trajectory models.

### Outputs

The progression step writes:

- `genes_long.csv`
- `pathways_long.csv`
- `modules_long.csv`
- `entities_progression_labels.csv`
- `summary.json`
- optional `report.md` (when `report_md=true`)

This layer is best interpreted as structured biological summarization across stages, not causal disease dynamics inference.

### Optional gene set overlap metrics

When metrics are enabled and a profile is provided, the tool writes per-stage overlap tables as CSV plus a JSON summary for plotting. **Preferred config (v1 plan):** nested profile `actionConfig.progression.gene_set_metrics` with `enabled`, optional `gene_universe` (`mapper_all`, `mapper_top_n`, `mapper_min_weight_quantile`), optional `output_basename` (default `stage_gene_set_fractions` when this block is used), plus `gene_set_profile` (path string, `{"path": ...}`, or inline `categories` with `{id, label, genes}`) or `disease_context` (e.g. `prostate_cancer` → bundled `profiles/prostate_cancer.json`). **Legacy flat keys** remain supported: `gene_set_metrics_enabled`, `gene_sets_path`, `disease_profile`, and `gene_set_denominator` / `gene_set_top_n` / `gene_set_weight_quantile` (default CSV basename `stage_gene_set_metrics`). For each stage and category, outputs include `n_universe` (mapper genes after optional filtering), `n_overlap`, and `fraction = n_overlap / n_universe`. These fractions are descriptive checks against curated hypotheses; they complement pathway q-values and do not replace enrichment statistics or imply causal stage biology.

### Stage-wise enrichment interpretation (prostate cancer template)

For prostate cancer stage series (PCa1 -> PCa4), a common interpretation template is:

| Stage | Prostate-specific signal | NKX3-1 lineage TF | Mitochondrial/OXPHOS | Overall pattern | Interpretation |
|---|---|---|---|---|---|
| PCa1 (early) | very strong | high | low/secondary | clean prostate identity | early prostate cancer phenotype with strong lineage specificity |
| PCa2 | strong | moderate down | moderate up | mixed | transition phase: partial lineage erosion with metabolic emergence |
| PCa3 | moderate | low down-down | high up-up | shifted | inflection point: lineage programs weaken while metabolic programs dominate |
| PCa4 (late) | moderate (still present) | weak | very strong | metabolic-dominant | advanced phenotype with strong mitochondrial/OXPHOS signature and diluted lineage identity |

This stage-wise pattern is exactly why mapper -> enricher -> progression is valuable: it tests whether selected DMP/gene signals produce coherent biological trajectories rather than isolated statistical hits.

### Key axes of progression (what is changing)

| Feature | Direction across stages | Meaning |
|---|---|---|
| Prostate identity (NKX3-1, prostate terms) | decreasing | Loss of lineage control and differentiation state |
| Disease specificity (prostate cancer terms) | slight decrease but stable | Disease signal persists across all stages |
| Mitochondrial respiration (Complex I, OXPHOS) | strongly increasing | Metabolic rewiring becomes dominant in later stages |
| Pathway structure | simple -> complex | Transition from cleaner single-axis signal to multi-system adaptation |

## Two Workflows Summary {#sec-two-workflows-summary}
MethylValidation supports two primary operational modes, described in full in [§ two workflows](12-two-workflows.md#sec-two-workflows). A brief summary for theory readers:

**Workflow 1 — Model Creation.** Repeated Monte Carlo runs with `--stability` estimate DMP recurrence frequencies from centroid+detector iterations. The `--freeze` step then builds production biological outputs on the stable panel (`fixed_dmp_panel`) and may run progression synthesis. The `--model` step then performs final classifier/predictor execution after optional biological gate checks.

**Workflow 2 — Model Use for Prediction.** The `--predictor-only` flag evaluates the frozen production model on repeated random holdouts without retraining. This workflow answers the question: *what is the empirical distribution of classification performance for the frozen production classifier under random sampling from the available cohort?*

The two workflows share the same split-and-aggregate infrastructure (`split.py`, `project_gen.py`, `pipeline_runner.py`) but differ in which pipeline steps execute per iteration. Workflow 1 iterations run centroid+detector; Workflow 2 iterations run predictor only.

## Publication Guidance

For publication text:

- describe `methylpredictor` as the evaluation and application layer for trained classifiers,
- reserve discrimination claims for labeled validation cohorts,
- treat blind-mode entropy as an uncertainty diagnostic only,
- describe `methylvalidation` as repeated split-sample internal validation of the end-to-end workflow,
- report the stability threshold $\tau$ and the number of qualifying runs $R^*$ when using `--stability`, and
- state explicitly whether the production model was trained on all samples or on a split-specific subset.

That is both accurate to the code and defensible statistically.
