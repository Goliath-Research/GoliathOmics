# MethylClassifier {#sec-methylclassifier}
## Role

`methylclassifier` is the orchestration layer that applies one or more binary ECDF classifiers to new samples. The mathematical core still lives in `methyl_utils/ecdf_classifier.py`, but `methylclassifier` determines how per-chromosome models, one-vs-rest bundles, and pairwise disease heads are fused into final probabilities.

The relevant implementation files are:

- `packages/methylclassifier/methyl_classifier/core/classifier.py`
- `packages/methylclassifier/methyl_classifier/core/multiclass_ovr.py`

## Binary Classification

For a single binary model, the underlying score is the ECDF log-density average described in [Eq. ecdf-score](01-methylutils.md#eq-ecdf-score) and [Eq. ecdf-softmax](01-methylutils.md#eq-ecdf-softmax), with explicit class priors:

<div id="eq-chrom-fusion" markdown="1">

$$
\hat P(Y=c\mid x)\propto \exp\!\left(\frac{\log \hat p(x\mid c)}{T}\right)\hat P(Y=c).
$$

From the perspective of `methylclassifier`, the main practical issues are:

- aligning the input sample to the stored DMP order,
- constructing an availability mask for missing loci, and
- optionally applying a stored Platt calibrator.

If the binary classifier is used alone, this is the cleanest statistical story in the package.

## Multi-Chromosome Binary Classification

When a detector run exports one classifier per chromosome, `methylclassifier` computes chromosome-specific binary probabilities and then fuses them using normalized weights:

$$
\hat p(y \mid x)
\propto
\sum_{c \in \mathcal{C}} w_c\,\hat p_c(y \mid x_c),
\qquad
\sum_{c \in \mathcal{C}} w_c > 0.
$$

</div>

The code supports several ways to determine the weights $w_c$:

- fixed configuration weights,
- trimmed mean effect-size weights derived from detector output,
- fitted linear, ridge, lasso, logistic, or elastic-net models trained on validation probabilities.

The last family is best understood as a second-stage meta-model that uses per-chromosome probabilities as features. It can improve prediction, but it is no longer a single coherent generative model.

## One-vs-Rest (OvR) Multiclass Fusion

For multiclass problems the package builds $K$ binary heads and fuses them into a $K$-class probability vector. The active implementation in `multiclass_ovr.py` uses binary probabilities or logits as intermediate scores and then applies a softmax-like normalization. In symbolic form,

<div id="eq-ovr-fusion" markdown="1">

$$
\ell_k(x) = \log \hat p_k(y = k \mid x) - \log \hat p_k(y \neq k \mid x),
$$

$$
\hat P(Y = k \mid x)
=
\frac{\exp(\ell_k(x))}
{\sum_{r=1}^{K} \exp(\ell_r(x))}.
$$

</div>

This is a practical multiclass reduction. It is not guaranteed to recover a single joint Bayesian posterior unless the binary heads are themselves perfectly compatible. Package metadata now records `ovr_inference_version` and `ovr_fuse_mode` so multiclass semantics are explicit and reproducible.

## Pairwise Control Aggregation

Some project layouts export only pairwise control-vs-disease detectors. In that case the package constructs the control class probability from the geometric mean of the pairwise control probabilities:

<div id="eq-geomean-control" markdown="1">

$$
\hat p_{\text{ctrl}}(x)
=
\exp\!\left(
\frac{1}{K-1}
\sum_{k=1}^{K-1}
\log \hat p_{\text{ctrl} \mid \text{ctrl-vs-}k}(x)
\right).
$$

</div>

The complement $1 - \hat p_{\text{ctrl}}(x)$ is then used to form a binary control-vs-rest head before final row normalization.

This is a useful aggregation rule, but it is a heuristic. The geometric mean is not derived in the code from a global multiclass likelihood.

## Bipartite Multi-Control × Multi-Disease Aggregation

The package also supports bipartite layouts with several control strata and several disease strata. Here it aggregates one probability column across several pairwise experts, again using geometric means. The logic is similar to [Eq. geomean-control](#eq-geomean-control) but now applied to either the control column or the disease column depending on which class head is being built.

That construction is convenient for complex cohort hierarchies, but it should be presented as a carefully engineered reduction strategy rather than a textbook probabilistic model.

## Calibration

When enabled, the package can apply Platt scaling [platt1999] to the raw binary outputs. In effect, one learns parameters $a$ and $b$ so that a raw score $s$ is mapped to

<div id="eq-platt" markdown="1">

$$
\hat p(y=1 \mid s) = \frac{1}{1 + \exp(as + b)}.
$$

</div>

This improves probability calibration under distribution shift or class imbalance, but it should not be confused with changing the underlying classifier likelihood.

## New Modeling Features in Production

Recent versions of `methylclassifier` expose a broader post-detector modeling layer than the original binary ECDF-only story. In practice, production runs may combine several of the following:

- native multiclass histogram model export (`multiclass-classifier.pkl`) from per-comparison detector outputs,
- optional learned multinomial logistic head on top of histogram pre-softmax scores,
- optional isotonic calibration for probability reshaping,
- optional elastic-net stacking for chromosome-level fusion,
- optional dependence-aware block aggregation (`dependence_block_size`, `dependence_block_shrinkage`),
- hierarchical panel readout for family-level reporting.

These are all valid engineering extensions, but conceptually they sit **on top of** the ECDF likelihood core rather than replacing it.

### Learned Multiclass Head

The learned head takes base multiclass score vectors $z(x)$ and fits a multinomial logistic layer:

<div id="eq-class-weight-balanced" markdown="1">

$$
\hat P(Y=k\mid x)
=
\frac{\exp(\beta_k^\top z(x)+b_k)}
{\sum_{r=1}^K \exp(\beta_r^\top z(x)+b_r)}.
$$

This layer is controlled by `multiclass_train_learned_head` and related hyperparameters in profile `actionConfig.detection` (see reference/configuration-reference). It is especially useful when the raw histogram scores have good ranking behavior but imperfect class boundaries.

### Isotonic and Stacking Layers

Two additional modeling switches in profile `actionConfig.classifier` are important for current production behavior:

- `use_isotonic_calibration`: nonparametric monotone calibration of predicted probabilities,
- `use_elasticnet_stacking`: linear meta-model for chromosome-weight fusion under elastic-net regularization.

Both improve empirical calibration or discrimination in many cohorts, but they increase model complexity and should be reported explicitly in methods sections.

### Practical Reproducibility

For reproducibility claims, report:

1. whether the learned multiclass head was enabled,
2. whether class balancing was used (`multiclass_learned_class_weight`),
3. whether isotonic calibration and/or elastic-net stacking were enabled,
4. the sklearn version stored in PKL metadata.

Without these details, two runs with the same DMP panel can still produce materially different probabilities.

## Multiclass Learned Head: Class Weighting {#sec-classifier-class-weight}
When the native multiclass histogram classifier is extended with a learned logistic head (`multiclass_train_learned_head: true`), the logistic regression is fitted on top of the pre-softmax histogram scores from the base model. This second-stage model can accept a `class_weight` parameter (exposed as `multiclass_learned_class_weight` in profile `actionConfig.detection`; see reference/configuration-reference).

With `multiclass_learned_class_weight: "balanced"`, sklearn computes inverse-frequency weights:

$$
w_k = \frac{n_{\text{total}}}{K \cdot n_k},
$$

</div>

where $n_k$ is the number of training samples in class $k$, $K$ is the number of classes, and $n_{\text{total}} = \sum_k n_k$. These weights are passed to the loss function of the multinomial logistic regression, penalizing errors on minority classes more heavily.

This is particularly important for the prostate cancer staging problem where the healthy control cohort may be two to four times larger than any individual disease stage cohort. Without balancing, the logistic head learns to predict the healthy class as the default when the pre-softmax histogram scores do not provide strong class separation (for example, when only 20% of DMP positions are covered in a test sample; see [§ detector coverage](03-methyldetector.md#sec-detector-coverage)).

**Theoretical note.** Class weighting corrects for imbalance in the empirical class frequencies of the training set. It does not correct for population prevalence differences between training and deployment contexts. If the intended clinical population has very different disease prevalence from the training cohort, additional calibration against the target distribution is required.

**Reproducibility.** Since sklearn 1.4, the version of sklearn used to train the logistic head is saved in the model PKL metadata under the key `sklearn_version`. This allows detection of version mismatches between training and inference environments. The key is written by `_fit_learned_multiclass_head` in `packages/methylclassifier/methyl_classifier/utils/multiclass_builder.py`.

## Missing Data Semantics

A practical but important detail is how the classifier handles unavailable loci. The implementation masks those loci out of the weighted average rather than imputing them with a full modeled distribution. As a result, the effective evidence for a sample depends on how many DMPs are observed for that sample.

This is a sensible engineering choice, but it means that the posterior confidence is partly a function of data availability as well as class separation.

## Publication Guidance

The strongest defensible summary is:

- the binary head is an ECDF-based Naive Bayes style score with explicit priors and optional calibration,
- multi-chromosome fusion is a weighted ensemble,
- multiclass OvR is a reduction from binary heads to a normalized $K$-class score,
- pairwise control aggregation is heuristic,
- and fitted chromosome weights are second-stage discriminative models, not part of the original density model.

That framing is more accurate than calling the entire package a single Bayesian multiclass classifier.
